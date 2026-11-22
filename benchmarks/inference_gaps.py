"""What one step of inference would add: the compilers' missed uses, by kind.

Phase 3 of docs/rule-sources.md starts from numbers. Every oracle
(`oracle_*.py --json`) writes the uses the compiler binds to a definition of
the graph that have no edge (`missed`). This reads those rows and sorts each
by what stands before the name, the receiver:

  closure-parameter   a parameter of a lambda / closure written on the line or
                      just above it (`x => x.m()`, `|r| r.m()`, `{ it.m() }`,
                      `$0.m()`): typed by the call the closure is passed to
  call-result         the result of a call or an index (`f().m()`, `xs[0].m()`)
  bare-name           a local, parameter or field the walk did not type
  other-receiver      a member of a member, a cast, a literal, a parenthesized value
and, for a name that stands alone:
  initializer         a member named in an initializer, a struct literal or a
                      named argument (`new T { Name = … }`, `f(name: …)`,
                      `Use: "child",` on a line of its own)
  type-before-member  a type a member is reached through (`Proxy::new()`,
                      `Token.Name()`): the member is linked, not the type
  type                a type written elsewhere
  bare                a call or a value by its bare name
  import              an import statement's own names

    python benchmarks/inference_gaps.py out/csharp.json out/rust.json ... --root DIR --samples 5

`--root` holds the repositories the rows' `case` names (`<repo>/<file>`), so a
closure written over several lines is seen from the lines above the use.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path

KINDS = ("closure-parameter", "call-result", "bare-name", "other-receiver",
         "initializer", "type-before-member", "type", "bare", "import")
TYPE_KINDS = {"class", "struct", "interface", "enum", "trait", "type_alias", "typealias",
              "protocol", "record", "object", "module", "namespace", "union"}

# the parameters a lambda declares on a line, per spelling
LAMBDA_PARAMS = [
    re.compile(r"\(([\w\s,:<>\[\]?]*)\)\s*=>"),          # (a, b) => / (a: T) =>
    re.compile(r"\b(\w+)\s*=>"),                          # a =>
    re.compile(r"\|([\w\s,:&<>()\[\]']*)\|"),             # |a, b|  (Rust, Ruby)
    re.compile(r"\{\s*([\w\s,:<>()]+?)\s*->"),            # { a, b -> (Kotlin)
    re.compile(r"\{\s*\(?([\w\s,]+?)\)?\s+in\b"),         # { a in / { (a, b) in (Swift)
    re.compile(r"\bfunc\s*\(([^)]*)\)"),                  # func(a T) (Go)
    re.compile(r"\bfunction\s*\(([^)]*)\)"),              # function (a) (JS, PHP)
    re.compile(r"\blambda\s+([\w\s,]+):"),                # lambda a: (Python)
]
IMPLICIT = {"it", "$0", "$1", "$2", "_"}


def lambda_params(line: str) -> set[str]:
    out: set[str] = set()
    for rx in LAMBDA_PARAMS:
        for m in rx.finditer(line):
            for part in m.group(1).split(","):
                words = re.findall(r"[A-Za-z_$][\w$]*", part)
                if words:
                    # `a: T` / `T a` / `a T`: the name is the first word in
                    # most spellings, the last in C-like ones
                    out.add(words[0])
                    out.add(words[-1])
    return out


def receiver(src: str, name: str) -> str | None:
    """What is written before `.name` / `?.name` / `->name` / `::name`, or None."""
    for m in re.finditer(r"(?:\?\.|\.|->|::)\s*" + re.escape(name) + r"\b", src):
        left = src[: m.start()].rstrip()
        if not left:
            continue
        return left
    return None


def kind_of(row: dict, before: list[str], full: str | None = None) -> str:
    name = str(row.get("expr") or "").split(".")[-1].split("(")[0].strip()
    # (the oracles keep 110 characters of the line: the file has all of it)
    src = full if full is not None else str(row.get("src") or "")
    if not name:
        return "other-receiver"
    # an import names what it imports: a use the reader leaves to the import
    # edge, no inference
    if re.match(r"\s*(use|import|from|using|require|include|#include)\b", src):
        return "import"
    left = receiver(src, name)
    if left is None:
        if str(row.get("kind") or "") in TYPE_KINDS:
            # `Proxy::new(…)`, `Token.Name(…)`: the type a member is reached
            # through, which the engine links the member of, not itself
            if re.search(r"\b" + re.escape(name) + r"\s*(::|\.)\s*[A-Za-z_]", src):
                return "type-before-member"
            # a type written (a parameter's, a `new`'s, a base)
            return "type"
        # `new T { Name = … }`, `f(name: …)`: an initializer's or a named
        # argument's member name, a position the reader leaves out
        if re.search(r"(^|[{(,])\s*" + re.escape(name) + r"\s*[=:](?![=:])", src):
            return "initializer"
        return "bare"
    m = re.search(r"([A-Za-z_$][\w$]*|\$\d)\s*$", left)
    if m:
        head = m.group(1)
        # the head is one name (not a member of something before it)
        start = m.start()
        if start == 0 or left[start - 1] not in ".>:":
            params = set()
            for line in before + [src]:
                params |= lambda_params(line)
            if head in IMPLICIT or head in params:
                return "closure-parameter"
            return "bare-name"
        return "other-receiver"
    if left.endswith((")", "]")):
        return "call-result"
    return "other-receiver"


_LINES: dict[Path, list[str]] = {}


def context(row: dict, roots: list[Path]) -> tuple[list[str], str | None]:
    """The three lines above the use, and the use's whole line."""
    case = str(row.get("case") or "")
    line = int(row.get("line") or 0)
    for root in roots:
        for cand in (root / case, root / str(row.get("file") or "")):
            if cand.is_file() and line > 0:
                if cand not in _LINES:
                    _LINES[cand] = cand.read_text(encoding="utf-8", errors="replace").splitlines()
                lines = _LINES[cand]
                if line <= len(lines):
                    return lines[max(0, line - 4): line - 1], lines[line - 1]
    return [], None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("files", nargs="+", help="oracle --json outputs, named <language>.json")
    ap.add_argument("--root", action="append", default=[], help="where the rows' repositories are")
    ap.add_argument("--samples", type=int, default=0)
    ap.add_argument("--json", help="write the counts here")
    args = ap.parse_args()
    roots = [Path(r) for r in args.root]
    table: dict[str, collections.Counter] = {}
    found: dict[str, int] = {}
    samples: dict[tuple[str, str], list[str]] = collections.defaultdict(list)
    for f in args.files:
        lang = Path(f).stem
        data = json.loads(Path(f).read_text())
        rows = data.get("missed", [])
        found[lang] = int(data.get("found") or 0)
        counts: collections.Counter = collections.Counter()
        for row in rows:
            before, full = context(row, roots)
            k = kind_of(row, before, full)
            counts[k] += 1
            if len(samples[(lang, k)]) < args.samples:
                samples[(lang, k)].append(f"{row.get('case')}:{row.get('line')}  {row.get('src')}")
        table[lang] = counts
    # (each cell: the rows of the kind, and what share of every use the
    # compiler binds, found or missed, they are)
    print("| language | uses | missed | " + " | ".join(KINDS) + " |")
    print("|---|--:|--:|" + "--:|" * len(KINDS))
    for lang, c in table.items():
        missed = sum(c.values())
        uses = found[lang] + missed
        cells = [f"{c[k]} ({100 * c[k] / max(1, uses):.1f}%)" for k in KINDS]
        print(f"| {lang} | {uses} | {missed} | " + " | ".join(cells) + " |")
    for (lang, k), rows in sorted(samples.items()):
        print(f"\n{lang} / {k}:")
        for r in rows:
            print("   ", r[:200])
    if args.json:
        counts = {lang: dict(c) for lang, c in table.items()}
        Path(args.json).write_text(json.dumps(counts, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
