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
