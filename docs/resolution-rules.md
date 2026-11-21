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

Measured 2026-11-21 with graphlore-engine 0.5.69: **625 of 625 rules hold**
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
(`benchmarks/verify_cases.py`: 625 of 625 accepted). The Versions column is
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

### python (38 of 38)

The Python Language Reference, 'Naming and binding' (4.2) and 'The import system'.

Found by: the specification 36, a compiler 1, comparison 0, the grammar 1, the language's own tools 0. With the place it was read in the language's own tools: 0.

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

### go (53 of 53)

The Go Programming Language Specification, 'Declarations and scope', 'Selectors', 'Method sets'.

Found by: the specification 23, a compiler 8, comparison 2, the grammar 5, the language's own tools 15. With the place it was read in the language's own tools: 15.

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

### kotlin (53 of 53)

Kotlin Language Specification, 'Scopes and identifiers', 'Overload resolution' (receivers).

Found by: the specification 31, a compiler 9, comparison 0, the grammar 0, the language's own tools 13. With the place it was read in the language's own tools: 13.

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

### scala (32 of 32)

Scala Language Specification, chapter 2 'Identifiers, Names and Scopes', 4.7 'Import Clauses'.

Found by: the specification 20, a compiler 8, comparison 0, the grammar 4, the language's own tools 0. With the place it was read in the language's own tools: 0.

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

### csharp (70 of 70)

C# Language Specification, 'Namespace and type names', 'Member lookup', 'Extension method invocations'.

Found by: the specification 39, a compiler 6, comparison 6, the grammar 1, the language's own tools 18. With the place it was read in the language's own tools: 18.

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

### rust (70 of 70)

The Rust Reference, 'Names' (name resolution, scopes, paths), 'Method call expressions', 'Field access expressions'.

Found by: the specification 37, a compiler 10, comparison 6, the grammar 2, the language's own tools 15. With the place it was read in the language's own tools: 15.

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

### swift (43 of 43)

The Swift Programming Language, 'Declarations', 'Extensions', 'Protocols' (member lookup is the compiler's).

Found by: the specification 33, a compiler 5, comparison 0, the grammar 5, the language's own tools 0. With the place it was read in the language's own tools: 0.

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

### c (16 of 16)

ISO C, 6.2.1 'Scopes of identifiers', 6.2.2 'Linkages of identifiers'.

Found by: the specification 14, a compiler 0, comparison 0, the grammar 2, the language's own tools 0. With the place it was read in the language's own tools: 0.

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

### cpp (36 of 36)

ISO C++, [basic.lookup] (unqualified, qualified and argument-dependent lookup), [class.member.lookup], [dcl.ambig.res].

Found by: the specification 32, a compiler 0, comparison 0, the grammar 4, the language's own tools 0. With the place it was read in the language's own tools: 0.

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

### ruby (29 of 29)

Ruby documentation, 'Calling Methods' and Module#ancestors (method lookup), 'Modules and Classes' (constant lookup).

Found by: the specification 29, a compiler 0, comparison 0, the grammar 0, the language's own tools 0. With the place it was read in the language's own tools: 0.

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

### php (34 of 34)

PHP Manual, 'Namespaces: Name resolution rules', 'Classes and Objects' (inheritance, traits, static).

Found by: the specification 31, a compiler 2, comparison 0, the grammar 1, the language's own tools 0. With the place it was read in the language's own tools: 0.

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

625 of 625 rules hold
