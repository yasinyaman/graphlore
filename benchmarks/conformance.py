#!/usr/bin/env python3
# ruff: noqa: E501  (the cases are source text, one string a file)
"""The name-resolution rules of each language, one small project per rule.

The other benchmarks measure the native engine on real repositories, where a
gap shows up as a row to read and its cause has to be found. This goes the
other way: the rules a language's own definition gives for what a name means
(scopes, imports, member lookup, overloads) are a finite list, and each one
is a case here — a few lines of source, the edge the rule says is there
(`expect`) and, where the rule is about one name hiding another, the edge
that must not be (`absent`).

  python benchmarks/conformance.py                 # every case, a table per language
  python benchmarks/conformance.py --only rust,csharp
  python benchmarks/conformance.py --failed        # only what the engine gets wrong
  python benchmarks/conformance.py --json out.json --baseline benchmarks/results-conformance.json
  python benchmarks/conformance.py --markdown      # the table of docs/resolution-rules.md

A case names its ends by qualified name (`Repo.find`, or `find` when one
definition has that last name), by `path:name` when two files define the name,
or by a file's name for code outside any definition. A failing case is not a
failing test: it is the list of what is left, and `results-conformance.json`
holds the state so that a change says what it fixed and what it broke.

The `where` of a rule is the place the language defines it, by the title of
the section: they were written from memory of those documents, not checked
against a copy.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

Edge = tuple[str, str, str]

# (language, id, rule, where the language defines it, files, expect, absent)
CASES: list[dict[str, Any]] = []


def case(lang: str, cid: str, rule: str, where: str, files: dict[str, str],
         expect: list[Edge] | None = None, absent: list[Edge] | None = None) -> None:
    CASES.append({"lang": lang, "id": f"{lang}/{cid}", "rule": rule, "where": where,
                  "files": files, "expect": expect or [], "absent": absent or []})


# --- Python -------------------------------------------------------------------
PY = "The Python Language Reference, 'Naming and binding' (4.2) and 'The import system'"
case("python", "module-function", "a module-level function is in scope in every function of the module",
     PY, {"a.py": "def helper():\n    return 1\n\n\ndef run():\n    return helper()\n"},
     [("run", "calls", "helper")])
case("python", "enclosing-scope", "a nested function sees the names of the function around it",
     PY, {"a.py": "def helper():\n    return 0\n\n\ndef outer():\n    def helper():\n        return 1\n\n"
                  "    def inner():\n        return helper()\n\n    return inner()\n"},
     [("outer.inner", "calls", "outer.helper")], [("outer.inner", "calls", "a.py:helper")])
case("python", "local-hides-global", "a name bound in a function is local to it and hides the module's",
     PY, {"a.py": "def helper():\n    return 0\n\n\ndef run(helper):\n    return helper()\n"},
     [], [("run", "calls", "helper")])
case("python", "from-import", "`from m import f` binds f to m's definition",
     PY, {"pkg/__init__.py": "", "pkg/m.py": "def f():\n    return 1\n",
          "pkg/a.py": "from pkg.m import f\n\n\ndef run():\n    return f()\n"},
     [("run", "calls", "f")])
case("python", "import-alias", "`from m import f as g` binds g",
     PY, {"pkg/__init__.py": "", "pkg/m.py": "def f():\n    return 1\n",
          "pkg/a.py": "from pkg.m import f as g\n\n\ndef run():\n    return g()\n"},
     [("run", "calls", "f")])
case("python", "module-attribute", "`import pkg.m` then `pkg.m.f()` is m's f",
     PY, {"pkg/__init__.py": "", "pkg/m.py": "def f():\n    return 1\n",
          "app.py": "import pkg.m\n\n\ndef run():\n    return pkg.m.f()\n"},
     [("run", "calls", "f")])
case("python", "relative-import", "`from .m import f` is relative to the package of the importer",
     PY, {"pkg/__init__.py": "", "pkg/m.py": "def f():\n    return 1\n",
          "other/__init__.py": "", "other/m.py": "def f():\n    return 2\n",
          "pkg/a.py": "from .m import f\n\n\ndef run():\n    return f()\n"},
     [("run", "calls", "pkg/m.py:f")], [("run", "calls", "other/m.py:f")])
case("python", "star-import", "`from m import *` binds m's public names",
     PY, {"pkg/__init__.py": "", "pkg/m.py": "def f():\n    return 1\n",
          "pkg/a.py": "from pkg.m import *\n\n\ndef run():\n    return f()\n"},
     [("run", "calls", "f")])
case("python", "self-method", "`self.m()` is the class's method",
     PY, {"a.py": "class A:\n    def m(self):\n        return 1\n\n    def run(self):\n        return self.m()\n"},
     [("A.run", "calls", "A.m")])
case("python", "inherited-method", "an attribute not on the class is looked up on its bases (the MRO)",
     PY, {"base.py": "class Base:\n    def m(self):\n        return 1\n",
          "a.py": "from base import Base\n\n\nclass A(Base):\n    def run(self):\n        return self.m()\n"},
     [("A.run", "calls", "Base.m")])
case("python", "super", "`super().m()` is the next class's m",
     PY, {"a.py": "class Base:\n    def m(self):\n        return 1\n\n\nclass A(Base):\n    def m(self):\n"
                  "        return super().m()\n"},
     [("A.m", "calls", "Base.m")])
case("python", "class-call", "calling a class constructs it",
     PY, {"a.py": "class Repo:\n    def __init__(self):\n        self.x = 1\n\n\ndef run():\n    return Repo()\n"},
     [("run", "calls", "Repo")])
case("python", "annotated-parameter", "a parameter annotated with a class has that class's methods",
     PY, {"a.py": "class Repo:\n    def find(self):\n        return 1\n\n\nclass Other:\n    def find(self):\n"
                  "        return 2\n\n\ndef run(r: Repo):\n    return r.find()\n"},
     [("run", "calls", "Repo.find")], [("run", "calls", "Other.find")])
case("python", "attribute-assigned-in-init", "`self.repo = Repo()` in __init__ types self.repo",
     PY, {"a.py": "class Repo:\n    def find(self):\n        return 1\n\n\nclass Other:\n    def find(self):\n"
                  "        return 2\n\n\nclass Svc:\n    def __init__(self):\n        self.repo = Repo()\n\n"
                  "    def run(self):\n        return self.repo.find()\n"},
     [("Svc.run", "calls", "Repo.find")], [("Svc.run", "calls", "Other.find")])
case("python", "classmethod-via-class", "`Cls.create()` is the class's own method",
     PY, {"a.py": "class Repo:\n    @classmethod\n    def create(cls):\n        return cls()\n\n\n"
                  "def run():\n    return Repo.create()\n"},
     [("run", "calls", "Repo.create")])
case("python", "local-typed-by-value", "a local assigned a construction has that class's methods",
     PY, {"a.py": "class Repo:\n    def find(self):\n        return 1\n\n\nclass Other:\n    def find(self):\n"
                  "        return 2\n\n\ndef run():\n    r = Repo()\n    return r.find()\n"},
     [("run", "calls", "Repo.find")], [("run", "calls", "Other.find")])
case("python", "decorator", "a decorator is a use of the name it is written with",
     PY, {"a.py": "def traced(f):\n    return f\n\n\n@traced\ndef run():\n    return 1\n"},
     [("run", "references", "traced")])

# --- JavaScript ---------------------------------------------------------------
JS = "ECMAScript Language Specification, 'Executable Code and Execution Contexts' (scopes), 'Modules'"
case("javascript", "hoisting", "a function declaration is in scope in its whole enclosing scope",
     JS, {"a.js": "function run() {\n  return helper()\n}\n\nfunction helper() {\n  return 1\n}\n"},
     [("run", "calls", "helper")])
case("javascript", "block-const-hides", "a `const` of a block hides an outer function of its name",
     JS, {"a.js": "function helper() {\n  return 0\n}\n\nfunction run(make) {\n  const helper = make()\n"
                  "  return helper()\n}\n"},
     [], [("run", "calls", "helper")])
case("javascript", "closure", "an inner function sees the functions of the one around it",
     JS, {"a.js": "function outer() {\n  function helper() {\n    return 1\n  }\n  function inner() {\n"
                  "    return helper()\n  }\n  return inner()\n}\n"},
     [("outer.inner", "calls", "outer.helper")])
case("javascript", "named-import", "`import { f } from './m.js'` binds m's export",
     JS, {"m.js": "export function f() {\n  return 1\n}\n",
          "a.js": "import { f } from './m.js'\n\nexport function run() {\n  return f()\n}\n"},
     [("run", "calls", "f")])
case("javascript", "import-alias", "`import { f as g }` binds g",
     JS, {"m.js": "export function f() {\n  return 1\n}\n",
          "a.js": "import { f as g } from './m.js'\n\nexport function run() {\n  return g()\n}\n"},
     [("run", "calls", "f")])
case("javascript", "default-import", "a default import is the module's default export",
     JS, {"m.js": "export default function make() {\n  return 1\n}\n",
          "a.js": "import build from './m.js'\n\nexport function run() {\n  return build()\n}\n"},
     [("run", "calls", "make")])
case("javascript", "namespace-import", "`import * as m` then `m.f()`",
     JS, {"m.js": "export function f() {\n  return 1\n}\n",
          "a.js": "import * as m from './m.js'\n\nexport function run() {\n  return m.f()\n}\n"},
     [("run", "calls", "f")])
case("javascript", "re-export", "`export { f } from './m.js'` passes m's f on",
     JS, {"m.js": "export function f() {\n  return 1\n}\n", "index.js": "export { f } from './m.js'\n",
          "a.js": "import { f } from './index.js'\n\nexport function run() {\n  return f()\n}\n"},
     [("run", "calls", "f")])
case("javascript", "commonjs-destructure", "`const { f } = require('./m')` binds what m exports as f",
     JS, {"m.js": "function f() {\n  return 1\n}\n\nmodule.exports = { f }\n",
          "a.js": "const { f } = require('./m')\n\nfunction run() {\n  return f()\n}\n\nmodule.exports = { run }\n"},
     [("run", "calls", "f")])
case("javascript", "this-method", "`this.m()` in a class method is the class's m",
     JS, {"a.js": "class A {\n  m() {\n    return 1\n  }\n  run() {\n    return this.m()\n  }\n}\n"},
     [("A.run", "calls", "A.m")])
case("javascript", "inherited-method", "a method not on the class is looked up on what it extends",
     JS, {"a.js": "class Base {\n  m() {\n    return 1\n  }\n}\n\nclass A extends Base {\n  run() {\n"
                  "    return this.m()\n  }\n}\n"},
     [("A.run", "calls", "Base.m")])
case("javascript", "super", "`super.m()` is the base class's m",
     JS, {"a.js": "class Base {\n  m() {\n    return 1\n  }\n}\n\nclass A extends Base {\n  m() {\n"
                  "    return super.m()\n  }\n}\n"},
     [("A.m", "calls", "Base.m")])
case("javascript", "new-then-method", "a local assigned `new C()` has C's methods",
     JS, {"a.js": "class Repo {\n  find() {\n    return 1\n  }\n}\n\nclass Other {\n  find() {\n    return 2\n  }\n}\n\n"
                  "function run() {\n  const r = new Repo()\n  return r.find()\n}\n"},
     [("run", "calls", "Repo.find")], [("run", "calls", "Other.find")])
case("javascript", "prototype-method", "a function assigned to `F.prototype` is a method of F's instances",
     JS, {"a.js": "function View() {}\n\nView.prototype.render = function () {\n  return 1\n}\n\n"
                  "function run() {\n  const v = new View()\n  return v.render()\n}\n"},
     [("run", "calls", "render")])

# --- TypeScript ---------------------------------------------------------------
TS = "TypeScript Handbook, 'Modules', 'Classes', 'Generics' (types are the compiler's, tsc)"
case("typescript", "typed-parameter", "a parameter's declared type gives its members",
     TS, {"a.ts": "export class Repo {\n  find(): number {\n    return 1\n  }\n}\n\nexport class Other {\n"
                  "  find(): number {\n    return 2\n  }\n}\n\nexport function run(r: Repo): number {\n"
                  "  return r.find()\n}\n"},
     [("run", "calls", "Repo.find")], [("run", "calls", "Other.find")])
case("typescript", "interface-typed", "a call through an interface type is the interface's method",
     TS, {"a.ts": "export interface Store {\n  find(): number\n}\n\nexport class Mem implements Store {\n"
                  "  find(): number {\n    return 1\n  }\n}\n\nexport function run(s: Store): number {\n"
                  "  return s.find()\n}\n"},
     [("run", "calls", "Store.find")])
case("typescript", "parameter-property", "`constructor(private repo: Repo)` declares a typed field",
     TS, {"a.ts": "export class Repo {\n  find(): number {\n    return 1\n  }\n}\n\nexport class Other {\n"
                  "  find(): number {\n    return 2\n  }\n}\n\nexport class Svc {\n"
                  "  constructor(private repo: Repo) {}\n  run(): number {\n    return this.repo.find()\n  }\n}\n"},
     [("Svc.run", "calls", "Repo.find")], [("Svc.run", "calls", "Other.find")])
case("typescript", "typed-field", "a field's declared type gives its members",
     TS, {"a.ts": "export class Repo {\n  find(): number {\n    return 1\n  }\n}\n\nexport class Other {\n"
                  "  find(): number {\n    return 2\n  }\n}\n\nexport class Svc {\n  private repo: Repo = new Repo()\n"
                  "  run(): number {\n    return this.repo.find()\n  }\n}\n"},
     [("Svc.run", "calls", "Repo.find")], [("Svc.run", "calls", "Other.find")])
case("typescript", "generic-argument", "`Page<Item>`'s `head: T` is an Item",
     TS, {"a.ts": "export class Item {\n  name(): string {\n    return ''\n  }\n}\n\nexport class Other {\n"
                  "  name(): string {\n    return ''\n  }\n}\n\nexport class Page<T> {\n  head: T\n  constructor(v: T) {\n    this.head = v\n  }\n}\n\n"
                  "export function run(b: Page<Item>): string {\n  return b.head.name()\n}\n"},
     [("run", "calls", "Item.name")], [("run", "calls", "Other.name")])
case("typescript", "return-type", "a call's declared return type gives the members of its result",
     TS, {"a.ts": "export class Repo {\n  find(): number {\n    return 1\n  }\n}\n\nexport class Other {\n"
                  "  find(): number {\n    return 2\n  }\n}\n\nexport function make(): Repo {\n  return new Repo()\n}\n\n"
                  "export function run(): number {\n  return make().find()\n}\n"},
     [("run", "calls", "Repo.find")], [("run", "calls", "Other.find")])
case("typescript", "import-type", "`import type { T }` is a reference to T",
     TS, {"m.ts": "export interface Shape {\n  area(): number\n}\n",
          "a.ts": "import type { Shape } from './m'\n\nexport function run(s: Shape): number {\n  return s.area()\n}\n"},
     [("run", "references", "Shape"), ("run", "calls", "Shape.area")])
case("typescript", "barrel", "an import through `index.ts` reaches the file that defines the name",
     TS, {"lib/m.ts": "export function f(): number {\n  return 1\n}\n", "lib/index.ts": "export * from './m'\n",
          "a.ts": "import { f } from './lib'\n\nexport function run(): number {\n  return f()\n}\n"},
     [("run", "calls", "f")])
case("typescript", "optional-chaining", "`a?.m()` is a's m",
     TS, {"a.ts": "export class Repo {\n  find(): number {\n    return 1\n  }\n}\n\n"
                  "export function run(r: Repo | undefined): number | undefined {\n  return r?.find()\n}\n"},
     [("run", "calls", "Repo.find")])
case("typescript", "optional-parameter", "an optional parameter `r?: T` has type T",
     TS, {"a.ts": "export class Repo {\n  find(): number {\n    return 1\n  }\n}\n\nexport class Other {\n"
                  "  find(): number {\n    return 2\n  }\n}\n\nexport function run(r?: Repo): number {\n"
                  "  return r!.find()\n}\n"},
     [("run", "calls", "Repo.find")], [("run", "calls", "Other.find")])
case("typescript", "enum-member", "an enum member is a member of its enum",
     TS, {"a.ts": "export enum Color {\n  Red,\n  Green,\n}\n\nexport function run(): Color {\n  return Color.Red\n}\n"},
     [("run", "references", "Color")])
case("typescript", "static-method", "`Cls.make()` is the class's static method",
     TS, {"a.ts": "export class Repo {\n  static make(): Repo {\n    return new Repo()\n  }\n}\n\n"
                  "export function run(): Repo {\n  return Repo.make()\n}\n"},
     [("run", "calls", "Repo.make")])
case("typescript", "namespace-import-type", "`import * as m` then `m.Repo` as a type",
     TS, {"m.ts": "export class Repo {\n  find(): number {\n    return 1\n  }\n}\n",
          "a.ts": "import * as m from './m'\n\nexport function run(r: m.Repo): number {\n  return r.find()\n}\n"},
     [("run", "calls", "Repo.find")])

# --- Go -----------------------------------------------------------------------
GO = "The Go Programming Language Specification, 'Declarations and scope', 'Selectors', 'Method sets'"
GOMOD = "module example.com/app\n\ngo 1.22\n"
case("go", "package-scope", "a package-level function is in scope in every file of the package",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\nfunc Run() int {\n\treturn helper()\n}\n",
          "b.go": "package app\n\nfunc helper() int {\n\treturn 1\n}\n"},
     [("Run", "calls", "helper")])
case("go", "imported-package", "`pkg.F()` is F of the imported package",
     GO, {"go.mod": GOMOD, "util/u.go": "package util\n\nfunc F() int {\n\treturn 1\n}\n",
          "a.go": "package app\n\nimport \"example.com/app/util\"\n\nfunc Run() int {\n\treturn util.F()\n}\n"},
     [("Run", "calls", "F")])
case("go", "import-alias", "an import can be renamed",
     GO, {"go.mod": GOMOD, "util/u.go": "package util\n\nfunc F() int {\n\treturn 1\n}\n",
          "a.go": "package app\n\nimport u \"example.com/app/util\"\n\nfunc Run() int {\n\treturn u.F()\n}\n"},
     [("Run", "calls", "F")])
case("go", "method-on-local", "a method call on a typed local is the type's method",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\ntype Repo struct{}\n\nfunc (r *Repo) Find() int {\n\treturn 1\n}\n\n"
                                  "type Other struct{}\n\nfunc (o *Other) Find() int {\n\treturn 2\n}\n\n"
                                  "func Run() int {\n\tr := &Repo{}\n\treturn r.Find()\n}\n"},
     [("Run", "calls", "Repo.Find")], [("Run", "calls", "Other.Find")])
case("go", "embedded-struct", "the methods of an embedded type are promoted",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\ntype Base struct{}\n\nfunc (b *Base) Close() int {\n\treturn 1\n}\n\n"
                                  "type File struct {\n\tBase\n}\n\nfunc Run(f *File) int {\n\treturn f.Close()\n}\n"},
     [("Run", "calls", "Base.Close")])
case("go", "interface-method", "a call through an interface type is the interface's method",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\ntype Store interface {\n\tFind() int\n}\n\n"
                                  "func Run(s Store) int {\n\treturn s.Find()\n}\n"},
     [("Run", "calls", "Store.Find")])
case("go", "result-type", "the declared result of a call gives the methods of the result",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\ntype Repo struct{}\n\nfunc (r *Repo) Find() int {\n\treturn 1\n}\n\n"
                                  "type Other struct{}\n\nfunc (o *Other) Find() int {\n\treturn 2\n}\n\n"
                                  "func New() *Repo {\n\treturn &Repo{}\n}\n\nfunc Run() int {\n\treturn New().Find()\n}\n"},
     [("Run", "calls", "Repo.Find")], [("Run", "calls", "Other.Find")])
case("go", "local-hides-function", "a local declared in a function hides a package function of its name",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\nfunc helper() int {\n\treturn 0\n}\n\n"
                                  "func Run(make func() func() int) int {\n\thelper := make()\n\treturn helper()\n}\n"},
     [], [("Run", "calls", "a.go:helper")])
case("go", "field-method", "a method of a struct field's type",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\ntype Repo struct{}\n\nfunc (r *Repo) Find() int {\n\treturn 1\n}\n\n"
                                  "type Other struct{}\n\nfunc (o *Other) Find() int {\n\treturn 2\n}\n\n"
                                  "type Svc struct {\n\trepo *Repo\n}\n\nfunc (s *Svc) Run() int {\n\treturn s.repo.Find()\n}\n"},
     [("Svc.Run", "calls", "Repo.Find")], [("Svc.Run", "calls", "Other.Find")])
case("go", "multiple-results", "`x, err := f()` types x by f's first result",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\ntype Repo struct{}\n\nfunc (r *Repo) Find() int {\n\treturn 1\n}\n\n"
                                  "type Other struct{}\n\nfunc (o *Other) Find() int {\n\treturn 2\n}\n\n"
                                  "func Open() (*Repo, error) {\n\treturn &Repo{}, nil\n}\n\n"
                                  "func Run() int {\n\tr, _ := Open()\n\treturn r.Find()\n}\n"},
     [("Run", "calls", "Repo.Find")], [("Run", "calls", "Other.Find")])

# --- Java ---------------------------------------------------------------------
JAVA = "The Java Language Specification, 6.4 'Shadowing and Obscuring', 15.12 'Method Invocation Expressions'"
case("java", "implicit-this", "an unqualified method name is a method of the class",
     JAVA, {"app/A.java": "package app;\n\npublic class A {\n    int helper() {\n        return 1;\n    }\n\n"
                          "    int run() {\n        return helper();\n    }\n}\n"},
     [("A.run", "calls", "A.helper")])
case("java", "inherited", "an unqualified method name is also searched in the superclasses",
     JAVA, {"app/Base.java": "package app;\n\npublic class Base {\n    int helper() {\n        return 1;\n    }\n}\n",
            "app/A.java": "package app;\n\npublic class A extends Base {\n    int run() {\n        return helper();\n    }\n}\n"},
     [("A.run", "calls", "Base.helper")])
case("java", "super", "`super.m()` is the superclass's m",
     JAVA, {"app/A.java": "package app;\n\nclass Base {\n    int m() {\n        return 1;\n    }\n}\n\n"
                          "public class A extends Base {\n    int m() {\n        return super.m();\n    }\n}\n"},
     [("A.m", "calls", "Base.m")])
case("java", "same-package", "a class of the same package needs no import",
     JAVA, {"app/Repo.java": "package app;\n\npublic class Repo {\n    int find() {\n        return 1;\n    }\n}\n",
            "app/A.java": "package app;\n\npublic class A {\n    int run(Repo r) {\n        return r.find();\n    }\n}\n"},
     [("A.run", "calls", "Repo.find")])
case("java", "single-type-import", "`import p.C;` binds C",
     JAVA, {"lib/Repo.java": "package lib;\n\npublic class Repo {\n    public int find() {\n        return 1;\n    }\n}\n",
            "other/Repo.java": "package other;\n\npublic class Repo {\n    public int find() {\n        return 2;\n    }\n}\n",
            "app/A.java": "package app;\n\nimport lib.Repo;\n\npublic class A {\n    int run(Repo r) {\n"
                          "        return r.find();\n    }\n}\n"},
     [("A.run", "calls", "lib/Repo.java:Repo.find")], [("A.run", "calls", "other/Repo.java:Repo.find")])
case("java", "on-demand-import", "`import p.*;` makes p's types available",
     JAVA, {"lib/Repo.java": "package lib;\n\npublic class Repo {\n    public int find() {\n        return 1;\n    }\n}\n",
            "app/A.java": "package app;\n\nimport lib.*;\n\npublic class A {\n    int run(Repo r) {\n"
                          "        return r.find();\n    }\n}\n"},
     [("A.run", "calls", "Repo.find")])
case("java", "static-import", "`import static p.C.m;` binds m",
     JAVA, {"lib/Util.java": "package lib;\n\npublic class Util {\n    public static int twice(int x) {\n"
                             "        return 2 * x;\n    }\n}\n",
            "app/A.java": "package app;\n\nimport static lib.Util.twice;\n\npublic class A {\n    int run() {\n"
                          "        return twice(2);\n    }\n}\n"},
     [("A.run", "calls", "Util.twice")])
case("java", "static-via-class", "`C.m()` is C's static method",
     JAVA, {"app/A.java": "package app;\n\nclass Util {\n    static int twice(int x) {\n        return 2 * x;\n    }\n}\n\n"
                          "public class A {\n    int run() {\n        return Util.twice(2);\n    }\n}\n"},
     [("A.run", "calls", "Util.twice")])
case("java", "field-type", "a field's declared type gives its members",
     JAVA, {"app/A.java": "package app;\n\nclass Repo {\n    int find() {\n        return 1;\n    }\n}\n\n"
                          "class Other {\n    int find() {\n        return 2;\n    }\n}\n\n"
                          "public class A {\n    private Repo repo;\n\n    int run() {\n        return repo.find();\n    }\n}\n"},
     [("A.run", "calls", "Repo.find")], [("A.run", "calls", "Other.find")])
case("java", "local-hides-field", "a local variable shadows a field of its name",
     JAVA, {"app/A.java": "package app;\n\nclass Repo {\n    int find() {\n        return 1;\n    }\n}\n\n"
                          "class Other {\n    int find() {\n        return 2;\n    }\n}\n\n"
                          "public class A {\n    private Repo x;\n\n    int run(Other o) {\n        Other x = o;\n"
                          "        return x.find();\n    }\n}\n"},
     [("A.run", "calls", "Other.find")], [("A.run", "calls", "Repo.find")])
case("java", "overload-arity", "overloads are told apart by the number of arguments",
     JAVA, {"app/A.java": "package app;\n\npublic class A {\n    int send(int a) {\n        return a;\n    }\n\n"
                          "    int send(int a, int b) {\n        return a + b;\n    }\n\n"
                          "    int run() {\n        return send(1, 2);\n    }\n}\n"},
     [("A.run", "calls", "A.send")])
case("java", "constructor", "`new C(…)` calls C's constructor",
     JAVA, {"app/A.java": "package app;\n\nclass Repo {\n    Repo(int size) {\n    }\n}\n\n"
                          "public class A {\n    Repo run() {\n        return new Repo(1);\n    }\n}\n"},
     [("A.run", "calls", "Repo")])
case("java", "interface-default", "a default method of an implemented interface is inherited",
     JAVA, {"app/A.java": "package app;\n\ninterface Named {\n    default String label() {\n        return \"x\";\n    }\n}\n\n"
                          "public class A implements Named {\n    String run() {\n        return label();\n    }\n}\n"},
     [("A.run", "calls", "Named.label")])
case("java", "outer-method", "an inner class sees the methods of the class around it",
     JAVA, {"app/A.java": "package app;\n\npublic class A {\n    int helper() {\n        return 1;\n    }\n\n"
                          "    class Inner {\n        int run() {\n            return helper();\n        }\n    }\n}\n"},
     [("A.Inner.run", "calls", "A.helper")])
case("java", "chained-result", "a method's declared return type gives the members of its result",
     JAVA, {"app/A.java": "package app;\n\nclass Repo {\n    int find() {\n        return 1;\n    }\n}\n\n"
                          "class Other {\n    int find() {\n        return 2;\n    }\n}\n\n"
                          "public class A {\n    Repo make() {\n        return new Repo();\n    }\n\n"
                          "    int run() {\n        return make().find();\n    }\n}\n"},
     [("A.run", "calls", "Repo.find")], [("A.run", "calls", "Other.find")])
case("java", "generic-argument", "`Page<Item>`'s `T lead()` gives an Item",
     JAVA, {"app/A.java": "package app;\n\nclass Item {\n    int size() {\n        return 1;\n    }\n}\n\n"
                          "class Other {\n    int size() {\n        return 2;\n    }\n}\n\n"
                          "class Page<T> {\n    T head;\n\n    T lead() {\n        return head;\n    }\n}\n\n"
                          "public class A {\n    int run(Page<Item> b) {\n        return b.lead().size();\n    }\n}\n"},
     [("A.run", "calls", "Item.size")], [("A.run", "calls", "Other.size")])
case("java", "own-accessor-name", "a project type's own `get()` is its method, whatever libraries name so",
     JAVA, {"app/A.java": "package app;\n\nclass Item {\n    int size() {\n        return 1;\n    }\n}\n\n"
                          "class Other {\n    int size() {\n        return 2;\n    }\n}\n\n"
                          "class Holder<T> {\n    T held;\n\n    T get() {\n        return held;\n    }\n}\n\n"
                          "public class A {\n    int run(Holder<Item> h) {\n        return h.get().size();\n    }\n}\n"},
     [("A.run", "calls", "Holder.get"), ("A.run", "calls", "Item.size")], [("A.run", "calls", "Other.size")])
case("java", "class-named-like-a-wrapper", "a generic class the project names `Box` is the project's class",
     JAVA, {"app/A.java": "package app;\n\nclass Item {\n    int size() {\n        return 1;\n    }\n}\n\n"
                          "class Other {\n    int size() {\n        return 2;\n    }\n}\n\n"
                          "class Box<T> {\n    T held;\n\n    T lead() {\n        return held;\n    }\n}\n\n"
                          "public class A {\n    int run(Box<Item> b) {\n        return b.lead().size();\n    }\n}\n"},
     [("A.run", "calls", "Box.lead"), ("A.run", "calls", "Item.size")], [("A.run", "calls", "Other.size")])

# --- Kotlin -------------------------------------------------------------------
KT = "Kotlin Language Specification, 'Scopes and identifiers', 'Overload resolution' (receivers)"
case("kotlin", "same-package", "a top-level function of the same package needs no import",
     KT, {"src/app/Util.kt": "package app\n\nfun helper(): Int = 1\n",
          "src/app/A.kt": "package app\n\nfun run(): Int = helper()\n"},
     [("run", "calls", "helper")])
case("kotlin", "import", "an import binds a top-level function of another package",
     KT, {"src/lib/Util.kt": "package lib\n\nfun helper(): Int = 1\n",
          "src/other/Util.kt": "package other\n\nfun helper(): Int = 2\n",
          "src/app/A.kt": "package app\n\nimport lib.helper\n\nfun run(): Int = helper()\n"},
     [("run", "calls", "src/lib/Util.kt:helper")], [("run", "calls", "src/other/Util.kt:helper")])
case("kotlin", "import-alias", "`import a.b as c` binds c",
     KT, {"src/lib/Util.kt": "package lib\n\nfun helper(): Int = 1\n",
          "src/app/A.kt": "package app\n\nimport lib.helper as h\n\nfun run(): Int = h()\n"},
     [("run", "calls", "helper")])
case("kotlin", "member", "a method call on a typed value is the type's member",
     KT, {"src/app/A.kt": "package app\n\nclass Repo {\n    fun find(): Int = 1\n}\n\nclass Other {\n    fun find(): Int = 2\n}\n\n"
                          "fun run(r: Repo): Int = r.find()\n"},
     [("run", "calls", "Repo.find")], [("run", "calls", "Other.find")])
case("kotlin", "extension", "an extension function is called like a member of its receiver type",
     KT, {"src/app/A.kt": "package app\n\nclass Repo\n\nfun Repo.size(): Int = 1\n\nfun run(r: Repo): Int = r.size()\n"},
     [("run", "calls", "size")])
case("kotlin", "member-wins", "a member wins over an extension of the same name",
     KT, {"src/app/A.kt": "package app\n\nclass Repo {\n    fun size(): Int = 1\n}\n\nfun Repo.size(): Int = 2\n\n"
                          "fun run(r: Repo): Int = r.size()\n"},
     [("run", "calls", "Repo.size")])
case("kotlin", "companion", "`C.make()` is a member of C's companion object",
     KT, {"src/app/A.kt": "package app\n\nclass Repo {\n    companion object {\n        fun make(): Repo = Repo()\n    }\n}\n\n"
                          "fun run(): Repo = Repo.make()\n"},
     [("run", "calls", "make")])
case("kotlin", "object", "an `object` declaration's members are called through its name",
     KT, {"src/app/A.kt": "package app\n\nobject Registry {\n    fun add(x: Int): Int = x\n}\n\nfun run(): Int = Registry.add(1)\n"},
     [("run", "calls", "Registry.add")])
case("kotlin", "constructor-property", "a `val` of the primary constructor is a typed property",
     KT, {"src/app/A.kt": "package app\n\nclass Repo {\n    fun find(): Int = 1\n}\n\nclass Other {\n    fun find(): Int = 2\n}\n\n"
                          "class Svc(private val repo: Repo) {\n    fun run(): Int = repo.find()\n}\n"},
     [("Svc.run", "calls", "Repo.find")], [("Svc.run", "calls", "Other.find")])
case("kotlin", "receiver-lambda", "inside `x.apply { … }` an unqualified name is x's member",
     KT, {"src/app/A.kt": "package app\n\nclass Repo {\n    fun find(): Int = 1\n}\n\nfun run(r: Repo): Repo = r.apply {\n    find()\n}\n"},
     [("run", "calls", "Repo.find")])
case("kotlin", "it", "`it` in a lambda given to a collection's method is an element",
     KT, {"src/app/A.kt": "package app\n\nclass Repo {\n    fun find(): Int = 1\n}\n\nclass Other {\n    fun find(): Int = 2\n}\n\n"
                          "fun run(rs: List<Repo>): List<Int> = rs.map { it.find() }\n"},
     [("run", "calls", "Repo.find")], [("run", "calls", "Other.find")])
case("kotlin", "inherited", "an unqualified name in a class is also searched in its supertypes",
     KT, {"src/app/A.kt": "package app\n\nopen class Base {\n    fun helper(): Int = 1\n}\n\nclass A : Base() {\n"
                          "    fun run(): Int = helper()\n}\n"},
     [("A.run", "calls", "Base.helper")])
case("kotlin", "local-typed-by-value", "a `val` assigned a construction has that type",
     KT, {"src/app/A.kt": "package app\n\nclass Repo {\n    fun find(): Int = 1\n}\n\nclass Other {\n    fun find(): Int = 2\n}\n\n"
                          "fun run(): Int {\n    val r = Repo()\n    return r.find()\n}\n"},
     [("run", "calls", "Repo.find")], [("run", "calls", "Other.find")])
case("kotlin", "generic-argument", "`Page<Item>`'s `val head: T` is an Item",
     KT, {"src/app/A.kt": "package app\n\nclass Item {\n    fun size(): Int = 1\n}\n\nclass Other {\n    fun size(): Int = 2\n}\n\n"
                          "class Page<T>(val head: T)\n\nfun run(b: Page<Item>): Int = b.head.size()\n"},
     [("run", "calls", "Item.size")], [("run", "calls", "Other.size")])

# --- Scala --------------------------------------------------------------------
SC = "Scala Language Specification, chapter 2 'Identifiers, Names and Scopes', 4.7 'Import Clauses'"
case("scala", "object-method", "`Obj.f()` is a member of the object",
     SC, {"src/app/A.scala": "package app\n\nobject Util {\n  def twice(x: Int): Int = 2 * x\n}\n\n"
                             "object Main {\n  def run(): Int = Util.twice(2)\n}\n"},
     [("Main.run", "calls", "Util.twice")])
case("scala", "typed-parameter", "a method call on a typed value is the type's member",
     SC, {"src/app/A.scala": "package app\n\nclass Repo {\n  def find(): Int = 1\n}\n\nclass Other {\n  def find(): Int = 2\n}\n\n"
                             "object Main {\n  def run(r: Repo): Int = r.find()\n}\n"},
     [("Main.run", "calls", "Repo.find")], [("Main.run", "calls", "Other.find")])
case("scala", "trait-method", "a method of a mixed-in trait is inherited",
     SC, {"src/app/A.scala": "package app\n\ntrait Named {\n  def label(): String = \"x\"\n}\n\n"
                             "class A extends Named {\n  def run(): String = label()\n}\n"},
     [("A.run", "calls", "Named.label")])
case("scala", "wildcard-import", "`import p._` makes p's members available",
     SC, {"src/lib/Util.scala": "package lib\n\nobject Util {\n  def twice(x: Int): Int = 2 * x\n}\n",
          "src/app/A.scala": "package app\n\nimport lib._\n\nobject Main {\n  def run(): Int = Util.twice(2)\n}\n"},
     [("Main.run", "calls", "Util.twice")])
case("scala", "import-rename", "`import p.{A => B}` binds B",
     SC, {"src/lib/Util.scala": "package lib\n\nobject Util {\n  def twice(x: Int): Int = 2 * x\n}\n",
          "src/app/A.scala": "package app\n\nimport lib.{Util => U}\n\nobject Main {\n  def run(): Int = U.twice(2)\n}\n"},
     [("Main.run", "calls", "Util.twice")])
case("scala", "implicit-this", "an unqualified name in a class is a member of it",
     SC, {"src/app/A.scala": "package app\n\nclass A {\n  def helper(): Int = 1\n  def run(): Int = helper()\n}\n"},
     [("A.run", "calls", "A.helper")])
case("scala", "case-class", "`C(…)` constructs a case class",
     SC, {"src/app/A.scala": "package app\n\ncase class Point(x: Int, y: Int)\n\n"
                             "object Main {\n  def run(): Point = Point(1, 2)\n}\n"},
     [("Main.run", "calls", "Point")])
case("scala", "local-typed-by-value", "a `val` assigned `new C` has that type",
     SC, {"src/app/A.scala": "package app\n\nclass Repo {\n  def find(): Int = 1\n}\n\nclass Other {\n  def find(): Int = 2\n}\n\n"
                             "object Main {\n  def run(): Int = {\n    val r = new Repo\n    r.find()\n  }\n}\n"},
     [("Main.run", "calls", "Repo.find")], [("Main.run", "calls", "Other.find")])

# --- C# -----------------------------------------------------------------------
CS = "C# Language Specification, 'Namespace and type names', 'Member lookup', 'Extension method invocations'"
case("csharp", "enclosing-namespace", "a name is looked up in every namespace around the use",
     CS, {"NameInfo.cs": "namespace Shop\n{\n    public class NameInfo\n    {\n        public int Size() { return 1; }\n    }\n}\n",
          "Core/Ext.cs": "namespace Shop.Core\n{\n    public class Ext\n    {\n        public int Run(NameInfo n)\n        {\n"
                         "            return n.Size();\n        }\n    }\n}\n"},
     [("Ext.Run", "calls", "NameInfo.Size")])
case("csharp", "using", "`using N;` makes N's types available",
     CS, {"Lib/Repo.cs": "namespace Lib\n{\n    public class Repo\n    {\n        public int Find() { return 1; }\n    }\n}\n",
          "Other/Repo.cs": "namespace Other\n{\n    public class Repo\n    {\n        public int Find() { return 2; }\n    }\n}\n",
          "App/A.cs": "using Lib;\n\nnamespace App\n{\n    public class A\n    {\n        public int Run(Repo r)\n        {\n"
                      "            return r.Find();\n        }\n    }\n}\n"},
     [("A.Run", "calls", "Lib/Repo.cs:Lib.Repo.Find")], [("A.Run", "calls", "Other/Repo.cs:Other.Repo.Find")])
case("csharp", "file-scoped-namespace", "`namespace N;` holds the whole file",
     CS, {"Lib/Repo.cs": "namespace Lib;\n\npublic class Repo\n{\n    public int Find() { return 1; }\n}\n",
          "App/A.cs": "using Lib;\n\nnamespace App;\n\npublic class A\n{\n    public int Run(Repo r)\n    {\n"
                      "        return r.Find();\n    }\n}\n"},
     [("A.Run", "calls", "Repo.Find")])
case("csharp", "using-alias", "`using R = N.C;` binds R",
     CS, {"Lib/Repo.cs": "namespace Lib\n{\n    public class Repo\n    {\n        public int Find() { return 1; }\n    }\n}\n",
          "App/A.cs": "using R = Lib.Repo;\n\nnamespace App\n{\n    public class A\n    {\n        public int Run(R r)\n        {\n"
                      "            return r.Find();\n        }\n    }\n}\n"},
     [("A.Run", "calls", "Repo.Find")])
case("csharp", "using-static", "`using static N.C;` makes C's static members available",
     CS, {"Lib/Util.cs": "namespace Lib\n{\n    public static class Util\n    {\n        public static int Twice(int x) { return 2 * x; }\n    }\n}\n",
          "App/A.cs": "using static Lib.Util;\n\nnamespace App\n{\n    public class A\n    {\n        public int Run()\n        {\n"
                      "            return Twice(2);\n        }\n    }\n}\n"},
     [("A.Run", "calls", "Util.Twice")])
case("csharp", "implicit-this", "an unqualified name in a class is a member of it",
     CS, {"A.cs": "namespace App\n{\n    public class A\n    {\n        int Helper() { return 1; }\n\n        public int Run()\n        {\n"
                  "            return Helper();\n        }\n    }\n}\n"},
     [("A.Run", "calls", "A.Helper")])
case("csharp", "base", "`base.M()` is the base class's M",
     CS, {"A.cs": "namespace App\n{\n    public class Base\n    {\n        public virtual int M() { return 1; }\n    }\n\n"
                  "    public class A : Base\n    {\n        public override int M()\n        {\n            return base.M();\n        }\n    }\n}\n"},
     [("A.M", "calls", "Base.M")])
case("csharp", "extension", "an extension method is called like a member of its `this` parameter's type",
     CS, {"A.cs": "namespace App\n{\n    public class Repo\n    {\n    }\n\n    public static class RepoExt\n    {\n"
                  "        public static int Size(this Repo r) { return 1; }\n    }\n\n    public class A\n    {\n"
                  "        public int Run(Repo r)\n        {\n            return r.Size();\n        }\n    }\n}\n"},
     [("A.Run", "calls", "RepoExt.Size")])
case("csharp", "partial", "the members of a partial type are those of all its parts",
     CS, {"A1.cs": "namespace App\n{\n    public partial class A\n    {\n        int Helper() { return 1; }\n    }\n}\n",
          "A2.cs": "namespace App\n{\n    public partial class A\n    {\n        public int Run()\n        {\n            return Helper();\n        }\n    }\n}\n"},
     [("A.Run", "calls", "A.Helper")])
case("csharp", "var-new", "`var x = new C();` has type C",
     CS, {"A.cs": "namespace App\n{\n    public class Repo\n    {\n        public int Find() { return 1; }\n    }\n\n"
                  "    public class Other\n    {\n        public int Find() { return 2; }\n    }\n\n    public class A\n    {\n"
                  "        public int Run()\n        {\n            var r = new Repo();\n            return r.Find();\n        }\n    }\n}\n"},
     [("A.Run", "calls", "Repo.Find")], [("A.Run", "calls", "Other.Find")])
case("csharp", "property-type", "a property's declared type gives its members",
     CS, {"A.cs": "namespace App\n{\n    public class Repo\n    {\n        public int Find() { return 1; }\n    }\n\n"
                  "    public class Other\n    {\n        public int Find() { return 2; }\n    }\n\n    public class A\n    {\n"
                  "        public Repo Store { get; set; }\n\n        public int Run()\n        {\n            return Store.Find();\n        }\n    }\n}\n"},
     [("A.Run", "calls", "Repo.Find")], [("A.Run", "calls", "Other.Find")])
case("csharp", "overload-arity", "overloads are told apart by the number of arguments",
     CS, {"A.cs": "namespace App\n{\n    public class A\n    {\n        int Send(int a) { return a; }\n\n"
                  "        int Send(int a, int b) { return a + b; }\n\n        public int Run()\n        {\n"
                  "            return Send(1, 2);\n        }\n    }\n}\n"},
     [("A.Run", "calls", "A.Send")])
case("csharp", "static-via-class", "`C.M()` is C's static method",
     CS, {"A.cs": "namespace App\n{\n    public static class Util\n    {\n        public static int Twice(int x) { return 2 * x; }\n    }\n\n"
                  "    public class A\n    {\n        public int Run()\n        {\n            return Util.Twice(2);\n        }\n    }\n}\n"},
     [("A.Run", "calls", "Util.Twice")])
case("csharp", "interface-typed", "a call through an interface type is the interface's member",
     CS, {"A.cs": "namespace App\n{\n    public interface IStore\n    {\n        int Find();\n    }\n\n    public class A\n    {\n"
                  "        public int Run(IStore s)\n        {\n            return s.Find();\n        }\n    }\n}\n"},
     [("A.Run", "calls", "IStore.Find")])
case("csharp", "nested-type", "a nested type is a member of the type around it",
     CS, {"A.cs": "namespace App\n{\n    public class Outer\n    {\n        public class Inner\n        {\n"
                  "            public int Find() { return 1; }\n        }\n    }\n\n    public class A\n    {\n"
                  "        public int Run(Outer.Inner i)\n        {\n            return i.Find();\n        }\n    }\n}\n"},
     [("A.Run", "calls", "Outer.Inner.Find")])
case("csharp", "generic-argument", "`Page<Item>`'s `T Head` is an Item",
     CS, {"A.cs": "namespace App\n{\n    public class Item\n    {\n        public int Size() { return 1; }\n    }\n\n"
                  "    public class Other\n    {\n        public int Size() { return 2; }\n    }\n\n    public class Page<T>\n    {\n"
                  "        public T Head { get; set; }\n    }\n\n    public class A\n    {\n        public int Run(Page<Item> b)\n        {\n"
                  "            return b.Head.Size();\n        }\n    }\n}\n"},
     [("A.Run", "calls", "Item.Size")], [("A.Run", "calls", "Other.Size")])

# --- Rust ---------------------------------------------------------------------
RS = "The Rust Reference, 'Names' (name resolution, scopes, paths), 'Method call expressions', 'Field access expressions'"
CARGO = "[package]\nname = \"demo\"\nversion = \"0.1.0\"\n"
case("rust", "use-path", "`use crate::m::f;` binds f",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub mod m;\npub mod a;\n", "src/m.rs": "pub fn f() -> i32 {\n    1\n}\n",
          "src/a.rs": "use crate::m::f;\n\npub fn run() -> i32 {\n    f()\n}\n"},
     [("run", "calls", "f")])
case("rust", "use-alias", "`use m::f as g;` binds g",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub mod m;\npub mod a;\n", "src/m.rs": "pub fn f() -> i32 {\n    1\n}\n",
          "src/a.rs": "use crate::m::f as g;\n\npub fn run() -> i32 {\n    g()\n}\n"},
     [("run", "calls", "f")])
case("rust", "glob-import", "`use m::*;` binds m's public names",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub mod m;\npub mod a;\n", "src/m.rs": "pub fn f() -> i32 {\n    1\n}\n",
          "src/a.rs": "use crate::m::*;\n\npub fn run() -> i32 {\n    f()\n}\n"},
     [("run", "calls", "f")])
case("rust", "qualified-path", "`crate::m::f()` names f by its path",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub mod m;\npub mod a;\n", "src/m.rs": "pub fn f() -> i32 {\n    1\n}\n",
          "src/a.rs": "pub fn run() -> i32 {\n    crate::m::f()\n}\n"},
     [("run", "calls", "f")])
case("rust", "super", "`super::f()` is f of the parent module",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub fn f() -> i32 {\n    1\n}\n\nmod inner {\n    pub fn run() -> i32 {\n        super::f()\n    }\n}\n"},
     [("inner.run", "calls", "f")])
case("rust", "re-export", "`pub use a::T;` makes T a name of the re-exporting module",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "mod agent;\npub mod a;\npub use agent::Agent;\n",
          "src/agent.rs": "pub struct Agent;\n\nimpl Agent {\n    pub fn make() -> Agent {\n        Agent\n    }\n}\n",
          "src/a.rs": "pub fn run() -> crate::Agent {\n    crate::Agent::make()\n}\n"},
     [("run", "calls", "Agent.make")])
case("rust", "associated-function", "`T::f()` is an associated function of T",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub struct Repo;\n\nimpl Repo {\n    pub fn new() -> Repo {\n        Repo\n    }\n}\n\n"
                                            "pub fn run() -> Repo {\n    Repo::new()\n}\n"},
     [("run", "calls", "Repo.new")])
case("rust", "method-on-local", "a method call on a local is a method of its type",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub struct Repo;\n\nimpl Repo {\n    pub fn new() -> Repo {\n        Repo\n    }\n"
                                            "    pub fn find(&self) -> i32 {\n        1\n    }\n}\n\npub struct Other;\n\n"
                                            "impl Other {\n    pub fn find(&self) -> i32 {\n        2\n    }\n}\n\n"
                                            "pub fn run() -> i32 {\n    let r = Repo::new();\n    r.find()\n}\n"},
     [("run", "calls", "Repo.find")], [("run", "calls", "Other.find")])
case("rust", "self-type", "`Self::f()` is the type the impl is for",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub struct Repo;\n\nimpl Repo {\n    fn helper() -> i32 {\n        1\n    }\n"
                                            "    pub fn run() -> i32 {\n        Self::helper()\n    }\n}\n"},
     [("Repo.run", "calls", "Repo.helper")])
case("rust", "field-not-method", "`x.f` with no call is the field f, whatever methods are named f",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub struct Buf;\n\nimpl Buf {\n    pub fn fill(&mut self) {}\n}\n\n"
                                            "pub trait Source {\n    fn input(&self) -> i32;\n}\n\npub struct Lazy {\n    input: Buf,\n}\n\n"
                                            "impl Source for Lazy {\n    fn input(&self) -> i32 {\n        1\n    }\n}\n\n"
                                            "impl Lazy {\n    pub fn run(&mut self) {\n        self.input.fill();\n    }\n}\n"},
     [("Lazy.run", "calls", "Buf.fill")])
case("rust", "trait-method", "a trait's method is a method of the types that implement it",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub trait Named {\n    fn label(&self) -> i32;\n}\n\npub struct Repo;\n\n"
                                            "impl Named for Repo {\n    fn label(&self) -> i32 {\n        1\n    }\n}\n\n"
                                            "pub fn run(r: &Repo) -> i32 {\n    r.label()\n}\n"},
     [("run", "calls", "Repo.label")])
case("rust", "impl-in-another-file", "an impl block can be written in another module than the type",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub mod repo;\npub mod ops;\n\npub fn run(r: &repo::Repo) -> i32 {\n    r.find()\n}\n",
          "src/repo.rs": "pub struct Repo;\n", "src/ops.rs": "use crate::repo::Repo;\n\nimpl Repo {\n    pub fn find(&self) -> i32 {\n        1\n    }\n}\n"},
     [("run", "calls", "Repo.find")])
case("rust", "shadowing", "a later `let` of the same name is the name from there on",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub struct Repo;\n\nimpl Repo {\n    pub fn new() -> Repo {\n        Repo\n    }\n"
                                            "    pub fn find(&self) -> i32 {\n        1\n    }\n}\n\n"
                                            "pub struct Other;\n\nimpl Other {\n    pub fn new() -> Other {\n        Other\n    }\n"
                                            "    pub fn find(&self) -> i32 {\n        2\n    }\n}\n\n"
                                            "pub fn run() -> i32 {\n    let r = Other::new();\n    let r = Repo::new();\n    r.find()\n}\n"},
     [("run", "calls", "Repo.find")], [("run", "calls", "Other.find")])
case("rust", "macro-arguments", "the arguments of a macro are expressions like any other",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub fn count() -> i32 {\n    1\n}\n\npub fn run() {\n    assert_eq!(count(), 1);\n}\n"},
     [("run", "calls", "count")])
case("rust", "enum-variant", "`E::V(…)` constructs a variant of E",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub enum Shape {\n    Circle(i32),\n    Square(i32),\n}\n\n"
                                            "pub fn run() -> Shape {\n    Shape::Circle(1)\n}\n"},
     [("run", "references", "Shape")])
case("rust", "field-type", "a field's declared type gives its methods",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub struct Repo;\n\nimpl Repo {\n    pub fn find(&self) -> i32 {\n        1\n    }\n}\n\n"
                                            "pub struct Other;\n\nimpl Other {\n    pub fn find(&self) -> i32 {\n        2\n    }\n}\n\n"
                                            "pub struct Svc {\n    repo: Repo,\n}\n\nimpl Svc {\n    pub fn run(&self) -> i32 {\n"
                                            "        self.repo.find()\n    }\n}\n"},
     [("Svc.run", "calls", "Repo.find")], [("Svc.run", "calls", "Other.find")])

# --- Swift --------------------------------------------------------------------
SW = "The Swift Programming Language, 'Declarations', 'Extensions', 'Protocols' (member lookup is the compiler's)"
case("swift", "method-on-parameter", "a method call on a typed value is the type's method",
     SW, {"a.swift": "class Repo {\n  func find() -> Int {\n    return 1\n  }\n}\n\nclass Other {\n  func find() -> Int {\n    return 2\n  }\n}\n\n"
                     "func run(r: Repo) -> Int {\n  return r.find()\n}\n"},
     [("run", "calls", "Repo.find")], [("run", "calls", "Other.find")])
case("swift", "implicit-self", "an unqualified name in a type is a member of it",
     SW, {"a.swift": "class A {\n  func helper() -> Int {\n    return 1\n  }\n\n  func run() -> Int {\n    return helper()\n  }\n}\n"},
     [("A.run", "calls", "A.helper")])
case("swift", "extension", "an extension's methods are members of the extended type",
     SW, {"a.swift": "class Repo {\n}\n\nextension Repo {\n  func size() -> Int {\n    return 1\n  }\n}\n\n"
                     "func run(r: Repo) -> Int {\n  return r.size()\n}\n"},
     [("run", "calls", "size")])
case("swift", "static", "`T.make()` is T's static method",
     SW, {"a.swift": "struct Repo {\n  static func make() -> Repo {\n    return Repo()\n  }\n}\n\nfunc run() -> Repo {\n  return Repo.make()\n}\n"},
     [("run", "calls", "Repo.make")])
case("swift", "initializer", "`T(…)` calls T's initializer",
     SW, {"a.swift": "struct Repo {\n  init(size: Int) {\n  }\n}\n\nfunc run() -> Repo {\n  return Repo(size: 1)\n}\n"},
     [("run", "calls", "Repo")])
case("swift", "super", "`super.m()` is the superclass's m",
     SW, {"a.swift": "class Base {\n  func m() -> Int {\n    return 1\n  }\n}\n\nclass A: Base {\n  override func m() -> Int {\n"
                     "    return super.m()\n  }\n}\n"},
     [("A.m", "calls", "Base.m")])
case("swift", "inherited", "a member not on the class is looked up on its superclass",
     SW, {"a.swift": "class Base {\n  func helper() -> Int {\n    return 1\n  }\n}\n\nclass A: Base {\n  func run() -> Int {\n"
                     "    return helper()\n  }\n}\n"},
     [("A.run", "calls", "Base.helper")])
case("swift", "protocol-typed", "a call through a protocol type is the protocol's requirement",
     SW, {"a.swift": "protocol Store {\n  func find() -> Int\n}\n\nfunc run(s: Store) -> Int {\n  return s.find()\n}\n"},
     [("run", "calls", "Store.find")])
case("swift", "protocol-extension", "a protocol extension's method is a member of every conforming type",
     SW, {"a.swift": "protocol Named {\n}\n\nextension Named {\n  func label() -> String {\n    return \"x\"\n  }\n}\n\n"
                     "struct Repo: Named {\n}\n\nfunc run(r: Repo) -> String {\n  return r.label()\n}\n"},
     [("run", "calls", "label")])
case("swift", "property-type", "a stored property's declared type is a use of that type, and gives its members",
     SW, {"a.swift": "class Repo {\n  func find() -> Int {\n    return 1\n  }\n}\n\nclass Other {\n  func find() -> Int {\n    return 2\n  }\n}\n\n"
                     "class Svc {\n  var repo: Repo = Repo()\n\n  func run() -> Int {\n    return repo.find()\n  }\n}\n"},
     [("Svc", "references", "Repo"), ("Svc.run", "calls", "Repo.find")], [("Svc.run", "calls", "Other.find")])
case("swift", "implicit-member", "`.name` where a type is expected is a static member of that type",
     SW, {"a.swift": "struct Header {\n  static func json() -> Header {\n    return Header()\n  }\n}\n\n"
                     "func send(_ h: Header) {\n}\n\nfunc run() {\n  send(.json())\n}\n"},
     [("run", "calls", "Header.json")])
case("swift", "local-typed-by-value", "a `let` assigned an initializer call has that type",
     SW, {"a.swift": "class Repo {\n  func find() -> Int {\n    return 1\n  }\n}\n\nclass Other {\n  func find() -> Int {\n    return 2\n  }\n}\n\n"
                     "func run() -> Int {\n  let r = Repo()\n  return r.find()\n}\n"},
     [("run", "calls", "Repo.find")], [("run", "calls", "Other.find")])
case("swift", "nested-type", "a nested type is a member of the type around it",
     SW, {"a.swift": "struct Outer {\n  struct Inner {\n    func find() -> Int {\n      return 1\n    }\n  }\n}\n\n"
                     "func run(i: Outer.Inner) -> Int {\n  return i.find()\n}\n"},
     [("run", "calls", "Outer.Inner.find")])

# --- C ------------------------------------------------------------------------
CC = "ISO C, 6.2.1 'Scopes of identifiers', 6.2.2 'Linkages of identifiers'"
case("c", "same-file", "a function is in scope after its declaration in the file",
     CC, {"a.c": "static int helper(void) {\n    return 1;\n}\n\nint run(void) {\n    return helper();\n}\n"},
     [("run", "calls", "helper")])
case("c", "external-linkage", "a function declared in an included header is the one another file defines",
     CC, {"util.h": "int twice(int x);\n", "util.c": "#include \"util.h\"\n\nint twice(int x) {\n    return 2 * x;\n}\n",
          "a.c": "#include \"util.h\"\n\nint run(void) {\n    return twice(2);\n}\n"},
     [("run", "calls", "util.c:twice")])
case("c", "static-wins", "a `static` function of the file wins over an external one of its name",
     CC, {"other.c": "int helper(void) {\n    return 0;\n}\n",
          "a.c": "static int helper(void) {\n    return 1;\n}\n\nint run(void) {\n    return helper();\n}\n"},
     [("run", "calls", "a.c:helper")], [("run", "calls", "other.c:helper")])
case("c", "local-hides-function", "a local variable hides a function of its name",
     CC, {"a.c": "int helper(void) {\n    return 0;\n}\n\nint run(int (*make)(void)) {\n    int (*helper)(void) = make;\n"
                 "    return helper();\n}\n"},
     [], [("run", "calls", "a.c:helper")])
case("c", "function-pointer-slot", "a function put in a struct's slot is what a call through the slot reaches",
     CC, {"a.c": "struct ops {\n    int (*read)(void);\n};\n\nstatic int net_read(void) {\n    return 1;\n}\n\n"
                 "static struct ops net = { .read = net_read };\n"},
     [("ops.read", "dispatches_to", "net_read")])
case("c", "typedef-struct", "a `typedef struct` names the struct written in it",
     CC, {"a.c": "typedef struct reader {\n    int pos;\n} reader;\n\nint run(reader *r) {\n    return r->pos;\n}\n"},
     [("run", "reads", "reader.pos")])

# --- C++ ----------------------------------------------------------------------
CPP = "ISO C++, [basic.lookup] (unqualified, qualified and argument-dependent lookup), [class.member.lookup], [dcl.ambig.res]"
case("cpp", "member-declared-in-header", "a call on an object is the member the class declares, defined in another file",
     CPP, {"repo.h": "class Repo {\n public:\n  int Find();\n};\n", "repo.cc": "#include \"repo.h\"\n\nint Repo::Find() {\n  return 1;\n}\n",
           "a.cc": "#include \"repo.h\"\n\nint run(Repo& r) {\n  return r.Find();\n}\n"},
     [("run", "calls", "Repo.Find")])
case("cpp", "namespace-qualified", "`ns::f()` is f of the namespace",
     CPP, {"a.cc": "namespace util {\nint twice(int x) {\n  return 2 * x;\n}\n}  // namespace util\n\nint run() {\n  return util::twice(2);\n}\n"},
     [("run", "calls", "util.twice")])
case("cpp", "using-namespace", "`using namespace ns;` makes its names available unqualified",
     CPP, {"a.cc": "namespace util {\nint twice(int x) {\n  return 2 * x;\n}\n}  // namespace util\n\nusing namespace util;\n\n"
                   "int run() {\n  return twice(2);\n}\n"},
     [("run", "calls", "util.twice")])
case("cpp", "enclosing-namespace", "a name is looked up in the namespaces around the use",
     CPP, {"a.cc": "namespace app {\nint helper() {\n  return 1;\n}\n\nnamespace inner {\nint run() {\n  return helper();\n}\n"
                   "}  // namespace inner\n}  // namespace app\n"},
     [("run", "calls", "app.helper")])
case("cpp", "implicit-this", "an unqualified name in a member function is a member of the class",
     CPP, {"a.cc": "class A {\n public:\n  int Helper() {\n    return 1;\n  }\n  int Run() {\n    return Helper();\n  }\n};\n"},
     [("A.Run", "calls", "A.Helper")])
case("cpp", "inherited", "a member not in the class is looked up in its bases",
     CPP, {"a.cc": "class Base {\n public:\n  int Helper() {\n    return 1;\n  }\n};\n\nclass A : public Base {\n public:\n"
                   "  int Run() {\n    return Helper();\n  }\n};\n"},
     [("A.Run", "calls", "Base.Helper")])
case("cpp", "static-member", "`C::f()` is C's static member",
     CPP, {"a.cc": "class Repo {\n public:\n  static Repo Make() {\n    return Repo();\n  }\n};\n\nRepo run() {\n  return Repo::Make();\n}\n"},
     [("run", "calls", "Repo.Make")])
case("cpp", "local-object", "`T x;` then `x.m()` is T's member",
     CPP, {"a.cc": "class Repo {\n public:\n  int Find() {\n    return 1;\n  }\n};\n\nclass Other {\n public:\n  int Find() {\n    return 2;\n  }\n};\n\n"
                   "int run() {\n  Repo r;\n  return r.Find();\n}\n"},
     [("run", "calls", "Repo.Find")], [("run", "calls", "Other.Find")])
case("cpp", "vexing-parse", "`T x(f());` declares a variable when f() is no type",
     CPP, {"a.cc": "class Random {\n public:\n  explicit Random(int s) {}\n  int Next() {\n    return 1;\n  }\n};\n\nint seed() {\n  return 3;\n}\n\n"
                   "int run() {\n  Random rnd(seed());\n  return rnd.Next();\n}\n"},
     [("run", "calls", "seed"), ("run", "calls", "Random.Next")])
case("cpp", "overload-arity", "overloads are told apart by the number of arguments",
     CPP, {"a.cc": "int send(int a) {\n  return a;\n}\n\nint send(int a, int b) {\n  return a + b;\n}\n\nint run() {\n  return send(1, 2);\n}\n"},
     [("run", "calls", "send")])
case("cpp", "pointer-member", "`p->m()` is the member of the pointed-to class",
     CPP, {"a.cc": "class Repo {\n public:\n  int Find() {\n    return 1;\n  }\n};\n\nclass Other {\n public:\n  int Find() {\n    return 2;\n  }\n};\n\n"
                   "int run(Repo* r) {\n  return r->Find();\n}\n"},
     [("run", "calls", "Repo.Find")], [("run", "calls", "Other.Find")])
case("cpp", "type-alias", "`using R = Repo;` names Repo",
     CPP, {"a.cc": "class Repo {\n public:\n  int Find() {\n    return 1;\n  }\n};\n\nusing R = Repo;\n\nint run(R& r) {\n  return r.Find();\n}\n"},
     [("run", "calls", "Repo.Find")])
case("cpp", "field-type", "a data member's declared type gives its members",
     CPP, {"a.cc": "class Repo {\n public:\n  int Find() {\n    return 1;\n  }\n};\n\nclass Other {\n public:\n  int Find() {\n    return 2;\n  }\n};\n\n"
                   "class Svc {\n public:\n  int Run() {\n    return repo_.Find();\n  }\n\n private:\n  Repo repo_;\n};\n"},
     [("Svc.Run", "calls", "Repo.Find")], [("Svc.Run", "calls", "Other.Find")])

# --- Ruby ---------------------------------------------------------------------
RB = "Ruby documentation, 'Calling Methods' and Module#ancestors (method lookup), 'Modules and Classes' (constant lookup)"
case("ruby", "implicit-self", "a bare name that is no local is a method of self",
     RB, {"a.rb": "class A\n  def helper\n    1\n  end\n\n  def run\n    helper\n  end\nend\n"},
     [("A.run", "calls", "A.helper")])
case("ruby", "local-hides-method", "a local variable hides a method of its name",
     RB, {"a.rb": "class A\n  def helper\n    1\n  end\n\n  def run(make)\n    helper = make\n    helper\n  end\nend\n"},
     [], [("A.run", "calls", "A.helper")])
case("ruby", "superclass", "a method not in the class is looked up in its superclass",
     RB, {"a.rb": "class Base\n  def helper\n    1\n  end\nend\n\nclass A < Base\n  def run\n    helper\n  end\nend\n"},
     [("A.run", "calls", "Base.helper")])
case("ruby", "include", "the methods of an included module are instance methods",
     RB, {"a.rb": "module Named\n  def label\n    'x'\n  end\nend\n\nclass A\n  include Named\n\n  def run\n    label\n  end\nend\n"},
     [("A.run", "calls", "Named.label")])
case("ruby", "extend", "the methods of an extended module are methods of the class itself",
     RB, {"a.rb": "module Registry\n  def register(x)\n    x\n  end\nend\n\nclass A\n  extend Registry\nend\n\ndef run\n  A.register(1)\nend\n"},
     [("run", "calls", "Registry.register")])
case("ruby", "class-method", "`def self.m` is called through the class",
     RB, {"a.rb": "class Repo\n  def self.make\n    new\n  end\nend\n\ndef run\n  Repo.make\nend\n"},
     [("run", "calls", "Repo.make")])
case("ruby", "new-then-method", "`C.new.m` is C's instance method",
     RB, {"a.rb": "class Repo\n  def find\n    1\n  end\nend\n\nclass Other\n  def find\n    2\n  end\nend\n\ndef run\n  Repo.new.find\nend\n"},
     [("run", "calls", "Repo.find")], [("run", "calls", "Other.find")])
case("ruby", "local-typed-by-value", "a local assigned `C.new` has C's methods",
     RB, {"a.rb": "class Repo\n  def find\n    1\n  end\nend\n\nclass Other\n  def find\n    2\n  end\nend\n\ndef run\n  r = Repo.new\n  r.find\nend\n"},
     [("run", "calls", "Repo.find")], [("run", "calls", "Other.find")])
case("ruby", "super", "`super` is the method of the same name further up",
     RB, {"a.rb": "class Base\n  def m\n    1\n  end\nend\n\nclass A < Base\n  def m\n    super\n  end\nend\n"},
     [("A.m", "calls", "Base.m")])
case("ruby", "nested-constant", "`A::B` is the constant B of A",
     RB, {"a.rb": "module Shop\n  class Order\n    def total\n      1\n    end\n  end\nend\n\ndef run\n  Shop::Order.new.total\nend\n"},
     [("run", "calls", "Shop.Order.total")])
case("ruby", "lexical-constant", "a constant is looked up in the modules written around the use",
     RB, {"a.rb": "module Shop\n  class Order\n    def total\n      1\n    end\n  end\n\n  class Cart\n    def run\n      Order.new.total\n"
                  "    end\n  end\nend\n"},
     [("Shop.Cart.run", "calls", "Shop.Order.total")])
case("ruby", "reopened-class", "a class opened again in another file is the same class",
     RB, {"a.rb": "class Repo\n  def find\n    1\n  end\nend\n", "b.rb": "require_relative 'a'\n\nclass Repo\n  def run\n    find\n  end\nend\n"},
     [("Repo.run", "calls", "Repo.find")])
case("ruby", "instance-variable-typed", "an instance variable assigned `C.new` in initialize has C's methods",
     RB, {"a.rb": "class Repo\n  def find\n    1\n  end\nend\n\nclass Other\n  def find\n    2\n  end\nend\n\n"
                  "class Svc\n  def initialize\n    @repo = Repo.new\n  end\n\n  def run\n    @repo.find\n  end\nend\n"},
     [("Svc.run", "calls", "Repo.find")], [("Svc.run", "calls", "Other.find")])

# --- PHP ----------------------------------------------------------------------
PHP = "PHP Manual, 'Namespaces: Name resolution rules', 'Classes and Objects' (inheritance, traits, static)"
case("php", "use", "`use N\\C;` binds C",
     PHP, {"lib/Repo.php": "<?php\nnamespace Lib;\n\nclass Repo\n{\n    public function find(): int\n    {\n        return 1;\n    }\n}\n",
           "other/Repo.php": "<?php\nnamespace Other;\n\nclass Repo\n{\n    public function find(): int\n    {\n        return 2;\n    }\n}\n",
           "app/A.php": "<?php\nnamespace App;\n\nuse Lib\\Repo;\n\nclass A\n{\n    public function run(Repo $r): int\n    {\n"
                        "        return $r->find();\n    }\n}\n"},
     [("A.run", "calls", "lib/Repo.php:Repo.find")], [("A.run", "calls", "other/Repo.php:Repo.find")])
case("php", "use-alias", "`use N\\C as D;` binds D",
     PHP, {"lib/Repo.php": "<?php\nnamespace Lib;\n\nclass Repo\n{\n    public function find(): int\n    {\n        return 1;\n    }\n}\n",
           "app/A.php": "<?php\nnamespace App;\n\nuse Lib\\Repo as Store;\n\nclass A\n{\n    public function run(Store $r): int\n    {\n"
                        "        return $r->find();\n    }\n}\n"},
     [("A.run", "calls", "Repo.find")])
case("php", "fully-qualified", "`\\N\\C` names C whatever the current namespace",
     PHP, {"lib/Repo.php": "<?php\nnamespace Lib;\n\nclass Repo\n{\n    public static function make(): Repo\n    {\n        return new Repo();\n    }\n}\n",
           "app/A.php": "<?php\nnamespace App;\n\nclass A\n{\n    public function run()\n    {\n        return \\Lib\\Repo::make();\n    }\n}\n"},
     [("A.run", "calls", "Repo.make")])
case("php", "qualified-relative", "`Sub\\C` is relative to the current namespace",
     PHP, {"app/Handler/Check.php": "<?php\nnamespace App\\Handler;\n\nclass Check\n{\n    public static function ok(): bool\n    {\n        return true;\n    }\n}\n",
           "app/A.php": "<?php\nnamespace App;\n\nclass A\n{\n    public function run(): bool\n    {\n        return Handler\\Check::ok();\n    }\n}\n"},
     [("A.run", "calls", "Check.ok")])
case("php", "same-namespace", "an unqualified class name is of the current namespace",
     PHP, {"app/Repo.php": "<?php\nnamespace App;\n\nclass Repo\n{\n    public function find(): int\n    {\n        return 1;\n    }\n}\n",
           "app/A.php": "<?php\nnamespace App;\n\nclass A\n{\n    public function run(Repo $r): int\n    {\n        return $r->find();\n    }\n}\n"},
     [("A.run", "calls", "Repo.find")])
case("php", "this", "`$this->m()` is the class's method",
     PHP, {"a.php": "<?php\nclass A\n{\n    private function helper(): int\n    {\n        return 1;\n    }\n\n    public function run(): int\n    {\n"
                    "        return $this->helper();\n    }\n}\n"},
     [("A.run", "calls", "A.helper")])
case("php", "inherited", "a method not in the class is looked up in its parent",
     PHP, {"a.php": "<?php\nclass Base\n{\n    protected function helper(): int\n    {\n        return 1;\n    }\n}\n\nclass A extends Base\n{\n"
                    "    public function run(): int\n    {\n        return $this->helper();\n    }\n}\n"},
     [("A.run", "calls", "Base.helper")])
case("php", "parent", "`parent::m()` is the parent class's m",
     PHP, {"a.php": "<?php\nclass Base\n{\n    public function m(): int\n    {\n        return 1;\n    }\n}\n\nclass A extends Base\n{\n"
                    "    public function m(): int\n    {\n        return parent::m();\n    }\n}\n"},
     [("A.m", "calls", "Base.m")])
case("php", "self-static", "`self::m()` and `static::m()` are the class's own",
     PHP, {"a.php": "<?php\nclass A\n{\n    public static function helper(): int\n    {\n        return 1;\n    }\n\n"
                    "    public static function run(): int\n    {\n        return self::helper() + static::helper();\n    }\n}\n"},
     [("A.run", "calls", "A.helper")])
case("php", "trait", "the methods of a used trait are methods of the class",
     PHP, {"a.php": "<?php\ntrait Named\n{\n    public function label(): string\n    {\n        return 'x';\n    }\n}\n\nclass A\n{\n    use Named;\n\n"
                    "    public function run(): string\n    {\n        return $this->label();\n    }\n}\n"},
     [("A.run", "calls", "Named.label")])
case("php", "typed-property", "a property's declared type gives its members",
     PHP, {"a.php": "<?php\nclass Repo\n{\n    public function find(): int\n    {\n        return 1;\n    }\n}\n\nclass Other\n{\n"
                    "    public function find(): int\n    {\n        return 2;\n    }\n}\n\nclass Svc\n{\n    private Repo $repo;\n\n"
                    "    public function run(): int\n    {\n        return $this->repo->find();\n    }\n}\n"},
     [("Svc.run", "calls", "Repo.find")], [("Svc.run", "calls", "Other.find")])
case("php", "new-then-method", "a variable assigned `new C()` has C's methods",
     PHP, {"a.php": "<?php\nclass Repo\n{\n    public function find(): int\n    {\n        return 1;\n    }\n}\n\nclass Other\n{\n"
                    "    public function find(): int\n    {\n        return 2;\n    }\n}\n\nfunction run(): int\n{\n    $r = new Repo();\n"
                    "    return $r->find();\n}\n"},
     [("run", "calls", "Repo.find")], [("run", "calls", "Other.find")])
case("php", "function-fallback", "an unqualified function call falls back to the global function",
     PHP, {"lib/util.php": "<?php\nfunction twice(int $x): int\n{\n    return 2 * $x;\n}\n",
           "app/A.php": "<?php\nnamespace App;\n\nclass A\n{\n    public function run(): int\n    {\n        return twice(2);\n    }\n}\n"},
     [("A.run", "calls", "twice")])
case("php", "use-function", "`use function N\\f;` binds f",
     PHP, {"lib/util.php": "<?php\nnamespace Lib;\n\nfunction twice(int $x): int\n{\n    return 2 * $x;\n}\n",
           "app/A.php": "<?php\nnamespace App;\n\nuse function Lib\\twice;\n\nclass A\n{\n    public function run(): int\n    {\n"
                        "        return twice(2);\n    }\n}\n"},
     [("A.run", "calls", "twice")])


# === the second round: types the source writes, more import forms, nesting, ===
# === functions passed as values, and what each language has of its own      ===

# Two types with a method of one name: only the type says which is called.
PY2 = "class Repo:\n    def find(self):\n        return 1\n\n\nclass Other:\n    def find(self):\n        return 2\n\n\n"
PY_BOTH = ([("run", "calls", "Repo.find")], [("run", "calls", "Other.find")])
case("python", "return-annotation", "a call's annotated result gives the methods of what it returns",
     PY, {"a.py": PY2 + "def make() -> Repo:\n    return Repo()\n\n\ndef run():\n    return make().find()\n"}, *PY_BOTH)
case("python", "class-attribute-annotation", "an attribute annotated in the class body has that type",
     PY, {"a.py": PY2 + "class Svc:\n    repo: Repo\n\n    def run(self):\n        return self.repo.find()\n"},
     [("Svc.run", "calls", "Repo.find")], [("Svc.run", "calls", "Other.find")])
case("python", "property", "a property read is what its getter returns",
     PY, {"a.py": PY2 + "class Svc:\n    @property\n    def repo(self) -> Repo:\n        return Repo()\n\n"
                        "    def run(self):\n        return self.repo.find()\n"},
     [("Svc.run", "calls", "Repo.find")], [("Svc.run", "calls", "Other.find")])
case("python", "with-as", "`with f() as x` binds x to what f returns",
     PY, {"a.py": PY2 + "def open_repo() -> Repo:\n    return Repo()\n\n\ndef run():\n    with open_repo() as r:\n        return r.find()\n"},
     *PY_BOTH)
case("python", "for-over-annotated-list", "a loop variable over `list[T]` is a T",
     PY, {"a.py": PY2 + "def run(repos: list[Repo]):\n    for r in repos:\n        r.find()\n"}, *PY_BOTH)
case("python", "comprehension", "a comprehension's variable over `list[T]` is a T",
     PY, {"a.py": PY2 + "def run(repos: list[Repo]):\n    return [r.find() for r in repos]\n"}, *PY_BOTH)
case("python", "module-alias", "`import pkg.m as mm` binds mm to the module",
     PY, {"pkg/__init__.py": "", "pkg/m.py": "def f():\n    return 1\n",
          "app.py": "import pkg.m as mm\n\n\ndef run():\n    return mm.f()\n"},
     [("run", "calls", "f")])
case("python", "submodule-import", "`from pkg import m` binds the submodule m",
     PY, {"pkg/__init__.py": "", "pkg/m.py": "def f():\n    return 1\n",
          "app.py": "from pkg import m\n\n\ndef run():\n    return m.f()\n"},
     [("run", "calls", "f")])
case("python", "package-reexport", "a name the package's __init__ imports is a name of the package",
     PY, {"pkg/__init__.py": "from .m import f\n", "pkg/m.py": "def f():\n    return 1\n",
          "app.py": "from pkg import f\n\n\ndef run():\n    return f()\n"},
     [("run", "calls", "f")])
case("python", "function-local-import", "an import inside a function binds the name in that function",
     PY, {"pkg/__init__.py": "", "pkg/m.py": "def f():\n    return 1\n",
          "app.py": "def run():\n    from pkg.m import f\n    return f()\n"},
     [("run", "calls", "f")])
case("python", "pytest-fixture", "pytest hands a test the result of the fixture its parameter names (conftest.py)",
     "pytest documentation, 'How to use fixtures' (requesting a fixture by its name; conftest.py)",
     {"app.py": "class Runner:\n    def invoke(self):\n        return 1\n\n\nclass Context:\n    def invoke(self):\n        return 2\n",
      "tests/conftest.py": "import pytest\n\nfrom app import Runner\n\n\n@pytest.fixture\ndef runner():\n    return Runner()\n",
      "tests/test_app.py": "def test_run(runner):\n    assert runner.invoke() == 1\n"},
     [("test_run", "calls", "Runner.invoke")], [("test_run", "calls", "Context.invoke")])
case("python", "except-clause", "`except E as e:` and `except (A, B):` name the types caught",
     PY, {"a.py": "class Exit(Exception):\n    pass\n\n\nclass Abort(Exception):\n    pass\n\n\n"
                  "def run():\n    try:\n        pass\n    except Exit as e:\n        raise\n    except (Abort, ValueError):\n        pass\n"},
     [("run", "references", "Exit"), ("run", "references", "Abort")])
case("python", "mro-first-base", "of two bases with the method, the first listed wins",
     PY, {"a.py": "class Left:\n    def m(self):\n        return 1\n\n\nclass Right:\n    def m(self):\n        return 2\n\n\n"
                  "class A(Left, Right):\n    def run(self):\n        return self.m()\n"},
     [("A.run", "calls", "Left.m")], [("A.run", "calls", "Right.m")])
case("python", "mro-second-base", "a method only the second base has is found there",
     PY, {"a.py": "class Left:\n    pass\n\n\nclass Right:\n    def m(self):\n        return 2\n\n\n"
                  "class A(Left, Right):\n    def run(self):\n        return self.m()\n"},
     [("A.run", "calls", "Right.m")])
case("python", "nested-class", "a class nested in a class is reached through it",
     PY, {"a.py": "class Outer:\n    class Inner:\n        def find(self):\n            return 1\n\n\ndef run():\n    return Outer.Inner().find()\n"},
     [("run", "calls", "Outer.Inner.find")])
case("python", "staticmethod-via-self", "a static method is found on the instance too",
     PY, {"a.py": "class A:\n    @staticmethod\n    def helper():\n        return 1\n\n    def run(self):\n        return self.helper()\n"},
     [("A.run", "calls", "A.helper")])
case("python", "class-body-call", "a call in a class body is the class's",
     PY, {"a.py": "def make():\n    return 1\n\n\nclass A:\n    limit = make()\n"},
     [("A", "calls", "make")])
case("python", "function-as-value", "a function passed by name is a use of it",
     PY, {"a.py": "def handle(x):\n    return x\n\n\ndef run(items):\n    return list(map(handle, items))\n"},
     [("run", "indirect_call", "handle")])
case("python", "inherited-from-aliased-import", "a base imported under another name is still the base",
     PY, {"base.py": "class Base:\n    def m(self):\n        return 1\n",
          "a.py": "from base import Base as Parent\n\n\nclass A(Parent):\n    def run(self):\n        return self.m()\n"},
     [("A.run", "calls", "Base.m")])

JS2 = "class Repo {\n  find() {\n    return 1\n  }\n}\n\nclass Other {\n  find() {\n    return 2\n  }\n}\n\n"
case("javascript", "arrow-const", "a `const` assigned an arrow function is a function of its scope",
     JS, {"a.js": "const helper = () => 1\n\nfunction run() {\n  return helper()\n}\n"},
     [("run", "calls", "helper")])
case("javascript", "function-expression", "a `const` assigned a function expression is a function",
     JS, {"a.js": "const helper = function () {\n  return 1\n}\n\nfunction run() {\n  return helper()\n}\n"},
     [("run", "calls", "helper")])
case("javascript", "object-literal-method", "a method of an object literal is called through the object",
     JS, {"a.js": "const api = {\n  fetchAll() {\n    return 1\n  },\n}\n\nfunction run() {\n  return api.fetchAll()\n}\n"},
     [("run", "calls", "fetchAll")])
case("javascript", "object-literal-this", "`this` in an object literal's method is the object",
     JS, {"a.js": "const api = {\n  load() {\n    return 1\n  },\n  run() {\n    return this.load()\n  },\n}\n"},
     [("run", "calls", "load")])
case("javascript", "commonjs-default", "`module.exports = f` is what `require` gives",
     JS, {"m.js": "function make() {\n  return 1\n}\n\nmodule.exports = make\n",
          "a.js": "const build = require('./m')\n\nfunction run() {\n  return build()\n}\n\nmodule.exports = run\n"},
     [("run", "calls", "make")])
case("javascript", "commonjs-member", "`exports.f = …` is a member of what `require` gives",
     JS, {"m.js": "exports.f = function f() {\n  return 1\n}\n",
          "a.js": "const m = require('./m')\n\nfunction run() {\n  return m.f()\n}\n\nmodule.exports = run\n"},
     [("run", "calls", "f")])
case("javascript", "static-method", "`C.m()` is C's static method",
     JS, {"a.js": "class Repo {\n  static make() {\n    return new Repo()\n  }\n}\n\nfunction run() {\n  return Repo.make()\n}\n"},
     [("run", "calls", "Repo.make")])
case("javascript", "default-export-class", "a default-exported class is what a default import names",
     JS, {"m.js": "export default class Repo {\n  find() {\n    return 1\n  }\n}\n",
          "a.js": "import Store from './m.js'\n\nexport function run() {\n  const s = new Store()\n  return s.find()\n}\n"},
     [("run", "calls", "Repo.find")])
case("javascript", "exported-arrow", "an exported `const` arrow function is importable by its name",
     JS, {"m.js": "export const f = () => 1\n",
          "a.js": "import { f } from './m.js'\n\nexport function run() {\n  return f()\n}\n"},
     [("run", "calls", "f")])
case("javascript", "function-as-value", "a function passed by name is a use of it",
     JS, {"a.js": "function handle(x) {\n  return x\n}\n\nfunction run(items) {\n  return items.map(handle)\n}\n"},
     [("run", "indirect_call", "handle")])
case("javascript", "parameter-hides-import", "a parameter hides an import of its name",
     JS, {"m.js": "export function f() {\n  return 1\n}\n",
          "a.js": "import { f } from './m.js'\n\nexport function run(f) {\n  return f()\n}\n\nexport function other() {\n  return f()\n}\n"},
     [("other", "calls", "f")], [("run", "calls", "f")])
case("javascript", "module-pattern", "`var X = (function () { function X() {} return X })()` names the function written in it",
     JS, {"a.js": "var Dataset = (function () {\n  function Dataset() {}\n  return Dataset\n})()\n\nfunction run() {\n  return new Dataset()\n}\n"},
     [("run", "calls", "Dataset")])
case("javascript", "dynamic-import", "`await import('./m.js')` is the module",
     JS, {"m.js": "export function f() {\n  return 1\n}\n",
          "a.js": "export async function run() {\n  const m = await import('./m.js')\n  return m.f()\n}\n"},
     [("run", "calls", "f")])
case("javascript", "class-field-arrow", "an arrow function in a class field sees the class's `this`",
     JS, {"a.js": "class A {\n  m() {\n    return 1\n  }\n  handle = () => {\n    return this.m()\n  }\n}\n"},
     [("A.handle", "calls", "A.m")])
case("javascript", "for-of-new", "a local assigned `new C()` keeps its type in a nested callback",
     JS, {"a.js": JS2 + "function run(items) {\n  const r = new Repo()\n  return items.map(() => r.find())\n}\n"},
     [("run", "calls", "Repo.find")], [("run", "calls", "Other.find")])

TS2 = ("export class Repo {\n  find(): number {\n    return 1\n  }\n}\n\nexport class Other {\n"
       "  find(): number {\n    return 2\n  }\n}\n\n")
TS_BOTH = ([("run", "calls", "Repo.find")], [("run", "calls", "Other.find")])
case("typescript", "dollar-name", "`$` is part of a name (`$Base`), not a sigil",
     TS, {"a.ts": "export interface $Base {\n  x: number\n}\n\nexport interface $Sub extends $Base {\n  y: number\n}\n\n"
                  "export function run(b: $Base): number {\n  return b.x\n}\n"},
     [("$Sub", "inherits", "$Base"), ("run", "references", "$Base")])
case("typescript", "type-and-value", "a name declared as a type and as a value is the value where a value is used",
     TS, {"a.ts": "export interface Kind {\n  tag: string\n}\n\nexport const Kind = {\n  make(): number {\n    return 1\n  },\n}\n\n"
                  "export function run(k: Kind): number {\n  return Kind.make()\n}\n"},
     [("run", "references", "Kind"), ("run", "calls", "make")])
case("typescript", "array-element", "an element of `T[]` is a T",
     TS, {"a.ts": TS2 + "export function run(repos: Repo[]): number {\n  return repos[0].find()\n}\n"}, *TS_BOTH)
case("typescript", "for-of", "a `for … of` variable over `T[]` is a T",
     TS, {"a.ts": TS2 + "export function run(repos: Repo[]): void {\n  for (const r of repos) {\n    r.find()\n  }\n}\n"}, *TS_BOTH)
case("typescript", "await-promise", "`await` of a `Promise<T>` is a T",
     TS, {"a.ts": TS2 + "export async function load(): Promise<Repo> {\n  return new Repo()\n}\n\n"
                        "export async function run(): Promise<number> {\n  const r = await load()\n  return r.find()\n}\n"}, *TS_BOTH)
case("typescript", "getter", "a getter's declared type gives the members of what is read",
     TS, {"a.ts": TS2 + "export class Svc {\n  get repo(): Repo {\n    return new Repo()\n  }\n  run(): number {\n    return this.repo.find()\n  }\n}\n"},
     [("Svc.run", "calls", "Repo.find")], [("Svc.run", "calls", "Other.find")])
case("typescript", "as-cast", "`x as T` is a T",
     TS, {"a.ts": TS2 + "export function run(x: unknown): number {\n  return (x as Repo).find()\n}\n"}, *TS_BOTH)
case("typescript", "non-null", "`x!` keeps x's type",
     TS, {"a.ts": TS2 + "export function run(r: Repo | null): number {\n  return r!.find()\n}\n"}, *TS_BOTH)
case("typescript", "readonly-field", "a `readonly` field's declared type gives its members",
     TS, {"a.ts": TS2 + "export class Svc {\n  readonly repo: Repo = new Repo()\n  run(): number {\n    return this.repo.find()\n  }\n}\n"},
     [("Svc.run", "calls", "Repo.find")], [("Svc.run", "calls", "Other.find")])
case("typescript", "interface-extends", "an interface that extends another is its subtype",
     TS, {"a.ts": "export interface Named {\n  label(): string\n}\n\nexport interface Titled extends Named {\n  title(): string\n}\n\n"
                  "export function run(t: Titled): string {\n  return t.label()\n}\n"},
     [("Titled", "inherits", "Named"), ("run", "calls", "Named.label")])
case("typescript", "implements", "a class that implements an interface names it",
     TS, {"a.ts": "export interface Named {\n  label(): string\n}\n\nexport class A implements Named {\n  label(): string {\n    return ''\n  }\n}\n"},
     [("A", "implements", "Named")])
case("typescript", "abstract-base", "a call through an abstract class is the abstract class's method",
     TS, {"a.ts": "export abstract class Shape {\n  abstract area(): number\n}\n\nexport function run(s: Shape): number {\n  return s.area()\n}\n"},
     [("run", "calls", "Shape.area")])
case("typescript", "type-alias", "a type alias is a type of its own, referenced where it is written",
     TS, {"a.ts": "export type Id = string\n\nexport function run(id: Id): Id {\n  return id\n}\n"},
     [("run", "references", "Id")])
case("typescript", "namespace", "`N.f()` is a function a namespace exports",
     TS, {"a.ts": "export namespace Util {\n  export function twice(x: number): number {\n    return 2 * x\n  }\n}\n\n"
                  "export function run(): number {\n  return Util.twice(2)\n}\n"},
     [("run", "calls", "Util.twice")])
case("typescript", "default-reexport", "`export { default as X } from './x'` passes the default on",
     TS, {"lib/x.ts": "export default function make(): number {\n  return 1\n}\n",
          "lib/index.ts": "export { default as build } from './x'\n",
          "a.ts": "import { build } from './lib'\n\nexport function run(): number {\n  return build()\n}\n"},
     [("run", "calls", "make")])
case("typescript", "local-typed-by-declaration", "`const r: T = …` is a T whatever it is assigned",
     TS, {"a.ts": TS2 + "declare function load(): unknown\n\nexport function run(): number {\n  const r: Repo = load() as Repo\n  return r.find()\n}\n"}, *TS_BOTH)
case("typescript", "map-value", "a value of `Map<K, V>` is a V",
     TS, {"a.ts": TS2 + "export function run(m: Map<string, Repo>): number {\n  return m.get('a')!.find()\n}\n"}, *TS_BOTH)

GO2 = ("type Repo struct{}\n\nfunc (r *Repo) Find() int {\n\treturn 1\n}\n\n"
       "type Other struct{}\n\nfunc (o *Other) Find() int {\n\treturn 2\n}\n\n")
GO_BOTH = ([("Run", "calls", "Repo.Find")], [("Run", "calls", "Other.Find")])
case("go", "range-slice", "a `range` value over `[]*T` is a *T",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\n" + GO2 + "func Run(repos []*Repo) {\n\tfor _, r := range repos {\n\t\tr.Find()\n\t}\n}\n"}, *GO_BOTH)
case("go", "slice-index", "an element of `[]*T` is a *T",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\n" + GO2 + "func Run(repos []*Repo) int {\n\treturn repos[0].Find()\n}\n"}, *GO_BOTH)
case("go", "map-value", "a value of `map[K]*T` is a *T",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\n" + GO2 + "func Run(m map[string]*Repo) int {\n\treturn m[\"a\"].Find()\n}\n"}, *GO_BOTH)
case("go", "package-variable", "a package-level variable's value gives its type",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\n" + GO2 + "var store = &Repo{}\n\nfunc Run() int {\n\treturn store.Find()\n}\n"}, *GO_BOTH)
case("go", "type-assertion", "`x.(*T)` is a *T",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\n" + GO2 + "func Run(x interface{}) int {\n\treturn x.(*Repo).Find()\n}\n"}, *GO_BOTH)
case("go", "go-and-defer", "`go f()` and `defer g()` are calls",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\nfunc worker() {}\n\nfunc cleanup() {}\n\nfunc Run() {\n\tdefer cleanup()\n\tgo worker()\n}\n"},
     [("Run", "calls", "worker"), ("Run", "calls", "cleanup")])
case("go", "function-as-value", "a function passed by name is a use of it",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\nfunc handler(x int) int {\n\treturn x\n}\n\nfunc apply(f func(int) int) int {\n\treturn f(1)\n}\n\n"
                                  "func Run() int {\n\treturn apply(handler)\n}\n"},
     [("Run", "indirect_call", "handler")])
case("go", "named-slice-type", "a method declared on a named slice type",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\ntype Names []string\n\nfunc (n Names) Count() int {\n\treturn len(n)\n}\n\n"
                                  "func Run(n Names) int {\n\treturn n.Count()\n}\n"},
     [("Run", "calls", "Names.Count")])
case("go", "embedded-interface", "the methods of an interface embedded in an interface are its own",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\ntype Reader interface {\n\tRead() int\n}\n\ntype ReadCloser interface {\n\tReader\n\tClose() int\n}\n\n"
                                  "func Run(rc ReadCloser) int {\n\treturn rc.Read()\n}\n"},
     [("Run", "calls", "Reader.Read")])
case("go", "imported-type", "a method call on a value of an imported package's type",
     GO, {"go.mod": GOMOD, "store/s.go": "package store\n\ntype Repo struct{}\n\nfunc (r *Repo) Find() int {\n\treturn 1\n}\n",
          "a.go": "package app\n\nimport \"example.com/app/store\"\n\nfunc Run(r *store.Repo) int {\n\treturn r.Find()\n}\n"},
     [("Run", "calls", "Repo.Find")])
case("go", "test-file", "a test file of the package sees the package's functions",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\nfunc helper() int {\n\treturn 1\n}\n",
          "a_test.go": "package app\n\nimport \"testing\"\n\nfunc TestHelper(t *testing.T) {\n\tif helper() != 1 {\n\t\tt.Fail()\n\t}\n}\n"},
     [("TestHelper", "calls", "helper")])
case("go", "value-receiver", "a method with a value receiver is called on a value",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\ntype Point struct{}\n\nfunc (p Point) Norm() int {\n\treturn 1\n}\n\n"
                                  "func Run() int {\n\tp := Point{}\n\treturn p.Norm()\n}\n"},
     [("Run", "calls", "Point.Norm")])
case("go", "closure", "a function literal sees the functions of the package",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\nfunc helper() int {\n\treturn 1\n}\n\nfunc Run() int {\n\tf := func() int {\n\t\treturn helper()\n\t}\n\treturn f()\n}\n"},
     [("Run", "calls", "helper")])

JAVA2 = ("class Repo {\n    int find() {\n        return 1;\n    }\n}\n\n"
         "class Other {\n    int find() {\n        return 2;\n    }\n}\n\n")
JAVA_BOTH = ([("A.run", "calls", "Repo.find")], [("A.run", "calls", "Other.find")])


def java(body: str) -> dict[str, str]:
    return {"app/A.java": "package app;\n\nimport java.util.List;\n\n" + JAVA2 + "public class A {\n" + body + "}\n"}


case("java", "for-each", "an enhanced `for` variable is declared with its type",
     JAVA, java("    void run(List<Repo> repos) {\n        for (Repo r : repos) {\n            r.find();\n        }\n    }\n"), *JAVA_BOTH)
case("java", "list-element", "`List<T>.get(i)` is a T",
     JAVA, java("    int run(List<Repo> repos) {\n        return repos.get(0).find();\n    }\n"), *JAVA_BOTH)
case("java", "var", "`var r = new C();` has type C",
     JAVA, java("    int run() {\n        var r = new Repo();\n        return r.find();\n    }\n"), *JAVA_BOTH)
case("java", "cast", "`(T) x` is a T",
     JAVA, java("    int run(Object o) {\n        return ((Repo) o).find();\n    }\n"), *JAVA_BOTH)
case("java", "instanceof-pattern", "`o instanceof T r` binds r as a T",
     JAVA, java("    int run(Object o) {\n        if (o instanceof Repo r) {\n            return r.find();\n        }\n        return 0;\n    }\n"), *JAVA_BOTH)
case("java", "try-with-resources", "a resource variable is declared with its type",
     JAVA, {"app/A.java": "package app;\n\nclass Repo implements AutoCloseable {\n    int find() {\n        return 1;\n    }\n\n"
                          "    public void close() {\n    }\n}\n\nclass Other {\n    int find() {\n        return 2;\n    }\n}\n\n"
                          "public class A {\n    int run() {\n        try (Repo r = open()) {\n            return r.find();\n        }\n    }\n\n"
                          "    Repo open() {\n        return new Repo();\n    }\n}\n"},
     *JAVA_BOTH)
case("java", "this-field", "`this.f.m()` is a member of the field's type",
     JAVA, java("    private Repo repo;\n\n    int run() {\n        return this.repo.find();\n    }\n"), *JAVA_BOTH)
case("java", "static-field", "a static field's declared type gives its members",
     JAVA, java("    static final Repo DEFAULT = new Repo();\n\n    int run() {\n        return DEFAULT.find();\n    }\n"), *JAVA_BOTH)
case("java", "lambda-body", "a call in a lambda's body is the enclosing method's",
     JAVA, java("    int helper(int x) {\n        return x;\n    }\n\n    void run(List<Integer> xs) {\n        xs.forEach(x -> helper(x));\n    }\n"),
     [("A.run", "calls", "A.helper")])
case("java", "method-reference", "`this::m` and `C::m` are uses of m",
     JAVA, java("    int helper(int x) {\n        return x;\n    }\n\n    void run(List<Integer> xs) {\n        xs.forEach(this::helper);\n    }\n"),
     [("A.run", "indirect_call", "A.helper")])
case("java", "overload-by-type", "overloads of one arity are told apart by the argument's type",
     JAVA, {"app/A.java": "package app;\n\npublic class A {\n    int send(String s) {\n        return 1;\n    }\n\n"
                          "    int send(int n) {\n        return 2;\n    }\n\n    int run() {\n        return send(\"x\");\n    }\n}\n"},
     [("A.run", "calls", "A.send")])
case("java", "explicit-constructor-call", "`super(…)` / `this(…)` in a constructor call the constructor of the base, or another of the class",
     JAVA, {"app/A.java": "package app;\n\nclass Token {\n    Token(int tag, String text) {\n    }\n}\n\n"
                          "class Name extends Token {\n    Name(String text) {\n        super(1, text);\n    }\n\n"
                          "    Name() {\n        this(\"x\");\n    }\n}\n"},
     [("Name.Name", "calls", "Token.Token"), ("Name.Name", "calls", "Name.Name")])
case("java", "module-info", "an import of `p.C` names the class of package p, whatever module-info.java declares a module `p`",
     JAVA, {"src/main/java/module-info.java": "module com.acme {\n  exports com.acme;\n}\n",
            "src/main/java/com/acme/Gson.java": "package com.acme;\n\npublic class Gson {\n    public int go() {\n        return 1;\n    }\n}\n",
            "src/test/java/com/acme/fn/T.java": "package com.acme.fn;\n\nimport com.acme.Gson;\n\npublic class T {\n    int run(Gson g) {\n        return g.go();\n    }\n}\n"},
     [("T.java", "imports", "Gson"), ("T.run", "calls", "Gson.go")])
case("java", "own-optional", "a generic class the project names `Optional` is the project's class where it is in scope",
     JAVA, {"app/A.java": "package app;\n\nclass Item {\n    int size() {\n        return 1;\n    }\n}\n\n"
                          "class Other {\n    int size() {\n        return 2;\n    }\n}\n\n"
                          "class Optional<T> {\n    T held;\n\n    T lead() {\n        return held;\n    }\n}\n\n"
                          "public class A {\n    int run(Optional<Item> b) {\n        return b.lead().size();\n    }\n}\n"},
     [("A.run", "calls", "Optional.lead"), ("A.run", "calls", "Item.size")], [("A.run", "calls", "Other.size")])
case("java", "library-optional-beside-own", "`java.util.Optional` imported by a file stays the library's beside a project class of that name elsewhere",
     JAVA, {"lib/Optional.java": "package lib;\n\npublic class Optional<T> {\n    public T lead() {\n        return null;\n    }\n}\n",
            "app/A.java": "package app;\n\nimport java.util.Optional;\n\nclass Item {\n    int size() {\n        return 1;\n    }\n}\n\n"
                          "public class A {\n    int run(Optional<Item> b) {\n        return b.get().size();\n    }\n}\n"},
     [("A.run", "calls", "Item.size")], [("A.run", "references", "lib/Optional.java:lib.Optional")])
case("java", "method-named-self", "`self` is a name like any other where the language has no such word",
     JAVA, {"app/A.java": "package app;\n\npublic class A {\n    int self() {\n        return 1;\n    }\n\n    int run() {\n        return self();\n    }\n}\n"},
     [("A.run", "calls", "A.self")])
case("java", "nested-static-class", "a nested class is named through the class around it",
     JAVA, {"app/A.java": "package app;\n\nclass Outer {\n    static class Inner {\n        int find() {\n            return 1;\n        }\n    }\n}\n\n"
                          "public class A {\n    int run(Outer.Inner i) {\n        return i.find();\n    }\n}\n"},
     [("A.run", "calls", "Outer.Inner.find")])
case("java", "enum-method", "an enum constant has the enum's methods",
     JAVA, {"app/A.java": "package app;\n\nenum Color {\n    RED;\n\n    String label() {\n        return \"r\";\n    }\n}\n\n"
                          "public class A {\n    String run() {\n        return Color.RED.label();\n    }\n}\n"},
     [("A.run", "calls", "Color.label")])
case("java", "interface-typed", "a call through an interface type is the interface's method",
     JAVA, {"app/A.java": "package app;\n\ninterface Store {\n    int find();\n}\n\npublic class A {\n    int run(Store s) {\n        return s.find();\n    }\n}\n"},
     [("A.run", "calls", "Store.find")])
case("java", "fully-qualified", "`p.C.m()` names C by its package",
     JAVA, {"lib/Util.java": "package lib;\n\npublic class Util {\n    public static int twice(int x) {\n        return 2 * x;\n    }\n}\n",
            "app/A.java": "package app;\n\npublic class A {\n    int run() {\n        return lib.Util.twice(2);\n    }\n}\n"},
     [("A.run", "calls", "Util.twice")])
case("java", "interface-static", "`I.m()` is a static method of the interface",
     JAVA, {"app/A.java": "package app;\n\ninterface Named {\n    static Named of() {\n        return null;\n    }\n}\n\n"
                          "public class A {\n    Named run() {\n        return Named.of();\n    }\n}\n"},
     [("A.run", "calls", "Named.of")])
case("java", "record", "`new R(…)` constructs a record",
     JAVA, {"app/A.java": "package app;\n\nrecord Point(int x, int y) {\n    int norm() {\n        return x + y;\n    }\n}\n\n"
                          "public class A {\n    int run() {\n        Point p = new Point(1, 2);\n        return p.norm();\n    }\n}\n"},
     [("A.run", "calls", "Point"), ("A.run", "calls", "Point.norm")])
case("java", "anonymous-class", "a method of an anonymous class sees the methods of the class around it",
     JAVA, {"app/A.java": "package app;\n\npublic class A {\n    int helper() {\n        return 1;\n    }\n\n"
                          "    Runnable run() {\n        return new Runnable() {\n            public void run() {\n                helper();\n            }\n        };\n    }\n}\n"},
     [("run", "calls", "A.helper")])

KT2 = "class Repo {\n    fun find(): Int = 1\n}\n\nclass Other {\n    fun find(): Int = 2\n}\n\n"
KT_BOTH = ([("run", "calls", "Repo.find")], [("run", "calls", "Other.find")])


def kt(body: str) -> dict[str, str]:
    return {"src/app/A.kt": "package app\n\n" + KT2 + body}


case("kotlin", "safe-call", "`r?.m()` is r's m",
     KT, kt("fun run(r: Repo?): Int? = r?.find()\n"), *KT_BOTH)
case("kotlin", "with", "inside `with(x) { … }` an unqualified name is x's member",
     KT, kt("fun run(r: Repo): Int = with(r) {\n    find()\n}\n"), *KT_BOTH)
case("kotlin", "let", "`x.let { it.m() }`: `it` is x",
     KT, kt("fun run(r: Repo): Int = r.let { it.find() }\n"), *KT_BOTH)
case("kotlin", "top-level-property", "a top-level `val` assigned a construction has that type",
     KT, kt("val store = Repo()\n\nfun run(): Int = store.find()\n"), *KT_BOTH)
case("kotlin", "lateinit", "a `lateinit var`'s declared type gives its members",
     KT, kt("class Svc {\n    lateinit var repo: Repo\n\n    fun run(): Int = repo.find()\n}\n"),
     [("Svc.run", "calls", "Repo.find")], [("Svc.run", "calls", "Other.find")])
case("kotlin", "for-loop", "a `for` variable over `List<T>` is a T",
     KT, kt("fun run(rs: List<Repo>) {\n    for (r in rs) {\n        r.find()\n    }\n}\n"), *KT_BOTH)
case("kotlin", "cast", "`x as T` is a T",
     KT, kt("fun run(x: Any): Int = (x as Repo).find()\n"), *KT_BOTH)
case("kotlin", "return-type", "a function's declared result gives the members of what it returns",
     KT, kt("fun make(): Repo = Repo()\n\nfun run(): Int = make().find()\n"), *KT_BOTH)
case("kotlin", "typealias", "a typealias names the type it is written for",
     KT, kt("typealias Store = Repo\n\nfun run(s: Store): Int = s.find()\n"), *KT_BOTH)
case("kotlin", "apply-also", "`x.apply { … }` and `x.also { … }` give x back",
     KT, {"src/app/A.kt": "package app\n\nclass Builder {\n    fun build(): Int = 1\n}\n\nclass Other {\n    fun build(): Int = 2\n}\n\n"
                          "fun make(b: Builder, action: Builder.() -> Unit): Int = b.apply(action).build()\n\n"
                          "fun log(b: Builder): Int = b.also { println(it) }.build()\n"},
     [("make", "calls", "Builder.build"), ("log", "calls", "Builder.build")],
     [("make", "calls", "Other.build"), ("log", "calls", "Other.build")])
case("kotlin", "constructor", "`C(…)` calls C's constructor",
     KT, {"src/app/A.kt": "package app\n\nclass Repo(val size: Int)\n\nfun run(): Repo = Repo(1)\n"},
     [("run", "calls", "Repo")])
case("kotlin", "super", "`super.m()` is the supertype's m",
     KT, {"src/app/A.kt": "package app\n\nopen class Base {\n    open fun m(): Int = 1\n}\n\nclass A : Base() {\n    override fun m(): Int = super.m()\n}\n"},
     [("A.m", "calls", "Base.m")])
case("kotlin", "interface-default", "a method an interface defines is inherited",
     KT, {"src/app/A.kt": "package app\n\ninterface Named {\n    fun label(): String = \"x\"\n}\n\nclass A : Named {\n    fun run(): String = label()\n}\n"},
     [("A.run", "calls", "Named.label")])
case("kotlin", "interface-typed", "a call through an interface type is the interface's member",
     KT, {"src/app/A.kt": "package app\n\ninterface Store {\n    fun find(): Int\n}\n\nfun run(s: Store): Int = s.find()\n"},
     [("run", "calls", "Store.find")])
case("kotlin", "function-reference", "`::f` is a use of f",
     KT, {"src/app/A.kt": "package app\n\nfun handle(x: Int): Int = x\n\nfun run(xs: List<Int>): List<Int> = xs.map(::handle)\n"},
     [("run", "indirect_call", "handle")])
case("kotlin", "nested-class", "a nested class is named through the class around it",
     KT, {"src/app/A.kt": "package app\n\nclass Outer {\n    class Inner {\n        fun find(): Int = 1\n    }\n}\n\nfun run(): Int = Outer.Inner().find()\n"},
     [("run", "calls", "Outer.Inner.find")])
case("kotlin", "enum-entry", "an enum entry has the enum's members",
     KT, {"src/app/A.kt": "package app\n\nenum class Color {\n    RED;\n\n    fun label(): String = \"r\"\n}\n\nfun run(): String = Color.RED.label()\n"},
     [("run", "calls", "Color.label")])

SC2 = "class Repo {\n  def find(): Int = 1\n}\n\nclass Other {\n  def find(): Int = 2\n}\n\n"
SC_BOTH = ([("Main.run", "calls", "Repo.find")], [("Main.run", "calls", "Other.find")])


def sc(body: str) -> dict[str, str]:
    return {"src/app/A.scala": "package app\n\n" + SC2 + "object Main {\n" + body + "}\n"}


case("scala", "new-with-parentheses", "a `val` assigned `new C()` has that type",
     SC, sc("  def run(): Int = {\n    val r = new Repo()\n    r.find()\n  }\n"), *SC_BOTH)
case("scala", "typed-val", "a `val` with a declared type has that type",
     SC, sc("  val repo: Repo = new Repo()\n\n  def run(): Int = repo.find()\n"), *SC_BOTH)
case("scala", "return-type", "a method's declared result gives the members of what it returns",
     SC, sc("  def make(): Repo = new Repo()\n\n  def run(): Int = make().find()\n"), *SC_BOTH)
case("scala", "pattern-typed", "`case r: T` binds r as a T",
     SC, sc("  def run(x: Any): Int = x match {\n    case r: Repo => r.find()\n    case _ => 0\n  }\n"), *SC_BOTH)
case("scala", "no-parentheses", "a method called without parentheses is still called",
     SC, {"src/app/A.scala": "package app\n\nclass Repo {\n  def size: Int = 1\n}\n\nobject Main {\n  def run(r: Repo): Int = r.size\n}\n"},
     [("Main.run", "calls", "Repo.size")])
case("scala", "trait-typed", "a call through a trait type is the trait's member",
     SC, {"src/app/A.scala": "package app\n\ntrait Store {\n  def find(): Int\n}\n\nobject Main {\n  def run(s: Store): Int = s.find()\n}\n"},
     [("Main.run", "calls", "Store.find")])
case("scala", "member-import", "`import p.Obj.f` binds f",
     SC, {"src/lib/Util.scala": "package lib\n\nobject Util {\n  def twice(x: Int): Int = 2 * x\n}\n",
          "src/app/A.scala": "package app\n\nimport lib.Util.twice\n\nobject Main {\n  def run(): Int = twice(2)\n}\n"},
     [("Main.run", "calls", "Util.twice")])
case("scala", "super", "`super.m()` is the parent's m",
     SC, {"src/app/A.scala": "package app\n\nclass Base {\n  def m(): Int = 1\n}\n\nclass A extends Base {\n  override def m(): Int = super.m()\n}\n"},
     [("A.m", "calls", "Base.m")])
case("scala", "companion-apply", "`C(…)` is the companion object's `apply`",
     SC, {"src/app/A.scala": "package app\n\nclass Repo(val size: Int)\n\nobject Repo {\n  def apply(size: Int): Repo = new Repo(size)\n}\n\n"
                             "object Main {\n  def run(): Repo = Repo(1)\n}\n"},
     [("Main.run", "calls", "Repo")])
case("scala", "generic-argument", "`Page[Item]`'s `head: T` is an Item",
     SC, {"src/app/A.scala": "package app\n\nclass Item {\n  def size(): Int = 1\n}\n\nclass Other {\n  def size(): Int = 2\n}\n\n"
                             "class Page[T](val head: T)\n\nobject Main {\n  def run(p: Page[Item]): Int = p.head.size()\n}\n"},
     [("Main.run", "calls", "Item.size")], [("Main.run", "calls", "Other.size")])
case("scala", "extends", "`class A extends B` names its parent",
     SC, {"src/app/A.scala": "package app\n\nclass Base\n\nclass A extends Base\n"},
     [("A", "inherits", "Base")])
case("scala", "same-package", "a class of the same package in another file needs no import",
     SC, {"src/app/Repo.scala": "package app\n\nclass Repo {\n  def find(): Int = 1\n}\n",
          "src/app/A.scala": "package app\n\nobject Main {\n  def run(r: Repo): Int = r.find()\n}\n"},
     [("Main.run", "calls", "Repo.find")])

CS2 = ("    public class Repo\n    {\n        public int Find() { return 1; }\n    }\n\n"
       "    public class Other\n    {\n        public int Find() { return 2; }\n    }\n\n")
CS_BOTH = ([("A.Run", "calls", "Repo.Find")], [("A.Run", "calls", "Other.Find")])


def cs(body: str, head: str = "") -> dict[str, str]:
    return {"A.cs": "using System.Collections.Generic;\nusing System.Threading.Tasks;\n" + head + "\nnamespace App\n{\n" + CS2
            + "    public class A\n    {\n" + body + "    }\n}\n"}


case("csharp", "foreach-typed", "a `foreach` variable declared with a type has it",
     CS, cs("        public void Run(List<Repo> repos)\n        {\n            foreach (Repo r in repos)\n            {\n                r.Find();\n            }\n        }\n"), *CS_BOTH)
case("csharp", "foreach-var", "a `foreach (var r in xs)` variable over `List<T>` is a T",
     CS, cs("        public void Run(List<Repo> repos)\n        {\n            foreach (var r in repos)\n            {\n                r.Find();\n            }\n        }\n"), *CS_BOTH)
case("csharp", "is-pattern", "`o is T r` binds r as a T",
     CS, cs("        public int Run(object o)\n        {\n            if (o is Repo r)\n            {\n                return r.Find();\n            }\n            return 0;\n        }\n"), *CS_BOTH)
case("csharp", "cast", "`(T)x` is a T",
     CS, cs("        public int Run(object o)\n        {\n            return ((Repo)o).Find();\n        }\n"), *CS_BOTH)
case("csharp", "as-cast", "`x as T` is a T",
     CS, cs("        public int Run(object o)\n        {\n            return (o as Repo).Find();\n        }\n"), *CS_BOTH)
case("csharp", "null-conditional", "`r?.M()` is r's M",
     CS, cs("        public int? Run(Repo r)\n        {\n            return r?.Find();\n        }\n"), *CS_BOTH)
case("csharp", "target-typed-new", "`T r = new();` constructs a T",
     CS, cs("        public int Run()\n        {\n            Repo r = new();\n            return r.Find();\n        }\n"), *CS_BOTH)
case("csharp", "await-task", "`await` of a `Task<T>` is a T",
     CS, cs("        public Task<Repo> LoadAsync()\n        {\n            return Task.FromResult(new Repo());\n        }\n\n"
            "        public async Task<int> Run()\n        {\n            var r = await LoadAsync();\n            return r.Find();\n        }\n"), *CS_BOTH)
case("csharp", "static-property", "a static property's declared type gives its members",
     CS, cs("        public static Repo Default { get; } = new Repo();\n\n        public int Run()\n        {\n            return A.Default.Find();\n        }\n"), *CS_BOTH)
case("csharp", "lambda-body", "a call in a lambda's body is the enclosing method's",
     CS, cs("        int Map(int x) { return x; }\n\n        public void Run(List<int> xs)\n        {\n            xs.ForEach(x => Map(x));\n        }\n"),
     [("A.Run", "calls", "A.Map")])
case("csharp", "method-group", "a method passed by name is a use of it",
     CS, cs("        void Handle(int x) { }\n\n        public void Run(List<int> xs)\n        {\n            xs.ForEach(Handle);\n        }\n"),
     [("A.Run", "indirect_call", "A.Handle")])
case("csharp", "local-function", "a local function is called by its name in the method",
     CS, cs("        public int Run()\n        {\n            int Local() { return 1; }\n            return Local();\n        }\n"),
     [("A.Run", "calls", "Local")])
case("csharp", "this", "`this.M()` is the class's M",
     CS, cs("        int Helper() { return 1; }\n\n        public int Run()\n        {\n            return this.Helper();\n        }\n"),
     [("A.Run", "calls", "A.Helper")])
case("csharp", "inherited", "an unqualified name in a class is also searched in its base",
     CS, {"A.cs": "namespace App\n{\n    public class Base\n    {\n        protected int Helper() { return 1; }\n    }\n\n"
                  "    public class A : Base\n    {\n        public int Run()\n        {\n            return Helper();\n        }\n    }\n}\n"},
     [("A.Run", "calls", "Base.Helper")])
case("csharp", "constructor-initializer", "`: base(…)` / `: this(…)` call the constructor of the base, or another of the class",
     CS, {"A.cs": "namespace App\n{\n    public class Token\n    {\n        public Token(int tag, string text) { }\n    }\n\n"
                  "    public class Name : Token\n    {\n        public Name(string text) : base(1, text) { }\n"
                  "        public Name() : this(\"x\") { }\n    }\n}\n"},
     [("Name.Name", "calls", "Token.Token"), ("Name.Name", "calls", "Name.Name")])
case("csharp", "using-block-namespace", "`using N;` brings the extensions of a namespace written as a block in another directory into sight",
     CS, {"Infra/Ext.cs": "using System.Collections.Generic;\n\nnamespace App.Infra\n{\n    static class EnumerableExtensions\n    {\n"
                          "        public static bool Empty<T>(this IEnumerable<T> source) { return true; }\n    }\n}\n",
          "Core/A.cs": "using System.Collections.Generic;\nusing App.Infra;\n\nnamespace App.Core\n{\n    public class A\n    {\n"
                       "        public bool Run(IEnumerable<string> values)\n        {\n            return values.Empty();\n        }\n    }\n}\n"},
     [("A.Run", "calls", "EnumerableExtensions.Empty")])
case("csharp", "new-names-a-type", "`new Name(…)` names the type, whatever method of the class around is called `Name`",
     CS, {"A.cs": "namespace App\n{\n    public class Name : Token\n    {\n        public Name(string text) { }\n    }\n\n"
                  "    public class Token\n    {\n        public static Token Name(string text)\n        {\n            return new Name(text);\n        }\n    }\n}\n"},
     [("Token.Name", "calls", "A.cs:App.Name")])
case("csharp", "generic-namesake", "`Maybe.Nothing<T>()` is a member of the static class `Maybe` beside the generic `Maybe<T>`",
     CS, {"A.cs": "namespace App\n{\n    public abstract class Maybe<T>\n    {\n    }\n\n"
                  "    public static class Maybe\n    {\n        public static Maybe<T> Nothing<T>() { return null; }\n    }\n\n"
                  "    public class A\n    {\n        public object Run()\n        {\n            return Maybe.Nothing<object>();\n        }\n    }\n}\n"},
     [("A.Run", "calls", "Maybe.Nothing")])
case("csharp", "primary-constructor", "a class's primary constructor parameter is a typed name of the class",
     CS, {"A.cs": "namespace App\n{\n" + CS2 + "    public class A(Repo repo)\n    {\n        public int Run()\n        {\n            return repo.Find();\n        }\n    }\n}\n"},
     *CS_BOTH)
case("csharp", "global-using", "`global using N;` in one file holds for every file",
     CS, {"Usings.cs": "global using Lib;\n",
          "Lib/Repo.cs": "namespace Lib\n{\n    public class Repo\n    {\n        public int Find() { return 1; }\n    }\n}\n",
          "App/A.cs": "namespace App\n{\n    public class A\n    {\n        public int Run(Repo r)\n        {\n            return r.Find();\n        }\n    }\n}\n"},
     [("A.Run", "calls", "Repo.Find")])
case("csharp", "extension-on-interface", "an extension written for an interface is a member of the classes that implement it",
     CS, {"A.cs": "namespace App\n{\n    public interface IStore\n    {\n    }\n\n    public class Repo : IStore\n    {\n    }\n\n"
                  "    public static class StoreExt\n    {\n        public static int Size(this IStore s) { return 1; }\n    }\n\n"
                  "    public class A\n    {\n        public int Run(Repo r)\n        {\n            return r.Size();\n        }\n    }\n}\n"},
     [("A.Run", "calls", "StoreExt.Size")])
case("csharp", "enum-member", "an enum member is a member of its enum",
     CS, {"A.cs": "namespace App\n{\n    public enum Color\n    {\n        Red,\n        Green,\n    }\n\n    public class A\n    {\n"
                  "        public Color Run()\n        {\n            return Color.Red;\n        }\n    }\n}\n"},
     [("A.Run", "references", "Color")])
case("csharp", "record", "`new R(…)` constructs a record, which has methods like a class",
     CS, {"A.cs": "namespace App\n{\n    public record Point(int X, int Y)\n    {\n        public int Norm() { return X + Y; }\n    }\n\n"
                  "    public class A\n    {\n        public int Run()\n        {\n            var p = new Point(1, 2);\n            return p.Norm();\n        }\n    }\n}\n"},
     [("A.Run", "calls", "Point"), ("A.Run", "calls", "Point.Norm")])

RS2 = ("pub struct Repo;\n\nimpl Repo {\n    pub fn new() -> Repo {\n        Repo\n    }\n    pub fn find(&self) -> i32 {\n        1\n    }\n}\n\n"
       "pub struct Other;\n\nimpl Other {\n    pub fn find(&self) -> i32 {\n        2\n    }\n}\n\n")
RS_BOTH = ([("run", "calls", "Repo.find")], [("run", "calls", "Other.find")])


def rs(body: str, head: str = "") -> dict[str, str]:
    return {"Cargo.toml": CARGO, "src/lib.rs": head + RS2 + body}


case("rust", "question-mark", "`f()?` of a `Result<T, E>` is a T",
     RS, rs("pub fn open() -> Result<Repo, String> {\n    Ok(Repo::new())\n}\n\npub fn run() -> Result<i32, String> {\n    let r = open()?;\n    Ok(r.find())\n}\n"), *RS_BOTH)
case("rust", "option-unwrap", "`opt.unwrap()` of an `Option<T>` is a T",
     RS, rs("pub fn run(opt: Option<Repo>) -> i32 {\n    opt.unwrap().find()\n}\n"), *RS_BOTH)
case("rust", "if-let-some", "`if let Some(r) = opt` binds r as the option's T",
     RS, rs("pub fn run(opt: Option<Repo>) -> i32 {\n    if let Some(r) = opt {\n        return r.find();\n    }\n    0\n}\n"), *RS_BOTH)
case("rust", "smart-pointer", "a method call goes through `Arc<T>` / `Box<T>` to T",
     RS, rs("pub fn run(r: Arc<Repo>) -> i32 {\n    r.find()\n}\n", "use std::sync::Arc;\n\n"), *RS_BOTH)
case("rust", "vec-iteration", "a `for` variable over `&Vec<T>` is a T",
     RS, rs("pub fn run(repos: &Vec<Repo>) {\n    for r in repos {\n        r.find();\n    }\n}\n"), *RS_BOTH)
case("rust", "tuple-struct-field", "`w.0` of `struct W(T)` is a T",
     RS, rs("pub struct Wrap(Repo);\n\npub fn run(w: &Wrap) -> i32 {\n    w.0.find()\n}\n"), *RS_BOTH)
case("rust", "static-item", "a `static`'s declared type gives its methods",
     RS, rs("static STORE: Repo = Repo;\n\npub fn run() -> i32 {\n    STORE.find()\n}\n"), *RS_BOTH)
case("rust", "reference-parameter", "a `&T` / `&mut T` parameter is a T",
     RS, rs("pub fn run(r: &mut Repo) -> i32 {\n    r.find()\n}\n"), *RS_BOTH)
case("rust", "dyn-trait", "a call through `&dyn Trait` is the trait's method",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub trait Named {\n    fn label(&self) -> i32;\n}\n\npub fn run(n: &dyn Named) -> i32 {\n    n.label()\n}\n"},
     [("run", "calls", "Named.label")])
case("rust", "generic-bound", "a call on a `T: Trait` value is the trait's method",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub trait Named {\n    fn label(&self) -> i32;\n}\n\npub fn run<T: Named>(n: &T) -> i32 {\n    n.label()\n}\n"},
     [("run", "calls", "Named.label")])
case("rust", "trait-default-method", "a trait's default method is a method of every implementor",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub trait Named {\n    fn label(&self) -> i32 {\n        1\n    }\n}\n\npub struct Repo;\n\n"
                                            "impl Named for Repo {}\n\npub fn run(r: &Repo) -> i32 {\n    r.label()\n}\n"},
     [("run", "calls", "Named.label")])
case("rust", "nested-module-path", "`a::b::f()` walks nested modules",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub mod a {\n    pub mod b {\n        pub fn f() -> i32 {\n            1\n        }\n    }\n}\n\n"
                                            "pub fn run() -> i32 {\n    a::b::f()\n}\n"},
     [("run", "calls", "a.b.f")])
case("rust", "self-path", "`self::f()` is f of the current module",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "fn f() -> i32 {\n    1\n}\n\npub fn run() -> i32 {\n    self::f()\n}\n"},
     [("run", "calls", "f")])
case("rust", "use-super-glob", "`use super::*;` in a test module binds the parent's names",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub fn f() -> i32 {\n    1\n}\n\n#[cfg(test)]\nmod tests {\n    use super::*;\n\n"
                                            "    #[test]\n    fn works() {\n        assert_eq!(f(), 1);\n    }\n}\n"},
     [("tests.works", "calls", "f")])
case("rust", "mod-rs", "`mod x;` can be the file `x/mod.rs`",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub mod x;\n\npub fn run() -> i32 {\n    x::f()\n}\n",
          "src/x/mod.rs": "pub fn f() -> i32 {\n    1\n}\n"},
     [("run", "calls", "f")])
case("rust", "use-self-in-braces", "`use a::{self, f};` binds the module and the name",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub mod m;\npub mod a;\n", "src/m.rs": "pub fn f() -> i32 {\n    1\n}\n\npub fn g() -> i32 {\n    2\n}\n",
          "src/a.rs": "use crate::m::{self, f};\n\npub fn run() -> i32 {\n    f() + m::g()\n}\n"},
     [("run", "calls", "f"), ("run", "calls", "g")])
case("rust", "function-as-value", "a function passed by name is a use of it",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "fn handle(x: i32) -> i32 {\n    x\n}\n\npub fn run(xs: Vec<i32>) -> Vec<i32> {\n    xs.into_iter().map(handle).collect()\n}\n"},
     [("run", "indirect_call", "handle")])
case("rust", "closure-body", "a call in a closure's body is the enclosing function's",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "fn helper() -> i32 {\n    1\n}\n\npub fn run() -> i32 {\n    let f = || helper();\n    f()\n}\n"},
     [("run", "calls", "helper")])
case("rust", "builder-chain", "a method returning `Self` keeps the type through a chain",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub struct Builder;\n\nimpl Builder {\n    pub fn new() -> Self {\n        Builder\n    }\n"
                                            "    pub fn name(self, _n: &str) -> Self {\n        self\n    }\n    pub fn build(self) -> i32 {\n        1\n    }\n}\n\n"
                                            "pub fn run() -> i32 {\n    Builder::new().name(\"x\").build()\n}\n"},
     [("run", "calls", "Builder.name"), ("run", "calls", "Builder.build")])
case("rust", "enum-method", "a method of an enum's impl is called on its values",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub enum Shape {\n    Circle,\n}\n\nimpl Shape {\n    pub fn area(&self) -> i32 {\n        1\n    }\n}\n\n"
                                            "pub fn run(s: &Shape) -> i32 {\n    s.area()\n}\n"},
     [("run", "calls", "Shape.area")])
case("rust", "struct-literal", "`T { … }` constructs a T",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub struct Point {\n    pub x: i32,\n}\n\npub fn run() -> Point {\n    Point { x: 1 }\n}\n"},
     [("run", "references", "Point")])

SW2 = "class Repo {\n  func find() -> Int {\n    return 1\n  }\n}\n\nclass Other {\n  func find() -> Int {\n    return 2\n  }\n}\n\n"
SW_BOTH = ([("run", "calls", "Repo.find")], [("run", "calls", "Other.find")])
case("swift", "optional-chaining", "`r?.m()` is r's m",
     SW, {"a.swift": SW2 + "func run(r: Repo?) -> Int? {\n  return r?.find()\n}\n"}, *SW_BOTH)
case("swift", "if-let", "`if let r = opt` binds r as the optional's type",
     SW, {"a.swift": SW2 + "func run(opt: Repo?) -> Int {\n  if let r = opt {\n    return r.find()\n  }\n  return 0\n}\n"}, *SW_BOTH)
case("swift", "guard-let", "`guard let r = opt` binds r for the rest of the scope",
     SW, {"a.swift": SW2 + "func run(opt: Repo?) -> Int {\n  guard let r = opt else {\n    return 0\n  }\n  return r.find()\n}\n"}, *SW_BOTH)
case("swift", "for-in", "a `for` variable over `[T]` is a T",
     SW, {"a.swift": SW2 + "func run(repos: [Repo]) {\n  for r in repos {\n    _ = r.find()\n  }\n}\n"}, *SW_BOTH)
case("swift", "subscript", "an element of `[T]` is a T",
     SW, {"a.swift": SW2 + "func run(repos: [Repo]) -> Int {\n  return repos[0].find()\n}\n"}, *SW_BOTH)
case("swift", "as-cast", "`x as! T` is a T",
     SW, {"a.swift": SW2 + "func run(x: Any) -> Int {\n  return (x as! Repo).find()\n}\n"}, *SW_BOTH)
case("swift", "return-type", "a function's declared result gives the members of what it returns",
     SW, {"a.swift": SW2 + "func make() -> Repo {\n  return Repo()\n}\n\nfunc run() -> Int {\n  return make().find()\n}\n"}, *SW_BOTH)
case("swift", "computed-property", "a computed property's declared type gives its members",
     SW, {"a.swift": SW2 + "class Svc {\n  var repo: Repo {\n    return Repo()\n  }\n\n  func run() -> Int {\n    return repo.find()\n  }\n}\n"},
     [("Svc.run", "calls", "Repo.find")], [("Svc.run", "calls", "Other.find")])
case("swift", "static-property", "`T.shared.m()` goes through a static property's type",
     SW, {"a.swift": "class Repo {\n  static let shared = Repo()\n\n  func find() -> Int {\n    return 1\n  }\n}\n\nfunc run() -> Int {\n  return Repo.shared.find()\n}\n"},
     [("run", "calls", "Repo.find")])
case("swift", "enum-payload", "the types an enum case carries are types the enum names",
     SW, {"a.swift": "struct InputKey {\n}\n\nenum ValidationResult {\n  case missing([InputKey])\n  case plain\n}\n"},
     [("missing", "references", "InputKey")])
case("swift", "enum-case-built", "building a case with what it carries (`E.c(x)`) is a use of the enum",
     SW, {"a.swift": "enum ParserError: Error {\n  case noValue(String)\n}\n\nfunc run() throws {\n  throw ParserError.noValue(\"x\")\n}\n"},
     [("run", "references", "ParserError")])
case("swift", "metatype", "`T.Type` names T",
     SW, {"a.swift": "protocol Command {\n}\n\nfunc run(root: Command.Type) {\n}\n"},
     [("run", "references", "Command")])
case("swift", "typealias", "a typealias names the type it is written for",
     SW, {"a.swift": SW2 + "typealias Store = Repo\n\nfunc run(s: Store) -> Int {\n  return s.find()\n}\n"}, *SW_BOTH)
case("swift", "generic-argument", "`Page<Item>`'s `head: T` is an Item",
     SW, {"a.swift": "class Item {\n  func size() -> Int {\n    return 1\n  }\n}\n\nclass Other {\n  func size() -> Int {\n    return 2\n  }\n}\n\n"
                     "struct Page<T> {\n  let head: T\n}\n\nfunc run(p: Page<Item>) -> Int {\n  return p.head.size()\n}\n"},
     [("run", "calls", "Item.size")], [("run", "calls", "Other.size")])
case("swift", "conformance-in-extension", "a method an extension adds with a conformance is a member of the type",
     SW, {"a.swift": "protocol Named {\n  func label() -> String\n}\n\nstruct Repo {\n}\n\nextension Repo: Named {\n  func label() -> String {\n    return \"x\"\n  }\n}\n\n"
                     "func run(r: Repo) -> String {\n  return r.label()\n}\n"},
     [("run", "calls", "label")])
case("swift", "enum-method", "an enum case has the enum's methods",
     SW, {"a.swift": "enum Shape {\n  case circle\n\n  func area() -> Int {\n    return 1\n  }\n}\n\nfunc run() -> Int {\n  return Shape.circle.area()\n}\n"},
     [("run", "calls", "Shape.area")])
case("swift", "class-func", "`T.make()` is a `class func` of T",
     SW, {"a.swift": "class Repo {\n  class func make() -> Repo {\n    return Repo()\n  }\n}\n\nfunc run() -> Repo {\n  return Repo.make()\n}\n"},
     [("run", "calls", "Repo.make")])
case("swift", "nested-function", "a function nested in a function is called by its name there",
     SW, {"a.swift": "func run() -> Int {\n  func helper() -> Int {\n    return 1\n  }\n  return helper()\n}\n"},
     [("run", "calls", "run.helper")])
case("swift", "closure-body", "a call in a closure's body is the enclosing function's",
     SW, {"a.swift": "func helper(_ x: Int) -> Int {\n  return x\n}\n\nfunc run(xs: [Int]) -> [Int] {\n  return xs.map { helper($0) }\n}\n"},
     [("run", "calls", "helper")])
case("swift", "function-as-value", "a function passed by name is a use of it",
     SW, {"a.swift": "func handle(_ x: Int) -> Int {\n  return x\n}\n\nfunc run(xs: [Int]) -> [Int] {\n  return xs.map(handle)\n}\n"},
     [("run", "indirect_call", "handle")])

case("c", "forward-declaration", "a call before the definition, after a prototype, is of the definition",
     CC, {"a.c": "static int helper(void);\n\nint run(void) {\n    return helper();\n}\n\nstatic int helper(void) {\n    return 1;\n}\n"},
     [("run", "calls", "helper")])
case("c", "callback-argument", "a function passed by name is a use of it",
     CC, {"a.c": "static int cmp(const void *a, const void *b) {\n    return 0;\n}\n\nvoid sort(void *base, int (*f)(const void *, const void *));\n\n"
                 "void run(void *base) {\n    sort(base, cmp);\n}\n"},
     [("run", "indirect_call", "cmp")])
case("c", "slot-assignment", "`x.f = func;` puts func in the slot f",
     CC, {"a.c": "struct ops {\n    int (*read)(void);\n};\n\nstatic int net_read(void) {\n    return 1;\n}\n\n"
                 "void setup(struct ops *o) {\n    o->read = net_read;\n}\n"},
     [("ops.read", "dispatches_to", "net_read")])
case("c", "global-variable", "a file-level variable is read and written by the functions that name it",
     CC, {"a.c": "static int counter;\n\nvoid bump(void) {\n    counter++;\n}\n\nint peek(void) {\n    return counter;\n}\n"},
     [("bump", "writes", "counter"), ("peek", "reads", "counter")])
case("c", "extern-variable", "`extern T x;` is the variable another file defines",
     CC, {"state.c": "int counter;\n", "a.c": "extern int counter;\n\nint peek(void) {\n    return counter;\n}\n"},
     [("peek", "reads", "state.c:counter")])
case("c", "header-inline", "a `static inline` function of an included header is called",
     CC, {"util.h": "static inline int twice(int x) {\n    return 2 * x;\n}\n",
          "a.c": "#include \"util.h\"\n\nint run(void) {\n    return twice(2);\n}\n"},
     [("run", "calls", "twice")])
case("c", "struct-field-chain", "`a->b->c` reads the field of the struct the field points to",
     CC, {"a.c": "struct inner {\n    int value;\n};\n\nstruct outer {\n    struct inner *in;\n};\n\nint run(struct outer *o) {\n    return o->in->value;\n}\n"},
     [("run", "reads", "inner.value")])
case("c", "typedef-function-pointer", "a call through a parameter is no call of a function of its name",
     CC, {"a.c": "int handler(void) {\n    return 0;\n}\n\ntypedef int (*fn)(void);\n\nint run(fn handler) {\n    return handler();\n}\n"},
     [], [("run", "calls", "a.c:handler")])

CPP2 = ("class Repo {\n public:\n  int Find() {\n    return 1;\n  }\n};\n\nclass Other {\n public:\n  int Find() {\n    return 2;\n  }\n};\n\n")
CPP_BOTH = ([("run", "calls", "Repo.Find")], [("run", "calls", "Other.Find")])
case("cpp", "auto-from-construction", "`auto r = T();` is a T",
     CPP, {"a.cc": CPP2 + "int run() {\n  auto r = Repo();\n  return r.Find();\n}\n"}, *CPP_BOTH)
case("cpp", "auto-from-result", "`auto r = f();` has f's declared result type",
     CPP, {"a.cc": CPP2 + "Repo make() {\n  return Repo();\n}\n\nint run() {\n  auto r = make();\n  return r.Find();\n}\n"}, *CPP_BOTH)
case("cpp", "unique-ptr", "`p->m()` goes through `std::unique_ptr<T>` to T",
     CPP, {"a.cc": "#include <memory>\n\n" + CPP2 + "int run(std::unique_ptr<Repo> p) {\n  return p->Find();\n}\n"}, *CPP_BOTH)
case("cpp", "new-expression", "`new T()` constructs a T",
     CPP, {"a.cc": CPP2 + "int run() {\n  Repo* r = new Repo();\n  return r->Find();\n}\n"},
     [("run", "calls", "Repo"), ("run", "calls", "Repo.Find")], [("run", "calls", "Other.Find")])
case("cpp", "const-reference", "a `const T&` parameter is a T",
     CPP, {"a.cc": "class Repo {\n public:\n  int Find() const {\n    return 1;\n  }\n};\n\nclass Other {\n public:\n  int Find() const {\n    return 2;\n  }\n};\n\n"
                   "int run(const Repo& r) {\n  return r.Find();\n}\n"}, *CPP_BOTH)
case("cpp", "range-for", "a range-`for` variable over `std::vector<T>` is a T",
     CPP, {"a.cc": "#include <vector>\n\n" + CPP2 + "void run(std::vector<Repo>& repos) {\n  for (auto& r : repos) {\n    r.Find();\n  }\n}\n"}, *CPP_BOTH)
case("cpp", "this-member", "`this->f_.m()` is a member of the data member's type",
     CPP, {"a.cc": CPP2 + "class Svc {\n public:\n  int Run() {\n    return this->repo_.Find();\n  }\n\n private:\n  Repo repo_;\n};\n"},
     [("Svc.Run", "calls", "Repo.Find")], [("Svc.Run", "calls", "Other.Find")])
case("cpp", "virtual-base", "a call through a base pointer is the base's member",
     CPP, {"a.cc": "class Shape {\n public:\n  virtual int Area() {\n    return 0;\n  }\n};\n\nint run(Shape* s) {\n  return s->Area();\n}\n"},
     [("run", "calls", "Shape.Area")])
case("cpp", "namespace-alias", "`namespace u = util;` names util",
     CPP, {"a.cc": "namespace util {\nint twice(int x) {\n  return 2 * x;\n}\n}  // namespace util\n\nnamespace u = util;\n\nint run() {\n  return u::twice(2);\n}\n"},
     [("run", "calls", "util.twice")])
case("cpp", "using-declaration", "`using ns::f;` makes f available unqualified",
     CPP, {"a.cc": "namespace util {\nint twice(int x) {\n  return 2 * x;\n}\n}  // namespace util\n\nusing util::twice;\n\nint run() {\n  return twice(2);\n}\n"},
     [("run", "calls", "util.twice")])
case("cpp", "nested-class", "a nested class is a member of the class around it",
     CPP, {"a.cc": "class Outer {\n public:\n  class Inner {\n   public:\n    int Find() {\n      return 1;\n    }\n  };\n};\n\nint run(Outer::Inner& i) {\n  return i.Find();\n}\n"},
     [("run", "calls", "Outer.Inner.Find")])
case("cpp", "anonymous-namespace", "a function of the file's unnamed namespace wins over another file's",
     CPP, {"other.cc": "int helper() {\n  return 0;\n}\n",
           "a.cc": "namespace {\nint helper() {\n  return 1;\n}\n}  // namespace\n\nint run() {\n  return helper();\n}\n"},
     [("run", "calls", "a.cc:helper")], [("run", "calls", "other.cc:helper")])
case("cpp", "argument-dependent-lookup", "an unqualified call finds a function in the namespace of its argument's type",
     CPP, {"a.cc": "namespace shop {\nclass Order {};\n\nint total(const Order& o) {\n  return 1;\n}\n}  // namespace shop\n\n"
                   "int run(const shop::Order& o) {\n  return total(o);\n}\n"},
     [("run", "calls", "shop.total")])
case("cpp", "lambda-body", "a call in a lambda's body is the enclosing function's",
     CPP, {"a.cc": "int helper() {\n  return 1;\n}\n\nint run() {\n  auto f = []() { return helper(); };\n  return f();\n}\n"},
     [("run", "calls", "helper")])
case("cpp", "function-as-value", "a function passed by name is a use of it",
     CPP, {"a.cc": "int handle(int x) {\n  return x;\n}\n\nint apply(int (*f)(int)) {\n  return f(1);\n}\n\nint run() {\n  return apply(handle);\n}\n"},
     [("run", "indirect_call", "handle")])
case("cpp", "out-of-class-static", "a static member defined out of its class is the class's",
     CPP, {"repo.h": "class Repo {\n public:\n  static Repo Make();\n};\n", "repo.cc": "#include \"repo.h\"\n\nRepo Repo::Make() {\n  return Repo();\n}\n",
           "a.cc": "#include \"repo.h\"\n\nRepo run() {\n  return Repo::Make();\n}\n"},
     [("run", "calls", "Repo.Make")])
case("cpp", "template-argument", "`Page<Item>`'s member typed `T` is an Item",
     CPP, {"a.cc": "class Item {\n public:\n  int size() {\n    return 1;\n  }\n};\n\nclass Spare {\n public:\n  int size() {\n    return 2;\n  }\n};\n\n"
                   "template <typename T>\nclass Page {\n public:\n  T lead() {\n    return held;\n  }\n  T held;\n};\n\n"
                   "int run(Page<Item>& p) {\n  return p.lead().size();\n}\n"},
     [("run", "calls", "Page.lead"), ("run", "calls", "Item.size")], [("run", "calls", "Spare.size")])
case("cpp", "export-macro", "a macro between `class` and the name (`class API Env {`) leaves the class a class",
     CPP, {"env.h": "#define API\n\nclass API Env {\n public:\n  virtual int NowMicros() = 0;\n};\n\n"
                    "class API Clock {\n public:\n  virtual int NowMicros() = 0;\n};\n",
           "a.cc": "#include \"env.h\"\n\nint run(Env* env) {\n  return env->NowMicros();\n}\n"},
     [("run", "calls", "Env.NowMicros")], [("run", "calls", "Clock.NowMicros")])
case("cpp", "constructor-arguments", "`T x(1);` constructs a T",
     CPP, {"a.cc": "class Repo {\n public:\n  explicit Repo(int size) {}\n  int Find() {\n    return 1;\n  }\n};\n\nint run() {\n  Repo r(1);\n  return r.Find();\n}\n"},
     [("run", "calls", "Repo"), ("run", "calls", "Repo.Find")])

RB2 = "class Repo\n  def find\n    1\n  end\nend\n\nclass Other\n  def find\n    2\n  end\nend\n\n"
case("ruby", "prepend", "the methods of a prepended module come before the class's",
     RB, {"a.rb": "module Traced\n  def label\n    'x'\n  end\nend\n\nclass A\n  prepend Traced\n\n  def run\n    label\n  end\nend\n"},
     [("A.run", "calls", "Traced.label")])
case("ruby", "module-function", "`def self.f` in a module is called through the module",
     RB, {"a.rb": "module Util\n  def self.twice(x)\n    2 * x\n  end\nend\n\ndef run\n  Util.twice(2)\nend\n"},
     [("run", "calls", "Util.twice")])
case("ruby", "singleton-class", "a method under `class << self` is a class method",
     RB, {"a.rb": "class Repo\n  class << self\n    def make\n      new\n    end\n  end\nend\n\ndef run\n  Repo.make\nend\n"},
     [("run", "calls", "make")])
case("ruby", "attr-reader-typed", "an `attr_reader` gives what its instance variable holds",
     RB, {"a.rb": RB2 + "class Svc\n  attr_reader :repo\n\n  def initialize\n    @repo = Repo.new\n  end\n\n  def run\n    repo.find\n  end\nend\n"},
     [("Svc.run", "calls", "Repo.find")], [("Svc.run", "calls", "Other.find")])
case("ruby", "struct-with-block", "a class written `X = Struct.new(…) do … end` has the methods of its block",
     RB, {"a.rb": "Point = Struct.new(:x) do\n  def norm\n    x\n  end\nend\n\ndef run\n  Point.new(1).norm\nend\n"},
     [("run", "calls", "Point.norm")])
case("ruby", "constant-alias", "a constant assigned a class names that class",
     RB, {"a.rb": RB2 + "Store = Repo\n\ndef run\n  Store.new.find\nend\n"},
     [("run", "calls", "Repo.find")], [("run", "calls", "Other.find")])
case("ruby", "self-class", "`self.class.m` is a class method",
     RB, {"a.rb": "class Repo\n  def self.limit\n    1\n  end\n\n  def run\n    self.class.limit\n  end\nend\n"},
     [("Repo.run", "calls", "Repo.limit")])
case("ruby", "super-with-arguments", "`super(x)` is the method of the same name further up",
     RB, {"a.rb": "class Base\n  def m(x)\n    x\n  end\nend\n\nclass A < Base\n  def m(x)\n    super(x)\n  end\nend\n"},
     [("A.m", "calls", "Base.m")])
case("ruby", "safe-navigation", "`r&.m` is r's m",
     RB, {"a.rb": RB2 + "def run\n  r = Repo.new\n  r&.find\nend\n"},
     [("run", "calls", "Repo.find")], [("run", "calls", "Other.find")])
case("ruby", "predicate-name", "a method named with `?` is called like any other",
     RB, {"a.rb": "class A\n  def valid?\n    true\n  end\n\n  def run\n    valid?\n  end\nend\n"},
     [("A.run", "calls", "A.valid?")])
case("ruby", "namespaced-superclass", "`class A < Shop::Base` inherits from the namespaced class",
     RB, {"base.rb": "module Shop\n  class Base\n    def helper\n      1\n    end\n  end\nend\n",
          "a.rb": "require_relative 'base'\n\nclass A < Shop::Base\n  def run\n    helper\n  end\nend\n"},
     [("A.run", "calls", "Shop.Base.helper")])
case("ruby", "include-across-files", "a module of a required file can be included",
     RB, {"named.rb": "module Named\n  def label\n    'x'\n  end\nend\n",
          "a.rb": "require_relative 'named'\n\nclass A\n  include Named\n\n  def run\n    label\n  end\nend\n"},
     [("A.run", "calls", "Named.label")])
case("ruby", "new-with-arguments", "`C.new(…)` constructs C",
     RB, {"a.rb": "class Repo\n  def initialize(size)\n    @size = size\n  end\nend\n\ndef run\n  Repo.new(1)\nend\n"},
     [("run", "calls", "Repo")])
case("ruby", "top-level-method", "a top-level method of a required file is in scope",
     RB, {"util.rb": "def twice(x)\n  2 * x\nend\n", "a.rb": "require_relative 'util'\n\nclass A\n  def run\n    twice(2)\n  end\nend\n"},
     [("A.run", "calls", "twice")])
case("ruby", "block-body", "a call in a block is the enclosing method's",
     RB, {"a.rb": "class A\n  def helper(x)\n    x\n  end\n\n  def run(xs)\n    xs.map { |x| helper(x) }\n  end\nend\n"},
     [("A.run", "calls", "A.helper")])
case("ruby", "private-method", "a private method is called with an implicit receiver",
     RB, {"a.rb": "class A\n  def run\n    helper\n  end\n\n  private\n\n  def helper\n    1\n  end\nend\n"},
     [("A.run", "calls", "A.helper")])

PHP2 = ("class Repo\n{\n    public function find(): int\n    {\n        return 1;\n    }\n}\n\nclass Other\n{\n"
        "    public function find(): int\n    {\n        return 2;\n    }\n}\n\n")
PHP_BOTH = ([("run", "calls", "Repo.find")], [("run", "calls", "Other.find")])
case("php", "constructor-promotion", "a promoted constructor parameter is a typed property",
     PHP, {"a.php": "<?php\n" + PHP2 + "class Svc\n{\n    public function __construct(private Repo $repo)\n    {\n    }\n\n"
                    "    public function run(): int\n    {\n        return $this->repo->find();\n    }\n}\n"},
     [("Svc.run", "calls", "Repo.find")], [("Svc.run", "calls", "Other.find")])
case("php", "return-type", "a method's declared result gives the members of what it returns",
     PHP, {"a.php": "<?php\n" + PHP2 + "class Svc\n{\n    public function make(): Repo\n    {\n        return new Repo();\n    }\n\n"
                    "    public function run(): int\n    {\n        return $this->make()->find();\n    }\n}\n"},
     [("Svc.run", "calls", "Repo.find")], [("Svc.run", "calls", "Other.find")])
case("php", "nullable-parameter", "a `?T` parameter is a T",
     PHP, {"a.php": "<?php\n" + PHP2 + "function run(?Repo $r): int\n{\n    return $r->find();\n}\n"}, *PHP_BOTH)
case("php", "nullsafe", "`$r?->m()` is $r's m",
     PHP, {"a.php": "<?php\n" + PHP2 + "function run(?Repo $r): ?int\n{\n    return $r?->find();\n}\n"}, *PHP_BOTH)
case("php", "closure-use", "a closure's `use ($x)` variable keeps its type",
     PHP, {"a.php": "<?php\n" + PHP2 + "function run(Repo $r): int\n{\n    $f = function () use ($r) {\n        return $r->find();\n    };\n    return $f();\n}\n"}, *PHP_BOTH)
case("php", "arrow-function-parameter", "an arrow function's typed parameter has that type",
     PHP, {"a.php": "<?php\n" + PHP2 + "function run(array $repos): array\n{\n    return array_map(fn(Repo $r) => $r->find(), $repos);\n}\n"}, *PHP_BOTH)
case("php", "interface-typed", "a call through an interface type is the interface's method",
     PHP, {"a.php": "<?php\ninterface Store\n{\n    public function find(): int;\n}\n\nfunction run(Store $s): int\n{\n    return $s->find();\n}\n"},
     [("run", "calls", "Store.find")])
case("php", "group-use", "`use N\\{A, B};` binds both",
     PHP, {"lib/Repo.php": "<?php\nnamespace Lib;\n\nclass Repo\n{\n    public function find(): int\n    {\n        return 1;\n    }\n}\n",
           "lib/Cache.php": "<?php\nnamespace Lib;\n\nclass Cache\n{\n    public function get(): int\n    {\n        return 1;\n    }\n}\n",
           "app/A.php": "<?php\nnamespace App;\n\nuse Lib\\{Repo, Cache};\n\nclass A\n{\n    public function run(Repo $r, Cache $c): int\n    {\n"
                        "        return $r->find() + $c->get();\n    }\n}\n"},
     [("A.run", "calls", "Repo.find"), ("A.run", "calls", "Cache.get")])
case("php", "class-constant-fetch", "`C::class` is a reference to C",
     PHP, {"a.php": "<?php\nclass Repo\n{\n}\n\nfunction run(): string\n{\n    return Repo::class;\n}\n"},
     [("run", "references", "Repo")])
case("php", "new", "`new C(…)` constructs C",
     PHP, {"a.php": "<?php\nclass Repo\n{\n    public function __construct(int $size)\n    {\n    }\n}\n\nfunction run(): Repo\n{\n    return new Repo(1);\n}\n"},
     [("run", "calls", "Repo")])
case("php", "parent-constructor", "`parent::__construct()` is the parent's constructor",
     PHP, {"a.php": "<?php\nclass Base\n{\n    public function __construct()\n    {\n    }\n}\n\nclass A extends Base\n{\n"
                    "    public function __construct()\n    {\n        parent::__construct();\n    }\n}\n"},
     [("A.__construct", "calls", "Base.__construct")])
case("php", "namespaced-function", "an unqualified function call is first a function of the current namespace",
     PHP, {"lib/util.php": "<?php\nfunction twice(int $x): int\n{\n    return 0;\n}\n",
           "app/util.php": "<?php\nnamespace App;\n\nfunction twice(int $x): int\n{\n    return 2 * $x;\n}\n",
           "app/A.php": "<?php\nnamespace App;\n\nclass A\n{\n    public function run(): int\n    {\n        return twice(2);\n    }\n}\n"},
     [("A.run", "calls", "app/util.php:twice")], [("A.run", "calls", "lib/util.php:twice")])
case("php", "enum-method", "an enum case has the enum's methods",
     PHP, {"a.php": "<?php\nenum Status\n{\n    case Active;\n\n    public function label(): string\n    {\n        return 'a';\n    }\n}\n\n"
                    "function run(): string\n{\n    return Status::Active->label();\n}\n"},
     [("run", "calls", "Status.label")])
case("php", "static-return", "`new static()` / `new self()` construct the class",
     PHP, {"a.php": "<?php\nclass Repo\n{\n    public static function make(): static\n    {\n        return new static();\n    }\n}\n"},
     [("Repo.make", "calls", "Repo")])
case("php", "abstract-method", "a call of an abstract method through `$this` is the declaring class's",
     PHP, {"a.php": "<?php\nabstract class Shape\n{\n    abstract public function area(): int;\n\n    public function run(): int\n    {\n"
                    "        return $this->area();\n    }\n}\n"},
     [("Shape.run", "calls", "Shape.area")])
case("php", "static-method-via-class", "`C::m()` is C's static method",
     PHP, {"a.php": "<?php\nclass Util\n{\n    public static function twice(int $x): int\n    {\n        return 2 * $x;\n    }\n}\n\n"
                    "function run(): int\n{\n    return Util::twice(2);\n}\n"},
     [("run", "calls", "Util.twice")])
case("php", "callable-string", "a method passed as `[$this, 'm']` is a use of m",
     PHP, {"a.php": "<?php\nclass A\n{\n    public function handle(int $x): int\n    {\n        return $x;\n    }\n\n"
                    "    public function run(array $xs): array\n    {\n        return array_map([$this, 'handle'], $xs);\n    }\n}\n"},
     [("A.run", "indirect_call", "A.handle")])

# What the TypeScript compiler's own test suite showed missing (`oracle_typescript.py`)
# and the same rules where the other languages have them.
case("typescript", "block-scope", "a `const` of a block is a name of that block: beside it the outer one stands",
     "ECMAScript Language Specification, 'Block' (14.2) and 'Let and Const Declarations' (14.3.1)",
     {"a.ts": "class A { run() {} }\nclass B { go() {} }\n\nexport function f(x: A) {\n  {\n    const x = new B();\n    x.go();\n  }\n  x.run();\n}\n"},
     [("f", "calls", "A.run"), ("f", "calls", "B.go")])
case("typescript", "block-scope-branches", "the same name declared in two branches is two variables",
     "ECMAScript Language Specification, 'Block' (14.2)",
     {"a.ts": "class A { run() {} }\nclass B { run() {} }\n\nexport function f(c: boolean) {\n  if (c) {\n    const x = new A();\n    x.run();\n  }\n}\n\nexport function g(c: boolean) {\n  if (c) {\n    const x = new A();\n    return x;\n  } else {\n    const x = new B();\n    x.run();\n  }\n}\n"},
     [("f", "calls", "A.run"), ("g", "calls", "B.run")], [("g", "calls", "A.run")])
case("typescript", "type-parameter-hides", "a type parameter hides a type of its name declared outside",
     TS, {"a.ts": "export type T = string;\n\nexport interface Box<T> {\n  held: T;\n}\n\nexport function plain(v: T): T {\n  return v;\n}\n"},
     [("plain", "references", "T")], [("Box", "references", "T")])
case("typescript", "type-parameter-bound", "a value typed by a type parameter has the members of its constraint",
     TS, {"a.ts": "export interface Foo {\n  run(): void;\n}\n\nexport function f<T extends Foo>(x: T) {\n  x.run();\n}\n"},
     [("f", "calls", "Foo.run")])
case("typescript", "namespace-member-inside", "what a namespace declares is a name inside it, not beside it",
     TS, {"a.ts": "export namespace Types {\n  export interface Shape {\n    side: number;\n  }\n}\n\nexport interface Shape {\n  area: number;\n}\n\nexport interface Square extends Shape {\n  w: number;\n}\n"},
     [("Square", "inherits", "a.ts:Shape")], [("Square", "inherits", "Types.Shape")])
case("typescript", "parameter-property-read", "a constructor parameter with a modifier declares a property of the class",
     TS, {"a.ts": "export class Greeter {\n  constructor(public greeting: string) {}\n\n  greet() {\n    return this.greeting;\n  }\n}\n"},
     [("Greeter.greet", "reads", "Greeter.greeting")])
case("typescript", "parameter-called", "a call of a parameter is no call of a function of its name outside",
     TS, {"a.ts": "export function cb(n: number): number {\n  return n;\n}\n\nexport function run(cb: (x: string) => number) {\n  return cb('hi');\n}\n"},
     [], [("run", "calls", "cb")])
case("rust", "block-scope", "a `let` in a block ends with the block",
     RS, {"Cargo.toml": CARGO,
          "src/lib.rs": "pub struct A;\nimpl A {\n    pub fn run(&self) {}\n}\npub struct B {}\nimpl B {\n    pub fn go(&self) {}\n}\n\npub fn f(x: A) {\n    {\n        let x = B {};\n        x.go();\n    }\n    x.run();\n}\n"},
     [("f", "calls", "A.run"), ("f", "calls", "B.go")])
case("rust", "generic-bound-where", "a value of a type parameter has the methods of the trait that bounds it",
     RS, {"Cargo.toml": CARGO,
          "src/lib.rs": "pub trait Sink {\n    fn begin(&self);\n}\n\npub fn run<S: Sink>(s: S) {\n    s.begin();\n}\n\npub fn later<T>(s: T)\nwhere\n    T: Sink,\n{\n    s.begin();\n}\n"},
     [("run", "calls", "Sink.begin"), ("later", "calls", "Sink.begin")])
case("rust", "wrapper-holds-first-argument", "`Option<Space<M>>` holds a `Space`, whatever `M` is bound by",
     RS, {"Cargo.toml": CARGO,
          "src/lib.rs": "pub trait Matcher {\n    fn find(&self);\n}\n\npub struct Space<M: Matcher> {\n    pub inner: M,\n    pub dst: Vec<u8>,\n}\n\npub struct Replacer<M: Matcher> {\n    space: Option<Space<M>>,\n}\n\nimpl<M: Matcher> Replacer<M> {\n    pub fn clear(&mut self) {\n        if let Some(ref mut space) = self.space {\n            space.dst.clear();\n        }\n    }\n}\n"},
     [("Replacer.clear", "reads", "Space.dst")])
case("swift", "generic-bound", "a member asked of a type parameter is a member of the protocol that constrains it",
     SW, {"a.swift": "protocol Parsable {\n    static func parse()\n    func go()\n}\n\nfunc run<A>(_ v: A) where A: Parsable {\n    A.parse()\n    v.go()\n}\n\nfunc other<B: Parsable>(_ v: B) {\n    v.go()\n}\n"},
     [("run", "calls", "Parsable.parse"), ("run", "calls", "Parsable.go"), ("other", "calls", "Parsable.go")])
case("swift", "own-value-is-outer", "inside the value a `let` is declared with, its name is still the outer one",
     SW, {"a.swift": "protocol Convertible {\n    func asURL()\n}\n\nclass Wrap {\n    init(url: Convertible) {}\n}\n\nfunc use(_ convertible: Convertible) -> Wrap {\n    let convertible = Wrap(url: convertible)\n    return convertible\n}\n"},
     [("use", "uses", "Convertible")])
case("scala", "generic-method-parameters", "a generic method's parameters are its names, typed through an upper bound",
     SC, {"a.scala": "trait Base {\n  def id(): Int\n}\n\nobject O {\n  def opt[B <: Base](b: B): Int = b.id()\n}\n"},
     [("O.opt", "calls", "Base.id")])
case("go", "generic-constraint", "a value of a type parameter has the methods of its constraint",
     GO, {"go.mod": GOMOD,
          "a.go": "package app\n\ntype Runner interface {\n\tRun()\n}\n\nfunc Each[T Runner](x T) {\n\tx.Run()\n}\n"},
     [("Each", "calls", "Runner.Run")])
case("kotlin", "function-beside-parameter", "a call names the function beside a parameter of its name that cannot be called",
     KT, {"a.kt": "package app\n\nfun help(block: () -> String): String = block()\n\nfun describe(help: String): String {\n    return help { help }\n}\n"},
     [("describe", "calls", "help")])


case("typescript", "enum-member-value", "an enum member named as a value is a use of its enum",
     TS, {"a.ts": "export enum Color {\n  Red,\n  Green = 2,\n}\n\nexport function pick() {\n  return Color.Red;\n}\n\nexport function two() {\n  return Color.Green;\n}\n"},
     [("pick", "references", "Color"), ("two", "references", "Color")])
case("typescript", "type-alias-body", "the types a type alias is written with are uses of them",
     TS, {"a.ts": "export class Left {\n  l = 1;\n}\n\nexport class Right {\n  r = 2;\n}\n\nexport type Either = Left | Right;\n"},
     [("Either", "references", "Left"), ("Either", "references", "Right")])
case("typescript", "interface-signatures", "the types of an interface's property, call, construct and index signatures are uses of them",
     TS, {"a.ts": "export interface Opt {\n  x: number;\n}\n\nexport class Made {\n  m = 1;\n}\n\nexport class Kept {\n  k = 1;\n}\n\nexport class Held {\n  h = 1;\n}\n\nexport interface Maker {\n  (o: Opt): number;\n  new (n: number): Made;\n  [n: number]: Kept;\n  held: Held;\n}\n"},
     [("Maker", "references", "Opt"), ("Maker", "references", "Made"), ("Maker", "references", "Kept"),
      ("Maker", "references", "Held")])
case("typescript", "typeof-type", "`typeof ns.Class` in a type position is a use of the class",
     TS, {"a.ts": "export namespace Backbone {\n  export class Model {\n    id = 1;\n  }\n}\n\nexport interface Holder {\n  ctor: typeof Backbone.Model;\n}\n"},
     [("Holder", "references", "Backbone.Model")])
case("rust", "enum-variant-value", "an enum variant named as a value or a pattern is a use of its enum",
     RS, {"Cargo.toml": CARGO,
          "src/lib.rs": "pub enum Mode {\n    Fast,\n    Slow,\n}\n\npub fn pick() -> u8 {\n    let _m = Mode::Fast;\n    1\n}\n"},
     [("pick", "references", "Mode")])
case("java", "enum-constant-value", "an enum constant named as a value is a use of its enum",
     JAVA, {"Kind.java": "enum Kind {\n    A,\n    B\n}\n",
            "U.java": "class U {\n    Object pick() {\n        return Kind.A;\n    }\n}\n"},
     [("U.pick", "references", "Kind")])
case("csharp", "enum-member-value", "an enum member named as a value is a use of its enum",
     CS, {"A.cs": "namespace App\n{\n    public enum Dir\n    {\n        Up,\n        Down\n    }\n\n    public class V\n    {\n        public object Pick()\n        {\n            return Dir.Up;\n        }\n    }\n}\n"},
     [("V.Pick", "references", "Dir")])

# What the Scala compiler's SemanticDB expect files showed (`oracle_scala.py`).
case("scala", "other-package-not-in-reach", "a type of another package is not in reach without an import",
     SC, {"other/List.scala": "package other\n\nclass List[A] {\n  def x = 2\n}\n",
          "ext/Use.scala": "package ext\n\nclass Use {\n  def held: List[Int] = Nil\n}\n",
          "ext/Seen.scala": "package ext\n\nimport other.List\n\nclass Seen {\n  def held: List[Int] = new List[Int]\n}\n"},
     [("Seen.held", "references", "List")], [("Use.held", "references", "List")])
case("scala", "nested-type-not-in-reach", "a type nested in a class of another file is no bare name elsewhere",
     SC, {"example/Methods.scala": "package example\n\nclass Methods {\n  class List[T]\n  def own: List[Int] = new List[Int]\n}\n",
          "example/Other.scala": "package example\n\nclass Other {\n  def e1: List[Int] = Nil\n}\n"},
     [("Methods.own", "references", "Methods.List")], [("Other.e1", "references", "Methods.List")])
case("scala", "named-argument", "an argument given by name writes no variable of that name",
     SC, {"a.scala": "package app\n\nclass Local(a: Int, b: Int)\n\nobject Use {\n  var a = 0\n  def bump(): Unit = { a = 1 }\n  def make = new Local(a = 1, b = 2)\n}\n"},
     [("Use.bump", "writes", "Use.a")], [("Use.make", "writes", "Use.a")])
case("scala", "extension-type-parameters", "the type parameters of an `extension` are those of its methods",
     SC, {"a.scala": "package app\n\nclass A\n\nobject Ops {\n  extension [A](opt: Option[A]) def unwrap: A = opt.get\n  def plain: A = new A\n}\n"},
     [("Ops.plain", "references", "A")], [("Ops.unwrap", "references", "A")])
case("scala", "type-through-package", "`pkg.Type` in a type position is that package's type",
     SC, {"main/scopt/RenderingMode.scala": "package scopt\n\ntrait RenderingMode\n",
          "test/scopttest/Spec.scala": "package scopttest\n\nclass Spec {\n  def mode: scopt.RenderingMode = ???\n}\n"},
     [("Spec.mode", "references", "RenderingMode")])
case("scala", "pattern-types", "a constructor pattern and a typed pattern are uses of the type they name",
     SC, {"a.scala": "package app\n\ncase class Succ(p: Int)\nclass Obj\n\nobject M {\n  def f(n: Any): Int = n match {\n    case Succ(p) => p\n    case o: Obj => 2\n    case _ => 0\n  }\n}\n"},
     [("M.f", "references", "Succ"), ("M.f", "references", "Obj")])
case("scala", "bound-types", "the types a type parameter is bound by are uses of them",
     SC, {"a.scala": "package app\n\ntrait Txn[T]\ntrait Show[A]\n\ntrait Copy[In <: Txn[In]] {\n  def k = 1\n}\n\nclass W[T: Show] {\n  def k = 2\n}\n"},
     [("Copy", "references", "Txn"), ("W", "references", "Show")])
case("java", "other-package-not-in-reach", "a class of another package is not in reach without an import",
     JAVA, {"a/Lists.java": "package a;\n\npublic class Lists {\n    public static int of() {\n        return 1;\n    }\n}\n",
            "c/Seen.java": "package c;\n\nimport a.Lists;\n\nclass Seen {\n    int f() {\n        return Lists.of();\n    }\n}\n",
            "c/Blind.java": "package c;\n\nimport java.util.List;\n\nclass Blind {\n    Object f() {\n        return List.of();\n    }\n}\n",
            "d/List.java": "package d;\n\npublic class List {\n    public static Object of() {\n        return null;\n    }\n}\n"},
     [("Seen.f", "calls", "Lists.of")], [("Blind.f", "calls", "List.of")])
case("php", "global-class-is-not-a-namespaced-one", "`\\Name` is the global class, never one a namespace of the project declares",
     PHP, {"src/Clock.php": "<?php\n\nnamespace App\\Time;\n\nclass DateTimeImmutable\n{\n    public function __construct(string $at)\n    {\n    }\n}\n",
           "src/Use.php": "<?php\n\nnamespace App\\Other;\n\nclass Use_\n{\n    public function make()\n    {\n        return new \\DateTimeImmutable('@0');\n    }\n}\n"},
     [], [("Use_.make", "calls", "DateTimeImmutable")])

# The rest of the TypeScript compiler's list (`oracle_typescript.py`, engine 0.5.54).
case("typescript", "import-equals-require", "`import x = require('./m')` binds the module under that name",
     TS, {"back.ts": "export class Model {\n  id = 1;\n}\n\nexport function helper(): number {\n  return 1;\n}\n",
          "a.ts": "import Backbone = require(\"./back\");\n\nexport class Viz extends Backbone.Model {}\n\nexport function f() {\n  return Backbone.helper();\n}\n"},
     [("Viz", "inherits", "Model"), ("f", "calls", "helper")])
case("typescript", "export-assignment", "`export = E` is what `import x = require()` of the module names",
     TS, {"e.ts": "enum E {\n  A,\n  B,\n}\n\nexport = E;\n",
          "use.ts": "import EnumE = require(\"./e\");\n\nexport function g() {\n  return EnumE.A;\n}\n"},
     [("g", "references", "E")])
case("typescript", "import-alias", "`import A = N.M` names what the path names",
     TS, {"a.ts": "namespace My.Internal {\n  export function getThing(): void {}\n  export class Cls {\n    run() {}\n  }\n}\n\nnamespace Other {\n  import Internal = My.Internal;\n  import C = My.Internal.Cls;\n\n  export function k() {\n    Internal.getThing();\n    const c = new C();\n    c.run();\n  }\n}\n"},
     [("Other.k", "calls", "My.Internal.getThing"), ("Other.k", "calls", "My.Internal.Cls.run")])
case("typescript", "function-namespace-merge", "a function merged with a namespace of its name has what the namespace declares",
     TS, {"a.ts": "function Cf(x: number) {\n  return x;\n}\n\nnamespace Cf {\n  export function foo() {\n    return 1;\n  }\n}\n\nexport function h() {\n  return Cf.foo();\n}\n"},
     [("h", "calls", "Cf.foo")])
case("typescript", "function-as-value", "a function named as a value is a use of it",
     TS, {"a.ts": "export function helper(): number {\n  return 1;\n}\n\nexport function user(cb = helper) {\n  const q = helper;\n  return [q, cb];\n}\n"},
     [("user", "references", "helper")])
case("typescript", "block-scope-file-level", "a `let` in a block at the top of a file is the block's, beside a variable of the file",
     "ECMAScript Language Specification, 'Block' (14.2) and 'Let and Const Declarations' (14.3.1)",
     {"a.ts": "export let count = 0;\n\nexport function bump() {\n  count += 1;\n}\n\nexport function inner() {\n  {\n    let count = 5;\n    count += 1;\n  }\n}\n\nexport function after() {\n  {\n    let count = 5;\n    void count;\n  }\n  return count;\n}\n"},
     [("bump", "writes", "count"), ("after", "reads", "count")], [("inner", "writes", "count")])
case("python", "function-named-as-value", "a function named as a value is a use of it",
     PY, {"a.py": "def helper():\n    return 1\n\n\ndef user():\n    q = helper\n    return q\n"},
     [("user", "references", "helper")])
case("go", "function-named-as-value", "a function named as a value is a use of it",
     GO, {"go.mod": GOMOD,
          "a.go": "package app\n\nfunc helper() int {\n\treturn 1\n}\n\nfunc user() func() int {\n\tq := helper\n\treturn q\n}\n"},
     [("user", "references", "helper")])

# What the Kotlin and Swift compilers' own test suites showed (`oracle_kotlin.py`,
# `oracle_swift.py`, engine 0.5.55): the places a type is named to test, convert
# or catch a value, in every language that has them.
case("kotlin", "property-type", "the type a property is declared with is a use of it",
     KT, {"a.kt": "package app\n\nclass Engine\n\nclass Car {\n    val engine: Engine = Engine()\n}\n\nfun local(): Int {\n    val spare: Engine? = null\n    return if (spare == null) 0 else 1\n}\n"},
     [("Car", "references", "Engine"), ("local", "references", "Engine")])
case("kotlin", "is-and-as", "`x is T` and `x as T` are uses of T",
     KT, {"a.kt": "package app\n\nopen class Shape\nclass Circle : Shape()\nclass Square : Shape()\n\nfun kind(s: Any): Int {\n    if (s is Circle) return 1\n    val q = s as? Square\n    return if (q == null) 0 else 2\n}\n"},
     [("kind", "references", "Circle"), ("kind", "references", "Square")])
case("kotlin", "when-is", "`is T ->` in a `when` is a use of T, written through its outer type too",
     KT, {"a.kt": "package app\n\nsealed class Sealed {\n    class B : Sealed()\n    object A : Sealed()\n}\n\nfun pick(q: Sealed): Int = when (q) {\n    is Sealed.B -> 1\n    Sealed.A -> 2\n}\n"},
     [("pick", "references", "Sealed.B"), ("pick", "references", "Sealed.A")])
case("kotlin", "catch-type", "the type a `catch` names is a use of it",
     KT, {"a.kt": "package app\n\nclass Boom : Exception()\n\nfun safe(): Int {\n    try {\n        return 1\n    } catch (e: Boom) {\n        return 2\n    }\n}\n"},
     [("safe", "references", "Boom")])
case("kotlin", "object-as-value", "an object named as a value is a use of it",
     KT, {"a.kt": "package app\n\nopen class Base {\n    object Nested : Base()\n}\n\nfun take(b: Base): Base = b\n\nfun give(): Base = take(Base.Nested)\n"},
     [("give", "references", "Base.Nested")])
case("kotlin", "typealias-body", "the type a typealias names is a use of it",
     KT, {"a.kt": "package app\n\nclass Engine\n\ntypealias Motor = Engine\n"},
     [("Motor", "references", "Engine")])
case("java", "instanceof-cast-catch", "`instanceof T`, `(T) x` and `catch (T e)` are uses of T",
     JAVA, {"A.java": "class Foo {\n}\n\nclass Bar extends Foo {\n}\n\nclass Baz extends Foo {\n}\n\nclass Err extends RuntimeException {\n}\n\nclass J {\n    int f(Object s) {\n        if (s instanceof Bar) {\n            return 1;\n        }\n        Object c = (Baz) s;\n        try {\n            return c.hashCode();\n        } catch (Err e) {\n            return 2;\n        }\n    }\n}\n"},
     [("J.f", "references", "Bar"), ("J.f", "references", "Baz"), ("J.f", "references", "Err")])
case("csharp", "is-as-catch", "`x is T`, `x as T` and `catch (T e)` are uses of T",
     CS, {"A.cs": "namespace App\n{\n    public class Foo { }\n    public class Bar : Foo { }\n    public class Baz : Foo { }\n    public class Qux : Foo { }\n    public class Err : System.Exception { }\n\n    public class C\n    {\n        public int F(object s)\n        {\n            if (s is Bar) return 1;\n            if (s is Qux q) return 3;\n            var c = s as Baz;\n            try { return c == null ? 0 : 4; }\n            catch (Err) { return 2; }\n        }\n    }\n}\n"},
     [("C.F", "references", "Bar"), ("C.F", "references", "Qux"), ("C.F", "references", "Baz"),
      ("C.F", "references", "Err")])
case("swift", "is-and-as", "`x is T`, `x as? T` and `catch let e as T` are uses of T",
     SW, {"a.swift": "class Foo {}\nclass Bar: Foo {}\nclass Baz: Foo {}\nstruct Err: Error {}\n\nfunc kind(_ s: Any) -> Int {\n    if s is Bar { return 1 }\n    let c = s as? Baz\n    do {\n        throw Err()\n    } catch let e as Err {\n        _ = e\n        return 2\n    } catch {\n        return c == nil ? 0 : 3\n    }\n}\n"},
     [("kind", "references", "Bar"), ("kind", "references", "Baz"), ("kind", "references", "Err")])
case("swift", "typealias-and-where", "what a typealias composes and what a `where` clause bounds by are uses of those types",
     SW, {"a.swift": "protocol P1 {}\nprotocol P2 {}\nclass Base {}\n\ntypealias Both = Base & P1\n\nclass Foo<Bar> {}\n\nextension Foo where Bar: P2 {\n    func bar() {}\n}\n\nfunc gen<T>(x: T) where T: P1 {}\n"},
     [("Both", "references", "Base"), ("Both", "references", "P1"), ("gen", "references", "P1")])
case("typescript", "as-and-instanceof", "`x as T` and `x instanceof T` are uses of T",
     TS, {"a.ts": "export class Foo {\n  f = 1;\n}\n\nexport class Bar extends Foo {\n  b = 2;\n}\n\nexport class Baz extends Foo {\n  z = 3;\n}\n\nexport function f(s: unknown) {\n  if (s instanceof Bar) return 1;\n  const c = s as Baz;\n  return c.z;\n}\n"},
     [("f", "references", "Bar"), ("f", "references", "Baz")])
case("go", "type-assertion-and-switch", "`x.(T)` and a type switch's `case T` are uses of T",
     GO, {"go.mod": GOMOD,
          "a.go": "package app\n\ntype Foo struct{}\n\ntype Bar struct{}\n\nfunc kind(s interface{}) int {\n\tif _, ok := s.(Bar); ok {\n\t\treturn 1\n\t}\n\tswitch s.(type) {\n\tcase Foo:\n\t\treturn 2\n\t}\n\treturn 0\n}\n"},
     [("kind", "references", "Bar"), ("kind", "references", "Foo")])
case("php", "instanceof-and-catch", "`$x instanceof T` and `catch (T $e)` are uses of T",
     PHP, {"src/A.php": "<?php\n\nnamespace App;\n\nclass Foo\n{\n}\n\nclass Bar extends Foo\n{\n}\n\nclass Err extends \\Exception\n{\n}\n\nclass P\n{\n    public function f($s)\n    {\n        if ($s instanceof Bar) {\n            return 1;\n        }\n        try {\n            return 0;\n        } catch (Err $e) {\n            return 2;\n        }\n    }\n}\n"},
     [("P.f", "references", "Bar"), ("P.f", "references", "Err")])
case("rust", "associated-type-is-no-file-name", "the type an `impl` gives its trait is no name of the file",
     RS, {"Cargo.toml": CARGO,
          "src/lib.rs": "pub struct Resp;\npub struct Other;\n\npub type Outcome<T> = std::result::Result<T, Other>;\n\npub trait Handler {\n    type Outcome;\n    fn run(&self);\n}\n\npub struct A;\npub struct B;\n\nimpl Handler for A {\n    type Outcome = Outcome<Resp>;\n    fn run(&self) {}\n}\n\nimpl Handler for B {\n    type Outcome = Outcome<Other>;\n    fn run(&self) {}\n}\n\npub fn free() -> Outcome<Resp> {\n    Ok(Resp)\n}\n"},
     [("free", "references", "src/lib.rs:Outcome")])
case("rust", "alias-and-where", "what a type alias names and what a `where` clause bounds by are uses of those types",
     RS, {"Cargo.toml": CARGO,
          "src/lib.rs": "pub struct Wide;\n\npub trait Lim {}\n\npub type Id = Wide;\n\npub fn lim<T>(x: T) -> T\nwhere\n    T: Lim,\n{\n    x\n}\n"},
     [("Id", "references", "Wide"), ("lim", "references", "Lim")])

# What the compilers themselves showed on whole repositories (`oracle_go.py`: go/types;
# `oracle_rust.py`: rust-analyzer; engine 0.5.56).
case("go", "short-var-hides-package-name", "a name declared with `:=` is a local, whatever it is assigned",
     GO, {"go.mod": GOMOD,
          "a.go": "package app\n\nfunc endpoint() string {\n\treturn \"/pkg\"\n}\n\nfunc take(s interface{}) {}\n\nfunc local() {\n\tendpoint := \"/secure\"\n\ttake(endpoint)\n}\n\nfunc outer() {\n\ttake(endpoint)\n}\n"},
     [("outer", "indirect_call", "endpoint")], [("local", "references", "endpoint"), ("local", "indirect_call", "endpoint")])
case("go", "multi-name-var", "`var a, b T` declares every name",
     GO, {"go.mod": GOMOD,
          "a.go": "package app\n\nvar expected = \"pkg\"\n\nfunc local() string {\n\tvar body, expected string\n\texpected = \"x\"\n\treturn body + expected\n}\n\nfunc pkg() string {\n\treturn expected\n}\n"},
     [("pkg", "reads", "expected")], [("local", "writes", "expected"), ("local", "reads", "expected")])
case("go", "struct-field-is-no-package-name", "a field of a struct, named or not, is no name of the package",
     GO, {"go.mod": GOMOD,
          "a.go": "package app\n\ntype Client struct{}\n\nfunc table() int {\n\tcases := []struct {\n\t\twanted int\n\t\tcmd    *Client\n\t}{}\n\treturn len(cases)\n}\n",
          "b.go": "package app\n\nfunc other() int {\n\twanted := 1\n\twanted = 2\n\treturn wanted\n}\n"},
     [], [("other", "writes", "wanted"), ("other", "reads", "wanted")])
case("go", "member-read-types-a-local", "a local assigned a field is of the field's type",
     GO, {"go.mod": GOMOD,
          "a.go": "package app\n\ntype Inner struct {\n\tSize int\n}\n\nfunc (i *Inner) Dump() {}\n\ntype Other struct{}\n\nfunc (o *Other) Dump() {}\n\ntype Outer struct {\n\tIn *Inner\n}\n\nfunc use(o *Outer) int {\n\tin := o.In\n\tin.Dump()\n\treturn in.Size\n}\n"},
     [("use", "calls", "Inner.Dump"), ("use", "reads", "Inner.Size")], [("use", "calls", "Other.Dump")])
case("go", "library-receiver-is-no-guess", "a method called on a value the source types as a library's is the library's",
     GO, {"go.mod": GOMOD,
          "a.go": "package app\n\nimport (\n\t\"bytes\"\n\t\"net/http/cookiejar\"\n\t\"net/url\"\n)\n\ntype Client struct{}\n\nfunc (c *Client) SetCookies(u *url.URL, n int) {}\n\nfunc (c *Client) Bytes() []byte {\n\treturn nil\n}\n\nfunc newJar() *cookiejar.Jar {\n\tjar, _ := cookiejar.New(nil)\n\treturn jar\n}\n\nfunc use() []byte {\n\tjar := newJar()\n\tjar.SetCookies(nil, nil)\n\tvar buf bytes.Buffer\n\treturn buf.Bytes()\n}\n"},
     [("use", "calls", "newJar")], [("use", "calls", "Client.SetCookies"), ("use", "calls", "Client.Bytes")])
case("rust", "field-beside-a-method", "`self.x` with no call is the field, beside a method of its name",
     RS, {"Cargo.toml": CARGO,
          "src/lib.rs": "pub struct Config {\n    proxy: u8,\n}\n\nimpl Config {\n    pub fn proxy(mut self, p: u8) -> Self {\n        self.proxy = p;\n        self\n    }\n\n    pub fn show(&self) -> u8 {\n        self.proxy\n    }\n}\n"},
     [("Config.show", "reads", "src/lib.rs:Config.proxy")], [("Config.show", "references", "Config.proxy")])
case("rust", "result-of-self", "`-> Result<Self, E>` gives the type the method is declared for",
     RS, {"Cargo.toml": CARGO,
          "src/lib.rs": "pub struct Error;\n\npub struct Authority;\n\nimpl Authority {\n    pub fn username(&self) -> u8 {\n        1\n    }\n}\n\npub struct Proxy;\n\nimpl Proxy {\n    pub fn new(_s: &str) -> Result<Self, Error> {\n        Err(Error)\n    }\n\n    pub fn username(&self) -> u8 {\n        2\n    }\n}\n\npub fn t() -> u8 {\n    let proxy = Proxy::new(\"x\").ok().unwrap();\n    proxy.username()\n}\n\npub fn direct() -> u8 {\n    let proxy = match Proxy::new(\"x\") {\n        Ok(p) => p,\n        Err(_) => return 0,\n    };\n    proxy.username()\n}\n\npub fn plain() -> u8 {\n    let proxy = Proxy::new(\"x\").unwrap_or(Proxy);\n    proxy.username()\n}\n"},
     [("plain", "calls", "Proxy.username")], [("plain", "calls", "Authority.username")])
case("rust", "untyped-let-hides-a-function", "a `let` of any value is a local where it is passed on",
     RS, {"Cargo.toml": CARGO,
          "src/lib.rs": "pub fn hostname() -> u8 {\n    1\n}\n\npub fn take(_x: u8) {}\n\npub fn user() {\n    let hostname = 3u8;\n    take(hostname);\n}\n\npub fn caller() {\n    take(hostname());\n}\n"},
     [("caller", "calls", "hostname")], [("user", "indirect_call", "hostname"), ("user", "references", "hostname")])
case("rust", "another-crates-name", "a name of another crate is in reach only through a `use`",
     RS, {"Cargo.toml": "[workspace]\nmembers = [\"a\", \"b\"]\nresolver = \"2\"\n",
          "a/Cargo.toml": "[package]\nname = \"a\"\nversion = \"0.1.0\"\nedition = \"2021\"\n",
          "a/src/lib.rs": "pub fn run() -> Result<u8, String> {\n    Ok(1)\n}\n",
          "b/Cargo.toml": "[package]\nname = \"b\"\nversion = \"0.1.0\"\nedition = \"2021\"\n",
          "b/src/lib.rs": "pub type Result<T> = std::result::Result<T, String>;\n\npub fn local() -> Result<u8> {\n    Ok(1)\n}\n"},
     [("local", "references", "Result")], [("run", "references", "Result")])
case("typescript", "destructured-closure-parameter", "every name a closure's parameter pattern binds is the closure's",
     TS, {"bench.ts": "export function value(): number {\n  return 1;\n}\n\nexport function take(x: unknown): unknown {\n  return x;\n}\n",
          "a.ts": "import { value, take } from \"./bench\";\n\nexport function run(pairs: [string, number][]) {\n  return pairs.map(([key, value], index) => take([key, value, index]));\n}\n\nexport function outer() {\n  return take(value);\n}\n"},
     [("outer", "indirect_call", "value")], [("run", "indirect_call", "value"), ("run", "references", "value")])

# --- running ------------------------------------------------------------------

def _matches(node: dict[str, Any], want: str) -> bool:
    qual = node.get("qualname")
    if ":" in want:
        # `path:name`: that very definition, by its whole qualified name
        path, want = want.split(":", 1)
        return bool(str(node.get("source_file") or "") == path and qual == want)
    if qual:
        return bool(qual == want or qual.endswith("." + want))
    return bool(node.get("label") == want)


def run_case(c: dict[str, Any]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "project"
        for rel, src in c["files"].items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(src, encoding="utf-8")
        out = Path(tmp) / "graph.json"
        done = subprocess.run([sys.executable, "-m", "graphlore_engine", "build", str(root),
                               "--out", str(out)], capture_output=True, text=True)
        if done.returncode != 0 or not out.exists():
            return {"id": c["id"], "ok": False, "missing": [], "unwanted": [],
                    "error": done.stderr.strip()[-200:]}
        graph = json.loads(out.read_text(encoding="utf-8"))
    nodes = {n["id"]: n for n in graph["nodes"]}

    def has(edge: Edge) -> bool:
        src, rel, dst = edge
        return any(e.get("relation") == rel
                   and _matches(nodes.get(e["source"], {}), src)
                   and _matches(nodes.get(e["target"], {}), dst)
                   for e in graph["links"])

    missing = [list(e) for e in c["expect"] if not has(e)]
    unwanted = [list(e) for e in c["absent"] if has(e)]
    return {"id": c["id"], "ok": not missing and not unwanted, "missing": missing,
            "unwanted": unwanted}


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--only", default="", help="comma-separated languages or case ids")
    ap.add_argument("--failed", action="store_true", help="print only the cases that fail")
    ap.add_argument("--json", help="write the result of every case here")
    ap.add_argument("--baseline", help="an earlier --json: say what changed")
    ap.add_argument("--markdown", action="store_true", help="print the table as Markdown")
    args = ap.parse_args()
    only = {x for x in args.only.split(",") if x}
    cases = [c for c in CASES if not only or c["lang"] in only or c["id"] in only]
    results = {c["id"]: run_case(c) for c in cases}

    langs: list[str] = []
    for c in cases:
        if c["lang"] not in langs:
            langs.append(c["lang"])
    for lang in langs:
        mine = [c for c in cases if c["lang"] == lang]
        passed = sum(1 for c in mine if results[c["id"]]["ok"])
        if args.markdown:
            print(f"\n### {lang} ({passed} of {len(mine)})\n\n{mine[0]['where']}.\n")
            print("| Rule | Case | Engine |\n|---|---|---|")
        else:
            print(f"\n{lang}: {passed} of {len(mine)}   [{mine[0]['where']}]")
        for c in mine:
            r = results[c["id"]]
            if args.failed and r["ok"]:
                continue
            why = ""
            if r.get("error"):
                why = "build failed: " + r["error"]
            if r["missing"]:
                why += "no " + "; ".join(f"{s} -{rel}-> {d}" for s, rel, d in r["missing"])
            if r["unwanted"]:
                why += (" " if why else "") + "has " + "; ".join(
                    f"{s} -{rel}-> {d}" for s, rel, d in r["unwanted"])
            if args.markdown:
                rule = c["rule"].replace("|", "\\|")
                state = "yes" if r["ok"] else f"**no**: {why}".replace("|", "\\|")
                print(f"| {rule} | `{c['id'].split('/', 1)[1]}` | {state} |")
            else:
                print(f"  {'ok  ' if r['ok'] else 'FAIL'} {c['id']:38} {c['rule']}")
                if why:
                    print(f"       {why}")
    total = sum(1 for r in results.values() if r["ok"])
    print(f"\n{total} of {len(results)} rules hold")

    if args.baseline:
        before = {r["id"]: r["ok"] for r in json.loads(Path(args.baseline).read_text())["cases"]}
        fixed = sorted(i for i, r in results.items() if r["ok"] and before.get(i) is False)
        broken = sorted(i for i, r in results.items() if not r["ok"] and before.get(i) is True)
        new = sorted(i for i in results if i not in before)
        print(f"against the baseline: {len(fixed)} fixed, {len(broken)} broken, {len(new)} new")
        for i in fixed:
            print(f"  fixed:  {i}")
        for i in broken:
            print(f"  BROKEN: {i}")
        if broken:
            return 1
    if args.json:
        Path(args.json).write_text(json.dumps(
            {"passed": total, "total": len(results), "cases": list(results.values())}, indent=1)
            + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
