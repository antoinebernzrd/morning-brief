import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import news_fetcher as nf
import sources


def art(title, source="Reuters", ts=None, **kw):
    return {"title": title, "source": source, "ts": time.time() if ts is None else ts, **kw}


# ── text helpers ─────────────────────────────────────────────────────────────

def test_escape_decodes_then_escapes_once():
    assert nf._s("Lenny&#8217;s") == "Lenny’s"
    assert nf._s("<b>a & b</b>") == "&lt;b&gt;a &amp; b&lt;/b&gt;"
    assert nf._s(None) == ""


def test_clean_title_strips_publication_suffix():
    assert nf._clean_title("Fed holds rates - Reuters") == "Fed holds rates"
    assert nf._clean_title("Fed holds rates | Financial Times") == "Fed holds rates"
    assert nf._clean_title("A title with Reuters inside") == "A title with Reuters inside"
    assert nf._clean_title(None) == "—"


def test_snip_strips_tags_and_truncates():
    assert nf._snip({"summary": "<p>Hello   <b>world</b></p>"}) == "Hello world"
    long = nf._snip({"summary": "x" * 400})
    assert len(long) == 281 and long.endswith("…")
    assert nf._snip({}) == ""


# ── dates ────────────────────────────────────────────────────────────────────

def test_parse_date_prefers_published_then_updated():
    e = SimpleNamespace(published_parsed=(2026, 5, 1, 12, 30, 0, 0, 0, 0))
    assert nf._parse_date(e) == datetime(2026, 5, 1, 12, 30, tzinfo=timezone.utc)
    e = SimpleNamespace(updated_parsed=(2026, 6, 2, 0, 0, 0, 0, 0, 0))
    assert nf._parse_date(e).month == 6
    assert nf._parse_date(SimpleNamespace()) is None


def test_ts_and_ago():
    assert nf._ts(None) == 0
    assert nf._ts(datetime(1970, 1, 2, tzinfo=timezone.utc)) == 86400
    now = datetime.now(timezone.utc)
    assert nf._ago(None) == ""
    assert nf._ago(now - timedelta(minutes=5, seconds=5)) == "5m"
    assert nf._ago(now - timedelta(hours=3, minutes=1)) == "3h"
    assert nf._ago(now - timedelta(days=2, hours=1)) == "2d"


# ── recency windows ──────────────────────────────────────────────────────────

DAY = 86400


def test_filter_recent_default_window_and_undated():
    now = time.time()
    arts = [art("fresh", ts=now - DAY), art("stale", ts=now - 5 * DAY), art("undated", ts=0)]
    kept = [a["title"] for a in nf._filter_recent(arts, days=2)]
    assert kept == ["fresh", "undated"]


def test_filter_recent_daily_and_slow_cadence_get_wider_windows():
    daily = next(s for s, c in sources.SOURCE_CADENCE.items() if c == "daily" and s not in sources.SOURCE_WINDOW_DAYS)
    slow = next(s for s, c in sources.SOURCE_CADENCE.items() if c == "slow" and s not in sources.SOURCE_WINDOW_DAYS)
    now = time.time()
    arts = [art("d3", daily, now - 3 * DAY), art("d5", daily, now - 5 * DAY),
            art("s15", slow, now - 15 * DAY), art("s30", slow, now - 30 * DAY)]
    assert [a["title"] for a in nf._filter_recent(arts, days=2)] == ["d3", "s15"]


def test_filter_recent_per_source_override_beats_everything():
    src, days = next(iter(sources.SOURCE_WINDOW_DAYS.items()))
    now = time.time()
    arts = [art("in", src, now - (days - 1) * DAY), art("out", src, now - (days + 1) * DAY)]
    assert [a["title"] for a in nf._filter_recent(arts, days=2)] == ["in"]


def test_filter_recent_weekly_sources_use_weekly_days():
    weekly = next(s for s in sources.WEEKLY_SOURCES if s not in sources.SOURCE_WINDOW_DAYS and s not in sources.SOURCE_CADENCE)
    now = time.time()
    arts = [art("a", weekly, now - 6 * DAY)]
    assert nf._filter_recent(arts, days=2, weekly_days=7)
    assert not nf._filter_recent(arts, days=2, weekly_days=3)


# ── keywords ─────────────────────────────────────────────────────────────────

