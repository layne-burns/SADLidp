"""Leak guard (SPEC.md §7): abort before anything secret or private gets committed.

Reports only the file path and the pattern name, never the matched text.

    python -m probe.leakcheck PATH...           # scan files/directories
    python -m probe.leakcheck --staged REPO     # scan files staged in REPO
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

PATTERNS = {
    "age_secret_key": re.compile(rb"AGE-SECRET-KEY-"),
    "http_credential_header": re.compile(rb"(?im)^[ \t\"']*(cookie|set-cookie|authorization)[ \t]*:"),
}
PRIVATE_SOURCES = ("fantasypros",)
MIN_SECRET_LEN = 8


def secret_values(env: dict[str, str] | None = None) -> dict[str, list[bytes]]:
    env = os.environ if env is None else env
    out: dict[str, list[bytes]] = {}
    cookie = env.get("FP_COOKIE", "")
    parts = []
    for pair in cookie.split(";"):
        value = pair.split("=", 1)[1] if "=" in pair else pair
        value = value.strip()
        if len(value) >= MIN_SECRET_LEN:
            parts.append(value.encode())
    if parts:
        out["fp_cookie_value"] = parts
    key = env.get("ODDS_API_KEY", "").strip()
    if len(key) >= MIN_SECRET_LEN:
        out["odds_api_key"] = [key.encode()]
    return out


def scan_file(path: Path, secrets: dict[str, list[bytes]]) -> list[str]:
    hits = []
    name = path.name.lower()
    if any(src in str(path).lower() for src in PRIVATE_SOURCES) and not name.endswith(".age"):
        hits.append("private_source_plaintext")
    if name.endswith(".age"):
        return hits  # ciphertext
    try:
        data = path.read_bytes()
    except OSError:
        return hits
    for pname, rx in PATTERNS.items():
        if rx.search(data):
            hits.append(pname)
    for sname, values in secrets.items():
        if any(v in data for v in values):
            hits.append(sname)
    return hits


def iter_files(paths: list[Path]):
    for p in paths:
        if p.is_dir():
            yield from (f for f in sorted(p.rglob("*")) if f.is_file() and ".git" not in f.parts)
        elif p.is_file():
            yield p


def staged_files(repo: Path) -> list[Path]:
    res = subprocess.run(
        ["git", "-C", str(repo), "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"],
        capture_output=True,
        check=True,
    )
    return [repo / n.decode() for n in res.stdout.split(b"\0") if n]


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--staged"]:
        files = staged_files(Path(argv[1]))
    else:
        files = list(iter_files([Path(a) for a in argv]))
    secrets = secret_values()
    hits = [(f, h) for f in files for h in scan_file(f, secrets)]
    for f, h in hits:
        print(f"LEAK: {f}: {h}", file=sys.stderr)
    print(f"leakcheck: scanned {len(files)} files, {len(hits)} hits")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
