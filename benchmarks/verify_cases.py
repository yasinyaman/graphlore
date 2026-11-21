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
    Kotlin      kotlinc
    Swift       swiftc -typecheck
    C#          dotnet build (a library of every source)

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


def check(lang: str, root: Path, version: str | None = None) -> tuple[int, str]:
    """Give the case to its language's compiler; in `version` of the language
    when the case states one (`since`): one installed compiler answers for its
    older versions through a flag."""
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
        srcs = files(root, ".ts", ".tsx")
        text = "\n".join((root / f).read_text() for f in srcs)
        # `import x = require()` and `export =` are CommonJS's
        if "= require(" in text or "export =" in text:
            return sh(["tsc", "--noEmit", "--skipLibCheck", "--target", "ES2022", "--module",
                       "commonjs", "--strict", "false", *srcs], root)
        return sh(["tsc", "--noEmit", "--skipLibCheck", "--target", "ES2022", "--module",
                   "ESNext", "--moduleResolution", "bundler", "--strict", "false",
                   "--allowImportingTsExtensions", *srcs], root)
    if lang == "go":
        return sh(["go", "vet", "./..."], root, {"GOFLAGS": "-mod=mod", "GOPROXY": "off"})
    if lang == "java":
        release = ["--release", version] if version else []
        return sh(["javac", "-nowarn", *release, "-d", "out", *files(root, ".java")], root,
                  {"JAVA_TOOL_OPTIONS": ""})
    if lang == "rust":
        return sh(["cargo", "check", "--offline", "--quiet", "--all-targets"], root,
                  {"CARGO_TARGET_DIR": str(root / "target"), "RUSTFLAGS": "-A warnings"})
    if lang == "c":
        std = [f"-std=c{version}"] if version else []
        return sh(["gcc", *std, "-fsyntax-only", "-w", *files(root, ".c")], root)
    if lang == "cpp":
        return sh(["g++", f"-std=c++{version or 20}", "-fsyntax-only", "-w",
                   *files(root, ".cc", ".cpp")], root)
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
    if lang == "kotlin":
        lv = ["-language-version", version] if version else []
        return sh(["kotlinc", "-nowarn", *lv, "-d", "out", *files(root, ".kt")], root)
    if lang == "swift":
        mode = ["-swift-version", version] if version else []
        return sh(["swiftc", "-typecheck", "-suppress-warnings", *mode, *files(root, ".swift")], root)
    if lang == "scala":
        source = ["-source", version] if version else []
        (root / "out").mkdir(exist_ok=True)
        return sh(["scalac", "-nowarn", *source, "-d", "out", *files(root, ".scala")], root,
                  {"JAVA_TOOL_OPTIONS": ""})
    if lang == "csharp":
        # one library project of every source; no restore from the network
        (root / "case.csproj").write_text(
            '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net8.0</TargetFramework>'
            "<OutputType>Library</OutputType><Nullable>disable</Nullable><ImplicitUsings>disable</ImplicitUsings>"
            "<TreatWarningsAsErrors>false</TreatWarningsAsErrors><NoWarn>CS0169;CS0649;CS0219;CS8321;CS0414;CS0168</NoWarn>"
            f"<LangVersion>{version or 'preview'}</LangVersion></PropertyGroup></Project>\n")
        return sh(["dotnet", "build", "--nologo", "-v", "q", "-clp:NoSummary"], root,
                  {"DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_NOLOGO": "1"})
    return -1, "no check written for this language"


