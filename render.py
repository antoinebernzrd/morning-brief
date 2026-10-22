"""HTML for each section of the brief, plus the inlined CSS and phone-nav script."""
import hashlib
import random
from datetime import datetime, timezone
from sources import (LGC_GEO, LGC_BEAT, GEO_PINNED, MACRO_BEAT, CULTURE_BEAT, CULTURE_LONG_READ, CULTURE_SOURCE_COLORS, GOSSIP_SOURCE_COLORS, MACRO_SOURCE_COLORS)
from articles import (_ago, _cadence, _daily_first, _one_per_source, _s, _sort_by_time)
from paths import (ASSETS_DIR)
import calendar_events

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

def _build_group_row(g, extra_cls="", data_attrs=""):
    """Kept for its callers; every list in the brief now renders the same
    brick as the Markets page."""
    return _brick(g, extra_cls=extra_cls, data_attrs=data_attrs)

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
    _live      = sorted([e for e in calendar_events.CALENDAR_EVENTS if e["start"] <= today_str <= e["end"]],
                        key=_live_key)
    _upcoming  = sorted([e for e in calendar_events.CALENDAR_EVENTS if e["start"] > today_str],
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
    sorted_evs = sorted(calendar_events.CALENDAR_EVENTS, key=_ev_sort)

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
