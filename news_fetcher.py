#!/usr/bin/env python3
"""Morning Brief v4: fetch, rank and write index.html."""
import json
import re
import socket
from datetime import datetime, timezone
from sources import (CONFLICTS, TECH_SOURCES, SOURCE_CADENCE, TECH_EVERGREEN, MACRO_SOURCES, GEO_SOURCES, _drop_audio_dupes, _strip_gn_suffix, _keep_war_room, _keep_world_brief, _keep_block_daily, CULTURE_SOURCES, GOSSIP_SOURCES_OTHER, GOSSIP_WINDOW_DAYS, CONFLICT_NEWS_SOURCES, PARIS_SOURCES)
from articles import (_ago, _cap_per_source, _dedup, _dedup_exact, _dedup_smart, _filter_recent, _match_conflicts, _route_afp)
from calendar_events import (_build_calendar_events)
from fetchers import (_backfill_images, _enrich_groups, _fetch, _fetch_art_newspaper, _fetch_calendar_event_news, _fetch_evergreen, _fetch_les_echos, _fetch_nss, _fetch_telegram, _load_headline_cache, _load_openai, _save_headline_cache)
from paths import (OUTPUT_DIR, OUTPUT_FILE)
from render import (CSS, PHONE_APP, _logo_mark, build_culture, build_gossip, build_macro, build_map, build_tech, build_ticker)
import calendar_events

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
    print("  Checking the event calendar…")
    calendar_events.CALENDAR_EVENTS = _build_calendar_events()
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
