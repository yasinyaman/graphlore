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

Measured 2026-11-20 with graphlore-engine 0.5.45: **356 of 406 rules hold**.

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

The cases that fail are the engine's open defects of this kind. They are
few rules written in many languages, so they are listed by rule:

- **A type written in an expression is not read.** A cast used at once:
  Java and C# `((Repo) o).find()`, C# / TypeScript / Kotlin `x as Repo`,
  Swift `x as! Repo`, Go `x.(*Repo)`. (A cast assigned to a local is read.)
- **A null-safe call.** Kotlin `r?.find()` and C# `r?.Find()` name nothing;
  Swift's, Ruby's `&.` and PHP's `?->` do.
- **A binding that takes an optional apart.** Rust `if let Some(r) = opt`
  and Swift `if let r = opt` / `guard let r = opt` on a parameter typed
  `Option<Repo>` / `Repo?`; Scala `case r: Repo =>`; Java's resource
  variable in `try (Repo r = open())`.
- **A member of an enum's value.** `Color.RED.label()` in Java, Kotlin,
  Swift and PHP stops at the enum.
- **A type alias.** Kotlin and Swift `typealias Store = Repo`, a Ruby
  constant assigned a class, C++ `namespace u = util;`.
- **A variable outside a function, typed by its value.** Go
  `var store = &Repo{}`, a Kotlin top-level `val store = Repo()`, a Ruby
  instance variable assigned in `initialize`; Scala `val r = new Repo`, in
  a method too.
- **A function named, not called.** Java `this::helper` and PHP
  `[$this, 'handle']` are no use of the method; Kotlin `::handle` is a
  `calls` edge where the other languages write `indirect_call`; Scala's
  `r.size` on a method without parentheses is a `references` edge.
- **Paths through the module itself.** Rust `self::f()`, and `super::f()`
  in an inline module; a C# `global using`; `m.Repo` as a type through a
  TypeScript `import * as m`; the members of a JavaScript
  `await import('./m.js')`.
- **Objects.** A method of a JavaScript object literal called through the
  object (`api.fetchAll()`), a method assigned to `F.prototype` on a local
  assigned `new F()`, a TypeScript getter read as a value, a TypeScript
  optional parameter `r?: T`.
- **Shadowing.** A second Rust `let` of a name does not replace the first;
  a C parameter does not hide a function of its name.
- **`super`.** Ruby's bare `super` and `super(x)` are no call of the method
  further up.
- **The rest.** A C++ range-`for` over `std::vector<T>` with `auto&`, a
  C++ nested class written `Outer::Inner&` as a parameter type, PHP
  `C::class` and `new static()`, and a generic Java class the project names
  `Box`, read as a library's wrapper.

The first 185 cases were about scopes, imports and member lookup, and nine
failed. The second round (221 more) is mostly about the places a type is
written other than a declaration — a cast, a pattern, a loop, an alias, a
wrapper — and what a language has of its own; 41 of those fail. The first
round's count says the engine follows the scoping rules; the second says
where it stops reading types.

Four cases failed for the cases' own sake and were corrected: a decorator
is a `references` edge, not `uses`; a C local declared as a function
pointer is a definition of its own that the call reaches; generic cases
named `Box` / `value` / `get` ran into the last defect above, which is now
a case of its own; and an object literal's method named `get` is a
library's name to the engine, so the case names it `fetchAll`.

## The rules

### python (34 of 34)

The Python Language Reference, 'Naming and binding' (4.2) and 'The import system'.

