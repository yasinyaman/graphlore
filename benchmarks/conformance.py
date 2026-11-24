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


# How the cases that follow were found (docs/rule-sources.md): written from the
# language's `specification`; shown missing by a `compiler` (its test suite, or
# its answers on whole repositories); by `comparison` with graphify's graph; or
# from the `grammar` (a place a name can be written that was not read).
_FOUND = ["specification"]


def found_by(how: str) -> None:
    _FOUND[0] = how


def case(lang: str, cid: str, rule: str, where: str, files: dict[str, str],
         expect: list[Edge] | None = None, absent: list[Edge] | None = None, *,
         source: str | None = None, since: str | None = None, until: str | None = None) -> None:
    """`source`: where in the language's own tools the rule was read, as
    `repository@commit:path` and the function, or a section of the
    specification (`where` is the specification as a whole). `since` /
    `until`: the versions of the language the rule holds for; none, all."""
    CASES.append({"lang": lang, "id": f"{lang}/{cid}", "rule": rule, "where": where,
                  "files": files, "expect": expect or [], "absent": absent or [],
                  "source": source, "since": since, "until": until, "found": _FOUND[0]})


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
found_by("compiler")
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

# What Roslyn showed on whole repositories (`oracle_csharp.py`), and what was left of the
# Kotlin and Swift lists (engine 0.5.58).
case("csharp", "type-by-arity", "a type is told from its generic namesake by the type arguments written",
     CS, {"A.cs": "namespace App\n{\n    public class RestResponse\n    {\n        public int Code;\n    }\n\n    public class RestResponse<T> : RestResponse\n    {\n        public T Data;\n    }\n\n    public class C\n    {\n        public RestResponse Plain(int x)\n        {\n            return new RestResponse();\n        }\n\n        public RestResponse<string> Gen(int x)\n        {\n            return new RestResponse<string>();\n        }\n    }\n}\n"},
     [("C.Plain", "references", "A.cs:App.RestResponse")], [])
case("csharp", "generic-use-is-no-plain-type", "`List<string>` is never a project class `List` that takes no type argument",
     CS, {"A.cs": "using System.Collections.Generic;\n\nnamespace App\n{\n    public class List\n    {\n        public int N;\n    }\n\n    public class C\n    {\n        public List<string> Tags()\n        {\n            return new List<string>();\n        }\n\n        public List Own()\n        {\n            return new List();\n        }\n    }\n}\n"},
     [("C.Own", "references", "List")], [("C.Tags", "references", "List"), ("C.Tags", "calls", "List")])
case("csharp", "extension-by-type-arguments", "a call written with more type arguments than any method of the type takes is the extension's",
     CS, {"A.cs": "namespace App\n{\n    public class Parser\n    {\n        public int Parse<T>(string[] args)\n        {\n            return 1;\n        }\n    }\n\n    public static class ParserExtensions\n    {\n        public static int Parse<T1, T2>(this Parser parser, string[] args)\n        {\n            return 2;\n        }\n    }\n\n    public class Use\n    {\n        public int Two(Parser p, string[] args)\n        {\n            return p.Parse<int, string>(args);\n        }\n\n        public int One(Parser p, string[] args)\n        {\n            return p.Parse<int>(args);\n        }\n    }\n}\n"},
     [("Use.Two", "calls", "ParserExtensions.Parse"), ("Use.One", "calls", "Parser.Parse")],
     [("Use.Two", "calls", "Parser.Parse")])
case("csharp", "local-declared-type", "the type a local is declared with, and `typeof(T)`, are uses of the type",
     CS, {"A.cs": "namespace App\n{\n    public class Result<T>\n    {\n        public T Value;\n    }\n\n    public class Marker\n    {\n    }\n\n    public class C\n    {\n        public object M()\n        {\n            Result<int> r = null;\n            var t = typeof(Marker);\n            return r ?? (object)t;\n        }\n    }\n}\n"},
     [("C.M", "references", "Result"), ("C.M", "references", "Marker")])
case("kotlin", "constructor-property-read", "a constructor parameter written `val` is a property of the class",
     KT, {"a.kt": "package app\n\nclass Holder(val parameters: List<String>, other: Int) {\n    val size = other\n\n    fun count(): Int {\n        return parameters.size\n    }\n}\n\nfun use(h: Holder): List<String> = h.parameters\n"},
     [("Holder.count", "reads", "Holder.parameters"), ("use", "reads", "Holder.parameters")])
case("kotlin", "named-argument-label", "the name of an argument given by name reads no property of that name",
     KT, {"a.kt": "package app\n\nopen class Command(val name: String)\n\nclass Tool {\n    val name = \"tool\"\n\n    fun make(): Command {\n        return Command(name = \"x\")\n    }\n\n    fun own(): String {\n        return name\n    }\n}\n"},
     [("Tool.own", "reads", "Tool.name")], [("Tool.make", "reads", "Tool.name"), ("Tool.make", "reads", "Command.name")])
case("swift", "self-constructs", "`Self()` constructs the type around",
     SW, {"a.swift": "struct Point {\n    var x = 0\n\n    init() {}\n\n    static func origin() -> Point {\n        Self()\n    }\n}\n"},
     [("Point.origin", "calls", "Point")])

# The confirmed edges of graphify's the native graph lacked (`verify_graphify.py`, engine 0.5.59).
found_by("comparison")
case("csharp", "property-body-is-the-property", "what a property's accessor calls is the property's call, not its class's",
     CS, {"A.cs": "namespace App\n{\n    public static class Setter\n    {\n        public static void Set(ref bool f, bool v) { f = v; }\n    }\n\n    public static class Helper\n    {\n        public static int Size() { return 1; }\n    }\n\n    public class Settings\n    {\n        private bool mode;\n\n        public bool Mode\n        {\n            get => mode;\n            set => Setter.Set(ref mode, value);\n        }\n\n        public int Count => Helper.Size();\n    }\n}\n"},
     [("Settings.Mode", "calls", "Setter.Set"), ("Settings.Count", "calls", "Helper.Size")],
     [("Settings", "calls", "Setter.Set")])
case("csharp", "block-namespace-sees-outer-extension", "a namespace written as a block sees the extensions of the namespaces around it",
     CS, {"A.cs": "namespace App\n{\n    public class Help\n    {\n        public override string ToString() { return \"\"; }\n    }\n}\n",
          "tests/Ext.cs": "namespace App.Tests\n{\n    static class StringExtensions\n    {\n        public static string[] ToNotEmptyLines(this string value) { return null; }\n    }\n}\n",
          "tests/unit/T.cs": "using App;\n\nnamespace App.Tests.Unit\n{\n    public class Tests\n    {\n        public int Run(Help sut)\n        {\n            var lines = sut.ToString().ToNotEmptyLines();\n            return lines.Length;\n        }\n    }\n}\n"},
     [("Tests.Run", "calls", "StringExtensions.ToNotEmptyLines")])
case("csharp", "brace-opened-in-a-directive", "a brace an `#if` opens and a later `#if` closes hides no member after it",
     CS, {"A.cs": "namespace App;\n\npublic class Handler\n{\n    public bool Cookies;\n}\n\npublic class Client\n{\n    static void Configure(Handler handler)\n    {\n#if NET\n        if (!System.OperatingSystem.IsBrowser()) {\n#endif\n        handler.Cookies = false;\n#if NET\n        }\n#endif\n    }\n\n    public void Dispose()\n    {\n    }\n}\n\npublic class Tests\n{\n    readonly Client _client = new Client();\n\n    public void Done()\n    {\n        _client.Dispose();\n    }\n}\n"},
     [("Tests.Done", "calls", "Client.Dispose")])
case("csharp", "local-from-member-read", "a local assigned a member read is what the member holds",
     CS, {"A.cs": "namespace App\n{\n    public class Parser\n    {\n        public static Parser Default { get { return null; } }\n        public Parser Next;\n\n        public int Go() { return 1; }\n    }\n\n    public class Other\n    {\n        public int Go() { return 2; }\n    }\n\n    public class Tests\n    {\n        public int A()\n        {\n            var sut = Parser.Default;\n            return sut.Go();\n        }\n\n        public int B(Parser p)\n        {\n            var next = p.Next;\n            return next.Go();\n        }\n    }\n}\n"},
     [("Tests.A", "calls", "Parser.Go"), ("Tests.B", "calls", "Parser.Go")],
     [("Tests.A", "calls", "Other.Go"), ("Tests.B", "calls", "Other.Go")])
case("csharp", "initializer-without-arguments", "`new T { … }` with no argument list calls the constructor that takes none",
     CS, {"A.cs": "namespace App\n{\n    public class Help\n    {\n        public bool Dashes { get; set; }\n\n        public Help() { }\n\n        public Help(string heading) { }\n    }\n\n    public class Tests\n    {\n        public Help Make()\n        {\n            return new Help { Dashes = true };\n        }\n    }\n}\n"},
     [("Tests.Make", "calls", "Help.Help")])
case("csharp", "partial-type-constructor", "a partial type's constructors are those of every file that declares it",
     CS, {"A.cs": "namespace App\n{\n    public partial class Client\n    {\n        public void Send() { }\n    }\n}\n",
          "B.cs": "namespace App\n{\n    public partial class Client\n    {\n        public Client(string url = null) { }\n\n        public Client(int port, string url) { }\n    }\n}\n",
          "T.cs": "namespace App.Tests\n{\n    public class Tests\n    {\n        public Client Make()\n        {\n            return new Client();\n        }\n    }\n}\n"},
     [("Tests.Make", "calls", "Client.Client")])

# The rest of the confirmed edges of graphify's (`verify_graphify.py`, engine 0.5.60): Rust and Go.
case("rust", "tuple-field-types", "the types of a tuple struct's and a tuple variant's fields are uses of them",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub struct Agent {\n    pub n: u32,\n}\n\npub struct Timeout;\n\npub struct Call<T> {\n    pub t: T,\n}\n\npub struct AgentInstance(pub(crate) Agent);\n\npub enum Error {\n    Timeout(Timeout),\n    Redirect(Call<Agent>, Agent),\n}\n"},
     [("AgentInstance", "references", "Agent"), ("Error.Timeout", "references", "src/lib.rs:Timeout"),
      ("Error.Redirect", "references", "Call"), ("Error.Redirect", "references", "Agent")])
case("rust", "item-type-is-the-items", "the type a `const` or `static` item is written with is the item's use",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub struct Options {\n    pub casei: bool,\n}\n\npub trait Flag {\n    fn name(&self) -> &str;\n}\n\npub const CASEI: Options = Options { casei: true };\n\npub const FLAGS: &[&dyn Flag] = &[];\n\npub static COUNT: Options = Options { casei: false };\n"},
     [("CASEI", "references", "Options"), ("FLAGS", "references", "Flag"), ("COUNT", "references", "Options")])
case("rust", "crate-root-named-by-the-manifest", "`crate::` starts at the file a `[[bin]] path` names",
     RS, {"Cargo.toml": "[package]\nname = \"tool\"\nversion = \"0.1.0\"\n\n[[bin]]\nbench = false\npath = \"crates/core/main.rs\"\nname = \"tl\"\n",
          "crates/core/main.rs": "mod flags;\nmod messages;\n\nfn main() {\n    flags::parse::run();\n}\n",
          "crates/core/messages.rs": "pub fn set_messages(_on: bool) {}\n",
          "crates/core/flags/mod.rs": "pub mod doc;\npub mod parse;\n",
          "crates/core/flags/parse.rs": "pub fn lookup(_name: &str) -> Option<u8> {\n    None\n}\n\npub fn run() {\n    crate::messages::set_messages(true);\n}\n",
          "crates/core/flags/doc/mod.rs": "pub mod help;\npub mod version;\n",
          "crates/core/flags/doc/version.rs": "pub fn generate_digits() -> String {\n    String::new()\n}\n",
          "crates/core/flags/doc/help.rs": "use crate::flags::doc::version;\n\npub fn generate() -> String {\n    let _ = crate::flags::parse::lookup(\"x\");\n    version::generate_digits()\n}\n"},
     [("run", "calls", "set_messages"), ("generate", "calls", "lookup"), ("generate", "calls", "generate_digits"),
      ("main", "calls", "run")])
case("rust", "impl-of-the-files-own-type", "an `impl` is of the type its own file names, where two modules each have a type of the name",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "mod json;\nmod standard;\n",
          "src/json.rs": "struct Config {\n    pretty: bool,\n}\n\nimpl Default for Config {\n    fn default() -> Config {\n        Config { pretty: false }\n    }\n}\n\npub struct JsonBuilder {\n    config: Config,\n}\n\nimpl JsonBuilder {\n    pub fn new() -> JsonBuilder {\n        JsonBuilder { config: Config::default() }\n    }\n}\n",
          "src/standard.rs": "struct Config {\n    stats: bool,\n}\n\nimpl Default for Config {\n    fn default() -> Config {\n        Config { stats: false }\n    }\n}\n\npub struct StandardBuilder {\n    config: Config,\n}\n\nimpl StandardBuilder {\n    pub fn new() -> StandardBuilder {\n        StandardBuilder { config: Config::default() }\n    }\n}\n"},
     [("StandardBuilder.new", "calls", "src/standard.rs:Config.default"), ("JsonBuilder.new", "calls", "src/json.rs:Config.default")],
     [("StandardBuilder.new", "calls", "src/json.rs:Config.default"), ("JsonBuilder.new", "calls", "src/standard.rs:Config.default")])
case("rust", "trait-method-through-a-library-type", "`io::Error::message(m)` is the method a trait of the project gives the library's type",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "use std::io;\n\npub trait SinkError: Sized {\n    fn error_message(message: String) -> Self;\n}\n\nimpl SinkError for io::Error {\n    fn error_message(message: String) -> io::Error {\n        io::Error::new(io::ErrorKind::Other, message)\n    }\n}\n\npub fn fail(msg: &str) -> Result<(), io::Error> {\n    Err(io::Error::error_message(msg.to_string()))\n}\n"},
     [("fail", "calls", "error_message")])
case("rust", "impl-trait-type-arguments", "the types a trait is written with in an `impl` are uses of the implementing type",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub struct Config {\n    pub n: u32,\n}\n\npub struct Agent {\n    pub n: u32,\n}\n\nimpl From<Config> for Agent {\n    fn from(c: Config) -> Agent {\n        Agent { n: c.n }\n    }\n}\n"},
     [("Agent", "references", "Config")])