def verify(c: dict[str, Any]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "project"
        for rel, src in c["files"].items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(src, encoding="utf-8")
        code, out = check(c["lang"], root, c.get("since"))
    first = next((ln for ln in out.splitlines() if ln.strip() and "JAVA_TOOL_OPTIONS" not in ln), "")
    return {"id": c["id"], "ok": code == 0, "error": "" if code == 0 else out[-1500:],
            "first": first[:200]}


# The versions each installed compiler can be asked to compile as, oldest
# first (its own flag: `--release`, `<LangVersion>`, `-std=`, `-swift-version`,
# `-language-version`, `-source`). A language missing here has no such flag
# (Go and Rust state the version in the case's own manifest; the interpreters
# need one installation per version).
ASKABLE = {
    "java": ["8", "9", "10", "11", "14", "15", "16", "17", "21"],
    "csharp": ["7.3", "8.0", "9.0", "10.0", "11.0", "12.0"],
    "cpp": ["98", "11", "14", "17", "20"],
    "c": ["90", "99", "11", "17"],
    "swift": ["4", "5", "6"],
    "kotlin": ["1.8", "1.9", "2.0", "2.1", "2.2"],
    "scala": ["3.0", "3.1", "3.2", "3.3"],
}
CASE_VERSIONS = Path(__file__).resolve().parent / "results-case-versions.json"


def lowest_version(c: dict[str, Any]) -> dict[str, Any]:
    """The oldest version of its language that accepts the case, by asking
    the compiler (a binary search: what one version accepts the next does)."""
    versions = ASKABLE[c["lang"]]
    lo, hi = 0, len(versions) - 1
    with tempfile.TemporaryDirectory() as tmp:
        def accepts(i: int) -> bool:
            root = Path(tmp) / f"v{i}"
            for rel, src in c["files"].items():
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(src, encoding="utf-8")
            return check(c["lang"], root, versions[i])[0] == 0
        until = None
        if not accepts(hi):
            # valid once and no longer (Swift 6 rejects a mutable `static var`
            # Swift 5 takes): the newest version that still accepts it
            hi = next((i for i in range(hi - 1, -1, -1) if accepts(i)), -1)
            if hi < 0:
                return {"id": c["id"], "since": None, "newest_rejects": True}
            until = versions[hi]
        while lo < hi:
            mid = (lo + hi) // 2
            if accepts(mid):
                hi = mid
            else:
                lo = mid + 1
    return {"id": c["id"], "since": versions[lo], "oldest_asked": versions[0], "until": until}


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--only", default="", help="comma-separated languages or case ids")
    ap.add_argument("--json", help="write the result of every case here")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--min-version", action="store_true",
                    help="ask each compiler for the oldest version that accepts each case, and "
                         f"record it in {CASE_VERSIONS.name}")
    args = ap.parse_args()
    only = {x for x in args.only.split(",") if x}
    cases = [c for c in conformance.CASES if not only or c["lang"] in only or c["id"] in only]
    have = {lang for lang, tool in TOOL.items() if shutil.which(tool)}
    todo = [c for c in cases if c["lang"] in have]
    if args.min_version:
        asked = [c for c in todo if c["lang"] in ASKABLE]
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            found = list(pool.map(lowest_version, asked))
        kept = json.loads(CASE_VERSIONS.read_text(encoding="utf-8")) if CASE_VERSIONS.exists() else {}
        for c, r in zip(asked, found, strict=True):
            # (the oldest version asked accepting it says "that old or older")
            kept[c["id"]] = {"since": r["since"], "at_least": r.get("oldest_asked") == r["since"]}
            if r.get("until"):
                kept[c["id"]]["until"] = r["until"]
                print(f"  accepted up to {r['until']} only: {c['id']}")
            if r.get("newest_rejects"):
                print(f"  REJECTED by every version asked: {c['id']}")
        CASE_VERSIONS.write_text(json.dumps(dict(sorted(kept.items())), indent=1) + "\n", encoding="utf-8")
        for lang in dict.fromkeys(c["lang"] for c in asked):
            rows = [kept[c["id"]] for c in asked if c["lang"] == lang]
            newer = sorted({r["since"] for r in rows if r["since"] and not r["at_least"]},
                           key=ASKABLE[lang].index)
            print(f"{lang}: {len(rows)} cases, {sum(1 for r in rows if not r['at_least'])} need a version "
                  f"newer than {ASKABLE[lang][0]} ({', '.join(newer) or 'none'})")
        return 0
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
