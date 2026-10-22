"""Pure article helpers: dates, filters, dedup and per-source caps. No network, no HTML."""
import html as html_lib
import re
from datetime import datetime, timezone
from sources import (WEEKLY_SOURCES, SOURCE_CANONICAL, TECH_KEYWORDS, MACRO_KEYWORDS, CONFLICTS, SOURCE_CADENCE, DEFAULT_CADENCE, SOURCE_WINDOW_DAYS, MARSEILLE_SOURCE_NAMES)

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

def _daily_first(g):
    """Put the daily debrief at the head of its group so the row is labelled
    with that masthead rather than whichever story happened to sort first."""
    if len(g) < 2:
        return g
    for i, a in enumerate(g):
        if SOURCE_CADENCE.get(a.get("_canon") or a["source"]) == "daily":
            return [a] + g[:i] + g[i + 1:]
    return g