case("go", "every-result-type", "each result of a function names its type",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\ntype Directive int\n\ntype Completion string\n\nfunc NoFile(args []string) ([]Completion, Directive) {\n\treturn nil, 0\n}\n"},
     [("NoFile", "references", "Completion"), ("NoFile", "references", "Directive")])
case("go", "method-value-argument", "a method value handed to a call is a function handed over",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\nimport \"time\"\n\ntype Breaker struct {\n\ttimer *time.Timer\n}\n\nfunc (b *Breaker) onReset() {}\n\nfunc (b *Breaker) open() {\n\tb.timer = time.AfterFunc(time.Second, b.onReset)\n}\n"},
     [("Breaker.open", "indirect_call", "Breaker.onReset")])

# The places a type is written that the reader never looked at, listed from the
# grammars (`positions.py`, engine 0.5.62).
found_by("grammar")
case("go", "defined-type-names-its-type", "what a defined type is defined as is a use of it",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\ntype Opt struct{}\n\ntype Handler struct{}\n\ntype Key Opt\n\ntype Mid []func(h Handler) Handler\n"},
     [("Key", "references", "Opt"), ("Mid", "references", "Handler")])
case("go", "constant-type", "the type a constant is declared with is a use of it",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\ntype Key int\n\nconst RequestID Key = 0\n"},
     [("RequestID", "references", "Key")])
case("go", "function-literal-signature", "a function literal's parameters and result name their types",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\ntype Handler struct{}\n\ntype Opt struct{}\n\nfunc Run() {\n\tf := func(next Handler) Opt {\n\t\treturn Opt{}\n\t}\n\t_ = f\n}\n"},
     [("Run", "references", "Handler"), ("Run", "references", "Opt")])
case("go", "literal-element-types", "a slice or map literal names the types it is made of",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\ntype Opt struct{}\n\ntype Handler struct{}\n\nfunc Run() int {\n\ta := []*Opt{}\n\tb := map[string]Handler{}\n\treturn len(a) + len(b)\n}\n"},
     [("Run", "references", "Opt"), ("Run", "references", "Handler")])
case("go", "make-type-argument", "the type handed to `make` is a use of it",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\ntype Enc func() int\n\ntype Handler struct{}\n\nfunc Run() int {\n\tm := make(map[string]Enc)\n\tc := make(chan Handler, 1)\n\treturn len(m) + len(c)\n}\n"},
     [("Run", "references", "Enc"), ("Run", "references", "Handler")])
case("c", "local-declaration-type", "the type a local is declared with is a use of it",
     CC, {"a.c": "typedef struct {\n    int n;\n} buffer;\n\nint run(void) {\n    buffer p = { 0 };\n    buffer *q = &p;\n    return q->n;\n}\n"},
     [("run", "references", "buffer")])
case("c", "typedef-names-its-type", "a `typedef` of a named type is a use of that type",
     CC, {"a.c": "typedef struct item {\n    int v;\n} item;\n\ntypedef item *item_ptr;\n\nint run(item_ptr p) {\n    return p->v;\n}\n"},
     [("item_ptr", "references", "item")])
case("java", "throws-clause", "the exceptions a method declares it throws are uses of them",
     JAVA, {"A.java": "class Err extends Exception {\n}\n\nclass A {\n    void run() throws Err {\n        throw new Err();\n    }\n\n    void quiet() throws Err {\n    }\n}\n"},
     [("A.quiet", "references", "Err")])
case("java", "class-literal", "`T.class` is a use of T",
     JAVA, {"A.java": "class El {\n}\n\nclass A {\n    Object run() {\n        return El.class;\n    }\n}\n"},
     [("A.run", "references", "El")])
case("java", "for-each-and-varargs-types", "the type of a for-each variable and of a varargs parameter are uses of it",
     JAVA, {"A.java": "class El {\n}\n\nclass Many {\n}\n\nclass A {\n    int each(java.util.List<Object> xs) {\n        int n = 0;\n        for (Object o : xs) {\n            n++;\n        }\n        for (El e : new java.util.ArrayList<El>()) {\n            n++;\n        }\n        return n;\n    }\n\n    int spread(Many... many) {\n        return many.length;\n    }\n}\n"},
     [("A.each", "references", "El"), ("A.spread", "references", "Many")])
case("java", "interface-constant-type", "the type of a constant an interface declares is a use of it",
     JAVA, {"A.java": "class Filter {\n}\n\ninterface Filters {\n    Filter ALL = null;\n}\n"},
     [("Filters", "references", "Filter")])
case("swift", "typed-throws", "`throws(E)` is a use of E",
     SW, {"a.swift": "struct ParseError: Error {\n}\n\nfunc run() throws(ParseError) -> Int {\n    return 1\n}\n"},
     [("run", "references", "ParseError")])
case("swift", "protocol-property-type", "the type of a property a protocol requires is a use of it",
     SW, {"a.swift": "protocol Handler {\n}\n\nstruct Cred {\n}\n\nprotocol Delegate {\n    var handler: (any Handler)? { get }\n    var cred: Cred { get }\n}\n"},
     [("var handler", "references", "Handler"), ("var cred", "references", "Cred")])
case("swift", "metatype-of-existential", "`(any P).self` is a use of P",
     SW, {"a.swift": "protocol Handler {\n}\n\nfunc run() -> Any {\n    return (any Handler).self\n}\n"},
     [("run", "references", "Handler")])
case("swift", "generic-construction", "`Box<T>(…)` constructs Box",
     SW, {"a.swift": "struct Cred {\n}\n\nstruct Box<T> {\n    init(value: Int) {\n    }\n}\n\nfunc run() -> Box<Cred> {\n    return Box<Cred>(value: 1)\n}\n"},
     [("run", "calls", "Box")])
case("csharp", "array-creation-type", "`new T[n]` is a use of T",
     CS, {"A.cs": "namespace App\n{\n    public class Err\n    {\n    }\n\n    public class C\n    {\n        public object Make()\n        {\n            return new Err[2];\n        }\n    }\n}\n"},
     [("C.Make", "references", "Err")])
case("scala", "declared-value-type", "the type a `val` or `var` is declared with is a use of it",
     SC, {"src/app/A.scala": "package app\n\nclass Compress\n\nclass Other\n\nclass T {\n  val c: Compress = null\n\n  def run(): Any = {\n    val l: Other = null\n    l\n  }\n}\n"},
     [("T", "references", "Compress"), ("T.run", "references", "Other")])
case("scala", "new-through-its-holder", "`new Outer.Inner(…)` constructs Inner",
     SC, {"src/app/A.scala": "package app\n\nobject Outer {\n  class Inner(s: String)\n}\n\nobject Main {\n  def run(): Any = new Outer.Inner(\"x\")\n}\n"},
     [("Main.run", "calls", "Outer.Inner")])
case("rust", "struct-pattern-type", "the struct a pattern takes apart is a use of it",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub struct Limited {\n    pub n: u32,\n}\n\npub enum Mode {\n    L(Limited),\n    N,\n}\n\npub fn run(m: Mode) -> u32 {\n    match m {\n        Mode::L(Limited { n }) => n,\n        Mode::N => 0,\n    }\n}\n"},
     [("run", "references", "Limited")])
case("php", "closure-signature", "an anonymous function's parameters and result name their types",
     PHP, {"src/A.php": "<?php\n\nnamespace App;\n\nclass Cookie\n{\n}\n\nclass Result\n{\n}\n\nclass A\n{\n    public function run()\n    {\n        return static function (Cookie $c): Result {\n            return new Result();\n        };\n    }\n}\n"},
     [("A.run", "references", "Cookie")])

case("cpp", "brace-construction", "`T{…}` constructs T",
     CPP, {"a.cpp": "struct Auth {\n    const char* user;\n    const char* pass;\n};\n\nstruct Pool {\n};\n\nint send(Auth a) {\n    return a.user != 0;\n}\n\nint run() {\n    Pool p = Pool{};\n    (void)p;\n    return send(Auth{\"user\", \"pw\"});\n}\n"},
     [("run", "calls", "Auth"), ("run", "calls", "Pool")])
case("cpp", "member-declaration-result", "the result type of a member function declared in its class is a use of it",
     CPP, {"a.cpp": "class Pool {\n};\n\nclass Slice {\n};\n\nstruct Handle {\n};\n\nclass Cache {\n public:\n    static Pool* Get(Slice& s);\n    virtual Handle* Lookup(const Slice& key) = 0;\n};\n"},
     [("Cache.Get", "references", "Pool"), ("Cache.Lookup", "references", "Handle")])
case("cpp", "explicit-template-argument", "a type written as a template argument of a call is a use of it",
     CPP, {"a.cpp": "struct Slice {\n};\n\nstruct Pool {\n};\n\ntemplate <class T>\nint setup(Pool& p) {\n    return sizeof(T) > 0;\n}\n\nint run(Pool& p) {\n    return setup<Slice>(p);\n}\n"},
     [("run", "references", "Slice")])
case("cpp", "trailing-result-and-friend", "a trailing result type and a `friend` name their types",
     CPP, {"a.cpp": "class Pool {\n};\n\nclass Guard {\n};\n\nclass Cache {\n    friend class Guard;\n};\n\ninline auto make() -> Pool {\n    return Pool();\n}\n"},
     [("make", "references", "Pool"), ("Cache", "references", "Guard")])
case("scala", "new-generic-through-package", "`new p.T[A](…)` constructs T",
     SC, {"src/scopt/A.scala": "package scopt\n\nclass Config\n\nclass OptionParser[C](name: String)\n\nobject Main {\n  def run(): Any = new scopt.OptionParser[Config](\"scopt\")\n}\n"},
     [("Main.run", "calls", "OptionParser"), ("Main.run", "references", "Config")])
case("typescript", "function-type-parameter-types", "the parameters of a function type name their types, not their names",
     TS, {"a.ts": "export interface Ctx {\n  id: number;\n}\n\nexport interface Err {\n  message: string;\n}\n\nexport interface Hooks {\n  preprocess: (arg: unknown, ctx: Ctx) => unknown;\n}\n\nexport function run(cb: (err: Err) => void): void {\n  cb({ message: \"x\" });\n}\n\nexport const err = 1;\n"},
     [("Hooks", "references", "Ctx"), ("run", "references", "Err")], [("run", "references", "a.ts:err")])

# The places a value name is written that the reader did not record
# (`positions.py --values`, engine 0.5.63).
found_by("grammar")
case("typescript", "member-of-a-local", "a member read or written through a local or a parameter is the member of the object it holds",
     TS, {"a.ts": "export class Request {\n  retryCount = 0;\n  headers: string[] = [];\n}\n\nexport function make(first: Request): number {\n  const request = first;\n  request.retryCount = 1;\n  return first.headers.length;\n}\n"},
     [("make", "writes", "Request.retryCount"), ("make", "reads", "Request.headers")])
case("python", "member-of-a-parameter", "a member read through a typed parameter is the member of its type",
     PY, {"a.py": "class Ctx:\n    command_path = \"\"\n\n\ndef run(ctx: Ctx) -> str:\n    return \" \".join([ctx.command_path])\n"},
     [("run", "reads", "Ctx.command_path")])
case("swift", "implicit-member-beside-a-value", "`.case` compared with, assigned to or switched on a value is a case of that value's type",
     SW, {"a.swift": "enum Section {\n    case body\n    case header\n}\n\nenum Other {\n    case body\n}\n\nstruct Holder {\n    var section: Section\n}\n\nfunc compare(h: Holder) -> Bool {\n    return h.section == .header\n}\n\nfunc branch(h: Holder) -> Int {\n    switch h.section {\n    case .body:\n        return 1\n    default:\n        return 0\n    }\n}\n"},
     [("compare", "references", "Section"), ("branch", "references", "Section")],
     [("compare", "references", "Other"), ("branch", "references", "Other")])
case("rust", "macro-argument-member-read", "a field read in a macro's arguments is a read of the field",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub struct Args {\n    pub crlf: bool,\n}\n\npub fn check(args: &Args) {\n    assert_eq!(false, args.crlf);\n}\n"},
     [("check", "reads", "Args.crlf")])
case("scala", "generic-call", "`f[T](…)` calls f",
     SC, {"src/app/A.scala": "package app\n\nclass Parser {\n  def opt[A](name: String): Int = 1\n\n  def run(): Int = opt[String](\"foo\")\n}\n"},
     [("Parser.run", "calls", "Parser.opt")])

# Phase 2 of docs/rule-sources.md: C#'s binding rules read from the C# standard
# (dotnet/csharpstandard, `standard/`) and Roslyn's binder (dotnet/roslyn,
# `src/Compilers/CSharp/Portable/Binder/`), step by step. Every rule the two
# state is in `benchmarks/rule_inventory.py` with the case that holds it, or the
# reason the engine does not follow it.
found_by("source")
RS_CS = "dotnet/roslyn@da9878a16b49:src/Compilers/CSharp/Portable/Binder/"
STD_CS = "dotnet/csharpstandard@dce7bdbaa8b5:standard/"
case("csharp", "method-type-parameter-first", "in a generic method a name of its type parameters is that parameter, not a type of the namespace",
     CS, {"A.cs": "namespace App\n{\n    public class T\n    {\n    }\n\n    public class C\n    {\n        public int M<T>(T x)\n        {\n            return 1;\n        }\n\n        public int N(T x)\n        {\n            return 2;\n        }\n    }\n}\n"},
     [("C.N", "references", "App.T")], [("C.M", "references", "App.T")],
     source=STD_CS + "basic-concepts.md §7.8.1 (R₀, first step); " + RS_CS + "Binder_Lookup.cs LookupSymbolsInSingleBinder (InMethodBinder: type parameters)")
case("csharp", "nested-type-of-a-base", "a nested type of a base class is named without its outer type in the derived class",
     CS, {"A.cs": "namespace App\n{\n    public class Inner\n    {\n    }\n\n    public class Base\n    {\n        public class Inner\n        {\n        }\n    }\n\n    public class Derived : Base\n    {\n        public Inner Make()\n        {\n            return null;\n        }\n    }\n}\n"},
     [("Derived.Make", "references", "Base.Inner")], [("Derived.Make", "references", "A.cs:App.Inner")],
     source=STD_CS + "basic-concepts.md §7.8.1 (R₀: T or any of its base types contain a nested accessible type); " + RS_CS + "Binder_Lookup.cs LookupMembersInClass")