| Rule | Case | Engine |
|---|---|---|
| a module-level function is in scope in every function of the module | `module-function` | yes |
| a nested function sees the names of the function around it | `enclosing-scope` | yes |
| a name bound in a function is local to it and hides the module's | `local-hides-global` | yes |
| `from m import f` binds f to m's definition | `from-import` | yes |
| `from m import f as g` binds g | `import-alias` | yes |
| `import pkg.m` then `pkg.m.f()` is m's f | `module-attribute` | yes |
| `from .m import f` is relative to the package of the importer | `relative-import` | yes |
| `from m import *` binds m's public names | `star-import` | yes |
| `self.m()` is the class's method | `self-method` | yes |
| an attribute not on the class is looked up on its bases (the MRO) | `inherited-method` | yes |
| `super().m()` is the next class's m | `super` | yes |
| calling a class constructs it | `class-call` | yes |
| a parameter annotated with a class has that class's methods | `annotated-parameter` | yes |
| `self.repo = Repo()` in __init__ types self.repo | `attribute-assigned-in-init` | yes |
| `Cls.create()` is the class's own method | `classmethod-via-class` | yes |
| a local assigned a construction has that class's methods | `local-typed-by-value` | yes |
| a decorator is a use of the name it is written with | `decorator` | yes |
| a call's annotated result gives the methods of what it returns | `return-annotation` | yes |
| an attribute annotated in the class body has that type | `class-attribute-annotation` | yes |
| a property read is what its getter returns | `property` | yes |
| `with f() as x` binds x to what f returns | `with-as` | yes |
| a loop variable over `list[T]` is a T | `for-over-annotated-list` | yes |
| a comprehension's variable over `list[T]` is a T | `comprehension` | yes |
| `import pkg.m as mm` binds mm to the module | `module-alias` | yes |
| `from pkg import m` binds the submodule m | `submodule-import` | yes |
| a name the package's __init__ imports is a name of the package | `package-reexport` | yes |
| an import inside a function binds the name in that function | `function-local-import` | yes |
| of two bases with the method, the first listed wins | `mro-first-base` | yes |
| a method only the second base has is found there | `mro-second-base` | yes |
| a class nested in a class is reached through it | `nested-class` | yes |
| a static method is found on the instance too | `staticmethod-via-self` | yes |
| a call in a class body is the class's | `class-body-call` | yes |
| a function passed by name is a use of it | `function-as-value` | yes |
| a base imported under another name is still the base | `inherited-from-aliased-import` | yes |

### javascript (26 of 29)

ECMAScript Language Specification, 'Executable Code and Execution Contexts' (scopes), 'Modules'.

| Rule | Case | Engine |
|---|---|---|
| a function declaration is in scope in its whole enclosing scope | `hoisting` | yes |
| a `const` of a block hides an outer function of its name | `block-const-hides` | yes |
| an inner function sees the functions of the one around it | `closure` | yes |
| `import { f } from './m.js'` binds m's export | `named-import` | yes |
| `import { f as g }` binds g | `import-alias` | yes |
| a default import is the module's default export | `default-import` | yes |
| `import * as m` then `m.f()` | `namespace-import` | yes |
| `export { f } from './m.js'` passes m's f on | `re-export` | yes |
| `const { f } = require('./m')` binds what m exports as f | `commonjs-destructure` | yes |
| `this.m()` in a class method is the class's m | `this-method` | yes |
| a method not on the class is looked up on what it extends | `inherited-method` | yes |
| `super.m()` is the base class's m | `super` | yes |
| a local assigned `new C()` has C's methods | `new-then-method` | yes |
| a function assigned to `F.prototype` is a method of F's instances | `prototype-method` | **no**: no run -calls-> render |
| a `const` assigned an arrow function is a function of its scope | `arrow-const` | yes |
| a `const` assigned a function expression is a function | `function-expression` | yes |
| a method of an object literal is called through the object | `object-literal-method` | **no**: no run -calls-> fetchAll |
| `this` in an object literal's method is the object | `object-literal-this` | yes |
| `module.exports = f` is what `require` gives | `commonjs-default` | yes |
| `exports.f = …` is a member of what `require` gives | `commonjs-member` | yes |
| `C.m()` is C's static method | `static-method` | yes |
| a default-exported class is what a default import names | `default-export-class` | yes |
| an exported `const` arrow function is importable by its name | `exported-arrow` | yes |
| a function passed by name is a use of it | `function-as-value` | yes |
| a parameter hides an import of its name | `parameter-hides-import` | yes |
| `var X = (function () { function X() {} return X })()` names the function written in it | `module-pattern` | yes |
| `await import('./m.js')` is the module | `dynamic-import` | **no**: no run -calls-> f |
| an arrow function in a class field sees the class's `this` | `class-field-arrow` | yes |
| a local assigned `new C()` keeps its type in a nested callback | `for-of-new` | yes |

### typescript (24 of 28)

