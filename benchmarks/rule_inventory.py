"""Every name-binding rule a language's own tools state, and what the engine does with it.

Phase 2 of docs/rule-sources.md reads a language's binder and specification
section by section and lists the rules found there. Each rule is either held
by a case of `benchmarks/conformance.py` (its id) or written down as not
followed, with the reason. This file is that list; it checks that every case
it names exists, and prints, per language, how many rules are held and how
many of the checklist's cases were found in the language's own tools
(`found_by("source")`).

    python benchmarks/rule_inventory.py              # the summary
    python benchmarks/rule_inventory.py --markdown   # the table docs/rule-sources.md holds
    python benchmarks/rule_inventory.py --lang csharp
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

STD_CS = "csharpstandard"
RS_CS = "Roslyn"


def held(*cases: str) -> dict:
    return {"cases": list(cases)}


def not_followed(reason: str) -> dict:
    return {"reason": reason}


# (section of the source, the rule, held / not followed)
INVENTORY: dict[str, list[tuple[str, str, dict]]] = {
    "csharp": [
        # §7.8.1 Namespace and type names (Binder_Symbols.cs BindNamespaceOrTypeSymbol,
        # Binder_Lookup.cs LookupSymbolsSimpleName walking the binder chain)
        (f"{STD_CS} §7.8.1; {RS_CS} LookupSymbolsSimpleName",
         "a method's type parameter is the name first", held("csharp/method-type-parameter-first")),
        (f"{STD_CS} §7.8.1",
         "then a nested type of the type around, its own or a base's",
         held("csharp/nested-type", "csharp/nested-type-of-a-base")),
        (f"{STD_CS} §7.8.1; {RS_CS} InContainerBinder",
         "then a type of each namespace around, innermost first",
         held("csharp/enclosing-namespace")),
        (f"{STD_CS} §7.8.1",
         "a type of the namespace comes before one a `using` imports",
         held("csharp/namespace-type-before-using")),
        (f"{STD_CS} §7.8.1; {RS_CS} WithExternAndUsingAliasesBinder",
         "a using alias comes before the namespaces `using` imports",
         held("csharp/using-alias-before-using-namespace", "csharp/using-alias")),
        (f"{STD_CS} §7.8.1; {RS_CS} WithUsingNamespacesAndTypesBinder",
         "a `using` namespace imports its types, not its nested namespaces",
         held("csharp/using")),
        (f"{STD_CS} §14.5.1",
         "a `using` holds in the namespace body (or file) that writes it",
         held("csharp/using-inside-a-namespace-block")),
        (f"{STD_CS} §14.5.1", "`global using` holds in every file of the compilation",
         held("csharp/global-using")),
        (f"{STD_CS} §14.5.4", "`using static` imports a type's static members and nested types",
         held("csharp/using-static")),
        (f"{STD_CS} §14.2", "a file-scoped namespace is the namespace of the whole file",
         held("csharp/file-scoped-namespace")),
        (f"{STD_CS} §7.8.1; {RS_CS} BindQualifiedName", "`N.T`, `T.Nested`: qualified names",
         held("csharp/qualified-nested-type")),
        (f"{STD_CS} §7.8.1", "a generic name is looked up by its number of type arguments",
         held("csharp/type-by-arity", "csharp/generic-namesake",
              "csharp/generic-use-is-no-plain-type")),
        (f"{STD_CS} §7.8.1",
         "two `using` namespaces giving the same name is an ambiguity error",
         not_followed("an error is no binding; a graph of code that compiles never has one")),
        (f"{STD_CS} §14.4 extern alias", "`extern alias` names an assembly's root namespace",
         not_followed("assemblies are not read: an aliased name is external")),
        # §12.8.4 Simple names
        (f"{STD_CS} §12.8.4", "a local or a parameter hides a member of its name",
         held("csharp/local-hides-member")),
        (f"{STD_CS} §12.8.4", "a member of the class or a base by its bare name (implicit `this`)",
         held("csharp/implicit-this", "csharp/inherited", "csharp/inherited-from-a-grandparent")),
        (f"{STD_CS} §12.8.4", "a local function is a name of its block",
         held("csharp/local-function")),
        (f"{STD_CS} §12.8.4", "a primary constructor's parameter is a name in the type's body",
         held("csharp/primary-constructor")),
        (f"{STD_CS} §12.8.4",
         "a name declared later in the block is an error before its declaration",
         not_followed("an error is no binding")),
        # §12.5 Member lookup
        (f"{STD_CS} §12.5; {RS_CS} LookupMembersInClass",
         "a member declared in a derived type hides the base's of its name",
         held("csharp/derived-member-hides-base")),
        (f"{STD_CS} §12.5", "a member of a base is a member of the type",
         held("csharp/base", "csharp/inherited")),
        (f"{STD_CS} §12.5", "an interface's members are members of a value typed by it",
         held("csharp/interface-typed")),
        (f"{STD_CS} §15.2.7", "a partial type is every declaration of it",
         held("csharp/partial", "csharp/partial-type-constructor")),
        (f"{STD_CS} §12.5", "an inaccessible member is no candidate (`private` in another type)",
         not_followed("accessibility is not modelled; code that compiles uses only what it "
                      "can reach, so it seldom decides")),
        # §12.8.7 Member access
        (f"{STD_CS} §12.8.7.2; {RS_CS} BindLeftOfPotentialColorColorMemberAccess",
         "`Color Color`: a member named like its type is the value or the type, by what follows",
         held("csharp/color-color")),
        (f"{STD_CS} §12.8.7", "a static member through the type's name",
         held("csharp/static-via-class", "csharp/static-property")),
        (f"{STD_CS} §12.8.7", "an enum member through its enum",
         held("csharp/enum-member", "csharp/enum-member-value")),
        (f"{STD_CS} §12.8.8", "`a?.b` is a member of `a`", held("csharp/null-conditional")),
        (f"{STD_CS} §12.8.14", "`this.m` and `base.m`", held("csharp/this", "csharp/base")),
        # §12.8.10.3 Extension method invocations
        (f"{STD_CS} §12.8.10.3", "an instance method comes before an extension of the name",
         held("csharp/instance-method-before-extension")),
        (f"{STD_CS} §12.8.10.3; {RS_CS} LookupAllExtensions",
         "extensions are searched namespace by namespace, closest first",
         held("csharp/closest-extension-namespace")),
        (f"{STD_CS} §12.8.10.3",
         "an extension is in sight only from a namespace around or one a `using` imports",
         held("csharp/extension-needs-its-namespace", "csharp/extension",
              "csharp/block-namespace-sees-outer-extension", "csharp/using-block-namespace")),
        (f"{STD_CS} §12.8.10.3", "an extension of an interface applies to its implementations",
         held("csharp/extension-on-interface")),
        (f"{STD_CS} §12.8.10.3", "an extension's type arguments are inferred or written",
         held("csharp/extension-by-type-arguments")),
        # §12.6.4 Overload resolution
        (f"{STD_CS} §12.6.4.2", "a candidate takes the call's number of arguments",
         held("csharp/overload-arity")),
        (f"{STD_CS} §12.6.4.2", "a named argument picks the overload with a parameter of that name",
         held("csharp/overload-by-argument-name")),
        (f"{STD_CS} §12.6.4.3; {RS_CS} BetterFunctionMember",
         "a better conversion wins: an exact type over `object`",
         held("csharp/overload-exact-type")),
        (f"{STD_CS} §12.6.4.3", "a candidate needing no default arguments wins",
         held("csharp/overload-without-defaults")),
        (f"{STD_CS} §12.6.4.3", "a `params` candidate in normal form wins over its expanded form",
         held("csharp/overload-normal-before-params")),
        (f"{STD_CS} §12.6.4.5",
         "better conversion between numeric types (`int` → `long` over `double`)",
         not_followed("literal kinds only: the walk does not know an expression's numeric type")),
        (f"{STD_CS} §12.6.4.3", "a non-generic candidate wins over a generic one alike",
         held("csharp/overload-non-generic-first")),
        # Types of values the walk follows (§12.8.x, §13.6.2)
        (f"{STD_CS} §13.6.2", "`var x = new T()`: x is a T", held("csharp/var-new")),
        (f"{STD_CS} §13.6.2", "a declared local's type", held("csharp/local-declared-type")),
        (f"{STD_CS} §12.8.17.2", "target-typed `new()` is a construction of the declared type",
         held("csharp/target-typed-new")),
        (f"{STD_CS} §13.9.5", "`foreach` gives the element type",
         held("csharp/foreach-typed", "csharp/foreach-var")),
        (f"{STD_CS} §12.12.12", "`is T x` and `as T` bind and convert",
         held("csharp/is-pattern", "csharp/as-cast", "csharp/is-as-catch")),
        (f"{STD_CS} §12.9.7", "a cast is a value of its type", held("csharp/cast")),
        (f"{STD_CS} §12.9.8", "`await` gives what a `Task<T>` holds", held("csharp/await-task")),
        (f"{STD_CS} §15.7", "a property's type, and the members its body uses",
         held("csharp/property-type", "csharp/property-body-is-the-property")),
        (f"{STD_CS} §12.8.4", "a method group passed as a value", held("csharp/method-group")),
        (f"{STD_CS} §15.11.2", "`: base(…)` / `: this(…)` call a constructor",
         held("csharp/constructor-initializer")),
        (f"{STD_CS} §15.16", "a record is a class", held("csharp/record")),
        (f"{STD_CS} §12.8.4", "`dynamic` binds at run time",
         not_followed("no static answer: a member of a `dynamic` value names nothing")),
        (f"{STD_CS} §12.20", "a lambda's parameter typed by the delegate it is converted to",
         not_followed("phase 3: inference from the target type")),
    ],
}


def _cases() -> dict[str, dict]:
    import conformance
    return {c["id"]: c for c in conformance.CASES}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--lang", default="")
    ap.add_argument("--markdown", action="store_true")
    args = ap.parse_args()
    cases = _cases()
    missing = [(lang, cid) for lang, rows in INVENTORY.items() for _, _, st in rows
               for cid in st.get("cases", []) if cid not in cases]
    if missing:
        for lang, cid in missing:
            print(f"{lang}: no case {cid} in benchmarks/conformance.py", file=sys.stderr)
        return 1
    for lang, rows in INVENTORY.items():
        if args.lang and lang != args.lang:
            continue
        mine = [c for c in cases.values() if c["lang"] == lang]
        sourced = sum(1 for c in mine if c["found"] == "source")
        kept = sum(1 for _, _, st in rows if "cases" in st)
        if args.markdown:
            print("| Source | Rule | Held by / why not |\n|---|---|---|")
            for where, rule, st in rows:
                what = ", ".join(f"`{c.split('/', 1)[1]}`" for c in st["cases"]) if "cases" in st \
                    else "not followed: " + st["reason"]
                print(f"| {where} | {rule} | {what} |")
            print()
        print(f"{lang}: {len(rows)} rules, {kept} held by a case, {len(rows) - kept} not followed; "
              f"{sourced} of {len(mine)} cases found in the language's own tools")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