case("csharp", "namespace-type-before-using", "a type of the namespace the use is in comes before one a `using` imports",
     CS, {"Lib.cs": "namespace Lib\n{\n    public class Repo\n    {\n        public int Find() { return 1; }\n    }\n}\n",
          "A.cs": "using Lib;\n\nnamespace App\n{\n    public class Repo\n    {\n        public int Find() { return 2; }\n    }\n\n    public class C\n    {\n        public int M(Repo r)\n        {\n            return r.Find();\n        }\n    }\n}\n"},
     [("C.M", "calls", "App.Repo.Find")], [("C.M", "calls", "Lib.Repo.Find")],
     source=STD_CS + "basic-concepts.md §7.8.1 (for each namespace N: a type of N before the using directives); " + RS_CS + "InContainerBinder.cs LookupSymbolsInSingleBinder")
case("csharp", "using-alias-before-using-namespace", "an alias a `using` declares comes before a type of the same name another `using` imports",
     CS, {"Lib.cs": "namespace Lib\n{\n    public class Repo\n    {\n        public int Find() { return 1; }\n    }\n}\n",
          "Other.cs": "namespace Other\n{\n    public class Store\n    {\n        public int Find() { return 2; }\n    }\n\n    public class Repo\n    {\n        public int Find() { return 3; }\n    }\n}\n",
          "A.cs": "namespace App\n{\n    using Repo = Other.Store;\n    using Lib;\n\n    public class C\n    {\n        public int M(Repo r)\n        {\n            return r.Find();\n        }\n    }\n}\n"},
     [("C.M", "calls", "Other.Store.Find")], [("C.M", "calls", "Lib.Repo.Find"), ("C.M", "calls", "Other.Repo.Find")],
     source=STD_CS + "basic-concepts.md §7.8.1 (a using_alias_directive before the using_namespace_directives); " + RS_CS + "WithExternAndUsingAliasesBinder.cs, WithUsingNamespacesAndTypesBinder.cs")
case("csharp", "using-inside-a-namespace-block", "a `using` written inside a namespace block holds only inside it",
     CS, {"Lib.cs": "namespace Lib\n{\n    public class Repo\n    {\n        public int Find() { return 1; }\n    }\n}\n",
          "A.cs": "namespace App\n{\n    using Lib;\n\n    public class A\n    {\n        public int M(Repo r)\n        {\n            return r.Find();\n        }\n    }\n}\n\nnamespace Elsewhere\n{\n    public class Repo\n    {\n        public int Find() { return 2; }\n    }\n\n    public class B\n    {\n        public int M(Repo r)\n        {\n            return r.Find();\n        }\n    }\n}\n"},
     [("A.M", "calls", "Lib.Repo.Find"), ("B.M", "calls", "Elsewhere.Repo.Find")], [("B.M", "calls", "Lib.Repo.Find")],
     source=STD_CS + "namespaces.md §14.5.1 (the scope of a using directive is the namespace body that contains it)")
case("csharp", "local-hides-member", "a local or a parameter of a name hides the member of that name",
     CS, {"A.cs": "namespace App\n{\n    public class C\n    {\n        public int Count;\n\n        public int Get(int Count)\n        {\n            return Count;\n        }\n\n        public int Own()\n        {\n            return Count;\n        }\n    }\n}\n"},
     [("C.Own", "reads", "C.Count")], [("C.Get", "reads", "C.Count")],
     source=STD_CS + "expressions.md §12.8.4 (first step: a local variable, parameter or constant); " + RS_CS + "LocalScopeBinder.cs")
case("csharp", "derived-member-hides-base", "a member of the derived class hides one of its name in the base",
     CS, {"A.cs": "namespace App\n{\n    public class Base\n    {\n        public int Size() { return 1; }\n    }\n\n    public class Derived : Base\n    {\n        public new int Size() { return 2; }\n\n        public int Use() { return Size(); }\n    }\n\n    public class Client\n    {\n        public int M(Derived d) { return d.Size(); }\n\n        public int N(Base b) { return b.Size(); }\n    }\n}\n"},
     [("Derived.Use", "calls", "Derived.Size"), ("Client.M", "calls", "Derived.Size"), ("Client.N", "calls", "Base.Size")],
     [("Derived.Use", "calls", "Base.Size"), ("Client.M", "calls", "Base.Size"), ("Client.N", "calls", "Derived.Size")],
     source=STD_CS + "expressions.md §12.5.1 (members hidden by other members are removed); " + RS_CS + "Binder_Lookup.cs LookupMembersInClass")
case("csharp", "color-color", "`Color.White` where a property `Color Color` is in scope is the type's static member",
     CS, {"A.cs": "namespace App\n{\n    public class Color\n    {\n        public static Color White = new Color();\n\n        public Color Complement() { return this; }\n    }\n\n    public class Shape\n    {\n        public Color Color { get; set; }\n\n        public Color Paint()\n        {\n            return Color.White;\n        }\n\n        public Color Flip()\n        {\n            return Color.Complement();\n        }\n    }\n}\n"},
     [("Shape.Paint", "reads", "Color.White"), ("Shape.Flip", "calls", "Color.Complement")],
     source=STD_CS + "expressions.md §12.8.7.2 (identical simple names and type names); " + RS_CS + "Binder_Expressions.cs BindLeftOfPotentialColorColorMemberAccess")
case("csharp", "instance-method-before-extension", "an instance method that fits comes before an extension method of its name",
     CS, {"A.cs": "namespace App\n{\n    public class Repo\n    {\n        public int Find(int id) { return id; }\n    }\n\n    public static class RepoExtensions\n    {\n        public static int Find(this Repo r, int id) { return 0; }\n\n        public static int Count(this Repo r) { return 0; }\n    }\n\n    public class C\n    {\n        public int M(Repo r) { return r.Find(1) + r.Count(); }\n    }\n}\n"},
     [("C.M", "calls", "Repo.Find"), ("C.M", "calls", "RepoExtensions.Count")], [("C.M", "calls", "RepoExtensions.Find")],
     source=STD_CS + "expressions.md §12.8.10.3 (an extension only if normal processing finds no applicable method); " + RS_CS + "Binder_Invocation.cs BindInvocationExpression")
case("csharp", "closest-extension-namespace", "of extensions in sight, those of the closest enclosing namespace are the candidates",
     CS, {"A.cs": "namespace App\n{\n    public class Repo\n    {\n    }\n\n    public static class Outer\n    {\n        public static int Size(this Repo r) { return 1; }\n    }\n\n    namespace Inner\n    {\n        public static class Near\n        {\n            public static int Size(this Repo r) { return 2; }\n        }\n\n        public class C\n        {\n            public int M(Repo r) { return r.Size(); }\n        }\n    }\n}\n"},
     [("C.M", "calls", "Near.Size")], [("C.M", "calls", "Outer.Size")],
     source=STD_CS + "expressions.md §12.8.10.3 (starting with the closest enclosing namespace declaration); " + RS_CS + "Binder_Lookup.cs LookupAllExtensions")
case("csharp", "extension-needs-its-namespace", "an extension of a namespace neither around the use nor imported is not in sight",
     CS, {"Ext.cs": "namespace Far\n{\n    public static class RepoExtensions\n    {\n        public static int Size(this App.Repo r) { return 1; }\n    }\n}\n",
          "A.cs": "namespace App\n{\n    public class Repo\n    {\n        public int Size() { return 2; }\n    }\n\n    public class C\n    {\n        public int M(Repo r) { return r.Size(); }\n    }\n}\n"},
     [("C.M", "calls", "Repo.Size")], [("C.M", "calls", "RepoExtensions.Size")],
     source=STD_CS + "expressions.md §12.8.10.3 (candidate sets: the namespaces around and those their using directives import)")
case("csharp", "overload-exact-type", "of two overloads that take the argument, the one whose parameter is its type is called",
     CS, {"A.cs": "namespace App\n{\n    public class Log\n    {\n        public int Write(int n) { return 1; }\n\n        public int Write(object o) { return 2; }\n\n        public int Write(string s) { return 3; }\n    }\n\n    public class C\n    {\n        public int M(Log l) { return l.Write(\"x\"); }\n    }\n}\n"},
     [("C.M", "calls", "A.cs:App.Log.Write@9")], [("C.M", "calls", "A.cs:App.Log.Write@5"), ("C.M", "calls", "A.cs:App.Log.Write@7")],
     source=STD_CS + "expressions.md §12.6.4.3 (better function member), §12.6.4.5 (better conversion from expression); " + RS_CS + "Semantics/OverloadResolution/OverloadResolution.cs BetterFunctionMember")
case("csharp", "overload-without-defaults", "an overload all of whose parameters have arguments is better than one that needs a default",
     CS, {"A.cs": "namespace App\n{\n    public class Log\n    {\n        public int Write(int n) { return 1; }\n\n        public int Write(int n, int level = 0) { return 2; }\n    }\n\n    public class C\n    {\n        public int M(Log l) { return l.Write(1); }\n    }\n}\n"},
     [("C.M", "calls", "A.cs:App.Log.Write@5")], [("C.M", "calls", "A.cs:App.Log.Write@7")],
     source=STD_CS + "expressions.md §12.6.4.3 (default arguments substituted for an optional parameter make a member worse)")
case("csharp", "overload-normal-before-params", "an overload applicable in its normal form is better than a `params` one in its expanded form",
     CS, {"A.cs": "namespace App\n{\n    public class Log\n    {\n        public int Write(int a, int b) { return 1; }\n\n        public int Write(params int[] all) { return 2; }\n    }\n\n    public class C\n    {\n        public int M(Log l) { return l.Write(1, 2); }\n    }\n}\n"},
     [("C.M", "calls", "A.cs:App.Log.Write@5")], [("C.M", "calls", "A.cs:App.Log.Write@7")],
     source=STD_CS + "expressions.md §12.6.4.3 (if Mp is applicable in its normal form and Mq has a params array and is applicable only in its expanded form, Mp is better)")
case("csharp", "overload-by-argument-name", "a named argument picks the overload that has a parameter of that name",
     CS, {"A.cs": "namespace App\n{\n    public class Log\n    {\n        public int Write(int count) { return 1; }\n\n        public int Write(string text) { return 2; }\n    }\n\n    public class C\n    {\n        public int M(Log l, object v) { return l.Write(text: v.ToString()); }\n    }\n}\n"},
     [("C.M", "calls", "A.cs:App.Log.Write@7")], [("C.M", "calls", "A.cs:App.Log.Write@5")],
     source=STD_CS + "expressions.md §12.6.4.2 (applicable function member: each named argument corresponds to a parameter of that name)")
case("csharp", "overload-non-generic-first", "of two candidates with the same parameter types, the one that is not generic is better",
     CS, {"A.cs": "namespace App\n{\n    public class Log\n    {\n        public int Write<T>(T value) { return 1; }\n\n        public int Write(string text) { return 2; }\n    }\n\n    public class C\n    {\n        public int M(Log l) { return l.Write(\"x\"); }\n    }\n}\n"},
     [("C.M", "calls", "A.cs:App.Log.Write@7")], [("C.M", "calls", "A.cs:App.Log.Write@5")],
     source=STD_CS + "expressions.md §12.6.4.3 (if MP is a non-generic method and MQ is a generic method, MP is better); " + RS_CS + "OverloadResolution.cs BetterFunctionMember")
case("csharp", "inherited-from-a-grandparent", "a member of a base of the base class is a member of the class",
     CS, {"A.cs": "namespace App\n{\n    public class Root\n    {\n        public int Id() { return 1; }\n    }\n\n    public class Mid : Root\n    {\n    }\n\n    public class Leaf : Mid\n    {\n        public int Use() { return Id(); }\n    }\n\n    public class C\n    {\n        public int M(Leaf l) { return l.Id(); }\n    }\n}\n"},
     [("Leaf.Use", "calls", "Root.Id"), ("C.M", "calls", "Root.Id")],
     source=STD_CS + "expressions.md §12.5.1 (all accessible members named N in T, including inherited members), §12.5.2")
case("csharp", "qualified-nested-type", "`Outer.Inner` names the nested type",
     CS, {"A.cs": "namespace App\n{\n    public class Inner\n    {\n    }\n\n    public class Outer\n    {\n        public class Inner\n        {\n            public int Go() { return 1; }\n        }\n    }\n\n    public class C\n    {\n        public int M(Outer.Inner i) { return i.Go(); }\n    }\n}\n"},
     [("C.M", "references", "Outer.Inner"), ("C.M", "calls", "Outer.Inner.Go")], [("C.M", "references", "A.cs:App.Inner")],
     source=STD_CS + "basic-concepts.md §7.8.1 (trailing part: a nested accessible type of Rₚ); " + RS_CS + "Binder_Symbols.cs BindQualifiedName")

# Phase 2 for Rust: the rules the Rust Reference states (rust-lang/reference,
# `src/names/`, `src/items/use-declarations.md`, `src/expressions/method-call-expr.md`,
# each cited by its rule identifier `r[...]`) and rustc's resolver
# (`compiler/rustc_resolve`) and method probe (`compiler/rustc_hir_typeck/src/method/probe.rs`)
# where the Reference leaves primary and type-relative resolution to the compiler.
found_by("source")
REF_RS = "rust-lang/reference@c9cc32825b03:src/"
RC_RS = "rust-lang/rust@c7d5ba59d8e1:compiler/"
CARGO21 = CARGO + "edition = \"2021\"\n"
case("rust", "item-in-a-block", "a function declared in a block is a name of that block, before the module's of its name",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub fn helper() -> i32 {\n    1\n}\n\npub fn outer() -> i32 {\n    fn helper() -> i32 {\n        2\n    }\n    helper()\n}\n\npub fn other() -> i32 {\n    helper()\n}\n"},
     [("outer", "calls", "outer.helper"), ("other", "calls", "src/lib.rs:helper")], [("outer", "calls", "src/lib.rs:helper")],
     source=REF_RS + "names/scopes.md r[names.scopes.items.statement]")