def test_filter_keywords_matches_title_or_snippet_case_insensitively():
    arts = [art("OpenAI ships a model"), art("Weather today", snip="mentions NVIDIA chips"), art("Unrelated")]
    kept = [a["title"] for a in nf._filter_keywords(arts, ["openai", "nvidia"])]
    assert kept == ["OpenAI ships a model", "Weather today"]


def test_filter_keywords_treats_keywords_literally():
    assert nf._filter_keywords([art("price is 5.0")], ["5.0"])
    assert not nf._filter_keywords([art("price is 500")], ["5.0"])


# ── dedup ────────────────────────────────────────────────────────────────────

def test_dedup_exact_collapses_same_title_same_publication():
    a = art("Same Story", "Reuters", link="https://x/1")
    b = art("  same   story ", "Reuters", link="https://x/2")
    c = art("Same Story", "BBC", link="https://x/3")
    kept = nf._dedup_exact([a, b, c])
    assert [x["source"] for x in kept] == ["Reuters", "BBC"]
    assert kept[0]["_canon"] == "Reuters"


def test_dedup_exact_collapses_same_url_across_feeds():
    a = art("Title one", "Politico EU", link="https://p/a?utm=1")
    b = art("Different headline", "Politico France", link="https://p/a?utm=2")
    assert len(nf._dedup_exact([a, b])) == 1


def test_dedup_exact_merges_subfeeds_into_canonical_source():
    sub, canon = next(iter(sources.SOURCE_CANONICAL.items()))
    kept = nf._dedup_exact([art("One story", sub), art("One story", canon)])
    assert len(kept) == 1 and kept[0]["_canon"] == canon


def test_entities_picks_proper_nouns_and_acronyms():
    ents = nf._entities("The ECB warns Nvidia about Microsoft")
    assert {"ECB", "Nvidia", "Microsoft"} <= ents
    assert "The" not in ents


def test_dedup_groups_same_story_across_outlets():
    arts = [
        art("stocks rally as inflation cools sharply", "Reuters"),
        art("inflation cools sharply as stocks rally strongly", "BBC"),
        art("parliament votes on fishing quotas", "Politico"),
    ]
    groups = nf._dedup(arts)
    assert sorted(len(g) for g in groups) == [1, 2]


def test_dedup_does_not_group_unrelated_stories():
    arts = [art("stocks rally as inflation cools sharply", "Reuters"),
            art("parliament votes on fishing quotas", "BBC")]
    assert sorted(len(g) for g in nf._dedup(arts)) == [1, 1]


def test_dedup_matches_across_languages_on_shared_entities():
    arts = [art("OpenAI Microsoft renegotiate partnership", "Reuters"),
            art("Partenariat renégocié entre OpenAI et Microsoft", "Le Monde")]
    assert len(nf._dedup(arts)) == 1


def test_dedup_keeps_daily_debriefs_standalone():
    daily = next(s for s, c in sources.SOURCE_CADENCE.items() if c == "daily")
    arts = [art("Nvidia unveils new chip for datacenters", "Reuters"),
            art("Nvidia unveils new chip for datacenters", daily)]
    assert sorted(len(g) for g in nf._dedup(arts)) == [1, 1]


def test_sort_groups_floats_multi_source_first_preserving_order():
    g1, g2, g3, g4 = [1], [2, 2], [3], [4, 4]
    assert nf._sort_groups([g1, g2, g3, g4]) == [g2, g4, g1, g3]


# ── caps ─────────────────────────────────────────────────────────────────────

def test_cap_per_source_respects_specific_and_default_caps():
    one = next((s for s, c in nf.SOURCE_CAPS.items() if c == 1), None)
    arts = [art(f"t{i}", "Plain Source") for i in range(nf.DEFAULT_CAP + 3)]
    assert len(nf._cap_per_source(arts)) == nf.DEFAULT_CAP
    if one:
        assert len(nf._cap_per_source([art("a", one), art("b", one)])) == 1


def test_one_per_source_keeps_first_group_of_each_source():
    groups = [[art("a", "X")], [art("b", "X")], [art("c", "Y")]]
    assert [g[0]["title"] for g in nf._one_per_source(groups)] == ["a", "c"]


# ── generated-asset contract ─────────────────────────────────────────────────

def test_assets_are_loaded_and_well_formed():
    assert nf.CSS.lstrip().startswith("*,*::before")
    assert nf.PHONE_APP.lstrip().startswith("<script>") and nf.PHONE_APP.rstrip().endswith("</script>")
