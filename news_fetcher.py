#!/usr/bin/env python3
"""Morning Brief v4"""
import base64
import hashlib
import random
import calendar as _cal
import feedparser
import html as html_lib
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
try:
    from zoneinfo import ZoneInfo
    _PARIS = ZoneInfo("Europe/Paris")
    # Validate it actually applies DST correctly
    _test = datetime(2026, 5, 1, tzinfo=_PARIS).utcoffset().seconds // 3600
    if _test not in (1, 2):
        raise ValueError("bad offset")
except Exception:
    # Fallback: compute Paris offset from UTC (CET=+1 Oct-Mar, CEST=+2 Apr-Sep)
    from datetime import timedelta as _td
    class _ParisTZ(timezone):
        def utcoffset(self, dt):
            m = dt.month if dt else 5
            return _td(hours=2 if 3 < m <= 10 else 1)
        def tzname(self, dt): return "CEST" if (dt and 3 < dt.month <= 10) else "CET"
        def dst(self, dt): return _td(hours=1) if (dt and 3 < dt.month <= 10) else _td(0)
    _PARIS = _ParisTZ()
OUTPUT_DIR          = Path(__file__).parent
ASSETS_DIR          = OUTPUT_DIR / "assets"
OUTPUT_FILE         = OUTPUT_DIR / "index.html"
CONFIG_FILE         = OUTPUT_DIR / "telegram_config.json"
SESSION_FILE        = OUTPUT_DIR / "telegram_session"
OPENAI_CONFIG_FILE  = OUTPUT_DIR / "openai_config.json"
HEADLINE_CACHE_FILE = OUTPUT_DIR / "headline_cache.json"
# feedparser's own fetcher takes no timeout argument, so a black-holed host
# leaves the build wedged in SYN_SENT indefinitely. A process-wide default
# bounds every socket, including the ones we do not open ourselves.
socket.setdefaulttimeout(25)

# Prefer IPv4. Networks that advertise IPv6 but drop the traffic (some French
# ISPs, plenty of cafe wifi) leave every connection sitting in SYN_SENT until
# it times out — browsers hide this with Happy Eyeballs, urllib does not, and
# one such host per feed is enough to stretch a build into the tens of minutes.
# Every host we fetch publishes an A record, so we sort IPv4 first and keep the
# IPv6 results as a fallback rather than dropping them.
_orig_getaddrinfo = socket.getaddrinfo
def _ipv4_first(*args, **kwargs):
    res = _orig_getaddrinfo(*args, **kwargs)
    return sorted(res, key=lambda r: 0 if r[0] == socket.AF_INET else 1)
socket.getaddrinfo = _ipv4_first

POLY_CACHE_FILE     = OUTPUT_DIR / "poly_cache.json"
from sources import (
    MAX_PER_SOURCE,
    WEEKLY_SOURCES,
    LGC_RUBRIQUES,
    LGC_OPINION,
    LGC_GEO,
    _lgc_feeds,
    LGC_GEO_SOURCES,
    LGC_OPINION_SOURCES,
    LGC_BEAT,
    SOURCE_CANONICAL,
    TELEGRAM_CHANNELS,
    TELEGRAM_LIMIT,
    TECH_KEYWORDS,
    MACRO_KEYWORDS,
    CONFLICTS,
    TECH_SOURCES,
    TECH_PINNED,
    SOURCE_CADENCE,
    DEFAULT_CADENCE,
    TECH_EVERGREEN,
    SOURCE_WINDOW_DAYS,
    MACRO_SOURCES,
    GEO_SOURCES,
    GEO_PINNED,
    _GN_SUFFIX,
    _drop_audio_dupes,
    _strip_gn_suffix,
    _keep_war_room,
    _keep_world_brief,
    MACRO_BEAT,
    MACRO_CATEGORY,
    MACRO_PINNED,
    _keep_block_daily,
    CULTURE_SOURCES,
    CULTURE_BEAT,
    CULTURE_LONG_READ,
    CULTURE_SOURCE_COLORS,
    ART_NEWSPAPER_FEED,
    GOSSIP_SOURCES_OTHER,
    GOSSIP_WINDOW_DAYS,
    GOSSIP_SOURCE_COLORS,
    MACRO_SOURCE_COLORS,
    SPORTS_SOURCES_FR,
    SPORTS_SOURCES_INT,
    CONFLICT_NEWS_SOURCES,
    PARIS_SOURCES,
    CITIES_SOURCES,
    MARSEILLE_SOURCE_NAMES,
    CALENDAR_SERIES,
    _WIKIDATA_UA,
)


def _calendar_editions():
    """Every dated edition reachable from each seed, in one SPARQL request.
    Returns {seed_qid: [(start, end), ...]} with day-precision dates only —
    Wikidata stores next year's F1 season as "2027", and that must never be
    read as 1 January."""
    seeds = sorted({q for _, _, _, q, _, _ in CALENDAR_SERIES if q})
    if not seeds:
        return {}
    query = """SELECT ?seed ?item ?start ?sp ?end ?ep ?pt ?pp WHERE {
      VALUES ?seed { %s }
      ?seed wdt:P156* ?item .
      OPTIONAL { ?item p:P580/psv:P580 [ wikibase:timeValue ?start ; wikibase:timePrecision ?sp ] }
      OPTIONAL { ?item p:P582/psv:P582 [ wikibase:timeValue ?end ; wikibase:timePrecision ?ep ] }
      OPTIONAL { ?item p:P585/psv:P585 [ wikibase:timeValue ?pt ; wikibase:timePrecision ?pp ] }
    }""" % " ".join("wd:" + q for q in seeds)
    try:
        req = urllib.request.Request(
            "https://query.wikidata.org/sparql?format=json&query=" + urllib.parse.quote(query),
            headers=_WIKIDATA_UA)
        with urllib.request.urlopen(req, timeout=40) as r:
            rows = json.loads(r.read())["results"]["bindings"]
    except Exception as e:
        print(f"  ⚠  calendar: Wikidata unavailable ({type(e).__name__}) — using estimates")
        return {}
    val = lambda b, k: b.get(k, {}).get("value")
    out = {}
    for b in rows:
        if "/entity/" not in (val(b, "item") or ""):
            continue                                    # blank nodes in the chain
        day = lambda t, p: t[:10] if t and p and int(p) >= 11 else None
        start = day(val(b, "start"), val(b, "sp")) or day(val(b, "pt"), val(b, "pp"))
        if not start:
            continue
        end = day(val(b, "end"), val(b, "ep")) or start
        seed = val(b, "seed").rsplit("/", 1)[1]
        out.setdefault(seed, set()).add((start, end))
    return {k: sorted(v) for k, v in out.items()}


def _build_calendar_events(years=None, verify=True):
    """This year's and next year's occurrence of every series, verified where
    Wikidata allows. Each event carries verified=True/False."""
    from datetime import date
    today = date.today()
    years = years or (today.year, today.year + 1)
    editions = _calendar_editions() if verify else {}
    events, n_ok = [], 0
    for name, cat, (es, ee), qid, every, anchor in CALENDAR_SERIES:
        for y in years:
            if every > 1 and (y - (anchor or y)) % every:
                continue                                 # not an edition year
            est_s = date(y, *map(int, es.split("-")))
            est_e = date(y + (1 if ee < es else 0), *map(int, ee.split("-")))
            chosen = None
            for s_, e_ in editions.get(qid, []):
                d = date.fromisoformat(s_)
                if d.year == y and abs((d - est_s).days) <= 60:
                    chosen = (s_, e_)
                    break
            if chosen:
                n_ok += 1
                events.append({"name": name, "cat": cat, "start": chosen[0],
                               "end": chosen[1], "verified": True})
            else:
                events.append({"name": name, "cat": cat, "start": est_s.isoformat(),
                               "end": est_e.isoformat(), "verified": False})
    if editions:
        print(f"    → calendar: {n_ok}/{len(events)} occurrences verified against Wikidata")
    return events


# Estimates only at import, so importing this module never touches the network.
# main() replaces it with the verified list before anything reads it.
CALENDAR_EVENTS = _build_calendar_events(verify=False)
# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════
def _s(t):
    """Escape for HTML. Feeds hand us text that is already entity-encoded —
    escaping that a second time is what printed "Lenny&#8217;s" on the page —
    so decode first, then escape exactly once."""
    return html_lib.escape(html_lib.unescape(str(t or "")), quote=True)