case("rust", "outer-module-items-not-in-scope", "the items of a module around are no names of a nested module: only its own and what it imports are",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub fn helper() -> i32 {\n    1\n}\n\npub mod inner {\n    pub fn helper() -> i32 {\n        2\n    }\n\n    pub mod deeper {\n        use super::helper;\n\n        pub fn run() -> i32 {\n            helper()\n        }\n    }\n}\n"},
     [("inner.deeper.run", "calls", "inner.helper")], [("inner.deeper.run", "calls", "src/lib.rs:helper")],
     source=REF_RS + "names/scopes.md r[names.scopes.items.nested-modules]")
case("rust", "let-shadows-from-where-it-is", "a `let` binding shadows a function of its name only after it: a call before it is the function",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub fn foo() -> i32 {\n    1\n}\n\npub fn before() -> i32 {\n    let a = foo();\n    let foo = || 2;\n    a + foo()\n}\n\npub fn after() -> i32 {\n    let foo = || 2;\n    foo()\n}\n"},
     [("before", "calls", "foo")], [("after", "calls", "foo")],
     source=REF_RS + "names/scopes.md r[names.scopes.pattern-bindings.let], r[names.scopes.pattern-bindings.shadow]")
case("rust", "associated-items-are-not-scoped", "in an `impl`, a bare name is never an associated function of the type: that takes `Self::`",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub fn make() -> i32 {\n    1\n}\n\npub struct S;\n\nimpl S {\n    pub fn make() -> i32 {\n        2\n    }\n\n    pub fn bare() -> i32 {\n        make()\n    }\n\n    pub fn own() -> i32 {\n        Self::make()\n    }\n}\n"},
     [("S.bare", "calls", "make"), ("S.own", "calls", "S.make")], [("S.bare", "calls", "S.make")],
     source=REF_RS + "names/scopes.md r[names.scopes.associated-items.scope]")
case("rust", "generic-parameter-hides-a-type", "a generic type parameter is the name in the item it is declared on, before a type of the module",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub struct Config;\n\npub fn generic<Config: Clone>(c: Config) -> Config {\n    c.clone()\n}\n\npub fn plain(c: Config) -> Config {\n    c\n}\n"},
     [("plain", "references", "Config")], [("generic", "references", "Config")],
     source=REF_RS + "names/scopes.md r[names.scopes.generic-parameters.param-list]")
case("rust", "glob-shadowed-by-an-item", "a name the module declares shadows the one a glob import brings; the glob still brings the others",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub mod other;\n\nuse other::*;\n\npub struct Foo;\n\npub fn make() -> usize {\n    let _a = Foo;\n    let _b = Bar;\n    0\n}\n",
          "src/other.rs": "pub struct Foo;\npub struct Bar;\n"},
     [("make", "references", "src/lib.rs:Foo"), ("make", "references", "Bar")], [("make", "references", "src/other.rs:Foo")],
     source=REF_RS + "items/use-declarations.md r[items.use.glob.shadowing]")
case("rust", "named-import-shadows-a-glob", "a name imported by name shadows the one a glob import brings",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub mod a;\npub mod b;\n\nuse a::*;\nuse b::Thing;\n\npub fn make() -> i32 {\n    Thing::new()\n}\n",
          "src/a.rs": "pub struct Thing;\n\nimpl Thing {\n    pub fn new() -> i32 {\n        1\n    }\n}\n",
          "src/b.rs": "pub struct Thing;\n\nimpl Thing {\n    pub fn new() -> i32 {\n        2\n    }\n}\n"},
     [("make", "calls", "src/b.rs:Thing.new")], [("make", "calls", "src/a.rs:Thing.new")],
     source=REF_RS + "items/use-declarations.md r[items.use.glob.shadowing]")
case("rust", "inner-use-shadows-outer", "a `use` in a block shadows a `use` of the module of the same name",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub mod m1;\npub mod m2;\n\nuse m1::make;\n\npub fn outer() -> i32 {\n    make()\n}\n\npub fn inner() -> i32 {\n    use m2::make;\n    make()\n}\n",
          "src/m1.rs": "pub fn make() -> i32 {\n    1\n}\n", "src/m2.rs": "pub fn make() -> i32 {\n    2\n}\n"},
     [("outer", "calls", "src/m1.rs:make"), ("inner", "calls", "src/m2.rs:make")], [("inner", "calls", "src/m1.rs:make")],
     source=REF_RS + "names/name-resolution.md r[names.resolution.expansion.imports.shadowing]")
case("rust", "module-and-function-namespaces", "a module and a function of one name live in different namespaces: `log::f()` is the module's, `log()` the function",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub mod log {\n    pub fn write() -> i32 {\n        1\n    }\n}\n\npub fn log() -> i32 {\n    2\n}\n\npub fn run() -> i32 {\n    log::write() + log()\n}\n"},
     [("run", "calls", "log.write"), ("run", "calls", "src/lib.rs:log")],
     source=REF_RS + "names/namespaces.md r[names.namespaces.kinds]")
case("rust", "uniform-paths", "since 2018 a `use` path starts at a name in scope: a child module of the module that writes it before one of the crate root",
     RS, {"Cargo.toml": CARGO21, "src/lib.rs": "pub mod m {\n    pub fn f() -> i32 {\n        1\n    }\n}\n\npub mod a {\n    pub mod m {\n        pub fn f() -> i32 {\n            2\n        }\n    }\n\n    use m::f;\n\n    pub fn run() -> i32 {\n        f()\n    }\n}\n"},
     [("a.run", "calls", "a.m.f")], [("a.run", "calls", "src/lib.rs:m.f")],
     source=REF_RS + "items/use-declarations.md r[items.use.path.edition2018]", since="2018")
case("rust", "inherent-method-first", "a method of the type's own `impl` is found before a trait's method of the name",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub struct Foo;\n\npub trait Run {\n    fn run(&self) -> i32;\n}\n\nimpl Foo {\n    pub fn run(&self) -> i32 {\n        1\n    }\n}\n\nimpl Run for Foo {\n    fn run(&self) -> i32 {\n        2\n    }\n}\n\npub fn go(f: &Foo) -> i32 {\n    f.run()\n}\n"},
     [("go", "calls", "src/lib.rs:Foo.run@8")], [("go", "calls", "src/lib.rs:Foo.run@14"), ("go", "calls", "Run.run")],
     source=REF_RS + "expressions/method-call-expr.md r[expr.method.candidate-search]; " + RC_RS + "rustc_hir_typeck/src/method/probe.rs assemble_inherent_candidates")
case("rust", "self-in-a-trait", "`Self::f()` in a trait's default method is the trait's associated function",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub fn helper() -> i32 {\n    0\n}\n\npub trait Tr {\n    fn helper() -> i32;\n\n    fn run() -> i32 {\n        Self::helper()\n    }\n}\n"},
     [("Tr.run", "calls", "Tr.helper")], [("Tr.run", "calls", "src/lib.rs:helper")],
     source=REF_RS + "names/scopes.md r[names.scopes.self.def-scope]")
case("rust", "self-type-in-a-struct", "`Self` in a struct's own fields is the struct",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub struct Node {\n    pub next: Option<Box<Self>>,\n    pub value: i32,\n}\n\nimpl Node {\n    pub fn value(&self) -> i32 {\n        self.value\n    }\n}\n\npub fn second(n: &Node) -> i32 {\n    n.next.as_ref().unwrap().value()\n}\n"},
     [("second", "calls", "Node.value")],
     source=REF_RS + "names/scopes.md r[names.scopes.self.def-scope]")
case("rust", "tuple-struct-constructor", "a tuple struct's name called is its constructor, in the value namespace",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub struct Meters(pub i32);\n\npub fn make() -> Meters {\n    Meters(3)\n}\n"},
     [("make", "calls", "Meters")],
     source=REF_RS + "names/namespaces.md r[names.namespaces.kinds] (struct constructors)")
case("rust", "match-arm-binding", "a binding of a `match` arm is a name of the arm: it hides a function of its name there only",
     RS, {"Cargo.toml": CARGO, "src/lib.rs": "pub fn size() -> i32 {\n    1\n}\n\npub fn pick(o: Option<i32>) -> i32 {\n    match o {\n        Some(size) => size,\n        None => size(),\n    }\n}\n\npub fn shadowed(o: Option<i32>) -> i32 {\n    match o {\n        Some(size) => size,\n        None => 0,\n    }\n}\n"},
     [("pick", "calls", "size")], [("shadowed", "calls", "size"), ("shadowed", "references", "size")],
     source=REF_RS + "names/scopes.md r[names.scopes.pattern-bindings.match-arm]")

# Phase 2 for Go: the Go specification (golang/go, `doc/go_spec.html`: 'Blocks',
# 'Declarations and scope', 'Selectors', 'Import declarations', 'Method expressions')
# and go/types (`src/go/types/lookup.go` LookupFieldOrMethod, `scope.go`
# LookupParent, `resolver.go` collectObjects).
found_by("source")
SPEC_GO = "golang/go@2dbf8b413af3:doc/go_spec.html#"
GT_GO = "golang/go@2dbf8b413af3:src/go/types/"
case("go", "dot-import", "`import . \"p\"` declares p's exported names in the file block: they are used bare",
     GO, {"go.mod": GOMOD, "util/u.go": "package util\n\nfunc Sin() int {\n\treturn 1\n}\n",
          "a.go": "package app\n\nimport . \"example.com/app/util\"\n\nfunc Run() int {\n\treturn Sin()\n}\n"},
     [("Run", "calls", "util/u.go:Sin")],
     source=SPEC_GO + "Import_declarations; " + GT_GO + "resolver.go collectObjects (dot-imports)")
case("go", "package-name-from-the-clause", "an import is named by the imported package's clause, not by its directory",
     GO, {"go.mod": GOMOD, "internal/v2/s.go": "package store\n\nfunc Get() int {\n\treturn 1\n}\n",
          "a.go": "package app\n\nimport \"example.com/app/internal/v2\"\n\nfunc Run() int {\n\treturn store.Get()\n}\n"},
     [("Run", "calls", "internal/v2/s.go:Get")],
     source=SPEC_GO + "Import_declarations (if the PackageName is omitted, it defaults to the identifier in the package clause)")
case("go", "shallowest-depth", "a selector is the field or method at the shallowest depth: a type's own method before an embedded type's",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\ntype Base struct{}\n\nfunc (b *Base) Close() int {\n\treturn 1\n}\n\ntype File struct {\n\tBase\n}\n\nfunc (f *File) Close() int {\n\treturn 2\n}\n\nfunc Run(f *File) int {\n\treturn f.Close()\n}\n"},
     [("Run", "calls", "File.Close")], [("Run", "calls", "Base.Close")],
     source=SPEC_GO + "Selectors (the field or method at the shallowest depth); " + GT_GO + "lookup.go LookupFieldOrMethod")
case("go", "shallower-embedding-wins", "of two embedded types, the method found at the lesser depth",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\ntype Inner struct{}\n\nfunc (Inner) Name() string {\n\treturn \"inner\"\n}\n\ntype Middle struct {\n\tInner\n}\n\ntype Named struct{}\n\nfunc (Named) Name() string {\n\treturn \"named\"\n}\n\ntype Outer struct {\n\tMiddle\n\tNamed\n}\n\nfunc Run(o Outer) string {\n\treturn o.Name()\n}\n"},
     [("Run", "calls", "Named.Name")], [("Run", "calls", "Inner.Name")],
     source=SPEC_GO + "Selectors (depth of f in T); " + GT_GO + "lookup.go lookupFieldOrMethodImpl")
case("go", "promoted-field", "a field of an embedded struct is a field of the outer one",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\ntype Meta struct {\n\tVersion int\n}\n\ntype Doc struct {\n\tMeta\n}\n\nfunc Version(d Doc) int {\n\treturn d.Version\n}\n"},
     [("Version", "reads", "Meta.Version")],
     source=SPEC_GO + "Selectors (t.y is t.T1.y)")
case("go", "block-redeclaration-ends-with-the-block", "a name declared in a block hides a package name only inside that block",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\nfunc helper() int {\n\treturn 1\n}\n\nfunc Run(ok bool) int {\n\tif ok {\n\t\thelper := 2\n\t\treturn helper\n\t}\n\treturn helper()\n}\n\nfunc Hidden() int {\n\thelper := 2\n\treturn helper\n}\n"},
     [("Run", "calls", "helper")], [("Hidden", "calls", "helper"), ("Hidden", "references", "helper")],
     source=SPEC_GO + "Declarations_and_scope (redeclared in an inner block)")
case("go", "switch-clause-is-a-block", "each clause of a `switch` is a block of its own: a name of one clause is none of the next",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\nfunc helper() int {\n\treturn 1\n}\n\nfunc Run(n int) int {\n\tswitch n {\n\tcase 1:\n\t\thelper := 2\n\t\treturn helper\n\tdefault:\n\t\treturn helper()\n\t}\n}\n"},
     [("Run", "calls", "helper")],
     source=SPEC_GO + "Blocks (each clause in a switch or select statement acts as an implicit block)")
case("go", "short-var-scope-starts-after", "a name declared by `:=` is in scope after the declaration: its own value names the outer one",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\nfunc helper() int {\n\treturn 1\n}\n\nfunc Run() int {\n\thelper := helper()\n\treturn helper\n}\n"},
     [("Run", "calls", "helper")],
     source=SPEC_GO + "Declarations_and_scope (begins at the end of the ShortVarDecl); " + GT_GO + "scope.go LookupParent (pos)")
case("go", "parameter-hides-a-function", "a parameter is a name of the function body: it hides a package function of its name",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\nfunc helper() int {\n\treturn 1\n}\n\nfunc Run(helper func() int) int {\n\treturn helper()\n}\n"},
     [], [("Run", "calls", "helper")],
     source=SPEC_GO + "Declarations_and_scope (a function parameter's scope is the function body)")
