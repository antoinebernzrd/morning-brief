"""Everything that touches the network: feeds, Telegram, prices, Polymarket, og:image and AI headlines."""
import feedparser
import html as html_lib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from sources import (MAX_PER_SOURCE, TELEGRAM_CHANNELS, TELEGRAM_LIMIT, ART_NEWSPAPER_FEED)
from articles import (_ago, _clean_title, _img, _parse_date, _resolve_gnews, _snip, _ts)
from paths import (CONFIG_FILE, HEADLINE_CACHE_FILE, OPENAI_CONFIG_FILE, POLY_CACHE_FILE, SESSION_FILE)
import calendar_events

_OG_RE = (
    re.compile(r'<meta[^>]+property=["\']og:image(?::url)?["\'][^>]+content=["\']([^"\']+)', re.I),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image(?::url)?["\']', re.I),
    re.compile(r'<meta[^>]+name=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)', re.I),
)

# Hosts that never yield a usable og:image: Google News wraps articles behind a JS
# redirect (its og:image is a Google logo), and these sit behind a Cloudflare
# challenge that returns 403 to any script. Don't waste build time on them.
_OG_SKIP_HOSTS = ("news.google.com", "economist.com")

def _og_image(url, timeout=8):
    """Scrape an article page for its og:image. Returns "" on any failure —
    callers fall back to the designed colour tile."""
    if not url or not url.startswith("http"):
        return ""
    if any(h in url for h in _OG_SKIP_HOSTS):
        return ""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": _FEED_UA,
            "Accept": "text/html,application/xhtml+xml,*/*",
            "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if "html" not in r.headers.get("Content-Type", ""):
                return ""
            head = r.read(180_000).decode(
                r.headers.get_content_charset() or "utf-8", "ignore")
    except Exception:
        return ""
    for pat in _OG_RE:
        m = pat.search(head)
        if m:
            img = html_lib.unescape(m.group(1).strip())
            if img.startswith("//"):
                img = "https:" + img
            if img.startswith("http"):
                return img
    return ""

def _backfill_images(arts, limit=25):
    """Fill in missing images by scraping og:image, newest first and bounded so a
    slow site can't stall the build. Articles still without an image afterwards
    render as colour tiles."""
    todo = [a for a in arts if not a.get("img")][:limit]
    if not todo:
        return arts
    done = 0
    for a in todo:
        img = _og_image(a.get("link", ""))
        if img:
            a["img"] = img
            done += 1
    print(f"    → og:image backfill: {done}/{len(todo)} recovered")
    return arts

_FEED_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Cloudflare blocks *.substack.com for datacenter IPs (GitHub Actions) — if the
# direct fetch comes back empty there, retry via the Google News RSS proxy.
FEED_FALLBACKS = {
    "Silicon Carne":  "https://news.google.com/rss/search?q=site:siliconcarne.substack.com&hl=en&gl=US&ceid=US:en",
    "TBPN":           "https://news.google.com/rss/search?q=site:tbpn.substack.com&hl=en&gl=US&ceid=US:en",
    "MTS Newsletter": "https://news.google.com/rss/search?q=site:mtslive.substack.com&hl=en&gl=US&ceid=US:en",
}

def _http_feed(url, _hops=0):
    """Fetch feed bytes with full browser headers, then hand to feedparser.
    Returns (feed, http_status). feedparser's own fetcher trips Cloudflare more
    often than a plain request with browser-like headers does.
    Follows redirects manually — pre-3.11 urllib doesn't auto-follow HTTP 308."""
    req = urllib.request.Request(url, headers={
        "User-Agent": _FEED_UA,
        "Accept": "application/rss+xml,application/xml,text/xml,*/*",
        "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
    })
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return feedparser.parse(r.read()), r.status
    except urllib.error.HTTPError as he:
        loc = he.headers.get("Location") if he.headers else None
        if he.code in (301, 302, 303, 307, 308) and loc and _hops < 3:
            return _http_feed(urllib.parse.urljoin(url, loc), _hops + 1)
        raise

def _fetch(sources):
    arts = []
    for name, url in sources:
        try:
            try:
                feed, status = _http_feed(url)
            except urllib.error.HTTPError as he:
                feed, status = None, he.code
            except Exception:
                feed, status = None, "?"
            if not feed or not feed.entries:
                # transport failed or empty — feedparser's own fetcher handles some
                # redirect/encoding cases urllib doesn't (e.g. 308 on older Pythons)
                fp = feedparser.parse(url, agent=_FEED_UA,
                    request_headers={"Accept":"application/rss+xml,application/xml,text/xml,*/*"})
                if fp.entries:
                    feed = fp
                else:
                    print(f"  ⚠  {name}: 0 entries (HTTP {status})", end="")
                    fb = FEED_FALLBACKS.get(name)
                    if fb:
                        try:
                            feed, _ = _http_feed(fb)
                            print(f" → Google News fallback: {len(feed.entries)} entries", end="")
                        except Exception:
                            feed = None
                    print()
            if not feed:
                continue
            for e in feed.entries[:MAX_PER_SOURCE]:
                arts.append({
                    "source":  name,
                    "title":   _clean_title(e.get("title","—")),
                    "link":    _resolve_gnews(e.get("link","#")),
                    "date":    _parse_date(e),
                    "ts":      _ts(_parse_date(e)),
                    "img":     _img(e),
                    "snip":    _snip(e),
                })
        except Exception as ex:
            print(f"  ⚠  {name}: {ex}")
    arts.sort(key=lambda a: a["date"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return arts

def _fetch_telegram():
    if not CONFIG_FILE.exists() or not SESSION_FILE.with_suffix(".session").exists():
        print("  ⚠  Telegram: no session — skipping (run telegram_auth.py)")
        return []
    try:
        from telethon.sync import TelegramClient
    except ImportError:
        print("  ⚠  Telegram: telethon not installed — skipping")
        return []
    try:
        cfg = json.loads(CONFIG_FILE.read_text())
    except Exception as e:
        print(f"  ⚠  Telegram config: {e}")
        return []
    arts = []
    try:
        with TelegramClient(str(SESSION_FILE), cfg["api_id"], cfg["api_hash"]) as client:
            for label, channel in TELEGRAM_CHANNELS:
                try:
                    entity = client.get_entity(channel)
                    msgs   = client.get_messages(entity, limit=TELEGRAM_LIMIT)
                    is_inv = channel.startswith("https://t.me/+")
                    for msg in msgs:
                        txt = (msg.text or "").strip()
                        if not txt or len(txt) < 20: continue
                        lines = txt.split("\n")
                        title = lines[0][:160]
                        snip  = " ".join(lines[1:])[:280] if len(lines)>1 else ""
                        link  = channel if is_inv else f"https://t.me/{channel}/{msg.id}"
                        dt    = msg.date.replace(tzinfo=timezone.utc) if msg.date else None
                        arts.append({"source":label,"title":title,"link":link,
                                     "date":dt,"ts":_ts(dt),"img":"","snip":snip})
                except Exception as e:
                    print(f"  ⚠  Telegram {channel}: {e}")
    except Exception as e:
        print(f"  ⚠  Telegram client: {e}")
    arts.sort(key=lambda a: a["date"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    print(f"    → {len(arts)} AFP messages")
    return arts

_TRUSTED_PUBS = frozenset([
    "financial times","ft","economist","les echos","le monde","reuters","bbc",
    "al jazeera","new york times","nyt","nss magazine","art newspaper",
    "télérama","telerama","l'équipe","l'equipe","equipe","defense news",
    "timeout","the liber","theliber","the free press","thefp",
    "silicon carne","not boring","tbpn","the nbs","siliconmania","silicon mania",
    "sortir","leparisien","parisien",
])

def _fetch_event_news(name, max_items=8, extra=""):
    """Fetch latest news for a calendar event, filtered to trusted sources.
    `name` is matched as an exact phrase; `extra` (a year) is matched loosely —
    put inside the quotes it produced phrases like "London Fashion Week SS
    2026" that no headline ever contains."""
    q = name.replace(" ", "+").replace("'", "").replace("&", "and")
    x = ("+" + urllib.parse.quote(extra)) if extra else ""
    url = (f"https://news.google.com/rss/search?q=%22{q}%22{x}+when:7d"
           f"&hl=en&gl=US&ceid=US:en")
    try:
        feed = feedparser.parse(
            url,
            agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            request_headers={"Accept": "application/rss+xml,application/xml,text/xml,*/*"},
        )
        def _make(e):
            dt  = _parse_date(e)
            src = getattr(getattr(e, "source", None), "title", "") or ""
            return {"title": e.get("title", "—"), "link": _resolve_gnews(e.get("link", "#")),
                    "source": src, "ago": _ago(dt), "ts": _ts(dt), "img": _img(e)}
        # First pass: trusted publishers only
        arts = []
        for e in feed.entries[:30]:
            src = (getattr(getattr(e, "source", None), "title", "") or "").lower()
            if any(p in src for p in _TRUSTED_PUBS):
                arts.append(_make(e))
            if len(arts) >= max_items:
                break
        # Fallback: too few trusted results → take top unfiltered
        if len(arts) < 3:
            arts = [_make(e) for e in feed.entries[:max_items]]
        # Google returns these by its own relevance ranking, not by date
        arts.sort(key=lambda a: a.get("ts") or 0, reverse=True)
        return arts
    except Exception as ex:
        print(f"  ⚠  event news ({name}): {ex}")
        return []

# Google matches an event's phrase anywhere in the article, so series that
# share words bleed into each other — London Fashion Week was showing New York
# shows, the Biennale was showing the Film Festival. For those families the
# headline itself has to name the one that's meant.
EVENT_TITLE_MUST = {
    # the city alone is far too broad — "paris" matched any Paris listing — so
    # each of these needs the city and the event together
    "NY Fashion Week SS": ("new york+fashion", "nyfw"),
    "NY Fashion Week FW": ("new york+fashion", "nyfw"),
    "London Fashion Week SS": ("london+fashion",),  "London Fashion Week FW": ("london+fashion",),
    "Milan Fashion Week SS": ("milan+fashion",),    "Milan Fashion Week FW": ("milan+fashion",),
    "Paris Fashion Week SS": ("paris+fashion",),    "Paris Fashion Week FW": ("paris+fashion",),
    "Paris Men's Fashion Week": ("paris+men",),
    "Haute Couture SS": ("couture",),               "Haute Couture FW": ("couture",),
    # "biennale" alone matched Gwangju and Gyeonggi; a term with + needs both
    "Venice Biennale": ("venice+biennale",),        "Venice Film Festival": ("venice+film",),
    "Art Basel": ("basel",),                        "Art Basel Miami": ("miami",),
    "US Open Tennis": ("tennis",),                  "US Open Golf": ("golf",),
    "F1 Monaco GP": ("monaco",),                    "F1 Abu Dhabi GP": ("abu dhabi",),
    "F1 Italian GP (Monza)": ("monza", "italian"),  "F1 British GP (Silverstone)": ("silverstone", "british"),
}

def _fetch_calendar_event_news():
    """Fetch news for calendar events active within a ±30/+90 day window."""
    from datetime import timedelta
    today   = datetime.now()
    w_start = (today - timedelta(days=30)).strftime("%Y-%m-%d")
    w_end   = (today + timedelta(days=90)).strftime("%Y-%m-%d")
    relevant = [e for e in calendar_events.CALENDAR_EVENTS
                if e["start"] <= w_end and e["end"] >= w_start]
    out = {}
    for e in relevant:
        print(f"    → event news: {e['name']}")
        # the phrase is the event itself — not the season code, not the venue in
        # brackets — and the year narrows it without being part of the phrase
        core = re.sub(r"\s*\([^)]*\)|\s+(?:SS|FW)$", "", e["name"]).strip()
        arts = _fetch_event_news(core, extra=e["start"][:4], max_items=20)
        must = EVENT_TITLE_MUST.get(e["name"])
        if must:
            def _hit(title):
                t = title.lower()
                return any(all(w in t for w in m.split("+")) for m in must)
            arts = [a for a in arts if _hit(a["title"])]
        arts = arts[:8]
        if arts:
            out[e["name"]] = arts
    return out

# ══════════════════════════════════════════════════════════════════════════════
#  LES ECHOS — Python-side keyword filtering (reliable, no URL query parsing issues)
# ══════════════════════════════════════════════════════════════════════════════
_LE_FEED = "https://news.google.com/rss/search?q=site:lesechos.fr+-site:investir.lesechos.fr&hl=fr&gl=FR&ceid=FR:fr"

def _fetch_les_echos(keywords, label):
    """Fetch Les Echos broadly, filter by keywords in Python — 100% reliable.
    The label becomes the source name, so it has to read as a masthead."""
    arts = _fetch([(f"Les Echos {label}", _LE_FEED)])
    result = []
    for a in arts:
        text = (a.get("title","") + " " + a.get("snip","")).lower()
        if any(kw.lower() in text for kw in keywords):
            result.append(a)
    print(f"    → Les Echos {label}: {len(result)}/{len(arts)} articles matched")
    return result

def _load_openai():
    """Return an OpenAI client if configured, else None."""
    import os
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key and OPENAI_CONFIG_FILE.exists():
        try:
            api_key = json.loads(OPENAI_CONFIG_FILE.read_text()).get("api_key")
        except Exception:
            pass
    if not api_key:
        print("  ⚠  OpenAI: no API key found — skipping AI headlines")
        return None
    try:
        from openai import OpenAI
        return OpenAI(api_key=api_key)
    except ImportError:
        print("  ⚠  OpenAI: package not installed — run: pip install openai")
        return None

def _load_headline_cache():
    if HEADLINE_CACHE_FILE.exists():
        try:
            return json.loads(HEADLINE_CACHE_FILE.read_text())
        except Exception:
            pass
    return {}

def _save_headline_cache(cache):
    try:
        HEADLINE_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2))
    except Exception:
        pass

def _ai_headline(titles, client, cache):
    """Generate a clean one-line headline for a story group via GPT-4o-mini."""
    cache_key = "||".join(sorted(t.strip().lower() for t in titles[:8]))
    if cache_key in cache:
        return cache[cache_key]
    try:
        prompt = (
            "These headlines from different sources all cover the same news story.\n"
            "Write ONE clean, factual headline in English (max 12 words) capturing the core event.\n"
            "Rules: no source names, no opinion words, just the fact, no quotes around output.\n\n"
            + "\n".join(f"- {t}" for t in titles[:8])
            + "\n\nHeadline:"
        )
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=40,
            temperature=0.2,
        )
        headline = resp.choices[0].message.content.strip().strip('"').strip("'")
        cache[cache_key] = headline
        return headline
    except Exception as e:
        print(f"  ⚠  AI headline: {e}")
        return titles[0]

def _enrich_groups(groups, client, cache):
    """Add AI-generated headline to every multi-source story group."""
    if not client:
        return groups
    result = []
    for g in groups:
        if len(g) > 1:
            titles   = [a["title"] for a in g]
            headline = _ai_headline(titles, client, cache)
            g        = [dict(g[0], _headline=headline)] + list(g[1:])
        result.append(g)
    return result

def _fetch_evergreen(sources, n=1):
    """Latest n from each source, ignoring the recency window. For writers who
    publish a few times a year, where the newest piece is what matters, not
    whether it landed this week."""
    out = []
    for name, url in sources:
        try:
            arts = _fetch([(name, url)])
            arts.sort(key=lambda a: a["ts"] or 0, reverse=True)
            out.extend(arts[:n])
        except Exception as ex:
            print(f"  ⚠  {name}: {ex}")
    return out

def _fetch_art_newspaper(n=5):
    """Always return the latest n NYT Arts articles, ignoring age filter."""
    try:
        arts = _fetch([("The NYT Arts", ART_NEWSPAPER_FEED)])
        arts.sort(key=lambda a: a["ts"] or 0, reverse=True)
        return arts[:n]
    except Exception as ex:
        print(f"  ⚠  NYT Arts: {ex}")
        return []

_NSS_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# English articles only: /en/<section>/<numeric-id>/<slug>, excluding author/tag/search pages
_NSS_ARTICLE = re.compile(r'nssmag\.com/en/(?!author/|tag/|search/)[a-z0-9-]+/\d+/', re.IGNORECASE)

def _fetch_nss(n=5):
    """Latest n NSS articles from their monthly sitemap — real URLs, real dates, no Google News."""
    now = datetime.now()
    months = [(now.year, now.month)]
    months.append((now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12))
    arts, seen = [], set()
    for y, m in months:
        try:
            url = f"https://www.nssmag.com/sitemap.xml?year={y}&month={m}"
            req = urllib.request.Request(url, headers={"User-Agent": _NSS_UA})
            root = None
            for attempt in range(3):
                try:
                    with urllib.request.urlopen(req, timeout=30) as r:
                        root = ET.fromstring(r.read())
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    import time as _t
                    _t.sleep(2)
            ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
            for u in root.findall("s:url", ns):
                loc = (u.findtext("s:loc", default="", namespaces=ns) or "").strip()
                mod = (u.findtext("s:lastmod", default="", namespaces=ns) or "").strip()
                if not _NSS_ARTICLE.search(loc) or loc in seen:
                    continue
                seen.add(loc)
                try:
                    dt = datetime.fromisoformat(mod.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                except Exception:
                    dt = None
                slug  = loc.rstrip("/").split("/")[-1].replace("-", " ")
                title = slug.title()
                arts.append({"source": "NSS Magazine", "title": title, "link": loc,
                             "date": dt, "ts": _ts(dt), "img": "", "snip": ""})
        except Exception as ex:
            print(f"  ⚠  NSS sitemap {y}-{m}: {ex}")
        if len(arts) >= n:
            break
    arts.sort(key=lambda a: a["ts"] or 0, reverse=True)
    print(f"    → {len(arts)} NSS articles (sitemap)")
    return arts[:n]

def _fetch_prices():
    """Fetch index/commodity/forex prices via Yahoo v8 at build time. Crypto via CoinGecko."""
    import urllib.parse, time as _time
    tickers = [
        ("^GSPC","S&P 500"), ("^IXIC","NASDAQ"), ("^DJI","Dow Jones"),
        ("GC=F","Gold"), ("CL=F","WTI Oil"), ("EURUSD=X","EUR/USD"),
        ("^VIX","VIX"), ("^TNX","10Y UST"),
    ]
    UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
    out = []
    try:
        cg_url = ("https://api.coingecko.com/api/v3/simple/price"
                  "?ids=bitcoin,ethereum&vs_currencies=usd&include_24hr_change=true")
        req = urllib.request.Request(cg_url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=10) as r:
            cg = json.loads(r.read())
        for cid, name, sym in [("bitcoin","Bitcoin","BTC-USD"),
                               ("ethereum","Ethereum","ETH-USD")]:
            if cid in cg:
                out.append({"name": name, "sym": sym,
                            "price": cg[cid].get("usd"),
                            "pct": cg[cid].get("usd_24h_change")})
    except Exception as ex:
        print(f"  ⚠  CoinGecko: {ex}")
    for sym, name in tickers:
        try:
            s = urllib.parse.quote(sym)
            url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{s}"
                   f"?interval=1d&range=2d")
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=10) as r:
                d = json.loads(r.read())
            meta  = d["chart"]["result"][0]["meta"]
            price = meta.get("regularMarketPrice")
            prev  = meta.get("previousClose") or meta.get("chartPreviousClose")
            pct   = ((price - prev) / prev * 100) if (price and prev) else None
            out.append({"name": name, "sym": sym, "price": price, "pct": pct})
        except Exception as ex:
            print(f"  ⚠  price {name}: {ex}")
            out.append({"name": name, "sym": sym, "price": None, "pct": None})
        _time.sleep(0.4)
    print(f"    → {sum(1 for p in out if p['price'] is not None)}/{len(out)} prices fetched")
    return out

def _fetch_polymarket(limit=16):
    """Fetch top Polymarket markets by 24h volume."""
    try:
        import urllib.request, json as _json
        url = ("https://gamma-api.polymarket.com/markets"
               "?active=true&closed=false&limit=25&order=volume24hr&ascending=false")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = _json.loads(r.read())
        markets = []
        for m in data:
            q = (m.get("question") or "").strip()
            if not q or len(q) < 8:
                continue
            # outcomes + prices
            try:
                outcomes_raw = m.get("outcomes")
                outcomes = _json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else (outcomes_raw or [])
                prices_raw = m.get("outcomePrices")
                prices = _json.loads(prices_raw) if isinstance(prices_raw, str) else (prices_raw or [])
                pairs = [(outcomes[i], round(float(prices[i]) * 100))
                         for i in range(min(len(outcomes), len(prices)))]
                pairs.sort(key=lambda x: -x[1])
                pairs = pairs[:3]
            except Exception:
                pairs = []
            # volume
            try:
                vol = float(m.get("volume") or m.get("volume24hr") or 0)
                if vol >= 1e9:   vol_str = f"${vol/1e9:.1f}B Vol."
                elif vol >= 1e6: vol_str = f"${vol/1e6:.0f}M Vol."
                elif vol >= 1e3: vol_str = f"${vol/1e3:.0f}K Vol."
                else:            vol_str = f"${vol:.0f} Vol."
            except Exception:
                vol_str = ""
            slug = m.get("slug") or ""
            markets.append({"q": q, "pairs": pairs, "vol": vol_str, "slug": slug})
            if len(markets) >= limit:
                break
        if markets:
            try:
                POLY_CACHE_FILE.write_text(json.dumps(markets), encoding="utf-8")
            except Exception:
                pass
        print(f"    → {len(markets)} Polymarket markets")
        return markets
    except Exception as ex:
        # French ISPs resolve gamma-api.polymarket.com to the ANJ regulator's
        # block page, so a build run from France always fails here while CI
        # (US runners) succeeds. Fall back to the last good payload rather than
        # publishing an empty band.
        print(f"  ⚠  Polymarket: {ex}")
        try:
            cached = json.loads(POLY_CACHE_FILE.read_text(encoding="utf-8"))
            if cached:
                print(f"    → reusing {len(cached)} cached markets")
                return cached
        except Exception:
            pass
        return []
