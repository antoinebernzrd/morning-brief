#!/usr/bin/env python3
"""
Test which FT RSS / proxy URLs actually return Technology section articles.
Run this on your Mac:  python3 ~/news_dashboard/test_ft_feeds.py
"""
import feedparser, http.cookiejar, urllib.request, sys

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")

TESTS = [
    # ── FT direct feeds ──────────────────────────────────────────────────────
    ("FT /technology?format=rss",          "https://www.ft.com/technology?format=rss"),
    ("FT /companies/technology?format=rss","https://www.ft.com/companies/technology?format=rss"),
    ("FT homepage?format=rss",             "https://www.ft.com/?format=rss"),
    # ── FT Alphaville (often more accessible) ────────────────────────────────
    ("FT Alphaville",                      "https://www.ft.com/alphaville?format=rss"),
    # ── Google News — various query strategies ────────────────────────────────
    # site: on ft.com (may return 0 — FT opts out of Google News)
    ("GN site:ft.com",                     "https://news.google.com/rss/search?q=site:ft.com&hl=en&gl=US&ceid=US:en"),
    ("GN site:ft.com technology",          "https://news.google.com/rss/search?q=site:ft.com+technology&hl=en&gl=US&ceid=US:en"),
    # keyword search mentioning FT
    ("GN \"FT\" OR \"Financial Times\" tech","https://news.google.com/rss/search?q=%22Financial+Times%22+technology+startup+AI&hl=en&gl=US&ceid=US:en"),
    # ── Feedly proxy (caches FT behind their login wall) ─────────────────────
    ("Feedly FT tech",                     "https://cloud.feedly.com/v3/mixes/contents?streamId=feed%2Fhttps%3A%2F%2Fwww.ft.com%2Ftechnology%3Fformat%3Drss&count=20&unreadOnly=false"),
]

def fetch(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA,
            "Accept": "application/rss+xml,application/xml,text/xml,*/*"})
        with urllib.request.urlopen(req, timeout=12) as r:
            raw = r.read()
        feed = feedparser.parse(raw)
        return feed.entries
    except Exception as e:
        return e

print(f"\n{'─'*70}")
print("  FT FEED DIAGNOSTIC")
print(f"{'─'*70}")

for label, url in TESTS:
    result = fetch(url)
    if isinstance(result, Exception):
        print(f"\n  ✗  [{label}]")
        print(f"       {type(result).__name__}: {result}")
        continue
    print(f"\n  {'✓' if result else '○'}  [{label}] → {len(result)} articles")
    for e in result[:4]:
        title = e.get("title","—")[:72]
        link  = e.get("link","")
        print(f"       • {title}")
        print(f"         {link[:80]}")

print(f"\n{'─'*70}")
print("  → Any feed with ✓ and relevant titles is usable in news_fetcher.py")
print(f"{'─'*70}\n")
