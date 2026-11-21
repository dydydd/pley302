#!/usr/bin/env python3
"""Point `.strm` files at pley302.

A `.strm` file's first line may have to be an address (http/https) rather than
a path, so a library generated for Emby/Jellyfin (`/CloudNAS/...`) can't play
there. This rewrites each such file into

    http://<host>/d/<percent-encoded path>

which pley302 answers with the 302 to 115. The original line is kept in
`<file>.strm.bak` the first time a file is changed.

    python3 scripts/rewrite_strm.py --root /mnt/library --base http://127.0.0.1:8096
    python3 scripts/rewrite_strm.py --root /mnt/library --base http://127.0.0.1:8096 --apply
"""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path
from urllib.parse import quote


def first_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    return ""


def read_strm(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            return first_line(raw.decode(encoding))
        except (UnicodeDecodeError, UnicodeError):
            continue
    return ""


def rewrite(path: Path, base: str, apply: bool) -> tuple[str, str] | None:
    line = read_strm(path)
    if not line or line.lower().startswith(("http://", "https://")):
        return None
    encoded = quote(line, safe="/")
    new = f"{base.rstrip('/')}/d{encoded}"
    if not apply:
        return line, new
    backup = path.with_suffix(path.suffix + ".bak")
    if not backup.exists():
        shutil.copy2(path, backup)
    path.write_text(new + "\n", encoding="utf-8")
    return line, new


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", action="append", required=True, help="library folder to walk")
    parser.add_argument("--base", required=True, help="address players reach pley302 at")
    parser.add_argument("--apply", action="store_true", help="write the files (default: dry run)")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    changed = 0
    for root in args.root:
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in sorted(filenames):
                if not name.lower().endswith(".strm"):
                    continue
                path = Path(dirpath) / name
                result = rewrite(path, args.base, args.apply)
                if result is None:
                    continue
                changed += 1
                old, new = result
                if changed <= 3 or args.limit:
                    print(f"{'WRITE' if args.apply else 'PLAN '} {path}\n   - {old}\n   + {new}")
                if args.limit and changed >= args.limit:
                    print(f"{changed} file(s) {'rewritten' if args.apply else 'planned'}")
                    return
    print(f"{changed} file(s) {'rewritten' if args.apply else 'planned'}")


if __name__ == "__main__":
    main()
