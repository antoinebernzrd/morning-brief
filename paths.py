"""Filesystem locations shared by the fetchers and the renderer."""
from pathlib import Path
OUTPUT_DIR          = Path(__file__).parent
ASSETS_DIR          = OUTPUT_DIR / "assets"
OUTPUT_FILE         = OUTPUT_DIR / "index.html"
CONFIG_FILE         = OUTPUT_DIR / "telegram_config.json"
SESSION_FILE        = OUTPUT_DIR / "telegram_session"
OPENAI_CONFIG_FILE  = OUTPUT_DIR / "openai_config.json"
HEADLINE_CACHE_FILE = OUTPUT_DIR / "headline_cache.json"
POLY_CACHE_FILE     = OUTPUT_DIR / "poly_cache.json"
