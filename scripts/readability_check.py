#!/usr/bin/env python3
"""G5 — readability gate.

Checks that the user-facing docs read at or below a 7th-grade level
(Flesch-Kincaid). Developer docs (ARCHITECTURE.md) are exempt.

    python scripts/readability_check.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET_GRADE = 7.0
FILES = ["README.md", "docs/USER_GUIDE.md", "docs/TROUBLESHOOTING.md", "docs/FAQ.md", "docs/PRIVACY.md"]


def strip_markdown(md: str) -> str:
    md = re.sub(r"```.*?```", " ", md, flags=re.S)          # code blocks
    md = re.sub(r"`[^`]*`", " ", md)                        # inline code
    md = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", md)           # images
    md = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", md)        # links -> text
    md = re.sub(r"^\s*\|.*\|\s*$", " ", md, flags=re.M)     # tables
    md = re.sub(r"^#+\s*", "", md, flags=re.M)              # headings
    md = re.sub(r"[*_>#|-]{1,}", " ", md)                   # emphasis / bullets
    md = re.sub(r"https?://\S+", " ", md)
    md = re.sub(r"\s+", " ", md)
    return md.strip()


def main() -> int:
    try:
        import textstat
    except ImportError:
        print("textstat not installed. Run: pip install -r requirements-dev.txt")
        return 2
    worst = 0.0
    failed = False
    print(f"{'file':34} {'grade':>6} {'ease':>6}")
    for rel in FILES:
        p = ROOT / rel
        if not p.exists():
            print(f"{rel:34} MISSING")
            failed = True
            continue
        text = strip_markdown(p.read_text(encoding="utf-8"))
        grade = textstat.flesch_kincaid_grade(text)
        ease = textstat.flesch_reading_ease(text)
        flag = "" if grade <= TARGET_GRADE else "  <-- too hard"
        print(f"{rel:34} {grade:6.1f} {ease:6.1f}{flag}")
        worst = max(worst, grade)
        if grade > TARGET_GRADE:
            failed = True
    print(f"\nTarget: grade <= {TARGET_GRADE}. Worst: {worst:.1f}. {'FAIL' if failed else 'PASS'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
