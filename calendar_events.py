"""The events calendar: recurring series resolved against Wikidata. `CALENDAR_EVENTS` is rebuilt by `main`, so read it as `calendar_events.CALENDAR_EVENTS`."""
import json
import urllib.error
import urllib.parse
import urllib.request
from sources import (CALENDAR_SERIES, _WIKIDATA_UA)

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
