# The name-resolution rules, language by language

What a name means is not a property of a repository: each language defines
it, in a finite list of rules — where a name is in scope, what an import
binds, where a member is looked up, which overload is called. The native
engine's resolver was written the other way round, from the edges real
repositories showed missing, and every new repository showed some more
([native-engine.md](native-engine.md#a-second-set-of-repositories)). This
page is the list itself: one rule a line, with the place the language
defines it, a few lines of source that exercise it and whether the engine
follows it.

Back to the [README](../README.md) · [Native engine](native-engine.md) ·
[Polyglot audit](benchmark-polyglot.md)

`benchmarks/conformance.py` holds the cases and reruns them; a case is a
small project, the edge the rule says is there and, where the rule is about
one name hiding another, the edge that must not be:

```bash
python benchmarks/conformance.py                 # every case
python benchmarks/conformance.py --only rust     # one language
python benchmarks/conformance.py --failed        # what is left
python benchmarks/conformance.py --baseline benchmarks/results-conformance.json   # fixed / broken since
python benchmarks/conformance.py --markdown      # the tables below
```

Measured 2026-11-22 with graphlore-engine 0.5.76: **666 of 666 rules hold**
(356 of the first 406 with 0.5.45). Ten came from the Go type checker and
rust-analyzer run on whole repositories; fifteen from the Kotlin and Swift
compilers' tests (type checks, casts, `catch`, aliases); thirty-nine from two
compilers' own test suites ([native-engine.md](native-engine.md#the-compiler-as-the-judge)):
TypeScript's (block scopes, type parameters and their bounds, parameter
properties, enum members, the types of aliases and signatures) and Scala's
(packages, named arguments, extensions, patterns).

## What this list is, and is not

- **It is the scoping rules**: scopes and shadowing, imports and their
  forms, member lookup through bases, mix-ins and extensions, overloads by
  the number of arguments, and the types the source writes (a parameter, a
  field, a local assigned a construction, a declared result, one type
  argument). These are decidable from the text of a project, and a rule the
  engine does not follow here is a defect with a name.
- **It is not type inference.** What Kotlin, Swift, Rust or TypeScript infer
  for an expression is the compiler's work and is not a line of this page;
  where the source writes no type the engine says `INFERRED` or nothing.
- **It is not complete.** A language's definition has more rules than its
  table has rows (C++ templates and overloads by conversion, Scala
  implicits, Python metaclasses, Ruby `method_missing` are not here). A rule missing from the
  table is not known to hold: add the case, then look.
- **The places named** (a specification's section, by its title) were
  written from memory of those documents, not checked against a copy.

No case fails with engine 0.5.46. A case that fails is the engine's open
defect of this kind; with 0.5.45, 50 did, and they were few rules written in
many languages. What 0.5.46 closed, by rule:

- **A type written in an expression.** A cast used at once: Java and C#
  `((Repo) o).find()`, C# / TypeScript / Kotlin `x as Repo`, Swift
  `x as! Repo`, Go `x.(*Repo)`.
- **A null-safe call.** C# `r?.Find()`; a Kotlin parameter typed `Repo?`
  (the `?.` was read, the type was not).
- **A binding that takes an optional apart.** Rust `if let Some(r) = opt`
  and Swift `if let r = opt` / `guard let r = opt` on a parameter; Scala
  `case r: Repo =>`; Java's resource variable in `try (Repo r = open())`;
  the `it` of Kotlin `r.let { … }`.
- **A member of an enum's value.** `Color.RED.label()` in Java, Kotlin,
  Swift and PHP.
- **A type alias.** Kotlin and Swift `typealias Store = Repo`, a Ruby
  constant assigned a class, C++ `namespace u = util;`.
- **A variable outside a function, typed by its value.** Go
  `var store = &Repo{}`, a Kotlin top-level `val store = Repo()`, a Ruby
  instance variable (`@repo.find`); and a Scala `val` in a method, which
  was no local at all.
- **A function named, not called.** Java `this::helper`, PHP
  `[$this, 'handle']`, Kotlin `::handle` (it was a `calls` edge), Scala's
  `r.size` on a method that takes nothing (it was `references`).
- **Paths through the module itself.** Rust `self::f()`, and `super::f()`
  in an inline module; a C# `global using`; `m.Repo` as a type through a
  TypeScript `import * as m`; the names bound by a JavaScript
  `await import('./m.js')`.
- **Objects.** A method of a JavaScript object literal called through the
  constant holding it, a method assigned to `F.prototype` on a local
  assigned `new F()`, a TypeScript getter read as a value, a TypeScript
  optional parameter `r?: T`.
- **Shadowing.** A second Rust `let` of a name is the name from there on;
  a C parameter hides a function of its name; a use written before a local
  is declared is no use of it.
- **`super`.** Ruby's bare `super` and `super(x)`.
- **The rest.** A C++ range-`for` over `std::vector<T>` with `auto&`, a
  C++ nested class written `Outer::Inner&`, PHP `C::class` and
  `new static()`, and a generic Java class the project names `Box`.

Eight cases were added with 0.5.47, each a rule found by reading the edges
only graphify had on a repository: a pytest fixture as the type of a test's
parameter, `super(…)` / `this(…)` and `: base(…)` / `: this(…)` as constructor
calls, a C# static class beside its generic namesake, a `using` of a
namespace written as a block, `$` as part of a TypeScript name, a TypeScript
name that is a type and a value, and a C++ class written with an export macro
(`class API Env {`).

Each change was measured on the 45 audit repositories as well, by reading
the edges it added and removed at their source lines; the page on the
[native engine](native-engine.md#the-rules-themselves) has the numbers.
Every case has been compiled by its own language
(`benchmarks/verify_cases.py`: 666 of 666 accepted). The Versions column is
the oldest version of the language whose compiler accepts the case, where the
compiler can be asked (`verify_cases.py --min-version`: Java, Kotlin, Scala,
C#, Swift, C, C++); `all` is the oldest version asked, `–` a language with no
such flag. Each language's header says how its cases were found and how many
cite the place the rule was read in the language's own tools
([rule-sources.md](rule-sources.md)).

Passing every case says the engine follows the rules written here, not
every rule of each language: see the limits above.

Four cases failed for the cases' own sake and were corrected: a decorator
is a `references` edge, not `uses`; a C local declared as a function
pointer is a definition of its own that the call reaches; generic cases
named `Box` / `value` / `get` ran into the wrapper names, which is now a
case of its own; and an object literal's method named `get` is a library's
name to the engine, so the case names it `fetchAll`.

## The rules

### python (52 of 52)

The Python Language Reference, 'Naming and binding' (4.2) and 'The import system'.

Found by: the specification 36, a compiler 7, comparison 0, the grammar 1, the language's own tools 8. With the place it was read in the language's own tools: 14.

| Rule | Case | Versions | Engine |
|---|---|---|---|
| a module-level function is in scope in every function of the module | `module-function` | – | yes |
| a nested function sees the names of the function around it | `enclosing-scope` | – | yes |
| a name bound in a function is local to it and hides the module's | `local-hides-global` | – | yes |
| `from m import f` binds f to m's definition | `from-import` | – | yes |
| `from m import f as g` binds g | `import-alias` | – | yes |
| `import pkg.m` then `pkg.m.f()` is m's f | `module-attribute` | – | yes |
| `from .m import f` is relative to the package of the importer | `relative-import` | – | yes |
| `from m import *` binds m's public names | `star-import` | – | yes |
| `self.m()` is the class's method | `self-method` | – | yes |
| an attribute not on the class is looked up on its bases (the MRO) | `inherited-method` | – | yes |
| `super().m()` is the next class's m | `super` | – | yes |
| calling a class constructs it | `class-call` | – | yes |
| a parameter annotated with a class has that class's methods | `annotated-parameter` | – | yes |
| `self.repo = Repo()` in __init__ types self.repo | `attribute-assigned-in-init` | – | yes |
| `Cls.create()` is the class's own method | `classmethod-via-class` | – | yes |
| a local assigned a construction has that class's methods | `local-typed-by-value` | – | yes |
| a decorator is a use of the name it is written with | `decorator` | – | yes |
| a call's annotated result gives the methods of what it returns | `return-annotation` | – | yes |
| an attribute annotated in the class body has that type | `class-attribute-annotation` | – | yes |
| a property read is what its getter returns | `property` | – | yes |
| `with f() as x` binds x to what f returns | `with-as` | – | yes |
| a loop variable over `list[T]` is a T | `for-over-annotated-list` | – | yes |
| a comprehension's variable over `list[T]` is a T | `comprehension` | – | yes |
| `import pkg.m as mm` binds mm to the module | `module-alias` | – | yes |
| `from pkg import m` binds the submodule m | `submodule-import` | – | yes |
| a name the package's __init__ imports is a name of the package | `package-reexport` | – | yes |
| an import inside a function binds the name in that function | `function-local-import` | – | yes |
| pytest hands a test the result of the fixture its parameter names (conftest.py) | `pytest-fixture` | – | yes |
| `except E as e:` and `except (A, B):` name the types caught | `except-clause` | – | yes |
| of two bases with the method, the first listed wins | `mro-first-base` | – | yes |
| a method only the second base has is found there | `mro-second-base` | – | yes |
| a class nested in a class is reached through it | `nested-class` | – | yes |
| a static method is found on the instance too | `staticmethod-via-self` | – | yes |
| a call in a class body is the class's | `class-body-call` | – | yes |
| a function passed by name is a use of it | `function-as-value` | – | yes |
| a base imported under another name is still the base | `inherited-from-aliased-import` | – | yes |
| a function named as a value is a use of it | `function-named-as-value` | – | yes |
| a member read through a typed parameter is the member of its type | `member-of-a-parameter` | – | yes |
| a name bound in a class body is no name of its methods: a bare call is the module's | `class-scope-not-in-methods` | – | yes |
| a name bound anywhere in a function is local to all of it, before the binding too | `bound-later-is-local` | – | yes |
| `global x` makes an assignment in a function write the module's variable | `global-statement` | – | yes |
| `from m import *` binds only the names of m's `__all__` | `star-import-respects-all` | – | yes |
| `from m import *` without `__all__` binds no name that starts with `_` | `star-import-skips-underscore` | – | yes |
| a lambda's parameter is a name of the lambda: it hides a function of its name there | `lambda-parameter-hides` | – | yes |
| a type parameter (`def f[T](x: T)`) hides a class of its name in the function | `pep695-type-parameter` | 3.12– | yes |
| `self.x` of a class attribute is the class's attribute | `class-attribute-through-self` | – | yes |
| a bare name a module neither defines nor imports is a builtin or nothing: never a definition of another module | `name-of-another-module-needs-import` | – | yes |
| `State.CLOSED` of an `enum.Enum` subclass is a member of the enum: a reference to the enum | `enum-member-names-its-enum` | – | yes |
| a parameter's default value is read where the function is defined: a module variable written there is read by the method | `default-value-reads-the-name` | – | yes |
| a module variable assigned a type (`Number = Union[int, float]`) written as an annotation is a reference to the alias | `type-alias-in-annotation` | – | yes |
| a member of a field annotated `A \| B \| None` is the one type of the union that has it; never the class's own method of the name | `union-annotated-field-member` | – | yes |
| `self.request = request` in `__init__` with `request: Request` types the field, also in a class with a library base | `field-typed-by-its-parameter` | – | yes |

### javascript (31 of 31)

ECMAScript Language Specification, 'Executable Code and Execution Contexts' (scopes), 'Modules'.

Found by: the specification 29, a compiler 0, comparison 0, the grammar 0, the language's own tools 2. With the place it was read in the language's own tools: 2.

| Rule | Case | Versions | Engine |
|---|---|---|---|
| a function declaration is in scope in its whole enclosing scope | `hoisting` | – | yes |
| a `const` of a block hides an outer function of its name | `block-const-hides` | – | yes |
| an inner function sees the functions of the one around it | `closure` | – | yes |
| `import { f } from './m.js'` binds m's export | `named-import` | – | yes |
| `import { f as g }` binds g | `import-alias` | – | yes |
| a default import is the module's default export | `default-import` | – | yes |
| `import * as m` then `m.f()` | `namespace-import` | – | yes |
| `export { f } from './m.js'` passes m's f on | `re-export` | – | yes |
| `const { f } = require('./m')` binds what m exports as f | `commonjs-destructure` | – | yes |
| `this.m()` in a class method is the class's m | `this-method` | – | yes |
| a method not on the class is looked up on what it extends | `inherited-method` | – | yes |
| `super.m()` is the base class's m | `super` | – | yes |
| a local assigned `new C()` has C's methods | `new-then-method` | – | yes |
| a function assigned to `F.prototype` is a method of F's instances | `prototype-method` | – | yes |
| a `const` assigned an arrow function is a function of its scope | `arrow-const` | – | yes |
| a `const` assigned a function expression is a function | `function-expression` | – | yes |
| a method of an object literal is called through the object | `object-literal-method` | – | yes |
| `this` in an object literal's method is the object | `object-literal-this` | – | yes |
| `module.exports = f` is what `require` gives | `commonjs-default` | – | yes |
| `exports.f = …` is a member of what `require` gives | `commonjs-member` | – | yes |
| `C.m()` is C's static method | `static-method` | – | yes |
| a default-exported class is what a default import names | `default-export-class` | – | yes |
| an exported `const` arrow function is importable by its name | `exported-arrow` | – | yes |
| a function passed by name is a use of it | `function-as-value` | – | yes |
| a parameter hides an import of its name | `parameter-hides-import` | – | yes |
| `var X = (function () { function X() {} return X })()` names the function written in it | `module-pattern` | – | yes |
| `await import('./m.js')` is the module | `dynamic-import` | – | yes |
| an arrow function in a class field sees the class's `this` | `class-field-arrow` | – | yes |
| a local assigned `new C()` keeps its type in a nested callback | `for-of-new` | – | yes |
| a `var` declared in a block is a name of the whole function | `var-is-the-functions` | – | yes |
| the name of a function expression is a name of its own body only | `named-function-expression` | – | yes |

### typescript (58 of 58)

TypeScript Handbook, 'Modules', 'Classes', 'Generics' (types are the compiler's, tsc).

Found by: the specification 30, a compiler 19, comparison 0, the grammar 2, the language's own tools 7. With the place it was read in the language's own tools: 7.

| Rule | Case | Versions | Engine |
|---|---|---|---|
| a parameter's declared type gives its members | `typed-parameter` | – | yes |
| a call through an interface type is the interface's method | `interface-typed` | – | yes |
| `constructor(private repo: Repo)` declares a typed field | `parameter-property` | – | yes |
| a field's declared type gives its members | `typed-field` | – | yes |
| `Page<Item>`'s `head: T` is an Item | `generic-argument` | – | yes |
| a call's declared return type gives the members of its result | `return-type` | – | yes |
| `import type { T }` is a reference to T | `import-type` | – | yes |
| an import through `index.ts` reaches the file that defines the name | `barrel` | – | yes |
| `a?.m()` is a's m | `optional-chaining` | – | yes |
| an optional parameter `r?: T` has type T | `optional-parameter` | – | yes |
| an enum member is a member of its enum | `enum-member` | – | yes |
| `Cls.make()` is the class's static method | `static-method` | – | yes |
| `import * as m` then `m.Repo` as a type | `namespace-import-type` | – | yes |
| `$` is part of a name (`$Base`), not a sigil | `dollar-name` | – | yes |
| a name declared as a type and as a value is the value where a value is used | `type-and-value` | – | yes |
| an element of `T[]` is a T | `array-element` | – | yes |
| a `for … of` variable over `T[]` is a T | `for-of` | – | yes |
| `await` of a `Promise<T>` is a T | `await-promise` | – | yes |
| a getter's declared type gives the members of what is read | `getter` | – | yes |
| `x as T` is a T | `as-cast` | – | yes |
| `x!` keeps x's type | `non-null` | – | yes |
| a `readonly` field's declared type gives its members | `readonly-field` | – | yes |
| an interface that extends another is its subtype | `interface-extends` | – | yes |
| a class that implements an interface names it | `implements` | – | yes |
| a call through an abstract class is the abstract class's method | `abstract-base` | – | yes |
| a type alias is a type of its own, referenced where it is written | `type-alias` | – | yes |
| `N.f()` is a function a namespace exports | `namespace` | – | yes |
| `export { default as X } from './x'` passes the default on | `default-reexport` | – | yes |
| `const r: T = …` is a T whatever it is assigned | `local-typed-by-declaration` | – | yes |
| a value of `Map<K, V>` is a V | `map-value` | – | yes |
| a `const` of a block is a name of that block: beside it the outer one stands | `block-scope` | – | yes |
| the same name declared in two branches is two variables | `block-scope-branches` | – | yes |
| a type parameter hides a type of its name declared outside | `type-parameter-hides` | – | yes |
| a value typed by a type parameter has the members of its constraint | `type-parameter-bound` | – | yes |
| what a namespace declares is a name inside it, not beside it | `namespace-member-inside` | – | yes |
| a constructor parameter with a modifier declares a property of the class | `parameter-property-read` | – | yes |
| a call of a parameter is no call of a function of its name outside | `parameter-called` | – | yes |
| an enum member named as a value is a use of its enum | `enum-member-value` | – | yes |
| the types a type alias is written with are uses of them | `type-alias-body` | – | yes |
| the types of an interface's property, call, construct and index signatures are uses of them | `interface-signatures` | – | yes |
| `typeof ns.Class` in a type position is a use of the class | `typeof-type` | – | yes |
| `import x = require('./m')` binds the module under that name | `import-equals-require` | – | yes |
| `export = E` is what `import x = require()` of the module names | `export-assignment` | – | yes |
| `import A = N.M` names what the path names | `import-alias` | – | yes |
| a function merged with a namespace of its name has what the namespace declares | `function-namespace-merge` | – | yes |
| a function named as a value is a use of it | `function-as-value` | – | yes |
| a `let` in a block at the top of a file is the block's, beside a variable of the file | `block-scope-file-level` | – | yes |
| `x as T` and `x instanceof T` are uses of T | `as-and-instanceof` | – | yes |
| every name a closure's parameter pattern binds is the closure's | `destructured-closure-parameter` | – | yes |
| the parameters of a function type name their types, not their names | `function-type-parameter-types` | – | yes |
| a member read or written through a local or a parameter is the member of the object it holds | `member-of-a-local` | – | yes |
| a class's members are no names inside its methods: a bare call is the module's function | `class-members-are-not-names` | – | yes |
| a decorator is resolved where the class is declared, not among the method's parameters | `decorator-resolved-at-the-class` | – | yes |
| a `catch` binding is a name of the catch block only | `catch-binding-is-the-blocks` | – | yes |
| two `interface` declarations of one name are one interface: a member of either | `declaration-merging` | – | yes |
| an import through a `paths` mapping of `tsconfig.json` is the file it maps to | `tsconfig-paths` | – | yes |
| an import of a directory is the file its `package.json` names | `package-json-types-entry` | – | yes |
| where a type is written, a value of the name does not hide the type around it | `type-meaning-skips-values` | – | yes |

### go (299 of 317)

The Go Programming Language Specification, 'Declarations and scope', 'Selectors', 'Method sets'.

Found by: the specification 23, a compiler 13, comparison 2, the grammar 5, the language's own tools 15, a sweep of them 259. With the place it was read in the language's own tools: 279.

| Rule | Case | Versions | Engine |
|---|---|---|---|
| a package-level function is in scope in every file of the package | `package-scope` | – | yes |
| `pkg.F()` is F of the imported package | `imported-package` | – | yes |
| an import can be renamed | `import-alias` | – | yes |
| a method call on a typed local is the type's method | `method-on-local` | – | yes |
| the methods of an embedded type are promoted | `embedded-struct` | – | yes |
| a call through an interface type is the interface's method | `interface-method` | – | yes |
| the declared result of a call gives the methods of the result | `result-type` | – | yes |
| a local declared in a function hides a package function of its name | `local-hides-function` | – | yes |
| a method of a struct field's type | `field-method` | – | yes |
| `x, err := f()` types x by f's first result | `multiple-results` | – | yes |
| a `range` value over `[]*T` is a *T | `range-slice` | – | yes |
| an element of `[]*T` is a *T | `slice-index` | – | yes |
| a value of `map[K]*T` is a *T | `map-value` | – | yes |
| a package-level variable's value gives its type | `package-variable` | – | yes |
| `x.(*T)` is a *T | `type-assertion` | – | yes |
| `go f()` and `defer g()` are calls | `go-and-defer` | – | yes |
| a function passed by name is a use of it | `function-as-value` | – | yes |
| a method declared on a named slice type | `named-slice-type` | – | yes |
| the methods of an interface embedded in an interface are its own | `embedded-interface` | – | yes |
| a method call on a value of an imported package's type | `imported-type` | – | yes |
| a test file of the package sees the package's functions | `test-file` | – | yes |
| a method with a value receiver is called on a value | `value-receiver` | – | yes |
| a function literal sees the functions of the package | `closure` | – | yes |
| a value of a type parameter has the methods of its constraint | `generic-constraint` | – | yes |
| a function named as a value is a use of it | `function-named-as-value` | – | yes |
| `x.(T)` and a type switch's `case T` are uses of T | `type-assertion-and-switch` | – | yes |
| a name declared with `:=` is a local, whatever it is assigned | `short-var-hides-package-name` | – | yes |
| `var a, b T` declares every name | `multi-name-var` | – | yes |
| a field of a struct, named or not, is no name of the package | `struct-field-is-no-package-name` | – | yes |
| a local assigned a field is of the field's type | `member-read-types-a-local` | – | yes |
| a method called on a value the source types as a library's is the library's | `library-receiver-is-no-guess` | – | yes |
| each result of a function names its type | `every-result-type` | – | yes |
| a method value handed to a call is a function handed over | `method-value-argument` | – | yes |
| what a defined type is defined as is a use of it | `defined-type-names-its-type` | – | yes |
| the type a constant is declared with is a use of it | `constant-type` | – | yes |
| a function literal's parameters and result name their types | `function-literal-signature` | – | yes |
| a slice or map literal names the types it is made of | `literal-element-types` | – | yes |
| the type handed to `make` is a use of it | `make-type-argument` | – | yes |
| `import . "p"` declares p's exported names in the file block: they are used bare | `dot-import` | – | yes |
| an import is named by the imported package's clause, not by its directory | `package-name-from-the-clause` | – | yes |
| a selector is the field or method at the shallowest depth: a type's own method before an embedded type's | `shallowest-depth` | – | yes |
| of two embedded types, the method found at the lesser depth | `shallower-embedding-wins` | – | yes |
| a field of an embedded struct is a field of the outer one | `promoted-field` | – | yes |
| a name declared in a block hides a package name only inside that block | `block-redeclaration-ends-with-the-block` | – | yes |
| each clause of a `switch` is a block of its own: a name of one clause is none of the next | `switch-clause-is-a-block` | – | yes |
| a name declared by `:=` is in scope after the declaration: its own value names the outer one | `short-var-scope-starts-after` | – | yes |
| a parameter is a name of the function body: it hides a package function of its name | `parameter-hides-a-function` | – | yes |
| a type declared in a function hides a package type of its name inside the function | `local-type-hides-a-type` | – | yes |
| a method is no name of the package block: a bare call in a method is the package function | `method-is-no-package-name` | – | yes |
| a type parameter of a function hides a package type of its name in the function | `type-parameter-hides-a-type` | – | yes |
| `T.M` is a method expression: a use of T's method | `method-expression` | – | yes |
| `x.M` without a call is a method value: a use of the method | `method-value` | – | yes |
| a method of `*T` is called on an addressable `T` | `pointer-method-on-a-value` | – | yes |
| an element of a named slice type (`type byName []*Command`) is of the element type: `c[i].Name()` is Command's | `element-of-a-named-slice-type` | – | yes |
| an element of a field declared as a slice of a named type is of that type: `w.values[i].inverse()` | `element-of-a-slice-field` | – | yes |
| an embedded field is named by its type: `f.basicWriter.flush()` is basicWriter's method | `embedded-field-by-its-type-name` | – | yes |
| a call's result typed by a named type answers its method: `Chain(m).Handler(h)` is Middlewares.Handler | `named-result-type-method` | – | yes |
| the parameter and result types of a struct field's function type are references of the struct | `function-typed-field-types` | – | yes |
| a name declared in an `if` statement's init is a name of that statement only, else-branch included | `if-init-is-scoped-to-the-if` | – | yes |
| a name declared in a `for` statement's init is a name of that statement only | `for-init-is-scoped-to-the-for` | – | yes |
| each clause of a `select` is a block of its own: a name its case declares is none after the statement | `select-clause-is-a-block` | – | yes |
| the name of an import is a name of its file only: two files may bind one name to two packages | `import-name-is-a-name-of-its-file` | – | yes |
| a receiver is a name of the method body: it hides a package function of its name | `receiver-hides-a-function` | – | yes |
| a named result is a name of the function body: it hides a package function of its name | `result-variable-hides-a-function` | – | yes |
| a type parameter declared by a receiver hides a package type of its name in the method | `receiver-type-parameter-hides-a-type` | 1.18– | yes |
| a type parameter of a type is a name from the type's name to the end of its declaration: it hides a package type there | `type-parameter-of-a-type-hides-a-type` | 1.18– | yes |
| a variable declared with `var` in a function is in scope after its VarSpec: its own value names the outer one | `var-spec-scope-starts-after` | – | yes |
| a name used before a local of its name is declared means the outer one | `use-before-a-local-declaration-names-the-outer` | – | yes |
| a label conflicts with no identifier: a call of a function named like a label is the function | `label-is-no-identifier` | – | yes |
| a function of the package named like a predeclared one hides it: `max(a, b)` is the package function | `predeclared-name-redeclared-in-the-package` | – | yes |
| a dot-import declares only the exported names of the package: an unexported name of its own is the file's own | `dot-import-takes-only-exported-names` | – | yes |
| an unexported name of another package is a different identifier: a selector finds the one of its own package at the same depth | `unexported-names-of-two-packages-differ` | – | **no**: no Run -calls-> L.helper has Run -calls-> util/u.go:A.helper |
| a constant declared without a type takes the type of its expression | `constant-takes-the-type-of-its-value` | – | yes |
| a constant whose expression list is omitted repeats the previous list and its type: it has the type of the first | `implicitly-repeated-constant-has-the-type` | – | yes |
| a variable declared with a type has that type, whatever its value: a method called on it is the declared type's | `declared-type-wins-over-the-value` | – | yes |
| `var a, b = f()` gives each name its result's type | `var-multiple-results` | – | yes |
| `var t, ok = x.(T)` gives t the type T | `var-comma-ok-assertion` | – | yes |
| a short variable declaration redeclaring a variable of the block assigns it: the variable keeps its declared type | `short-var-redeclaration-keeps-the-variable` | – | yes |
| a generic function written with type arguments, `Map[int](x)`, is a call of that function | `generic-function-instantiation` | 1.18– | yes |
| a method is bound to its receiver base type wherever in the package it is declared | `method-in-another-file` | – | yes |
| a method declared for a generic type `S[T]` is a method of S on every instantiation | `method-of-a-generic-type` | 1.18– | yes |
| a method declared on an alias's name is a method of the aliased defined type | `method-on-an-alias-receiver` | 1.9– | yes |
| `r, n := open()` in the function body redeclares the parameter r: it keeps the type Reader, so `r.Read()` is Reader's method | `short-var-redeclares-a-parameter` | – | yes |
| in `func use(conf conf)` and `func (conf conf) Other()` the type is the package type conf and `conf.Run()` in the body is conf's method, not the package function Run | `parameter-named-like-its-type` | – | yes |
| `err.Error()` on a parameter of type error is the universe interface's method: no project type's Error method is meant | `predeclared-error-method` | – | yes |
| in `func (b Box[U]) Go(u U)` U is the receiver's own name for T: `u.Run()` is the method of T's constraint Runner | `receiver-type-parameter-renamed` | 1.18– | yes |
| `type A = B; type B = *T; func (a A) Go()` binds Go to T: `t.Go()` on a *T is T's method, not another type's Go | `receiver-alias-chain-and-pointer` | – | **no**: no Use -calls-> T.Go |
| in `v, ok := m[k]` and `w, ok := <-ch` the first name has the value / element type | `comma-ok-map-and-receive` | – | yes |
| `type S struct{ conf conf }`: the field's type is the package type conf, and `s.conf.Run()` is conf's method | `field-named-like-its-type` | – | yes |
| a variable or parameter of a function hides an imported package's name inside the function: the selector is the value's method | `local-hides-an-imported-package` | – | yes |
| a variable declared in a function hides a package type of its name: a selector on it is a method of the value's type | `local-hides-a-type-name` | – | yes |
| a parameter of a function literal hides a package function of its name inside the literal | `function-literal-parameter-hides-a-function` | – | yes |
| with as many values as names each name takes the type of the value at its position | `short-var-pairs-values-by-position` | – | yes |
| an embedded `Base[int]` promotes the methods the receiver `Base[T]` declared | `embedded-generic-instantiation` | – | yes |
| an alias declaration is a use of the type it names | `alias-names-its-type` | – | yes |
| a method declared on an alias is a method of the aliased type: it is found on a value written with either name | `alias-method-call` | – | yes |
| an alias of an instantiated generic type has that instance's methods | `alias-of-instance` | – | yes |
| a generic alias, instantiated, is the type it stands for: its methods are that type's | `generic-alias` | 1.24– | yes |
| a defined type does not inherit the methods of the type it is defined from: its own method of the name is the one called | `defined-type-does-not-inherit` | – | yes |
| the method set of an interface type is unchanged by a type definition from it: its methods are the interface's | `defined-interface-keeps-methods` | – | yes |
| a method called on a value of an instantiated generic type is the generic type's method | `instance-local-method` | – | yes |
| a method of a generic type does not see the type declaration's type parameters: a name its receiver does not declare is the package's | `receiver-redeclares-type-parameters` | – | yes |
| a type parameter of a type declaration hides a package type of its name in the declaration | `type-declaration-parameter-hides` | – | yes |
| a type parameter declared by a method's receiver hides a package type of its name in the method only: the method's other uses of the name are the package's | `receiver-type-parameter-hides` | – | yes |
| the type parameters of one list are all in scope in each constraint of it, including those declared after | `constraint-forward-reference` | – | yes |
| a value of a type parameter constrained by an instantiated generic interface has that interface's methods | `instantiated-constraint` | – | yes |
| the interface a type parameter is constrained by, and the types of the union such an interface lists, are uses of those names | `constraint-names-are-uses` | – | yes |
| a value of a type parameter has the methods of the interfaces its constraint embeds | `constraint-embeds-interface` | – | yes |
| a generic type written with type arguments is a use of the generic type and of each argument | `generic-type-instance-is-a-use` | – | yes |
| instantiating substitutes the type arguments: a field or result typed by the type parameter is the argument's type | `instance-field-and-result-substituted` | – | yes |
| a call of a generic function with an explicit type argument gives the argument's type as result | `explicit-instantiation-result` | – | **no**: no Use -calls-> Item.Run |
| a generic function of another package called with explicit type arguments is that function, not the same-named one of the importing package | `explicit-function-instantiation` | – | yes |
| `Box[T].M` is a method expression of the generic type's method | `method-expression-on-instance` | – | yes |
| a generic function handed over uninstantiated is a use of that function | `generic-function-as-argument` | – | yes |
| a generic type of another package, instantiated, gives that type's methods: not those of the importing package's same-named type | `imported-generic-type-instance` | – | yes |
| an embedded instance of a generic type is a field named by the type's name; its methods are promoted | `embedded-instance` | – | yes |
| an array length written as a name is a use of the constant of that name in scope: a constant of the function hides the package's | `array-length-names-a-constant` | – | yes |
| a field of a struct is a field of a type defined from it | `defined-type-keeps-the-fields` | – | yes |
| a type defined from a struct with an embedded field has the embedded type's promoted methods | `defined-type-keeps-the-embedding` | – | yes |
| a value of an alias of pkg.T answers T's methods | `alias-of-an-imported-type` | – | yes |
| a value of a type parameter constrained by pkg.I has I's methods | `constraint-of-an-imported-package` | – | yes |
| ranging over a value of S ~[]E gives E, whose constraint's methods are called on it | `core-type-element-of-a-type-parameter` | – | **no**: no Total -calls-> Shape.Area |
| the type argument written in `Conv[Celsius](1)` is a use of Celsius, the call is of Conv | `explicit-type-argument-of-a-call` | – | yes |
| `f := Conv[int]` names Conv: a use of it, and `f(1)` is a call of Conv | `instantiated-function-value` | – | yes |
| `func (b Box[_]) Size()` is Box's method, found on a Box[int] value | `blank-receiver-type-parameter` | – | yes |
| `l.Next().Len()` where Next returns `*List[T]` is List.Len | `generic-method-result-chain` | – | yes |
| a struct embedding `Alias` (type Alias = Base) has a field Alias, and Base's methods | `embedded-alias-field` | – | yes |
| `s.Conn.Close()` for an embedded `*util.Conn` is Conn's method | `embedded-imported-type-field` | – | yes |
| a type parameter of a function hides a package function of its name: `Size(v)` in the function is a conversion, no call | `type-parameter-hides-a-function` | 1.18– | yes |
| a field typed by the type parameter of a generic type, read in a method of it, has the methods of the parameter's constraint | `field-of-a-type-parameter-type` | 1.18– | yes |
| a recursive generic type's field typed `*Node[T]` has the type's methods | `recursive-generic-type` | 1.18– | yes |
| an alias of an alias names the final type, whatever the order of the declarations: its methods are the type's | `alias-chain` | – | yes |
| a range over an alias of `[]*T`, and a value of an alias of `*T`, have T's methods | `alias-of-composite-type` | – | yes |
| a field read on a value of a defined pointer type is the struct's field | `defined-pointer-type-field` | – | yes |
| a call on a conversion to a type parameter constrained by `interface{ *T; Set() }` is the constraint's method | `pointer-constraint-method` | 1.18– | **no**: no Make -calls-> Setter.Set |
| the methods of an interface embedding an instantiated generic interface are the generic interface's | `embedded-generic-interface` | 1.18– | yes |
| `b.Get` without a call, b of type `Box[int]`, is a use of Box's method | `method-value-of-an-instance` | 1.18– | yes |
| `m[k].Get()` with m of type `map[string]Box[int]` calls the generic type's method | `map-value-of-an-instance` | 1.18– | yes |
| a generic type's name and each of its type arguments are uses | `type-arguments-are-uses` | 1.18– | yes |
| `b.Get()` on a `*Box[Item]` is an Item, so `.Run()` is Item.Run | `generic-result-is-the-argument` | 1.18– | yes |
| a value of an alias type (also an alias of an alias) has the methods of the type the alias names | `alias-has-the-methods-of-its-type` | – | yes |
| `HandlerFunc(plain).Serve(1)` is the method of the converted-to type, and `plain` is a use | `conversion-gives-its-type` | – | **no**: no Use -references-> plain |
| an element of `[3]*Cell`, of `[2][2]*Cell` and of a pointer to an array is a *Cell | `array-element-type` | – | yes |
| `grid[0][1]` of `[][]*Cell` is a *Cell | `slice-of-slices` | – | yes |
| a field of a local declared with an unnamed struct type is of its declared type | `anonymous-struct-field` | – | **no**: no Use -calls-> Repo.Find |
| `*util.Tool` embedded is the field Tool, and `o.Tool.Run()` is util's Tool.Run | `embedded-pointer-and-qualified-field` | – | yes |
| a method of an interface embedded in a struct is promoted: `c.Get()` is the interface's Get | `embedded-interface-in-a-struct` | – | yes |
| `Box[int]` embedded is the field Box: `h.Get()` is Box.Get | `embedded-generic-type` | 1.18– | yes |
| a local assigned `new(Repo)` or `*r` is of type Repo | `new-and-dereference` | – | yes |
| `open().Find()` and `make2(1)().Find()` on parameters of function type are the result type's method | `function-typed-value-result` | – | yes |
| `repos ...*Repo` is a []*Repo: `repos[0]` is a *Repo | `variadic-parameter-is-a-slice` | – | yes |
| a parameter or receiver named like a type hides it in the body only, not in the signature | `parameter-hides-a-type` | – | **no**: no Use -references-> Config |
| `util.Reader` embedded gives util's Reader.Read, not a local Reader's | `qualified-embedded-interface` | – | yes |
| a value of type parameter T with constraint `Number` (embedding Labeled and `~int \| ~int64`) has Labeled.Label | `constraint-embeds-an-interface` | 1.18– | yes |
| `for k := range m` over `map[Key]int` makes k a Key | `range-map-key` | – | yes |
| `<-jobs` and `range jobs` over a `<-chan *Job` give a *Job | `channel-element-type` | – | yes |
| `type Source Reader` (Reader an interface): `s.Read()` is Reader.Read | `defined-interface-keeps-the-interface-methods` | – | yes |
| an element of `S ~[]*Repo` is a *Repo | `core-type-of-a-type-parameter` | 1.18– | **no**: no First -calls-> Repo.Find |
| `type Derived Base` does not inherit Base's methods: `d.Name()` is Derived.Name | `defined-type-has-only-its-own-methods` | – | yes |
| a struct type defined from another struct type has its embedded fields, so a promoted method is called on it: `c.Close()` on a `Copy` is Base's | `defined-struct-keeps-promoted-methods` | – | yes |
| an interface embedding an instantiated generic interface has its methods: `r.Get()` is the generic interface's | `embedded-instantiated-interface` | – | yes |
| a pointer method called on `&x` or `&T{}` is T's | `address-of-a-value-is-a-pointer` | – | yes |
| the names inside map, channel and array type literals of a field are references | `type-literal-component-types` | – | yes |
| a method whose receiver is written with an alias is a method of the aliased type: `t.Bump()` on a T is that method | `method-declared-on-an-alias` | – | yes |
| an embedded alias is a field named by the alias; the aliased type's methods are promoted: `f.Alias.Close()` and `f.Close()` are Base's | `embedded-alias` | – | yes |
| an embedded `*T` field promotes the methods of T and *T | `embedded-pointer-field-promotes` | – | yes |
| the value variable of `for _, v := range m` is the map's element type, not its key type | `range-map-value` | – | yes |
| a method declared on a defined function type is called on a value of that type | `method-of-a-defined-function-type` | – | yes |
| `b.V` and `b.Vs[0]` of a `Box[Item]` are Items | `generic-field-has-the-type-argument` | 1.18– | yes |
| `c.DB.Pool.Close()` goes through a field of anonymous struct type to Pool's method | `nested-anonymous-struct-field` | – | **no**: no Run -calls-> Pool.Close |
| a defined type of a defined slice type is indexed to the element type | `underlying-through-a-chain` | – | yes |
| `util.Map[int, string](…)` and `Map[int, int](…)` are calls of the function written, qualified or not | `instantiated-generic-function` | – | yes |
| `(helper)()` is a call of helper and `((r)).Find()` a call on r | `parenthesized-operand` | – | yes |
| `util.Config` is the imported package's type; a bare `Config` is the importing package's | `qualified-type-is-the-packages` | – | yes |
| `apply(util.Handler)` hands over the imported package's Handler, not the importing package's | `qualified-function-value` | – | yes |
| `util.Limit` and `util.Max` are the imported package's variable and constant, not the importing package's of the same names | `qualified-variable-and-constant` | – | yes |
| `Config{helper: 1}` names the field helper, not the function helper in scope | `struct-literal-key-is-a-field` | – | yes |
| in `map[int]string{Key: "k"}` Key is the constant; in `Config{Key: 2}` it is the field | `literal-key-field-or-value` | – | yes |
| a local assigned `&Repo{}` or `new(Repo)`, or the literal called directly, answers Repo's methods | `address-of-literal-types-the-value` | – | yes |
| in `[]*Pair{{Key: 1}}` the key is Pair's field, not the function Key in scope | `elided-element-type-keys` | – | yes |
| `helper()` in a literal is the local helper of the surrounding function, not the package function | `closure-sees-the-enclosing-local` | – | yes |
| `helper()` in a literal written before `helper := 2` is the package function | `closure-sees-only-earlier-locals` | – | yes |
| in `func(helper func() int) int { return helper() }` helper is the parameter | `closure-parameter-hides-a-function` | – | yes |
| a call inside `go func() { helper() }()` is a call of helper made by Run | `literal-invoked-in-place` | – | yes |
| `repos()[0].Find()` is Repo's method: the call's result is a slice of *Repo | `index-of-a-call-result` | – | yes |
| through `*T0` and `T1` embedded in T2, p.M0(), p.M1(), p.x and p.y are T0's and T1's | `embedded-pointer-is-promoted` | – | yes |
| `s.Log()` on a struct embedding Logger is Logger's method | `struct-embeds-interface` | – | yes |
| `o.Name()` on a struct with a func-typed field Name that embeds a type with method Name is the field | `field-at-a-shallower-depth-hides-a-method` | – | yes |
| `util.Sin()` where util is a parameter is the parameter type's method, not the package's function | `local-hides-the-package-name` | – | yes |
| `type Copy Base`: c.ID is Base's field; `type Wrapped Wrapper` (Wrapper embeds Base): w.Show() is Base's method | `defined-type-keeps-fields-and-promotions` | – | yes |
| `type Alias = Repo`: a.Find() is Repo's method | `alias-has-the-methods` | – | yes |
| V embeds util.T (unexported method run) and A (method run): `v.run()` is A's | `unexported-names-of-another-package` | – | **no**: no Run -calls-> A.run has Run -calls-> T.run |
| `Counter.Value(c)`, `(Counter).Value(c)` and `(*Counter).Inc(&c)` are calls of the methods | `method-expression-called` | – | yes |
| `(*Counter).Inc` and `(*Counter).Get` (Get has a value receiver) are uses of the methods | `pointer-method-expression` | – | yes |
| `Wrapper.Value` with Counter embedded in Wrapper is Counter.Value | `method-expression-promoted` | – | yes |
| `Reader.Read` is Reader's method, not another interface's of the same name | `interface-method-expression` | – | yes |
| `(*util.Repo).Find` is util's method, not the importing package's Repo.Find | `imported-method-expression` | – | yes |
| `s.M` with *T embedded in S is T.M | `method-value-through-embedding` | – | yes |
| `r.Read` on an interface and `c.Inc` on an addressable Counter are uses of Reader.Read and Counter.Inc | `method-value-of-interface-and-pointer` | – | yes |
| `a[0]` of `[3]*Repo` is a *Repo | `array-element` | – | yes |
| `p[0]` of `*[3]*Repo` is a *Repo | `pointer-to-array-element` | – | yes |
| `grid[0][1]` of `[][]*Repo` is a *Repo | `index-of-index` | – | yes |
| `r, ok := m["a"]` types r as the map's *Repo | `map-comma-ok` | – | yes |
| `repos[1:][0]` and `a[:2][0]` are *Repo | `slice-expression-keeps-the-type` | – | yes |
| `n[1:].Count()` on `type Names []string` is Names.Count | `slice-of-a-named-slice-keeps-its-type` | – | yes |
| `r, ok := x.(*Repo)` types r as *Repo | `type-assertion-comma-ok` | – | yes |
| `x.(Finder).Find()` is Finder's method | `type-assertion-to-an-interface` | – | yes |
| in `case *Repo:` of `switch v := x.(type)`, v is a *Repo | `type-switch-binding` | – | yes |
| `Celsius(f).String()` is Celsius's method and a reference to the type, never a call of it | `conversion-types-the-value` | – | yes |
| `(*Repo)(m).Find()` on a *Mid is Repo's method | `parenthesized-pointer-conversion` | – | yes |
| `(*pp).Norm()` with pp **Point and `(&v).Norm()` are Point's method | `indirection-and-address-of` | – | yes |
| `r := <-ch`, `v, ok := <-ch` and `(<-ch).Find()` on `chan *Repo` give a *Repo | `receive-element-type` | – | yes |
| `for r := range ch` and `case s := <-ch:` on `<-chan *Repo` give *Repo | `channel-range-and-select` | – | yes |
| in a type switch clause listing several types, the variable has the switched expression's type: v.M() is the interface's method | `type-switch-multi-type-clause` | – | yes |
| x.Get() on a value of Box[int] is the method of the generic type Box | `instantiated-generic-method` | – | yes |
| a named result is a typed local of the function body | `named-result-local` | – | yes |
| util.F() with import "example.com/app/tools" (package util) is tools' F, not the importing package's F; an alias names the same package | `package-name-is-not-directory` | – | yes |
| x := B{} inside a function literal hides the surrounding function's x, typed A | `closure-local-shadows-outer-local` | – | yes |
| in `var v, ok = m[k]` v is the map's element type: `v.Find()` is Repo's, never Other's | `var-declaration-comma-ok-map` | – | yes |
| in `var v, ok = x.(*T)` v is a *T: `v.Find()` is Repo's, never Other's | `var-declaration-comma-ok-assertion` | – | yes |
| in `var v, ok = <-ch` v is the channel's element type: `v.Find()` is Repo's, never Other's | `var-declaration-comma-ok-receive` | – | yes |
| `p[1:3]` of a `*[4]*Repo` is a []*Repo: `s[0].Find()` is Repo's, never Other's | `slice-of-a-pointer-to-an-array` | – | yes |
| `a[1:2:3]` of a `[]*Repo` is a []*Repo: `a[1:2:3][0].Find()` is Repo's, never Other's | `full-slice-expression` | – | yes |
| `p := &items[0]` is a *Repo: `p.Find()` is Repo's, never Other's | `address-of-an-element` | – | yes |
| in `[]*Point{{x: 1}}` the key `x` is a field of Point: it reads no package variable `x` | `elided-pointer-element-literal` | – | yes |
| in `map[Point]string{{x: 1}: "a"}` the inner `x` is a field of Point, the map's key type: it reads no package variable `x` | `elided-map-key-literal` | – | yes |
| `(Repo).Find(r)` is a use of Repo's method, never of Other's | `parenthesized-method-expression` | – | yes |
| `s.Find()` on a struct embedding `store.Base` is Base's method, never the other type's of the same name | `embedded-imported-type-promotes` | – | yes |
| `store.Default.Find()` is the method of the type of the imported variable `Default`, never another type's method of that name | `imported-variable-member` | – | yes |
| the call inside `var handler = func() int { return helper() }` is made by `handler` | `package-level-function-literal` | – | yes |
| a call whose function expression is a type is a conversion: a use of the type, not a call of a function of the same name | `conversion-is-a-use-of-the-type` | – | yes |
| `Ident[int](1)` calls the generic function Ident; a method Ident of a type is not that function | `instantiated-generic-call` | – | yes |
| a call that is the only argument of another call binds like any call: a package-qualified name is the imported package's, not a same-named function of this package | `call-as-the-only-argument` | – | yes |
| a package-level function named like a built-in (`min`) hides the universe's: the bare call is the project's function, not a method of that name | `package-function-hides-a-builtin` | – | yes |
| `new(T)` is a use of the type T | `new-type-argument` | – | yes |
| duplicate of conversion-is-a-use-of-the-type and instantiated-generic-call | `conversion-is-a-use-of-the-type-2` | – | yes |
| `new(pkg.T)` is a use of the imported package's type, not of a same-named type of this package | `new-type-argument-of-another-package` | – | yes |
| the result of a conversion `T(x)` is of type T: `Celsius(x).String()` and `HandlerFunc(f).Serve()` are those types' methods | `conversion-result-is-the-type` | – | yes |
| a local assigned `new(T)` is a *T: `p.Run()` is T's method, not another type's of the same name | `new-gives-a-pointer-local` | – | yes |
| a local made by `make([]*T, n)` or `append(s, x)` is a slice of *T: its elements' methods are T's | `make-and-append-keep-the-slice-type` | – | yes |
| `NewStack[int]()` gives a *Stack[int] and `var q Queue[string]` a Queue: `Push` is the method of the generic type of the value | `generic-type-instance-method` | – | yes |
| `s.run()` where run is a field of function type selects the field, not the package function run | `func-field-call-is-no-function` | – | yes |
| the type of a composite literal is the type written, so a method called on it is that type's method | `composite-literal-receiver` | – | yes |
| `err.Error()` on a value typed by the predeclared `error` is no call of a project type's Error method | `error-value-is-no-project-type` | – | yes |
| calls written inside a built-in's arguments are calls, bound like any other | `call-inside-builtin-argument` | – | yes |
| `who ...util.Person` names util's Person, a use of that type by the function | `variadic-parameter-type-use` | – | yes |
| a `var` declaration statement declares its name after its spec: `var helper = helper()` calls the package function, and a later use is the local | `var-declaration-scope-starts-after` | – | yes |
| a label named like a package function is no use of it and hides nothing: `helper:` with `break helper` and `continue helper`, and `helper()` is the function | `label-is-no-name-of-the-block` | – | yes |
| `h.out <- 1` reads the field `out` of Hub (the channel is an operand, the send changes the channel, not the field) | `send-evaluates-the-channel-field` | – | yes |
| `r.n++` and `r.n += 2` use the field n of r's type, written (and read, as `x = x op 1` does) | `incdec-reads-and-writes-the-field` | – | yes |
| `r.n = 1` writes the field n of r's type, `return r.n` reads it | `assignment-writes-the-field` | – | yes |
| `var f Finder; f = &Repo{}; f.Find()` is the interface's method: the variable's type is the declared one | `assignment-keeps-the-declared-type` | – | yes |
| a name declared by an `if`'s init statement hides a package function in the `if` and its `else`, and no longer after the statement | `if-init-var-is-seen-by-else` | – | yes |
| `if r := load(); r == nil {...} else if r.Find() > 0` types r by load's result | `else-if-sees-the-init-var-of-the-if` | – | yes |
| a name declared in a `switch` init statement hides a package function in the switch only | `switch-init-var-scope` | – | yes |
| `case limit:` reads the constant and `case helper():` calls the function | `expression-switch-case-is-a-use` | – | yes |
| in `switch v := f.(type)`, v is *Repo in `case *Repo` and the interface Finder in a clause with two types | `type-switch-binding-is-typed-by-its-clause` | – | yes |
| `switch helper := helper().(type)` calls the package function in the guard and names the variable in the clauses | `type-switch-binding-hides-a-function` | – | yes |
| `for helper := 0; ...` hides a package function in the loop and `helper()` after the loop is the function | `for-clause-var-scope` | – | yes |
| the variable of `for r := range ch` over `chan *Repo` is a *Repo | `range-over-channel` | – | yes |
| in `for k, v := range m` over `map[Key]*Val`, k is a Key and v a *Val | `range-over-map-key-and-value` | – | yes |
| the value of `for _, r := range a` with `a *[2]*Repo` is a *Repo | `range-over-array-pointer` | – | yes |
| `for r := range t.All` where All takes `yield func(*Repo) bool`: r is a *Repo, and t.All is a method value | `range-over-function-yield-type` | 1.23– | **no**: no Run -calls-> Repo.Find |
| `for _, current = range repos` writes the package variable current and keeps its type | `range-assignment-declares-nothing` | – | yes |
| `defer r.Find()`, `go r.Find()` and the calls in a `defer func() {...}()` are calls of the enclosing function | `defer-and-go-methods-and-closures` | – | yes |
| `case r := <-ch:` over `chan *Repo` declares r as a *Repo | `select-receive-binding` | – | yes |
| a result named like a package function hides it in the function body | `named-result-is-a-local` | – | yes |
| `func Run() (r *Repo) { r.Find() }`: r.Find is Repo's method | `named-result-is-typed` | – | yes |
| `goto helper` and a `helper:` label beside a package function `helper`: `helper()` is the function and the label is no reference to it | `goto-label-is-no-name-of-the-block` | – | yes |
| `for _, helper := range helper()` calls the package function, and the body's `helper` is the variable | `range-expression-is-outside-its-variables` | – | yes |
| `for i := range n` with `n Count` makes i a Count: i.Next() is Count's method | `range-over-integer-has-its-type` | 1.22– | yes |
| a plain assignment writes a variable without reading it; `x += y` reads and writes it; a local of that name is its own variable | `plain-assignment-is-no-read` | – | yes |
| the blank identifier binds no name: `var _ I = T{}`, `_, v := f()` and `_ = g` are uses of I, T, f and g | `blank-identifier-declares-nothing` | – | yes |
| assigning to an element `s.items[k] = v` writes the field `items` of s's type; reading `s.items[k]` reads it | `element-assignment-writes-the-field` | – | yes |
| `p.hits++` through an embedded struct is a read and a write of the embedded type's field `hits` | `promoted-field-incdec` | – | yes |
| a receive case written with `=` assigns an existing operand (a field is written) and declares nothing | `select-receive-assigns-a-field` | – | yes |
| in `case nil` and `default` clauses the type switch variable has the guard expression's type (the interface), not the type of another clause | `type-switch-nil-and-default-keep-the-guard-type` | – | yes |
| a name redeclared by an `else if` init statement is a new variable there, with its own type | `else-if-redeclares-a-name` | – | **no**: no Run -calls-> B.Name has Run -calls-> A.Name |
| the scope of a type declared in a function starts at its identifier: `next *node` inside the local `node` is the local type | `local-type-scope-starts-at-its-name` | – | yes |
| a range value over `[3]*T` or over a named `map[K]*T` type is a *T | `range-over-an-array-and-a-named-map` | – | yes |
| `range t.children` and `range t.Kids()` iterate the field's and the declared result's element type | `range-over-a-field-and-a-call-result` | – | yes |
| `v, ok := f()` in the scope that declared `v` assigns it (v keeps its declared interface type); in a nested block it declares a new `v` of the value's type | `short-var-redeclaration-keeps-the-type` | – | yes |
| in `case *Sq:` of a type switch the guard variable is a `*Sq`, so `v.Area()` is Sq.Area | `type-switch-pointer-clause-type` | – | yes |
| an import's name is declared in the file block: two files of one package may import different packages under the same name | `import-name-is-file-scoped` | – | yes |
| of two packages with the same clause name, `util.F()` is the F of the one whose path the file imports | `import-path-picks-the-package` | – | yes |
| a local declared with the name of an imported package hides it inside its block only | `local-hides-an-import` | – | yes |
| several `init` functions are declared in a package yet none is a name: `t.init()` is the method | `init-is-not-a-name` | – | yes |
| a package variable's initializer is a use made by the variable: its calls and reads of other package variables | `package-variable-initializer-uses` | – | yes |
| `import "unsafe"` names the built-in package: a project directory called unsafe is only reached by its full path | `unsafe-is-the-builtin-package` | – | yes |
| files of one directory whose package clauses differ form two packages: a_test.go (package app_test) calls its own helper, and reaches app only through its import | `external-test-package-is-another-package` | – | yes |
| a method is declared in any file of the package: s.Count() in a.go is the method written in b.go, not the package function Count | `method-declared-in-another-file` | – | yes |
| a package-level max hides the builtin max of Go 1.21: the call binds to the package function | `package-function-hides-a-new-builtin` | 1.21– | yes |
| a parameter named like an import hides the package only in the function body: its type in the signature still names the package | `go-parameter-named-like-import` | – | yes |
| a field named like an imported package is no name of the file block: h.util.Run() is the field type's method | `go-field-named-like-import` | – | yes |
| a local declared in a function hides a dot-imported name inside that function | `go-local-hides-dot-import` | – | yes |
| a local named init is called as a local: the package init functions are no names | `go-local-named-init` | – | yes |
| the calls inside a function literal assigned to a package-level variable belong to that variable | `go-func-literal-in-package-var` | – | yes |
| a method called on a value of a generic alias is the aliased generic type's method | `go-generic-alias-method` | 1.24– | yes |
| in package app_test, app.Run is the package under test and a bare Run is app_test's own function | `go-external-test-package-qualifier` | – | yes |
| a blank variable declares no name, yet the types written in its declaration are uses of the project's types | `blank-var-type-uses` | – | yes |
| a method called on a value of an alias type is a method of the aliased type | `alias-typed-value-call` | – | yes |
| a struct literal type that embeds a type promotes its methods: x.Hello() on a variable of it is Base.Hello, not another type's | `anonymous-struct-embeds-a-type` | – | **no**: no Run -calls-> Base.Hello |
| type ItemBox Box[Item]: the field v of an ItemBox is an Item, so b.v.Size() is Item.Size | `defined-from-an-instance-has-its-fields` | – | **no**: no Run -calls-> Item.Size |
| for it := range All() with All returning func(yield func(Item) bool): it is an Item, so it.Size() is Item.Size | `range-over-a-function-iterator` | 1.23– | **no**: no Run -calls-> Item.Size |
| the embedded field of `util.Base` is named Base: `Svc{Base: b}` and `s.Base.Touch()` are the type's, not the importing package's variable Base | `embedded-field-named-by-its-type` | – | yes |
| h.run() on a field of function type reads the field Hook.run; a method run of another type is not called | `function-typed-field-called` | – | yes |
| a literal assigned with := does not see the variable it initializes: its own name inside the body is the outer (package) one | `short-var-literal-sees-outer-name` | – | **no**: no Walk -calls-> step |
| `Box[int].Get` is a use of Box.Get, not of Other.Get | `method-expression-on-an-instance` | – | yes |
| an import name is a name of its file block: the same name in two files of a package means two packages | `go-import-name-is-per-file` | – | yes |
| a call of a method promoted from an interface embedded in a struct is the interface's method | `go-embedded-interface-in-struct` | – | yes |
| a parameter hides an imported package's name in the function body only | `go-parameter-hides-an-import-name` | – | yes |
| a call through a package variable of function type is a use of the variable; a multi-value call forwarded as arguments is a call | `go-call-of-function-typed-variable` | – | **no**: no Run -reads-> a.go:handler |
| a function literal run by a defer statement sees the enclosing function's named result as the local it is: a method call on it is its type's method, not that of a package variable of the same name | `go-defer-closure-sees-named-result` | – | yes |
| a parameter of a function literal hides a local of the enclosing function with the same name and has its own declared type | `go-func-literal-param-hides-outer-local` | – | yes |
| two directories whose files share a package clause name are two packages: a bare helper() is the one of its own directory's package block | `same-clause-name-in-two-directories-is-two-packages` | – | yes |
| a dot-imported function's declared result gives the local its type: r.Find() is the imported type's method | `dot-imported-function-result-types-a-local` | – | yes |
| a package type named any hides the universe's any: x.Run() on a parameter of type any is the package interface's method | `package-type-hides-the-predeclared-any` | 1.18– | yes |

### java (62 of 62)

The Java Language Specification, 6.4 'Shadowing and Obscuring', 15.12 'Method Invocation Expressions'.

Found by: the specification 41, a compiler 3, comparison 0, the grammar 4, the language's own tools 14. With the place it was read in the language's own tools: 14.

| Rule | Case | Versions | Engine |
|---|---|---|---|
| an unqualified method name is a method of the class | `implicit-this` | all | yes |
| an unqualified method name is also searched in the superclasses | `inherited` | all | yes |
| `super.m()` is the superclass's m | `super` | all | yes |
| a class of the same package needs no import | `same-package` | all | yes |
| `import p.C;` binds C | `single-type-import` | all | yes |
| `import p.*;` makes p's types available | `on-demand-import` | all | yes |
| `import static p.C.m;` binds m | `static-import` | all | yes |
| `C.m()` is C's static method | `static-via-class` | all | yes |
| a field's declared type gives its members | `field-type` | all | yes |
| a local variable shadows a field of its name | `local-hides-field` | all | yes |
| overloads are told apart by the number of arguments | `overload-arity` | all | yes |
| `new C(…)` calls C's constructor | `constructor` | all | yes |
| a default method of an implemented interface is inherited | `interface-default` | all | yes |
| an inner class sees the methods of the class around it | `outer-method` | all | yes |
| a method's declared return type gives the members of its result | `chained-result` | all | yes |
| `Page<Item>`'s `T lead()` gives an Item | `generic-argument` | all | yes |
| a project type's own `get()` is its method, whatever libraries name so | `own-accessor-name` | all | yes |
| a generic class the project names `Box` is the project's class | `class-named-like-a-wrapper` | all | yes |
| an enhanced `for` variable is declared with its type | `for-each` | all | yes |
| `List<T>.get(i)` is a T | `list-element` | all | yes |
| `var r = new C();` has type C | `var` | 10+ | yes |
| `(T) x` is a T | `cast` | all | yes |
| `o instanceof T r` binds r as a T | `instanceof-pattern` | 16+ | yes |
| a resource variable is declared with its type | `try-with-resources` | all | yes |
| `this.f.m()` is a member of the field's type | `this-field` | all | yes |
| a static field's declared type gives its members | `static-field` | all | yes |
| a call in a lambda's body is the enclosing method's | `lambda-body` | all | yes |
| `this::m` and `C::m` are uses of m | `method-reference` | all | yes |
| overloads of one arity are told apart by the argument's type | `overload-by-type` | all | yes |
| `super(…)` / `this(…)` in a constructor call the constructor of the base, or another of the class | `explicit-constructor-call` | all | yes |
| an import of `p.C` names the class of package p, whatever module-info.java declares a module `p` | `module-info` | 9+ | yes |
| a generic class the project names `Optional` is the project's class where it is in scope | `own-optional` | all | yes |
| `java.util.Optional` imported by a file stays the library's beside a project class of that name elsewhere | `library-optional-beside-own` | all | yes |
| `self` is a name like any other where the language has no such word | `method-named-self` | all | yes |
| a nested class is named through the class around it | `nested-static-class` | all | yes |
| an enum constant has the enum's methods | `enum-method` | all | yes |
| a call through an interface type is the interface's method | `interface-typed` | all | yes |
| `p.C.m()` names C by its package | `fully-qualified` | all | yes |
| `I.m()` is a static method of the interface | `interface-static` | all | yes |
| `new R(…)` constructs a record | `record` | 16+ | yes |
| a method of an anonymous class sees the methods of the class around it | `anonymous-class` | all | yes |
| an enum constant named as a value is a use of its enum | `enum-constant-value` | all | yes |
| a class of another package is not in reach without an import | `other-package-not-in-reach` | 9+ | yes |
| `instanceof T`, `(T) x` and `catch (T e)` are uses of T | `instanceof-cast-catch` | all | yes |
| the exceptions a method declares it throws are uses of them | `throws-clause` | – | yes |
| `T.class` is a use of T | `class-literal` | – | yes |
| the type of a for-each variable and of a varargs parameter are uses of it | `for-each-and-varargs-types` | – | yes |
| the type of a constant an interface declares is a use of it | `interface-constant-type` | – | yes |
| a method a class inherits comes before one of the class around it (the innermost class with a method of the name) | `inherited-before-enclosing` | – | yes |
| a field a class inherits comes before a field of the class around it | `inherited-field-before-enclosing` | – | yes |
| a method of the class shadows a method a static import brings | `own-method-before-static-import` | – | yes |
| a single-type import shadows a type of the same package | `single-type-import-before-package` | – | yes |
| a type of the same package shadows one an on-demand import brings | `package-before-on-demand-import` | – | yes |
| a member type of the class shadows an imported type of its name | `member-type-before-import` | – | yes |
| a member type a class inherits is in scope in it | `inherited-member-type` | – | yes |
| `Outer.this.m()` is the method of the class around | `qualified-this` | – | yes |
| a generic method's type parameter hides a class of its name | `method-type-parameter-hides-a-class` | – | yes |
| a constant of an implemented interface is a name in the class | `inherited-interface-constant` | – | yes |
| of two applicable overloads, the one whose parameter type is a subtype is more specific | `most-specific-reference-type` | – | yes |
| an overload applicable without variable arity is chosen before a varargs one | `fixed-arity-before-varargs` | – | yes |
| an overload applicable without boxing is chosen before one that needs it | `no-boxing-before-boxing` | – | yes |
| a class declared in a block is a name of the block | `local-class` | – | yes |

### kotlin (55 of 55)

Kotlin Language Specification, 'Scopes and identifiers', 'Overload resolution' (receivers).

Found by: the specification 31, a compiler 11, comparison 0, the grammar 0, the language's own tools 13. With the place it was read in the language's own tools: 15.

| Rule | Case | Versions | Engine |
|---|---|---|---|
| a top-level function of the same package needs no import | `same-package` | all | yes |
| an import binds a top-level function of another package | `import` | all | yes |
| `import a.b as c` binds c | `import-alias` | all | yes |
| a method call on a typed value is the type's member | `member` | all | yes |
| an extension function is called like a member of its receiver type | `extension` | all | yes |
| a member wins over an extension of the same name | `member-wins` | all | yes |
| `C.make()` is a member of C's companion object | `companion` | all | yes |
| an `object` declaration's members are called through its name | `object` | all | yes |
| a `val` of the primary constructor is a typed property | `constructor-property` | all | yes |
| inside `x.apply { … }` an unqualified name is x's member | `receiver-lambda` | all | yes |
| `it` in a lambda given to a collection's method is an element | `it` | all | yes |
| an unqualified name in a class is also searched in its supertypes | `inherited` | all | yes |
| a `val` assigned a construction has that type | `local-typed-by-value` | all | yes |
| `Page<Item>`'s `val head: T` is an Item | `generic-argument` | all | yes |
| `r?.m()` is r's m | `safe-call` | all | yes |
| inside `with(x) { … }` an unqualified name is x's member | `with` | all | yes |
| `x.let { it.m() }`: `it` is x | `let` | all | yes |
| a top-level `val` assigned a construction has that type | `top-level-property` | all | yes |
| a `lateinit var`'s declared type gives its members | `lateinit` | all | yes |
| a `for` variable over `List<T>` is a T | `for-loop` | all | yes |
| `x as T` is a T | `cast` | all | yes |
| a function's declared result gives the members of what it returns | `return-type` | all | yes |
| a typealias names the type it is written for | `typealias` | all | yes |
| `x.apply { … }` and `x.also { … }` give x back | `apply-also` | all | yes |
| `C(…)` calls C's constructor | `constructor` | all | yes |
| `super.m()` is the supertype's m | `super` | all | yes |
| a method an interface defines is inherited | `interface-default` | all | yes |
| a call through an interface type is the interface's member | `interface-typed` | all | yes |
| `::f` is a use of f | `function-reference` | all | yes |
| a nested class is named through the class around it | `nested-class` | all | yes |
| an enum entry has the enum's members | `enum-entry` | all | yes |
| a call names the function beside a parameter of its name that cannot be called | `function-beside-parameter` | all | yes |
| the type a property is declared with is a use of it | `property-type` | all | yes |
| `x is T` and `x as T` are uses of T | `is-and-as` | all | yes |
| `is T ->` in a `when` is a use of T, written through its outer type too | `when-is` | all | yes |
| the type a `catch` names is a use of it | `catch-type` | all | yes |
| an object named as a value is a use of it | `object-as-value` | all | yes |
| the type a typealias names is a use of it | `typealias-body` | all | yes |
| a constructor parameter written `val` is a property of the class | `constructor-property-read` | all | yes |
| the name of an argument given by name reads no property of that name | `named-argument-label` | all | yes |
| a local function comes before a member of the implicit receiver | `local-function-before-member` | – | yes |
| a member of the implicit receiver comes before a top-level function | `member-before-top-level` | – | yes |
| a top-level function imported by name comes before one of the same package | `explicit-import-before-package` | – | yes |
| a top-level function of the same package comes before a star-imported one | `package-before-star-import` | – | yes |
| an extension declared in the function comes before a top-level one | `local-extension-first` | – | yes |
| an extension imported by name comes before one of the same package | `imported-extension-before-package` | – | yes |
| `super<A>.f()` is A's member | `super-of-a-named-supertype` | – | yes |
| `a + b` calls the `operator fun plus` of a's type | `operator-call` | – | yes |
| `a[i]` calls the `operator fun get` of a's type | `index-operator` | – | yes |
| `a foo b` calls the `infix fun foo` of a's type | `infix-call` | – | yes |
| of two applicable overloads, the one with the more specific parameter type | `most-specific-type` | – | yes |
| a candidate that needs no `vararg` expansion is more specific | `non-vararg-before-vararg` | – | yes |
| a property of a function type called is the property, not a function of its name | `property-invoke` | – | yes |
| an extension called in the middle of a chain gives its declared result, which the next call is made on: `c.argument().optional()` | `extension-result-in-a-chain` | – | yes |
| an extension whose result is written as a type alias gives the aliased type to the next call | `extension-result-through-typealias` | – | yes |

### scala (37 of 37)

Scala Language Specification, chapter 2 'Identifiers, Names and Scopes', 4.7 'Import Clauses'.

Found by: the specification 20, a compiler 8, comparison 0, the grammar 4, the language's own tools 5. With the place it was read in the language's own tools: 5.

| Rule | Case | Versions | Engine |
|---|---|---|---|
| `Obj.f()` is a member of the object | `object-method` | all | yes |
| a method call on a typed value is the type's member | `typed-parameter` | all | yes |
| a method of a mixed-in trait is inherited | `trait-method` | all | yes |
| `import p._` makes p's members available | `wildcard-import` | all | yes |
| `import p.{A => B}` binds B | `import-rename` | all | yes |
| an unqualified name in a class is a member of it | `implicit-this` | all | yes |
| `C(…)` constructs a case class | `case-class` | all | yes |
| a `val` assigned `new C` has that type | `local-typed-by-value` | all | yes |
| a `val` assigned `new C()` has that type | `new-with-parentheses` | all | yes |
| a `val` with a declared type has that type | `typed-val` | all | yes |
| a method's declared result gives the members of what it returns | `return-type` | all | yes |
| `case r: T` binds r as a T | `pattern-typed` | all | yes |
| a method called without parentheses is still called | `no-parentheses` | all | yes |
| a call through a trait type is the trait's member | `trait-typed` | all | yes |
| `import p.Obj.f` binds f | `member-import` | all | yes |
| `super.m()` is the parent's m | `super` | all | yes |
| `C(…)` is the companion object's `apply` | `companion-apply` | all | yes |
| `Page[Item]`'s `head: T` is an Item | `generic-argument` | all | yes |
| `class A extends B` names its parent | `extends` | all | yes |
| a class of the same package in another file needs no import | `same-package` | all | yes |
| a generic method's parameters are its names, typed through an upper bound | `generic-method-parameters` | all | yes |
| a type of another package is not in reach without an import | `other-package-not-in-reach` | all | yes |
| a type nested in a class of another file is no bare name elsewhere | `nested-type-not-in-reach` | all | yes |
| an argument given by name writes no variable of that name | `named-argument` | all | yes |
| the type parameters of an `extension` are those of its methods | `extension-type-parameters` | all | yes |
| `pkg.Type` in a type position is that package's type | `type-through-package` | all | yes |
| a constructor pattern and a typed pattern are uses of the type they name | `pattern-types` | all | yes |
| the types a type parameter is bound by are uses of them | `bound-types` | all | yes |
| the type a `val` or `var` is declared with is a use of it | `declared-value-type` | – | yes |
| `new Outer.Inner(…)` constructs Inner | `new-through-its-holder` | – | yes |
| `new p.T[A](…)` constructs T | `new-generic-through-package` | – | yes |
| `f[T](…)` calls f | `generic-call` | – | yes |
| a name imported by name beats one a wildcard import brings | `named-import-before-wildcard` | – | yes |
| a definition of the compilation unit beats an imported name | `definition-before-import` | – | yes |
| a wildcard import beats a member of the package that another compilation unit defines | `wildcard-import-before-other-unit` | – | yes |
| a member of the receiver's type comes before an extension method of the name | `member-before-extension` | – | yes |
| `export inner.f` makes f a member of the object that writes it | `export-clause` | – | yes |

### csharp (264 of 397)

C# Language Specification, 'Namespace and type names', 'Member lookup', 'Extension method invocations'.

Found by: the specification 39, a compiler 8, comparison 6, the grammar 1, the language's own tools 18, a sweep of them 325. With the place it was read in the language's own tools: 345.

| Rule | Case | Versions | Engine |
|---|---|---|---|
| a name is looked up in every namespace around the use | `enclosing-namespace` | all | yes |
| `using N;` makes N's types available | `using` | all | yes |
| `namespace N;` holds the whole file | `file-scoped-namespace` | 10.0+ | yes |
| `using R = N.C;` binds R | `using-alias` | all | yes |
| `using static N.C;` makes C's static members available | `using-static` | all | yes |
| an unqualified name in a class is a member of it | `implicit-this` | all | yes |
| `base.M()` is the base class's M | `base` | all | yes |
| an extension method is called like a member of its `this` parameter's type | `extension` | all | yes |
| the members of a partial type are those of all its parts | `partial` | all | yes |
| `var x = new C();` has type C | `var-new` | all | yes |
| a property's declared type gives its members | `property-type` | all | yes |
| overloads are told apart by the number of arguments | `overload-arity` | all | yes |
| `C.M()` is C's static method | `static-via-class` | all | yes |
| a call through an interface type is the interface's member | `interface-typed` | all | yes |
| a nested type is a member of the type around it | `nested-type` | all | yes |
| `Page<Item>`'s `T Head` is an Item | `generic-argument` | all | yes |
| a `foreach` variable declared with a type has it | `foreach-typed` | all | yes |
| a `foreach (var r in xs)` variable over `List<T>` is a T | `foreach-var` | all | yes |
| `o is T r` binds r as a T | `is-pattern` | all | yes |
| `(T)x` is a T | `cast` | all | yes |
| `x as T` is a T | `as-cast` | all | yes |
| `r?.M()` is r's M | `null-conditional` | all | yes |
| `T r = new();` constructs a T | `target-typed-new` | 9.0+ | yes |
| `await` of a `Task<T>` is a T | `await-task` | all | yes |
| a static property's declared type gives its members | `static-property` | all | yes |
| a call in a lambda's body is the enclosing method's | `lambda-body` | all | yes |
| a method passed by name is a use of it | `method-group` | all | yes |
| a local function is called by its name in the method | `local-function` | all | yes |
| `this.M()` is the class's M | `this` | all | yes |
| an unqualified name in a class is also searched in its base | `inherited` | all | yes |
| `: base(…)` / `: this(…)` call the constructor of the base, or another of the class | `constructor-initializer` | all | yes |
| `using N;` brings the extensions of a namespace written as a block in another directory into sight | `using-block-namespace` | all | yes |
| `new Name(…)` names the type, whatever method of the class around is called `Name` | `new-names-a-type` | all | yes |
| `Maybe.Nothing<T>()` is a member of the static class `Maybe` beside the generic `Maybe<T>` | `generic-namesake` | all | yes |
| a class's primary constructor parameter is a typed name of the class | `primary-constructor` | 12.0+ | yes |
| `global using N;` in one file holds for every file | `global-using` | 10.0+ | yes |
| an extension written for an interface is a member of the classes that implement it | `extension-on-interface` | all | yes |
| an enum member is a member of its enum | `enum-member` | all | yes |
| `new R(…)` constructs a record, which has methods like a class | `record` | 9.0+ | yes |
| an enum member named as a value is a use of its enum | `enum-member-value` | all | yes |
| `x is T`, `x as T` and `catch (T e)` are uses of T | `is-as-catch` | all | yes |
| a type is told from its generic namesake by the type arguments written | `type-by-arity` | all | yes |
| `List<string>` is never a project class `List` that takes no type argument | `generic-use-is-no-plain-type` | all | yes |
| a call written with more type arguments than any method of the type takes is the extension's | `extension-by-type-arguments` | all | yes |
| the type a local is declared with, and `typeof(T)`, are uses of the type | `local-declared-type` | all | yes |
| what a property's accessor calls is the property's call, not its class's | `property-body-is-the-property` | all | yes |
| a namespace written as a block sees the extensions of the namespaces around it | `block-namespace-sees-outer-extension` | all | yes |
| a brace an `#if` opens and a later `#if` closes hides no member after it | `brace-opened-in-a-directive` | 10.0+ | yes |
| a local assigned a member read is what the member holds | `local-from-member-read` | all | yes |
| `new T { … }` with no argument list calls the constructor that takes none | `initializer-without-arguments` | all | yes |
| a partial type's constructors are those of every file that declares it | `partial-type-constructor` | all | yes |
| `new T[n]` is a use of T | `array-creation-type` | – | yes |
| in a generic method a name of its type parameters is that parameter, not a type of the namespace | `method-type-parameter-first` | – | yes |
| a nested type of a base class is named without its outer type in the derived class | `nested-type-of-a-base` | – | yes |
| a type of the namespace the use is in comes before one a `using` imports | `namespace-type-before-using` | – | yes |
| an alias a `using` declares comes before a type of the same name another `using` imports | `using-alias-before-using-namespace` | – | yes |
| a `using` written inside a namespace block holds only inside it | `using-inside-a-namespace-block` | – | yes |
| a local or a parameter of a name hides the member of that name | `local-hides-member` | – | yes |
| a member of the derived class hides one of its name in the base | `derived-member-hides-base` | – | yes |
| `Color.White` where a property `Color Color` is in scope is the type's static member | `color-color` | – | yes |
| an instance method that fits comes before an extension method of its name | `instance-method-before-extension` | – | yes |
| of extensions in sight, those of the closest enclosing namespace are the candidates | `closest-extension-namespace` | – | yes |
| an extension of a namespace neither around the use nor imported is not in sight | `extension-needs-its-namespace` | – | yes |
| of two overloads that take the argument, the one whose parameter is its type is called | `overload-exact-type` | – | yes |
| an overload all of whose parameters have arguments is better than one that needs a default | `overload-without-defaults` | – | yes |
| an overload applicable in its normal form is better than a `params` one in its expanded form | `overload-normal-before-params` | – | yes |
| a named argument picks the overload that has a parameter of that name | `overload-by-argument-name` | – | yes |
| of two candidates with the same parameter types, the one that is not generic is better | `overload-non-generic-first` | – | yes |
| a member of a base of the base class is a member of the class | `inherited-from-a-grandparent` | – | yes |
| `Outer.Inner` names the nested type | `qualified-nested-type` | – | yes |
| an argument declared with exactly a parameter's type picks that overload over one taking another constructed type | `identity-conversion-overload` | – | yes |
| an extension method called in the middle of a chain gives its result, which the next extension is called on | `extension-result-in-a-chain` | – | yes |
| a namespace and a generic type share a name: `Result.Item` goes through the namespace, `Result<int>` is the type | `namespace-and-generic-type-of-one-name` | – | **no**: no C.M -calls-> Result.Get |
| a lambda parameter of a name hides a local of the method around, inside the lambda | `lambda-parameter-hides-local` | – | yes |
| a local declared in one switch section is in scope in a later section, and hides a field of its name | `switch-section-local-scope` | – | yes |
| a private member of a base is not accessible in a derived class outside it, so a bare name is the member of the class around | `inaccessible-base-member-skipped` | – | **no**: no Derived.M -calls-> Outer.Helper has Derived.M -calls-> Base.Helper |
| a private member that hides an inherited one hides it only inside its own class: a further derived class names the base's | `inaccessible-hider-hides-nothing` | – | **no**: no MoreDerived.G -calls-> Base.F has MoreDerived.G -calls-> Derived.F |
| a private nested type of a base class is not a meaning of a type name in the derived class: the namespace's type is | `inaccessible-nested-type-of-a-base` | – | **no**: no Derived.M -references-> A.cs:App.Item; Derived.M -calls-> Item.Run has Derived.M -references-> Base.Item |
| a derived method hides base methods of the same signature only: a base overload of another signature is still called | `derived-overload-of-another-signature` | – | **no**: no Derived.UseInt -calls-> Base.Show has Derived.UseInt -calls-> Derived.Show |
| a class's type parameter hides a namespace type of its name in the class, and only there | `class-type-parameter-hides-type` | – | yes |
| a base class's type parameter is no name in a derived class: `T` there is the namespace's type | `type-parameter-scope-ends-with-its-class` | – | yes |
| a local's scope ends with its block or its `for`: after it the name is the field again | `local-scope-ends-with-its-block` | – | yes |
| a local of a name hides the field of that name in its whole block, its own declarator included | `local-hides-field-in-its-block` | – | yes |
| where a type is written, a field of the same name is ignored | `field-named-like-a-type-is-no-type` | – | yes |
| a member of a nested type hides every overload of the outer type's of its name: `F(1)` is the inner `F(long)`, not the outer `F(int)` | `inner-member-hides-outer-overloads` | – | yes |
| an interface's `new` member hides the base interface's of its signature for a value typed by the derived interface | `interface-member-hides-base-interface-member` | – | yes |
| inside a namespace, `Sub` is its nested namespace, not a global type of that name | `namespace-before-outer-type` | – | yes |
| `global::Item` is the global namespace's type, `Item` the one of the namespace around | `global-alias-qualifier` | – | **no**: no C.M -calls-> Item.cs:Item.Run has C.M -calls-> A.cs:App.Item.Run |
| `using L = Lib;` makes `L.Repo` the type of Lib, though `using Other;` imports another Repo | `using-alias-of-a-namespace` | – | yes |
| `G<int>.H` is the type nested in G, not the namespace's `H` | `nested-type-of-a-constructed-type` | – | yes |
| a name in a type's base list is looked up outside the type: an inherited nested type is not a meaning | `base-list-ignores-inherited-nested-types` | – | **no**: no Derived -references-> A.cs:App.Inner has Derived -references-> Base.Inner |
| a member of a value typed by a type parameter is a member of its constraint (interface or base class) | `type-parameter-constraint-member` | – | yes |
| `[Cache]` names the attribute class `CacheAttribute`, not a class `Cache` that is no attribute | `attribute-suffix` | – | yes |
| a local declared in a local function hides a local of the method around, inside the function only | `local-function-local-hides-outer-local` | – | yes |
| in a `set` accessor `value` is the property's implicit parameter, not a field of that name | `setter-value-hides-field` | – | **no**: no C.Prop -calls-> A.Run has C.Prop -calls-> B.Run |
| a `using static N.Outer;` makes the nested type Inner a name of the file | `using-static-nested-type` | – | yes |
| an explicit interface implementation `void I.M()` is no member of its class by name: `M()` in the class means the inherited `B.M` | `explicit-interface-impl-not-found-by-name` | – | **no**: no C.Run -calls-> B.M has C.Run -calls-> C.M |
| a local function hides a method of its name throughout its block, including a call written before its declaration | `local-function-hides-method-in-whole-block` | – | yes |
| `if (o is Box x)` declares x for the if statement only: inside it x is the local, after it x is the field | `csharp-pattern-variable-if-scope` | – | yes |
| `Make(out var x);` declares x in the enclosing block: a later `x.Open()` is the local, not the field x | `csharp-out-variable-block-scope` | – | **no**: has Holder.Leaks -reads-> Holder.x |
| a type of the global namespace is visible from every namespace | `global-namespace-type` | – | yes |
| the usings of a compilation unit have no effect on another | `using-is-per-file` | – | yes |
| a namespace declared in several files is one: its types are visible before a `using`'s | `namespace-open-ended` | – | yes |
| a `using` alias names a namespace, and `R.Repo` is that namespace's type | `alias-to-namespace` | – | yes |
| a using alias is hidden by a type of the same name in a nested namespace | `alias-hidden-by-namespace-member` | – | yes |
| an alias of an inner namespace body hides one of the same name of the file, which holds elsewhere | `alias-innermost-first` | – | **no**: no A.Run -calls-> Other/Repo.cs:Other.Repo.Find has A.Run -calls-> Lib/Repo.cs:Lib.Repo.Find |
| each part of a partial type sees the aliases of its own namespace body | `alias-per-partial-part` | – | yes |
| a using alias of a closed constructed type is that type | `alias-closed-generic` | – | **no**: no A.Run -calls-> Box.Get |
| `using N;` imports no namespace of N: `Inner.Part` is the global Inner | `using-does-not-import-namespaces` | – | yes |
| a using of the inner namespace body comes before a type of the namespace around | `inner-using-before-outer-type` | – | **no**: no C.Run -calls-> Lib.Repo.Find has C.Run -calls-> Outer.Repo.Find |
| `using static C;` makes C's extension methods callable as extensions | `using-static-extension` | – | **no**: no A.Run -calls-> Ext.Shout |
| `using static T;` imports what T declares, not what its bases declare | `using-static-not-inherited` | – | **no**: no A.Run -calls-> Util.Twice has A.Run -calls-> Base.Twice |
| `global::Repo` is the type of the global namespace, not the one of the namespace around | `global-qualifier` | – | **no**: has A.Run -calls-> App.Repo.Find |
| `S::Repo` looks S up only as an alias, never as the type S of the namespace | `alias-qualifier` | – | **no**: has A.Run -references-> App.S |
| `global.Tool` is the alias named global; `global::Tool` is still the global namespace's | `alias-named-global` | – | yes |
| a `using` after a file-scoped `namespace App.Sub;` is read before a type of the namespace App around it | `file-scoped-using-before-outer-namespace` | 10– | **no**: no C.M -references-> Lib.Repo; C.M -calls-> Lib.Repo.Find has C.M -references-> App.Repo |
| an alias is the type it names as the qualifier of a static call and in `new` | `using-alias-as-qualifier` | – | yes |
| `using static T;` imports T's static fields and properties as simple names | `using-static-fields-and-properties` | 6– | yes |
| a member of the enclosing class comes before a name imported by `using static` | `using-static-hidden-by-own-member` | 6– | yes |
| `using static Lib.Mode;` makes Mode's members simple names: `Fast` is Mode.Fast, not Other.Fast | `using-static-enum-members` | – | yes |
| the keyword `string` is System.String whatever a project type is named `String`; the name `String` is the project's | `keyword-alias-is-system-type` | – | yes |
| a tuple's element is its field by element name or by ItemN, each with its own type | `tuple-element-names` | – | **no**: no C.Left -calls-> Repo.Find; C.Right -calls-> Other.Find |
| `S?` is Nullable<S>: its `.Value` and the `?.` operand are S, whose members are the ones used | `nullable-value-type-members` | – | **no**: no C.Run -calls-> Point.Len |
| a nested type named inside a generic class is that class's, written without type arguments | `generic-outer-nested-type` | – | yes |
| `Box<int>.Make()` is the static member of the generic Box, `Box.Make()` of the non-generic one: the type arguments tell them apart in an expression too | `static-member-of-a-constructed-type` | – | **no**: no C.Plain -calls-> A.cs:App.Box.Make@10 has C.Plain -calls-> A.cs:App.Box.Make@5 |
| `typeof(Pair<,>)` names the unbound generic type with as many type parameters as there are commas plus one | `typeof-unbound-generic-arity` | – | yes |
| the types a `where` clause names are uses of those types | `constraint-names-are-uses` | – | yes |
| in a generic class a name of its type parameters is that parameter, not a type of the namespace | `class-type-parameter-first` | – | yes |
| a value of a type parameter has the members of the type its constraint names | `type-parameter-constraint-members` | – | yes |
| `T?` on a reference type is the type T: the annotation changes no meaning | `nullable-reference-annotation` | – | **no**: no C.Run -calls-> Repo.Find |
| an argument converts implicitly to a base class: only the overload taking it applies | `overload-base-conversion` | – | yes |
| an argument converts implicitly to an interface its class implements | `overload-interface-conversion` | – | yes |
| of two parameter types the argument converts to, the more derived one is better | `overload-more-derived-parameter` | – | **no**: has C.Run -calls-> A.cs:App.Log.Write@17 |
| an int variable or constant converts implicitly to long, never to string | `overload-numeric-conversion` | – | **no**: has C.Local -calls-> A.cs:App.Log.Write@7 |
| an int constant in range converts implicitly to byte | `overload-constant-conversion` | – | yes |
| the literal null converts to a reference type, never to a non-nullable value type | `overload-null-literal` | – | **no**: has C.Run -calls-> A.cs:App.Log.Write@5 |
| a value of a type parameter converts implicitly to its effective base class | `overload-type-parameter-bound` | – | yes |
| a lambda's untyped parameter has the type of the declared delegate it is assigned to | `lambda-parameter-from-declared-delegate` | – | **no**: no C.Make -calls-> Repo.Find |
| a lambda passed as an argument takes its parameter types from the delegate type of the parameter | `lambda-parameter-from-method-parameter` | – | **no**: no C.Run -calls-> Repo.Find |
| a lambda is compatible only with a delegate with as many parameters as it | `overload-by-lambda-arity` | – | **no**: has C.Run -calls-> A.cs:App.Runner.Apply@7 |
| a method group converted to a delegate is the overload applicable to the delegate's parameter types | `method-group-picks-overload-by-delegate` | – | **no**: no C.Make -indirect_call-> A.cs:App.C.Handle@9 |
| an extension method named through an instance is a method group that converts to a delegate | `method-group-extension` | – | **no**: no C.Make -indirect_call-> RepoExt.Size |
| a method group converted to a delegate is the overload applicable to the delegate's parameter types (passed as an argument) | `method-group-overload-by-delegate-roslyn` | – | yes |
| the types a generic method's `where` clause names are uses of those types | `generic-method-constraint-is-a-use` | – | yes |
| an extension method written for a base class is called on a value of a derived class; one written for a sibling class is not | `extension-on-base-class` | – | yes |
| an extension written for `this string` is called on a string, one written for `this int` on an int | `extension-on-predefined-type` | – | **no**: no Run.Go -calls-> StrExt.Twice; Run.Go2 -calls-> IntExt.Twice |
| calling a parameter of a declared delegate type gives the delegate's return type | `delegate-invocation-result` | – | **no**: no Run.Go -calls-> Item.Weigh |
| `xs[0].M()` on an `Item[]` is Item's M | `array-element-type` | – | yes |
| `new Handler(OnData)` is a use of the OnData overload that takes the delegate's parameter type | `delegate-creation-method-group` | – | yes |
| `Func<Item,int> f = x => x.Weigh();` has x of type Item | `lambda-parameter-from-generic-delegate` | – | **no**: no Run.Make -calls-> Item.Weigh |
| `(Op)Inc` is a use of the Inc overload that takes Op's parameter type | `cast-method-group-to-delegate` | – | **no**: no Run.Go -indirect_call-> A.cs:App.Run.Inc@4 |
| members of `t` typed `T : IShape, ISized`, `T : Node` (member of its base) and `T : U` with `U : IShape` are the constraints' members | `type-parameter-several-constraints` | – | **no**: no Run.Go -calls-> ISized.Size; Run.Go3 -calls-> IShape.Draw |
| `Write(i)` with an int variable is the overload taking `int?`, not the one taking string | `overload-nullable-conversion` | – | yes |
| `Write(0)` is the overload taking the enum, not the one taking string | `overload-enum-zero-literal` | – | yes |
| `Write(s)` with a struct is the overload taking the interface the struct implements, not the one taking string | `overload-boxing-conversion` | – | yes |
| `Write(ds)` with a `Dog[]` is the overload taking `Animal[]`, not the one taking string | `overload-array-covariance` | – | yes |
| `Write(b)` with an IBar (IBar : IFoo) is the overload taking IFoo, not the one taking string | `overload-base-interface-conversion` | – | yes |
| a base class written with type arguments gives its members, with each type parameter replaced by the argument written | `generic-base-substitution` | – | yes |
| the interfaces of a partial type are the union of those each part names | `partial-interface-lists-union` | – | yes |
| of the methods applicable to a call, those declared in a base type of another candidate's type are dropped, even a better fit | `applicable-derived-method-hides-base` | – | yes |
| a nested type declared with `new` hides a member of a base class of its outer type, for `Derived.M.F()` | `nested-type-hides-base-member` | – | yes |
| a bare name in a nested class is a member of the nested class, with its bases, before a member of the class around it | `nested-bare-name-outer-then-inherited` | – | yes |
| inside a generic class its generic nested type is named without the outer's type arguments, before a type of the namespace of that name | `nested-generic-type-unqualified` | – | yes |
| the value of a constant is read by a simple name or a member access, in another constant's expression too | `constant-reads-constant` | – | yes |
| one declaration of several fields declares each as its own member | `multi-declarator-field` | – | yes |
| a variable initializer is bound in the class: its names are members of the class before a `using static` import | `field-initializer-uses` | – | yes |
| the expanded form of a params method is not considered when a method of that very signature is declared | `params-expanded-form-yields-to-declared` | – | **no**: has T.Two -calls-> A.cs:App.T.F@5 |
| a default argument is an expression of the method's own scope | `default-argument-constant` | – | **no**: no C.N -reads-> C.Size |
| an `out` argument given as `out var x` is a local of the parameter's type | `out-variable-type` | – | **no**: no C.Var -calls-> Item.Id |
| a non-virtual method is the one of the static type of the receiver, hiding or not | `static-type-picks-hidden-method` | – | yes |
| a call of a virtual method is bound to the declaration of the receiver's static type, an override or a hiding `new virtual` | `override-call-binds-to-override` | – | yes |
| a call of a partial method is a use of the one method its two parts declare | `partial-method-call` | – | yes |
| an extension method is the same method called as `Ext.M(x)`, and its first parameter may be an array type | `extension-static-and-array-receiver` | – | yes |
| a method written `IFoo.Bar` is not the class's member `Bar`: `c.Bar()` is the public one, `((IFoo)c).Bar()` the interface's | `explicit-interface-member-is-no-class-member` | – | **no**: has User.Direct -calls-> A.cs:App.C.Bar@12 |
| a property of the derived class hides the base's for reading and writing alike; a cast reaches the base's | `cast-reaches-hidden-property` | – | **no**: no C.Write -writes-> A.P |
| `base.X` and `base.X = v` in an overriding property are the base property's reads and writes | `base-property-access` | – | **no**: no B.X -reads-> A.X; B.Y -reads-> A.Y; B.Y -writes-> A.Y |
| an auto-property is written in the constructor and read by its name; its initializer is bound in the class | `auto-property-use` | – | **no**: no Point.Point -writes-> Point.X |
| a method group attached to an event with `+=` or `new Handler(m)` is a use of the method | `method-group-event-subscription` | – | **no**: no Dialog.Wire -indirect_call-> Dialog.OkClicked |
| an element access is a use of the indexer picked by the arguments, for reading or writing | `indexer-use` | – | yes |
| `base[i]` in an overriding indexer is the inherited indexer | `base-indexer-access` | – | yes |
| an operator expression is a use of the user-defined operator of its operand types, also one a base class declares | `user-defined-operator` | – | yes |
| an implicit or explicit conversion between a class and another type is a use of the conversion operator | `conversion-operator` | – | **no**: no C.Widen -calls-> Digit.operator byte; C.Narrow -calls-> Digit.operator Digit |
| a constructor with no initializer calls the base class's parameterless constructor | `implicit-base-constructor` | – | **no**: no B.B -calls-> A.cs:App.A.A@5 |
| the parameters of a constructor are names of its initializer, before the fields | `constructor-initializer-parameter-hides-field` | – | yes |
| the variable of a `foreach` over an iterator method is the iterator's yield type | `foreach-over-iterator` | – | yes |
| an `override` method is excluded from member lookup, so `d.M(1)` picks the derived class's own applicable `M(double)` over its override `M(int)` | `override-is-no-candidate` | – | **no**: has C.Go -calls-> A.cs:App.Derived.M@10 |
| `Put(ref x)` is the overload whose parameter is a reference parameter, not the value one | `overload-by-ref-kind` | – | **no**: has C.Go -calls-> A.cs:App.Log.Put@5 |
| `t.Foo()` with `T : U` and `U : IFoo` is IFoo's member | `constraint-through-type-parameter` | – | **no**: no Box.Run -calls-> IFoo.Foo |
| `t.Describe()` with `T : IFoo` is the extension written for IFoo | `extension-on-constrained-type-parameter` | – | yes |
| a bare name in a part that names no base is a member of the base another part names, before a type's member of that name elsewhere | `partial-base-in-other-part` | – | yes |
| `a += b`, `a++`, `-a` and `a && b` call the operator methods of the operand's class | `operator-forms-use-the-operator` | – | **no**: no C.Both -calls-> V.operator&; C.Both -calls-> V.operator false |
| `o.secret` and `Shared()` in a nested class are the containing class's private field and static method, not a same-named member of another class | `nested-type-reaches-private-of-container` | – | yes |
| `await j` where j's type declares GetAwaiter is a call of that type's GetAwaiter, not another type's | `await-getawaiter-pattern` | – | yes |
| `foreach (var i in b)` over a type with a GetEnumerator method calls it, and `i.V` is the field of the type of Cursor.Current | `foreach-getenumerator-pattern` | – | yes |
| inside a set accessor `value` is the implicit parameter of the property's type, not a field of that name | `setter-value-parameter` | – | **no**: no C.P -calls-> B.Run has C.P -calls-> A.Run |
| a nested generic class's type parameter named like the outer's is the nested one: members asked of it are those of its own constraint | `nested-type-parameter-hides-outer` | – | yes |
| M(1, 2) calls M(int, int) rather than the expanded form of M(params int[]) | `params-normal-form-before-expanded` | – | yes |
| a field named like a base method, invoked, is no candidate: the call is the base class's method | `non-invocable-member-does-not-hide-method` | – | **no**: no U.F -calls-> Base.M |
| `await foreach (var x in b)` calls the GetAsyncEnumerator the type of b declares, and x has the Current type of what it returns | `await-foreach-pattern` | – | yes |
| `checked(a + b)` uses `operator checked +`, `unchecked(a + b)` the plain `operator +` | `checked-operator` | 11– | yes |
| `l[^1]` is a use of the int indexer of l's type and of its Count | `implicit-index-support` | 8– | **no**: no U.F -reads-> L.Count |
| `new C { X = …, N = 1 }` writes C.X and C.N, the members of C, not names of the surrounding scope | `object-initializer-member` | – | yes |
| the members of a partial struct are those of all its parts | `partial-struct` | – | yes |
| a base list names a class (inherits) and interfaces (implements); a struct names only interfaces | `class-and-interfaces-in-a-base-list` | – | yes |
| a struct's own override of ToString is what `s.ToString()` means | `struct-overrides-a-valuetype-member` | – | yes |
| `new S()` of a struct without a parameterless constructor is the implicit one; a constructor with parameters is not called | `struct-default-constructor` | – | **no**: has A.Zero -calls-> Point.Point |
| a member asked of a type parameter is a member of its interface constraint | `constraint-interface-member` | – | yes |
| the initializer of a static field of a struct names the struct's members | `struct-static-field-initializer` | – | yes |
| an element of an array is of the array's element type, whatever the rank and nesting | `array-element-access` | – | **no**: no A.Jagged -calls-> Repo.Find; A.Mixed -calls-> Repo.Find |
| an extension written for an array type applies to values of that array type | `extension-on-array-type` | – | yes |
| `new T[n]` is an array of T; its elements are T | `array-creation-element` | – | **no**: no A.Run -calls-> Repo.Find |
| a range index on an array gives an array of the same element type | `array-slice-keeps-the-array-type` | 8.0– | **no**: no A.Run -calls-> Repo.Find |
| a `foreach` over an array of T gives T | `array-foreach-element` | – | yes |
| a member of a base interface is a member of the derived interface | `interface-inherited-member` | – | yes |
| a member inherited from a constructed interface has its type parameters replaced by the type arguments | `interface-inherited-generic-member` | – | yes |
| an extension written for an interface applies to values of the interfaces derived from it | `extension-on-base-interface` | – | yes |
| an explicit implementation of an inherited member is qualified by the interface that declares it, not the derived one | `explicit-implementation-names-the-declaring-interface` | – | yes |
| a member a derived interface declares hides the base interface's member of its name | `interface-member-hides-base` | – | yes |
| a member hidden on any access path is hidden on all of them | `interface-hidden-on-every-path` | – | yes |
| the members of unrelated base interfaces are all candidates of an invocation | `interface-overloads-of-two-bases` | – | **no**: no A.RunDouble -calls-> IDouble.Add has A.RunDouble -calls-> IInteger.Add |
| a default method of an interface names the members of its base interfaces by their simple names | `interface-default-method-names-base-member` | 8.0– | yes |
| the members of a partial interface are those of all its parts | `partial-interface` | – | yes |
| a static member of an interface is named through the interface | `interface-static-member` | 8.0– | yes |
| a type nested in an interface is a member of the interface, named through it | `interface-nested-type` | 8.0– | yes |
| `base.M()` binds to the member of the base class, never to the interface member it implements | `base-call-reaches-the-class-member` | – | yes |
| the interface in an explicit implementation's qualified name is the one the name means in that scope | `explicit-implementation-names-its-interface` | – | yes |
| an explicit interface member implementation is reached through the interface, never the class | `explicit-implementation-through-the-interface` | – | yes |
| the body of an explicit interface member implementation sees the members of its class by their simple names | `explicit-implementation-body` | – | yes |
| a public member of a base class implements an interface the derived class lists; a derived `new` member does not take its place | `interface-mapping-through-a-base-class` | – | **no**: no Interface1.F -dispatches_to-> Class1.F |
| a member declared `new` hides the base member: calls through the base do not reach it | `new-member-overrides-nothing` | – | **no**: has Class1.G -dispatches_to-> Class2.G |
| one public member implements the same-named members of every interface the class lists | `interface-mapping-one-member-for-two-interfaces` | – | yes |
| a class that lists an inherited interface again maps it afresh: onto its own public member | `interface-reimplementation` | – | yes |
| the integral type name of an enum is a type name, looked up like any other | `enum-underlying-type-name` | – | yes |
| an enum member's initializer names the other members of its enum by their simple names, before a type of the same name around | `enum-member-initializer` | – | yes |
| an enum member has the type of its enum: an extension of the enum applies to it | `extension-on-enum-value` | – | **no**: no A.Direct -calls-> ColorExt.Code |
| a generic delegate type is named by its arity; type arguments give its parameter types | `generic-delegate-type` | – | **no**: no A.Run -calls-> Repo.Find |
| `d.Invoke(…)` is the delegate type's Invoke, not a method of another type | `delegate-invoke-member` | – | yes |
| `new D(x.M)` names the method M of x's type | `delegate-creation-names-a-method` | – | **no**: no Test.Static -indirect_call-> C.M1; Test.Instance -indirect_call-> C.M2 |
| a parameter of delegate type named like a method is what `name(args)` invokes | `delegate-local-hides-method` | – | yes |
| invoking a field of delegate type is not a call of a method of its name | `delegate-field-invocation` | – | **no**: no A.Run -reads-> A.handler |
| a delegate declaration's parameter and return types are uses of the types they name, the enclosing class's nested type before a namespace type | `delegate-signature-types` | – | **no**: no Host.Make -references-> Host.Seen |
| an extension written for a delegate type applies to a value of that delegate type | `extension-on-delegate` | – | **no**: no A.Run -calls-> ExtA.Twice |
| `Op o = Target;` names the method Target; it is an indirect use | `method-group-to-delegate` | – | **no**: no A.Run -indirect_call-> A.Target |
| `b.Current` on an IBox is IBox.Current, and the call on its result is the interface property's type's | `interface-property-access` | – | yes |
| `var xs = new[] { new Seen() }; xs[0].Mark()` is Seen.Mark | `implicit-array-element` | – | **no**: no A.Run -calls-> Seen.Mark |
| `c.Bump()` on a Counter variable is the extension written `this ref Counter` | `extension-on-ref-struct-receiver` | 7.2– | yes |
| T.Create() with T : IMake binds to IMake.Create, not Maker.Create or Other.Create | `csharp-interface-static-abstract-through-type-parameter` | 11– | yes |
| g.Hi() on an IGreet-typed local binds to IGreet.Hi (the default body), not to Plain2.Hi | `csharp-interface-default-method-via-interface` | 8– | yes |
| b.Get() on IBox<int> binds to the interface member IBox.Get, not to the class's explicit implementations | `csharp-interface-two-closed-generic-implementations` | – | yes |
| when a name is invoked, a member of the derived type that is not invocable does not hide the base's method of that name | `invoked-name-skips-non-invocable` | – | **no**: no C.M -calls-> Base.Run |
| a name written with K type arguments names only the methods with K type parameters | `generic-method-by-type-arguments` | – | yes |
| from a nested type a bare name is looked up in each enclosing type in turn, so an outer type's static member is in sight | `nested-type-sees-outer-static` | – | yes |
| a parenthesized expression has the meaning of the expression inside | `parenthesized-receiver` | – | yes |
| `T.M()` with a type parameter constrained by an interface with a static abstract member names that interface's member | `static-abstract-through-type-parameter` | 11– | yes |
| after `a?.b` the dependent accesses `.c()` are members of the type of `a.b` | `null-conditional-dependent-access` | – | **no**: no C.M -reads-> Car.Engine |
| an indexer access picks, among the indexers of the type, the one applicable to the arguments | `indexer-by-argument` | – | yes |
| `a?[i]` is an indexer access on a, and what follows it is a member of the indexer's type | `null-conditional-element-access` | – | yes |
| `base[i]` is an indexer access on the base class | `base-indexer` | – | yes |
| an object initializer's member name is a member of the created type, written | `object-initializer-targets` | – | yes |
| a nested object initializer's names are members of the type of the field or property it initializes | `nested-object-initializer` | – | yes |
| each element of a collection initializer calls the Add method of the collection | `collection-initializer-add` | – | yes |
| `typeof(T)` names the type the type-name lookup finds | `typeof-names-a-type` | – | yes |
| `default(T)` names the type the type-name lookup finds | `default-expression-names-a-type` | – | yes |
| `nameof(T.M)` binds its named entity by the rules of simple names and member access | `nameof-names-its-target` | – | **no**: no C.M -references-> Order.Total |
| `v++` calls the user-defined operator ++ of the operand's type | `increment-calls-operator` | – | yes |
| `await t` is `t.GetAwaiter().GetResult()`: a member of the awaited value is a member of GetResult's type | `await-custom-awaitable` | – | yes |
| a switch expression arm's declaration pattern binds a variable of the type it names | `switch-expression-type-pattern` | – | yes |
| among the operators of one name, the one whose parameters fit the operand types is called | `operator-by-operand-types` | – | **no**: no C.M -calls-> A.cs:App.Money.operator+@7 |
| `-a` calls the user-defined unary operator of its operand's type | `unary-operator` | – | **no**: no C.M -calls-> A.cs:App.Vec.operator-@5 |
| `a ?? b` has the type of a (its underlying type when nullable), so a member of it is a member of that type | `coalesce-type` | – | **no**: no C.M -calls-> Part.Size |
| an `out var` declared in an argument list has the declared type of the parameter | `out-variable-declaration` | – | **no**: no C.M -calls-> Part.Size |
| a variable declared by `var (a, b) = ...` has the type of its tuple element | `deconstruction-variable-types` | – | **no**: no C.M -calls-> Part.Size |
| `c ? x : y` has the type of its arms, so a member of it is a member of that type | `conditional-type` | – | **no**: no C.M -calls-> Part.Size |
| `x.P = v` assigns the property or field the member lookup finds | `simple-assignment-writes-member` | – | yes |
| `a += b` is `a = a + b`: it calls the user-defined operator + | `compound-assignment-uses-operator` | – | yes |
| `e += h` adds to the event the lookup finds and `h` is the method its group names | `event-assignment` | – | **no**: no C.M -references-> C.OnClick |
| a deconstructing assignment calls the Deconstruct method of the right side's type | `deconstruction-calls-deconstruct` | – | yes |
| the Deconstruct overload is the one with as many out parameters as the left side has variables | `deconstruct-overload-by-count` | – | yes |
| a type with no Deconstruct member is deconstructed by an extension method | `deconstruct-extension` | – | yes |
| a member named in a `with` initializer is a member of the type of the copied value, and the result has that type | `with-expression-members` | 9– | yes |
| a lambda parameter may shadow a local of the enclosing method: inside the lambda the name is the parameter, typed as written | `lambda-parameter-shadows-enclosing-local` | 8– | yes |
| alias::Type.Member: the alias names a namespace, so Type is that namespace's, not another namespace's type of the same name | `qualified-alias-member-access` | – | **no**: no U.Go -calls-> Lib.cs:Util.F |
| await of a value whose GetAwaiter is an extension method: the call is a use of the extension and the result has the type GetResult returns | `await-extension-get-awaiter` | – | **no**: no U.Go -calls-> Ext.GetAwaiter; U.Go -calls-> Num.Show |
| a cast (T)s with a user-defined explicit conversion operator in T calls that operator, not that of another type | `cast-calls-explicit-conversion` | – | **no**: no U.Go -calls-> Dst.operator Dst |
| a + b on Money? operands calls Money's operator+ (lifted), not another struct's | `lifted-user-operator` | – | yes |
| an indexer initializer `new Bag { ["x"] = 1 }` names the indexer of the created type | `object-initializer-indexer` | – | yes |
| an operator is found in the base class of the operand's type | `operator-of-base-class` | – | yes |
| `a && b` on a type with operator true/false and & calls operator false and operator &, not operator \| | `conditional-logical-operator` | – | **no**: no Use.Go -calls-> F.operator& |
| `var (a, b) = p;` calls Deconstruct of the type of p | `deconstruction-declaration` | – | yes |
| if (f) with a struct declaring operator true calls it | `cs-operator-true-condition` | – | **no**: no Use.Run -calls-> Flag.operator true |
| a != b calls operator != of the operand type | `cs-operator-not-equals` | – | yes |
| b << 2 calls operator << not >> | `cs-operator-shift` | – | yes |
| checked(a + b) calls operator checked +, unchecked(a + b) the plain operator + | `cs-checked-operator` | 11– | **no**: no Use.Chk -calls-> M.operator checked + has Use.Chk -calls-> M.operator+ |
| a + b on T : IAdd<T> calls IAdd.operator +, not the implementation | `cs-static-abstract-operator-typeparam` | 11– | yes |
| s?.Value() / s?.P with S? s are members of S | `cs-null-conditional-nullable-struct` | – | **no**: no Use.Run2 -reads-> S.P |
| b[^1] reads Bag.Count and the int indexer; b[1..] calls Bag.Slice | `cs-implicit-index-range-support` | – | **no**: no Use.Run -reads-> Bag.Count; Use.Run2 -calls-> Bag.Slice |
| p switch { { X: 0 } => ... } reads Pt.X | `cs-property-pattern-member` | – | **no**: no Use.Run -reads-> Pt.X |
| the deconstruction variable of foreach calls the element type's Deconstruct | `cs-foreach-deconstruct` | – | yes |
| `a + b` with a user-defined operator is a call of that operator declaration of the operand's type, not another type's | `operator-use-is-a-call` | – | yes |
| `a += b` is an overload use of operator `+` of a's type | `operator-compound-assignment` | – | yes |
| `-x` is a call of the unary operator of x's type | `operator-unary` | – | yes |
| a binary operator is also looked for in the type of the right operand | `operator-from-the-second-operand` | – | **no**: no Use.Run -calls-> Money.operator+ |
| an operator declared in a base class is a candidate for the derived type | `operator-of-a-base-class` | – | yes |
| if an operator of T is applicable, those of its base classes are not candidates | `operator-of-the-derived-type-first` | – | **no**: no Use.Run -calls-> Derived.operator+ |
| `a == b` and `a != b` are calls of the type's own `==` and `!=` | `operator-equality` | – | yes |
| a cast `(T)x` is a use of the user-defined conversion operator that converts to T | `conversion-operator-cast` | – | **no**: no Use.Run -calls-> Meters.operator Meters |
| a method of the most derived type that is applicable removes the base's methods from the candidates, even when a base method is the better match | `derived-applicable-method-hides-base` | – | **no**: no Use.Run3 -calls-> Base.F has Use.Run3 -calls-> Derived.F |
| a call through a receiver of the derived type is the override, and its result has the override's (covariant) return type | `override-called-through-its-type` | 9– | yes |
| an instance method of the name that does not take the arguments does not stop the search: the extension that does is called | `extension-when-the-instance-method-is-inapplicable` | – | yes |
| of the extensions of one namespace, overload resolution chooses by the type of the first parameter: the one written for the more derived type | `extension-better-this-type` | – | yes |
| an extension declared in a namespace comes before one the namespace imports with `using`, which comes before the enclosing namespace's | `extension-declared-before-imported` | – | **no**: no Use.Run -calls-> E.F; Use.Run2 -calls-> D.G |
| an extension is eligible only if an identity, reference or boxing conversion leads from the receiver to its first parameter: an `int` receiver does not reach a `long` one | `extension-receiver-conversion` | – | **no**: no Use.Run -calls-> A.cs:App.Ext.Twice@7 |
| a call written with type arguments takes only generic methods with that many type parameters; one written without them prefers the non-generic | `type-argument-list-picks-the-generic` | – | yes |
| the type arguments of a generic method are inferred from the arguments: the result is the argument's type | `inferred-generic-result` | – | **no**: no Use.Run -calls-> Item.Go |
| a call through a value does not consider static overloads and one through a type does not consider instance ones, even when they fit better | `receiver-decides-static-or-instance` | – | **no**: has Use.Run -calls-> A.cs:App.Log.Write@6 |
| a generic method whose inferred type arguments do not satisfy their constraints is not applicable | `generic-constraint-excludes-candidate` | – | **no**: has Use.Run -calls-> A.cs:App.Pick.Take@6 |
| a value argument takes the value parameter where an `in` one fits too; an `in` argument only the `in` parameter | `overload-value-over-in` | – | **no**: has Use.Run -calls-> A.cs:App.M.M1@7; Use.Run2 -calls-> A.cs:App.M.M1@6 |
| of two `params` candidates applicable only in the expanded form with the same parameter types, the one with more declared parameters (fewer elements in the array) is better | `overload-params-more-declared-parameters` | – | **no**: no Use.Run -calls-> A.cs:App.Log.Write@7 has Use.Run -calls-> A.cs:App.Log.Write@6 |
| of generic candidates with the same parameter types after inference, the one declared with the more specific parameter type is better | `overload-more-specific-generic` | – | yes |
| a `null` argument picks the parameter whose type converts to the other's | `overload-null-takes-the-more-specific` | – | **no**: has Use.Run -calls-> A.cs:App.Log.Write@6 |
| when substituting the type arguments of a generic class makes two overloads identical, the one declared with the more specific parameter type is chosen | `generic-class-overload-original-signature` | – | yes |
| an element access selects among the indexers of the receiver's type by overload resolution | `indexer-overload` | – | yes |
| calling a value of a delegate type invokes that delegate type | `delegate-invocation` | – | **no**: no Use.Run -calls-> Handler; Use.Run2 -calls-> Other |
| where a method named `nameof` is in scope the call is a call of it, otherwise `nameof(X)` names X | `nameof-method-in-scope` | – | **no**: has Names.Run -calls-> Use.nameof |
| a deconstruction calls the `Deconstruct` method of the type, and each new variable has the type of its out parameter | `deconstruct-method-and-element-types` | – | **no**: no Use.Run2 -calls-> Thing.Go |
| a type with no `Deconstruct` method is deconstructed by an extension method of that name | `deconstruct-extension-method` | – | yes |
| a tuple deconstructs into its elements, each with its element type | `deconstruct-tuple-types` | – | **no**: no Use.Run -calls-> Thing.Go |
| in `a?.b?.M()` and `a?.F().M()` each member is looked up in the type of the part before it | `null-conditional-chain` | – | **no**: no Use.Run -reads-> Node.Child |
| a parameter of an anonymous function written with its type has that type in the body | `lambda-explicit-parameter-type` | – | yes |
| the parameter of an anonymous function hides a field of its name in the body only | `lambda-parameter-hides-member` | – | yes |
| a name in the body of an anonymous function that is no parameter of it is looked up outward: an outer local hides a field | `lambda-uses-outer-local` | – | yes |
| two or more parameters named `_` are discards that name nothing; a single `_` is a parameter | `lambda-discard-parameters` | 9– | **no**: no Use.Run2 -calls-> Item.Go has Use.Run2 -calls-> Other.Go |
| the range variable of a `from` clause has the element type of the source and hides a field of its name in the whole query | `query-range-variable` | – | **no**: no Use.Run -calls-> Customer.Active; Use.Run -calls-> Customer.Name has Use.Run -calls-> Other.Active; Use.Run -calls-> Other.Name; Use.Run -reads-> Use.c |
| `from T x in e` gives the range variable the type T | `query-explicit-range-type` | – | **no**: no Use.Run -calls-> Customer.Active |
| a `let` variable has the type of its expression, a second `from` ranges over the elements of its expression, and earlier range variables stay in scope | `query-let-and-second-from` | – | **no**: no Use.Run -calls-> Customer.Id; Use.Run -calls-> Dept.Title; Use.Run2 -calls-> Order.Total |
| `join` ranges over its source and compares keys of the two range variables; a continuation `into g` ranges over the groups | `query-join-and-continuation` | – | **no**: no Use.Run -calls-> Customer.Id; Use.Run -calls-> Order.Total; Use.Run2 -calls-> Customer.Name |
| a query clause is a call of the source's method of that name (`where` is `Where`, `select` is `Select`), found as any method, an instance one before an extension; its lambdas take their parameter types from it | `query-pattern-methods` | – | **no**: no Use.Run -calls-> Source.Where; Use.Run -calls-> Source.Select; Use.Run -calls-> Item.Ok; Use.Run -calls-> Item.Val |
| a private method of the derived type is not a candidate from outside it; the accessible base method is called | `inaccessible-method-not-candidate` | – | **no**: no User.Run -calls-> Base.Put has User.Run -calls-> Box.Put |
| t.Draw() with T : IShape is IShape.Draw | `type-parameter-receiver-constraint` | – | yes |
| a + b on T : IAdd<T> is a use of IAdd.operator+, not of the implementing class's operator | `static-abstract-operator-via-constraint` | 11– | yes |
| `if (s.TryGet(out var n)) n.Show();` is Num.Show | `out-var-has-parameter-type` | – | **no**: no User.Run -calls-> Num.Show |
| `from x in s where .. orderby a, b select x` calls Where, OrderBy and ThenBy, and no Select | `query-trivial-select-omitted` | – | **no**: no User.Run -calls-> Src.Where; User.Run -calls-> Src.OrderBy; User.Run -calls-> Src.ThenBy |
| x + y inside checked(...) is the `operator checked +`, inside unchecked(...) the plain `operator +` | `checked-operator-by-context` | 11– | **no**: no Host.Strict -calls-> Money.operator checked + has Host.Strict -calls-> Money.operator+ |
| `T.Create()` with `where T : IMake<T>` binds to IMake.Create, never to the Create of an implementing class | `static-abstract-call-on-type-parameter` | 11– | yes |
| in `x => x.Hit()` the `x` is the lambda's parameter (type B), not the enclosing method's local `x` (type A) | `lambda-parameter-hides-outer-local` | 8– | **no**: no Host.Shadow -calls-> B.Hit |
| a derived class's method of the name that does not take the arguments does not hide the base's that does | `derived-method-not-applicable-base-used` | – | **no**: no Derived.Run -calls-> Base.M has Derived.Run -calls-> Derived.M |
| a field that is not of a delegate type does not hide a base method of its name when the name is invoked | `non-invocable-member-skipped` | – | **no**: no Derived.Run -calls-> Base.Size |
| `var (x, y) = p` is a call of the two-out-parameter Deconstruct, not the three-out one | `deconstruct-by-arity` | – | yes |
| `b?[0].Weight()` calls Weight of the indexer's type | `null-conditional-element-access-member` | – | yes |
| `delegate (Foo f) { f.Go(); }` calls Foo.Go, not the Go of the field f's type | `anonymous-method-parameter-type` | – | **no**: no C.Run -calls-> Foo.Go has C.Run -calls-> Bar.Go |
| `c++` is a call of `operator ++` of c's type | `increment-operator-call` | – | yes |
| `x` after `into y` is the field x, not the range variable | `query-continuation-ends-range-variables` | – | yes |
| `First(list).Go()` and `Head(array).Go()` call Foo.Go | `inference-through-interface-and-array` | – | **no**: no C.Run -calls-> Foo.Go |
| `new T("a")` constructs with T(string), not T(int) | `constructor-overload-by-type` | – | yes |
| `a` in the inner lambda is the outer lambda's `Foo a`, not the field a | `nested-lambda-outer-parameter` | – | yes |
| `Step(1)` beside a local function Step is the local function, not the method Step | `local-function-hides-method` | – | yes |
| `Helper()` in a nested class is the enclosing class's static; `Count()` is the nested class's own, not the enclosing one's | `nested-class-calls-enclosing-static` | – | yes |
| the name left of the colon in a property pattern is a field or property of the input type, not of the enclosing class | `property-pattern-member` | 9.0– | **no**: no Checker.Run -reads-> Person.Name; Checker.Typed -reads-> Person.Age has Checker.Run -reads-> Checker.Name |
| in `{ Home.City: ... }` Home is a member of the input type and City a member of Home's type | `extended-property-pattern` | 10.0– | **no**: no Checker.Run -reads-> Person.Home; Checker.Run -reads-> Address.City |
| `p is (var a, var b)` calls the instance Deconstruct of that arity; three subpatterns call the extension Deconstruct | `positional-pattern-deconstruct` | 8.0– | yes |
| `x is B` where B names a type of the namespace and a constant of the class: the type pattern (Roslyn binds the type) | `constant-pattern-before-type` | – | **no**: has A.Run -reads-> A.B |
| `case Limits.Max:` and `case Zero:` read the constants they name | `constant-pattern-case-label` | – | yes |
| `c is var d` gives d the type of c | `var-pattern-input-type` | – | **no**: no C.Run -calls-> Circle.Area |
| `o is Circle or Square` and `o is not Tri` are uses of the three types | `pattern-combinators` | 9.0– | yes |
| a local declared in an inner block hides the field only inside that block | `block-local-scope` | – | yes |
| a `for` variable hides the field of its name only inside the loop | `for-variable-scope` | – | yes |
| a call written before a local function's declaration still means the local function, not the method of the class | `local-function-before-declaration` | – | yes |
| with a class named `var` in scope, `var v = new var();` declares a local of that class | `type-named-var` | – | **no**: no C.Run -references-> var; C.Run -calls-> var.A |
| a local declared in one switch section is the name in the other sections too | `switch-block-local-scope` | – | yes |
| `case Circle c when c.R > 0:` binds c in its section only; elsewhere c is the field | `switch-section-pattern-scope` | – | **no**: no Shapes.Run -calls-> Other.Name; Shapes.Arm -calls-> Other.Name has Shapes.Run -calls-> Circle.Name; Shapes.Arm -calls-> Circle.Name |
| after `if (!(o is Foo s)) return;` s is the Foo local; after `while (o is Foo s)` s is the field again | `pattern-variable-leaks-from-if` | – | yes |
| `foreach (var it in bag)` where Bag : IEnumerable<Item> makes it an Item | `foreach-project-ienumerable` | – | yes |
| foreach over a type whose only GetEnumerator is an extension method types the variable by that enumerator's Current | `foreach-extension-getenumerator` | 9.0– | yes |
| the variables of a deconstructing foreach have the types of Deconstruct's out parameters | `foreach-deconstruct` | 7.0– | yes |
| `catch (AppError e) when (e.Code > 0) { e.Log(); }`: e is an AppError in the filter and block, the field e after them | `catch-variable-type` | – | **no**: no C.Run -calls-> AppError.Log; C.Run -reads-> AppError.Code has C.Run -calls-> Other.Log; C.Run -reads-> Other.Code |
| `using (var r = new Res())` and `using var r = new Res();` declare r as a Res, hiding the field r | `using-resource-variable` | 8.0– | yes |
| the iteration variable of a foreach is a name of the loop only; after it the name is the member again | `foreach-variable-scope` | – | yes |
| a public GetEnumerator method of the collection type wins over its IEnumerable<T> implementation: the iteration variable is the pattern enumerator's Current | `foreach-pattern-before-interface` | – | yes |
| the iteration variable of an `await foreach` over IAsyncEnumerable<T> is a T | `await-foreach-variable` | – | yes |
| in `var (a, b) = p;` a and b are typed by the out parameters of p's Deconstruct method | `deconstruction-declaration-types` | – | yes |
| `s.TryGet(out var r)` declares r, typed by the out parameter, in the enclosing block; a later `r.Find()` is on that local, not on the field r | `out-var-types-and-hides` | – | **no**: no A.Run -calls-> Repo.Find has A.Run -calls-> Other.Find |
| `if (o is not Repo r) return;` leaves r, a Repo, in scope after the statement, hiding a field r | `is-not-pattern-variable` | – | yes |
| `[Mark]` means `MarkAttribute`, not a non-attribute class `Mark` | `attribute-suffix-omitted` | – | yes |
| `[Help(Topic = ..., Level = ...)]`: the names are members of the attribute class (own or inherited), not of the decorated type | `attribute-named-argument-member` | – | **no**: no Doc -writes-> BaseAttr.Topic; Doc -writes-> HelpAttribute.Level |
| `[@Example]` is the class `Example`, `[ExampleAttribute]` is `ExampleAttribute`, though both exist | `attribute-verbatim-name` | – | yes |
| `[Mark]` means the `MarkAttribute` of the namespace its own file imports | `attribute-name-per-file-usings` | – | yes |
| each part of a partial type decorates the type, its attribute name resolved by that part's usings | `attribute-partial-parts-own-usings` | – | yes |
| `[assembly: Stamp]` means the `StampAttribute` of the namespace the file imports (the file is its source) | `assembly-attribute` | – | **no**: no A.cs -references-> Marks.StampAttribute |
| `[return: Mark]`, `[type: Mark]`, `[field: Mark]` and a parameter attribute name the same attribute class, on the declaration they are written on | `attribute-target-specifier` | – | **no**: no Holder.P -references-> MarkAttribute; Holder.N -references-> MarkAttribute |
| `[Limit(Limit)]` on a method or on the class itself: the argument is the class's own constant, not one a `using static` brings | `attribute-argument-scope-member` | – | yes |
| `[Help("x")]` calls `HelpAttribute`'s constructor, not its base attribute's | `attribute-constructor` | – | **no**: no Doc -calls-> HelpAttribute.HelpAttribute |
| a class `Mark` that is no attribute is not what `[Mark]` means when `MarkAttribute` is declared in an outer namespace | `attribute-lookup-skips-non-attribute` | – | yes |
| typeof(T) and a constant named in an attribute's arguments are uses, from the decorated method, of that type and constant | `csharp-attribute-argument-uses` | – | yes |
| an attribute on a type is looked up in the scope of that type: its nested attribute class hides the outer one of the same name | `attribute-nested-class-shadows-outer` | – | **no**: no Other -references-> A.cs:MarkAttribute |
| an attribute written with a using alias name means the aliased attribute class | `attribute-using-alias` | – | yes |
| a namespace-qualified attribute name finds the suffixed class in that namespace only | `attribute-qualified-name` | – | **no**: no B -references-> Two.MarkAttribute |
| a generic attribute's type arguments choose the class of that arity, not the non-generic class of the same name | `attribute-generic-arity` | 11– | **no**: no Target -references-> MarkAttribute<T> has Target -references-> MarkAttribute |

### rust (75 of 75)

The Rust Reference, 'Names' (name resolution, scopes, paths), 'Method call expressions', 'Field access expressions'.

Found by: the specification 37, a compiler 15, comparison 6, the grammar 2, the language's own tools 15. With the place it was read in the language's own tools: 20.

| Rule | Case | Versions | Engine |
|---|---|---|---|
| `use crate::m::f;` binds f | `use-path` | – | yes |
| `use m::f as g;` binds g | `use-alias` | – | yes |
| `use m::*;` binds m's public names | `glob-import` | – | yes |
| `crate::m::f()` names f by its path | `qualified-path` | – | yes |
| `super::f()` is f of the parent module | `super` | – | yes |
| `pub use a::T;` makes T a name of the re-exporting module | `re-export` | – | yes |
| `T::f()` is an associated function of T | `associated-function` | – | yes |
| a method call on a local is a method of its type | `method-on-local` | – | yes |
| `Self::f()` is the type the impl is for | `self-type` | – | yes |
| `x.f` with no call is the field f, whatever methods are named f | `field-not-method` | – | yes |
| a trait's method is a method of the types that implement it | `trait-method` | – | yes |
| an impl block can be written in another module than the type | `impl-in-another-file` | – | yes |
| a later `let` of the same name is the name from there on | `shadowing` | – | yes |
| the arguments of a macro are expressions like any other | `macro-arguments` | – | yes |
| `E::V(…)` constructs a variant of E | `enum-variant` | – | yes |
| a field's declared type gives its methods | `field-type` | – | yes |
| `f()?` of a `Result<T, E>` is a T | `question-mark` | – | yes |
| `opt.unwrap()` of an `Option<T>` is a T | `option-unwrap` | – | yes |
| `if let Some(r) = opt` binds r as the option's T | `if-let-some` | – | yes |
| a method call goes through `Arc<T>` / `Box<T>` to T | `smart-pointer` | – | yes |
| a `for` variable over `&Vec<T>` is a T | `vec-iteration` | – | yes |
| `w.0` of `struct W(T)` is a T | `tuple-struct-field` | – | yes |
| a `static`'s declared type gives its methods | `static-item` | – | yes |
| a `&T` / `&mut T` parameter is a T | `reference-parameter` | – | yes |
| a call through `&dyn Trait` is the trait's method | `dyn-trait` | – | yes |
| a call on a `T: Trait` value is the trait's method | `generic-bound` | – | yes |
| a trait's default method is a method of every implementor | `trait-default-method` | – | yes |
| `a::b::f()` walks nested modules | `nested-module-path` | – | yes |
| `self::f()` is f of the current module | `self-path` | – | yes |
| `use super::*;` in a test module binds the parent's names | `use-super-glob` | – | yes |
| `mod x;` can be the file `x/mod.rs` | `mod-rs` | – | yes |
| `use a::{self, f};` binds the module and the name | `use-self-in-braces` | – | yes |
| a function passed by name is a use of it | `function-as-value` | – | yes |
| a call in a closure's body is the enclosing function's | `closure-body` | – | yes |
| a method returning `Self` keeps the type through a chain | `builder-chain` | – | yes |
| a method of an enum's impl is called on its values | `enum-method` | – | yes |
| `T { … }` constructs a T | `struct-literal` | – | yes |
| a `let` in a block ends with the block | `block-scope` | – | yes |
| a value of a type parameter has the methods of the trait that bounds it | `generic-bound-where` | – | yes |
| `Option<Space<M>>` holds a `Space`, whatever `M` is bound by | `wrapper-holds-first-argument` | – | yes |
| an enum variant named as a value or a pattern is a use of its enum | `enum-variant-value` | – | yes |
| the type an `impl` gives its trait is no name of the file | `associated-type-is-no-file-name` | – | yes |
| what a type alias names and what a `where` clause bounds by are uses of those types | `alias-and-where` | – | yes |
| `self.x` with no call is the field, beside a method of its name | `field-beside-a-method` | – | yes |
| `-> Result<Self, E>` gives the type the method is declared for | `result-of-self` | – | yes |
| a `let` of any value is a local where it is passed on | `untyped-let-hides-a-function` | – | yes |
| a name of another crate is in reach only through a `use` | `another-crates-name` | – | yes |
| the types of a tuple struct's and a tuple variant's fields are uses of them | `tuple-field-types` | – | yes |
| the type a `const` or `static` item is written with is the item's use | `item-type-is-the-items` | – | yes |
| `crate::` starts at the file a `[[bin]] path` names | `crate-root-named-by-the-manifest` | – | yes |
| an `impl` is of the type its own file names, where two modules each have a type of the name | `impl-of-the-files-own-type` | – | yes |
| `io::Error::message(m)` is the method a trait of the project gives the library's type | `trait-method-through-a-library-type` | – | yes |
| the types a trait is written with in an `impl` are uses of the implementing type | `impl-trait-type-arguments` | – | yes |
| the struct a pattern takes apart is a use of it | `struct-pattern-type` | – | yes |
| a field read in a macro's arguments is a read of the field | `macro-argument-member-read` | – | yes |
| a function declared in a block is a name of that block, before the module's of its name | `item-in-a-block` | – | yes |
| the items of a module around are no names of a nested module: only its own and what it imports are | `outer-module-items-not-in-scope` | – | yes |
| a `let` binding shadows a function of its name only after it: a call before it is the function | `let-shadows-from-where-it-is` | – | yes |
| in an `impl`, a bare name is never an associated function of the type: that takes `Self::` | `associated-items-are-not-scoped` | – | yes |
| a generic type parameter is the name in the item it is declared on, before a type of the module | `generic-parameter-hides-a-type` | – | yes |
| a name the module declares shadows the one a glob import brings; the glob still brings the others | `glob-shadowed-by-an-item` | – | yes |
| a name imported by name shadows the one a glob import brings | `named-import-shadows-a-glob` | – | yes |
| a `use` in a block shadows a `use` of the module of the same name | `inner-use-shadows-outer` | – | yes |
| a module and a function of one name live in different namespaces: `log::f()` is the module's, `log()` the function | `module-and-function-namespaces` | – | yes |
| since 2018 a `use` path starts at a name in scope: a child module of the module that writes it before one of the crate root | `uniform-paths` | 2018– | yes |
| a method of the type's own `impl` is found before a trait's method of the name | `inherent-method-first` | – | yes |
| `Self::f()` in a trait's default method is the trait's associated function | `self-in-a-trait` | – | yes |
| `Self` in a struct's own fields is the struct | `self-type-in-a-struct` | – | yes |
| a tuple struct's name called is its constructor, in the value namespace | `tuple-struct-constructor` | – | yes |
| a binding of a `match` arm is a name of the arm: it hides a function of its name there only | `match-arm-binding` | – | yes |
| a macro's argument `E::V` is a path: its last name is the variant, never a bare name of the module | `path-in-macro-argument` | – | yes |
| in `impl<S: Sink> Core<S>`, a field typed `S` is a `Sink`: a method called on it is the trait's, never a blanket impl's | `impl-generics-bound-the-struct-parameter` | – | yes |
| an item of another module is in scope only through a `use`: `Result` in the crate root is the prelude's, not `http::Result` | `item-of-another-module-needs-use` | – | yes |
| `xs().is_empty()` where `xs` returns `&[T]` is the slice's method; `xs()[0].is_empty()` is the element's | `size-of-a-returned-slice` | – | yes |
| `usize::from(b)` is the primitive's associated function: no guess goes to a project method of the name | `primitive-type-path` | – | yes |

### swift (52 of 52)

The Swift Programming Language, 'Declarations', 'Extensions', 'Protocols' (member lookup is the compiler's).

Found by: the specification 33, a compiler 5, comparison 0, the grammar 5, the language's own tools 9. With the place it was read in the language's own tools: 9.

| Rule | Case | Versions | Engine |
|---|---|---|---|
| a method call on a typed value is the type's method | `method-on-parameter` | all | yes |
| an unqualified name in a type is a member of it | `implicit-self` | all | yes |
| an extension's methods are members of the extended type | `extension` | all | yes |
| `T.make()` is T's static method | `static` | all | yes |
| `T(…)` calls T's initializer | `initializer` | all | yes |
| `super.m()` is the superclass's m | `super` | all | yes |
| a member not on the class is looked up on its superclass | `inherited` | all | yes |
| a call through a protocol type is the protocol's requirement | `protocol-typed` | all | yes |
| a protocol extension's method is a member of every conforming type | `protocol-extension` | all | yes |
| a stored property's declared type is a use of that type, and gives its members | `property-type` | all | yes |
| `.name` where a type is expected is a static member of that type | `implicit-member` | all | yes |
| a `let` assigned an initializer call has that type | `local-typed-by-value` | all | yes |
| a nested type is a member of the type around it | `nested-type` | all | yes |
| `r?.m()` is r's m | `optional-chaining` | all | yes |
| `if let r = opt` binds r as the optional's type | `if-let` | all | yes |
| `guard let r = opt` binds r for the rest of the scope | `guard-let` | all | yes |
| a `for` variable over `[T]` is a T | `for-in` | all | yes |
| an element of `[T]` is a T | `subscript` | all | yes |
| `x as! T` is a T | `as-cast` | all | yes |
| a function's declared result gives the members of what it returns | `return-type` | all | yes |
| a computed property's declared type gives its members | `computed-property` | all | yes |
| `T.shared.m()` goes through a static property's type | `static-property` | up to 5 | yes |
| the types an enum case carries are types the enum names | `enum-payload` | all | yes |
| building a case with what it carries (`E.c(x)`) is a use of the enum | `enum-case-built` | all | yes |
| `T.Type` names T | `metatype` | all | yes |
| a typealias names the type it is written for | `typealias` | all | yes |
| `Page<Item>`'s `head: T` is an Item | `generic-argument` | all | yes |
| a method an extension adds with a conformance is a member of the type | `conformance-in-extension` | all | yes |
| an enum case has the enum's methods | `enum-method` | all | yes |
| `T.make()` is a `class func` of T | `class-func` | all | yes |
| a function nested in a function is called by its name there | `nested-function` | all | yes |
| a call in a closure's body is the enclosing function's | `closure-body` | all | yes |
| a function passed by name is a use of it | `function-as-value` | all | yes |
| a member asked of a type parameter is a member of the protocol that constrains it | `generic-bound` | all | yes |
| inside the value a `let` is declared with, its name is still the outer one | `own-value-is-outer` | all | yes |
| `x is T`, `x as? T` and `catch let e as T` are uses of T | `is-and-as` | all | yes |
| what a typealias composes and what a `where` clause bounds by are uses of those types | `typealias-and-where` | all | yes |
| `Self()` constructs the type around | `self-constructs` | all | yes |
| `throws(E)` is a use of E | `typed-throws` | – | yes |
| the type of a property a protocol requires is a use of it | `protocol-property-type` | – | yes |
| `(any P).self` is a use of P | `metatype-of-existential` | – | yes |
| `Box<T>(…)` constructs Box | `generic-construction` | – | yes |
| `.case` compared with, assigned to or switched on a value is a case of that value's type | `implicit-member-beside-a-value` | – | yes |
| a function declared in a method comes before a method of the type | `local-function-before-member` | – | yes |
| a member of the type around comes before a function of the module | `member-before-global` | – | yes |
| a type's own method comes before a protocol extension's default of its name | `concrete-before-protocol-extension` | – | yes |
| argument labels choose among overloads of one base name | `overload-by-label` | – | yes |
| of two applicable overloads, the one whose parameter type is not `Any` | `overload-specific-type` | – | yes |
| a non-generic overload is more specialized than a generic one | `non-generic-before-generic` | – | yes |
| a non-variadic overload is preferred to a variadic one | `non-variadic-before-variadic` | – | yes |
| of overloads that fit, the one that fills no default argument | `fewer-defaults` | – | yes |
| a closure's parameter is a name of the closure: it hides a function of its name | `closure-parameter-hides-a-function` | – | yes |

### c (17 of 17)

ISO C, 6.2.1 'Scopes of identifiers', 6.2.2 'Linkages of identifiers'.

Found by: the specification 14, a compiler 0, comparison 0, the grammar 2, the language's own tools 1. With the place it was read in the language's own tools: 1.

| Rule | Case | Versions | Engine |
|---|---|---|---|
| a function is in scope after its declaration in the file | `same-file` | all | yes |
| a function declared in an included header is the one another file defines | `external-linkage` | all | yes |
| a `static` function of the file wins over an external one of its name | `static-wins` | all | yes |
| a local variable hides a function of its name | `local-hides-function` | all | yes |
| a function put in a struct's slot is what a call through the slot reaches | `function-pointer-slot` | all | yes |
| a `typedef struct` names the struct written in it | `typedef-struct` | all | yes |
| a call before the definition, after a prototype, is of the definition | `forward-declaration` | all | yes |
| a function passed by name is a use of it | `callback-argument` | all | yes |
| `x.f = func;` puts func in the slot f | `slot-assignment` | all | yes |
| a file-level variable is read and written by the functions that name it | `global-variable` | all | yes |
| `extern T x;` is the variable another file defines | `extern-variable` | all | yes |
| a `static inline` function of an included header is called | `header-inline` | 99+ | yes |
| `a->b->c` reads the field of the struct the field points to | `struct-field-chain` | all | yes |
| a call through a parameter is no call of a function of its name | `typedef-function-pointer` | all | yes |
| the type a local is declared with is a use of it | `local-declaration-type` | – | yes |
| a `typedef` of a named type is a use of that type | `typedef-names-its-type` | – | yes |
| a struct tag and a function of one name are in different name spaces | `tag-name-space` | – | yes |

### cpp (46 of 46)

ISO C++, [basic.lookup] (unqualified, qualified and argument-dependent lookup), [class.member.lookup], [dcl.ambig.res].

Found by: the specification 32, a compiler 0, comparison 0, the grammar 4, the language's own tools 10. With the place it was read in the language's own tools: 10.

| Rule | Case | Versions | Engine |
|---|---|---|---|
| a call on an object is the member the class declares, defined in another file | `member-declared-in-header` | all | yes |
| `ns::f()` is f of the namespace | `namespace-qualified` | all | yes |
| `using namespace ns;` makes its names available unqualified | `using-namespace` | all | yes |
| a name is looked up in the namespaces around the use | `enclosing-namespace` | all | yes |
| an unqualified name in a member function is a member of the class | `implicit-this` | all | yes |
| a member not in the class is looked up in its bases | `inherited` | all | yes |
| `C::f()` is C's static member | `static-member` | all | yes |
| `T x;` then `x.m()` is T's member | `local-object` | all | yes |
| `T x(f());` declares a variable when f() is no type | `vexing-parse` | all | yes |
| overloads are told apart by the number of arguments | `overload-arity` | all | yes |
| `p->m()` is the member of the pointed-to class | `pointer-member` | all | yes |
| `using R = Repo;` names Repo | `type-alias` | 11+ | yes |
| a data member's declared type gives its members | `field-type` | all | yes |
| `auto r = T();` is a T | `auto-from-construction` | 11+ | yes |
| `auto r = f();` has f's declared result type | `auto-from-result` | 11+ | yes |
| `p->m()` goes through `std::unique_ptr<T>` to T | `unique-ptr` | 11+ | yes |
| `new T()` constructs a T | `new-expression` | all | yes |
| a `const T&` parameter is a T | `const-reference` | all | yes |
| a range-`for` variable over `std::vector<T>` is a T | `range-for` | 11+ | yes |
| `this->f_.m()` is a member of the data member's type | `this-member` | all | yes |
| a call through a base pointer is the base's member | `virtual-base` | all | yes |
| `namespace u = util;` names util | `namespace-alias` | all | yes |
| `using ns::f;` makes f available unqualified | `using-declaration` | all | yes |
| a nested class is a member of the class around it | `nested-class` | all | yes |
| a function of the file's unnamed namespace wins over another file's | `anonymous-namespace` | all | yes |
| an unqualified call finds a function in the namespace of its argument's type | `argument-dependent-lookup` | all | yes |
| a call in a lambda's body is the enclosing function's | `lambda-body` | 11+ | yes |
| a function passed by name is a use of it | `function-as-value` | all | yes |
| a static member defined out of its class is the class's | `out-of-class-static` | all | yes |
| `Page<Item>`'s member typed `T` is an Item | `template-argument` | all | yes |
| a macro between `class` and the name (`class API Env {`) leaves the class a class | `export-macro` | all | yes |
| `T x(1);` constructs a T | `constructor-arguments` | all | yes |
| `T{…}` constructs T | `brace-construction` | – | yes |
| the result type of a member function declared in its class is a use of it | `member-declaration-result` | – | yes |
| a type written as a template argument of a call is a use of it | `explicit-template-argument` | – | yes |
| a trailing result type and a `friend` name their types | `trailing-result-and-friend` | – | yes |
| a member of a derived class hides every member of its name in the base | `derived-member-hides-base` | – | yes |
| `using Base::Get;` brings the base's overloads into the derived class | `using-declaration-of-a-base-member` | – | yes |
| a member of the class comes before a function of the namespace around | `member-before-namespace` | – | yes |
| a member function's body sees members declared after it | `member-declared-later` | – | yes |
| `::f()` is the function of the global namespace, not the namespace around | `global-scope-operator` | – | yes |
| an overload whose parameter type is the argument's exactly wins over one that converts it | `exact-match-before-conversion` | – | yes |
| a function that is no template wins over a template specialization equally good | `non-template-before-template` | – | yes |
| `a + b` calls the `operator+` of a's class | `operator-call` | – | yes |
| a member of an inline namespace is a member of the namespace around | `inline-namespace` | – | yes |
| `T::f()` in a member of T: the name before `::` is the class, not the constructor of its name | `class-before-its-constructor` | – | yes |

### ruby (34 of 34)

Ruby documentation, 'Calling Methods' and Module#ancestors (method lookup), 'Modules and Classes' (constant lookup).

Found by: the specification 29, a compiler 0, comparison 0, the grammar 0, the language's own tools 5. With the place it was read in the language's own tools: 5.

| Rule | Case | Versions | Engine |
|---|---|---|---|
| a bare name that is no local is a method of self | `implicit-self` | – | yes |
| a local variable hides a method of its name | `local-hides-method` | – | yes |
| a method not in the class is looked up in its superclass | `superclass` | – | yes |
| the methods of an included module are instance methods | `include` | – | yes |
| the methods of an extended module are methods of the class itself | `extend` | – | yes |
| `def self.m` is called through the class | `class-method` | – | yes |
| `C.new.m` is C's instance method | `new-then-method` | – | yes |
| a local assigned `C.new` has C's methods | `local-typed-by-value` | – | yes |
| `super` is the method of the same name further up | `super` | – | yes |
| `A::B` is the constant B of A | `nested-constant` | – | yes |
| a constant is looked up in the modules written around the use | `lexical-constant` | – | yes |
| a class opened again in another file is the same class | `reopened-class` | – | yes |
| an instance variable assigned `C.new` in initialize has C's methods | `instance-variable-typed` | – | yes |
| the methods of a prepended module come before the class's | `prepend` | – | yes |
| `def self.f` in a module is called through the module | `module-function` | – | yes |
| a method under `class << self` is a class method | `singleton-class` | – | yes |
| an `attr_reader` gives what its instance variable holds | `attr-reader-typed` | – | yes |
| a class written `X = Struct.new(…) do … end` has the methods of its block | `struct-with-block` | – | yes |
| a constant assigned a class names that class | `constant-alias` | – | yes |
| `self.class.m` is a class method | `self-class` | – | yes |
| `super(x)` is the method of the same name further up | `super-with-arguments` | – | yes |
| `r&.m` is r's m | `safe-navigation` | – | yes |
| a method named with `?` is called like any other | `predicate-name` | – | yes |
| `class A < Shop::Base` inherits from the namespaced class | `namespaced-superclass` | – | yes |
| a module of a required file can be included | `include-across-files` | – | yes |
| `C.new(…)` constructs C | `new-with-arguments` | – | yes |
| a top-level method of a required file is in scope | `top-level-method` | – | yes |
| a call in a block is the enclosing method's | `block-body` | – | yes |
| a private method is called with an implicit receiver | `private-method` | – | yes |
| of two included modules with a method of one name, the one included last is found first | `last-include-first` | – | yes |
| a method of the class comes before an included module's | `class-before-included-module` | – | yes |
| a constant of a module around the use comes before one of the class's ancestors | `lexical-constant-before-ancestor` | – | yes |
| a block's parameter is a name of the block: it hides a method of its name there | `block-parameter-hides-a-method` | – | yes |
| a name is a local only after the assignment that makes it one: before it, it is a method call | `local-only-after-assignment` | – | yes |

### php (37 of 37)

PHP Manual, 'Namespaces: Name resolution rules', 'Classes and Objects' (inheritance, traits, static).

Found by: the specification 31, a compiler 2, comparison 0, the grammar 1, the language's own tools 3. With the place it was read in the language's own tools: 3.

| Rule | Case | Versions | Engine |
|---|---|---|---|
| `use N\C;` binds C | `use` | – | yes |
| `use N\C as D;` binds D | `use-alias` | – | yes |
| `\N\C` names C whatever the current namespace | `fully-qualified` | – | yes |
| `Sub\C` is relative to the current namespace | `qualified-relative` | – | yes |
| an unqualified class name is of the current namespace | `same-namespace` | – | yes |
| `$this->m()` is the class's method | `this` | – | yes |
| a method not in the class is looked up in its parent | `inherited` | – | yes |
| `parent::m()` is the parent class's m | `parent` | – | yes |
| `self::m()` and `static::m()` are the class's own | `self-static` | – | yes |
| the methods of a used trait are methods of the class | `trait` | – | yes |
| a property's declared type gives its members | `typed-property` | – | yes |
| a variable assigned `new C()` has C's methods | `new-then-method` | – | yes |
| an unqualified function call falls back to the global function | `function-fallback` | – | yes |
| `use function N\f;` binds f | `use-function` | – | yes |
| a promoted constructor parameter is a typed property | `constructor-promotion` | – | yes |
| a method's declared result gives the members of what it returns | `return-type` | – | yes |
| a `?T` parameter is a T | `nullable-parameter` | – | yes |
| `$r?->m()` is $r's m | `nullsafe` | – | yes |
| a closure's `use ($x)` variable keeps its type | `closure-use` | – | yes |
| an arrow function's typed parameter has that type | `arrow-function-parameter` | – | yes |
| a call through an interface type is the interface's method | `interface-typed` | – | yes |
| `use N\{A, B};` binds both | `group-use` | – | yes |
| `C::class` is a reference to C | `class-constant-fetch` | – | yes |
| `new C(…)` constructs C | `new` | – | yes |
| `parent::__construct()` is the parent's constructor | `parent-constructor` | – | yes |
| an unqualified function call is first a function of the current namespace | `namespaced-function` | – | yes |
| an enum case has the enum's methods | `enum-method` | – | yes |
| `new static()` / `new self()` construct the class | `static-return` | – | yes |
| a call of an abstract method through `$this` is the declaring class's | `abstract-method` | – | yes |
| `C::m()` is C's static method | `static-method-via-class` | – | yes |
| a method passed as `[$this, 'm']` is a use of m | `callable-string` | – | yes |
| `\Name` is the global class, never one a namespace of the project declares | `global-class-is-not-a-namespaced-one` | – | yes |
| `$x instanceof T` and `catch (T $e)` are uses of T | `instanceof-and-catch` | – | yes |
| an anonymous function's parameters and result name their types | `closure-signature` | – | yes |
| an unqualified class name is of the current namespace only: unlike a function, it does not fall back to the global one | `class-name-has-no-global-fallback` | – | yes |
| `A::hello insteadof B` picks A's method of two used traits | `trait-insteadof` | – | yes |
| a method a trait brings comes before an inherited one, and the class's own before both | `trait-before-inherited` | – | yes |

1119 of 1270 rules hold
