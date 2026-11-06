#!/usr/bin/env python3
"""Check my setup — run this if something isn't working.

    python scripts/selfcheck.py

It prints a friendly checklist and tells you exactly what to do for anything that fails.
"""

from __future__ import annotations

import importlib
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OK, BAD, WARN = "✅", "❌", "⚠️ "


def line(status: str, text: str, fix: str = "") -> None:
    print(f"{status} {text}")
    if fix:
        print(f"      → {fix}")


def main() -> int:
    problems = 0
    print("\nFix-It Assistant — setup check\n" + "-" * 32)

    # Python
    v = sys.version_info
    if v >= (3, 10):
        line(OK, f"Python {v.major}.{v.minor}.{v.micro}")
    else:
        problems += 1
        line(BAD, f"Python {v.major}.{v.minor} is too old (need 3.10+)", "Install a newer Python: sudo apt install python3")

    # venv
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    line(OK if in_venv else WARN, "Running inside a virtual environment" if in_venv else "Not inside a virtual environment",
         "" if in_venv else "Run:  source .venv/bin/activate   (see README step 2)")

    # packages
    missing = []
    for mod in ("fastapi", "uvicorn", "httpx", "pydantic", "multipart"):
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        problems += 1
        line(BAD, f"Missing packages: {', '.join(missing)}", "Run:  pip install -r requirements.txt")
    else:
        line(OK, "All required packages installed")

    # app imports
    try:
        from fix_it_assistant import __version__
        from fix_it_assistant.knowledge import PROBLEMS
        from fix_it_assistant.config import config_path, load_settings

        line(OK, f"App code loads (version {__version__})")
    except Exception as exc:  # noqa: BLE001
        problems += 1
        line(BAD, f"App code failed to load: {exc}", "Re-download the project folder; a file may be missing.")
        return 1

    # settings
    s = load_settings()
    line(OK, f"Settings file: {config_path()}  (AI option: {s.provider}, model: {s.effective_model()})")

    # Ollama
    from fix_it_assistant.llm import detect_ollama

    o = detect_ollama(s.ollama_host)
    if o["running"] and o["models"]:
        line(OK, f"Ollama is running with {len(o['models'])} model(s): {', '.join(o['models'][:4])}")
    elif o["running"]:
        line(WARN, "Ollama is running but has no models", "Run:  ollama pull llama3.2:3b")
        if s.provider == "ollama":
            problems += 1
    else:
        if s.provider == "ollama":
            problems += 1
            line(BAD, "Ollama is selected but not running", "Open another terminal and run:  ollama serve   (install: curl -fsSL https://ollama.com/install.sh | sh)")
        else:
            line(WARN, "Ollama not running (fine — you are not using it)", "Want free private AI? Install Ollama, then pick 'On my computer' in Settings.")

    # Cloud keys
    if s.provider == "openai" and not s.openai_api_key and "127.0.0.1" not in s.openai_base_url and "localhost" not in s.openai_base_url:
        problems += 1
        line(BAD, "OpenAI option selected but no API key saved", "Open Settings in the app and paste your key, then click Test.")
    if s.provider == "gemini" and not s.gemini_api_key:
        problems += 1
        line(BAD, "Gemini option selected but no API key saved", "Open Settings in the app and paste your key, then click Test.")

    # Port
    with socket.socket() as sock:
        busy = sock.connect_ex(("127.0.0.1", 8768)) == 0
    line(WARN if busy else OK, "Port 8768 is already in use (the app will pick the next free one)" if busy else "Port 8768 is free")

    # Rules engine sanity
    try:
        from fix_it_assistant.knowledge import match, validate
        errs = validate()
        assert not errs, errs
        m = match("toilet keeps running and hissing")
        assert m["candidates"] and m["candidates"][0]["id"] == "toilet_running"
        line(OK, "Repair guide loads and the matcher works")
    except Exception as exc:  # noqa: BLE001
        line(BAD, f"Repair guide check failed: {exc}", "Re-download the project folder.")

    # Live AI check (quick)
    if s.provider != "offline":
        from fix_it_assistant.llm import get_provider

        ok, msg = get_provider(s).check()
        line(OK if ok else BAD, f"AI engine check: {msg}", "" if ok else "Open Settings → Test for details, or switch to 'No AI (basic mode)'.")
        if not ok:
            problems += 1

    print("-" * 32)
    if problems:
        print(f"{problems} thing(s) need attention (see the → lines above). More help: docs/TROUBLESHOOTING.md\n")
        return 1
    print("Everything looks good! Start the app with:  python app.py\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
