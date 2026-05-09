#!/usr/bin/env python3
"""
Telegram one-time setup — run this ONCE, then never again.

Steps:
  1. Paste your api_id and api_hash below (from my.telegram.org)
  2. Run:  python3 ~/news_dashboard/telegram_auth.py
  3. Enter your phone number (+33...) and the code Telegram sends you
  4. Done — session saved, news_fetcher.py will use it silently from now on
"""

# ── Paste your credentials here ──────────────────────────────────────────────
API_ID   = 33788834           # replace with the integer from my.telegram.org
API_HASH = "d4d7a4fa445518bf2699129532f28a2b"          # replace with the string  from my.telegram.org
# ─────────────────────────────────────────────────────────────────────────────

import json, sys
from pathlib import Path

SESSION_DIR = Path.home() / "news_dashboard"
CONFIG_FILE = SESSION_DIR / "telegram_config.json"
SESSION_FILE = SESSION_DIR / "telegram_session"

def main():
    if API_ID == 0 or API_HASH == "":
        print("❌  Please open this file and fill in API_ID and API_HASH first.")
        sys.exit(1)

    try:
        from telethon.sync import TelegramClient
    except ImportError:
        print("Installing telethon…")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install",
                               "telethon", "--break-system-packages", "-q"])
        from telethon.sync import TelegramClient

    SESSION_DIR.mkdir(parents=True, exist_ok=True)

    print("Connecting to Telegram…")
    with TelegramClient(str(SESSION_FILE), API_ID, API_HASH) as client:
        me = client.get_me()
        print(f"✓ Logged in as {me.first_name} ({me.username or me.phone})")

        # Quick test — fetch 3 messages from AFP
        print("  Testing AFP channel fetch…")
        try:
            msgs = client.get_messages("afpfr", limit=3)
            print(f"  ✓ AFP (@afpfr): {len(msgs)} messages fetched")
            for m in msgs:
                if m.text:
                    print(f"    · {m.text[:80].strip()}…")
        except Exception as e:
            print(f"  ⚠  Could not reach @afpfr: {e}")

    # Save credentials so news_fetcher can reuse them
    CONFIG_FILE.write_text(json.dumps({"api_id": API_ID, "api_hash": API_HASH}))
    print(f"\n✓ Session saved → {SESSION_FILE}.session")
    print(f"✓ Config  saved → {CONFIG_FILE}")
    print("\nYou're all set — news_fetcher.py will now pull AFP automatically.")

if __name__ == "__main__":
    main()
