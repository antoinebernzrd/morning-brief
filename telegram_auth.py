#!/usr/bin/env python3
"""
Telegram one-time setup — run this ONCE, then never again.

Steps:
  1. Export your credentials as environment variables:
       export TELEGRAM_API_ID=<your_api_id>
       export TELEGRAM_API_HASH=<your_api_hash>
     (from my.telegram.org → API development tools)
  2. Run:  python3 ~/news_dashboard/telegram_auth.py
  3. Enter your phone number (+33...) and the code Telegram sends you
  4. Done — session saved, news_fetcher.py will use it silently from now on

Never hardcode credentials in this file — use environment variables only.
"""

import os, sys
from pathlib import Path

API_ID   = int(os.environ.get("TELEGRAM_API_ID", 0) or 0)
API_HASH = os.environ.get("TELEGRAM_API_HASH", "")

SESSION_DIR  = Path.home() / ".config" / "morning-brief"
SESSION_FILE = SESSION_DIR / "telegram_session"

def main():
    if not API_ID or not API_HASH:
        print("❌  Missing credentials. Export before running:")
        print("       export TELEGRAM_API_ID=<your_id>")
        print("       export TELEGRAM_API_HASH=<your_hash>")
        sys.exit(1)

    try:
        from telethon.sync import TelegramClient
    except ImportError:
        print("❌  telethon not installed. Run: pip install telethon")
        print("    (add it to requirements.txt and install once manually)")
        sys.exit(1)

    SESSION_DIR.mkdir(parents=True, exist_ok=True)

    print("Connecting to Telegram…")
    with TelegramClient(str(SESSION_FILE), API_ID, API_HASH) as client:
        me = client.get_me()
        print(f"✓ Logged in as {me.first_name} ({me.username or me.phone})")

        print("  Testing AFP channel fetch…")
        try:
            msgs = client.get_messages("afpfr", limit=3)
            print(f"  ✓ AFP (@afpfr): {len(msgs)} messages fetched")
            for m in msgs:
                if m.text:
                    print(f"    · {m.text[:80].strip()}…")
        except Exception as e:
            print(f"  ⚠  Could not reach @afpfr: {e}")

    print(f"\n✓ Session saved → {SESSION_FILE}.session")
    print("\nYou're all set — news_fetcher.py will now pull AFP automatically.")
    print("Make sure TELEGRAM_API_ID and TELEGRAM_API_HASH are set in ~/.zshrc")

if __name__ == "__main__":
    main()
