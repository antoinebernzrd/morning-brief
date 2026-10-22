from datetime import datetime, timedelta, timezone

import render


def article(title="A headline", source="Reuters", **kw):
    return {"title": title, "source": source, "link": "https://example.com/a?x=1&y=2",
            "date": datetime.now(timezone.utc) - timedelta(hours=2), "ts": 0, **kw}


def test_assets_are_loaded_and_well_formed():
    assert render.CSS.lstrip().startswith("*,*::before")
    assert render.PHONE_APP.lstrip().startswith("<script>")
    assert render.PHONE_APP.rstrip().endswith("</script>")


def test_section_wraps_body_with_header():
    html = render._sec("#123456", "Label", "<p>body</p>")
    assert "border-top:2px solid #123456" in html
    assert ">Label<" in html and "<p>body</p>" in html


def test_brick_single_article_links_and_escapes():
    html = render._brick([article(title="Fed <b>holds</b> & waits")])
    assert 'href="https://example.com/a?x=1&amp;y=2"' in html
    assert "Fed &lt;b&gt;holds&lt;/b&gt; &amp; waits" in html
    assert "<b>holds</b>" not in html
    assert ">2h<" in html


def test_brick_group_lists_every_source():
    html = render._brick([article("Same story", "Reuters"), article("Same story again", "BBC")])
    assert "Reuters" in html and "BBC" in html
    assert html.count("mk-sub-src") == 2


def test_brick_prefers_canonical_source_name():
    html = render._brick([article(source="LGC Doctrine", _canon="Le Grand Continent")])
    assert "Le Grand Continent" in html and "LGC Doctrine" not in html


def test_picture_card_uses_image_or_falls_back_to_flat_art():
    with_img = render._pcard(article(img="https://img/x.jpg"), {})
    without = render._pcard(article(), {})
    assert "background-image:url(https://img/x.jpg)" in with_img and "pc-noimg" not in with_img
    assert "pc-noimg" in without and "pc-img-flat" in without


def test_ticker_empty_and_duplicated_for_loop():
    assert render.build_ticker([]) == ""
    html = render.build_ticker([article("One"), article("Two")])
    assert html.count(">One<") == 2  # duplicated for the seamless scroll


def test_ticker_shows_at_most_twelve_items():
    html = render.build_ticker([article(f"T{i}") for i in range(30)])
    assert ">T11<" in html and ">T12<" not in html


def test_paris_section_empty_state_and_cap():
    assert "No Paris events fetched" in render.build_paris([])
    html = render.build_paris([article(f"E{i}") for i in range(40)])
    assert ">E19<" in html and ">E20<" not in html


def test_opinions_page_empty_state():
    assert "No articles fetched" in render.build_gossip([])


def test_opinions_page_leads_with_freshest_article_that_has_a_picture():
    arts = [article("no picture", "Reuters"), article("has picture", "BBC", img="https://img/p.jpg")]
    html = render.build_gossip(arts)
    lead = html.split('class="mkt-lead"')[1].split('class="mkt-stack"')[0]
    assert "has picture" in lead and "no picture" not in lead


def test_price_band_formats_values_and_direction():
    prices = [
        {"name": "S&P", "sym": "^GSPC", "price": 5234.5, "pct": 1.234},
        {"name": "10Y", "sym": "^TNX", "price": 4.25, "pct": -0.5},
        {"name": "EUR", "sym": "EURUSD=X", "price": 1.0831, "pct": None},
    ]
    html = render.build_price_band(prices)
    assert "5,234.5" in html and "+1.23%" in html and 'chg up' in html
    assert "4.25%" in html and "-0.50%" in html and 'chg dn' in html
    assert "1.0831" in html
    assert "No price data" in render.build_price_band([])


def test_polymarket_band_formats_percentages():
    market = {"q": "Will it rain?", "slug": "rain", "vol": "$1M", "pairs": [("Yes", 0), ("No", 100), ("Maybe", 42)]}
    html = render.build_polymarket_band([market])
    assert "&lt;1%" not in html and "<1%" in html and ">99%" in html and "42%" in html
    assert "https://polymarket.com/event/rain" in html
    assert "No data" in render.build_polymarket_band([])
