# ruff: noqa: E501
"""The places a type can be written, per grammar, and whether the reader reads them.

Phase 1 of docs/rule-sources.md. A resolution rule cannot apply to a name the
reader never looks at, and those gaps were being found one repository at a
time (a Rust tuple struct's field, a Go function's second result). This lists
them from the grammar instead.

For every source file of a corpus the script parses the file with the
language's tree-sitter grammar and takes each *name written where a type is*
(a `type_identifier`, or the grammar's equivalent). The place it is written is
named by the node that holds the type and the field it sits in
(`ordered_field_declaration_list.type`, `const_item.type`,
`method_declaration.result`), climbing through the nodes that only build a
type (`generic_type`, `pointer_type`, a list of type arguments). It then asks
the engine for the uses it read in that file (`graphlore_engine.file_facts`)
and counts a place as read when a use of that name is recorded on the line of
the name or of the declaration around it. Only names of types the repository
itself defines are counted: a library's type or a predeclared one makes no
edge, read or not.

    python benchmarks/positions.py --root /tmp/polyglot                # the places read less than nine times in ten
    python benchmarks/positions.py --root /tmp/polyglot --only rust,go --all
    python benchmarks/positions.py --root /tmp/polyglot --json out.json --baseline benchmarks/results-positions.json

A place the reader leaves out on purpose is written in DECIDED below with the
reason, and is not listed again. `--grammar DIR` (the cargo registry's
`tree-sitter-*` sources) adds, per grammar, the places `node-types.json` says a
type can be written that the corpus never showed.

JavaScript and Ruby write no types and are not measured.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compare_engines import EXT_LANG  # noqa: E402

# the grammar's name in tree_sitter_language_pack, per report language
PACK = {"python": "python", "typescript": "typescript", "tsx": "tsx", "go": "go", "java": "java",
        "kotlin": "kotlin", "scala": "scala", "csharp": "csharp", "rust": "rust", "swift": "swift",
        "c": "c", "cpp": "cpp", "c/c++ header": "cpp", "php": "php"}

# Nodes that only build a type out of other types: the place is what holds them.
BUILDERS = {
    "generic_type", "scoped_type_identifier", "qualified_type", "pointer_type", "reference_type",
    "array_type", "slice_type", "map_type", "channel_type", "tuple_type", "union_type",
    "intersection_type", "optional_type", "nullable_type", "function_type", "dynamic_type",
    "abstract_type", "bounded_type", "type_arguments", "type_argument_list", "user_type",
    "type_projection", "parenthesized_type", "nested_type_identifier", "qualified_name",
    "generic_name", "tuple_element", "ref_type", "alias_qualified_name", "named_type",
    "type_annotation", "type", "subscript", "attribute", "binary_operator", "type_list",
    "simple_user_type", "annotated_type", "compound_type", "infix_type", "projected_type",
    "stable_type_identifier", "type_descriptor", "template_type", "template_argument_list",
    "qualified_identifier", "scoped_identifier", "wildcard", "wildcard_bounds", "literal_type",
    "readonly_type", "rest_type", "optional_type_pattern", "lifetime", "negated_type",
    "type_identifier", "primary_type", "union_type_pattern", "array", "generic", "member_type",
    "implicit_type", "pointer_declarator", "abstract_pointer_declarator", "scoped_type_arguments",
    "type_binding", "removed_trait_bound", "higher_ranked_trait_bound", "bounded_type_parameter",
    "lookup_type", "conditional_type", "index_type_query", "flow_maybe_type", "object_type",
    "tuple_parameter", "metatype", "existential_type", "opaque_type", "protocol_composition_type",
    "dictionary_type", "tuple_type_item", "inheritance_specifier", "function_type_parameters",
    "parenthesized_type_identifier", "nullable_reference_type", "function_pointer_type",
    "optional_named_type", "disjunctive_normal_form_type", "variadic_type", "applied_type",
    "match_type", "singleton_type", "lambda_type", "structural_type", "refinement",
}

# The kind of a name written where a type is, per language (default: `type_identifier`).
LEAVES = {"python": {"identifier"}, "csharp": {"identifier"}, "php": {"name"}}

# Places the reader leaves out on purpose: "language: holder.field" -> why.
DECIDED: dict[str, str] = {
    "go: method_declaration.receiver": "the type a method is declared for owns it; it is not a use of the type",
    "rust: impl_item.type": "the type an `impl` is of owns what the block declares; it is not a use of the type",
    "kotlin: class_declaration.-": "the declaration's own name (the grammar gives it no `name` field)",
    "kotlin: object_declaration.-": "the declaration's own name",
    "c: type_definition.declarator": "the name a `typedef` declares",
    "cpp: type_definition.declarator": "the name a `typedef` declares",
    "c/c++ header: type_definition.declarator": "the name a `typedef` declares",
    "c/c++ header: variadic_type_parameter_declaration.-": "a template parameter being declared",
}


def position(leaf: Any, lang: str) -> tuple[str, Any] | None:
    """The place `leaf` is written, as `holder.field`, and the holder; None
    when it is not a type at all (a declaration's own name, a value)."""
    node, child = leaf.parent, leaf
    reached_type = leaf.type == "type_identifier"
    while node is not None:
        field = next((node.field_name_for_child(i) for i in range(node.child_count)
                      if node.child(i).id == child.id), None)
        if node.type in ("type", "named_type", "type_annotation", "type_arguments", "type_argument_list"):
            reached_type = True
        if node.type not in BUILDERS:
            if field in ("type", "returns", "return_type", "result", "bounds", "trait", "superclass",
                         "interfaces", "element", "constraint"):
                reached_type = True
            if node.type in ("base_list", "type_parameter_constraint", "superclass", "super_interfaces",
                             "type_parameter_constraints_clause", "base_clause", "class_interface_clause"):
                reached_type = True
            if not reached_type:
                return None
            # a declaration's own name, a type parameter being declared
            if field == "name" or node.type in ("type_parameter", "type_parameters", "type_parameter_list",
                                                "type_parameter_declaration", "constrained_type_parameter"):
                return None
            # (a Go method's receiver is a parameter list of its own)
            up = node.parent
            if up is not None and up.parent is not None and up.type == "parameter_list" \
                    and up.parent.child_by_field_name("receiver") is not None \
                    and up.parent.child_by_field_name("receiver").id == up.id:
                return "method_declaration.receiver", node
            return f"{node.type}.{field or '-'}", node
        # (inside a builder: the member of a qualified name that is not its
        # last part is a namespace or an outer type, read with the whole path)
        if node.type in ("scoped_type_identifier", "qualified_name", "qualified_type", "attribute",
                         "nested_type_identifier", "qualified_identifier", "stable_type_identifier",
                         "alias_qualified_name", "member_type", "scoped_identifier") \
                and node.child(node.child_count - 1).id != child.id:
            return None
        if node.type in ("subscript", "attribute", "binary_operator", "array", "generic") and lang != "python":
            return None
        child, node = node, node.parent
    return None


TYPE_KINDS = {"class", "struct", "interface", "enum", "type_alias", "union", "object", "record"}


def measure(lang: str, src: bytes, rel: str, facts: dict[str, Any], own: set[str], parser: Any,
            seen: dict[str, list[int]], samples: dict[str, str]) -> None:
    read: dict[int, set[str]] = collections.defaultdict(set)
    for r in facts.get("refs", []):
        for seg in r.get("path", []):
            read[r["line"]].add(seg.split("<")[0].lstrip("$"))
    for imp in facts.get("imports", []):
        line = imp.get("line") or 0
        for key in ("module", "name", "alias"):
            value = imp.get(key)
            if isinstance(value, str):
                read[line].update(value.replace("::", ".").replace("\\", ".").replace("/", ".").split("."))
        for name in imp.get("names") or []:
            if isinstance(name, list):
                read[line].update(str(x) for x in name if x)
            elif isinstance(name, str):
                read[line].add(name)
    tree = parser.parse(src)
    leaves = LEAVES.get(lang, {"type_identifier"})
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.child_count:
            stack.extend(node.children)
            continue
        if node.type not in leaves:
            continue
        found = position(node, lang)
        if found is None:
            continue
        key, holder = found
        name = node.text.decode("utf-8", "replace")
        if name not in own:
            continue
        # the line of the name, of what holds it, and of the declarations around
        lines = {node.start_point[0] + 1}
        up, depth = holder, 0
        while up is not None and depth < 5:
            lines.add(up.start_point[0] + 1)
            up, depth = up.parent, depth + 1
        hit = any(name in read.get(line, ()) for line in lines)
        row = seen.setdefault(key, [0, 0])
        row[0] += 1
        row[1] += int(hit)
        if not hit and key not in samples:
            text = src.splitlines()[node.start_point[0]].decode("utf-8", "replace").strip()[:100]
            samples[key] = f"{rel}:{node.start_point[0] + 1}  {text}"


def grammar_positions(registry: Path, lang: str) -> set[str]:
    """`holder.field` for every field of the grammar that can hold a type."""
    crate = {"csharp": "c-sharp", "c/c++ header": "cpp", "tsx": "typescript", "kotlin": "kotlin-sg"}.get(lang, lang)
    found: set[str] = set()
    for path in sorted(registry.glob(f"tree-sitter-{crate}-*/**/node-types.json")):
        if lang == "tsx" and "/tsx/" not in str(path):
            continue
        if lang == "typescript" and "/typescript/src" not in str(path):
            continue
        for node in json.loads(path.read_text(encoding="utf-8")):
            if node["type"] in BUILDERS:
                continue
            for field, spec in (node.get("fields") or {}).items():
                kinds = {t["type"] for t in spec.get("types", [])}
                if kinds & (BUILDERS | {"_type", "_simple_type", "type_identifier"}) and field != "name":
                    found.add(f"{node['type']}.{field}")
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--root", required=True, help="a directory of repositories (the audit's)")
    ap.add_argument("--only", help="comma-separated languages")
    ap.add_argument("--files", type=int, default=400, help="source files read per language")
    ap.add_argument("--all", action="store_true", help="every place, not only those read less than nine times in ten")
    ap.add_argument("--grammar", help="the cargo registry's sources: also list the places the corpus never showed")
    ap.add_argument("--json", help="write every place's counts here")
    ap.add_argument("--baseline", help="an earlier --json: exit 1 when a place is read less than it was")
    args = ap.parse_args()
    import graphlore_engine as engine
    from tree_sitter_language_pack import get_parser

    wanted = set(args.only.split(",")) if args.only else set(PACK)
    by_lang: dict[str, dict[str, list[tuple[Path, str]]]] = collections.defaultdict(dict)
    root = Path(args.root).resolve()
    for repo in sorted(p for p in root.iterdir() if p.is_dir()):
        for path in sorted(repo.rglob("*")):
            lang = EXT_LANG.get(path.suffix)
            parts = path.relative_to(repo).parts
            if (lang in wanted and lang in PACK and path.is_file()
                    and not any(p.startswith(".") or p in ("node_modules", "vendor", "graphify-out",
                                                           "graphlore-native", "target", "build") for p in parts)):
                by_lang[lang].setdefault(repo.name, []).append((path, str(path.relative_to(repo))))
    out: dict[str, dict[str, Any]] = {}
    for lang in sorted(by_lang):
        seen: dict[str, list[int]] = {}
        samples: dict[str, str] = {}
        parser = get_parser(PACK[lang])
        each = max(1, args.files // len(by_lang[lang]))
        for _repo, files in sorted(by_lang[lang].items()):
            # the types the repository defines, then each file against them
            read_files = []
            own: set[str] = set()
            for path, rel in files:
                src = path.read_bytes()
                if len(src) > 400_000:
                    continue
                try:
                    facts = json.loads(engine.file_facts(src, rel))
                except Exception:  # noqa: BLE001 (a file the engine cannot read is not a position)
                    continue
                own.update(d["name"] for d in facts.get("defs", []) if d.get("kind") in TYPE_KINDS)
                read_files.append((src, rel, facts))
            step = max(1, len(read_files) // each)
            for src, rel, facts in read_files[::step][:each]:
                measure(lang, src, rel, facts, own, parser, seen, samples)
        out[lang] = {key: {"seen": n, "read": r, "example": samples.get(key, "")}
                     for key, (n, r) in sorted(seen.items())}
        counted = [v for k, v in seen.items() if f"{lang}: {k}" not in DECIDED]
        total = sum(n for n, _ in counted)
        read = sum(r for _, r in counted)
        print(f"\n== {lang}: {len(seen)} places, {total:,} type names, {read:,} read ({100 * read / max(total, 1):.1f}%)")
        for key, (n, r) in sorted(seen.items(), key=lambda kv: kv[1][1] - kv[1][0]):
            if f"{lang}: {key}" in DECIDED or (not args.all and (n < 3 or r >= 0.9 * n)):
                continue
            print(f"   {key:<52} {r:>6,} of {n:>6,}   {samples.get(key, '')}")
        if args.grammar:
            unseen = grammar_positions(Path(args.grammar), lang) - set(seen)
            if unseen:
                print(f"   in the grammar, not in the corpus: {', '.join(sorted(unseen))}")
    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    if args.baseline:
        before = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        worse = [f"{lang}: {key}  {out[lang][key]['read']}/{out[lang][key]['seen']} read, "
                 f"{row['read']}/{row['seen']} in the baseline"
                 for lang, rows in before.items() if lang in out
                 for key, row in rows.items()
                 if key in out[lang] and row["seen"] and out[lang][key]["seen"]
                 and out[lang][key]["read"] / out[lang][key]["seen"] < row["read"] / row["seen"] - 0.02]
        print(f"\nagainst the baseline: {len(worse)} places read less")
        for line in worse:
            print(f"  LESS  {line}")
        return 1 if worse else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