TypeScript Handbook, 'Modules', 'Classes', 'Generics' (types are the compiler's, tsc).

| Rule | Case | Engine |
|---|---|---|
| a parameter's declared type gives its members | `typed-parameter` | yes |
| a call through an interface type is the interface's method | `interface-typed` | yes |
| `constructor(private repo: Repo)` declares a typed field | `parameter-property` | yes |
| a field's declared type gives its members | `typed-field` | yes |
| `Page<Item>`'s `head: T` is an Item | `generic-argument` | yes |
| a call's declared return type gives the members of its result | `return-type` | yes |
| `import type { T }` is a reference to T | `import-type` | yes |
| an import through `index.ts` reaches the file that defines the name | `barrel` | yes |
| `a?.m()` is a's m | `optional-chaining` | yes |
| an optional parameter `r?: T` has type T | `optional-parameter` | **no**: no run -calls-> Repo.find |
| an enum member is a member of its enum | `enum-member` | yes |
| `Cls.make()` is the class's static method | `static-method` | yes |
| `import * as m` then `m.Repo` as a type | `namespace-import-type` | **no**: no run -calls-> Repo.find |
| an element of `T[]` is a T | `array-element` | yes |
| a `for … of` variable over `T[]` is a T | `for-of` | yes |
| `await` of a `Promise<T>` is a T | `await-promise` | yes |
| a getter's declared type gives the members of what is read | `getter` | **no**: no Svc.run -calls-> Repo.find |
| `x as T` is a T | `as-cast` | **no**: no run -calls-> Repo.find |
| `x!` keeps x's type | `non-null` | yes |
| a `readonly` field's declared type gives its members | `readonly-field` | yes |
| an interface that extends another is its subtype | `interface-extends` | yes |
| a class that implements an interface names it | `implements` | yes |
| a call through an abstract class is the abstract class's method | `abstract-base` | yes |
| a type alias is a type of its own, referenced where it is written | `type-alias` | yes |
| `N.f()` is a function a namespace exports | `namespace` | yes |
| `export { default as X } from './x'` passes the default on | `default-reexport` | yes |
| `const r: T = …` is a T whatever it is assigned | `local-typed-by-declaration` | yes |
| a value of `Map<K, V>` is a V | `map-value` | yes |

### go (21 of 23)

The Go Programming Language Specification, 'Declarations and scope', 'Selectors', 'Method sets'.

| Rule | Case | Engine |
|---|---|---|
| a package-level function is in scope in every file of the package | `package-scope` | yes |
| `pkg.F()` is F of the imported package | `imported-package` | yes |
| an import can be renamed | `import-alias` | yes |
| a method call on a typed local is the type's method | `method-on-local` | yes |
| the methods of an embedded type are promoted | `embedded-struct` | yes |
| a call through an interface type is the interface's method | `interface-method` | yes |
| the declared result of a call gives the methods of the result | `result-type` | yes |
| a local declared in a function hides a package function of its name | `local-hides-function` | yes |
| a method of a struct field's type | `field-method` | yes |
| `x, err := f()` types x by f's first result | `multiple-results` | yes |
| a `range` value over `[]*T` is a *T | `range-slice` | yes |
| an element of `[]*T` is a *T | `slice-index` | yes |
| a value of `map[K]*T` is a *T | `map-value` | yes |
| a package-level variable's value gives its type | `package-variable` | **no**: no Run -calls-> Repo.Find |
| `x.(*T)` is a *T | `type-assertion` | **no**: no Run -calls-> Repo.Find |
| `go f()` and `defer g()` are calls | `go-and-defer` | yes |
| a function passed by name is a use of it | `function-as-value` | yes |
| a method declared on a named slice type | `named-slice-type` | yes |
| the methods of an interface embedded in an interface are its own | `embedded-interface` | yes |
| a method call on a value of an imported package's type | `imported-type` | yes |
| a test file of the package sees the package's functions | `test-file` | yes |
| a method with a value receiver is called on a value | `value-receiver` | yes |
| a function literal sees the functions of the package | `closure` | yes |

### java (31 of 36)

The Java Language Specification, 6.4 'Shadowing and Obscuring', 15.12 'Method Invocation Expressions'.

| Rule | Case | Engine |
|---|---|---|
| an unqualified method name is a method of the class | `implicit-this` | yes |
| an unqualified method name is also searched in the superclasses | `inherited` | yes |
| `super.m()` is the superclass's m | `super` | yes |
| a class of the same package needs no import | `same-package` | yes |
| `import p.C;` binds C | `single-type-import` | yes |
| `import p.*;` makes p's types available | `on-demand-import` | yes |
| `import static p.C.m;` binds m | `static-import` | yes |
| `C.m()` is C's static method | `static-via-class` | yes |
| a field's declared type gives its members | `field-type` | yes |
| a local variable shadows a field of its name | `local-hides-field` | yes |
| overloads are told apart by the number of arguments | `overload-arity` | yes |
| `new C(…)` calls C's constructor | `constructor` | yes |
| a default method of an implemented interface is inherited | `interface-default` | yes |
| an inner class sees the methods of the class around it | `outer-method` | yes |
| a method's declared return type gives the members of its result | `chained-result` | yes |
| `Page<Item>`'s `T lead()` gives an Item | `generic-argument` | yes |
| a project type's own `get()` is its method, whatever libraries name so | `own-accessor-name` | yes |
| a generic class the project names `Box` is the project's class | `class-named-like-a-wrapper` | **no**: no A.run -calls-> Box.lead; A.run -calls-> Item.size |
| an enhanced `for` variable is declared with its type | `for-each` | yes |
| `List<T>.get(i)` is a T | `list-element` | yes |
| `var r = new C();` has type C | `var` | yes |
| `(T) x` is a T | `cast` | **no**: no A.run -calls-> Repo.find |
| `o instanceof T r` binds r as a T | `instanceof-pattern` | yes |
| a resource variable is declared with its type | `try-with-resources` | **no**: no A.run -calls-> Repo.find |
| `this.f.m()` is a member of the field's type | `this-field` | yes |
| a static field's declared type gives its members | `static-field` | yes |
| a call in a lambda's body is the enclosing method's | `lambda-body` | yes |
| `this::m` and `C::m` are uses of m | `method-reference` | **no**: no A.run -indirect_call-> A.helper |
| overloads of one arity are told apart by the argument's type | `overload-by-type` | yes |
| a nested class is named through the class around it | `nested-static-class` | yes |
| an enum constant has the enum's methods | `enum-method` | **no**: no A.run -calls-> Color.label |
| a call through an interface type is the interface's method | `interface-typed` | yes |
| `p.C.m()` names C by its package | `fully-qualified` | yes |
| `I.m()` is a static method of the interface | `interface-static` | yes |
| `new R(…)` constructs a record | `record` | yes |
| a method of an anonymous class sees the methods of the class around it | `anonymous-class` | yes |

### kotlin (23 of 30)

Kotlin Language Specification, 'Scopes and identifiers', 'Overload resolution' (receivers).

| Rule | Case | Engine |
|---|---|---|
| a top-level function of the same package needs no import | `same-package` | yes |
| an import binds a top-level function of another package | `import` | yes |
| `import a.b as c` binds c | `import-alias` | yes |
| a method call on a typed value is the type's member | `member` | yes |
| an extension function is called like a member of its receiver type | `extension` | yes |
| a member wins over an extension of the same name | `member-wins` | yes |
| `C.make()` is a member of C's companion object | `companion` | yes |
| an `object` declaration's members are called through its name | `object` | yes |
| a `val` of the primary constructor is a typed property | `constructor-property` | yes |
| inside `x.apply { … }` an unqualified name is x's member | `receiver-lambda` | yes |
| `it` in a lambda given to a collection's method is an element | `it` | yes |
| an unqualified name in a class is also searched in its supertypes | `inherited` | yes |
| a `val` assigned a construction has that type | `local-typed-by-value` | yes |
| `Page<Item>`'s `val head: T` is an Item | `generic-argument` | yes |
| `r?.m()` is r's m | `safe-call` | **no**: no run -calls-> Repo.find |
| inside `with(x) { … }` an unqualified name is x's member | `with` | yes |
| `x.let { it.m() }`: `it` is x | `let` | **no**: no run -calls-> Repo.find |
| a top-level `val` assigned a construction has that type | `top-level-property` | **no**: no run -calls-> Repo.find |
| a `lateinit var`'s declared type gives its members | `lateinit` | yes |
| a `for` variable over `List<T>` is a T | `for-loop` | yes |
| `x as T` is a T | `cast` | **no**: no run -calls-> Repo.find |
| a function's declared result gives the members of what it returns | `return-type` | yes |
| a typealias names the type it is written for | `typealias` | **no**: no run -calls-> Repo.find |
| `C(…)` calls C's constructor | `constructor` | yes |
| `super.m()` is the supertype's m | `super` | yes |
| a method an interface defines is inherited | `interface-default` | yes |
| a call through an interface type is the interface's member | `interface-typed` | yes |
| `::f` is a use of f | `function-reference` | **no**: no run -indirect_call-> handle |
| a nested class is named through the class around it | `nested-class` | yes |
| an enum entry has the enum's members | `enum-entry` | **no**: no run -calls-> Color.label |

### scala (16 of 20)

Scala Language Specification, chapter 2 'Identifiers, Names and Scopes', 4.7 'Import Clauses'.

| Rule | Case | Engine |
|---|---|---|
| `Obj.f()` is a member of the object | `object-method` | yes |
| a method call on a typed value is the type's member | `typed-parameter` | yes |
| a method of a mixed-in trait is inherited | `trait-method` | yes |
| `import p._` makes p's members available | `wildcard-import` | yes |
| `import p.{A => B}` binds B | `import-rename` | yes |
| an unqualified name in a class is a member of it | `implicit-this` | yes |
| `C(…)` constructs a case class | `case-class` | yes |
| a `val` assigned `new C` has that type | `local-typed-by-value` | **no**: no Main.run -calls-> Repo.find |
| a `val` assigned `new C()` has that type | `new-with-parentheses` | **no**: no Main.run -calls-> Repo.find |
| a `val` with a declared type has that type | `typed-val` | yes |
| a method's declared result gives the members of what it returns | `return-type` | yes |
| `case r: T` binds r as a T | `pattern-typed` | **no**: no Main.run -calls-> Repo.find |
| a method called without parentheses is still called | `no-parentheses` | **no**: no Main.run -calls-> Repo.size |
| a call through a trait type is the trait's member | `trait-typed` | yes |
| `import p.Obj.f` binds f | `member-import` | yes |
| `super.m()` is the parent's m | `super` | yes |
| `C(…)` is the companion object's `apply` | `companion-apply` | yes |
| `Page[Item]`'s `head: T` is an Item | `generic-argument` | yes |
| `class A extends B` names its parent | `extends` | yes |
| a class of the same package in another file needs no import | `same-package` | yes |

### csharp (31 of 35)

C# Language Specification, 'Namespace and type names', 'Member lookup', 'Extension method invocations'.

| Rule | Case | Engine |
|---|---|---|
| a name is looked up in every namespace around the use | `enclosing-namespace` | yes |
| `using N;` makes N's types available | `using` | yes |
| `namespace N;` holds the whole file | `file-scoped-namespace` | yes |
| `using R = N.C;` binds R | `using-alias` | yes |
| `using static N.C;` makes C's static members available | `using-static` | yes |
| an unqualified name in a class is a member of it | `implicit-this` | yes |
| `base.M()` is the base class's M | `base` | yes |
| an extension method is called like a member of its `this` parameter's type | `extension` | yes |
| the members of a partial type are those of all its parts | `partial` | yes |
| `var x = new C();` has type C | `var-new` | yes |
| a property's declared type gives its members | `property-type` | yes |
| overloads are told apart by the number of arguments | `overload-arity` | yes |
| `C.M()` is C's static method | `static-via-class` | yes |
| a call through an interface type is the interface's member | `interface-typed` | yes |
| a nested type is a member of the type around it | `nested-type` | yes |
| `Page<Item>`'s `T Head` is an Item | `generic-argument` | yes |
| a `foreach` variable declared with a type has it | `foreach-typed` | yes |
| a `foreach (var r in xs)` variable over `List<T>` is a T | `foreach-var` | yes |
| `o is T r` binds r as a T | `is-pattern` | yes |
| `(T)x` is a T | `cast` | **no**: no A.Run -calls-> Repo.Find |
| `x as T` is a T | `as-cast` | **no**: no A.Run -calls-> Repo.Find |
| `r?.M()` is r's M | `null-conditional` | **no**: no A.Run -calls-> Repo.Find |
| `T r = new();` constructs a T | `target-typed-new` | yes |
| `await` of a `Task<T>` is a T | `await-task` | yes |
| a static property's declared type gives its members | `static-property` | yes |
| a call in a lambda's body is the enclosing method's | `lambda-body` | yes |
| a method passed by name is a use of it | `method-group` | yes |
| a local function is called by its name in the method | `local-function` | yes |
| `this.M()` is the class's M | `this` | yes |
| an unqualified name in a class is also searched in its base | `inherited` | yes |
| a class's primary constructor parameter is a typed name of the class | `primary-constructor` | yes |
| `global using N;` in one file holds for every file | `global-using` | **no**: no A.Run -calls-> Repo.Find |
| an extension written for an interface is a member of the classes that implement it | `extension-on-interface` | yes |
| an enum member is a member of its enum | `enum-member` | yes |
| `new R(…)` constructs a record, which has methods like a class | `record` | yes |

### rust (33 of 37)

The Rust Reference, 'Names' (name resolution, scopes, paths), 'Method call expressions', 'Field access expressions'.

| Rule | Case | Engine |
|---|---|---|
| `use crate::m::f;` binds f | `use-path` | yes |
| `use m::f as g;` binds g | `use-alias` | yes |
| `use m::*;` binds m's public names | `glob-import` | yes |
| `crate::m::f()` names f by its path | `qualified-path` | yes |
| `super::f()` is f of the parent module | `super` | **no**: no inner.run -calls-> f |
| `pub use a::T;` makes T a name of the re-exporting module | `re-export` | yes |
| `T::f()` is an associated function of T | `associated-function` | yes |
| a method call on a local is a method of its type | `method-on-local` | yes |
| `Self::f()` is the type the impl is for | `self-type` | yes |
| `x.f` with no call is the field f, whatever methods are named f | `field-not-method` | yes |
| a trait's method is a method of the types that implement it | `trait-method` | yes |
| an impl block can be written in another module than the type | `impl-in-another-file` | yes |
| a later `let` of the same name is the name from there on | `shadowing` | **no**: no run -calls-> Repo.find has run -calls-> Other.find |
| the arguments of a macro are expressions like any other | `macro-arguments` | yes |
| `E::V(…)` constructs a variant of E | `enum-variant` | yes |
| a field's declared type gives its methods | `field-type` | yes |
| `f()?` of a `Result<T, E>` is a T | `question-mark` | yes |
| `opt.unwrap()` of an `Option<T>` is a T | `option-unwrap` | yes |
| `if let Some(r) = opt` binds r as the option's T | `if-let-some` | **no**: no run -calls-> Repo.find |
| a method call goes through `Arc<T>` / `Box<T>` to T | `smart-pointer` | yes |
| a `for` variable over `&Vec<T>` is a T | `vec-iteration` | yes |
| `w.0` of `struct W(T)` is a T | `tuple-struct-field` | yes |
| a `static`'s declared type gives its methods | `static-item` | yes |
| a `&T` / `&mut T` parameter is a T | `reference-parameter` | yes |
| a call through `&dyn Trait` is the trait's method | `dyn-trait` | yes |
| a call on a `T: Trait` value is the trait's method | `generic-bound` | yes |
| a trait's default method is a method of every implementor | `trait-default-method` | yes |
| `a::b::f()` walks nested modules | `nested-module-path` | yes |
| `self::f()` is f of the current module | `self-path` | **no**: no run -calls-> f |
| `use super::*;` in a test module binds the parent's names | `use-super-glob` | yes |
| `mod x;` can be the file `x/mod.rs` | `mod-rs` | yes |
| `use a::{self, f};` binds the module and the name | `use-self-in-braces` | yes |
| a function passed by name is a use of it | `function-as-value` | yes |
| a call in a closure's body is the enclosing function's | `closure-body` | yes |
| a method returning `Self` keeps the type through a chain | `builder-chain` | yes |
| a method of an enum's impl is called on its values | `enum-method` | yes |
| `T { … }` constructs a T | `struct-literal` | yes |

### swift (25 of 30)

The Swift Programming Language, 'Declarations', 'Extensions', 'Protocols' (member lookup is the compiler's).

| Rule | Case | Engine |
|---|---|---|
| a method call on a typed value is the type's method | `method-on-parameter` | yes |
| an unqualified name in a type is a member of it | `implicit-self` | yes |
| an extension's methods are members of the extended type | `extension` | yes |
| `T.make()` is T's static method | `static` | yes |
| `T(…)` calls T's initializer | `initializer` | yes |
| `super.m()` is the superclass's m | `super` | yes |
| a member not on the class is looked up on its superclass | `inherited` | yes |
| a call through a protocol type is the protocol's requirement | `protocol-typed` | yes |
| a protocol extension's method is a member of every conforming type | `protocol-extension` | yes |
| a stored property's declared type is a use of that type, and gives its members | `property-type` | yes |
| `.name` where a type is expected is a static member of that type | `implicit-member` | yes |
| a `let` assigned an initializer call has that type | `local-typed-by-value` | yes |
| a nested type is a member of the type around it | `nested-type` | yes |
| `r?.m()` is r's m | `optional-chaining` | yes |
| `if let r = opt` binds r as the optional's type | `if-let` | **no**: no run -calls-> Repo.find |
| `guard let r = opt` binds r for the rest of the scope | `guard-let` | **no**: no run -calls-> Repo.find |
| a `for` variable over `[T]` is a T | `for-in` | yes |
| an element of `[T]` is a T | `subscript` | yes |
| `x as! T` is a T | `as-cast` | **no**: no run -calls-> Repo.find |
| a function's declared result gives the members of what it returns | `return-type` | yes |
| a computed property's declared type gives its members | `computed-property` | yes |
| `T.shared.m()` goes through a static property's type | `static-property` | yes |
| a typealias names the type it is written for | `typealias` | **no**: no run -calls-> Repo.find |
| `Page<Item>`'s `head: T` is an Item | `generic-argument` | yes |
| a method an extension adds with a conformance is a member of the type | `conformance-in-extension` | yes |
| an enum case has the enum's methods | `enum-method` | **no**: no run -calls-> Shape.area |
| `T.make()` is a `class func` of T | `class-func` | yes |
| a function nested in a function is called by its name there | `nested-function` | yes |
| a call in a closure's body is the enclosing function's | `closure-body` | yes |
| a function passed by name is a use of it | `function-as-value` | yes |

### c (13 of 14)

ISO C, 6.2.1 'Scopes of identifiers', 6.2.2 'Linkages of identifiers'.

| Rule | Case | Engine |
|---|---|---|
| a function is in scope after its declaration in the file | `same-file` | yes |
| a function declared in an included header is the one another file defines | `external-linkage` | yes |
| a `static` function of the file wins over an external one of its name | `static-wins` | yes |
| a local variable hides a function of its name | `local-hides-function` | yes |
| a function put in a struct's slot is what a call through the slot reaches | `function-pointer-slot` | yes |
| a `typedef struct` names the struct written in it | `typedef-struct` | yes |
| a call before the definition, after a prototype, is of the definition | `forward-declaration` | yes |
| a function passed by name is a use of it | `callback-argument` | yes |
| `x.f = func;` puts func in the slot f | `slot-assignment` | yes |
| a file-level variable is read and written by the functions that name it | `global-variable` | yes |
| `extern T x;` is the variable another file defines | `extern-variable` | yes |
| a `static inline` function of an included header is called | `header-inline` | yes |
| `a->b->c` reads the field of the struct the field points to | `struct-field-chain` | yes |
| a call through a parameter is no call of a function of its name | `typedef-function-pointer` | **no**: has run -calls-> a.c:handler |

### cpp (27 of 30)

ISO C++, [basic.lookup] (unqualified, qualified and argument-dependent lookup), [class.member.lookup], [dcl.ambig.res].

| Rule | Case | Engine |
|---|---|---|
| a call on an object is the member the class declares, defined in another file | `member-declared-in-header` | yes |
| `ns::f()` is f of the namespace | `namespace-qualified` | yes |
| `using namespace ns;` makes its names available unqualified | `using-namespace` | yes |
| a name is looked up in the namespaces around the use | `enclosing-namespace` | yes |
| an unqualified name in a member function is a member of the class | `implicit-this` | yes |
| a member not in the class is looked up in its bases | `inherited` | yes |
| `C::f()` is C's static member | `static-member` | yes |
| `T x;` then `x.m()` is T's member | `local-object` | yes |
| `T x(f());` declares a variable when f() is no type | `vexing-parse` | yes |
| overloads are told apart by the number of arguments | `overload-arity` | yes |
| `p->m()` is the member of the pointed-to class | `pointer-member` | yes |
| `using R = Repo;` names Repo | `type-alias` | yes |
| a data member's declared type gives its members | `field-type` | yes |
| `auto r = T();` is a T | `auto-from-construction` | yes |
| `auto r = f();` has f's declared result type | `auto-from-result` | yes |
| `p->m()` goes through `std::unique_ptr<T>` to T | `unique-ptr` | yes |
| `new T()` constructs a T | `new-expression` | yes |
| a `const T&` parameter is a T | `const-reference` | yes |
| a range-`for` variable over `std::vector<T>` is a T | `range-for` | **no**: no run -calls-> Repo.Find |
| `this->f_.m()` is a member of the data member's type | `this-member` | yes |
| a call through a base pointer is the base's member | `virtual-base` | yes |
| `namespace u = util;` names util | `namespace-alias` | **no**: no run -calls-> util.twice |
| `using ns::f;` makes f available unqualified | `using-declaration` | yes |
| a nested class is a member of the class around it | `nested-class` | **no**: no run -calls-> Outer.Inner.Find |
| a function of the file's unnamed namespace wins over another file's | `anonymous-namespace` | yes |
| an unqualified call finds a function in the namespace of its argument's type | `argument-dependent-lookup` | yes |
| a call in a lambda's body is the enclosing function's | `lambda-body` | yes |
| a function passed by name is a use of it | `function-as-value` | yes |
| a static member defined out of its class is the class's | `out-of-class-static` | yes |
| `T x(1);` constructs a T | `constructor-arguments` | yes |

### ruby (25 of 29)

Ruby documentation, 'Calling Methods' and Module#ancestors (method lookup), 'Modules and Classes' (constant lookup).

| Rule | Case | Engine |
|---|---|---|
| a bare name that is no local is a method of self | `implicit-self` | yes |
| a local variable hides a method of its name | `local-hides-method` | yes |
| a method not in the class is looked up in its superclass | `superclass` | yes |
| the methods of an included module are instance methods | `include` | yes |
| the methods of an extended module are methods of the class itself | `extend` | yes |
| `def self.m` is called through the class | `class-method` | yes |
| `C.new.m` is C's instance method | `new-then-method` | yes |
| a local assigned `C.new` has C's methods | `local-typed-by-value` | yes |
| `super` is the method of the same name further up | `super` | **no**: no A.m -calls-> Base.m |
| `A::B` is the constant B of A | `nested-constant` | yes |
| a constant is looked up in the modules written around the use | `lexical-constant` | yes |
| a class opened again in another file is the same class | `reopened-class` | yes |
| an instance variable assigned `C.new` in initialize has C's methods | `instance-variable-typed` | **no**: no Svc.run -calls-> Repo.find |
| the methods of a prepended module come before the class's | `prepend` | yes |
| `def self.f` in a module is called through the module | `module-function` | yes |
| a method under `class << self` is a class method | `singleton-class` | yes |
| an `attr_reader` gives what its instance variable holds | `attr-reader-typed` | yes |
| a class written `X = Struct.new(…) do … end` has the methods of its block | `struct-with-block` | yes |
| a constant assigned a class names that class | `constant-alias` | **no**: no run -calls-> Repo.find |
| `self.class.m` is a class method | `self-class` | yes |
| `super(x)` is the method of the same name further up | `super-with-arguments` | **no**: no A.m -calls-> Base.m |
| `r&.m` is r's m | `safe-navigation` | yes |
| a method named with `?` is called like any other | `predicate-name` | yes |
| `class A < Shop::Base` inherits from the namespaced class | `namespaced-superclass` | yes |
| a module of a required file can be included | `include-across-files` | yes |
| `C.new(…)` constructs C | `new-with-arguments` | yes |
| a top-level method of a required file is in scope | `top-level-method` | yes |
| a call in a block is the enclosing method's | `block-body` | yes |
| a private method is called with an implicit receiver | `private-method` | yes |

### php (27 of 31)

PHP Manual, 'Namespaces: Name resolution rules', 'Classes and Objects' (inheritance, traits, static).

| Rule | Case | Engine |
|---|---|---|
| `use N\C;` binds C | `use` | yes |
| `use N\C as D;` binds D | `use-alias` | yes |
| `\N\C` names C whatever the current namespace | `fully-qualified` | yes |
| `Sub\C` is relative to the current namespace | `qualified-relative` | yes |
| an unqualified class name is of the current namespace | `same-namespace` | yes |
| `$this->m()` is the class's method | `this` | yes |
| a method not in the class is looked up in its parent | `inherited` | yes |
| `parent::m()` is the parent class's m | `parent` | yes |
| `self::m()` and `static::m()` are the class's own | `self-static` | yes |
| the methods of a used trait are methods of the class | `trait` | yes |
| a property's declared type gives its members | `typed-property` | yes |
| a variable assigned `new C()` has C's methods | `new-then-method` | yes |
| an unqualified function call falls back to the global function | `function-fallback` | yes |
| `use function N\f;` binds f | `use-function` | yes |
| a promoted constructor parameter is a typed property | `constructor-promotion` | yes |
| a method's declared result gives the members of what it returns | `return-type` | yes |
| a `?T` parameter is a T | `nullable-parameter` | yes |
| `$r?->m()` is $r's m | `nullsafe` | yes |
| a closure's `use ($x)` variable keeps its type | `closure-use` | yes |
| an arrow function's typed parameter has that type | `arrow-function-parameter` | yes |
| a call through an interface type is the interface's method | `interface-typed` | yes |
| `use N\{A, B};` binds both | `group-use` | yes |
| `C::class` is a reference to C | `class-constant-fetch` | **no**: no run -references-> Repo |
| `new C(…)` constructs C | `new` | yes |
| `parent::__construct()` is the parent's constructor | `parent-constructor` | yes |
| an unqualified function call is first a function of the current namespace | `namespaced-function` | yes |
| an enum case has the enum's methods | `enum-method` | **no**: no run -calls-> Status.label |
| `new static()` / `new self()` construct the class | `static-return` | **no**: no Repo.make -calls-> Repo |
| a call of an abstract method through `$this` is the declaring class's | `abstract-method` | yes |
| `C::m()` is C's static method | `static-method-via-class` | yes |
| a method passed as `[$this, 'm']` is a use of m | `callable-string` | **no**: no A.run -indirect_call-> A.handle |
