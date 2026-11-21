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
# ruff: noqa: E501
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
    "rust": [
        # src/names/scopes.md
        ("r[names.scopes.items.module]", "an item of a module is a name in the whole module",
         held("rust/use-path", "rust/nested-module-path")),
        ("r[names.scopes.items.statement]", "an item declared in a block is a name of the block",
         held("rust/item-in-a-block")),
        ("r[names.scopes.items.nested-modules]",
         "the items of a module around are no names of a nested module",
         held("rust/outer-module-items-not-in-scope")),
        ("r[names.scopes.associated-items.scope]",
         "associated items are named by a path from their type or trait, never bare",
         held("rust/associated-items-are-not-scoped", "rust/associated-function", "rust/self-type")),
        ("r[names.scopes.pattern-bindings.let]", "a `let` binding is a name from after it to the end of its block",
         held("rust/let-shadows-from-where-it-is", "rust/block-scope", "rust/shadowing")),
        ("r[names.scopes.pattern-bindings.parameter]", "a parameter is a name of the function's body",
         held("rust/reference-parameter", "rust/method-on-local")),
        ("r[names.scopes.pattern-bindings.closure]", "a closure's parameter is a name of the closure's body",
         held("rust/closure-body")),
        ("r[names.scopes.pattern-bindings.loop]", "a `for` binding is a name of the loop body",
         held("rust/vec-iteration")),
        ("r[names.scopes.pattern-bindings.let-chains]",
         "an `if let` / `while let` binding is a name of the conditions after it and the block",
         held("rust/if-let-some")),
        ("r[names.scopes.pattern-bindings.match-arm]", "a `match` arm's binding is a name of that arm",
         held("rust/match-arm-binding")),
        ("r[names.scopes.pattern-bindings.shadow]", "a binding shadows a function of its name",
         held("rust/untyped-let-hides-a-function", "rust/let-shadows-from-where-it-is")),
        ("r[names.scopes.generic-parameters.param-list]",
         "a generic parameter is a name in the item it is declared on",
         held("rust/generic-parameter-hides-a-type", "rust/generic-bound", "rust/generic-bound-where")),
        ("r[names.scopes.lifetimes]", "lifetimes and loop labels",
         not_followed("they name no definition of the graph")),
        ("r[names.scopes.prelude.layers]", "the preludes, which a module's own names shadow",
         held("rust/option-unwrap", "rust/question-mark", "rust/smart-pointer")),
        ("r[names.scopes.macro_rules]", "`macro_rules!` textual and path scopes",
         not_followed("macros are not expanded: a macro's arguments are read as expressions "
                      "(`rust/macro-arguments`), its definition names nothing")),
        ("r[names.scopes.derive]", "derive helper attributes",
         not_followed("attributes are macro input: a name in `#[derive(…)]` is no value read")),
        ("r[names.scopes.self.def-scope]", "`Self` in a struct, enum, trait or impl is the type",
         held("rust/self-type-in-a-struct", "rust/self-in-a-trait", "rust/result-of-self")),
        ("r[names.scopes.self.impl-scope]", "`Self(…)` / `Self { … }` constructs the implementing type",
         held("rust/self-type")),
        # src/names/namespaces.md
        ("r[names.namespaces.kinds]",
         "types, values and macros are separate namespaces: `mod log` beside `fn log`",
         held("rust/module-and-function-namespaces", "rust/tuple-struct-constructor", "rust/enum-variant")),
        ("r[names.namespaces.without.fields]", "a field is named only by a field expression on its type",
         held("rust/field-not-method", "rust/field-beside-a-method", "rust/field-type")),
        # src/names/name-resolution.md, src/items/use-declarations.md
        ("r[names.resolution.expansion.imports.shadowing]",
         "a `use` of an inner scope shadows one of an outer scope",
         held("rust/inner-use-shadows-outer")),
        ("r[items.use.glob.shadowing]", "an item or a named import shadows what a glob import brings",
         held("rust/glob-shadowed-by-an-item", "rust/named-import-shadows-a-glob")),
        ("r[items.use.forms-glob]", "`use m::*;` brings m's public names", held("rust/glob-import", "rust/use-super-glob")),
        ("r[items.use.forms-as]", "`use m::f as g;` binds g", held("rust/use-alias")),
        ("r[items.use.self]", "`use a::{self, f};` binds the module and the name", held("rust/use-self-in-braces")),
        ("r[items.use.visibility]", "`pub use` re-exports", held("rust/re-export")),
        ("r[items.use.path.edition2018]",
         "since 2018 a `use` path starts at a name in scope, or a crate",
         held("rust/uniform-paths", "rust/another-crates-name")),
        ("r[names.resolution.expansion.imports.ambiguity]", "ambiguous glob imports are errors",
         not_followed("an error is no binding")),
        ("r[paths.qualifiers]", "`crate::`, `self::`, `super::` paths",
         held("rust/qualified-path", "rust/super", "rust/self-path", "rust/crate-root-named-by-the-manifest")),
        ("r[paths.qualified]", "`<T as Trait>::f` qualified paths",
         not_followed("not read yet: the trait named in the path is not taken as the receiver's")),
        # src/expressions/method-call-expr.md, rustc_hir_typeck/src/method/probe.rs
        ("r[expr.method.candidate-search]", "a method of the type's own `impl` before a trait's",
         held("rust/inherent-method-first")),
        ("r[expr.method.candidate-search]",
         "a value of a type parameter has the methods of the traits that bound it",
         held("rust/generic-bound", "rust/generic-bound-where", "rust/dyn-trait")),
        ("r[expr.method.candidate-receivers]",
         "the receiver is dereferenced: `&T`, `Box<T>`, `Arc<T>` find T's methods",
         held("rust/smart-pointer", "rust/reference-parameter")),
        ("r[expr.method.candidate-search]", "a trait's method is found only when the trait is in scope",
         not_followed("code that compiles has the trait in scope; the engine takes it as given")),
        ("r[expr.method.candidate-search]", "a trait's default method is a method of every implementor",
         held("rust/trait-default-method", "rust/trait-method")),
        ("r[items.impl.inherent]", "an `impl` may be written in another module than its type",
         held("rust/impl-in-another-file", "rust/impl-of-the-files-own-type")),
        ("r[expr.field]", "`x.0` of a tuple struct", held("rust/tuple-struct-field", "rust/tuple-field-types")),
        ("r[type.closure] / typeck", "a closure's parameter typed by the iterator or option it is called on",
         not_followed("phase 3: inference from the call it is passed to")),
    ],
    "go": [
        # doc/go_spec.html: Blocks, Declarations and scope
        ("spec Declarations_and_scope", "a top-level constant, type, variable or function is a name of the package block, in every file",
         held("go/package-scope", "go/test-file")),
        ("spec Declarations_and_scope", "a method is no name of the package block",
         held("go/method-is-no-package-name")),
        ("spec Declarations_and_scope", "an imported package's name is a name of the file block of the importing file",
         held("go/imported-package", "go/import-alias")),
        ("spec Declarations_and_scope", "a receiver, parameter or result is a name of the function body",
         held("go/parameter-hides-a-function", "go/value-receiver")),
        ("spec Declarations_and_scope", "a type parameter of a function is a name from its name to the end of its body",
         held("go/type-parameter-hides-a-type", "go/generic-constraint")),
        ("spec Declarations_and_scope", "a variable or constant of a function is a name from the end of its declaration to the end of the block",
         held("go/short-var-scope-starts-after", "go/local-hides-function", "go/short-var-hides-package-name")),
        ("spec Declarations_and_scope", "a type of a function is a name from its identifier to the end of the block",
         held("go/local-type-hides-a-type")),
        ("spec Declarations_and_scope", "a name redeclared in an inner block denotes the inner one only there",
         held("go/block-redeclaration-ends-with-the-block")),
        ("spec Blocks", "`if`, `for`, `switch` and each clause of a `switch` or `select` are blocks",
         held("go/switch-clause-is-a-block")),
        ("spec Label_scopes", "labels are function-wide and not block scoped",
         not_followed("labels name no definition of the graph")),
        ("spec Predeclared_identifiers", "the universe block: `len`, `append`, `error`, …",
         not_followed("predeclared names are no project definitions; a project's own `len` shadows them as any package name does")),
        ("spec Exported_identifiers", "only an exported name of another package is reachable",
         not_followed("code that compiles uses only exported names: the engine does not check")),
        # Import declarations
        ("spec Import_declarations", "an import's name defaults to the imported package's clause",
         held("go/package-name-from-the-clause")),
        ("spec Import_declarations", "`import . \"p\"` declares p's exported names in the file block",
         held("go/dot-import")),
        ("spec Import_declarations", "`import _ \"p\"` binds nothing", not_followed("it binds no name to resolve")),
        # Selectors, method sets (go/types lookup.go)
        ("spec Selectors; go/types LookupFieldOrMethod", "`x.f` is the field or method at the shallowest depth",
         held("go/shallowest-depth", "go/shallower-embedding-wins")),
        ("spec Selectors", "an embedded type's fields and methods are promoted",
         held("go/embedded-struct", "go/promoted-field", "go/embedded-interface")),
        ("spec Selectors", "a method of an interface value is the interface's method",
         held("go/interface-method")),
        ("spec Calls", "a `*T` method is called on an addressable `T`", held("go/pointer-method-on-a-value")),
        ("spec Method_expressions", "`T.M` is a use of the method", held("go/method-expression")),
        ("spec Method_values", "`x.M` without a call is a use of the method",
         held("go/method-value", "go/method-value-argument")),
        ("spec Selectors", "two fields or methods at the same shallowest depth make the selector illegal",
         not_followed("an error is no binding")),
        # Types of values the walk follows
        ("spec Short_variable_declarations", "`x, err := f()` gives each name its result's type",
         held("go/multiple-results", "go/every-result-type")),
        ("spec For_range", "a `range` value is the element type", held("go/range-slice")),
        ("spec Index_expressions", "an element of a slice or map", held("go/slice-index", "go/map-value")),
        ("spec Type_assertions", "`x.(T)` is a T; a type switch's `case T` names T",
         held("go/type-assertion", "go/type-assertion-and-switch")),
        ("spec Type_declarations", "a defined type's methods, and what it is defined as",
         held("go/named-slice-type", "go/defined-type-names-its-type")),
        ("spec Function_literals", "a function literal sees the package and names its signature's types",
         held("go/closure", "go/function-literal-signature")),
        ("go/types infer.go", "a function argument's type inferred for a generic call",
         not_followed("phase 3: type inference is not modelled")),
    ],
    "typescript": [
        # tsc/internal/binder/nameresolver.go NameResolver.Resolve, scope by scope
        ("nameresolver.go Resolve: locals", "a block's `let` / `const` / class is a name of the block", held("typescript/block-scope", "typescript/block-scope-branches", "typescript/block-scope-file-level")),
        ("nameresolver.go Resolve: function-like", "a function's type parameters are names of its whole declaration", held("typescript/type-parameter-hides", "typescript/type-parameter-bound")),
        ("nameresolver.go Resolve: function-like", "a parameter is a name of the body and the parameter list", held("typescript/parameter-called", "javascript/parameter-hides-import")),
        ("nameresolver.go Resolve: function-like", "an expression in a parameter list does not see the body's declarations",
         not_followed("tsc rejects the program that would tell (TS2373); nothing to bind differently")),
        ("nameresolver.go Resolve: KindSourceFile / KindModuleDeclaration", "a module's exports are names inside it; an export specifier is not",
         held("typescript/namespace-member-inside", "typescript/namespace")),
        ("nameresolver.go Resolve: KindEnumDeclaration", "an enum's members are names inside it", held("typescript/enum-member", "typescript/enum-member-value")),
        ("nameresolver.go Resolve: KindClassDeclaration", "a class's members are no names inside its methods; only its type parameters are",
         held("typescript/class-members-are-not-names", "javascript/this-method")),
        ("nameresolver.go Resolve: KindExpressionWithTypeArguments", "a class's heritage sees its own type parameters", held("typescript/generic-argument")),
        ("nameresolver.go Resolve: KindDecorator", "a decorator is resolved at the class declaration", held("typescript/decorator-resolved-at-the-class")),
        ("nameresolver.go Resolve: KindCatchClause", "a `catch` binding is the catch block's", held("typescript/catch-binding-is-the-blocks")),
        ("nameresolver.go Resolve: KindInferType", "an `infer T` is a name of the true branch of the conditional type", held("typescript/type-alias-body")),
        ("nameresolver.go Resolve: meaning", "a lookup for a type passes over values of the name, and for a value over types",
         held("typescript/type-meaning-skips-values", "typescript/type-and-value", "typescript/typeof-type")),
        ("nameresolver.go Resolve: lookup / merging", "declarations of one name merge: interfaces, a function or class with a namespace",
         held("typescript/declaration-merging", "typescript/function-namespace-merge")),
        ("nameresolver.go Resolve: KindExportSpecifier", "`export { x as y }` binds y for importers only", held("javascript/re-export", "typescript/default-reexport")),
        ("nameresolver.go Resolve: globals", "the global scope and `declare global` augmentations",
         not_followed("library globals name no project definition; a project's own `declare global` is not read")),
        ("checker getPropertyOfType", "a member of a type, through what it extends and implements",
         held("javascript/inherited-method", "typescript/interface-extends", "typescript/implements", "typescript/abstract-base")),
        ("checker getTypeOfSymbol", "a declared type before the value's", held("typescript/local-typed-by-declaration", "typescript/typed-field", "typescript/readonly-field")),
        ("checker getReturnTypeOfSignature", "a call's declared result", held("typescript/return-type", "typescript/await-promise")),
        ("checker narrowing", "`as`, `!`, `instanceof` and optional chaining keep or set the type",
         held("typescript/as-cast", "typescript/non-null", "typescript/as-and-instanceof", "typescript/optional-chaining")),
        ("checker overload resolution", "a call of an overloaded function picks the signature its arguments fit",
         not_followed("TS overloads are signatures of one implementation: the edge is to the function either way")),
        ("checker contextual typing", "a callback's parameter typed by the parameter it is passed for",
         not_followed("phase 3: inference from the call it is passed to")),
        # module resolution: tsc/internal/module
        ("module resolver", "a relative import with or without its extension, a directory's `index`", held("typescript/barrel", "javascript/named-import")),
        ("module resolver", "a directory's `package.json` `types` / `main`", held("typescript/package-json-types-entry")),
        ("module resolver tryLoadModuleUsingPathsIfEligible", "`paths` in `tsconfig.json`", held("typescript/tsconfig-paths")),
        ("module resolver", "`import x = require()`, `export =`", held("typescript/import-equals-require", "typescript/export-assignment")),
        ("module resolver", "`node_modules` packages", not_followed("a library's sources are not read: its names are external")),
        # ECMAScript
        ("ECMAScript FunctionDeclarationInstantiation", "a function declaration is hoisted to its scope", held("javascript/hoisting")),
        ("ECMAScript VarDeclaredNames", "a `var` is the function's, whatever block it is written in", held("javascript/var-is-the-functions")),
        ("ECMAScript InstantiateOrdinaryFunctionExpression", "a function expression's name is its own body's", held("javascript/named-function-expression")),
        ("ECMAScript CommonJS (Node)", "`require`, `module.exports`, `exports.x`",
         held("javascript/commonjs-destructure", "javascript/commonjs-default", "javascript/commonjs-member")),
        ("ECMAScript prototypes", "`F.prototype.m = function` is a method of `new F()`", held("javascript/prototype-method")),
    ],
    "java": [
        # JLS 6.3 / 6.4 and javac Resolve.java findVar / findFun / findType
        ("JLS 6.3; Resolve findVar", "a local or parameter is a name from its declaration to the end of its block", held("java/local-hides-field", "java/for-each", "java/try-with-resources", "java/instanceof-pattern")),
        ("JLS 15.12.1; Resolve findFun", "a bare method name is a method of the innermost class that has one of the name, inherited ones counted",
         held("java/implicit-this", "java/inherited", "java/inherited-before-enclosing", "java/outer-method")),
        ("JLS 6.4.1; Resolve findVar", "a bare field name: the class's own and inherited fields before the class around",
         held("java/inherited-field-before-enclosing", "java/this-field")),
        ("JLS 9.3, 8.3; Resolve findField", "a constant of an implemented interface is inherited", held("java/inherited-interface-constant", "java/interface-constant-type")),
        ("JLS 6.4.1; Resolve findFun", "a method of the class shadows a static import of the name", held("java/own-method-before-static-import", "java/static-import")),
        ("JLS 6.4.1; Resolve findType", "type variables, then member types (own and inherited) of each class around, then the file's",
         held("java/method-type-parameter-hides-a-class", "java/member-type-before-import", "java/inherited-member-type", "java/nested-static-class")),
        ("JLS 6.3", "a local class is a name of the rest of its block", held("java/local-class")),
        ("JLS 6.4.1; Resolve findType", "a single-type import shadows a type of the package", held("java/single-type-import-before-package", "java/single-type-import")),
        ("JLS 7.5.2; Resolve findType", "a type of the package shadows an on-demand import", held("java/package-before-on-demand-import", "java/same-package", "java/on-demand-import")),
        ("JLS 7.4.3", "a class of another package is not in reach without an import or a qualified name", held("java/other-package-not-in-reach", "java/fully-qualified")),
        ("JLS 6.4.2 obscuring", "a variable obscures a type, a type obscures a package of its name",
         not_followed("code that compiles is written around it; no case tells a variable from a type there yet")),
        ("JLS 15.8.4", "`Outer.this` is the object of the class named", held("java/qualified-this")),
        ("JLS 15.11.2", "`super.m()`, `super(…)`, `this(…)`", held("java/super", "java/explicit-constructor-call")),
        # 15.12.2 overload resolution: Resolve findMethod phases, mostSpecific
        ("JLS 15.12.2.1", "a candidate takes the call's number of arguments", held("java/overload-arity")),
        ("JLS 15.12.2.2; Resolve findMethod", "phase 1: applicable without boxing", held("java/no-boxing-before-boxing")),
        ("JLS 15.12.2.4", "phase 3: variable arity last", held("java/fixed-arity-before-varargs")),
        ("JLS 15.12.2.5; Resolve mostSpecific", "the most specific of the applicable: a subtype, a narrower primitive",
         held("java/most-specific-reference-type", "java/overload-by-type")),
        ("JLS 15.12.2.5", "a floating literal's `float` / `double` overload",
         not_followed("the facts tell an integer literal from another number, not `1.0f` from `1.0`: such ties are INFERRED")),
        ("JLS 15.12.2.7", "a generic method's type arguments inferred from the call", not_followed("phase 3: inference")),
        # member lookup and types the walk follows
        ("JLS 8.4.8", "an interface's default method is inherited", held("java/interface-default", "java/interface-typed")),
        ("JLS 15.12.3", "a static method through its class or interface", held("java/static-via-class", "java/interface-static")),
        ("JLS 14.4.1", "`var` is the type of its initializer", held("java/var")),
        ("JLS 15.16", "a cast is a value of its type", held("java/cast", "java/instanceof-cast-catch")),
        ("JLS 15.13", "a method reference is a use of the method", held("java/method-reference")),
        ("JLS 15.27", "a lambda's body sees the method around", held("java/lambda-body")),
        ("JLS 15.9.5", "an anonymous class sees the class around", held("java/anonymous-class")),
        ("JLS 8.9", "an enum constant has the enum's methods and is a use of it", held("java/enum-method", "java/enum-constant-value")),
        ("JLS 8.10", "a record is a class with its components", held("java/record")),
        ("JLS 4.5", "a member of a parameterized type with its arguments", held("java/generic-argument", "java/list-element", "java/chained-result")),
        ("JLS 6.6", "access control (`private`, package-private)",
         not_followed("code that compiles reaches only what it may: the engine does not check")),
    ],
    "kotlin": [
        # Kotlin/kotlin-spec docs/src/md/kotlin.core/overload-resolution.md and scoping.md
        ("scoping.md 'Linked scopes'", "a declaration of an inner scope shadows an outer one", held("kotlin/function-beside-parameter", "kotlin/local-typed-by-value")),
        ("overload-resolution.md 'Call without an explicit receiver' 1", "local functions first", held("kotlin/local-function-before-member")),
        ("overload-resolution.md 'Call without an explicit receiver' 2", "then members of the implicit receivers, innermost first", held("kotlin/member-before-top-level", "kotlin/inherited", "kotlin/receiver-lambda", "kotlin/with")),
        ("overload-resolution.md 'Call without an explicit receiver' 3", "then top-level: imported by name, same package, star-imported, default imports",
         held("kotlin/explicit-import-before-package", "kotlin/package-before-star-import", "kotlin/same-package", "kotlin/import")),
        ("overload-resolution.md 'Call with an explicit receiver' 1", "members of the receiver's type before any extension", held("kotlin/member-wins", "kotlin/member")),
        ("overload-resolution.md 'Call with an explicit receiver' 2–5", "extensions: local, imported by name, package, star-imported",
         held("kotlin/local-extension-first", "kotlin/imported-extension-before-package", "kotlin/extension")),
        ("overload-resolution.md 'Call with an explicit receiver' (member extensions of implicit receivers)", "an extension declared as a member of a class in scope",
         not_followed("a member extension is read as a member of its class only")),
        ("overload-resolution.md 'Call with an explicit type receiver'", "`T.f()`: static members, then the companion object's", held("kotlin/companion", "kotlin/object")),
        ("overload-resolution.md 'Call with an explicit super-form receiver'", "`super.f()`, `super<A>.f()`", held("kotlin/super", "kotlin/super-of-a-named-supertype")),
        ("overload-resolution.md 'Infix function call'", "`a foo b` is `a.foo(b)` of an `infix fun`", held("kotlin/infix-call")),
        ("overload-resolution.md 'Operator call'; operators.md", "`a + b`, `a[i]` are the operator functions of a's type", held("kotlin/operator-call", "kotlin/index-operator")),
        ("overload-resolution.md 'Operator call'", "`+=`, `in`, `..`, comparison and `invoke` conventions, delegated properties",
         not_followed("only the arithmetic and indexing conventions are read yet")),
        ("overload-resolution.md 'Callables and invoke convention'", "a property of a function type called is the property", held("kotlin/property-invoke")),
        ("overload-resolution.md 'Call with named parameters'", "a named argument is a parameter of the callee", held("kotlin/named-argument-label")),
        ("overload-resolution.md 'Choosing the most specific candidate'", "the candidate with the more specific parameter types", held("kotlin/most-specific-type")),
        ("overload-resolution.md 'Algorithm of MSC selection'", "a candidate without `vararg` expansion or defaults wins a tie", held("kotlin/non-vararg-before-vararg")),
        ("overload-resolution.md 'Using lambda return type to refine function applicability'", "overloads told apart by a lambda's result",
         not_followed("phase 3: the lambda's result type is not inferred")),
        ("overload-resolution.md 'Resolving callable references'", "`::f` is a use of f", held("kotlin/function-reference")),
        ("overload-resolution.md 'Resolving property access'", "a property through its receiver, a constructor `val`", held("kotlin/constructor-property", "kotlin/constructor-property-read", "kotlin/property-type")),
        ("type-inference.md / cdfa.md", "smart casts after `is`, `as`, safe calls", held("kotlin/is-and-as", "kotlin/when-is", "kotlin/cast", "kotlin/safe-call")),
        ("declarations.md 'Type alias'", "a typealias names its type", held("kotlin/typealias", "kotlin/typealias-body")),
        ("declarations.md 'Enum class declaration'", "an enum entry has the enum's members", held("kotlin/enum-entry")),
        ("builtins / standard library", "scope functions: `let`, `apply`, `also`, `with`, `run`", held("kotlin/let", "kotlin/apply-also", "kotlin/it")),
        ("declarations.md 'Visibility'", "`private`, `internal`, `protected`", not_followed("code that compiles reaches only what it may: the engine does not check")),
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
