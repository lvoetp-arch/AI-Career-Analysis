import json
import os
import sys
from pathlib import Path


def main() -> None:
    public_keys = ("SUPABASE_URL", "SUPABASE_ANON_KEY")
    config = {key: os.environ.get(key, "").strip() for key in public_keys}
    missing = [key for key, value in config.items() if not value]
    if missing:
        raise SystemExit("Missing environment variables: " + ", ".join(missing))

    path = Path("frontend/js/config.local.js")
    path.write_text(f"window.APP_CONFIG = {json.dumps(config)};\n", encoding="utf-8")

    os.execv(sys.executable, [
        sys.executable, "-m", "uvicorn", "app.main:create_app",
        "--factory", "--app-dir", "backend", "--host", "0.0.0.0",
        "--port", os.environ.get("PORT", "8000"),
    ])


if __name__ == "__main__":
    main()
