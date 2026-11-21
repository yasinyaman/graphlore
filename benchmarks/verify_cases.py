# ruff: noqa: E501
"""Does the language itself accept the cases of `conformance.py`?

`conformance.py` holds a small project per name-resolution rule and asks the
engine for the edge the rule says is there. The rules were written from the
languages' definitions; nothing checked that each project is a program the
language accepts. A case its compiler rejects says nothing about the rule: it
is a mistake in the list.

This script gives each case to the language's own compiler or interpreter,
where one is installed, and reports the ones it rejects:

    Python      python -m py_compile            (and the modules are imported)
    JavaScript  node --check
    TypeScript  tsc --noEmit
    Go          go vet ./...                    (type-checks the tests too)
    Java        javac
    Rust        cargo check --all-targets
    C, C++      gcc / g++ -fsyntax-only
    Ruby        ruby -c, then the files are loaded
    PHP         php -l

A language with no toolchain on this machine is listed as not checked. It
compiles; it does not say which definition a call reaches: see `--binding` in
the docs for what does.

    python benchmarks/verify_cases.py
    python benchmarks/verify_cases.py --only go,rust
    python benchmarks/verify_cases.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import conformance  # noqa: E402

TOOL = {"python": "python3", "javascript": "node", "typescript": "tsc", "go": "go",
        "java": "javac", "rust": "cargo", "c": "gcc", "cpp": "g++", "ruby": "ruby", "php": "php",
        "kotlin": "kotlinc", "scala": "scalac", "csharp": "dotnet", "swift": "swiftc"}


def sh(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> tuple[int, str]:
    done = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=180,
                          env={**os.environ, **(env or {})})
    return done.returncode, (done.stdout + done.stderr).strip()


def files(root: Path, *exts: str) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*")
                  if p.is_file() and p.suffix in exts)


def check(lang: str, root: Path) -> tuple[int, str]:
    if lang == "python":
        code, out = sh(["python3", "-m", "py_compile", *files(root, ".py")], root)
        if code:
            return code, out
        # the names a module binds at import: a wrong import is a runtime error
        mods = [f[:-3].replace("/", ".") for f in files(root, ".py")
                if not f.endswith("__init__.py") and "conftest" not in f and "test_" not in f]
        return sh(["python3", "-c", "import importlib,sys\nsys.path.insert(0,'.')\n"
                   + "\n".join(f"importlib.import_module({m!r})" for m in mods)], root)
    if lang == "javascript":
        srcs = files(root, ".js", ".mjs", ".cjs")
        text = "\n".join((root / f).read_text() for f in srcs)
        esm = ("import " in text or "export " in text) and "require(" not in text \
            and "module.exports" not in text and "exports." not in text
        if esm and not (root / "package.json").exists():
            (root / "package.json").write_text('{"type": "module"}\n')
        for f in srcs:
            code, out = sh(["node", "--check", f], root)
            if code:
                return code, out
        return 0, ""
    if lang == "typescript":
        return sh(["tsc", "--noEmit", "--skipLibCheck", "--target", "ES2022", "--module",
                   "ESNext", "--moduleResolution", "bundler", "--strict", "false",
                   "--allowImportingTsExtensions", *files(root, ".ts", ".tsx")], root)
    if lang == "go":
        return sh(["go", "vet", "./..."], root, {"GOFLAGS": "-mod=mod", "GOPROXY": "off"})
    if lang == "java":
        return sh(["javac", "-nowarn", "-d", "out", *files(root, ".java")], root,
                  {"JAVA_TOOL_OPTIONS": ""})
    if lang == "rust":
        return sh(["cargo", "check", "--offline", "--quiet", "--all-targets"], root,
                  {"CARGO_TARGET_DIR": str(root / "target"), "RUSTFLAGS": "-A warnings"})
    if lang == "c":
        return sh(["gcc", "-fsyntax-only", "-w", *files(root, ".c")], root)
    if lang == "cpp":
        return sh(["g++", "-std=c++20", "-fsyntax-only", "-w", *files(root, ".cc", ".cpp")], root)
    if lang == "ruby":
        for f in files(root, ".rb"):
            code, out = sh(["ruby", "-c", f], root)
            if code:
                return code, out
        return 0, ""
    if lang == "php":
        for f in files(root, ".php"):
            code, out = sh(["php", "-l", f], root)
            if code:
                return code, out
        return 0, ""
    return -1, "no check written for this language"


def verify(c: dict[str, Any]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "project"
        for rel, src in c["files"].items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(src, encoding="utf-8")
        code, out = check(c["lang"], root)
    first = next((ln for ln in out.splitlines() if ln.strip() and "JAVA_TOOL_OPTIONS" not in ln), "")
    return {"id": c["id"], "ok": code == 0, "error": "" if code == 0 else out[-1500:],
            "first": first[:200]}


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--only", default="", help="comma-separated languages or case ids")
    ap.add_argument("--json", help="write the result of every case here")
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args()
    only = {x for x in args.only.split(",") if x}
    cases = [c for c in conformance.CASES if not only or c["lang"] in only or c["id"] in only]
    have = {lang for lang, tool in TOOL.items() if shutil.which(tool)}
    todo = [c for c in cases if c["lang"] in have]
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(verify, todo))
    by_lang: dict[str, list[dict[str, Any]]] = {}
    for c, r in zip(todo, results, strict=True):
        by_lang.setdefault(c["lang"], []).append(r)
    rejected = 0
    for lang in dict.fromkeys(c["lang"] for c in cases):
        if lang not in have:
            n = sum(1 for c in cases if c["lang"] == lang)
            print(f"{lang}: not checked ({n} cases; no {TOOL[lang]} here)")
            continue
        rows = by_lang.get(lang, [])
        bad = [r for r in rows if not r["ok"]]
        rejected += len(bad)
        print(f"{lang}: {len(rows) - len(bad)} of {len(rows)} accepted")
        for r in bad:
            print(f"  REJECTED {r['id']:40} {r['first']}")
    print(f"\n{len(results) - rejected} of {len(results)} cases accepted by their language; "
          f"{len(cases) - len(todo)} not checked")
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=1) + "\n", encoding="utf-8")
    return 1 if rejected else 0


if __name__ == "__main__":
    sys.exit(main())