def _parse_date(entry):
    for attr in ("published_parsed", "updated_parsed"):
        val = getattr(entry, attr, None)
        if val:
            try:
                return datetime(*val[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None
def _ts(dt):
    return int(dt.timestamp()) if dt else 0
def _ago(dt):
    if not dt:
        return ""
    diff = int((datetime.now(timezone.utc) - dt).total_seconds())
    if diff < 3600:  return f"{diff//60}m"
    if diff < 86400: return f"{diff//3600}h"
    return f"{diff//86400}d"
def _img(entry):
    mt = getattr(entry, "media_thumbnail", None)
    if mt and isinstance(mt, list) and mt[0].get("url"):
        return mt[0]["url"]
    for mc in getattr(entry, "media_content", []):
        url = mc.get("url", "")
        if url and ("image" in mc.get("type","") or
                    url.lower().endswith((".jpg",".jpeg",".png",".webp"))):
            return url
    for enc in getattr(entry, "enclosures", []):
        if "image" in enc.get("type","") and enc.get("href"):
            return enc["href"]
    for field in [entry.get("summary",""),
                  (entry.get("content") or [{}])[0].get("value","")]:
        m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', field or "")
        if m and m.group(1).startswith("http"):
            return m.group(1)
    return ""
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

def _filter_recent(arts, days=2, weekly_days=7):
    """Keep articles from the last `days` days. SOURCE_WINDOW_DAYS overrides that
    per source; WEEKLY_SOURCES falls back to `weekly_days`; otherwise the window
    follows the source's cadence, so a newsletter is not judged by wire time."""
    now_ts = datetime.now(timezone.utc).timestamp()
    result = []
    for a in arts:
        if not a["ts"]:          # no date → keep
            result.append(a)
            continue
        src = a["source"]
        cad = SOURCE_CADENCE.get(src)
        if src in SOURCE_WINDOW_DAYS:
            window = SOURCE_WINDOW_DAYS[src]
        elif src in WEEKLY_SOURCES:
            window = weekly_days
        elif cad == "daily":
            # A daily debrief skips weekends. At 48h the Friday issue was gone
            # by Monday midday — MTS, The Block Daily and FirstFT all vanished
            # from the page. Four days covers a weekend plus a bank holiday;
            # the builders then keep only the newest issue.
            window = max(days, 4)
        elif cad == "slow":
            # Long reads publish weekly or less. Without their own entry they
            # were getting the 48h wire window and never appeared at all.
            window = max(days, 21)
        else:
            window = days
        if a["ts"] >= now_ts - window * 86400:
            result.append(a)
    return result
def _filter_keywords(arts, keywords):
    """Keep only articles whose title+snippet contains at least one keyword (case-insensitive)."""
    pats = [re.compile(re.escape(kw), re.IGNORECASE) for kw in keywords]
    def _matches(a):
        text = (a.get("title","") or "") + " " + (a.get("snip","") or "")
        return any(p.search(text) for p in pats)
    return [a for a in arts if _matches(a)]

def _filter_city_local(arts):
    """Keep only articles whose title mentions the city they're attributed to.
    Curated city-section sources (Le Monde Marseille/Paris, Marsactu) pass through
    untouched — they're editorially scoped to the city already.
    Other sources require the city name in the title.
    """
    # These sources are editorially scoped — trust them entirely
    TRUSTED_LOCAL = {"Marsactu", "Le Monde Marseille", "Le Monde Paris", "BFM Marseille"}
    MARSEILLE_PATS = [re.compile(t, re.IGNORECASE) for t in [
        r"marseille", r"marseillais", r"bouches.du.rh",
    ]]
    PARIS_PATS = [re.compile(t, re.IGNORECASE) for t in [
        r"paris", r"parisien", r"parisienne", r"arrondissement",
        r"île.de.france", r"seine.saint.denis", r"val.de.marne",
        r"hauts.de.seine",
    ]]
    result = []
    for a in arts:
        src = a.get("source", "")
        title = a.get("title", "") or ""
        if src in TRUSTED_LOCAL:
            result.append(a)
        elif src in MARSEILLE_SOURCE_NAMES:
            if any(p.search(title) for p in MARSEILLE_PATS):
                result.append(a)
        else:  # Paris sources
            if any(p.search(title) for p in PARIS_PATS):
                result.append(a)
    return result
def _snip(entry):
    raw = entry.get("summary","") or ""
    txt = re.sub(r"<[^>]+>"," ", raw)
    txt = re.sub(r"\s+"," ", txt).strip()
    return txt[:280] + "…" if len(txt) > 280 else txt
# Strip source name suffixes that Google News RSS embeds in article titles
# e.g. "Italy extends tax cuts – Les Echos" → "Italy extends tax cuts"
_TITLE_SUFFIX_RE = re.compile(
    r'\s*[\-–|]\s*(?:Investir Les Echos|Les Echos|Le Monde|Le Parisien|L\'[ÉE]quipe|Télérama|Telerama|'
    r'Financial Times|The Economist|Reuters|BBC(?:\s+\w+)?|Al Jazeera|'
    r'The New York Times|Defense News|NSS Magazine|The NYT Arts|'
    r'Timeout(?:\s+\w+)?|The NBS|Silicon(?:Mania|Carne)|TBPN|'
    r'FT(?:\s+\w+)?|AFP|AP News|Politico|Bloomberg)\s*$',
    re.IGNORECASE
)
def _clean_title(t):
    return _TITLE_SUFFIX_RE.sub('', (t or '—').strip()).strip()

def _resolve_gnews(url):
    """No-op: modern Google News links (CBMi… protobuf) can't be decoded offline,
    and resolving them via network costs ~5s each → 10min runs. Links stay wrapped
    (they still work when clicked, just redirect through Google)."""
    return url

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
def _dedup_exact(arts):
    """Collapse articles with identical titles from the same canonical publication.
    e.g. FT Tech + FT Companies Tech publishing the same article → keep one."""
    seen = {}   # (canonical_source, normalised_title) → index kept
    seen_links = set()
    result = []
    for a in arts:
        # the same URL from two of a publisher's feeds is one article — Politico's
        # EU and France feeds were both delivering it, and the source+title key
        # treats them as different sources so it never collapsed them
        link = (a.get("link") or "").split("?")[0]
        if link and link in seen_links:
            continue
        canon  = SOURCE_CANONICAL.get(a["source"], a["source"])
        key    = (canon, re.sub(r"\s+", " ", a["title"].strip().lower()))
        if key not in seen:
            if link:
                seen_links.add(link)
            seen[key] = True
            # Store canonical name so grouping later sees it correctly
            a = dict(a); a["_canon"] = canon
            result.append(a)
    return result

def _entities(title):
    """Extract proper nouns / brand names — language-agnostic (OpenAI = OpenAI in FR and EN)."""
    # Capitalised words 5+ chars, excluding sentence-start stop words
    ECAP_STOP = {"The","This","That","These","Those","With","From","After",
                 "Before","About","Under","While","Their","There","Where","When",
                 "Pour","Dans","Avec","Sous","Mais","Plus","Tout","Sans","Vers",
                 "Entre","Contre","Selon"}
    caps = {w for w in re.findall(r'\b[A-Z][A-Za-z]{4,}\b', title) if w not in ECAP_STOP}
    # All-caps acronyms 2-6 chars (Fed, ECB, NATO, AI...)
    acro = set(re.findall(r'\b[A-Z]{2,6}\b', title))
    return caps | acro

def _dedup(arts):
    STOP = {"the","a","an","in","of","to","and","for","on","at","is","are","was",
            "with","by","that","this","as","it","its","from","be","or","has","have",
            "had","will","than","after","but","not","about","new","de","la","le",
            "les","du","un","une","en","et","des","sur","pour","par","dans","est"}
    def words(t):
        return {w for w in re.sub(r"[^a-z0-9àâéèêëîïôùûü ]","",t.lower()).split()
                if w not in STOP and len(w)>2}
    # Clustering is for the wire — the same story from several outlets. A daily
    # roundup names half the day's stories in its headline, so it was being
    # merged into whichever wire group it overlapped: FirstFT ended up hidden
    # inside a TechCrunch group and the page led with a wire story instead.
    # Debriefs and long reads always stand alone.
    def _solo(x):
        c = SOURCE_CADENCE.get(x["source"]) or SOURCE_CADENCE.get(x.get("_canon") or "")
        return c in ("daily", "slow")
    groups, used = [], set()
    for i, a in enumerate(arts):
        if i in used: continue
        if _solo(a):
            used.add(i); groups.append([a])
            continue
        wi = words(a["title"])
        ei = _entities(a["title"])
        grp = [a]
        for j, b in enumerate(arts):
            if j<=i or j in used: continue
            if _solo(b): continue
            wj = words(b["title"])
            ej = _entities(b["title"])
            u  = wi | wj
            # Same-language: word overlap ≥ 25%
            word_match   = bool(u) and len(wi & wj)/len(u) >= 0.25
            # Cross-language: share 2+ proper nouns/acronyms (1 is too broad — e.g. "Italy")
            shared_ent   = ei & ej
            entity_match = (
                len(shared_ent) >= 2                          # two entities match
                or any(len(e) >= 8 for e in shared_ent)      # or one very long brand name
            )
            if word_match or entity_match:
                grp.append(b); used.add(j)
        used.add(i); groups.append(grp)
    return groups

def _sort_groups(groups):
    """Float multi-source groups to the top, both halves sorted by recency."""
    multi  = [g for g in groups if len(g) > 1]
    single = [g for g in groups if len(g) == 1]
    return multi + single
def _match_conflicts(arts):
    """Return {conflict_id: [article_dict, ...]}"""
    out = {c["id"]: [] for c in CONFLICTS}
    for a in arts:
        txt = (a["title"]+" "+a.get("snip","")).lower()
        for c in CONFLICTS:
            if any(kw in txt for kw in c["keywords"]) and len(out[c["id"]]) < 10:
                out[c["id"]].append(a)
    return out
def _route_afp(msgs):
    """Sort AFP messages into tech, macro, conflict pools, and a ticker remainder."""
    tech, macro, ticker = [], [], []
    conflict_pool = []
    for m in msgs:
        txt = (m["title"]+" "+m.get("snip","")).lower()
        matched_conflict = any(
            any(kw in txt for kw in c["keywords"])
            for c in CONFLICTS
        )
        if matched_conflict:
            conflict_pool.append(m)
        elif any(kw in txt for kw in TECH_KEYWORDS):
            tech.append(m)
        elif any(kw in txt for kw in MACRO_KEYWORDS):
            macro.append(m)
        else:
            ticker.append(m)
    return {"conflict": conflict_pool, "tech": tech,
            "macro": macro, "ticker": ticker}
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
    relevant = [e for e in CALENDAR_EVENTS
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

LES_ECHOS_TECH_KW = [
    "start-up","scale-up","licorne","décacorne","jeune pousse",
    "deeptech","fintech","biotech","cleantech","greentech","healthtech",
    "edtech","proptech","insurtech","legaltech","medtech","agritech",
    "adtech","martech","femtech","crypto","levée de fonds","lève",
    "série a","série b","série c","seed","amorçage","pré-seed",
    "tour de table","monte au capital","capital-risque","venture capital",
    "business angel","valorisation","liquidation judiciaire","acquisition",
    "incubateur","french tech","station f","bpifrance","atomico","eqt",
    "partech","kima","eurazeo","astanor","ledger","fondateur","cofondateur",
    "saas","data center","quantique","semi-conducteurs","souveraineté numérique",
    "intelligence artificielle","ia générative","ia française","machine learning",
    "openai","mistral","anthropic","deepmind","gafam","nvidia","llm","chatbot",
    "cybersécurité","robotique","high tech","elevenlab","lovable",
]

LES_ECHOS_MACRO_KW = [
    "bourse","marchés financiers","taux d'intérêt","bce","fed ","inflation",
    "pib","récession","cac 40","banque centrale","déficit budgétaire",
    "dette publique","matières premières","taux directeur","obligataire",
    "wall street","dow jones","s&p 500","euro stoxx","taux de change",
    "croissance économique","chômage","balance commerciale","politique monétaire",
    "obligations","spread","rendement","indice boursier","banque de france",
]

LES_ECHOS_POLITIQUE_KW = [
    "politique","gouvernement","parlement","assemblée nationale","sénat",
    "macron","premier ministre","ministre","élection","parti","coalition",
    "réforme","loi","justice","syndicat","grève","manifestation","mobilisation",
    "idées","débat","essai","intellectuel","philosophie","démocratie",
    "immigration","laïcité","identité","liberté","droits","censure",
    "gauche","droite","extrême","rassemblement national","nfp","ps","lr",
    "opinion","éditorial","tribune","chronique","analyse","point de vue",
    "société","inégalités","fracture","populisme","souveraineté",
]

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

# Sources capped at 1 article (show only latest)
SOURCE_CAPS = {
    "Howard Marks":      1,
    "The War Room":      3,
    "Musings on Markets":1,
    "Citation Needed":   3,
    "Silicon Carne":     1,
    "Not Boring":        1,
    "TBPN":              1,
    "MTS Newsletter":    1,
    "Stratechery":       2,
    "Scott Aaronson":    1,
    "Bits About Money":  1,
    "Reaction Wheel":    1,
    # ── Opinions
    "The Economist":    10,
    "Works in Progress": 6,
    # the four new essayists publish slowly — a few each keeps the mix even
    "The Convivial Society": 3,
    "Noema":             4,
    "Programmable Mutter": 3,
    "The New Atlantis":  3,
    "Lenny's Newsletter":1,
    "Pragmatic Engineer":1,
    "The NBS":           1,
    "First Round Review":1,
    "Dezeen":            10,
    "Film Comment":       6,
    "Common Edge":        6,
    "Télérama Cinéma":   10,
    "Télérama Séries":   10,
    "The Ankler":        4,
    "Artnet":            10,
}
DEFAULT_CAP = 6  # all other sources

def _dedup_smart(arts):
    """Collapse near-duplicate articles (same topic / word overlap) keeping the most
    recent from each cluster. Used for the gossip section to reduce clutter."""
    STOP = {"the","a","an","in","of","to","and","for","on","at","is","are","was",
            "with","by","that","this","as","it","its","from","be","or","has","have",
            "had","will","than","after","but","not","about","new","de","la","le",
            "les","du","un","une","en","et","des","sur","pour","par","dans","est"}
    def words(t):
        return {w for w in re.sub(r"[^a-z0-9àâéèêëîïôùûü ]","",t.lower()).split()
                if w not in STOP and len(w)>2}
    used = set()
    result = []
    for i, a in enumerate(arts):
        if i in used: continue
        wi = words(a["title"])
        ei = _entities(a["title"])
        cluster = [a]
        for j, b in enumerate(arts):
            if j <= i or j in used: continue
            wj = words(b["title"])
            ej = _entities(b["title"])
            u  = wi | wj
            word_match  = bool(u) and len(wi & wj)/len(u) >= 0.40
            shared_ent  = ei & ej
            entity_match = (
                len(shared_ent) >= 2
                or any(len(e) >= 8 for e in shared_ent)
            )
            if word_match or entity_match:
                cluster.append(b); used.add(j)
        used.add(i)
        # Keep most recent from the cluster
        best = max(cluster, key=lambda x: x["ts"] or 0)
        result.append(best)
    return result

def _cap_per_source(arts):
    """Apply per-source caps — specific sources show only their latest article."""
    counts = {}
    result = []
    for a in arts:
        src = a["source"]
        cap = SOURCE_CAPS.get(src, DEFAULT_CAP)
        if counts.get(src, 0) < cap:
            result.append(a)
            counts[src] = counts.get(src, 0) + 1
    return result

# ══════════════════════════════════════════════════════════════════════════════
#  CSS
# ══════════════════════════════════════════════════════════════════════════════
CSS = (ASSETS_DIR / "brief.css").read_text(encoding="utf-8")
# ══════════════════════════════════════════════════════════════════════════════
#  MOBILE NAV (bottom tab bar — rendered only ≤768px via CSS)
# ══════════════════════════════════════════════════════════════════════════════
PHONE_APP = (ASSETS_DIR / "phone_app.html").read_text(encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════════════
#  HTML BUILDERS
# ══════════════════════════════════════════════════════════════════════════════
def _sec(color, label, body, extra_style=""):
    style = f"style='{extra_style}'" if extra_style else ""
    return (
        f'<div class="section" {style}>'
        f'<div class="sec-hd" style="border-top:2px solid {color}">'
        f'<span class="sec-hd-text">{label}</span>'
        f'</div>'
        f'{body}</div>\n'
    )
def build_ticker(items):
    if not items:
        return ""
    once = "".join(
        f'<a href="{_s(a["link"])}" target="_blank" rel="noopener" class="t-item">'
        f'{_s(a["title"])}</a>'
        for a in items[:12]
    )
    # Duplicate for seamless infinite scroll (translateX(-50%) loops back to start)
    links = once + once
    return (
        f'<div class="ticker">'
        f'<span class="ticker-label">AFP</span>'
        f'<div class="ticker-track"><div class="ticker-items">{links}</div></div>'
        f'</div>\n'
    )
def build_geo_feed(arts):
    """Right-hand Politico panel. Today carries the daily debriefs — World in
    Brief and Playbook Paris — and everything else reads as normal."""
    today  = [a for a in arts if a["source"] in GEO_PINNED]
    normal = [a for a in arts if a["source"] not in GEO_PINNED]
    def band(label, items, cls, limit):
        if not items:
            return ""
        rows = "".join(_brick([a]) for a in items[:limit])
        return (f'<div class="mkt-tier {cls}">'
                f'<div class="mkt-tier-hd"><span>{label}</span></div>'
                f'<div class="mk-row">{rows}</div></div>')
    body = band("Today", today, "geo-today", 4) + band("Latest", normal, "geo-latest", 40)
    if not body:
        body = '<p style="font-size:11px;color:var(--meta);padding:14px 16px">No articles fetched.</p>'
    return f'<div class="geo-body" id="geo-feed">{body}</div>'

# World in Brief arrives through Google News, which carries no image, so its
# lead falls back to a colour block the way FirstFT's does.
GEO_SOURCE_COLORS = {
    "World in Brief":"#3B1512", "Playbook Paris":"#1E2A4A",
    "Le Grand Continent":"#12324A",
    "The War Room":"#2E4A1E",
    "Politico Europe":"#1E2A4A", "Politico France":"#1E2A4A",
}

def build_map(conflicts_json, articles_json, geo_arts=()):
    """Geopolitics on the same band as Markets. The lead is the day's World in
    Brief; the flashpoint list is the standing index beneath it; Politico fills
    the stack and the rail."""
    brief  = [a for a in geo_arts if a["source"] == "World in Brief"]
    lead_a = brief[0] if brief else None
    # World in Brief is the lead and nothing else — its other issues in the rail
    # were just the same daily repeated.
    rest   = [a for a in geo_arts if a["source"] != "World in Brief"]
    others = [a for a in rest if a["source"] == "Playbook Paris"][:1]
    # spread the stack across rubriques before filling by recency, so one
    # prolific rubrique doesn't take the whole column
    _lgc   = [a for a in rest if a["source"] in LGC_GEO or a["source"] == "The War Room"]
    seen, longs = set(), []
    for a in _lgc:
        if a["source"] in seen:
            continue
        seen.add(a["source"])
        longs.append(a)
    longs += [a for a in _lgc if a not in longs]
    wire   = [a for a in rest if a["source"] not in LGC_GEO
              and a["source"] not in ("Playbook Paris", "The War Room")]
    stack  = "".join(_pcard(a, GEO_SOURCE_COLORS, extra_cls="pc-row",
                            beat=LGC_BEAT.get(a["source"], ""), clamp=3)
                     for a in longs[:8])
    rail   = "".join(_brick([a], thumb=True) for a in wire[:26])
    lead_html = (_pcard(lead_a, GEO_SOURCE_COLORS, extra_cls="pc-lead", clamp=3)
                 if lead_a else "")
    return f"""
<div class="section mkt-page">
  <div class="sec-hd"><span class="sec-hd-text">Geopolitics</span></div>
  <div class="mkt-band">
    <div class="mkt-lead">
      {lead_html}
      {"".join(_brick([a], extra_cls="mk-daily") for a in others)}
      <div class="mkt-index-hd">Flashpoints</div>
      <div id="cp-grid" class="cp-grid"></div>
    </div>
    <div class="mkt-stack">
      <div class="mkt-tier-hd"><span>Features</span></div>
      {stack}
    </div>
    <div class="mkt-rail">
      <div class="mkt-tier-hd"><span>Latest</span></div>
      <div class="mkt-rail-list">{rail}</div>
    </div>
  </div>
</div>
<div class="geo-backdrop" id="geo-backdrop"></div>
<div class="geo-modal" id="geo-modal" role="dialog" aria-modal="true">
  <button class="geo-modal-x" id="geo-modal-x" aria-label="Close">&#10005;</button>
  <div class="geo-modal-hd">
    <div class="geo-modal-meta"><span class="geo-modal-chip" id="geo-modal-type"></span></div>
    <div class="geo-modal-name" id="geo-modal-name"></div>
    <div class="geo-modal-since" id="geo-modal-since"></div>
  </div>
  <div class="geo-modal-body" id="geo-modal-body"></div>
</div>
<script>
(function(){{
  var C = {conflicts_json};
  var A = {articles_json};
  var TC = {{ conflict:'#EF4444', tension:'#EF4444' }};
  var listEl = document.getElementById('cp-grid');
  function _esc(t) {{
    return String(t||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }}
  function isNew(id) {{
    var arts = A[id] || [];
    if (!arts.length) return false;
    var seen = parseInt(localStorage.getItem('seen_'+id)||'0');
    return arts.some(function(a){{ return (a.ts||0)*1000 > seen; }});
  }}
  function markSeen(id) {{
    localStorage.setItem('seen_'+id, Date.now());
  }}
  function _replacePulse(c) {{ /* no map, nothing to swap */ }}
  /* ── Conflict modal — same open/close behaviour as the culture events ── */
  var mdl   = document.getElementById('geo-modal');
  var mdlBd = document.getElementById('geo-backdrop');
  function closeModal() {{
    mdl.classList.remove('active');
    mdlBd.classList.remove('active');
  }}
  mdlBd.addEventListener('click', closeModal);
  document.getElementById('geo-modal-x').addEventListener('click', closeModal);
  document.addEventListener('keydown', function(e){{
    if (e.key === 'Escape') closeModal();
  }});

  function toggleItem(id) {{
    var c = C.find(function(x){{return x.id===id;}});
    if (!c) return;
    var arts = A[c.id]||[];
    var artsHtml = arts.length
      ? '<div class="geo-modal-hd2">Recent Coverage</div>' + arts.map(function(a){{
          return '<a href="'+_esc(a.link)+'" target="_blank" rel="noopener" class="geo-modal-art">'
            +'<span class="geo-modal-art-ttl">'+_esc(a.title)+'</span>'
            +'<span class="geo-modal-art-meta">'+_esc(a.source)+(a.ago?' · '+a.ago:'')+'</span>'
            +'</a>';
        }}).join('')
      : '<div class="geo-modal-hd2">Recent Coverage</div>'
        +'<p class="geo-modal-none">No recent articles matched.</p>';
    document.getElementById('geo-modal-type').textContent  = c.type||'conflict';
    document.getElementById('geo-modal-name').textContent  = c.name;
    document.getElementById('geo-modal-since').textContent = 'Since '+(c.started||'—');
    document.getElementById('geo-modal-body').innerHTML =
      '<div class="geo-modal-sum">'+_esc(c.summary)+'</div>'+artsHtml;
    document.getElementById('geo-modal-body').scrollTop = 0;
    mdl.classList.add('active');
    mdlBd.classList.add('active');

    markSeen(id);
    _replacePulse(c);
    var chip = listEl.querySelector('[data-id="'+id+'"]');
    if (chip) chip.classList.remove('has-new');
  }}

  /* Build the conflict-name grid */
  C.forEach(function(c){{
    var col    = TC[c.type]||'#888';
    var hasNew = isNew(c.id);

    var item = document.createElement('button');
    item.className='cp-chip'+(hasNew?' has-new':''); item.dataset.id=c.id;
    item.type='button';
    item.innerHTML=
       '<span class="dot" style="background:'+col+'"></span>'
      +'<span class="cp-chip-name">'+_esc(c.name)+'</span>';

    item.addEventListener('click',function(){{toggleItem(c.id);}});
    listEl.appendChild(item);

  }});
}})();
</script>
"""
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

def _build_group_row(g, extra_cls="", data_attrs=""):
    """Kept for its callers; every list in the brief now renders the same
    brick as the Markets page."""
    return _brick(g, extra_cls=extra_cls, data_attrs=data_attrs)

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

def build_polymarket_band(markets):
    def fmt_pct(p):
        if p < 1:  return "<1%"
        if p > 99: return ">99%"
        return f"{p}%"
    def card_html(m):
        link = (f'https://polymarket.com/event/{_s(m["slug"])}'
                if m.get("slug") else "https://polymarket.com")
        outcomes_html = "".join(
            f'<div class="poly-outcome">'
            f'<span class="poly-out-name">{_s(name)}</span>'
            f'<span class="poly-out-pct {"high" if pct>=50 else "low"}">{fmt_pct(pct)}</span>'
            f'</div>'
            for name, pct in m["pairs"]
        )
        vol = f'<div class="poly-card-vol">{_s(m["vol"])}</div>' if m.get("vol") else ""
        return (
            f'<a href="{link}" target="_blank" rel="noopener" class="poly-card">'
            f'<div class="poly-card-q">{_s(m["q"])}</div>'
            f'<div class="poly-card-outcomes">{outcomes_html}</div>'
            f'{vol}</a>'
        )
    if markets:
        once  = "".join(card_html(m) for m in markets)
        items = once + once   # duplicate for seamless loop
        inner = f'<div class="poly-band-items">{items}</div>'
    else:
        inner = '<div style="padding:0 16px;font-size:11px;color:var(--muted);align-self:center">No data</div>'
    return (
        f'<div class="poly-band">'
        f'<div class="poly-band-track">'
        f'{inner}'
        f'</div></div>\n'
    )

def build_price_band(prices):
    def fmt(v, sym):
        if v is None: return "—"
        if sym == "EURUSD=X": return f"{v:.4f}"
        if sym == "^TNX":     return f"{v:.2f}%"
        if v >= 10000: return f"{v:,.0f}"
        if v >= 1000:  return f"{v:,.1f}"
        return f"{v:.2f}"
    tiles = ""
    for p in prices:
        chg = ""
        if p["pct"] is not None:
            cls  = "up" if p["pct"] >= 0 else "dn"
            sign = "+" if p["pct"] >= 0 else ""
            chg  = f'<div class="price-tile-chg {cls}">{sign}{p["pct"]:.2f}%</div>'
        tiles += (
            f'<div class="price-tile">'
            f'<div class="price-tile-name">{_s(p["name"])}</div>'
            f'<div class="price-tile-val">{fmt(p["price"], p["sym"])}</div>'
            f'{chg}</div>'
        )
    if not tiles:
        tiles = '<span class="price-tile-loading">No price data</span>'
    return (f'<div class="price-band">'
            f'<div class="price-band-track" id="price-band-track">{tiles}</div>'
            f'</div>\n')

def _sort_by_time(groups):
    """Sort groups purely by recency of their most recent article."""
    return sorted(groups, key=lambda g: max(a["ts"] or 0 for a in g), reverse=True)

def _one_per_source(groups):
    """The Today band has only three slots — spend them on three different
    debriefs rather than letting one source take two."""
    seen, out = set(), []
    for g in groups:
        src = g[0].get("_canon") or g[0]["source"]
        if src in seen:
            continue
        seen.add(src)
        out.append(g)
    return out

def _cadence(g):
    a = g[0]
    return (SOURCE_CADENCE.get(a["source"])
            or SOURCE_CADENCE.get(a.get("_canon") or "")
            or DEFAULT_CADENCE)

def _brick(g, extra_cls="", data_attrs="", name_src=False, thumb=False, beat=""):
    """One article as a brick — Source · time over a 500-weight headline.
    This is the single row shape for the whole brief: markets, geo, sports,
    cities and Paris all render through it. Multi-source groups keep the
    click-to-expand behaviour the old rows had."""
    primary  = g[0]
    n        = len(g)
    time_str = _ago(primary["date"])
    src      = _s(primary.get("_canon") or primary["source"])
    cls      = f' {extra_cls}' if extra_cls else ""
    attrs    = f' {data_attrs}' if data_attrs else ""
    beat_html = f'<span class="mk-beat">{_s(beat)}</span>' if beat else ""
    thumb_html = ""
    if thumb and primary.get("img"):
        thumb_html = (f'<span class="mk-thumb" '
                      f'style="background-image:url({_s(primary["img"])})"></span>')
        cls += " mk-has-thumb"
    if n == 1:
        return (f'<a class="mk{cls}"{attrs} href="{_s(primary["link"])}" '
                f'target="_blank" rel="noopener">'
                f'<span class="mk-hd"><span class="mk-src">{src}</span>'
                f'{beat_html}<span class="mk-time">{time_str}</span></span>'
                f'<span class="mk-t">{_s(primary["title"])}</span>'
                f'{thumb_html}</a>\n')
    arts = "".join(
        f'<a href="{_s(a["link"])}" target="_blank" rel="noopener" class="mk-sub" '
        f'onclick="event.stopPropagation()">'
        f'<span class="mk-sub-src">{_s(a.get("_canon") or a["source"])}</span>'
        f'<span class="mk-sub-t">{_s(a["title"])}</span></a>'
        for a in g)
    title = primary.get("_headline") or primary["title"]
    # A daily debrief keeps its masthead: "3 sources" over someone else's
    # headline reads as a stray article rather than as that day's briefing.
    if name_src:
        title = primary["title"]
        hd = (f'<span class="mk-src">{src}</span>'
              f'<span class="mk-src-multi">+{n - 1}</span>{beat_html}')
    else:
        hd = f'<span class="mk-src mk-src-multi">{n} sources</span>{beat_html}'
    return (f'<div class="mk mk-multi{cls}"{attrs} '
            f'onclick="this.classList.toggle(\'open\')">'
            f'<span class="mk-hd">{hd}'
            f'<span class="mk-time">{time_str}</span></span>'
            f'<span class="mk-t">{_s(title)}</span>'
            f'<div class="mk-subs">{arts}</div></div>\n')

# Colour behind a slow-read card when no picture can be found
SLOW_SOURCE_COLORS = {
    "Not Boring":"#1E3A5F", "Silicon Carne":"#5C2118", "TBPN":"#123A2E",
    "Scott Aaronson":"#2A2A3E", "First Round Review":"#3B2A55",
    "Lenny's Newsletter":"#5A3A12", "Pragmatic Engineer":"#123A5C",
    "Bits About Money":"#2C4A3B", "Reaction Wheel":"#4A2A2A",
}

def _slow_card(g):
    """Slow reads use the same picture card as Culture and Opinions."""
    return _pcard(g[0], SLOW_SOURCE_COLORS, clamp=2)

def _tier(label, groups, cls, limit):
    if not groups:
        return ""
    render = _slow_card if cls == "mkt-slow" else _brick
    bricks = "".join(render(g) for g in groups[:limit])
    return (f'<div class="mkt-tier {cls}">'
            f'<div class="mkt-tier-hd"><span>{label}</span></div>'
            f'<div class="mk-row">{bricks}</div></div>')

def _daily_first(g):
    """Put the daily debrief at the head of its group so the row is labelled
    with that masthead rather than whichever story happened to sort first."""
    if len(g) < 2:
        return g
    for i, a in enumerate(g):
        if SOURCE_CADENCE.get(a.get("_canon") or a["source"]) == "daily":
            return [a] + g[:i] + g[i + 1:]
    return g


def _build_market_page(groups, label, hd_buttons="", colors=None,
                       lead_source="", beats=None):
    """One market as an Art Newspaper band: a lead story, a stack of long
    reads beside it, and a rail of the latest wire. Lead and stack carry
    pictures; the rail is text, as on the reference."""
    colors = colors or SLOW_SOURCE_COLORS
    beats  = beats or {}
    def _beat(g): return beats.get(g[0].get("_canon") or g[0]["source"], "")
    buckets = {"daily": [], "fast": [], "slow": []}
    for g in _sort_by_time(groups):
        buckets[_cadence(g)].append(g)
    buckets["daily"] = _one_per_source(buckets["daily"])

    lead_g = None
    if lead_source:
        # The masthead can appear in more than one group, and a cluster it is
        # buried in takes its cadence from whoever heads it — so the newest
        # issue may not even be in the daily bucket. Search all three and take
        # the latest, leaving the rest of its group where it was.
        best = None
        for bucket in ("daily", "fast", "slow"):
            for g in buckets[bucket]:
                for i, a in enumerate(g):
                    if (a.get("_canon") or a["source"]) != lead_source:
                        continue
                    if best is None or (a.get("ts") or 0) > (best[0].get("ts") or 0):
                        best = (a, g, i, bucket)
        if best:
            a, g, i, bucket = best
            lead_g = [a]
            rest_g = g[:i] + g[i + 1:]
            buckets[bucket] = [x for x in buckets[bucket] if x is not g]
            if rest_g:
                buckets[bucket].insert(0, rest_g)
    if lead_g is None:
        lead_g = buckets["daily"][0] if buckets["daily"] else (
                 buckets["fast"][0] if buckets["fast"] else None)
    seen_src, stack_g = set(), []
    for g in buckets["slow"]:                  # one per masthead first
        src = g[0].get("_canon") or g[0]["source"]
        if src in seen_src:
            continue
        seen_src.add(src)
        stack_g.append(g)
    # then fill, but no masthead gets more than three slots — Oaktree's memo
    # archive would otherwise crowd out everyone else with year-old pieces
    per_src = {src: 1 for src in seen_src}
    for g in buckets["slow"]:
        if g in stack_g:
            continue
        src = g[0].get("_canon") or g[0]["source"]
        if per_src.get(src, 0) >= 3:
            continue
        per_src[src] = per_src.get(src, 0) + 1
        stack_g.append(g)
    stack_g = stack_g[:12]
    # Only borrow from the wire if the stack would otherwise be nearly empty —
    # a "Long reads" column full of wire copy is worse than a short one.
    if len(stack_g) < 3:
        used = {id(g) for g in stack_g} | ({id(lead_g)} if lead_g else set())
        stack_g += [g for g in buckets["fast"] if id(g) not in used][:3-len(stack_g)]
    rail_g = [g for g in buckets["fast"]
              if g is not lead_g and g not in stack_g][:24]

    # every daily belongs on the lead column — the freshest as the big story,
    # the others as compact entries beneath it
    _lead_src = ((lead_g[0].get("_canon") or lead_g[0]["source"]) if lead_g else "")
    others = [_daily_first(g) for g in buckets["daily"]
              if g is not lead_g
              and (g[0].get("_canon") or g[0]["source"]) != _lead_src][:3]
    lead = ""
    if lead_g:
        lead = (f'<div class="mkt-lead">'
                f'{_pcard(lead_g[0], colors, extra_cls="pc-lead", beat=_beat(lead_g), clamp=3)}'
                + "".join(_brick(g, extra_cls="mk-daily", name_src=True,
                                 beat=_beat(g)) for g in others)
                + f'</div>')
    stack = "".join(_pcard(g[0], colors, extra_cls="pc-row", beat=_beat(g), clamp=3)
                    for g in stack_g)
    rail  = "".join(_brick(g, thumb=True, beat=_beat(g)) for g in rail_g)
    return (f'<div class="section mkt-page">'
            f'<div class="sec-hd"><span class="sec-hd-text">{label}</span>{hd_buttons}</div>'
            f'<div class="mkt-band">'
            f'{lead}'
            f'<div class="mkt-stack"><div class="mkt-tier-hd"><span>Long reads</span></div>{stack}</div>'
            f'<div class="mkt-rail"><div class="mkt-tier-hd"><span>Latest</span></div>'
            f'<div class="mkt-rail-list">{rail}</div></div>'
            f'</div></div>\n')

def build_tech(groups):
    return _build_market_page(groups, "Private Markets",
                              lead_source="MTS Newsletter")

def build_macro(groups):
    """Public Markets. The filter buttons are gone — which side a piece is from
    is said next to the source, the way Culture names its beat."""
    return _build_market_page(groups, "Public Markets",
                              colors=MACRO_SOURCE_COLORS,
                              beats=MACRO_BEAT)

def _build_cal_band_html(event_news={}, paris_arts=()):
    """Returns the HTML+JS for the compact event band embedded in the culture section."""
    today     = datetime.now()
    today_str = today.strftime("%Y-%m-%d")
    cat_col = {
        "culture":"#3a3a3c","fashion":"#EA580C","football":"#15803D",
        "f1":"#DC2626","horses":"#B45309","swimming":"#1D4ED8",
        "rowing":"#0E7490","sailing":"#0F766E","tennis":"#0284C7",
        "golf":"#166534","cycling":"#D97706","rugby":"#7E22CE",
        "music":"#DB2777","tech":"#0369A1","finance":"#374151",
    }
    cat_lbl = {
        "culture":"Culture","fashion":"Fashion","football":"Football",
        "f1":"F1","horses":"Horses","swimming":"Swimming",
        "rowing":"Rowing","sailing":"Sailing","tennis":"Tennis",
        "golf":"Golf","cycling":"Cycling","rugby":"Rugby",
        "music":"Music","tech":"Tech","finance":"Finance",
    }
    mon_abbr = ["","Jan","Feb","Mar","Apr","May","Jun",
                "Jul","Aug","Sep","Oct","Nov","Dec"]
    def fmt_range(s_str, e_str):
        from datetime import datetime as _dt
        s = _dt.strptime(s_str, "%Y-%m-%d")
        e = _dt.strptime(e_str, "%Y-%m-%d")
        if s_str == e_str: return f"{s.day} {mon_abbr[s.month]}"
        if s.year == e.year and s.month == e.month:
            return f"{s.day}–{e.day} {mon_abbr[s.month]}"
        if s.year != e.year:
            return f"{s.day} {mon_abbr[s.month]} – {e.day} {mon_abbr[e.month]} {e.year}"
        span = (e - s).days
        if span > 60: return f"{mon_abbr[s.month]} – {mon_abbr[e.month]} {s.year}"
        return f"{s.day} {mon_abbr[s.month]} – {e.day} {mon_abbr[e.month]}"
    def _ev_sort(e):
        if e["start"] <= today_str <= e["end"]: return (0, e["start"])
        if e["end"] >= today_str:               return (1, e["start"])
        return                                        (2, e["start"])
    # As a standing index this only has to answer "what's on" — everything
    # running now, plus the next one to open.
    #
    # Live events used to sort by start date, which pinned the season-long ones
    # (F1 since March, the Biennale since April) permanently above whatever was
    # actually happening that week. Now: this week's events before season-long
    # backdrops, verified before estimated within each, then soonest to close.
    from datetime import date as _date
    def _span(e):
        return (_date.fromisoformat(e["end"]) - _date.fromisoformat(e["start"])).days
    def _live_key(e):
        return (_span(e) > 45, not e.get("verified", True), e["end"])
    _live      = sorted([e for e in CALENDAR_EVENTS if e["start"] <= today_str <= e["end"]],
                        key=_live_key)
    _upcoming  = sorted([e for e in CALENDAR_EVENTS if e["start"] > today_str],
                        key=lambda e: e["start"])[:1]
    sorted_evs = _live + _upcoming
    # Paris — What's On used to be its own column. It reads as a standing
    # listing rather than news, so it sits in this index like any other event.
    if paris_arts:
        event_news = dict(event_news)
        event_news["Paris — What\u2019s On"] = [
            {"title": a["title"], "link": a["link"], "source": a["source"],
             "ago": _ago(a["date"]), "img": a.get("img", "")}
            for a in paris_arts[:20]]
        sorted_evs = [{"name": "Paris — What\u2019s On", "cat": "culture",
                       "start": today_str, "end": today_str,
                       "_range": "Ongoing"}] + sorted_evs
    scroll_idx = 0
    for i, e in enumerate(sorted_evs):
        if e["end"] >= today_str:
            scroll_idx = i
            break
    cards_html = ""
    for i, ev in enumerate(sorted_evs):
        col     = cat_col.get(ev["cat"], "#555")
        lbl     = cat_lbl.get(ev["cat"], ev["cat"])
        verified = ev.get("verified", True)
        if ev.get("_range"):
            rng = ev["_range"]
        elif verified:
            rng = fmt_range(ev["start"], ev["end"])
        else:
            # no source gives the day, so don't print one
            _s0, _e0 = ev["start"], ev["end"]
            m0 = mon_abbr[int(_s0[5:7])]; m1 = mon_abbr[int(_e0[5:7])]
            rng = (f"{m0} {_s0[:4]}" if m0 == m1 else f"{m0} – {m1} {_e0[:4]}")
        is_live = verified and ev["start"] <= today_str <= ev["end"]
        is_past = ev["end"] < today_str
        cls     = (" ev-live" if is_live else " ev-past" if is_past else "")
        live_badge = '<span class="cal-live-badge">LIVE</span>' if is_live else ""
        arts = event_news.get(ev["name"], [])
        card_img = next((a["img"] for a in arts if a.get("img")), "")
        arts_html = ""
        for a in arts:
            arts_html += (
                f'<a href="{_s(a.get("link","#"))}" target="_blank" rel="noopener" class="cal-det-art">'
                f'<span class="cal-det-art-title">{_s(a.get("title",""))}</span>'
                f'<span class="cal-det-art-meta">{_s(a.get("source",""))}'
                f'{(" · "+_s(a["ago"])) if a.get("ago") else ""}</span>'
                f'</a>'
            )
        if not arts_html:
            arts_html = '<p class="cal-det-none">No recent coverage.</p>'
        search_url = f'https://www.google.com/search?q={_s(ev["name"]+" 2026")}'
        # A picture when the coverage has one; otherwise the card carries the
        # category colour and the portal simply opens on the headline.
        if card_img:
            bg_style = (f"background-image:url({_s(card_img)});"
                        f"background-size:cover;background-position:center top")
        else:
            bg_style = (f"background:linear-gradient(150deg,rgba(255,255,255,.14) 0%,"
                        f"rgba(0,0,0,.28) 100%),{col}")
        img_cls = "" if card_img else " cal-ev-bg-flat"
        cards_html += (
            f'<div class="cal-ev-card{cls}" id="ccv-{i}" style="--evc:{col}">'
            f'<div class="cal-ev-bg{img_cls}" style="{bg_style}"></div>'
            f'<div class="cal-ev-body">'
            f'<div class="cal-ev-meta">'
            f'<span class="cal-ev-cat-chip">{_s(lbl)}</span>'
            f'{live_badge}'
            f'</div>'
            f'<div class="cal-ev-name">{_s(ev["name"])}</div>'
            f'<div class="cal-ev-range">{_s(rng)}</div>'
            f'</div>'
            f'<div class="cal-ev-panel">'
            f'<div class="cal-ev-panel-hd">Recent Coverage</div>'
            f'{arts_html}'
            f'<a href="{search_url}" target="_blank" rel="noopener" class="cal-search-link">Search Google →</a>'
            f'</div>'
            f'</div>\n'
        )
    js = f"""<script>
(function(){{
  var band=document.getElementById('culture-cal-band');
  var all=[].slice.call(band.querySelectorAll('.cal-ev-card'));
  /* backdrop */
  var bd=document.createElement('div');
  bd.className='ccv-backdrop';
  document.body.appendChild(bd);
  /* portal: active card moved to body so it escapes band stacking context */
  var activeCard=null, placeholder=null;
  function closeAll(){{
    if(activeCard){{
      activeCard.classList.remove('cal-ev-portal-open');
      if(placeholder&&placeholder.parentNode){{
        placeholder.parentNode.insertBefore(activeCard,placeholder);
        placeholder.remove();
      }}
      activeCard=null;placeholder=null;
    }}
    bd.classList.remove('active');
    document.body.style.overflow='';
  }}
  bd.addEventListener('click',closeAll);
  document.addEventListener('keydown',function(e){{if(e.key==='Escape')closeAll();}});
  function setCenter(card){{
    all.forEach(function(c){{c.classList.remove('ev-center');}});
    if(card)card.classList.add('ev-center');
  }}
  function scrollToCard(card){{
    var target=card.offsetLeft+card.offsetWidth/2-band.clientWidth/2;
    band.scrollTo({{left:Math.max(0,target),behavior:'smooth'}});
  }}
  all.forEach(function(card){{
    if(card.classList.contains('ev-past'))return;
    card.addEventListener('click',function(){{
      if(activeCard===card)return;
      closeAll();
      /* portal: move card to body so it escapes band stacking context */
      placeholder=document.createElement('div');
      placeholder.style.cssText='flex:0 0 12%;opacity:.4;pointer-events:none';
      card.parentNode.insertBefore(placeholder,card);
      document.body.appendChild(card);
      card.classList.add('cal-ev-portal-open');
      bd.classList.add('active');
      document.body.style.overflow='hidden';
      activeCard=card;
    }});
  }});
  var si=document.getElementById('ccv-{scroll_idx}');
  if(si&&!si.classList.contains('ev-past')){{
    setCenter(si);
    setTimeout(function(){{
      var target=si.offsetLeft+si.offsetWidth/2-band.clientWidth/2;
      band.scrollLeft=Math.max(0,target);
    }},120);
  }}
}})();
</script>"""
    return f'<div class="culture-cal-band" id="culture-cal-band">{cards_html}</div>{js}'


# ══════════════════════════════════════════════════════════════════════════════
#  GENERATIVE TILES — for the slots where a picture cannot be had
# ══════════════════════════════════════════════════════════════════════════════
def _flat_art(seed_str, col):
    """A constructivist tile in place of a photograph.

    FT and the Economist answer 403 to any script and Google News never resolves
    to the article, so a handful of slots can never carry a picture. Rather than
    a flat colour field — which reads as a page still loading — each of those
    draws a composition in the International Typographic idiom the rest of the
    page is built on: arcs, bars, modular fields.

    The seed is the article link, so a story keeps its own tile across rebuilds
    rather than flickering into a new one every hour. Everything is a few large
    shapes, because these render at 100x72 in a stack row as well as 472x360 on
    a lead."""
    b = hashlib.md5(seed_str.encode("utf-8")).digest()
    n = lambda i, lo, hi: lo + b[i % 16] % (hi - lo + 1)   # stable pick
    W, H = 400, 300
    ink = lambda a: f'rgba(255,255,255,{a})'
    p = []

    # md5's first byte alone clustered badly on these seeds — five of eight
    # drew the same figure. Folding four bytes spreads them evenly.
    kind = (b[0] ^ b[5] ^ b[9] ^ b[13]) % 5
    if kind == 0:                                   # concentric arcs from a corner
        cx, cy = (0, H) if b[1] % 2 else (W, 0)
        for i in range(n(2, 5, 8)):
            r = 40 + i * n(3, 34, 52)
            p.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" '
                     f'stroke="{ink(round(.30 - i * .028, 3))}" stroke-width="14"/>')
    elif kind == 1:                                 # a rhythm of vertical bars
        x, i = 0, 0
        while x < W and i < 9:
            w = n(2 + i, 18, 74)
            if i % 2 == 0:
                p.append(f'<rect x="{x}" y="0" width="{w}" height="{H}" '
                         f'fill="{ink(round(.07 + (i % 3) * .07, 3))}"/>')
            x += w; i += 1
    elif kind == 2:                                 # a modular field of circles
        cols, rows = n(1, 3, 5), 3
        cw, ch = W / cols, H / rows
        for r_ in range(rows):
            for c in range(cols):
                k = b[(r_ * cols + c) % 16]
                if k % 4 == 0:
                    continue
                rad = (min(cw, ch) / 2) * (0.34 + (k % 5) * 0.13)
                p.append(f'<circle cx="{cw*(c+.5):.0f}" cy="{ch*(r_+.5):.0f}" '
                         f'r="{rad:.0f}" fill="{ink(round(.10 + (k % 4) * .07, 3))}"/>')
    elif kind == 3:                                 # diagonal bands
        step = n(1, 46, 78)
        for i in range(-2, 10):
            x = i * step
            p.append(f'<path d="M{x} {H} L{x+step*0.55:.0f} {H} '
                     f'L{x+step*0.55+H:.0f} 0 L{x+H} 0 Z" '
                     f'fill="{ink(round(.08 + (i % 3) * .06, 3))}"/>')
    else:                                           # blocks on a 4x3 module field
        mw, mh = W / 4, H / 3
        placed = []
        for i in range(n(1, 3, 5)):
            c, r_ = b[(i*2) % 16] % 4, b[(i*2+1) % 16] % 3
            cs_ = 1 + b[(i*3) % 16] % (4 - c)       # span, clipped to the field
            rs = 1 + b[(i*3+2) % 16] % (3 - r_)
            if (c, r_) in placed:
                continue
            placed.append((c, r_))
            p.append(f'<rect x="{c*mw:.0f}" y="{r_*mh:.0f}" '
                     f'width="{cs_*mw:.0f}" height="{rs*mh:.0f}" '
                     f'fill="{ink(round(.09 + (i % 3) * .08, 3))}"/>')

    return (f'<svg class="pc-art" viewBox="0 0 {W} {H}" '
            f'preserveAspectRatio="xMidYMid slice" aria-hidden="true" '
            f'focusable="false">{"".join(p)}</svg>')


# ══════════════════════════════════════════════════════════════════════════════
#  THE MARK — brush straps
# ══════════════════════════════════════════════════════════════════════════════
"""Brush straps in the hand of the reference painting.

Two earlier attempts got this wrong in opposite directions: the first put
high-frequency jitter on a symmetric ribbon (shaky), the second tapered hard
with independently curving edges (spiky blades). The reference is neither.

Looking at it properly, a strap is:
  * near CONSTANT width down its length — it swells and narrows a little, but
    never tapers to a point;
  * varied on each edge independently, and slowly — three or four undulations
    over the whole run, so the silhouette is asymmetric but calm;
  * long and narrow, roughly 1:12;
  * blunt at both ends, cut square or on a slight slant;
  * sometimes broken in two, at heights that differ from strap to strap.

Built as a dense polyline rather than a spline: with 60 samples the outline is
smooth on screen and, unlike a Q/T chain, it cannot collapse.
"""


def _strap_smooth(vals, n):
    """Catmull-Rom through a few knots — slow undulation, no jitter."""
    out, m = [], len(vals) - 1
    for i in range(n + 1):
        t = i / n * m
        k = min(int(t), m - 1)
        f = t - k
        p0, p1 = vals[max(k - 1, 0)], vals[k]
        p2, p3 = vals[min(k + 1, m)], vals[min(k + 2, m)]
        out.append(0.5 * ((2*p1) + (-p0 + p2)*f
                          + (2*p0 - 5*p1 + 4*p2 - p3)*f*f
                          + (-p0 + 3*p1 - 3*p2 + p3)*f*f*f))
    return out


def _strap(x, top, bot, w, seed, slant=None):
    r = random.Random(seed)
    n = 60
    lw = _strap_smooth([r.uniform(0.84, 1.14) for _ in range(4)], n)   # left edge
    rw = _strap_smooth([r.uniform(0.84, 1.14) for _ in range(4)], n)   # right edge
    lean = _strap_smooth([r.uniform(-0.4, 0.4) for _ in range(3)], n)  # a slight drift
    trend = r.uniform(0.86, 1.06)          # a touch wider or narrower at the foot
    L, R = [], []
    for i in range(n + 1):
        t = i / n
        y = top + (bot - top) * t
        cx = x + lean[i] * w * 0.4
        half = w / 2 * (1 + (trend - 1) * t)
        L.append((cx - half * lw[i], y))
        R.append((cx + half * rw[i], y))
    # blunt ends, cut on a slight slant
    s0 = r.uniform(-0.5, 0.5) * w if slant is None else slant * w
    s1 = r.uniform(-0.5, 0.5) * w
    L[0] = (L[0][0], L[0][1] + s0);  R[0] = (R[0][0], R[0][1] - s0)
    L[-1] = (L[-1][0], L[-1][1] - s1); R[-1] = (R[-1][0], R[-1][1] + s1)
    pts = L + list(reversed(R))
    return "M" + "L".join(f"{p[0]:.1f} {p[1]:.1f}" for p in pts) + "Z"


def _strap_split(x, top, bot, w, seed):
    r = random.Random(seed + 5)
    span = bot - top
    at = r.uniform(0.30, 0.72)
    gap = r.uniform(0.015, 0.05)
    return (_strap(x, top, top + span * (at - gap / 2), w * r.uniform(0.96, 1.04), seed)
            + " " + _strap(x, top + span * (at + gap / 2), bot,
                          w * r.uniform(0.90, 1.06), seed + 991))


def _logo_field(seed0=1971, n=12, W=520, H=216):
    """The hero lockup: a row of straps sized as a rule above the name."""
    step, d = W / n, []
    for i in range(n):
        r = random.Random(seed0 + i * 37)
        x, ww = step * (i + .55), step * r.uniform(.32, .44)
        top, bot = H * r.uniform(.02, .11), H * r.uniform(.90, 1.0)
        d.append(_strap_split(x, top, bot, ww, seed0 + i * 37) if r.random() < .42
                 else _strap(x, top, bot, ww, seed0 + i * 37))
    return (f'<svg class="logo-field" viewBox="0 0 {W} {H}" fill="currentColor" '
            f'aria-hidden="true" xmlns="http://www.w3.org/2000/svg">'
            + "".join(f'<path d="{p}"/>' for p in d) + '</svg>')


def _logo_mark(seed0=404, n=5, S=160):
    """The icon: five straps in a square, the same hand at a smaller scale."""
    step, d = S / n, []
    for i in range(n):
        r = random.Random(seed0 + i * 53)
        x, ww = step * (i + .5), step * r.uniform(.34, .46)
        top, bot = S * r.uniform(.06, .14), S * r.uniform(.86, .94)
        d.append(_strap_split(x, top, bot, ww, seed0 + i * 53) if i in (1, 3)
                 else _strap(x, top, bot, ww, seed0 + i * 53))
    return (f'<svg viewBox="0 0 {S} {S}" xmlns="http://www.w3.org/2000/svg">'
            f'<rect width="{S}" height="{S}" fill="#002FA7"/>'
            + "".join(f'<path d="{p}" fill="#fff"/>' for p in d) + '</svg>')


def _pcard(a, colors, extra_cls="", beat="", clamp=3):
    """Picture card in the reference's shape: source · beat · time, then the
    headline, then the image *below* — rather than text laid over a
    full-bleed photo. Falls back to a colour block when there is no image."""
    img  = a.get("img","")
    src  = a.get("_canon") or a["source"]
    col  = colors.get(src, colors.get(a["source"], "#2A2A2E"))
    snip = _s(a.get("snip","") or "")
    meta = f'<span class="pc-src">{_s(src)}</span>'
    if beat:
        meta += f'<span class="pc-beat">{_s(beat)}</span>'
    meta += f'<span class="pc-time">{_ago(a["date"])}</span>'
    if img:
        media = (f'<span class="pc-img" style="background-image:url({_s(img)})"></span>')
        cls = "pcard"
    else:
        media = (f'<span class="pc-img pc-img-flat" style="--pc-col:{col}">'
                 f'{_flat_art(a.get("link") or a["title"], col)}</span>')
        cls = "pcard pc-noimg"
    snip_html = f'<span class="pc-snip">{snip}</span>' if snip else ""
    return (f'<a href="{_s(a["link"])}" target="_blank" rel="noopener" '
            f'class="{cls}{" "+extra_cls if extra_cls else ""}">'
            f'<span class="pc-hd">{meta}</span>'
            f'<span class="pc-t" style="-webkit-line-clamp:{clamp}">{_s(a["title"])}</span>'
            f'{snip_html}{media}</a>\n')

def build_culture(arts, event_news={}, paris_arts=()):
    """Culture on the same band. No daily here, so the lead is simply the
    freshest picture story; the events agenda sits beneath it as the standing
    index, the long-form sources fill the stack, and the rest is the rail."""
    with_img = [a for a in arts if a.get("img")]
    lead_a   = with_img[0] if with_img else (arts[0] if arts else None)
    longs    = [a for a in arts if a["source"] in CULTURE_LONG_READ and a is not lead_a][:10]
    used     = {id(a) for a in longs} | ({id(lead_a)} if lead_a else set())
    rest     = [a for a in arts if id(a) not in used][:26]

    def beat(a): return CULTURE_BEAT.get(a["source"], "")
    lead = (_pcard(lead_a, CULTURE_SOURCE_COLORS, extra_cls="pc-lead",
                   beat=beat(lead_a), clamp=3) if lead_a else "")
    stack = "".join(_pcard(a, CULTURE_SOURCE_COLORS, extra_cls="pc-row",
                           beat=beat(a), clamp=3) for a in longs)
    rail  = "".join(_brick([a], thumb=True) for a in rest)
    cal_band = _build_cal_band_html(event_news, paris_arts)
    body = (f'<div class="mkt-band">'
            f'  <div class="mkt-lead">{lead}'
            f'    <div class="mkt-index-hd">What\'s on</div>{cal_band}'
            f'  </div>'
            f'  <div class="mkt-stack"><div class="mkt-tier-hd"><span>Long reads</span></div>{stack}</div>'
            f'  <div class="mkt-rail"><div class="mkt-tier-hd"><span>Latest</span></div>'
            f'    <div class="mkt-rail-list">{rail}</div></div>'
            f'</div>')
    return (f'<div class="section mkt-page">'
            f'<div class="sec-hd"><span class="sec-hd-text">Culture</span></div>'
            f'{body}</div>\n')

def build_paris(arts):
    rows = ""
    for a in arts[:20]:
        rows += _brick([a])
    if not rows:
        rows = '<p style="font-size:11px;color:var(--dim);padding:14px 0">No Paris events fetched — feeds may be unavailable.</p>'
    return _sec("#0C0C0C","Paris — What’s On",
                f'<div class="paris-list">{rows}</div>')
def build_gossip(arts):
    """Opinions on the same three columns as the rest of the brief. There is no
    daily and no wire here — every piece is the same kind of thing — so the
    columns carry no tier labels and the order is simply newest first."""
    if not arts:
        return ('<div class="section mkt-page"><div class="sec-hd">'
                '<span class="sec-hd-text">Opinions</span></div>'
                '<p style="font-size:11px;color:var(--dim);padding:20px">'
                'No articles fetched.</p></div>')
    # two sources feed this now, so size the stack to what is actually there
    # rather than leaving the rail with a couple of orphans
    n_stack = min(8, max(3, (len(arts) - 1) // 2))
    def beat(a): return LGC_BEAT.get(a["source"], "")
    # Order is otherwise strict arrival, but the lead is the one slot big enough
    # that a generated tile is obvious — so it takes the freshest piece that has
    # a real picture, and only falls back to the newest if none does.
    lead_a = next((a for a in arts if a.get("img")), arts[0])
    rest   = [a for a in arts if a is not lead_a]
    lead  = _pcard(lead_a, GOSSIP_SOURCE_COLORS, extra_cls="pc-lead",
                   beat=beat(lead_a), clamp=3)
    stack = "".join(_pcard(a, GOSSIP_SOURCE_COLORS, extra_cls="pc-row",
                           beat=beat(a), clamp=3)
                    for a in rest[:n_stack])
    rail  = "".join(_brick([a], thumb=True, beat=beat(a))
                    for a in rest[n_stack:34])
    return (f'<div class="section mkt-page">'
            f'<div class="sec-hd"><span class="sec-hd-text">Opinions</span></div>'
            f'<div class="mkt-band">'
            f'<div class="mkt-lead">{lead}</div>'
            f'<div class="mkt-stack">{stack}</div>'
            f'<div class="mkt-rail"><div class="mkt-rail-list">{rail}</div></div>'
            f'</div></div>\n')

def build_calendar(event_news={}):
    today     = datetime.now()
    today_str = today.strftime("%Y-%m-%d")

    cat_col = {
        "culture":"#3a3a3c","fashion":"#EA580C","football":"#15803D",
        "f1":"#DC2626","horses":"#B45309","swimming":"#1D4ED8",
        "rowing":"#0E7490","sailing":"#0F766E","tennis":"#0284C7",
        "golf":"#166534","cycling":"#D97706","rugby":"#7E22CE",
        "music":"#DB2777","tech":"#0369A1","finance":"#374151",
    }
    cat_lbl = {
        "culture":"Culture","fashion":"Fashion","football":"Football",
        "f1":"F1","horses":"Horses","swimming":"Swimming",
        "rowing":"Rowing","sailing":"Sailing","tennis":"Tennis",
        "golf":"Golf","cycling":"Cycling","rugby":"Rugby",
        "music":"Music","tech":"Tech","finance":"Finance",
    }
    mon_abbr = ["","Jan","Feb","Mar","Apr","May","Jun",
                "Jul","Aug","Sep","Oct","Nov","Dec"]

    def fmt_range(s_str, e_str):
        from datetime import datetime as _dt
        s = _dt.strptime(s_str, "%Y-%m-%d")
        e = _dt.strptime(e_str, "%Y-%m-%d")
        if s_str == e_str:
            return f"{s.day} {mon_abbr[s.month]}"
        if s.year == e.year and s.month == e.month:
            return f"{s.day}–{e.day} {mon_abbr[s.month]}"
        if s.year != e.year:
            return f"{s.day} {mon_abbr[s.month]} – {e.day} {mon_abbr[e.month]} {e.year}"
        span = (e - s).days
        if span > 60:
            return f"{mon_abbr[s.month]} – {mon_abbr[e.month]} {s.year}"
        return f"{s.day} {mon_abbr[s.month]} – {e.day} {mon_abbr[e.month]}"

    # Sort: live first → upcoming → past (chronological within each group)
    def _ev_sort(e):
        if e["start"] <= today_str <= e["end"]: return (0, e["start"])
        if e["end"] >= today_str:               return (1, e["start"])
        return                                        (2, e["start"])
    sorted_evs = sorted(CALENDAR_EVENTS, key=_ev_sort)

    # First non-past event (live events now come first, so idx 0 is live or next up)
    scroll_idx = 0
    for i, e in enumerate(sorted_evs):
        if e["end"] >= today_str:
            scroll_idx = i
            break

    cards_html = ""
    for i, e in enumerate(sorted_evs):
        col     = cat_col.get(e["cat"], "#555")
        lbl     = cat_lbl.get(e["cat"], e["cat"])
        rng     = fmt_range(e["start"], e["end"])
        is_live = e["start"] <= today_str <= e["end"]
        is_past = e["end"] < today_str
        cls     = (" ev-live" if is_live else " ev-past" if is_past else "")
        live_badge = '<span class="cal-live-badge">LIVE</span>' if is_live else ""

        arts = event_news.get(e["name"], [])
        # Use first article image as card background if available
        card_img = next((a["img"] for a in arts if a.get("img")), "")
        arts_html = ""
        for a in arts:
            arts_html += (
                f'<a href="{_s(a.get("link","#"))}" target="_blank" rel="noopener" class="cal-det-art">'
                f'<span class="cal-det-art-title">{_s(a.get("title",""))}</span>'
                f'<span class="cal-det-art-meta">{_s(a.get("source",""))}'
                f'{(" · "+_s(a["ago"])) if a.get("ago") else ""}</span>'
                f'</a>'
            )
        if not arts_html:
            arts_html = '<p class="cal-det-none">No recent coverage.</p>'
        search_url = f'https://www.google.com/search?q={_s(e["name"]+" 2026")}'
        # Background: photo if available, otherwise colour gradient
        if card_img:
            bg_style = (f"background-image:url({_s(card_img)});"
                        f"background-size:cover;background-position:center top")
        else:
            bg_style = (f"background:linear-gradient(135deg,{col} 0%,"
                        f"rgba(8,8,8,.97) 65%)")

        cards_html += (
            f'<div class="cal-ev-card{cls}" id="cev-{i}" style="--evc:{col}">'
            f'<div class="cal-ev-bg" style="{bg_style}"></div>'
            f'<div class="cal-ev-body">'
            f'<div class="cal-ev-meta">'
            f'<span class="cal-ev-cat-chip">{_s(lbl)}</span>'
            f'{live_badge}'
            f'</div>'
            f'<div class="cal-ev-name">{_s(e["name"])}</div>'
            f'<div class="cal-ev-range">{_s(rng)}</div>'
            f'</div>'
            f'<div class="cal-ev-panel">'
            f'<div class="cal-ev-panel-hd">Recent Coverage</div>'
            f'{arts_html}'
            f'<a href="{search_url}" target="_blank" rel="noopener" class="cal-search-link">Search Google →</a>'
            f'</div>'
            f'</div>\n'
        )

    body = f"""<div class="cal-band" id="cal-band">{cards_html}</div>
<script>
(function(){{
  var band=document.getElementById('cal-band');
  var all=[].slice.call(band.querySelectorAll('.cal-ev-card'));
  var hoverTimer=null; /* single shared timer — cancelled on any mouseleave */

  /* ── helpers ── */
  function setCenter(card){{
    all.forEach(function(c){{c.classList.remove('ev-center');}});
    if(card)card.classList.add('ev-center');
  }}

  function scrollToCard(card){{
    var target=card.offsetLeft+card.offsetWidth/2-band.clientWidth/2;
    band.scrollTo({{left:Math.max(0,target),behavior:'smooth'}});
  }}

  /* ── hover: only expand+scroll after mouse rests 220ms on a card ── */
  all.forEach(function(card){{
    if(card.classList.contains('ev-past'))return;

    card.addEventListener('mouseenter',function(){{
      clearTimeout(hoverTimer);
      hoverTimer=setTimeout(function(){{
        setCenter(card);
        /* wait for flex expansion to progress, then scroll */
        setTimeout(function(){{scrollToCard(card);}},80);
      }},220);
    }});

    card.addEventListener('mouseleave',function(){{
      /* cancel any pending expand/scroll immediately */
      clearTimeout(hoverTimer);
      if(!card.classList.contains('ev-open')){{
        setCenter(null);
      }}
    }});

    /* ── click: toggle article panel ── */
    card.addEventListener('click',function(){{
      var isOpen=card.classList.contains('ev-open');
      all.forEach(function(c){{c.classList.remove('ev-open');}});
      if(!isOpen){{
        card.classList.add('ev-open');
        setCenter(card);
        setTimeout(function(){{scrollToCard(card);}},80);
      }}
    }});
  }});

  /* ── on load: scroll to first live / upcoming event ── */
  var si=document.getElementById('cev-{scroll_idx}');
  if(si&&!si.classList.contains('ev-past')){{
    setCenter(si);
    setTimeout(function(){{
      var target=si.offsetLeft+si.offsetWidth/2-band.clientWidth/2;
      band.scrollLeft=Math.max(0,target);
    }},120);
  }}
}})();
</script>"""
    return _sec("#0C0C0C","Competitions &amp; Festivals 2026–27", body)

# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════
def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Morning Brief v4 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("─"*54)
    print("  Loading OpenAI…")
    ai_client     = _load_openai()
    headline_cache = _load_headline_cache()
    print("  Fetching AFP (Telegram)…")
    tg_arts  = _fetch_telegram()
    afp      = _route_afp(tg_arts)
    print("  Fetching Tech/VC…")
    les_echos_tech = _fetch_les_echos(LES_ECHOS_TECH_KW, "tech")
    tech_raw = _dedup_exact(_filter_recent(_fetch(TECH_SOURCES) + les_echos_tech + afp["tech"]))
    _ever = _fetch_evergreen(TECH_EVERGREEN)
    _ever_links = {a["link"] for a in _ever if a.get("link")}
    tech_raw = _ever + [a for a in tech_raw if a.get("link") not in _ever_links]
    print(f"    → {len(_ever)} evergreen ({', '.join(a['source'] for a in _ever) or 'none'})")
    # the Slow reads band renders picture cards, so those articles need images
    _slow = [a for a in tech_raw
             if SOURCE_CADENCE.get(a["source"]) == "slow" and not a.get("img")]
    if _slow:
        _backfill_images(_slow, limit=12)
    _backfill_images(tech_raw, limit=30)
    tech_grp = _dedup(tech_raw)
    print(f"    → {len(tech_raw)} articles → {len(tech_grp)} stories")
    print("  Fetching Macro…")
    macro_raw = _cap_per_source(_dedup_exact(_filter_recent(
        _drop_audio_dupes(_strip_gn_suffix(_keep_block_daily(_fetch(MACRO_SOURCES))))
        + afp["macro"])))
    _backfill_images(macro_raw, limit=30)
    macro_grp = _dedup(macro_raw)
    print(f"    → {len(macro_raw)} articles → {len(macro_grp)} stories")
    print("  Fetching Culture…")
    art_newspaper_arts = _fetch_art_newspaper(5)
    nss_arts = _fetch_nss(10)
    print(f"    → {len(art_newspaper_arts)} NYT Arts + {len(nss_arts)} NSS articles (pinned)")
    culture_raw = _cap_per_source(_dedup_exact(_filter_recent(_fetch(CULTURE_SOURCES))))
    # Prepend pinned articles (NYT Arts + NSS) so they survive dedup and always appear
    pinned = art_newspaper_arts + nss_arts
    seen_links = {a["link"] for a in pinned if a.get("link")}
    culture_arts = pinned + [a for a in culture_raw if a.get("link") not in seen_links]
    # try for a real picture before falling back to a colour tile
    _backfill_images(culture_arts, limit=40)
    _c_img = sum(1 for a in culture_arts if a.get("img"))
    print(f"    → {len(culture_arts)} articles total, "
          f"{_c_img} with image / {len(culture_arts)-_c_img} colour tiles")
    print("  Fetching conflict news…")
    conflict_pool = _dedup_exact(_filter_recent(_fetch(CONFLICT_NEWS_SOURCES) + afp["conflict"]))
    print(f"    → {len(conflict_pool)} articles for conflict matching")
    print("  Fetching Paris…")
    # Paris What's On: fetch then strip TV/radio show listings
    _PARIS_BLOCKLIST = [re.compile(p, re.IGNORECASE) for p in [
        r"\bBFM\b", r"\bLCI\b", r"\bTF1\b", r"\bFrance\s*[2345]\b",
        r"\bM6\b", r"\bC8\b", r"\bCNews\b", r"semaine \d+",
        r"week.end\s+(tv|radio)", r"direct (tv|bfm|lci)",
        r"matinale", r"grand rendez.vous", r"politique s.éclair",
        r"informés de l.europe", r"vérificateurs", r"franchise",
        r"monacoscope",
    ]]
    def _not_tv(a):
        t = (a.get("title","") or "")
        return not any(p.search(t) for p in _PARIS_BLOCKLIST)
    paris_arts = list(filter(_not_tv, _dedup_exact(_filter_recent(_fetch(PARIS_SOURCES)))))
    _backfill_images(paris_arts[:8], limit=6)
    print(f"    → {len(paris_arts)} Paris articles, "
          f"{sum(1 for a in paris_arts if a.get('img'))} with image")
    print("  Fetching Opinions…")
    # Les Echos Idées now comes straight from its section feed in
    # GOSSIP_SOURCES_OTHER, so no keyword pass over the general feed is needed.
    _gossip_all = _dedup_exact(_fetch(GOSSIP_SOURCES_OTHER))
    # Per-source time window filtering
    _now_ts = datetime.now(timezone.utc).timestamp()
    gossip_raw = [
        a for a in _gossip_all
        if not a.get("ts") or
           a["ts"] >= _now_ts - GOSSIP_WINDOW_DAYS.get(a["source"], 7) * 86400
    ]
    gossip_raw.sort(key=lambda a: a["date"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    gossip_raw = _dedup_smart(gossip_raw)
    gossip_raw.sort(key=lambda a: a["date"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    gossip_raw = _cap_per_source(gossip_raw)   # newest N per source
    gossip_raw = gossip_raw[:40]
    _backfill_images(gossip_raw, limit=40)
    _n_img = sum(1 for a in gossip_raw if a.get("img"))
    print(f"    → {len(gossip_raw)} gossip articles (after dedup), "
          f"{_n_img} with image / {len(gossip_raw)-_n_img} colour tiles")
    # The Polymarket and price bands were removed from the Markets page, so
    # their fetches are gone too — no point paying for data nothing renders.
    tech_grp  = _enrich_groups(tech_grp,  ai_client, headline_cache)
    macro_grp = _enrich_groups(macro_grp, ai_client, headline_cache)
    _save_headline_cache(headline_cache)
    print(f"    → {sum(1 for g in tech_grp+macro_grp if len(g)>1)} groups enriched")
    global CALENDAR_EVENTS
    print("  Checking the event calendar…")
    CALENDAR_EVENTS = _build_calendar_events()
    print("  Fetching calendar event news…")
    event_news = _fetch_calendar_event_news()
    print(f"    → {len(event_news)} events with coverage")
    raw_match = _match_conflicts(conflict_pool)
    conf_arts_js = {
        cid: [{"title":a["title"],"link":a["link"],
               "source":a["source"],"ago":_ago(a["date"]),"ts":a["ts"]}
              for a in arts]
        for cid, arts in raw_match.items()
    }
    conf_js = [{k:v for k,v in c.items() if k!="keywords"} for c in CONFLICTS]
    print("  Fetching Politico (geo panel)…")
    # Politico gets 48h; the weekly window is pinned to the same value so
    # Playbook Paris can't reach back further than the rest of the panel.
    geo_arts = _dedup_exact(_filter_recent(
        _keep_war_room(_keep_world_brief(_fetch(GEO_SOURCES))),
        days=2, weekly_days=2))
    # World in Brief is a daily — only ever show the current issue, not the
    # back catalogue Google News returns.
    _wib_cut = datetime.now(timezone.utc).timestamp() - 86400
    geo_arts = [a for a in geo_arts
                if a["source"] != "World in Brief"
                or (a.get("ts") and a["ts"] >= _wib_cut)]
    geo_arts.sort(key=lambda a: a["date"] or datetime.min.replace(tzinfo=timezone.utc),
                  reverse=True)
    _backfill_images(geo_arts, limit=30)
    print(f"    → {len(geo_arts)} geo articles, "
          f"{sum(1 for a in geo_arts if a.get('img'))} with image")
    now_paris = datetime.now(_PARIS)
    now_str   = now_paris.strftime("%A %d %B %Y — %H:%M")
    # Count articles published since midnight Paris time
    today_start_ts = now_paris.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    all_arts = (
        list(tech_raw) + list(macro_raw) + list(culture_arts)
        + list(conflict_pool) + list(paris_arts)
    )
    new_today = sum(1 for a in all_arts if a.get("ts") and a["ts"] >= today_start_ts)
    new_today_str = f"{new_today} new article{'s' if new_today != 1 else ''} today"
    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Morning Brief</title>
<meta name="theme-color" media="(prefers-color-scheme: light)" content="#ffffff">
<meta name="theme-color" media="(prefers-color-scheme: dark)" content="#060606">
<link rel="manifest" href="manifest.webmanifest">
<link rel="icon" type="image/svg+xml" href="icon.svg">
<link rel="apple-touch-icon" href="icons/icon-180.png">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Brief">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,500;1,400;1,600&family=DM+Sans:opsz,wght@9..40,300;9..40,400;9..40,500&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head>
<body>
<script src="https://cdn.jsdelivr.net/npm/topojson-client@3/dist/topojson-client.min.js"></script>

<!-- ① HERO -->
<section class="snap-sec hero-sec">
  <h1 class="hero-h1">Morning<br>Brief</h1>
  <div class="hero-meta">
    <span class="hero-count">{new_today_str}</span>
    <span class="hero-sep">·</span>
    <span class="hero-date-str" id="hero-time">{now_str}</span>
    <button class="btn" onclick="location.reload()" style="margin-left:8px">↻</button>
  </div>
  <span class="hero-hint">Scroll to explore ↓</span>
  <script>
  (function(){{
    function _updateTime(){{
      var el = document.getElementById('hero-time');
      if (!el) return;
      var now = new Date();
      var opts = {{timeZone:'Europe/Paris',weekday:'long',day:'2-digit',month:'long',year:'numeric',hour:'2-digit',minute:'2-digit',hour12:false}};
      var parts = new Intl.DateTimeFormat('en-GB', opts).formatToParts(now);
      var get = function(t){{ return (parts.find(function(p){{return p.type===t;}})||{{}}).value||''; }};
      el.textContent = get('weekday')+' '+get('day')+' '+get('month')+' '+get('year')+' — '+get('hour')+':'+get('minute');
    }}
    _updateTime();
    setInterval(_updateTime, 30000);
  }})();
  </script>
  {build_ticker(afp["ticker"])}
</section>

<!-- ② GEOPOLITICAL -->
<section class="snap-sec snap-geo">
{build_map(json.dumps(conf_js, ensure_ascii=False),
           json.dumps(conf_arts_js, ensure_ascii=False),
           geo_arts)}
</section>

<!-- ③ PRIVATE MARKETS -->
<section class="snap-sec snap-feed snap-private">
{build_tech(tech_grp)}
</section>

<!-- ④ PUBLIC MARKETS -->
<section class="snap-sec snap-feed snap-macro">
{build_macro(macro_grp)}
</section>

<!-- ④ CULTURE + EVENTS -->
<section class="snap-sec snap-culture">
{build_culture(culture_arts, event_news, paris_arts)}
</section>

<!-- ⑥ GOSSIP -->
<section class="snap-sec snap-gossip">
{build_gossip(gossip_raw)}
</section>

<!-- Phone -->
{PHONE_APP}

<!-- Unread dot tracker -->
<script>
(function(){{
  var KEY='mb_read';
  var read;
  try{{read=new Set(JSON.parse(localStorage.getItem(KEY)||'[]'));}}
  catch(e){{read=new Set();}}
  function save(){{
    try{{localStorage.setItem(KEY,JSON.stringify([...read].slice(-8000)));}}catch(e){{}}
  }}
  function addDot(a,isCard){{
    var url=a.getAttribute('href');
    if(!url||url==='#'||read.has(url)) return;
    var dot=document.createElement('span');
    dot.className='unread-dot';
    if(isCard){{a.appendChild(dot);}}
    else{{a.insertBefore(dot,a.firstChild);}}
    a.addEventListener('click',function(){{read.add(url);save();dot.remove();}},{{once:true}});
  }}
  // List-style article links
  document.querySelectorAll('.sg-title[target="_blank"],.pi-title[target="_blank"]').forEach(function(a){{addDot(a,false);}});
  // Card-style tiles
  document.querySelectorAll('.card[target="_blank"],.gos-card[target="_blank"]').forEach(function(a){{addDot(a,true);}});
}})();
</script>
</body>
</html>"""
    # A build that fetched nothing still renders perfectly well: the shell is
    # 147KB of stylesheet and script with not one article in it, and it weighs
    # enough to look like a real page. Writing that over a good index.html
    # blanks the site until the next cron — which is exactly what happened on
    # 27 September. So count what actually reached the page, and if there is
    # essentially nothing, leave the last good file alone and fail loudly.
    cards = page.count('class="pcard')
    rows  = page.count('<span class="mk-t">')
    if cards + rows < 20:
        print("─"*54)
        print(f"✗  Refusing to write: {cards} cards + {rows} rows is an empty page.")
        print(f"   {OUTPUT_FILE.name} left as it was. Check the network and re-run.")
        raise SystemExit(1)
    (OUTPUT_FILE.parent / "icon.svg").write_text(_logo_mark(), encoding="utf-8")
    OUTPUT_FILE.write_text(page, encoding="utf-8")
    print("─"*54)
    print(f"✓  Saved → {OUTPUT_FILE}  ({cards} cards, {rows} rows)")
if __name__ == "__main__":
    main()
