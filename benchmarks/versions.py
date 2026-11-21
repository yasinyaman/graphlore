# ruff: noqa: E501
"""Which version of a language has which feature, read out of its compiler.

Each compiler keeps that table in one file (docs/rule-sources.md). This script
reads the files `rule_sources.py` checked out and writes one JSON per language
to benchmarks/versions/: the versions, each feature with the version it
arrived in, and, where the compiler's name resolver itself asks for the
version, the places it asks (`asked`): those are where the same source means
something else in another version.

    python benchmarks/rule_sources.py --root /tmp/sources
    python benchmarks/versions.py --root /tmp/sources            # rewrite benchmarks/versions/*.json
    python benchmarks/versions.py --root /tmp/sources --summary   # the counts, as a Markdown table

Python, PHP and Ruby have no such table (release notes, in prose), and
TypeScript's axis is its module-resolution mode: none is generated for them.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

OUT = Path(__file__).resolve().parent / "versions"


def checkout(root: Path, repo: str) -> Path:
    return root / repo.replace("/", "__")


def asked(base: Path, directory: str, pattern: str, suffix: str) -> list[dict[str, Any]]:
    """The lines under `directory` that ask for the language version."""
    out = []
    for path in sorted((base / directory).rglob(f"*{suffix}")):
        if path.name.endswith(("_test.go", "tests.rs")):
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if re.search(pattern, line) and not line.lstrip().startswith(("//", "///", "*")):
                out.append({"file": str(path.relative_to(base)), "line": i, "text": line.strip()[:160]})
    return out


def csharp(root: Path) -> dict[str, Any]:
    path = "src/Compilers/CSharp/Portable/Errors/MessageID.cs"
    src = (checkout(root, "dotnet/roslyn") / path).read_text(encoding="utf-8")
    features, pending = [], []
    body = src[src.index("RequiredVersion(this MessageID feature)"):]
    for line in body.splitlines():
        m = re.search(r"case MessageID\.IDS_(\w+):", line)
        if m:
            pending.append(m.group(1))
        m = re.search(r"return LanguageVersion\.(\w+);", line)
        if m:
            version = m.group(1).replace("CSharp", "").replace("_", ".")
            features += [{"name": n, "since": version} for n in pending]
            pending = []
    base = checkout(root, "dotnet/roslyn")
    return {"repo": "dotnet/roslyn", "path": path, "features": features,
            "asked": asked(base, "src/Compilers/CSharp/Portable/Binder",
                           r"IsFeatureEnabled\(MessageID|LanguageVersion\.CSharp\d|\.LanguageVersion\b.*[<>]=?", ".cs")}


def java(root: Path) -> dict[str, Any]:
    path = "src/jdk.compiler/share/classes/com/sun/tools/javac/code/Source.java"
    src = (checkout(root, "openjdk/jdk") / path).read_text(encoding="utf-8")
    body = src[src.index("public enum Feature"):]
    features = []
    for m in re.finditer(r"^\s+([A-Z][A-Z0-9_]+)\((JDK\d+(?:_\d+)?)(?:,\s*(JDK\d+(?:_\d+)?))?", body, re.M):
        row = {"name": m.group(1), "since": m.group(2).replace("JDK", "").replace("1_", "1.")}
        if m.group(3):
            row["until"] = m.group(3).replace("JDK", "").replace("1_", "1.")
        features.append(row)
    base = checkout(root, "openjdk/jdk")
    return {"repo": "openjdk/jdk", "path": path, "features": features,
            "asked": asked(base, "src/jdk.compiler/share/classes/com/sun/tools/javac/comp",
                           r"Feature\.\w+\.allowedInSource|allow\w+\s*=\s*Feature\.", ".java")}


def kotlin(root: Path) -> dict[str, Any]:
    path = "core/language.version-settings/src/org/jetbrains/kotlin/config/LanguageVersionSettings.kt"
    src = (checkout(root, "JetBrains/kotlin") / path).read_text(encoding="utf-8")
    body = src[src.index("enum class LanguageFeature"):]
    body = body[:body.index("enum class LanguageVersion")] if "enum class LanguageVersion" in body else body
    features = []
    for m in re.finditer(r"^\s{4}([A-Z]\w+)\(\s*(?:sinceVersion\s*=\s*)?(KOTLIN_\d+_\d+|null)", body, re.M):
        since = None if m.group(2) == "null" else m.group(2).replace("KOTLIN_", "").replace("_", ".")
        features.append({"name": m.group(1), "since": since})
    base = checkout(root, "JetBrains/kotlin")
    return {"repo": "JetBrains/kotlin", "path": path, "features": features,
            "asked": asked(base, "compiler/fir/resolve/src", r"supportsFeature\(LanguageFeature\.|LanguageFeature\.\w+\.isEnabled", ".kt")}


def swift(root: Path) -> dict[str, Any]:
    path = "include/swift/Basic/Features.def"
    src = (checkout(root, "swiftlang/swift") / path).read_text(encoding="utf-8")
    features = []
    for m in re.finditer(r"^([A-Z_]*FEATURE[A-Z_]*)\((\w+)\s*,\s*([^)]*)\)", src, re.M):
        kind, name, rest = m.group(1), m.group(2), m.group(3)
        row: dict[str, Any] = {"name": name, "kind": kind.lower()}
        mode = re.search(r"LanguageMode::v(\d+)", rest)
        if mode:
            row["since"] = mode.group(1)  # the language mode that turns it on
        features.append(row)
    return {"repo": "swiftlang/swift", "path": path, "features": features}


def rust(root: Path) -> dict[str, Any]:
    base = checkout(root, "rust-lang/rust")
    path = "compiler/rustc_feature/src/accepted.rs"
    src = (base / path).read_text(encoding="utf-8")
    features = [{"name": m.group(1), "since": m.group(2)}
                for m in re.finditer(r'\(accepted,\s*(\w+),\s*"([\d.]+)"', src)
                if not m.group(1).startswith("test_")]
    editions = re.findall(r"^\s+Edition(\d{4}),", (base / "compiler/rustc_span/src/edition.rs").read_text(encoding="utf-8"), re.M)
    return {"repo": "rust-lang/rust", "path": path, "versions": sorted(set(editions)), "features": features,
            "asked": asked(base, "compiler/rustc_resolve/src", r"rust_20\d\d\(|Edition::Edition20|\.edition\(\)", ".rs")}


def go(root: Path) -> dict[str, Any]:
    base = checkout(root, "golang/go")
    path = "src/go/types/version.go"
    src = (base / path).read_text(encoding="utf-8")
    versions = re.findall(r'asGoVersion\("go(\d+\.\d+)"\)', src)
    return {"repo": "golang/go", "path": path, "versions": versions, "features": [],
            "asked": [a for a in asked(base, "src/go/types", r"\bgo1_\d+\b", ".go") if not a["file"].endswith("version.go")]}


def c_cpp(root: Path) -> dict[str, Any]:
    path = "clang/include/clang/Basic/LangStandards.def"
    src = (checkout(root, "llvm/llvm-project") / path).read_text(encoding="utf-8")
    features = []
    for m in re.finditer(r'^LANGSTANDARD\((\w+),\s*"([^"]+)",\s*(\w+),\s*"([^"]+)",\s*([^)]*)\)', src, re.M | re.S):
        flags = [f.strip() for f in re.split(r"[|,]", m.group(5)) if re.match(r"\s*[A-Za-z]", f)]
        features.append({"name": m.group(2), "language": m.group(3), "title": m.group(4),
                         "turns_on": [f for f in flags if f != "std::nullopt"]})
    return {"repo": "llvm/llvm-project", "path": path, "versions": [f["name"] for f in features],
            "features": features}


def scala(root: Path) -> dict[str, Any]:
    path = "compiler/src/dotty/tools/dotc/config/SourceVersion.scala"
    src = (checkout(root, "scala/scala3") / path).read_text(encoding="utf-8")
    versions = sorted(set(re.findall(r"`(\d+\.\d+)`", src)), key=lambda v: [int(x) for x in v.split(".")])
    base = checkout(root, "scala/scala3")
    return {"repo": "scala/scala3", "path": path, "versions": versions, "features": [],
            "asked": asked(base, "compiler/src/dotty/tools/dotc/typer", r"sourceVersion\.is|migrateTo3|isScala2Mode|SourceVersion\.`", ".scala")}


READERS = {"csharp": csharp, "java": java, "kotlin": kotlin, "swift": swift, "rust": rust, "go": go,
           "c-cpp": c_cpp, "scala": scala}


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--root", required=True, help="the checkouts of rule_sources.py")
    ap.add_argument("--only", help="comma-separated languages")
    ap.add_argument("--summary", action="store_true", help="print the counts as a Markdown table, write nothing")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    lock_path = root / "sources.lock.json"
    if not lock_path.exists():
        print(f"{lock_path}: run rule_sources.py --root {root} first", file=sys.stderr)
        return 2
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    wanted = args.only.split(",") if args.only else list(READERS)
    rows = []
    for lang in wanted:
        table = READERS[lang](root)
        repo, path = table.pop("repo"), table.pop("path")
        doc = {"language": lang, "source": f"{repo}@{lock.get(repo, '?')[:12]}:{path}", **table}
        if not doc.get("versions"):
            order = [f["since"] for f in doc["features"] if f.get("since")]
            doc["versions"] = sorted(set(order), key=lambda v: [int(x) if x.isdigit() else 0 for x in re.split(r"[._]", v)])
        rows.append((lang, len(doc["versions"]), len(doc["features"]), len(doc.get("asked", [])), doc["source"]))
        if not args.summary:
            OUT.mkdir(exist_ok=True)
            (OUT / f"{lang}.json").write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    if args.summary:
        print("| Language | versions | features | places the resolver asks | read from |")
        print("|---|--:|--:|--:|---|")
    for lang, versions, features, places, source in rows:
        if args.summary:
            print(f"| {lang} | {versions} | {features} | {places or '–'} | `{source}` |")
        else:
            print(f"{lang:<8} {versions:>3} versions, {features:>4} features, {places:>3} places asked  ({source})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
