# ruff: noqa: E501
"""Fetch the parts of each language's own tools that say what a name means.

The native engine's resolution rules are meant to be read out of the
languages' own compilers, interpreters and specifications, not found one
repository at a time (docs/rule-sources.md). This script checks out, per
language, only the directories that hold the name binder, the member and
overload lookup, the table that says which language version has which
feature, and the specification where it lives in a repository: a sparse,
blobless, depth-1 clone each, a few hundred megabytes for all of them where
the full repositories are tens of gigabytes.

    python benchmarks/rule_sources.py --list                 # what is read, per language
    python benchmarks/rule_sources.py --root /tmp/sources    # fetch everything
    python benchmarks/rule_sources.py --root /tmp/sources --only rust,csharp
    python benchmarks/rule_sources.py --root /tmp/sources --check   # every path named below exists

`--root`/sources.lock.json records the commit each checkout is at, so a rule
can cite `repo@commit:path`. Network: github.com only.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# language -> [(repository, [sparse directories], {role: path}), ...]
# The roles name what a reader is looking for:
#   binder    scopes and unqualified names
#   members   member, method and overload lookup
#   modules   imports, packages, module paths
#   versions  the table of language versions and the features each has
#   spec      the specification, where a repository holds it
SOURCES: dict[str, list[tuple[str, list[str], dict[str, str]]]] = {
    "go": [
        ("golang/go", ["src/go/types", "src/go/version", "src/internal/goversion", "doc"],
         {"binder": "src/go/types/resolver.go", "members": "src/go/types/lookup.go",
          "modules": "src/go/types/resolver.go", "versions": "src/go/types/version.go",
          "spec": "doc/go_spec.html"}),
    ],
    "rust": [
        ("rust-lang/rust", ["compiler/rustc_resolve/src", "compiler/rustc_hir_typeck/src/method",
                            "compiler/rustc_feature/src", "compiler/rustc_span/src"],
         {"binder": "compiler/rustc_resolve/src/ident.rs", "members": "compiler/rustc_hir_typeck/src/method/probe.rs",
          "modules": "compiler/rustc_resolve/src/imports.rs", "versions": "compiler/rustc_span/src/edition.rs"}),
        ("rust-lang/reference", ["src"], {"spec": "src/names/name-resolution.md"}),
    ],
    "csharp": [
        ("dotnet/roslyn", ["src/Compilers/CSharp/Portable/Binder", "src/Compilers/CSharp/Portable/Errors"],
         {"binder": "src/Compilers/CSharp/Portable/Binder/Binder_Lookup.cs",
          "members": "src/Compilers/CSharp/Portable/Binder/Semantics/OverloadResolution/OverloadResolution.cs",
          "modules": "src/Compilers/CSharp/Portable/Binder/Imports.cs",
          "versions": "src/Compilers/CSharp/Portable/Errors/MessageID.cs"}),
        ("dotnet/csharpstandard", ["standard"], {"spec": "standard/basic-concepts.md"}),
    ],
    "typescript": [
        ("microsoft/TypeScript", ["tsc/internal/binder", "tsc/internal/checker", "tsc/internal/module",
                                  "tsc/internal/core"],
         {"binder": "tsc/internal/binder/nameresolver.go", "members": "tsc/internal/checker/checker.go",
          "modules": "tsc/internal/module/resolver.go"}),
    ],
    "javascript": [
        ("nodejs/node", ["doc/api", "lib/internal/modules"],
         {"modules": "lib/internal/modules/cjs/loader.js", "spec": "doc/api/modules.md"}),
    ],
    "python": [
        ("python/cpython", ["Python", "Lib/importlib", "Doc/reference", "Doc/whatsnew", "Grammar"],
         {"binder": "Python/symtable.c", "modules": "Lib/importlib/_bootstrap.py",
          "versions": "Doc/whatsnew", "spec": "Doc/reference/executionmodel.rst"}),
    ],
    "java": [
        ("openjdk/jdk", ["src/jdk.compiler/share/classes/com/sun/tools/javac/comp",
                         "src/jdk.compiler/share/classes/com/sun/tools/javac/code"],
         {"binder": "src/jdk.compiler/share/classes/com/sun/tools/javac/comp/Resolve.java",
          "members": "src/jdk.compiler/share/classes/com/sun/tools/javac/comp/Attr.java",
          "modules": "src/jdk.compiler/share/classes/com/sun/tools/javac/comp/TypeEnter.java",
          "versions": "src/jdk.compiler/share/classes/com/sun/tools/javac/code/Source.java"}),
    ],
    "kotlin": [
        ("JetBrains/kotlin", ["compiler/fir/resolve/src", "core/language.version-settings/src"],
         {"binder": "compiler/fir/resolve/src/org/jetbrains/kotlin/fir/resolve/calls/tower/FirTowerResolver.kt",
          "members": "compiler/fir/resolve/src/org/jetbrains/kotlin/fir/resolve/calls",
          "versions": "core/language.version-settings/src/org/jetbrains/kotlin/config/LanguageVersionSettings.kt"}),
        ("Kotlin/kotlin-spec", ["docs/src/md"], {"spec": "docs/src/md/kotlin.core/overload-resolution.md"}),
    ],
    "swift": [
        ("swiftlang/swift", ["lib/Sema", "lib/AST", "include/swift/Basic"],
         {"binder": "lib/AST/UnqualifiedLookup.cpp", "members": "lib/Sema/TypeCheckNameLookup.cpp",
          "modules": "lib/AST/NameLookup.cpp", "versions": "include/swift/Basic/Features.def"}),
    ],
    "scala": [
        ("scala/scala3", ["compiler/src/dotty/tools/dotc/typer", "compiler/src/dotty/tools/dotc/config",
                          "docs/_docs/reference"],
         {"binder": "compiler/src/dotty/tools/dotc/typer/Typer.scala",
          "members": "compiler/src/dotty/tools/dotc/typer/Applications.scala",
          "modules": "compiler/src/dotty/tools/dotc/typer/ImportInfo.scala",
          "versions": "compiler/src/dotty/tools/dotc/config/SourceVersion.scala",
          "spec": "docs/_docs/reference"}),
    ],
    "c-cpp": [
        ("llvm/llvm-project", ["clang/lib/Sema", "clang/include/clang/Basic"],
         {"binder": "clang/lib/Sema/SemaLookup.cpp", "members": "clang/lib/Sema/SemaOverload.cpp",
          "versions": "clang/include/clang/Basic/LangStandards.def"}),
    ],
    "ruby": [
        ("ruby/ruby", ["doc"],
         {"binder": "vm_insnhelper.c", "members": "vm_method.c", "modules": "load.c",
          "versions": "doc/NEWS", "spec": "doc/syntax"}),
    ],
    "php": [
        ("php/php-src", ["Zend"],
         {"binder": "Zend/zend_compile.c", "members": "Zend/zend_inheritance.c", "versions": "UPGRADING"}),
        ("php/php-langspec", ["spec"], {"spec": "spec/18-namespaces.md"}),
    ],
}


def run(*argv: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(argv), cwd=cwd, capture_output=True, text=True)


def fetch(root: Path, repo: str, dirs: list[str]) -> str:
    """A sparse, blobless, depth-1 checkout of `repo` under `root`; its commit."""
    at = root / repo.replace("/", "__")
    if not (at / ".git").exists():
        at.parent.mkdir(parents=True, exist_ok=True)
        done = run("git", "clone", "--depth", "1", "--filter=blob:none", "--sparse",
                   f"https://github.com/{repo}.git", str(at))
        if done.returncode != 0:
            raise SystemExit(f"{repo}: {done.stderr.strip()[-300:]}")
    done = run("git", "sparse-checkout", "set", "--cone", *dirs, cwd=at)
    if done.returncode != 0:
        raise SystemExit(f"{repo}: {done.stderr.strip()[-300:]}")
    return run("git", "rev-parse", "HEAD", cwd=at).stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--root", help="where the checkouts go")
    ap.add_argument("--only", help="comma-separated languages")
    ap.add_argument("--list", action="store_true", help="print what is read per language and exit")
    ap.add_argument("--check", action="store_true", help="every path named exists in the checkouts under --root")
    args = ap.parse_args()
    wanted = set(args.only.split(",")) if args.only else set(SOURCES)
    unknown = wanted - set(SOURCES)
    if unknown:
        print(f"no such language: {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2
    if args.list or not args.root:
        for lang in sorted(wanted):
            for repo, _, roles in SOURCES[lang]:
                for role, path in roles.items():
                    print(f"{lang:<11} {role:<9} {repo}:{path}")
        return 0
    root = Path(args.root).resolve()
    lock_path = root / "sources.lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8")) if lock_path.exists() else {}
    missing = []
    for lang in sorted(wanted):
        for repo, dirs, roles in SOURCES[lang]:
            at = root / repo.replace("/", "__")
            if not args.check:
                lock[repo] = fetch(root, repo, dirs)
                print(f"{lang:<11} {repo} @ {lock[repo][:12]}")
            missing += [f"{lang}: {repo}:{path}" for path in roles.values() if not (at / path).exists()]
    if not args.check:
        root.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for row in missing:
        print(f"MISSING  {row}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
