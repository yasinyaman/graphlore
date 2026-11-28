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

A language with no toolchain on this machine is listed as not checked.

That a case compiles does not say which definition a name in it reaches.
`--binding` asks that too, where a type checker answers (Go: go/types, through
`oracle_go.py`'s program; C#: Roslyn; Rust: rust-analyzer, `oracle_rust.py
--dump`): for each edge a case expects, the compiler must bind
a use inside the source definition to the target definition; for each edge it
says is absent, it must not. A case whose expectation the compiler contradicts
is wrong, whatever the engine does with it. Edges that are no binding of a name
(imports, containment, dispatch, coupling, documents, HTTP) are not judged, nor
an absent edge that only says which relation an expected pair has (`references`
and not `calls`, `writes` and not `reads`): the compiler binds the name either way.

    python benchmarks/verify_cases.py
    python benchmarks/verify_cases.py --binding --only go
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
        # (a case that writes its own `tsconfig.json`: the project as it says)
        if (root / "tsconfig.json").exists():
            return sh(["tsc", "-p", "."], root)
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


# relations that bind no name the compiler could be asked about
NOT_BINDINGS = {"imports", "imports_from", "re_exports", "contains", "method", "defines",
                "case_of", "dispatches_to", "couples_with", "documents", "handles", "http_calls"}
BINDERS = {"go", "csharp", "rust"}


def binder(lang: str, tmp: Path) -> list[str] | None:
    """The command that prints a project's bindings in `oracle_go.py`'s
    shape (`file`, `line`, `expr`, `to_file`, `to_line`), built once under
    `tmp`: go/types for Go, Roslyn for C#, rust-analyzer for Rust. None when
    it cannot be built."""
    if lang == "go":
        import oracle_go
        if oracle_go.setup(tmp):
            return None
        built = subprocess.run(["go", "build", "-o", "dump", "."], cwd=tmp, capture_output=True,
                               text=True, env={**os.environ, "GOFLAGS": "-mod=mod"})
        return [str(tmp / "dump")] if built.returncode == 0 else None
    if lang == "csharp":
        import oracle_csharp
        tool = oracle_csharp.setup(tmp)
        built = subprocess.run(["dotnet", "build", str(tool), "-c", "Release", "-o", str(tool / "out"),
                                "--nologo", "-v", "q"], capture_output=True, text=True,
                               env={**os.environ, "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_NOLOGO": "1"})
        dll = tool / "out" / "oracle.dll"
        return ["dotnet", str(dll)] if built.returncode == 0 and dll.exists() else None
    if lang == "rust":
        import shutil
        if shutil.which("rust-analyzer") is None:
            return None
        return [sys.executable, str(Path(__file__).resolve().parent / "oracle_rust.py"), "--dump"]
    return None


def _write(c: dict[str, Any], root: Path) -> None:
    for rel, src in c["files"].items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(src, encoding="utf-8")


def _span(n: dict[str, Any]) -> tuple[str, int, int, int, str]:
    """A node's file, definition line, lines (a file node: all of it) and
    bare name (a type and its method can share a line: `type I interface{ Run() }`)."""
    loc = str(n.get("source_location") or "").lstrip("L").split("-")[0]
    line = int(loc) if loc.isdigit() else 0
    name = str(n.get("qualname") or n.get("label") or "").split(".")[-1].strip("()")
    if not line or n.get("kind") == "file":
        return str(n.get("source_file") or ""), 0, 0, 1 << 30, name
    return (str(n.get("source_file") or ""), line, int(n.get("region_start") or line),
            int(n.get("end_line") or line), name)


def binding(c: dict[str, Any], tool: Path | list[str]) -> dict[str, Any]:
    """The compiler's answer to each edge of the case (`--binding`)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "project"
        _write(c, root)
        out = Path(tmp) / "graph.json"
        subprocess.run([sys.executable, "-m", "graphlore_engine", "build", str(root), "--out", str(out)],
                       capture_output=True, text=True)
        if not out.exists():
            return {"id": c["id"], "wrong": [], "unjudged": [], "error": "the engine built no graph"}
        nodes = json.loads(out.read_text(encoding="utf-8"))["nodes"]
        cmd = [str(tool / "dump")] if isinstance(tool, Path) else tool
        done = subprocess.run([*cmd, str(root)], capture_output=True, text=True,
                              env={**os.environ, "GOFLAGS": "-mod=mod", "GOPROXY": "off",
                                   "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_NOLOGO": "1"})
        uses = [json.loads(ln) for ln in done.stdout.splitlines() if ln.startswith("{")]
    if done.returncode != 0 and not uses:
        return {"id": c["id"], "wrong": [], "unjudged": [], "error": done.stderr.strip()[-300:]}
    uses = [u for u in uses if u.get("to_file") and not u.get("receiver")
            and (u["file"], u["line"]) != (u["to_file"], u["to_line"])]

    # (a use belongs to the innermost definition around it, as the engine
    # gives it: a property's body is the property's, not its class's)
    # (but a type written in a field's or variable's declaration is its
    # type's, as the engine gives it: `RunE func(cmd *Command)` in `Command`)
    state = {"variable", "property", "constant"}
    spans = [(id(n), _span(n), n.get("kind") in state) for n in nodes if n.get("type") != "external"]
    types = {"type", "NamedType", "TypeParameter"}

    def home(u: dict[str, Any]) -> int | None:
        around = sorted((hi - lo, is_state, key) for key, (f, line, lo, hi, _), is_state in spans
                        if f == u["file"] and lo <= u["line"] <= hi)
        if not around:
            return None
        # (a field's type is its type's; a package constant's is its own)
        if around[0][1] and u.get("what") in types:
            outer = [a for a in around[1:] if not a[1] and a[0] < (1 << 29)]
            if outer:
                return outer[0][2]
        # (two definitions on the same lines, `{ int A() … int B() … }` on
        # one line: the line cannot say whose the use is)
        if len(around) > 1 and around[1][0] == around[0][0] and around[1][1] == around[0][1]:
            return None
        return around[0][2]

    homes = [home(u) for u in uses]

    def bound(src: str, dst: str) -> bool | None:
        srcs = {id(n) for n in nodes if conformance._matches(n, src) and n.get("type") != "external"}
        dsts = [_span(n) for n in nodes if conformance._matches(n, dst) and n.get("type") != "external"]
        if not srcs or not dsts or not all(d[1] for d in dsts):
            return None
        names = {d[4] for d in dsts}
        seen = [(u, h) for u, h in zip(uses, homes, strict=True) if h in srcs and u["expr"] in names]
        if any((u["to_file"], u["to_line"]) == (df, dl) and u["expr"] == dn
               for u, _ in seen for df, dl, _, _, dn in dsts):
            return True
        # (the compiler wrote nothing of the name there: no answer, as for
        # Roslyn's `: base(…)`, which its dump does not list)
        return False if seen else None

    wrong, unjudged = [], []
    # (an absent edge between two definitions the case also expects an edge
    # between says which relation it is, `references` and not `calls`, a
    # write and not a read: the compiler binds the name either way)
    expected_pairs = {(e[0], e[2]) for e in c["expect"]}
    for edges, want in ((c["expect"], True), (c["absent"], False)):
        for e in edges:
            if e[1] in NOT_BINDINGS:
                continue
            if not want and (e[0], e[2]) in expected_pairs:
                unjudged.append(list(e))
                continue
            got = bound(e[0], e[2])
            # (a name the compiler wrote nothing of is not bound, for an
            # absent edge; for an expected one it is no answer)
            if got is None and not want and any(
                    conformance._matches(n, e[0]) for n in nodes):
                got = False
            if got is None:
                unjudged.append(list(e))
            elif got != want:
                wrong.append({"edge": list(e), "expected": "bound" if want else "not bound",
                              "compiler": "bound" if got else "not bound"})
    return {"id": c["id"], "wrong": wrong, "unjudged": unjudged, "error": ""}


def check_bindings(cases: list[dict[str, Any]], jobs: int, json_out: str | None) -> int:
    todo = [c for c in cases if c["lang"] in BINDERS]
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as tmp:
        for lang in dict.fromkeys(c["lang"] for c in todo):
            (Path(tmp) / lang).mkdir()
            cmd = binder(lang, Path(tmp) / lang)
            if cmd is None:
                print(f"{lang}: the type checker could not be built here", file=sys.stderr)
                return 2
            mine = [c for c in todo if c["lang"] == lang]
            with ThreadPoolExecutor(max_workers=jobs) as pool:
                results += list(pool.map(lambda c, cmd=cmd: binding(c, cmd), mine))
    bad = 0
    for r in results:
        for w in r["wrong"]:
            bad += 1
            print(f"  WRONG    {r['id']:44} {tuple(w['edge'])}: the case says {w['expected']}, "
                  f"the compiler {w['compiler']}")
        if r["error"]:
            print(f"  ERROR    {r['id']:44} {r['error'][:160]}")
        for e in r["unjudged"]:
            print(f"  unjudged {r['id']:44} {tuple(e)}")
    judged = sum(1 for r in results if not r["error"])
    print(f"\n{len(results)} cases asked, {judged} answered; {bad} edges the compiler contradicts, "
          f"{sum(len(r['unjudged']) for r in results)} edges it could not be asked about")
    if json_out:
        Path(json_out).write_text(json.dumps(results, indent=1) + "\n", encoding="utf-8")
    return 1 if bad else 0


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
    ap.add_argument("--binding", action="store_true",
                    help=f"ask the type checker what each edge's names are bound to ({', '.join(sorted(BINDERS))})")
    args = ap.parse_args()
    only = {x for x in args.only.split(",") if x}
    cases = [c for c in conformance.CASES if not only or c["lang"] in only or c["id"] in only]
    if args.binding:
        return check_bindings(cases, args.jobs, args.json)
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