case("go", "local-type-hides-a-type", "a type declared in a function hides a package type of its name inside the function",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\ntype Config struct {\n\tName string\n}\n\nfunc Run() string {\n\ttype Config struct {\n\t\tName string\n\t}\n\tc := Config{Name: \"x\"}\n\treturn c.Name\n}\n\nfunc Other() string {\n\tc := Config{Name: \"y\"}\n\treturn c.Name\n}\n"},
     [("Other", "calls", "a.go:Config")], [("Run", "calls", "a.go:Config"), ("Run", "reads", "a.go:Config.Name")],
     source=SPEC_GO + "Declarations_and_scope (a type identifier declared inside a function)")
case("go", "method-is-no-package-name", "a method is no name of the package block: a bare call in a method is the package function",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\nfunc Stop() int {\n\treturn 1\n}\n\ntype Job struct{}\n\nfunc (j Job) Stop() int {\n\treturn 2\n}\n\nfunc (j Job) Run() int {\n\treturn Stop()\n}\n"},
     [("Job.Run", "calls", "a.go:Stop@3")], [("Job.Run", "calls", "Job.Stop")],
     source=SPEC_GO + "Declarations_and_scope (constant, type, variable or function (but not method) declared at top level)")
case("go", "type-parameter-hides-a-type", "a type parameter of a function hides a package type of its name in the function",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\ntype Config struct{}\n\nfunc Keep[Config any](c Config) Config {\n\treturn c\n}\n\nfunc Plain(c Config) Config {\n\treturn c\n}\n"},
     [("Plain", "references", "Config")], [("Keep", "references", "Config")],
     source=SPEC_GO + "Declarations_and_scope (a type parameter of a function)")
case("go", "method-expression", "`T.M` is a method expression: a use of T's method",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\ntype Counter struct{ n int }\n\nfunc (c Counter) Value() int {\n\treturn c.n\n}\n\nfunc Values(cs []Counter) []int {\n\tf := Counter.Value\n\tout := []int{}\n\tfor _, c := range cs {\n\t\tout = append(out, f(c))\n\t}\n\treturn out\n}\n"},
     [("Values", "references", "Counter.Value")],
     source=SPEC_GO + "Method_expressions")
case("go", "method-value", "`x.M` without a call is a method value: a use of the method",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\ntype Counter struct{ n int }\n\nfunc (c Counter) Value() int {\n\treturn c.n\n}\n\nfunc Later(c Counter) func() int {\n\treturn c.Value\n}\n"},
     [("Later", "references", "Counter.Value")],
     source=SPEC_GO + "Method_values")
case("go", "pointer-method-on-a-value", "a method of `*T` is called on an addressable `T`",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\ntype Counter struct{ n int }\n\nfunc (c *Counter) Inc() {\n\tc.n++\n}\n\nfunc Run() int {\n\tvar c Counter\n\tc.Inc()\n\treturn c.n\n}\n"},
     [("Run", "calls", "Counter.Inc")],
     source=SPEC_GO + "Calls (if x is addressable and &x's method set contains m, x.m() is shorthand for (&x).m())")

# Phase 2 for TypeScript / JavaScript: TypeScript has no current specification;
# the binder's name resolver is the source (microsoft/TypeScript, the Go port
# `tsc/internal/binder/nameresolver.go` `NameResolver.Resolve`, scope by scope),
# with module resolution from `tsc/internal/module` and ECMAScript's own rules
# for `var`, `catch` and named function expressions.
found_by("source")
NR_TS = "microsoft/TypeScript@01f1a759d92b:tsc/internal/binder/nameresolver.go "
MOD_TS = "microsoft/TypeScript@01f1a759d92b:tsc/internal/module/"
case("typescript", "class-members-are-not-names", "a class's members are no names inside its methods: a bare call is the module's function",
     TS, {"a.ts": "export function helper(): number {\n  return 1\n}\n\nexport class Job {\n  helper(): number {\n    return 2\n  }\n\n  run(): number {\n    return helper() + this.helper()\n  }\n}\n"},
     [("Job.run", "calls", "a.ts:helper"), ("Job.run", "calls", "Job.helper")],
     source=NR_TS + "Resolve (KindClassDeclaration: only the class's type parameters, `meaning & SymbolFlagsType`)")
case("typescript", "decorator-resolved-at-the-class", "a decorator is resolved where the class is declared, not among the method's parameters",
     TS, {"a.ts": "export function logged(target: any, context: any): any {\n  return target\n}\n\nexport class Job {\n  @logged\n  run(logged: number): number {\n    return logged\n  }\n}\n"},
     [("Job.run", "references", "logged")],
     source=NR_TS + "Resolve (KindDecorator: resolved at the class declaration)")
case("typescript", "catch-binding-is-the-blocks", "a `catch` binding is a name of the catch block only",
     TS, {"a.ts": "export function report(): number {\n  return 1\n}\n\nexport function run(): number {\n  try {\n    return 0\n  } catch (report) {\n    return report ? 2 : 3\n  } finally {\n    report()\n  }\n}\n"},
     [("run", "calls", "report")],
     source=NR_TS + "Resolve (KindCatchClause locals); ECMAScript 'The try Statement' (CatchClauseEvaluation)")
case("typescript", "declaration-merging", "two `interface` declarations of one name are one interface: a member of either",
     TS, {"a.ts": "export interface Repo {\n  find(): number\n}\n\nexport interface Repo {\n  save(): void\n}\n\nexport function run(r: Repo): number {\n  r.save()\n  return r.find()\n}\n"},
     [("run", "calls", "Repo.save"), ("run", "calls", "Repo.find")],
     source="TypeScript Handbook 'Declaration Merging' (merging interfaces); " + NR_TS + "lookup (one symbol per merged declaration)")
case("typescript", "tsconfig-paths", "an import through a `paths` mapping of `tsconfig.json` is the file it maps to",
     TS, {"tsconfig.json": "{\n  \"compilerOptions\": {\n    \"target\": \"ES2022\",\n    \"module\": \"ESNext\",\n    \"moduleResolution\": \"bundler\",\n    \"paths\": { \"@lib/*\": [\"./src/lib/*\"] },\n    \"skipLibCheck\": true,\n    \"noEmit\": true\n  },\n  \"include\": [\"src\"]\n}\n",
          "src/lib/util.ts": "export function f(): number {\n  return 1\n}\n",
          "src/a.ts": "import { f } from '@lib/util'\n\nexport function run(): number {\n  return f()\n}\n"},
     [("run", "calls", "f")],
     source=MOD_TS + "resolver.go tryLoadModuleUsingPathsIfEligible")
case("typescript", "package-json-types-entry", "an import of a directory is the file its `package.json` names",
     TS, {"shared/package.json": "{\n  \"name\": \"shared\",\n  \"types\": \"./src/main.ts\"\n}\n",
          "shared/src/main.ts": "export function f(): number {\n  return 1\n}\n",
          "a.ts": "import { f } from './shared'\n\nexport function run(): number {\n  return f()\n}\n"},
     [("run", "calls", "f")],
     source=MOD_TS + "resolver.go loadNodeModuleFromDirectoryWorker (the `types` field)")
case("javascript", "var-is-the-functions", "a `var` declared in a block is a name of the whole function",
     JS, {"a.js": "export function helper() {\n  return 1\n}\n\nexport function run(ok) {\n  if (ok) {\n    var helper = () => 2\n  }\n  return helper ? helper() : 0\n}\n"},
     [("run", "calls", "run.helper")], [("run", "calls", "a.js:helper")],
     source="ECMAScript 'VarDeclaredNames' (FunctionDeclarationInstantiation: var names are the function's)")
case("javascript", "named-function-expression", "the name of a function expression is a name of its own body only",
     JS, {"a.js": "export function fact(n) {\n  return n\n}\n\nexport const half = function fact(n) {\n  return n <= 1 ? 1 : fact(n - 1)\n}\n\nexport function run() {\n  return fact(3)\n}\n"},
     [("run", "calls", "a.js:fact@1")],
     source="ECMAScript 'Function Definitions' (InstantiateOrdinaryFunctionExpression: funcEnv binds the name)")
case("typescript", "type-meaning-skips-values", "where a type is written, a value of the name does not hide the type around it",
     TS, {"a.ts": "export interface Repo {\n  find(): number\n}\n\nexport function run(r: Repo): number {\n  const Repo = 1\n  const other: Repo = r\n  return other.find() + Repo\n}\n"},
     [("run", "calls", "Repo.find"), ("run", "references", "Repo")],
     source=NR_TS + "Resolve (meaning: a lookup for SymbolFlagsType passes over a value)")

# Phase 2 for Java: the JLS (6.3 scope, 6.4 shadowing, 7.5 imports, 15.12
# method invocation) and javac's resolver (openjdk/jdk,
# `src/jdk.compiler/share/classes/com/sun/tools/javac/comp/Resolve.java`:
# findVar, findFun, findType, mostSpecific).
found_by("source")
RS_JAVA = "openjdk/jdk@78bf06f425a9:src/jdk.compiler/share/classes/com/sun/tools/javac/comp/Resolve.java "
case("java", "inherited-before-enclosing", "a method a class inherits comes before one of the class around it (the innermost class with a method of the name)",
     JAVA, {"app/Base.java": "package app;\n\npublic class Base {\n    int size() {\n        return 1;\n    }\n}\n",
            "app/A.java": "package app;\n\npublic class A {\n    int size() {\n        return 2;\n    }\n\n    class Inner extends Base {\n        int run() {\n            return size();\n        }\n    }\n}\n"},
     [("A.Inner.run", "calls", "Base.size")], [("A.Inner.run", "calls", "A.size")],
     source="JLS 15.12.1 (the innermost type declaration of which the method is a member); " + RS_JAVA + "findFun")
case("java", "inherited-field-before-enclosing", "a field a class inherits comes before a field of the class around it",
     JAVA, {"app/Base.java": "package app;\n\npublic class Base {\n    protected Repo repo = new Repo();\n}\n",
            "app/Repo.java": "package app;\n\npublic class Repo {\n    int find() {\n        return 1;\n    }\n}\n",
            "app/Other.java": "package app;\n\npublic class Other {\n    int find() {\n        return 2;\n    }\n}\n",
            "app/A.java": "package app;\n\npublic class A {\n    Other repo = new Other();\n\n    class Inner extends Base {\n        int run() {\n            return repo.find();\n        }\n    }\n}\n"},
     [("A.Inner.run", "calls", "Repo.find")], [("A.Inner.run", "calls", "Other.find")],
     source="JLS 6.4.1, 15.11; " + RS_JAVA + "findVar (findField of the class before the outer environment)")
case("java", "own-method-before-static-import", "a method of the class shadows a method a static import brings",
     JAVA, {"lib/U.java": "package lib;\n\npublic class U {\n    public static int helper() {\n        return 1;\n    }\n}\n",
            "app/A.java": "package app;\n\nimport static lib.U.helper;\n\npublic class A {\n    static int helper() {\n        return 2;\n    }\n\n    int run() {\n        return helper();\n    }\n}\n"},
     [("A.run", "calls", "A.helper")], [("A.run", "calls", "U.helper")],
     source="JLS 6.4.1 (a method declared in a class shadows a single-static-import); " + RS_JAVA + "findFun (members before named imports)")
case("java", "single-type-import-before-package", "a single-type import shadows a type of the same package",
     JAVA, {"app/Repo.java": "package app;\n\npublic class Repo {\n    public int find() {\n        return 1;\n    }\n}\n",
            "lib/Repo.java": "package lib;\n\npublic class Repo {\n    public int find() {\n        return 2;\n    }\n}\n",
            "app/A.java": "package app;\n\nimport lib.Repo;\n\npublic class A {\n    int run(Repo r) {\n        return r.find();\n    }\n}\n"},
     [("A.run", "calls", "lib/Repo.java:Repo.find")], [("A.run", "calls", "app/Repo.java:Repo.find")],
     source="JLS 6.4.1 (a single-type-import shadows types of the same package); " + RS_JAVA + "findType (namedImportScope before the package)")
case("java", "package-before-on-demand-import", "a type of the same package shadows one an on-demand import brings",
     JAVA, {"app/Repo.java": "package app;\n\npublic class Repo {\n    public int find() {\n        return 1;\n    }\n}\n",
            "lib/Repo.java": "package lib;\n\npublic class Repo {\n    public int find() {\n        return 2;\n    }\n}\n",
            "app/A.java": "package app;\n\nimport lib.*;\n\npublic class A {\n    int run(Repo r) {\n        return r.find();\n    }\n}\n"},
     [("A.run", "calls", "app/Repo.java:Repo.find")], [("A.run", "calls", "lib/Repo.java:Repo.find")],
     source="JLS 7.5.2 (type-import-on-demand never shadows); " + RS_JAVA + "findType (the package before starImportScope)")
case("java", "member-type-before-import", "a member type of the class shadows an imported type of its name",
     JAVA, {"lib/Repo.java": "package lib;\n\npublic class Repo {\n    public int find() {\n        return 1;\n    }\n}\n",
            "app/A.java": "package app;\n\nimport lib.Repo;\n\npublic class A {\n    static class Repo {\n        int find() {\n            return 2;\n        }\n    }\n\n    int run(Repo r) {\n        return r.find();\n    }\n}\n"},
     [("A.run", "calls", "A.Repo.find")], [("A.run", "calls", "lib/Repo.java:Repo.find")],
     source="JLS 6.4.1; " + RS_JAVA + "findType (findMemberType of each enclosing class before the imports)")
case("java", "inherited-member-type", "a member type a class inherits is in scope in it",
     JAVA, {"app/Base.java": "package app;\n\npublic class Base {\n    public static class Node {\n        public int size() {\n            return 1;\n        }\n    }\n}\n",
            "app/A.java": "package app;\n\npublic class A extends Base {\n    int run(Node n) {\n        return n.size();\n    }\n}\n"},
     [("A.run", "calls", "Base.Node.size")],
     source="JLS 8.5 (member types are inherited); " + RS_JAVA + "findInheritedMemberType")
case("java", "qualified-this", "`Outer.this.m()` is the method of the class around",
     JAVA, {"app/A.java": "package app;\n\npublic class A {\n    int size() {\n        return 1;\n    }\n\n    class Inner {\n        int size() {\n            return 2;\n        }\n\n        int run() {\n            return A.this.size() + size();\n        }\n    }\n}\n"},
     [("A.Inner.run", "calls", "A.size"), ("A.Inner.run", "calls", "A.Inner.size")],
     source="JLS 15.8.4 (qualified this)")
case("java", "method-type-parameter-hides-a-class", "a generic method's type parameter hides a class of its name",
     JAVA, {"app/Repo.java": "package app;\n\npublic class Repo {\n}\n",
            "app/A.java": "package app;\n\npublic class A {\n    <Repo> Repo keep(Repo r) {\n        return r;\n    }\n\n    Repo plain(Repo r) {\n        return r;\n    }\n}\n"},
     [("A.plain", "references", "Repo")], [("A.keep", "references", "Repo")],
     source="JLS 6.4.1 (a type parameter shadows a type of the same name); " + RS_JAVA + "findType (type variables first)")
case("java", "inherited-interface-constant", "a constant of an implemented interface is a name in the class",
     JAVA, {"app/Limits.java": "package app;\n\npublic interface Limits {\n    int MAX = 10;\n}\n",
            "app/A.java": "package app;\n\npublic class A implements Limits {\n    int cap(int n) {\n        return Math.min(n, MAX);\n    }\n}\n"},
     [("A.cap", "reads", "Limits.MAX")],
     source="JLS 9.3, 8.3 (fields of superinterfaces are inherited); " + RS_JAVA + "findField")
case("java", "most-specific-reference-type", "of two applicable overloads, the one whose parameter type is a subtype is more specific",
     JAVA, {"app/A.java": "package app;\n\npublic class A {\n    int write(Object o) {\n        return 1;\n    }\n\n    int write(String s) {\n        return 2;\n    }\n\n    int run() {\n        return write(\"x\");\n    }\n}\n"},
     [("A.run", "calls", "app/A.java:A.write@8")], [("A.run", "calls", "app/A.java:A.write@4")],
     source="JLS 15.12.2.5 (choosing the most specific method); " + RS_JAVA + "mostSpecific")
case("java", "fixed-arity-before-varargs", "an overload applicable without variable arity is chosen before a varargs one",
     JAVA, {"app/A.java": "package app;\n\npublic class A {\n    int log(String... parts) {\n        return 1;\n    }\n\n    int log(String one) {\n        return 2;\n    }\n\n    int run() {\n        return log(\"x\");\n    }\n}\n"},
     [("A.run", "calls", "app/A.java:A.log@8")], [("A.run", "calls", "app/A.java:A.log@4")],
     source="JLS 15.12.2.2–4 (phases: strict, loose, then variable arity); " + RS_JAVA + "findMethod (phases)")
case("java", "no-boxing-before-boxing", "an overload applicable without boxing is chosen before one that needs it",
     JAVA, {"app/A.java": "package app;\n\npublic class A {\n    int put(Integer n) {\n        return 1;\n    }\n\n    int put(long n) {\n        return 2;\n    }\n\n    int run() {\n        return put(3);\n    }\n}\n"},
     [("A.run", "calls", "app/A.java:A.put@8")], [("A.run", "calls", "app/A.java:A.put@4")],
     source="JLS 15.12.2.2 (phase 1: strict invocation, no boxing); " + RS_JAVA + "findMethod (allowBoxing false first)")
case("java", "local-class", "a class declared in a block is a name of the block",
     JAVA, {"app/Repo.java": "package app;\n\npublic class Repo {\n    int find() {\n        return 1;\n    }\n}\n",
            "app/A.java": "package app;\n\npublic class A {\n    int run() {\n        class Repo {\n            int find() {\n                return 2;\n            }\n        }\n        return new Repo().find();\n    }\n\n    int other() {\n        return new Repo().find();\n    }\n}\n"},
     [("A.other", "calls", "app/Repo.java:Repo.find")], [("A.run", "calls", "app/Repo.java:Repo.find")],
     source="JLS 6.3 (the scope of a local class is the rest of the block)")

# Phase 2 for Kotlin: the Kotlin specification (Kotlin/kotlin-spec,
# `docs/src/md/kotlin.core/overload-resolution.md` 'Building the overload
# candidate set', `scoping.md` 'Linked scopes') and the K2 resolver
# (JetBrains/kotlin `compiler/fir`).
found_by("source")
OR_KT = "Kotlin/kotlin-spec@0f762a2314a9:docs/src/md/kotlin.core/overload-resolution.md "
case("kotlin", "local-function-before-member", "a local function comes before a member of the implicit receiver",
     KT, {"src/app/A.kt": "package app\n\nclass Job {\n    fun h(): Int = 1\n\n    fun run(): Int {\n        fun h(): Int = 2\n        return h()\n    }\n}\n"},
     [("Job.run", "calls", "Job.run.h")], [("Job.run", "calls", "src/app/A.kt:Job.h")],
     source=OR_KT + "'Call without an explicit receiver' (1. local non-extension callables)")
case("kotlin", "member-before-top-level", "a member of the implicit receiver comes before a top-level function",
     KT, {"src/app/A.kt": "package app\n\nfun h(): Int = 1\n\nclass Job {\n    fun h(): Int = 2\n\n    fun run(): Int = h()\n}\n"},
     [("Job.run", "calls", "Job.h")], [("Job.run", "calls", "src/app/A.kt:h")],
     source=OR_KT + "'Call without an explicit receiver' (2. implicit receivers before 3. top-level)")
case("kotlin", "explicit-import-before-package", "a top-level function imported by name comes before one of the same package",
     KT, {"src/lib/L.kt": "package lib\n\nfun helper(): Int = 1\n",
          "src/app/H.kt": "package app\n\nfun helper(): Int = 2\n",
          "src/app/A.kt": "package app\n\nimport lib.helper\n\nfun run(): Int = helper()\n"},
     [("run", "calls", "src/lib/L.kt:helper")], [("run", "calls", "src/app/H.kt:helper")],
     source=OR_KT + "'Call without an explicit receiver' (3a. explicitly imported, 3b. same package)")
case("kotlin", "package-before-star-import", "a top-level function of the same package comes before a star-imported one",
     KT, {"src/lib/L.kt": "package lib\n\nfun helper(): Int = 1\n",
          "src/app/H.kt": "package app\n\nfun helper(): Int = 2\n",
          "src/app/A.kt": "package app\n\nimport lib.*\n\nfun run(): Int = helper()\n"},
     [("run", "calls", "src/app/H.kt:helper")], [("run", "calls", "src/lib/L.kt:helper")],
     source=OR_KT + "'Call without an explicit receiver' (3b. same package, 3c. star-imported)")
case("kotlin", "local-extension-first", "an extension declared in the function comes before a top-level one",
     KT, {"src/app/A.kt": "package app\n\nclass Repo\n\nfun Repo.size(): Int = 1\n\nfun run(r: Repo): Int {\n    fun Repo.size(): Int = 2\n    return r.size()\n}\n"},
     [("run", "calls", "run.size")], [("run", "calls", "src/app/A.kt:size")],
     source=OR_KT + "'Call with an explicit receiver' (2. extensions in the current scope, smallest first)")
case("kotlin", "imported-extension-before-package", "an extension imported by name comes before one of the same package",
     KT, {"src/lib/L.kt": "package lib\n\nimport app.Repo\n\nfun Repo.size(): Int = 1\n",
          "src/app/R.kt": "package app\n\nclass Repo\n\nfun Repo.size(): Int = 2\n",
          "src/app/A.kt": "package app\n\nimport lib.size\n\nfun run(r: Repo): Int = r.size()\n"},
     [("run", "calls", "src/lib/L.kt:size")], [("run", "calls", "src/app/R.kt:size")],
     source=OR_KT + "'Call with an explicit receiver' (3. explicitly imported, 4. package scope)")
case("kotlin", "super-of-a-named-supertype", "`super<A>.f()` is A's member",
     KT, {"src/app/A.kt": "package app\n\ninterface A {\n    fun f(): Int = 1\n}\n\ninterface B {\n    fun f(): Int = 2\n}\n\nclass C : A, B {\n    override fun f(): Int = super<A>.f() + super<B>.f()\n}\n"},
     [("C.f", "calls", "A.f"), ("C.f", "calls", "B.f")],
     source=OR_KT + "'Call with an explicit super-form receiver' (super<A>)")
case("kotlin", "operator-call", "`a + b` calls the `operator fun plus` of a's type",
     KT, {"src/app/A.kt": "package app\n\nclass Money(val cents: Int) {\n    operator fun plus(o: Money): Money = Money(cents + o.cents)\n}\n\nfun total(a: Money, b: Money): Money = a + b\n"},
     [("total", "calls", "Money.plus")],
     source=OR_KT + "'Operator call'; kotlin.core/operators.md (a + b is a.plus(b))")
case("kotlin", "index-operator", "`a[i]` calls the `operator fun get` of a's type",
     KT, {"src/app/A.kt": "package app\n\nclass Grid {\n    operator fun get(i: Int): Int = i\n}\n\nfun first(g: Grid): Int = g[0]\n"},
     [("first", "calls", "Grid.get")],
     source=OR_KT + "'Operator call'; kotlin.core/expressions.md 'Indexing expressions' (a[i] is a.get(i))")
case("kotlin", "infix-call", "`a foo b` calls the `infix fun foo` of a's type",
     KT, {"src/app/A.kt": "package app\n\nclass Range(val lo: Int) {\n    infix fun upTo(hi: Int): Int = hi - lo\n}\n\nfun span(r: Range): Int = r upTo 10\n"},
     [("span", "calls", "Range.upTo")],
     source=OR_KT + "'Infix function call'")
case("kotlin", "most-specific-type", "of two applicable overloads, the one with the more specific parameter type",
     KT, {"src/app/A.kt": "package app\n\nclass Log {\n    fun write(o: Any): Int = 1\n\n    fun write(s: String): Int = 2\n}\n\nfun run(l: Log): Int = l.write(\"x\")\n"},
     [("run", "calls", "src/app/A.kt:Log.write@6")], [("run", "calls", "src/app/A.kt:Log.write@4")],
     source=OR_KT + "'Choosing the most specific candidate from the overload candidate set'")
case("kotlin", "non-vararg-before-vararg", "a candidate that needs no `vararg` expansion is more specific",
     KT, {"src/app/A.kt": "package app\n\nclass Log {\n    fun write(vararg parts: String): Int = 1\n\n    fun write(one: String): Int = 2\n}\n\nfun run(l: Log): Int = l.write(\"x\")\n"},
     [("run", "calls", "src/app/A.kt:Log.write@6")], [("run", "calls", "src/app/A.kt:Log.write@4")],
     source=OR_KT + "'Algorithm of MSC selection' (a non-parameterized, non-vararg candidate wins a tie)")
case("kotlin", "property-invoke", "a property of a function type called is the property, not a function of its name",
     KT, {"src/app/A.kt": "package app\n\nfun handler(): Int = 1\n\nclass Job(val handler: () -> Int) {\n    fun run(): Int = handler()\n}\n"},
     [], [("Job.run", "calls", "src/app/A.kt:handler")],
     source=OR_KT + "'Callables and invoke convention' (a property with invoke is a callable of the receiver)")

# Phase 2 for Swift: the compiler's unqualified lookup (swiftlang/swift
# `lib/AST/UnqualifiedLookup.cpp`, `ASTScopeLookup.cpp`) and its ranking of
# overloads (`lib/Sema/CSRanking.cpp` isDeclAsSpecializedAs, compareSolutions).
found_by("source")
UL_SW = "swiftlang/swift@1176ade30d65:lib/AST/"
RK_SW = "swiftlang/swift@1176ade30d65:lib/Sema/CSRanking.cpp "
case("swift", "local-function-before-member", "a function declared in a method comes before a method of the type",
     SW, {"a.swift": "class Job {\n  func h() -> Int {\n    return 1\n  }\n\n  func run() -> Int {\n    func h() -> Int {\n      return 2\n    }\n    return h()\n  }\n}\n"},
     [("Job.run", "calls", "Job.run.h")], [("Job.run", "calls", "a.swift:Job.h")],
     source=UL_SW + "ASTScopeLookup.cpp (local scopes before the type's members)")
case("swift", "member-before-global", "a member of the type around comes before a function of the module",
     SW, {"a.swift": "func h() -> Int {\n  return 1\n}\n\nclass Job {\n  func h() -> Int {\n    return 2\n  }\n\n  func run() -> Int {\n    return h()\n  }\n}\n"},
     [("Job.run", "calls", "Job.h")], [("Job.run", "calls", "a.swift:h")],
     source=UL_SW + "UnqualifiedLookup.cpp (lookInMembers of each type context before the module scope)")
case("swift", "concrete-before-protocol-extension", "a type's own method comes before a protocol extension's default of its name",
     SW, {"a.swift": "protocol Named {\n  func name() -> String\n}\n\nextension Named {\n  func name() -> String {\n    return \"default\"\n  }\n}\n\nstruct User: Named {\n  func name() -> String {\n    return \"user\"\n  }\n}\n\nfunc show(u: User) -> String {\n  return u.name()\n}\n"},
     [("show", "calls", "User.name")], [("show", "calls", "Named.name")],
     source=RK_SW + "isDeclAsSpecializedAs (a member of a concrete type over a protocol extension's)")
case("swift", "overload-by-label", "argument labels choose among overloads of one base name",
     SW, {"a.swift": "struct Mover {\n  func move(to x: Int) -> Int {\n    return x\n  }\n\n  func move(by dx: Int) -> Int {\n    return dx + 1\n  }\n}\n\nfunc go(m: Mover) -> Int {\n  return m.move(by: 3)\n}\n"},
     [("go", "calls", "a.swift:Mover.move@6")], [("go", "calls", "a.swift:Mover.move@2")],
     source="The Swift Programming Language, 'Functions' (argument labels are part of the name)")
case("swift", "overload-specific-type", "of two applicable overloads, the one whose parameter type is not `Any`",
     SW, {"a.swift": "struct Log {\n  func write(_ x: Any) -> Int {\n    return 1\n  }\n\n  func write(_ s: String) -> Int {\n    return 2\n  }\n}\n\nfunc run(l: Log) -> Int {\n  return l.write(\"x\")\n}\n"},
     [("run", "calls", "a.swift:Log.write@6")], [("run", "calls", "a.swift:Log.write@2")],
     source=RK_SW + "isDeclAsSpecializedAs (parameter types that convert to the other's)")
case("swift", "non-generic-before-generic", "a non-generic overload is more specialized than a generic one",
     SW, {"a.swift": "struct Log {\n  func write<T>(_ x: T) -> Int {\n    return 1\n  }\n\n  func write(_ s: String) -> Int {\n    return 2\n  }\n}\n\nfunc run(l: Log) -> Int {\n  return l.write(\"x\")\n}\n"},
     [("run", "calls", "a.swift:Log.write@6")], [("run", "calls", "a.swift:Log.write@2")],
     source=RK_SW + "isDeclAsSpecializedAs (generic parameters opened)")
case("swift", "non-variadic-before-variadic", "a non-variadic overload is preferred to a variadic one",
     SW, {"a.swift": "struct Log {\n  func write(_ parts: String...) -> Int {\n    return 1\n  }\n\n  func write(_ one: String) -> Int {\n    return 2\n  }\n}\n\nfunc run(l: Log) -> Int {\n  return l.write(\"x\")\n}\n"},
     [("run", "calls", "a.swift:Log.write@6")], [("run", "calls", "a.swift:Log.write@2")],
     source=RK_SW + "compareSolutions (a variadic parameter is a worse match)")
case("swift", "fewer-defaults", "of overloads that fit, the one that fills no default argument",
     SW, {"a.swift": "struct Log {\n  func flush(level: Int = 0) -> Int {\n    return 1\n  }\n\n  func flush() -> Int {\n    return 2\n  }\n}\n\nfunc run(l: Log) -> Int {\n  return l.flush()\n}\n"},
     [("run", "calls", "a.swift:Log.flush@6")], [("run", "calls", "a.swift:Log.flush@2")],
     source=RK_SW + "compareSolutions (fewer default arguments)")
case("swift", "closure-parameter-hides-a-function", "a closure's parameter is a name of the closure: it hides a function of its name",
     SW, {"a.swift": "func size() -> Int {\n  return 1\n}\n\nfunc run(xs: [Int]) -> [Int] {\n  return xs.map { size in size + 1 }\n}\n\nfunc other() -> Int {\n  return size()\n}\n"},
     [("other", "calls", "size")], [("run", "calls", "size"), ("run", "references", "size")],
     source=UL_SW + "ASTScopeLookup.cpp (a closure's parameters are names of its body)")

# Phase 2 for C and C++: the standards' lookup rules (C 6.2.1, 6.2.3 name
# spaces; C++ [basic.lookup], [class.member.lookup], [over.match.best]) and
# clang (llvm/llvm-project `clang/lib/Sema/SemaLookup.cpp` LookupName /
# CppLookupName, `SemaOverload.cpp` isBetterOverloadCandidate).
found_by("source")
SL_CPP = "llvm/llvm-project@87764a902a9b:clang/lib/Sema/SemaLookup.cpp "
SO_CPP = "llvm/llvm-project@87764a902a9b:clang/lib/Sema/SemaOverload.cpp "
case("c", "tag-name-space", "a struct tag and a function of one name are in different name spaces",
     CC, {"a.c": "struct stat {\n    int size;\n};\n\nint stat(int x) {\n    return x;\n}\n\nint run(void) {\n    struct stat s;\n    s.size = 1;\n    return stat(s.size);\n}\n"},
     [("run", "calls", "a.c:stat@5"), ("run", "references", "a.c:stat@1")],
     source="ISO C 6.2.3 (tags are a name space of their own); " + SL_CPP + "LookupName (LookupTagName)")
case("cpp", "derived-member-hides-base", "a member of a derived class hides every member of its name in the base",
     CPP, {"a.cc": "class Base {\n public:\n  int Get(int x) {\n    return x;\n  }\n};\n\nclass Derived : public Base {\n public:\n  int Get() {\n    return 2;\n  }\n  int Run() {\n    return Get();\n  }\n};\n\nint Use(Derived& d) {\n  return d.Get();\n}\n"},
     [("Derived.Run", "calls", "Derived.Get"), ("Use", "calls", "Derived.Get")], [("Use", "calls", "Base.Get"), ("Derived.Run", "calls", "Base.Get")],
     source="ISO C++ [class.member.lookup] (a declaration in a derived class hides the base's); " + SL_CPP + "LookupQualifiedName")
case("cpp", "using-declaration-of-a-base-member", "`using Base::Get;` brings the base's overloads into the derived class",
     CPP, {"a.cc": "class Base {\n public:\n  int Get(int x) {\n    return x;\n  }\n};\n\nclass Derived : public Base {\n public:\n  using Base::Get;\n  int Get() {\n    return 2;\n  }\n};\n\nint Use(Derived& d) {\n  return d.Get(3);\n}\n"},
     [("Use", "calls", "Base.Get")], [("Use", "calls", "Derived.Get")],
     source="ISO C++ [namespace.udecl] (a using-declaration in a class names the base's members)")
case("cpp", "member-before-namespace", "a member of the class comes before a function of the namespace around",
     CPP, {"a.cc": "int Helper() {\n  return 1;\n}\n\nclass A {\n public:\n  int Helper() {\n    return 2;\n  }\n  int Run() {\n    return Helper();\n  }\n};\n"},
     [("A.Run", "calls", "A.Helper")], [("A.Run", "calls", "a.cc:Helper")],
     source="ISO C++ [basic.lookup.unqual] (class scope before the enclosing namespace)")
case("cpp", "member-declared-later", "a member function's body sees members declared after it",
     CPP, {"a.cc": "class A {\n public:\n  int Run() {\n    return Later();\n  }\n  int Later() {\n    return 2;\n  }\n};\n"},
     [("A.Run", "calls", "A.Later")],
     source="ISO C++ [class.mem] (a function body is a complete-class context)")
case("cpp", "global-scope-operator", "`::f()` is the function of the global namespace, not the namespace around",
     CPP, {"a.cc": "int Helper() {\n  return 1;\n}\n\nnamespace app {\nint Helper() {\n  return 2;\n}\n\nint Run() {\n  return ::Helper() + Helper();\n}\n}  // namespace app\n"},
     [("app.Run", "calls", "a.cc:Helper"), ("app.Run", "calls", "app.Helper")],
     source="ISO C++ [namespace.qual] (a name after a bare `::` is looked up in the global namespace)")
case("cpp", "exact-match-before-conversion", "an overload whose parameter type is the argument's exactly wins over one that converts it",
     CPP, {"a.cc": "class Log {\n public:\n  int Write(double d) {\n    return 1;\n  }\n  int Write(int n) {\n    return 2;\n  }\n};\n\nint Run(Log& l) {\n  return l.Write(3);\n}\n"},
     [("Run", "calls", "a.cc:Log.Write@6")], [("Run", "calls", "a.cc:Log.Write@3")],
     source="ISO C++ [over.ics.rank] (an exact match is better than a conversion); " + SO_CPP + "CompareStandardConversionSequences")
case("cpp", "non-template-before-template", "a function that is no template wins over a template specialization equally good",
     CPP, {"a.cc": "template <typename T>\nint Show(T x) {\n  return 1;\n}\n\nint Show(int x) {\n  return 2;\n}\n\nint Run() {\n  return Show(3);\n}\n"},
     [("Run", "calls", "a.cc:Show@6")], [("Run", "calls", "a.cc:Show@2")],
     source="ISO C++ [over.match.best] (F1 is not a template specialization and F2 is); " + SO_CPP + "isBetterOverloadCandidate")
case("cpp", "operator-call", "`a + b` calls the `operator+` of a's class",
     CPP, {"a.cc": "class Money {\n public:\n  int cents;\n  Money operator+(const Money& o) const {\n    return Money{cents + o.cents};\n  }\n};\n\nMoney Total(const Money& a, const Money& b) {\n  return a + b;\n}\n"},
     [("Total", "calls", "Money.operator+")],
     source="ISO C++ [over.match.oper] (a + b is a.operator+(b) or operator+(a, b))")
case("cpp", "inline-namespace", "a member of an inline namespace is a member of the namespace around",
     CPP, {"a.cc": "namespace lib {\ninline namespace v2 {\nint Get() {\n  return 2;\n}\n}  // namespace v2\n}  // namespace lib\n\nint Run() {\n  return lib::Get();\n}\n"},
     [("Run", "calls", "lib.v2.Get")],
     source="ISO C++ [namespace.def] (members of an inline namespace are members of the enclosing one)")
case("cpp", "class-before-its-constructor", "`T::f()` in a member of T: the name before `::` is the class, not the constructor of its name",
     CPP, {"status.h": "class Status {\n public:\n  Status() {\n  }\n  Status(const Status& rhs);\n  static Status OK() {\n    return Status();\n  }\n  Status Copy();\n};\n\ninline Status::Status(const Status& rhs) {\n}\n",
           "status.cc": "#include \"status.h\"\n\nStatus Status::Copy() {\n  Status s = Status::OK();\n  return s;\n}\n"},
     [("Status.Copy", "calls", "Status.OK")],
     source="ISO C++ [class.qual] (in a nested-name-specifier a constructor is not found: the injected class name is the class)")

# Phase 2 for Python: the Language Reference's 'Naming and binding' (4.2) and
# 'The import system', and CPython's symbol table (python/cpython
# `Python/symtable.c`: analyze_name, the class block's free names).
found_by("source")
NB_PY = "python/cpython@1822ff517a90:Doc/reference/executionmodel.rst "
ST_PY = "python/cpython@1822ff517a90:Python/symtable.c "
case("python", "class-scope-not-in-methods", "a name bound in a class body is no name of its methods: a bare call is the module's",
     PY, {"a.py": "def helper():\n    return 1\n\n\nclass A:\n    def helper(self):\n        return 2\n\n    def run(self):\n        return helper()\n"},
     [("A.run", "calls", "a.py:helper")], [("A.run", "calls", "A.helper")],
     source=NB_PY + "4.2.2 (the scope of names defined in a class block is limited to the class block; it does not extend to the code blocks of methods); " + ST_PY + "analyze_name")
case("python", "bound-later-is-local", "a name bound anywhere in a function is local to all of it, before the binding too",
     PY, {"a.py": "def helper():\n    return 1\n\n\ndef run():\n    x = helper()\n    helper = 2\n    return x + helper\n\n\ndef other():\n    return helper()\n"},
     [("other", "calls", "helper")], [("run", "calls", "helper")],
     source=NB_PY + "4.2.2 (if a name binding operation occurs anywhere within a code block, all uses of the name within the block are treated as references to the current block)")
case("python", "global-statement", "`global x` makes an assignment in a function write the module's variable",
     PY, {"a.py": "counter = 0\n\n\ndef bump():\n    global counter\n    counter = counter + 1\n    return counter\n\n\ndef local_only():\n    counter = 5\n    return counter\n"},
     [("bump", "writes", "counter")], [("local_only", "writes", "counter")],
     source=NB_PY + "4.2.2 (the global statement); " + ST_PY + "DEF_GLOBAL")
case("python", "star-import-respects-all", "`from m import *` binds only the names of m's `__all__`",
     PY, {"pkg/__init__.py": "", "pkg/m.py": "__all__ = [\"shown\"]\n\n\ndef shown():\n    return 1\n\n\ndef hidden():\n    return 2\n",
          "pkg/a.py": "from pkg.m import *\n\n\ndef hidden():\n    return 3\n\n\ndef run():\n    return shown() + hidden()\n"},
     [("run", "calls", "shown"), ("run", "calls", "pkg/a.py:hidden")], [("run", "calls", "pkg/m.py:hidden")],
     source="The Python Language Reference 7.11 'The import statement' (the public names are those of __all__)")
case("python", "star-import-skips-underscore", "`from m import *` without `__all__` binds no name that starts with `_`",
     PY, {"pkg/__init__.py": "", "pkg/m.py": "def _private():\n    return 1\n\n\ndef public():\n    return 2\n",
          "pkg/a.py": "from pkg.m import *\n\n\ndef _private():\n    return 3\n\n\ndef run():\n    return public() + _private()\n"},
     [("run", "calls", "public"), ("run", "calls", "pkg/a.py:_private")], [("run", "calls", "pkg/m.py:_private")],
     source="The Python Language Reference 7.11 'The import statement' (all names that do not begin with an underscore)")
case("python", "lambda-parameter-hides", "a lambda's parameter is a name of the lambda: it hides a function of its name there",
     PY, {"a.py": "def size():\n    return 1\n\n\ndef run(xs):\n    return list(map(lambda size: size + 1, xs))\n\n\ndef other():\n    return size()\n"},
     [("other", "calls", "size")], [("run", "calls", "size"), ("run", "references", "size")],
     source=NB_PY + "4.2.1 (a lambda's parameters are bound in its block)")
case("python", "pep695-type-parameter", "a type parameter (`def f[T](x: T)`) hides a class of its name in the function",
     PY, {"a.py": "class T:\n    pass\n\n\ndef keep[T](x: T) -> T:\n    return x\n\n\ndef plain(x: T) -> T:\n    return x\n"},
     [("plain", "references", "T")], [("keep", "references", "T")],
     source=NB_PY + "4.2.2 'Annotation scopes' (type parameter lists introduce an annotation scope)", since="3.12")
case("python", "class-attribute-through-self", "`self.x` of a class attribute is the class's attribute",
     PY, {"a.py": "class Repo:\n    def find(self):\n        return 1\n\n\nclass A:\n    repo = Repo()\n\n    def run(self):\n        return self.repo.find()\n"},
     [("A.run", "calls", "Repo.find")],
     source="The Python Language Reference 3.2 'Custom classes' (an attribute not found on the instance is looked up on the class)")

# Phase 2 for PHP and Ruby: the PHP language specification (php/php-langspec
# `spec/18-namespaces.md`, `spec/16-traits.md`, `spec/14-classes.md`) and
# Ruby's own documentation and method table (ruby/ruby `doc/`, `class.c`
# include_modules_at / rb_prepend_module, `vm_insnhelper.c` constant lookup).
found_by("source")
SPEC_PHP = "php/php-langspec@ca697b43031e:spec/"
RUBY_SRC = "ruby/ruby@6e687b542739:"
case("php", "class-name-has-no-global-fallback", "an unqualified class name is of the current namespace only: unlike a function, it does not fall back to the global one",
     PHP, {"g.php": "<?php\nclass Helper\n{\n    public function run(): int\n    {\n        return 1;\n    }\n}\n",
           "a.php": "<?php\nnamespace App;\n\nfunction make()\n{\n    return new Helper();\n}\n"},
     [], [("App.make", "calls", "Helper"), ("make", "calls", "Helper")],
     source=SPEC_PHP + "18-namespaces.md 'Name Lookup' (unqualified class names are resolved against the current namespace; only functions and constants fall back)")
case("php", "trait-insteadof", "`A::hello insteadof B` picks A's method of two used traits",
     PHP, {"a.php": "<?php\ntrait A\n{\n    public function hello(): string\n    {\n        return 'a';\n    }\n}\n\ntrait B\n{\n    public function hello(): string\n    {\n        return 'b';\n    }\n}\n\nclass C\n{\n    use A, B {\n        A::hello insteadof B;\n    }\n\n    public function run(): string\n    {\n        return $this->hello();\n    }\n}\n"},
     [("C.run", "calls", "A.hello")], [("C.run", "calls", "B.hello")],
     source=SPEC_PHP + "16-traits.md 'Trait Declarations' (insteadof)")
case("php", "trait-before-inherited", "a method a trait brings comes before an inherited one, and the class's own before both",
     PHP, {"a.php": "<?php\nclass Base\n{\n    public function hello(): string\n    {\n        return 'base';\n    }\n}\n\ntrait T\n{\n    public function hello(): string\n    {\n        return 'trait';\n    }\n}\n\nclass C extends Base\n{\n    use T;\n\n    public function run(): string\n    {\n        return $this->hello();\n    }\n}\n"},
     [("C.run", "calls", "T.hello")], [("C.run", "calls", "Base.hello")],
     source=SPEC_PHP + "16-traits.md (an inherited member is overridden by a trait's; a member of the class overrides the trait's)")
case("ruby", "last-include-first", "of two included modules with a method of one name, the one included last is found first",
     RB, {"a.rb": "module A\n  def hello\n    'a'\n  end\nend\n\nmodule B\n  def hello\n    'b'\n  end\nend\n\nclass C\n  include A\n  include B\n\n  def run\n    hello\n  end\nend\n"},
     [("C.run", "calls", "B.hello")], [("C.run", "calls", "A.hello")],
     source=RUBY_SRC + "class.c include_modules_at (a module is inserted right above the class: the last included is the nearest)")
case("ruby", "class-before-included-module", "a method of the class comes before an included module's",
     RB, {"a.rb": "module A\n  def hello\n    'a'\n  end\nend\n\nclass C\n  include A\n\n  def hello\n    'c'\n  end\n\n  def run\n    hello\n  end\nend\n"},
     [("C.run", "calls", "C.hello")], [("C.run", "calls", "A.hello")],
     source=RUBY_SRC + "class.c (Module#ancestors: the class, then its modules, then the superclass)")
case("ruby", "lexical-constant-before-ancestor", "a constant of a module around the use comes before one of the class's ancestors",
     RB, {"a.rb": "class Base\n  LIMIT = Limits.new\nend\n\nclass Limits\n  def max\n    1\n  end\nend\n\nclass Other\n  def max\n    2\n  end\nend\n\nmodule Outer\n  LIMIT = Other.new\n\n  class C < Base\n    def run\n      LIMIT.max\n    end\n  end\nend\n"},
     [("Outer.C.run", "calls", "Other.max")], [("Outer.C.run", "calls", "Limits.max")],
     source=RUBY_SRC + "vm_insnhelper.c vm_get_ev_const (the lexical scope, Module.nesting, before the ancestors)")
case("ruby", "block-parameter-hides-a-method", "a block's parameter is a name of the block: it hides a method of its name there",
     RB, {"a.rb": "class C\n  def size\n    1\n  end\n\n  def run(xs)\n    xs.map { |size| size + 1 }\n  end\n\n  def other\n    size\n  end\nend\n"},
     [("C.other", "calls", "C.size")], [("C.run", "calls", "C.size")],
     source="Ruby documentation 'Methods' / 'Blocks' (block parameters are local to the block)")
case("ruby", "local-only-after-assignment", "a name is a local only after the assignment that makes it one: before it, it is a method call",
     RB, {"a.rb": "class C\n  def helper\n    1\n  end\n\n  def run\n    a = helper\n    helper = 2\n    a + helper\n  end\nend\n"},
     [("C.run", "calls", "C.helper")],
     source="Ruby documentation 'Assignment' (a local variable is created when the parser encounters the assignment)")

# Phase 2 for Scala: the binding precedences of the Scala 3 typer
# (scala/scala3 `compiler/src/dotty/tools/dotc/typer/Typer.scala`, `BindingPrec`
# and findRef: a definition of the compilation unit, then a named import, then
# a wildcard import, then a package member of another unit) and SLS chapter 2.
found_by("source")
TY_SC = "scala/scala3@1615177b58f6:compiler/src/dotty/tools/dotc/typer/Typer.scala "
case("scala", "named-import-before-wildcard", "a name imported by name beats one a wildcard import brings",
     SC, {"src/a/Thing.scala": "package a\n\nclass Thing {\n  def size: Int = 1\n}\n",
          "src/b/Thing.scala": "package b\n\nclass Thing {\n  def size: Int = 2\n}\n",
          "src/app/A.scala": "package app\n\nimport a.Thing\nimport b._\n\nobject Main {\n  def run(t: Thing): Int = t.size\n}\n"},
     [("Main.run", "calls", "src/a/Thing.scala:Thing.size")], [("Main.run", "calls", "src/b/Thing.scala:Thing.size")],
     source=TY_SC + "BindingPrec (NamedImport beats WildImport)")
case("scala", "definition-before-import", "a definition of the compilation unit beats an imported name",
     SC, {"src/lib/Util.scala": "package lib\n\nobject Util {\n  def helper(): Int = 1\n}\n",
          "src/app/A.scala": "package app\n\nimport lib.Util.helper\n\nobject Main {\n  def helper(): Int = 2\n\n  def run(): Int = helper()\n}\n"},
     [("Main.run", "calls", "Main.helper")], [("Main.run", "calls", "Util.helper")],
     source=TY_SC + "findRef (a definition in an inner scope is found before the imports outside it)")
case("scala", "wildcard-import-before-other-unit", "a wildcard import beats a member of the package that another compilation unit defines",
     SC, {"src/lib/Repo.scala": "package lib\n\nclass Repo {\n  def find: Int = 1\n}\n",
          "src/app/Repo.scala": "package app\n\nclass Repo {\n  def find: Int = 2\n}\n",
          "src/app/A.scala": "package app\n\nimport lib._\n\nobject Main {\n  def run(r: Repo): Int = r.find\n}\n"},
     [("Main.run", "calls", "src/lib/Repo.scala:Repo.find")], [("Main.run", "calls", "src/app/Repo.scala:Repo.find")],
     source=TY_SC + "BindingPrec (PackageClause of another unit is the lowest: WildImport beats it)")
case("scala", "member-before-extension", "a member of the receiver's type comes before an extension method of the name",
     SC, {"src/app/A.scala": "package app\n\nclass Repo {\n  def size: Int = 1\n}\n\nextension (r: Repo)\n  def size: Int = 2\n\nobject Main {\n  def run(r: Repo): Int = r.size\n}\n"},
     [("Main.run", "calls", "Repo.size")],
     source="Scala 3 reference 'Extension Methods' (an extension is tried only when the member is not there)")
case("scala", "export-clause", "`export inner.f` makes f a member of the object that writes it",
     SC, {"src/app/A.scala": "package app\n\nclass Printer {\n  def print(): Int = 1\n}\n\nobject Facade {\n  private val p = new Printer\n  export p.print\n}\n\nobject Main {\n  def run(): Int = Facade.print()\n}\n"},
     [("Main.run", "calls", "Printer.print")],
     source="Scala 3 reference 'Export Clauses' (an export defines aliases of the members selected)")

# Rust, from rust-analyzer's answers on whole repositories (benchmarks/oracle_rust.py
# on ripgrep and ureq, engine 0.5.80): the rules its wrong rows showed.
found_by("compiler")
case("rust", "path-in-macro-argument", "a macro's argument `E::V` is a path: its last name is the variant, never a bare name of the module",
     RS, {"Cargo.toml": CARGO21, "src/lib.rs": "pub struct Count;\n\npub enum Mode {\n    Count,\n    Json,\n}\n\n"
          "pub fn is_count(m: Mode) -> bool {\n    matches!(m, Mode::Count)\n}\n\n"
          "pub fn run() {\n    assert!(is_count(Mode::Count));\n    let all = vec![Mode::Count, Mode::Json];\n    drop(all);\n}\n"},
     [("run", "references", "Mode"), ("run", "calls", "is_count")],
     [("run", "references", "src/lib.rs:Count"), ("is_count", "references", "src/lib.rs:Count")],
     source=REF_RS + "macros-by-example.md r[macro.decl.meta.specifier] (an `expr` fragment is parsed as an expression)")

case("rust", "impl-generics-bound-the-struct-parameter", "in `impl<S: Sink> Core<S>`, a field typed `S` is a `Sink`: a method called on it is the trait's, never a blanket impl's",
     RS, {"Cargo.toml": CARGO21, "src/lib.rs": "pub trait Sink {\n    fn matched(&mut self, n: u64) -> bool;\n}\n\n"
          "impl<'a, S: Sink> Sink for &'a mut S {\n    fn matched(&mut self, n: u64) -> bool {\n        (**self).matched(n)\n    }\n}\n\n"
          "pub struct Core<S> {\n    sink: S,\n}\n\nimpl<X: Sink> Core<X> {\n    pub fn run(&mut self) -> bool {\n        self.sink.matched(1)\n    }\n}\n"},
     [("Core.run", "calls", "Sink.matched")], [("Core.run", "calls", "S.matched")],
     source=REF_RS + "items/implementations.md r[items.impl.generics] (an impl's generic parameters are in scope for its items); " + RC_RS + "rustc_hir_typeck/src/method/probe.rs assemble_inherent_candidates_from_param")
case("rust", "item-of-another-module-needs-use", "an item of another module is in scope only through a `use`: `Result` in the crate root is the prelude's, not `http::Result`",
     RS, {"Cargo.toml": CARGO21, "src/lib.rs": "mod http;\n\npub fn top() -> Result<(), ()> {\n    Ok(())\n}\n",
          "src/http/mod.rs": "mod extractor;\n\npub type Result<T, E = String> = std::result::Result<T, E>;\n\npub fn own() -> Result<()> {\n    Ok(())\n}\n",
          "src/http/extractor.rs": "pub fn child() -> Result<(), ()> {\n    Ok(())\n}\n"},
     [("own", "references", "Result")], [("top", "references", "Result"), ("child", "references", "Result")],
     source=REF_RS + "names/scopes.md r[names.scopes.item] (an item is in scope in the module it is declared in); names/preludes.md r[names.preludes.std]")
case("rust", "size-of-a-returned-slice", "`xs().is_empty()` where `xs` returns `&[T]` is the slice's method; `xs()[0].is_empty()` is the element's",
     RS, {"Cargo.toml": CARGO21, "src/lib.rs": "pub struct Match {\n    start: usize,\n}\n\nimpl Match {\n    pub fn is_empty(&self) -> bool {\n        self.start == 0\n    }\n\n    pub fn len(&self) -> usize {\n        self.start\n    }\n}\n\npub struct Sunk {\n    matches: Vec<Match>,\n}\n\nimpl Sunk {\n    pub fn matches(&self) -> &[Match] {\n        &self.matches\n    }\n}\n\npub fn none(s: &Sunk) -> bool {\n    s.matches().is_empty()\n}\n\npub fn first_empty(s: &Sunk) -> bool {\n    s.matches()[0].is_empty()\n}\n"},
     [("first_empty", "calls", "Match.is_empty")], [("none", "calls", "Match.is_empty")],
     source=REF_RS + "expressions/method-call-expr.md r[expr.method.candidate-receiver-types] (the receiver `&[Match]` is searched before any autoderef to an element)")
case("rust", "primitive-type-path", "`usize::from(b)` is the primitive's associated function: no guess goes to a project method of the name",
     RS, {"Cargo.toml": CARGO21, "src/lib.rs": "pub struct Wrapper(u8);\n\nimpl From<Wrapper> for std::io::Error {\n    fn from(_w: Wrapper) -> Self {\n        std::io::Error::other(\"x\")\n    }\n}\n\npub fn index(b: u8) -> usize {\n    usize::from(b)\n}\n"},
     [], [("index", "calls", "from")],
     source=REF_RS + "types/numeric.md and paths.md r[paths.qualifiers.type-to-trait] (a primitive's path names the language's type)")

# --- running ------------------------------------------------------------------

def _matches(node: dict[str, Any], want: str) -> bool:
    qual = node.get("qualname")
    if ":" in want:
        # `path:name`: that very definition, by its whole qualified name;
        # `path:name@line` the one of several (overloads) on that line
        path, want = want.split(":", 1)
        line = None
        if "@" in want:
            want, at = want.rsplit("@", 1)
            line = int(at)
        loc = str(node.get("source_location") or "").lstrip("L").split("-")[0]
        return bool(str(node.get("source_file") or "") == path and qual == want
                    and (line is None or loc == str(line)))
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

    versions_path = Path(__file__).resolve().parent / "results-case-versions.json"
    measured: dict[str, Any] = json.loads(versions_path.read_text(encoding="utf-8")) \
        if versions_path.exists() else {}
    langs: list[str] = []
    for c in cases:
        if c["lang"] not in langs:
            langs.append(c["lang"])
    for lang in langs:
        mine = [c for c in cases if c["lang"] == lang]
        passed = sum(1 for c in mine if results[c["id"]]["ok"])
        if args.markdown:
            how = {h: sum(1 for c in mine if c["found"] == h)
                   for h in ("specification", "compiler", "comparison", "grammar", "source")}
            read = sum(1 for c in mine if c["source"])
            print(f"\n### {lang} ({passed} of {len(mine)})\n\n{mine[0]['where']}.\n")
            print(f"Found by: the specification {how['specification']}, a compiler {how['compiler']}, "
                  f"comparison {how['comparison']}, the grammar {how['grammar']}, "
                  f"the language's own tools {how['source']}. "
                  f"With the place it was read in the language's own "
                  f"tools: {read}.\n")
            print("| Rule | Case | Versions | Engine |\n|---|---|---|---|")
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
                # what the case states, else the oldest version its compiler
                # accepts it in (`verify_cases.py --min-version`)
                asked = measured.get(c["id"]) or {}
                if c["since"] or c["until"]:
                    versions = f"{c['since'] or ''}–{c['until'] or ''}"
                elif asked.get("until"):
                    versions = f"up to {asked['until']}"
                elif asked.get("since") and not asked.get("at_least"):
                    versions = f"{asked['since']}+"
                else:
                    versions = "all" if asked else "–"
                print(f"| {rule} | `{c['id'].split('/', 1)[1]}` | {versions} | {state} |")
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
