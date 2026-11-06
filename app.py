#!/usr/bin/env python3
"""Start Fix-It Assistant.

    python app.py              -> opens http://127.0.0.1:8768 in your browser
    python app.py --port 9000  -> use a different port
    python app.py --no-browser -> don't open the browser automatically
"""

from __future__ import annotations

import argparse
import logging
import socket
import sys
import threading
import webbrowser

MIN_PY = (3, 10)


def _check_python() -> None:
    if sys.version_info < MIN_PY:
        print(f"Fix-It Assistant needs Python {MIN_PY[0]}.{MIN_PY[1]} or newer. You have {sys.version.split()[0]}.")
        print("On Ubuntu/Debian:  sudo apt install python3 python3-venv")
        sys.exit(1)


def _check_deps() -> None:
    missing = []
    for mod in ("fastapi", "uvicorn", "httpx", "pydantic", "multipart"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        print("Some required packages are not installed yet.")
        print("Run these two commands, then try again:\n")
        print("    source .venv/bin/activate")
        print("    pip install -r requirements.txt\n")
        print(f"(missing: {', '.join(missing)})")
        sys.exit(1)


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def main() -> None:
    _check_python()
    _check_deps()

    parser = argparse.ArgumentParser(description="Fix-It Assistant — figure out what is wrong around the house and whether you can fix it yourself.")
    parser.add_argument("--port", type=int, default=8768, help="port to listen on (default 8768; the next free one is used if busy)")
    parser.add_argument("--no-browser", action="store_true", help="don't open the browser automatically")
    parser.add_argument("--verbose", action="store_true", help="print detailed logs (useful when reporting a problem)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # Never print request URLs (could include server addresses) at normal verbosity.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    port = args.port
    if not _port_free(port):
        for candidate in range(port + 1, port + 20):
            if _port_free(candidate):
                print(f"Port {port} is busy, using {candidate} instead.")
                port = candidate
                break
        else:
            print(f"Port {port} and the next 20 are all busy. Try: python app.py --port 9000")
            sys.exit(1)

    import uvicorn

    url = f"http://127.0.0.1:{port}"
    print("\n  Fix-It Assistant is starting...")
    print(f"  Open this in your browser:  {url}")
    print("  Press Ctrl+C here to stop.\n")

    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    uvicorn.run("fix_it_assistant.server:app", host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
