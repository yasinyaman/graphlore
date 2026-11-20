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

Measured 2026-11-20 with graphlore-engine 0.5.45: **176 of 185 rules hold**.

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
  table has rows (C++ argument-dependent lookup, Scala implicits, Python
  metaclasses, Ruby `method_missing` are not here). A rule missing from the
  table is not known to hold: add the case, then look.
- **The places named** (a specification's section, by its title) were
  written from memory of those documents, not checked against a copy.

The cases that fail are the engine's open defects of this kind:

- **Rust**: `super::f()` inside an inline `mod inner { … }` names nothing;
  a second `let` of a name does not replace the first (the first one's type
  is used).
- **Ruby**: a bare `super` is no call of the method further up; an instance
  variable assigned `C.new` in `initialize` is not typed in the other
  methods.
- **TypeScript**: an optional parameter `r?: T` has no type; `m.Repo`
  through `import * as m` is no type.
- **JavaScript**: a method assigned to `F.prototype` is not found on a local
  assigned `new F()`.
- **Scala**: `val r = new Repo` (no parentheses) is not typed.
- **Java**: a generic class the project names `Box` loses its members: the
  name is read as a library's wrapper.

Three of the cases written first failed for the cases' own sake and were
corrected: a decorator is a `references` edge, not `uses`; a C local
declared as a function pointer is a definition of its own that the call
reaches; and generic cases named `Box` / `value` / `get` ran into the last
defect above, which is now a case of its own.

## The rules

### python (17 of 17)

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

### javascript (13 of 14)

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

### typescript (11 of 13)

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

### go (10 of 10)

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

### java (17 of 18)

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

### kotlin (14 of 14)

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

### scala (7 of 8)

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

### csharp (16 of 16)

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

### rust (14 of 16)

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

### swift (13 of 13)

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

### c (6 of 6)

ISO C, 6.2.1 'Scopes of identifiers', 6.2.2 'Linkages of identifiers'.

| Rule | Case | Engine |
|---|---|---|
| a function is in scope after its declaration in the file | `same-file` | yes |
| a function declared in an included header is the one another file defines | `external-linkage` | yes |
| a `static` function of the file wins over an external one of its name | `static-wins` | yes |
| a local variable hides a function of its name | `local-hides-function` | yes |
| a function put in a struct's slot is what a call through the slot reaches | `function-pointer-slot` | yes |
| a `typedef struct` names the struct written in it | `typedef-struct` | yes |

### cpp (13 of 13)

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

### ruby (11 of 13)

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

### php (14 of 14)

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
