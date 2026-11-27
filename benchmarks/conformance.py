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
  python benchmarks/conformance.py --only go,csharp --scip --baseline benchmarks/results-conformance.json

`--scip` reads each case as graphlore's tools do in precise mode: the
language's SCIP indexer (scip-go, scip-dotnet) is run on the case's project
and the engine's edges are checked against it (`resolve._precise`); an edge
the index contradicts counts as absent. Against the engine's baseline,
"fixed" is a case precise mode holds and the engine alone does not.

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
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

Edge = tuple[str, str, str]

# (language, id, rule, where the language defines it, files, expect, absent)
CASES: list[dict[str, Any]] = []


# How the cases that follow were found (docs/rule-sources.md): written from the
# language's `specification`; shown missing by a `compiler` (its test suite, or
# its answers on whole repositories); by `comparison` with graphify's graph; or
# from the `grammar` (a place a name can be written that was not read); or by a
# `sweep` of the language's own tools, section by section.
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
# C#, from Roslyn's answers on restsharp (benchmarks/oracle_csharp.py, engine 0.5.80)
case("csharp", "identity-conversion-overload", "an argument declared with exactly a parameter's type picks that overload over one taking another constructed type",
     CS, {"P.cs": "using System;\nusing System.Collections.Generic;\n\nnamespace App\n{\n    public static class P\n    {\n        static int Run(Func<object, IEnumerable<IFormattable>> get) => 1;\n\n        static int Run(Func<object, IEnumerable<IConvertible>> get) => 2;\n\n        static int Go(Func<object, IEnumerable<IFormattable>> getFormattables) => Run(getFormattables);\n    }\n}\n"},
     [("P.Go", "calls", "P.cs:App.P.Run@8")], [("P.Go", "calls", "P.cs:App.P.Run@10")],
     source="C# Language Specification 12.6.4.5 'Better conversion from expression' (an identity conversion is better than any other)")

# Python, from scip-python's answers on httpx and click (benchmarks/oracle_scip.py,
# engine 0.5.80): the rules its wrong and missed rows showed.
case("python", "name-of-another-module-needs-import", "a bare name a module neither defines nor imports is a builtin or nothing: never a definition of another module",
     PY, {"a/x.py": "def f(p):\n    return opener(p)\n\n\ndef g(p):\n    return open(p)\n",
          "b/y.py": "def opener(url):\n    pass\n\n\ndef open(url):\n    pass\n"},
     [], [("f", "calls", "opener"), ("g", "calls", "open")],
     source=PY + " 4.2.2 'Resolution of names' (a name is looked up in the local, enclosing, global and builtin namespaces; the global one is the module's)")
case("python", "enum-member-names-its-enum", "`State.CLOSED` of an `enum.Enum` subclass is a member of the enum: a reference to the enum",
     PY, {"a.py": "import enum\n\n\nclass State(enum.Enum):\n    OPEN = 1\n    CLOSED = 2\n\n\ndef closed(s):\n    return s == State.CLOSED\n"},
     [("closed", "references", "State")],
     source="Python standard library 'enum' (the class attributes of an Enum subclass are its members)")
case("python", "default-value-reads-the-name", "a parameter's default value is read where the function is defined: a module variable written there is read by the method",
     PY, {"a.py": "class UseDefault:\n    pass\n\n\nUSE_DEFAULT = UseDefault()\n\n\n"
          "class Client:\n    def get(self, url: str, *, timeout: float | UseDefault = USE_DEFAULT) -> None:\n        return None\n"},
     [("Client.get", "reads", "USE_DEFAULT")],
     source=PY + " 8.7 'Function definitions' (default parameter values are evaluated when the definition is executed)")
case("python", "type-alias-in-annotation", "a module variable assigned a type (`Number = Union[int, float]`) written as an annotation is a reference to the alias",
     PY, {"a.py": "import typing\n\nNumber = typing.Union[int, float]\n\n\ndef double(x: Number) -> Number:\n    return x\n"},
     [("double", "references", "Number")],
     source="typing documentation 'Type aliases' (a type alias is defined by assigning the type to the alias)")
case("python", "union-annotated-field-member", "a member of a field annotated `A | B | None` is the one type of the union that has it; never the class's own method of the name",
     PY, {"a.py": "class Sync:\n    def close(self):\n        pass\n\n\nclass Async:\n    async def aclose(self):\n        pass\n\n\n"
          "class Response:\n    def __init__(self, stream: Sync | Async | None = None):\n        self.stream = stream\n\n    def close(self):\n        self.stream.close()\n"},
     [("Response.close", "calls", "Sync.close")], [("Response.close", "calls", "Response.close")],
     source="PEP 604 (`X | Y` is a union: a value of it is one of its members' types)")
case("python", "field-typed-by-its-parameter", "`self.request = request` in `__init__` with `request: Request` types the field, also in a class with a library base",
     PY, {"a.py": "import urllib.request\n\n\nclass Request:\n    def __init__(self):\n        self.headers = {}\n\n\n"
          "class Response:\n    def __init__(self):\n        self.headers = {}\n\n\n"
          "class Compat(urllib.request.Request):\n    def __init__(self, request: Request) -> None:\n        super().__init__(url=\"x\")\n        self.request = request\n\n"
          "    def add(self, key, value):\n        self.request.headers[key] = value\n"},
     [("Compat.add", "writes", "Request.headers")], [("Compat.add", "writes", "Response.headers")],
     source=PY + " 3.2 (an instance attribute is created by assignment; its value is the parameter's)")

# Go, from go/types on chi, cobra, gin-examples and resty (benchmarks/oracle_go.py,
# engine 0.5.80): the uses it binds that the graph had no edge for.
case("go", "element-of-a-named-slice-type", "an element of a named slice type (`type byName []*Command`) is of the element type: `c[i].Name()` is Command's",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\ntype Command struct{ use string }\n\nfunc (c *Command) Name() string {\n\treturn c.use\n}\n\n"
          "type byName []*Command\n\nfunc (c byName) Less(i, j int) bool {\n\treturn c[i].Name() < c[j].Name()\n}\n"},
     [("byName.Less", "calls", "Command.Name")],
     source=GO + " ('Index expressions': a[x] of a slice type is the element at x)")
case("go", "element-of-a-slice-field", "an element of a field declared as a slice of a named type is of that type: `w.values[i].inverse()`",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\ntype group struct{ n int }\n\nfunc (g group) inverse() group {\n\treturn group{-g.n}\n}\n\n"
          "type window struct {\n\tvalues []group\n\tidx    int\n}\n\nfunc (w *window) flip() group {\n\treturn w.values[w.idx].inverse()\n}\n"},
     [("window.flip", "calls", "group.inverse")],
     source=GO + " ('Index expressions')")
case("go", "embedded-field-by-its-type-name", "an embedded field is named by its type: `f.basicWriter.flush()` is basicWriter's method",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\ntype basicWriter struct{ n int }\n\nfunc (b *basicWriter) flush() {\n\tb.n = 0\n}\n\n"
          "type flushWriter struct {\n\tbasicWriter\n}\n\nfunc (f *flushWriter) Flush() {\n\tf.basicWriter.flush()\n}\n"},
     [("flushWriter.Flush", "calls", "basicWriter.flush")],
     source=GO + " ('Struct types': the unqualified type name acts as the field name of an embedded field)")
case("go", "named-result-type-method", "a call's result typed by a named type answers its method: `Chain(m).Handler(h)` is Middlewares.Handler",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\ntype Middlewares []func(int) int\n\nfunc (ms Middlewares) Handler(h int) int {\n\treturn h\n}\n\n"
          "func Chain(ms ...func(int) int) Middlewares {\n\treturn Middlewares(ms)\n}\n\nfunc Use(h int) int {\n\treturn Chain().Handler(h)\n}\n"},
     [("Use", "calls", "Middlewares.Handler")],
     source=GO + " ('Method sets', 'Calls')")
case("go", "function-typed-field-types", "the parameter and result types of a struct field's function type are references of the struct",
     GO, {"go.mod": GOMOD, "a.go": "package app\n\ntype Command struct {\n\tRunE func(cmd *Command, args []string) error\n\tHook func() *Result\n}\n\ntype Result struct{ ok bool }\n"},
     [("Command", "references", "Result")],
     source=GO + " ('Function types')")

# Kotlin, from the calls clikt and kotlinpoet left without an edge
# (benchmarks/unresolved.py, engine 0.5.80): an extension's result in a chain.
case("kotlin", "extension-result-in-a-chain", "an extension called in the middle of a chain gives its declared result, which the next call is made on: `c.argument().optional()`",
     KT, {"src/app/A.kt": "package app\n\nclass Processed<A, V>(val name: String)\n\nclass Command\n\n"
          "fun Command.argument(name: String = \"\"): Processed<String, String> = Processed(name)\n\n"
          "fun <A : Any, V> Processed<A, V>.optional(): Processed<A?, V> = Processed(name)\n\n"
          "class Tool {\n    fun build(c: Command) {\n        c.argument().optional()\n    }\n}\n"},
     [("Tool.build", "calls", "optional"), ("Tool.build", "calls", "argument")],
     source="Kotlin Language Specification, 'Extension declaration' and 'Call with an explicit receiver' (the receiver of a call is the value of the expression before it)")
case("kotlin", "extension-result-through-typealias", "an extension whose result is written as a type alias gives the aliased type to the next call",
     KT, {"src/app/A.kt": "package app\n\nclass Processed<A, V>(val name: String)\n\ntypealias RawArgument = Processed<String, String>\n\nclass Command\n\n"
          "fun Command.argument(name: String = \"\"): RawArgument = Processed(name)\n\n"
          "fun <A : Any, V> Processed<A, V>.optional(): Processed<A?, V> = Processed(name)\n\n"
          "class Tool {\n    fun build(c: Command) {\n        c.argument().optional()\n    }\n}\n"},
     [("Tool.build", "calls", "optional")],
     source="Kotlin Language Specification, 'Type alias' (an alias stands for the type it names)")
case("csharp", "extension-result-in-a-chain", "an extension method called in the middle of a chain gives its result, which the next extension is called on",
     CS, {"A.cs": "namespace App\n{\n    public class Request { }\n\n    public class Command { }\n\n"
          "    public static class Ext\n    {\n        public static Request Build(this Command c) => new Request();\n\n"
          "        public static Request Signed(this Request r) => r;\n    }\n\n"
          "    public class Tool\n    {\n        public Request Run(Command c) => c.Build().Signed();\n    }\n}\n"},
     [("Tool.Run", "calls", "Ext.Signed"), ("Tool.Run", "calls", "Ext.Build")],
     source="C# Language Specification 12.8.10.3 'Extension method invocations'")

# Go, the sweep (docs/rule-sources.md, 'Phase 2b: the sweep'): the
# specification and go/types read section by section by a smaller model
# (.claude/workflows/rule-sweep.js), each case then given to the compiler, to
# go/types (`verify_cases.py --binding`) and to the engine
# (`benchmarks/sweep_check.py`) before it was written here. A failing one is an
# open defect.
found_by("sweep")
case('go', 'if-init-is-scoped-to-the-if', "a name declared in an `if` statement's init is a name of that statement only, else-branch included",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nfunc helper() int { return 1 }\n\ntype T struct{}\n\nfunc (*T) Close() int { return 0 }\n\nfunc mk() *T { return &T{} }\n\nfunc Run() int {\n\tif helper := mk(); helper == nil {\n\t\treturn 0\n\t} else {\n\t\thelper.Close()\n\t}\n\treturn helper()\n}\n'},
     [('Run', 'calls', 'T.Close'), ('Run', 'calls', 'helper')], [],
     source='spec Blocks (each if, for and switch statement is in its own implicit block)')
case('go', 'for-init-is-scoped-to-the-for', "a name declared in a `for` statement's init is a name of that statement only",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nfunc helper() int { return 1 }\n\ntype T struct{}\n\nfunc (*T) Close() int { return 0 }\n\nfunc mk() *T { return &T{} }\n\nfunc Run() int {\n\tfor helper := mk(); helper != nil; helper = nil {\n\t\thelper.Close()\n\t}\n\treturn helper()\n}\n'},
     [('Run', 'calls', 'T.Close'), ('Run', 'calls', 'helper')], [],
     source='spec Blocks (each for statement is in its own implicit block)')
case('go', 'select-clause-is-a-block', 'each clause of a `select` is a block of its own: a name its case declares is none after the statement',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nfunc helper() int { return 1 }\n\ntype T struct{}\n\nfunc (*T) Close() int { return 0 }\n\nfunc Run(ch chan *T) int {\n\tselect {\n\tcase helper := <-ch:\n\t\thelper.Close()\n\tdefault:\n\t}\n\treturn helper()\n}\n'},
     [('Run', 'calls', 'T.Close'), ('Run', 'calls', 'helper')], [],
     source='spec Blocks (each clause in a select statement acts as an implicit block)')
case('go', 'import-name-is-a-name-of-its-file', 'the name of an import is a name of its file only: two files may bind one name to two packages',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'util/u.go': 'package util\n\nfunc F() int { return 1 }\n', 'other/o.go': 'package other\n\nfunc F() int { return 2 }\n', 'a.go': 'package app\n\nimport "example.com/app/util"\n\nfunc A() int {\n\treturn util.F()\n}\n', 'b.go': 'package app\n\nimport util "example.com/app/other"\n\nfunc B() int {\n\treturn util.F()\n}\n'},
     [('A', 'calls', 'util/u.go:F'), ('B', 'calls', 'other/o.go:F')], [('A', 'calls', 'other/o.go:F'), ('B', 'calls', 'util/u.go:F')],
     source='spec Declarations_and_scope (scope of the package name of an imported package); go/types resolver.go collectObjects (fileScope per file)')
case('go', 'receiver-hides-a-function', 'a receiver is a name of the method body: it hides a package function of its name',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nfunc run() int { return 0 }\n\ntype T struct{}\n\nfunc (T) Step() int { return 1 }\n\nfunc (run T) Do() int {\n\treturn run.Step()\n}\n'},
     [('T.Do', 'calls', 'T.Step')], [('T.Do', 'calls', 'run')],
     source='spec Declarations_and_scope (receiver); go/types signature.go collectRecv')
case('go', 'result-variable-hides-a-function', 'a named result is a name of the function body: it hides a package function of its name',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nfunc helper() int { return 0 }\n\ntype T struct{}\n\nfunc (*T) Close() {}\n\nfunc Run() (helper *T) {\n\thelper = &T{}\n\thelper.Close()\n\treturn\n}\n'},
     [('Run', 'calls', 'T.Close')], [('Run', 'calls', 'helper')],
     source='spec Declarations_and_scope (result variable)')
case('go', 'receiver-type-parameter-hides-a-type', 'a type parameter declared by a receiver hides a package type of its name in the method',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Runner interface{ Run() int }\n\ntype T struct{}\n\nfunc (T) Run() int { return 1 }\n\ntype Box[P Runner] struct{ v P }\n\nfunc (b Box[T]) Do() int {\n\treturn b.v.Run()\n}\n'},
     [('Box.Do', 'calls', 'Runner.Run')], [('Box.Do', 'calls', 'T.Run')],
     source='spec Declarations_and_scope (type parameter declared by a method receiver); go/types resolver.go unpackRecv', since='1.18')
case('go', 'type-parameter-of-a-type-hides-a-type', "a type parameter of a type is a name from the type's name to the end of its declaration: it hides a package type there",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Runner interface{ Run() int }\n\ntype T struct{}\n\nfunc (T) Run() int { return 1 }\n\ntype Box[T Runner] struct{ v T }\n\nfunc Do(b Box[Runner]) int {\n\treturn b.v.Run()\n}\n'},
     [('Do', 'calls', 'Runner.Run')], [('Do', 'calls', 'T.Run'), ('Box', 'references', 'a.go:T')],
     source='spec Declarations_and_scope (type parameter of a type); go/types decl.go typeDecl (openScope "type parameters")', since='1.18')
case('go', 'var-spec-scope-starts-after', 'a variable declared with `var` in a function is in scope after its VarSpec: its own value names the outer one',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nfunc helper() int { return 1 }\n\nfunc Run() int {\n\tvar helper = helper()\n\treturn helper\n}\n'},
     [('Run', 'calls', 'helper')], [],
     source='spec Declarations_and_scope; go/types decl.go declStmt (scopePos := d.spec.End())')
case('go', 'use-before-a-local-declaration-names-the-outer', 'a name used before a local of its name is declared means the outer one',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nfunc helper() int { return 1 }\n\nfunc Run() int {\n\tn := helper()\n\thelper := 2\n\treturn n + helper\n}\n'},
     [('Run', 'calls', 'helper')], [],
     source='spec Declarations_and_scope; go/types scope.go LookupParent (an object is found only when its scopePos is at or before the use)')
case('go', 'label-is-no-identifier', 'a label conflicts with no identifier: a call of a function named like a label is the function',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nfunc helper() int { return 1 }\n\nfunc Run() int {\nhelper:\n\tfor {\n\t\tbreak helper\n\t}\n\treturn helper()\n}\n'},
     [('Run', 'calls', 'helper')], [],
     source="spec Label_scopes; go/types object.go Label (a label is its own kind of object, in the function's label scope)")
case('go', 'predeclared-name-redeclared-in-the-package', 'a function of the package named like a predeclared one hides it: `max(a, b)` is the package function',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nfunc max(a, b int) int {\n\tif a > b {\n\t\treturn a\n\t}\n\treturn b\n}\n\nfunc Run() int {\n\treturn max(1, 2)\n}\n'},
     [('Run', 'calls', 'max')], [],
     source='spec Declarations_and_scope (scope of a predeclared identifier is the universe block, outermost); spec Predeclared_identifiers')
case('go', 'dot-import-takes-only-exported-names', "a dot-import declares only the exported names of the package: an unexported name of its own is the file's own",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'util/u.go': 'package util\n\nfunc helper() int { return 1 }\n\nfunc Public() int { return helper() }\n', 'a.go': 'package app\n\nimport . "example.com/app/util"\n\nfunc helper() int { return 2 }\n\nfunc Run() int {\n\treturn helper() + Public()\n}\n'},
     [('Run', 'calls', 'a.go:helper'), ('Run', 'calls', 'Public')], [('Run', 'calls', 'util/u.go:helper')],
     source='spec Exported_identifiers; go/types resolver.go collectObjects (if token.IsExported(name))')
case('go', 'unexported-names-of-two-packages-differ', 'an unexported name of another package is a different identifier: a selector finds the one of its own package at the same depth',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'util/u.go': 'package util\n\ntype A struct{}\n\nfunc (A) helper() int { return 1 }\n\nfunc (a A) Public() int { return a.helper() }\n', 'a.go': 'package app\n\nimport "example.com/app/util"\n\ntype L struct{}\n\nfunc (L) helper() int { return 2 }\n\ntype B struct {\n\tutil.A\n\tL\n}\n\nfunc Run(b B) int {\n\treturn b.helper()\n}\n'},
     [('Run', 'calls', 'L.helper')], [('Run', 'calls', 'util/u.go:A.helper')],
     source='spec Uniqueness_of_identifiers; go/types object.go sameId / Id (an unexported name is qualified by its package)')
case('go', 'constant-takes-the-type-of-its-value', 'a constant declared without a type takes the type of its expression',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Color int\n\nconst Red Color = 1\n\nconst Default = Red\n\nfunc (c Color) Name() string { return "c" }\n\nfunc Run() string {\n\treturn Default.Name()\n}\n'},
     [('Run', 'calls', 'Color.Name')], [],
     source='spec Constant_declarations; go/types decl.go constDecl (obj.typ from the init expression)')
case('go', 'implicitly-repeated-constant-has-the-type', 'a constant whose expression list is omitted repeats the previous list and its type: it has the type of the first',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Color int\n\nconst (\n\tRed Color = iota\n\tGreen\n)\n\nfunc (c Color) Name() string { return "c" }\n\nfunc Run() string {\n\treturn Green.Name()\n}\n'},
     [('Run', 'calls', 'Color.Name')], [],
     source='spec Constant_declarations; go/types decl.go constDecl (inherited), resolver.go constDecl d.typ / d.inherited')
case('go', 'declared-type-wins-over-the-value', "a variable declared with a type has that type, whatever its value: a method called on it is the declared type's",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Speaker interface{ Speak() int }\n\ntype Dog struct{}\n\nfunc (*Dog) Speak() int { return 1 }\n\nfunc Run() int {\n\tvar s Speaker = &Dog{}\n\treturn s.Speak()\n}\n'},
     [('Run', 'calls', 'Speaker.Speak')], [('Run', 'calls', 'Dog.Speak')],
     source="spec Variable_declarations; go/types decl.go varDecl (the declared type is used before the value's)")
case('go', 'var-multiple-results', "`var a, b = f()` gives each name its result's type",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Conn struct{}\n\nfunc (*Conn) Close() error { return nil }\n\nfunc Open() (*Conn, error) { return &Conn{}, nil }\n\nfunc Run() error {\n\tvar c, err = Open()\n\tif err != nil {\n\t\treturn err\n\t}\n\treturn c.Close()\n}\n'},
     [('Run', 'calls', 'Conn.Close')], [],
     source='spec Variable_declarations; go/types decl.go declStmt (n:1 varDecl with lhs), assignments.go initVars')
case('go', 'var-comma-ok-assertion', '`var t, ok = x.(T)` gives t the type T',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Dog struct{}\n\nfunc (*Dog) Bark() int { return 1 }\n\nfunc Run(x any) int {\n\tvar d, ok = x.(*Dog)\n\tif !ok {\n\t\treturn 0\n\t}\n\treturn d.Bark()\n}\n'},
     [('Run', 'calls', 'Dog.Bark')], [],
     source='spec Variable_declarations')
case('go', 'short-var-redeclaration-keeps-the-variable', 'a short variable declaration redeclaring a variable of the block assigns it: the variable keeps its declared type',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Speaker interface{ Speak() int }\n\ntype Dog struct{}\n\nfunc (*Dog) Speak() int { return 1 }\n\nfunc Run() int {\n\tvar s Speaker\n\ts, n := &Dog{}, 1\n\treturn s.Speak() + n\n}\n'},
     [('Run', 'calls', 'Speaker.Speak')], [('Run', 'calls', 'Dog.Speak')],
     source='spec Short_variable_declarations; go/types assignments.go shortVarDecl (scope.Lookup, recordUse of the old variable)')
case('go', 'generic-function-instantiation', 'a generic function written with type arguments, `Map[int](x)`, is a call of that function',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nfunc Map[T any](xs []T, f func(T) T) []T {\n\treturn xs\n}\n\nfunc Run(xs []int) []int {\n\treturn Map[int](xs, func(x int) int { return x })\n}\n'},
     [('Run', 'calls', 'Map')], [],
     source='spec Function_declarations (a generic function must be instantiated before it can be called); spec Instantiations', since='1.18')
case('go', 'method-in-another-file', 'a method is bound to its receiver base type wherever in the package it is declared',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype T struct{}\n\ntype U struct{}\n\nfunc (U) Go() int { return 0 }\n', 'b.go': 'package app\n\nfunc (T) Go() int { return 1 }\n', 'c.go': 'package app\n\nfunc Run(t T) int {\n\treturn t.Go()\n}\n'},
     [('Run', 'calls', 'T.Go')], [('Run', 'calls', 'U.Go')],
     source='spec Method_declarations; go/types resolver.go collectObjects (methods associated with base type names after all files)')
case('go', 'method-of-a-generic-type', 'a method declared for a generic type `S[T]` is a method of S on every instantiation',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Stack[T any] struct{ items []T }\n\nfunc (s *Stack[T]) Push(x T) { s.items = append(s.items, x) }\n\ntype List[T any] struct{ items []T }\n\nfunc (l *List[T]) Push(x T) { l.items = append(l.items, x) }\n\nfunc Run() {\n\tvar s Stack[int]\n\ts.Push(1)\n}\n'},
     [('Run', 'calls', 'Stack.Push')], [('Run', 'calls', 'List.Push')],
     source='spec Method_declarations; go/types resolver.go unpackRecv', since='1.18')
case('go', 'method-on-an-alias-receiver', "a method declared on an alias's name is a method of the aliased defined type",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype T struct{}\n\nfunc (T) M() int { return 1 }\n\ntype U struct{}\n\ntype A = U\n\nfunc (A) M() int { return 2 }\n\nfunc Run(u U) int {\n\treturn u.M()\n}\n'},
     [('Run', 'calls', 'A.M')], [('Run', 'calls', 'T.M')],
     source='go/types resolver.go resolveBaseTypeName', since='1.9')
case('go', 'short-var-redeclares-a-parameter', "`r, n := open()` in the function body redeclares the parameter r: it keeps the type Reader, so `r.Read()` is Reader's method",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Reader interface{ Read() int }\n\ntype File struct{}\n\nfunc (File) Read() int { return 1 }\n\nfunc open() (File, int) { return File{}, 0 }\n\nfunc Run(r Reader) int {\n\tr, n := open()\n\treturn r.Read() + n\n}\n'},
     [('Run', 'calls', 'Reader.Read')], [('Run', 'calls', 'File.Read')],
     source='spec Short_variable_declarations')
case('go', 'parameter-named-like-its-type', "in `func use(conf conf)` and `func (conf conf) Other()` the type is the package type conf and `conf.Run()` in the body is conf's method, not the package function Run",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype conf struct{}\n\nfunc (conf) Run() int { return 1 }\n\nfunc Run() int { return 2 }\n\nfunc use(conf conf) int {\n\treturn conf.Run()\n}\n\nfunc (conf conf) Other() int {\n\treturn conf.Run()\n}\n'},
     [('use', 'references', 'conf'), ('use', 'calls', 'conf.Run'), ('conf.Other', 'calls', 'conf.Run')], [('use', 'calls', 'a.go:Run'), ('conf.Other', 'calls', 'a.go:Run')],
     source='spec Declarations_and_scope')
case('go', 'predeclared-error-method', "`err.Error()` on a parameter of type error is the universe interface's method: no project type's Error method is meant",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype MyErr struct{}\n\nfunc (MyErr) Error() string { return "x" }\n\nfunc Describe(err error) string {\n\treturn err.Error()\n}\n'},
     [], [('Describe', 'calls', 'MyErr.Error')],
     source='spec Predeclared_identifiers')
case('go', 'receiver-type-parameter-renamed', "in `func (b Box[U]) Go(u U)` U is the receiver's own name for T: `u.Run()` is the method of T's constraint Runner",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Runner interface{ Run() int }\n\ntype Box[T Runner] struct{ v T }\n\nfunc (b Box[U]) Go(u U) int {\n\treturn u.Run()\n}\n\ntype Other struct{}\n\nfunc (Other) Run() int { return 2 }\n'},
     [('Box.Go', 'calls', 'Runner.Run')], [('Box.Go', 'calls', 'Other.Run')],
     source='spec Method_declarations; go/types resolver.go unpackRecv', since='1.18')
case('go', 'receiver-alias-chain-and-pointer', "`type A = B; type B = *T; func (a A) Go()` binds Go to T: `t.Go()` on a *T is T's method, not another type's Go",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype T struct{}\n\nfunc (T) Run() int { return 1 }\n\ntype A = B\ntype B = *T\n\nfunc (a A) Go() int { return 2 }\n\nfunc Use(t *T) int {\n\treturn t.Go() + t.Run()\n}\n\ntype U struct{}\n\nfunc (U) Go() int { return 3 }\n'},
     [('Use', 'calls', 'T.Go'), ('Use', 'calls', 'T.Run')], [('Use', 'calls', 'U.Go')],
     source='go/types resolver.go resolveBaseTypeName')
case('go', 'comma-ok-map-and-receive', 'in `v, ok := m[k]` and `w, ok := <-ch` the first name has the value / element type',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Item struct{}\n\nfunc (Item) Run() int { return 1 }\n\ntype Other struct{}\n\nfunc (Other) Run() int { return 2 }\n\nfunc Use(m map[string]Item, ch chan Item) int {\n\tv, ok := m["a"]\n\tw, ok2 := <-ch\n\tif ok && ok2 {\n\t\treturn v.Run() + w.Run()\n\t}\n\treturn 0\n}\n'},
     [('Use', 'calls', 'Item.Run')], [('Use', 'calls', 'Other.Run')],
     source='spec Variable_declarations; go/types assignments.go')
case('go', 'field-named-like-its-type', "`type S struct{ conf conf }`: the field's type is the package type conf, and `s.conf.Run()` is conf's method",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype conf struct{}\n\nfunc (conf) Run() int { return 1 }\n\ntype S struct{ conf conf }\n\nfunc (s S) Go() int { return s.conf.Run() }\n\ntype Other struct{}\n\nfunc (Other) Run() int { return 2 }\n'},
     [('S.Go', 'calls', 'conf.Run')], [('S.Go', 'calls', 'Other.Run')],
     source='spec Declarations_and_scope; Selectors')
case('go', 'local-hides-an-imported-package', "a variable or parameter of a function hides an imported package's name inside the function: the selector is the value's method",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'util/u.go': 'package util\n\nfunc Run() int { return 1 }\n', 'a.go': 'package app\n\nimport "example.com/app/util"\n\ntype Thing struct{}\n\nfunc (t Thing) Run() int { return 2 }\n\nfunc newThing() Thing { return Thing{} }\n\nfunc viaLocal() int {\n\tutil := newThing()\n\treturn util.Run()\n}\n\nfunc viaParam(util Thing) int {\n\treturn util.Run()\n}\n\nfunc viaImport() int {\n\treturn util.Run()\n}\n'},
     [('viaLocal', 'calls', 'Thing.Run'), ('viaParam', 'calls', 'Thing.Run'), ('viaImport', 'calls', 'util/u.go:Run')], [('viaLocal', 'calls', 'util/u.go:Run'), ('viaParam', 'calls', 'util/u.go:Run'), ('viaImport', 'calls', 'Thing.Run')],
     source='spec Declarations_and_scope (the scope of the package name of an imported package; an identifier declared in a block may be redeclared in an inner block)')
case('go', 'local-hides-a-type-name', "a variable declared in a function hides a package type of its name: a selector on it is a method of the value's type",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Job struct{}\n\nfunc (j Job) Run() int { return 1 }\n\ntype Other struct{}\n\nfunc (o Other) Run() int { return 2 }\n\nfunc use() int {\n\tJob := Other{}\n\treturn Job.Run()\n}\n'},
     [('use', 'calls', 'Other.Run')], [('use', 'calls', 'Job.Run')],
     source='spec Declarations_and_scope (inner declaration denotes the inner entity)')
case('go', 'function-literal-parameter-hides-a-function', 'a parameter of a function literal hides a package function of its name inside the literal',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nfunc helper(n int) int { return n }\n\nfunc apply() int {\n\tf := func(helper func() int) int {\n\t\treturn helper()\n\t}\n\treturn f(nil)\n}\n'},
     [], [('apply', 'calls', 'helper'), ('apply', 'references', 'helper')],
     source='spec Declarations_and_scope (function parameter; function body of a function literal)')
case('go', 'short-var-pairs-values-by-position', 'with as many values as names each name takes the type of the value at its position',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype A struct{}\n\nfunc (a A) Go() int { return 1 }\n\ntype B struct{}\n\nfunc (b B) Go() int { return 2 }\n\nfunc newA() A { return A{} }\nfunc newB() B { return B{} }\n\nfunc pair() int {\n\ta, b := newA(), newB()\n\treturn a.Go() + b.Go()\n}\n'},
     [('pair', 'calls', 'A.Go'), ('pair', 'calls', 'B.Go')], [],
     source='spec Short_variable_declarations (IdentifierList := ExpressionList, one value per name)')
case('go', 'embedded-generic-instantiation', 'an embedded `Base[int]` promotes the methods the receiver `Base[T]` declared',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Base[T any] struct{ v T }\n\nfunc (b *Base[T]) Get() T { return b.v }\n\ntype Wrapper struct {\n\tBase[int]\n}\n\nfunc use(w *Wrapper) int {\n\treturn w.Get()\n}\n'},
     [('use', 'calls', 'Base.Get')], [],
     source='spec Struct_types (embedded field) with Method_declarations (receiver T[P])')
case('go', 'alias-names-its-type', 'an alias declaration is a use of the type it names',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype polar struct{}\n\ntype Polar = polar\n\ntype Other struct{}\n'},
     [('Polar', 'references', 'polar')], [],
     source='spec Alias_declarations')
case('go', 'alias-method-call', 'a method declared on an alias is a method of the aliased type: it is found on a value written with either name',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype New struct{}\n\ntype Old = New\n\nfunc (o Old) Hello() {}\n\ntype Other struct{}\n\nfunc (o Other) Hello() {}\n\nfunc Run() {\n\tvar n New\n\tn.Hello()\n\tvar o Old\n\to.Hello()\n}\n'},
     [('Run', 'calls', 'Old.Hello')], [('Run', 'calls', 'Other.Hello')],
     source='spec Alias_declarations; go/types alias.go Unalias')
case('go', 'alias-of-instance', "an alias of an instantiated generic type has that instance's methods",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Box[T any] struct {\n\tv T\n}\n\nfunc (b Box[T]) Get() T {\n\treturn b.v\n}\n\ntype IntBox = Box[int]\n\ntype Other struct{}\n\nfunc (o Other) Get() int { return 0 }\n\nfunc Run(b IntBox) int {\n\treturn b.Get()\n}\n'},
     [('Run', 'calls', 'Box.Get')], [('Run', 'calls', 'Other.Get')],
     source='spec Alias_declarations; go/types alias.go')
case('go', 'generic-alias', "a generic alias, instantiated, is the type it stands for: its methods are that type's",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.24\n', 'a.go': 'package app\n\ntype Item struct{}\n\ntype Box[T any] struct {\n\tv T\n}\n\nfunc (b Box[T]) Get() T {\n\treturn b.v\n}\n\ntype Pair[T any] = Box[T]\n\ntype Other struct{}\n\nfunc (o Other) Get() int { return 0 }\n\nfunc Run(p Pair[Item]) Item {\n\treturn p.Get()\n}\n'},
     [('Run', 'calls', 'Box.Get'), ('Run', 'references', 'Pair')], [('Run', 'calls', 'Other.Get')],
     source='spec Alias_declarations (generic aliases); go/types alias.go newAliasInstance', since='1.24')
case('go', 'defined-type-does-not-inherit', 'a defined type does not inherit the methods of the type it is defined from: its own method of the name is the one called',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Base struct{}\n\nfunc (Base) Run() {}\n\ntype Derived Base\n\nfunc (Derived) Run() {}\n\nfunc Use(d Derived) {\n\td.Run()\n}\n'},
     [('Use', 'calls', 'Derived.Run')], [('Use', 'calls', 'Base.Run')],
     source='spec Type_definitions (it does not inherit any methods bound to the given type)')
case('go', 'defined-interface-keeps-methods', "the method set of an interface type is unchanged by a type definition from it: its methods are the interface's",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Block interface {\n\tEncrypt()\n}\n\ntype MyBlock Block\n\ntype Other struct{}\n\nfunc (Other) Encrypt() {}\n\nfunc Use(b MyBlock) {\n\tb.Encrypt()\n}\n'},
     [('Use', 'calls', 'Block.Encrypt')], [('Use', 'calls', 'Other.Encrypt')],
     source='spec Type_definitions (MyBlock has the same method set as Block)')
case('go', 'instance-local-method', "a method called on a value of an instantiated generic type is the generic type's method",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Item struct{}\n\ntype Box[T any] struct {\n\tv T\n}\n\nfunc (b *Box[T]) Get() T {\n\treturn b.v\n}\n\ntype Other struct{}\n\nfunc (o *Other) Get() int {\n\treturn 0\n}\n\nfunc Run() Item {\n\tb := Box[Item]{}\n\treturn b.Get()\n}\n'},
     [('Run', 'calls', 'Box.Get')], [('Run', 'calls', 'Other.Get')],
     source='spec Type_definitions (methods of a generic type); go/types named.go expandMethod')
case('go', 'receiver-redeclares-type-parameters', "a method of a generic type does not see the type declaration's type parameters: a name its receiver does not declare is the package's",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype E struct{}\n\ntype Box[E any] struct {\n\tv E\n}\n\nfunc (b Box[K]) Set(x E) {}\n'},
     [('Box.Set', 'references', 'E')], [('Box', 'references', 'E')],
     source='spec Type_definitions (receivers declare the type parameters); Declarations_and_scope')
case('go', 'type-declaration-parameter-hides', 'a type parameter of a type declaration hides a package type of its name in the declaration',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Config struct{}\n\ntype Box[Config any] struct {\n\tc Config\n}\n\ntype Plain struct {\n\tc Config\n}\n'},
     [('Plain', 'references', 'Config')], [('Box', 'references', 'Config')],
     source='spec Type_parameter_declarations; Declarations_and_scope')
case('go', 'receiver-type-parameter-hides', "a type parameter declared by a method's receiver hides a package type of its name in the method only: the method's other uses of the name are the package's",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Config struct{}\n\ntype Box[T any] struct {\n\tv T\n}\n\nfunc (b Box[Config]) Get() Config {\n\treturn b.v\n}\n\nfunc (b Box[T]) Plain(c Config) {}\n'},
     [('Box.Plain', 'references', 'Config')], [('Box.Get', 'references', 'Config')],
     source='spec Type_parameter_declarations (receiver specification); Declarations_and_scope')
case('go', 'constraint-forward-reference', 'the type parameters of one list are all in scope in each constraint of it, including those declared after',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype E struct{}\n\nfunc Each[S ~[]E, E any](s S) {}\n\nfunc Plain(e E) {}\n'},
     [('Plain', 'references', 'E')], [('Each', 'references', 'E')],
     source='spec Type_parameter_declarations ([S ~[]E, E any]); Declarations_and_scope')
case('go', 'instantiated-constraint', "a value of a type parameter constrained by an instantiated generic interface has that interface's methods",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Item struct{}\n\ntype Getter[V any] interface {\n\tGet() V\n}\n\ntype Other struct{}\n\nfunc (Other) Get() int { return 0 }\n\nfunc Use[G Getter[Item]](g G) {\n\tg.Get()\n}\n'},
     [('Use', 'references', 'Getter'), ('Use', 'calls', 'Getter.Get')], [('Use', 'calls', 'Other.Get')],
     source='spec Type_parameter_declarations ([P Constraint[int]]); go/types typeparam.go iface')
case('go', 'constraint-names-are-uses', 'the interface a type parameter is constrained by, and the types of the union such an interface lists, are uses of those names',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Int int\n\ntype Float float64\n\ntype Number interface {\n\tInt | Float\n}\n\nfunc Sum[T Number](xs []T) T {\n\tvar z T\n\treturn z\n}\n'},
     [('Sum', 'references', 'Number'), ('Number', 'embeds', 'Int'), ('Number', 'embeds', 'Float')], [],
     source='spec Type_constraints')
case('go', 'constraint-embeds-interface', 'a value of a type parameter has the methods of the interfaces its constraint embeds',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Runner interface {\n\tRun()\n}\n\ntype Runnable interface {\n\tRunner\n\tcomparable\n}\n\ntype Other struct{}\n\nfunc (Other) Run() {}\n\nfunc Each[T Runnable](x T) {\n\tx.Run()\n}\n'},
     [('Each', 'calls', 'Runner.Run')], [('Each', 'calls', 'Other.Run')],
     source='spec Type_constraints; go/types typeparam.go iface')
case('go', 'generic-type-instance-is-a-use', 'a generic type written with type arguments is a use of the generic type and of each argument',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Item struct{}\n\ntype Box[T any] struct {\n\tv T\n}\n\ntype Other struct{}\n\nfunc Make() int {\n\tvar b Box[Item]\n\t_ = b\n\treturn 0\n}\n'},
     [('Make', 'references', 'Box'), ('Make', 'references', 'Item')], [],
     source='spec Instantiations; go/types typexpr.go instantiatedType')
case('go', 'instance-field-and-result-substituted', "instantiating substitutes the type arguments: a field or result typed by the type parameter is the argument's type",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Item struct{}\n\nfunc (Item) Run() {}\n\ntype Plain struct{}\n\nfunc (Plain) Run() {}\n\ntype Box[T any] struct {\n\tv T\n}\n\nfunc (b Box[T]) Get() T {\n\treturn b.v\n}\n\nfunc ViaField(b Box[Item]) {\n\tb.v.Run()\n}\n\nfunc ViaResult(b Box[Item]) {\n\tb.Get().Run()\n}\n'},
     [('ViaField', 'calls', 'Item.Run'), ('ViaResult', 'calls', 'Item.Run'), ('ViaResult', 'calls', 'Box.Get')], [('ViaField', 'calls', 'Plain.Run'), ('ViaResult', 'calls', 'Plain.Run')],
     source='spec Instantiations (each type argument is substituted for its type parameter)')
case('go', 'explicit-instantiation-result', "a call of a generic function with an explicit type argument gives the argument's type as result",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Item struct{}\n\nfunc (Item) Run() {}\n\ntype Plain struct{}\n\nfunc (Plain) Run() {}\n\nfunc Zero[T any]() T {\n\tvar z T\n\treturn z\n}\n\nfunc Use() {\n\tZero[Item]().Run()\n}\n'},
     [('Use', 'calls', 'Zero'), ('Use', 'calls', 'Item.Run')], [('Use', 'calls', 'Plain.Run')],
     source='spec Instantiations; go/types instantiate.go')
case('go', 'explicit-function-instantiation', 'a generic function of another package called with explicit type arguments is that function, not the same-named one of the importing package',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nimport "example.com/app/util"\n\nfunc Map[T any](x T) T {\n\treturn x\n}\n\nfunc Run() int {\n\treturn util.Map[int](1)\n}\n\nfunc Value() func(int) int {\n\treturn util.Map[int]\n}\n', 'util/u.go': 'package util\n\nfunc Map[T any](x T) T {\n\treturn x\n}\n'},
     [('Run', 'calls', 'util/u.go:Map')], [('Run', 'calls', 'a.go:Map')],
     source='spec Instantiations (sum[float64](2.0, 3)); go/types typexpr.go ident, selector')
case('go', 'method-expression-on-instance', "`Box[T].M` is a method expression of the generic type's method",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Item struct{}\n\ntype Box[T any] struct {\n\tv T\n}\n\nfunc (b Box[T]) Get() T {\n\treturn b.v\n}\n\ntype Other struct{}\n\nfunc (o Other) Get() int { return 0 }\n\nfunc Run() func(Box[Item]) Item {\n\treturn Box[Item].Get\n}\n'},
     [('Run', 'references', 'Box.Get')], [('Run', 'references', 'Other.Get')],
     source='spec Method_expressions; Instantiations')
case('go', 'generic-function-as-argument', 'a generic function handed over uninstantiated is a use of that function',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nfunc Double[T ~int](x T) T {\n\treturn x * 2\n}\n\nfunc apply(f func(int) int) int {\n\treturn f(1)\n}\n\nfunc Run() int {\n\treturn apply(Double)\n}\n'},
     [('Run', 'indirect_call', 'Double')], [],
     source='spec Type_inference (a generic function passed as an argument)')
case('go', 'imported-generic-type-instance', "a generic type of another package, instantiated, gives that type's methods: not those of the importing package's same-named type",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nimport "example.com/app/util"\n\ntype Box[T any] struct{}\n\ntype Item struct{}\n\nfunc Run() {\n\tb := util.Box[Item]{}\n\tb.Get()\n}\n', 'util/u.go': 'package util\n\ntype Box[T any] struct {\n\tv T\n}\n\nfunc (b Box[T]) Get() T {\n\treturn b.v\n}\n'},
     [('Run', 'calls', 'util/u.go:Box.Get')], [('Run', 'calls', 'a.go:Box'), ('Run', 'references', 'a.go:Box')],
     source='spec Instantiations; go/types typexpr.go instantiatedType (a selector as the generic type)')
case('go', 'embedded-instance', "an embedded instance of a generic type is a field named by the type's name; its methods are promoted",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Item struct{}\n\ntype Box[T any] struct {\n\tv T\n}\n\nfunc (b Box[T]) Get() T {\n\treturn b.v\n}\n\ntype Wrap struct {\n\tBox[Item]\n}\n\nfunc Run(w Wrap) Item {\n\treturn w.Get()\n}\n'},
     [('Wrap', 'embeds', 'Box'), ('Run', 'calls', 'Box.Get')], [],
     source='spec Struct_types (embedded field named by the type name, without type arguments); Selectors')
case('go', 'array-length-names-a-constant', "an array length written as a name is a use of the constant of that name in scope: a constant of the function hides the package's",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nconst Size = 4\n\ntype Buf [Size]byte\n\nfunc Make() [Size]int {\n\tvar b [Size]int\n\treturn b\n}\n\nfunc Local() int {\n\tconst Size = 8\n\tvar b [Size]int\n\treturn len(b)\n}\n'},
     [('Make', 'reads', 'Size'), ('Buf', 'reads', 'Size')], [('Local', 'reads', 'Size')],
     source='go/types typexpr.go arrayLength')
case('go', 'defined-type-keeps-the-fields', 'a field of a struct is a field of a type defined from it',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.24\n', 'a.go': 'package app\n\ntype Point struct{ x int }\n\ntype Other struct{ x int }\n\ntype polar Point\n\nfunc Use(p polar) int {\n\treturn p.x\n}\n'},
     [('Use', 'reads', 'Point.x')], [('Use', 'reads', 'Other.x')],
     source='spec Type_definitions (same underlying type and operations)')
case('go', 'defined-type-keeps-the-embedding', "a type defined from a struct with an embedded field has the embedded type's promoted methods",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.24\n', 'a.go': 'package app\n\ntype Mutex struct{}\n\nfunc (m *Mutex) Lock() {}\n\ntype Other struct{}\n\nfunc (o *Other) Lock() {}\n\ntype Printable struct {\n\tMutex\n}\n\ntype Copy Printable\n\nfunc Use(c *Copy) {\n\tc.Lock()\n}\n'},
     [('Use', 'calls', 'Mutex.Lock')], [('Use', 'calls', 'Other.Lock')],
     source='spec Type_definitions (PrintableMutex embeds Mutex)')
case('go', 'alias-of-an-imported-type', "a value of an alias of pkg.T answers T's methods",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.24\n', 'a.go': 'package app\n\nimport "example.com/app/util"\n\ntype Link = util.Conn\n\ntype Other struct{}\n\nfunc (o *Other) Close() {}\n\nfunc Use(l *Link) {\n\tl.Close()\n}\n', 'util/u.go': 'package util\n\ntype Conn struct{}\n\nfunc (c *Conn) Close() {}\n'},
     [('Use', 'calls', 'Conn.Close')], [('Use', 'calls', 'Other.Close')],
     source='spec Alias_declarations')
case('go', 'constraint-of-an-imported-package', "a value of a type parameter constrained by pkg.I has I's methods",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.24\n', 'a.go': 'package app\n\nimport "example.com/app/util"\n\ntype Other struct{}\n\nfunc (o Other) Run() {}\n\nfunc Each[T util.Runner](x T) {\n\tx.Run()\n}\n', 'util/u.go': 'package util\n\ntype Runner interface {\n\tRun()\n}\n'},
     [('Each', 'calls', 'Runner.Run'), ('Each', 'references', 'Runner')], [('Each', 'calls', 'Other.Run')],
     source='spec Type_constraints; go/types typeparam.go iface')
case('go', 'core-type-element-of-a-type-parameter', "ranging over a value of S ~[]E gives E, whose constraint's methods are called on it",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.24\n', 'a.go': 'package app\n\ntype Shape interface {\n\tArea() int\n}\n\ntype Sq struct{}\n\nfunc (Sq) Area() int { return 1 }\n\nfunc Total[S ~[]E, E Shape](s S) int {\n\tn := 0\n\tfor _, e := range s {\n\t\tn += e.Area()\n\t}\n\treturn n\n}\n'},
     [('Total', 'calls', 'Shape.Area')], [('Total', 'calls', 'Sq.Area')],
     source='spec Type_parameter_declarations; spec Type_constraints (core type)')
case('go', 'explicit-type-argument-of-a-call', 'the type argument written in `Conv[Celsius](1)` is a use of Celsius, the call is of Conv',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.24\n', 'a.go': 'package app\n\ntype Celsius float64\n\ntype Fahrenheit float64\n\nfunc Conv[T any](x T) T {\n\treturn x\n}\n\nfunc Use() Celsius {\n\treturn Conv[Celsius](1)\n}\n'},
     [('Use', 'calls', 'Conv'), ('Use', 'references', 'Celsius')], [('Use', 'references', 'Fahrenheit')],
     source='spec Instantiations')
case('go', 'instantiated-function-value', '`f := Conv[int]` names Conv: a use of it, and `f(1)` is a call of Conv',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.24\n', 'a.go': 'package app\n\nfunc Conv[T any](x T) T {\n\treturn x\n}\n\nfunc Other(x int) int {\n\treturn x\n}\n\nfunc Use() int {\n\tf := Conv[int]\n\treturn f(1)\n}\n'},
     [('Use', 'references', 'Conv')], [('Use', 'calls', 'Other')],
     source='spec Instantiations')
case('go', 'blank-receiver-type-parameter', "`func (b Box[_]) Size()` is Box's method, found on a Box[int] value",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.24\n', 'a.go': 'package app\n\ntype Box[T any] struct{ v T }\n\nfunc (b Box[_]) Size() int { return 1 }\n\ntype Crate struct{}\n\nfunc (c Crate) Size() int { return 2 }\n\nfunc Use(b Box[int]) int {\n\treturn b.Size()\n}\n'},
     [('Use', 'calls', 'Box.Size')], [('Use', 'calls', 'Crate.Size')],
     source='spec Type_definitions; spec Type_parameter_declarations ([_ any])')
case('go', 'generic-method-result-chain', '`l.Next().Len()` where Next returns `*List[T]` is List.Len',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.24\n', 'a.go': 'package app\n\ntype List[T any] struct{ next *List[T] }\n\nfunc (l *List[T]) Next() *List[T] { return l.next }\n\nfunc (l *List[T]) Len() int { return 1 }\n\ntype Other struct{}\n\nfunc (o *Other) Len() int { return 0 }\n\nfunc Use(l *List[int]) int {\n\treturn l.Next().Len()\n}\n'},
     [('Use', 'calls', 'List.Len'), ('Use', 'calls', 'List.Next')], [('Use', 'calls', 'Other.Len')],
     source='spec Type_definitions; spec Instantiations')
case('go', 'embedded-alias-field', "a struct embedding `Alias` (type Alias = Base) has a field Alias, and Base's methods",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.24\n', 'a.go': 'package app\n\ntype Base struct{}\n\nfunc (Base) Hello() {}\n\ntype Alias = Base\n\ntype Other struct{}\n\nfunc (Other) Hello() {}\n\ntype S struct {\n\tAlias\n}\n\nfunc Use(s S) {\n\ts.Alias.Hello()\n}\n'},
     [('Use', 'calls', 'Base.Hello')], [('Use', 'calls', 'Other.Hello')],
     source='spec Alias_declarations; go/types typexpr.go (embedded field names)')
case('go', 'embedded-imported-type-field', "`s.Conn.Close()` for an embedded `*util.Conn` is Conn's method",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.24\n', 'a.go': 'package app\n\nimport "example.com/app/util"\n\ntype Other struct{}\n\nfunc (o *Other) Close() {}\n\ntype S struct {\n\t*util.Conn\n}\n\nfunc Use(s S) {\n\ts.Conn.Close()\n}\n', 'util/u.go': 'package util\n\ntype Conn struct{}\n\nfunc (c *Conn) Close() {}\n'},
     [('Use', 'calls', 'Conn.Close')], [('Use', 'calls', 'Other.Close')],
     source='spec Type_definitions; go/types typexpr.go (embedded field names)')
case('go', 'type-parameter-hides-a-function', 'a type parameter of a function hides a package function of its name: `Size(v)` in the function is a conversion, no call',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nfunc Size() int { return 1 }\n\nfunc Conv[Size ~int](v int) Size {\n\treturn Size(v)\n}\n\nfunc Plain() int {\n\treturn Size()\n}\n'},
     [('Plain', 'calls', 'Size')], [('Conv', 'calls', 'Size')],
     source='spec Declarations_and_scope (a type parameter of a function)', since='1.18')
case('go', 'field-of-a-type-parameter-type', "a field typed by the type parameter of a generic type, read in a method of it, has the methods of the parameter's constraint",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Runner interface{ Run() }\n\ntype Box[T Runner] struct{ v T }\n\nfunc (b Box[T]) Do() {\n\tb.v.Run()\n}\n'},
     [('Box.Do', 'calls', 'Runner.Run'), ('Box.Do', 'reads', 'Box.v')], [],
     source='spec Type_parameter_declarations (receiver type parameters)', since='1.18')
case('go', 'recursive-generic-type', "a recursive generic type's field typed `*Node[T]` has the type's methods",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Node[T any] struct {\n\tnext *Node[T]\n\tval  T\n}\n\nfunc (n *Node[T]) Last() *Node[T] { return n }\n\nfunc (n *Node[T]) Walk() *Node[T] {\n\treturn n.next.Last()\n}\n'},
     [('Node.Walk', 'calls', 'Node.Last'), ('Node.Walk', 'reads', 'Node.next')], [],
     source='spec Type_definitions (TreeNode)', since='1.18')
case('go', 'alias-chain', "an alias of an alias names the final type, whatever the order of the declarations: its methods are the type's",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype T struct{}\n\nfunc (T) Run() {}\n\ntype B = A\ntype A = T\n\nfunc Use(b B) {\n\tb.Run()\n}\n\ntype U struct{}\n\nfunc (U) Run() {}\n'},
     [('Use', 'calls', 'T.Run')], [('Use', 'calls', 'U.Run')],
     source='spec Alias_declarations; go/types alias.go (Unalias)')
case('go', 'alias-of-composite-type', "a range over an alias of `[]*T`, and a value of an alias of `*T`, have T's methods",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype T struct{}\n\nfunc (*T) Run() {}\n\ntype List = []*T\ntype Ptr = *T\n\nfunc Use(l List, p Ptr) {\n\tfor _, x := range l {\n\t\tx.Run()\n\t}\n\tp.Run()\n}\n\ntype U struct{}\n\nfunc (*U) Run() {}\n'},
     [('Use', 'calls', 'T.Run')], [('Use', 'calls', 'U.Run')],
     source='spec Alias_declarations (nodeList)')
case('go', 'defined-pointer-type-field', "a field read on a value of a defined pointer type is the struct's field",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype S struct{ x int }\n\ntype PS *S\n\nfunc Read(p PS) int {\n\treturn p.x\n}\n\ntype S2 struct{ x int }\n'},
     [('Read', 'reads', 'S.x')], [('Read', 'reads', 'S2.x')],
     source='spec Type_definitions (PtrMutex), Selectors')
case('go', 'pointer-constraint-method', "a call on a conversion to a type parameter constrained by `interface{ *T; Set() }` is the constraint's method",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Setter[T any] interface {\n\t*T\n\tSet()\n}\n\ntype Cfg struct{}\n\nfunc (*Cfg) Set() {}\n\nfunc Make[T any, PT Setter[T]]() T {\n\tvar v T\n\tPT(&v).Set()\n\treturn v\n}\n\nfunc (*Cfg) Other() {}\n'},
     [('Make', 'calls', 'Setter.Set')], [('Make', 'calls', 'Cfg.Set')],
     source='spec Type_constraints; go/types typeparam.go', since='1.18')
case('go', 'embedded-generic-interface', "the methods of an interface embedding an instantiated generic interface are the generic interface's",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Getter[T any] interface{ Get() T }\n\ntype IntGetter interface{ Getter[int] }\n\nfunc Read(g IntGetter) int {\n\treturn g.Get()\n}\n\ntype Other interface{ Get() string }\n'},
     [('Read', 'calls', 'Getter.Get')], [('Read', 'calls', 'Other.Get')],
     source='spec Instantiations; go/types named.go', since='1.18')
case('go', 'method-value-of-an-instance', "`b.Get` without a call, b of type `Box[int]`, is a use of Box's method",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Box[T any] struct{ v T }\n\nfunc (b Box[T]) Get() T { return b.v }\n\nfunc Use(b Box[int]) func() int {\n\treturn b.Get\n}\n'},
     [('Use', 'references', 'Box.Get')], [],
     source='spec Instantiations; Method_values', since='1.18')
case('go', 'map-value-of-an-instance', "`m[k].Get()` with m of type `map[string]Box[int]` calls the generic type's method",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Box[T any] struct{ v T }\n\nfunc (b Box[T]) Get() T { return b.v }\n\nfunc Use(m map[string]Box[int]) int {\n\treturn m["a"].Get()\n}\n'},
     [('Use', 'calls', 'Box.Get')], [],
     source='spec Instantiations; Index_expressions', since='1.18')
case('go', 'type-arguments-are-uses', "a generic type's name and each of its type arguments are uses",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Box[T any] struct {\n\tv T\n}\n\ntype Item struct{}\n\ntype Key struct{}\n\nfunc Use() {\n\tvar b Box[Item]\n\tm := map[Key]Box[Item]{}\n\t_, _ = b, m\n}\n'},
     [('Use', 'references', 'Box'), ('Use', 'references', 'Item'), ('Use', 'references', 'Key')], [],
     source='spec Types (TypeName [ TypeArgs ])', since='1.18')
case('go', 'generic-result-is-the-argument', '`b.Get()` on a `*Box[Item]` is an Item, so `.Run()` is Item.Run',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Box[T any] struct {\n\tv T\n}\n\nfunc (b *Box[T]) Get() T {\n\treturn b.v\n}\n\ntype Item struct{}\n\nfunc (Item) Run() int {\n\treturn 1\n}\n\ntype Other struct{}\n\nfunc (Other) Run() int {\n\treturn 2\n}\n\nfunc Use(b *Box[Item]) int {\n\treturn b.Get().Run()\n}\n'},
     [('Use', 'calls', 'Box.Get'), ('Use', 'calls', 'Item.Run')], [('Use', 'calls', 'Other.Run')],
     source='spec Types (instantiated types)', since='1.18')
case('go', 'alias-has-the-methods-of-its-type', 'a value of an alias type (also an alias of an alias) has the methods of the type the alias names',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Real struct{}\n\nfunc (Real) Name() string {\n\treturn "real"\n}\n\ntype Other struct{}\n\nfunc (Other) Name() string {\n\treturn "other"\n}\n\ntype First = Real\n\ntype Second = First\n\nfunc Use(a Second) string {\n\treturn a.Name()\n}\n'},
     [('Use', 'calls', 'Real.Name')], [('Use', 'calls', 'Other.Name')],
     source='spec Types, Type_identity (A0, A1 and []string are identical)')
case('go', 'conversion-gives-its-type', '`HandlerFunc(plain).Serve(1)` is the method of the converted-to type, and `plain` is a use',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype HandlerFunc func(int) int\n\nfunc (f HandlerFunc) Serve(x int) int {\n\treturn f(x)\n}\n\ntype Other struct{}\n\nfunc (Other) Serve(x int) int {\n\treturn x\n}\n\nfunc plain(x int) int {\n\treturn x\n}\n\nfunc Use() int {\n\treturn HandlerFunc(plain).Serve(1)\n}\n'},
     [('Use', 'calls', 'HandlerFunc.Serve'), ('Use', 'references', 'plain')], [('Use', 'calls', 'Other.Serve')],
     source='spec Types (a type may be denoted by a type name); go/types call.go conversion')
case('go', 'array-element-type', 'an element of `[3]*Cell`, of `[2][2]*Cell` and of a pointer to an array is a *Cell',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Cell struct{}\n\nfunc (*Cell) Touch() int {\n\treturn 1\n}\n\ntype Other struct{}\n\nfunc (*Other) Touch() int {\n\treturn 2\n}\n\nfunc Use() int {\n\tvar row [3]*Cell\n\tgrid := [2][2]*Cell{}\n\tp := &row\n\treturn row[0].Touch() + grid[1][0].Touch() + p[2].Touch()\n}\n'},
     [('Use', 'calls', 'Cell.Touch')], [('Use', 'calls', 'Other.Touch')],
     source='spec Array_types (ElementType, composed arrays)')
case('go', 'slice-of-slices', '`grid[0][1]` of `[][]*Cell` is a *Cell',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Cell struct{}\n\nfunc (*Cell) Touch() int {\n\treturn 1\n}\n\ntype Other struct{}\n\nfunc (*Other) Touch() int {\n\treturn 2\n}\n\nfunc Use(grid [][]*Cell) int {\n\treturn grid[0][1].Touch()\n}\n'},
     [('Use', 'calls', 'Cell.Touch')], [('Use', 'calls', 'Other.Touch')],
     source='spec Slice_types (composed slices)')
case('go', 'anonymous-struct-field', 'a field of a local declared with an unnamed struct type is of its declared type',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Repo struct{}\n\nfunc (*Repo) Find() int {\n\treturn 1\n}\n\ntype Cache struct{}\n\nfunc (*Cache) Find() int {\n\treturn 2\n}\n\nfunc Use() int {\n\tvar s struct {\n\t\tr *Repo\n\t}\n\treturn s.r.Find()\n}\n'},
     [('Use', 'calls', 'Repo.Find')], [('Use', 'calls', 'Cache.Find')],
     source='spec Struct_types (type literal), Types (TypeLit)')
case('go', 'embedded-pointer-and-qualified-field', "`*util.Tool` embedded is the field Tool, and `o.Tool.Run()` is util's Tool.Run",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'util/u.go': 'package util\n\ntype Tool struct{}\n\nfunc (*Tool) Run() int {\n\treturn 1\n}\n', 'a.go': 'package app\n\nimport "example.com/app/util"\n\ntype Tool struct{}\n\nfunc (*Tool) Run() int {\n\treturn 2\n}\n\ntype Outer struct {\n\t*util.Tool\n}\n\nfunc Use(o Outer) int {\n\treturn o.Tool.Run()\n}\n'},
     [('Use', 'calls', 'util/u.go:Tool.Run')], [('Use', 'calls', 'a.go:Tool.Run')],
     source='spec Struct_types (EmbeddedField); go/types struct.go embeddedFieldIdent')
case('go', 'embedded-interface-in-a-struct', "a method of an interface embedded in a struct is promoted: `c.Get()` is the interface's Get",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Store interface {\n\tGet() int\n}\n\ntype Mem struct{}\n\nfunc (Mem) Get() int {\n\treturn 1\n}\n\ntype Cache struct {\n\tStore\n}\n\nfunc Use(c Cache) int {\n\treturn c.Get()\n}\n'},
     [('Use', 'calls', 'Store.Get')], [('Use', 'calls', 'Mem.Get')],
     source='spec Struct_types (promoted methods)')
case('go', 'embedded-generic-type', '`Box[int]` embedded is the field Box: `h.Get()` is Box.Get',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Box[T any] struct {\n\tv T\n}\n\nfunc (b *Box[T]) Get() T {\n\treturn b.v\n}\n\ntype Other struct{}\n\nfunc (Other) Get() int {\n\treturn 0\n}\n\ntype Holder struct {\n\tBox[int]\n}\n\nfunc Use(h Holder) int {\n\treturn h.Get()\n}\n'},
     [('Use', 'calls', 'Box.Get')], [('Use', 'calls', 'Other.Get')],
     source='spec Struct_types (EmbeddedField with TypeArgs); go/types struct.go embeddedFieldIdent (IndexExpr)', since='1.18')
case('go', 'new-and-dereference', 'a local assigned `new(Repo)` or `*r` is of type Repo',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Repo struct{}\n\nfunc (Repo) Find() int {\n\treturn 1\n}\n\ntype Cache struct{}\n\nfunc (Cache) Find() int {\n\treturn 2\n}\n\nfunc Use() int {\n\tr := new(Repo)\n\tv := *r\n\treturn r.Find() + v.Find()\n}\n'},
     [('Use', 'calls', 'Repo.Find')], [('Use', 'calls', 'Cache.Find')],
     source='spec Pointer_types (base type)')
case('go', 'function-typed-value-result', "`open().Find()` and `make2(1)().Find()` on parameters of function type are the result type's method",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Repo struct{}\n\nfunc (*Repo) Find() int {\n\treturn 1\n}\n\ntype Other struct{}\n\nfunc (*Other) Find() int {\n\treturn 2\n}\n\nfunc Use(open func() *Repo, make2 func(n int) func() *Repo) int {\n\treturn open().Find() + make2(1)().Find()\n}\n'},
     [('Use', 'calls', 'Repo.Find')], [('Use', 'calls', 'Other.Find')],
     source='spec Function_types (Signature, Result)')
case('go', 'variadic-parameter-is-a-slice', '`repos ...*Repo` is a []*Repo: `repos[0]` is a *Repo',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Repo struct{}\n\nfunc (*Repo) Find() int {\n\treturn 1\n}\n\ntype Other struct{}\n\nfunc (*Other) Find() int {\n\treturn 2\n}\n\nfunc Use(repos ...*Repo) int {\n\treturn repos[0].Find()\n}\n'},
     [('Use', 'calls', 'Repo.Find')], [('Use', 'calls', 'Other.Find')],
     source='spec Function_types (variadic)')
case('go', 'parameter-hides-a-type', 'a parameter or receiver named like a type hides it in the body only, not in the signature',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Config struct{}\n\nfunc (Config) Run() int {\n\treturn 1\n}\n\ntype Real struct{}\n\nfunc (Real) Run() int {\n\treturn 2\n}\n\nfunc Use(Config Real) *Config {\n\t_ = Config.Run()\n\treturn nil\n}\n\nfunc (Config Real) Name() int {\n\treturn Config.Run()\n}\n'},
     [('Use', 'calls', 'Real.Run'), ('Use', 'references', 'Config'), ('Real.Name', 'calls', 'Real.Run')], [('Use', 'calls', 'Config.Run'), ('Real.Name', 'calls', 'Config.Run')],
     source='spec Declarations_and_scope (parameters: the scope is the function body); go/types signature.go funcType (scopePos)')
case('go', 'qualified-embedded-interface', "`util.Reader` embedded gives util's Reader.Read, not a local Reader's",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'util/u.go': 'package util\n\ntype Reader interface {\n\tRead() int\n}\n', 'a.go': 'package app\n\nimport "example.com/app/util"\n\ntype Reader interface {\n\tRead() int\n}\n\ntype ReadWriter interface {\n\tutil.Reader\n\tWrite() int\n}\n\nfunc Use(rw ReadWriter) int {\n\treturn rw.Read()\n}\n'},
     [('Use', 'calls', 'util/u.go:Reader.Read')], [('Use', 'calls', 'a.go:Reader.Read')],
     source='spec Embedded_interfaces; go/types interface.go interfaceType, typeset.go computeInterfaceTypeSet')
case('go', 'constraint-embeds-an-interface', 'a value of type parameter T with constraint `Number` (embedding Labeled and `~int | ~int64`) has Labeled.Label',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Labeled interface {\n\tLabel() string\n}\n\ntype Number interface {\n\tLabeled\n\t~int | ~int64\n}\n\ntype Other struct{}\n\nfunc (Other) Label() string {\n\treturn "o"\n}\n\nfunc Show[T Number](v T) string {\n\treturn v.Label()\n}\n'},
     [('Show', 'calls', 'Labeled.Label')], [('Show', 'calls', 'Other.Label')],
     source='spec General_interfaces; go/types typeset.go computeInterfaceTypeSet', since='1.18')
case('go', 'range-map-key', '`for k := range m` over `map[Key]int` makes k a Key',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Key struct{}\n\nfunc (Key) Label() string {\n\treturn "k"\n}\n\ntype Other struct{}\n\nfunc (Other) Label() string {\n\treturn "o"\n}\n\nfunc Use(m map[Key]int) string {\n\ts := ""\n\tfor k := range m {\n\t\ts += k.Label()\n\t}\n\treturn s\n}\n'},
     [('Use', 'calls', 'Key.Label')], [('Use', 'calls', 'Other.Label')],
     source='spec Map_types (KeyType); spec For_range')
case('go', 'channel-element-type', '`<-jobs` and `range jobs` over a `<-chan *Job` give a *Job',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Job struct{}\n\nfunc (*Job) Run() int {\n\treturn 1\n}\n\ntype Other struct{}\n\nfunc (*Other) Run() int {\n\treturn 2\n}\n\nfunc Worker(jobs <-chan *Job) int {\n\tn := 0\n\tfor j := range jobs {\n\t\tn += j.Run()\n\t}\n\treturn n\n}\n\nfunc Once(jobs <-chan *Job) int {\n\tj := <-jobs\n\treturn j.Run()\n}\n'},
     [('Worker', 'calls', 'Job.Run'), ('Once', 'calls', 'Job.Run')], [('Worker', 'calls', 'Other.Run'), ('Once', 'calls', 'Other.Run')],
     source='spec Channel_types (element type, direction)')
case('go', 'defined-interface-keeps-the-interface-methods', '`type Source Reader` (Reader an interface): `s.Read()` is Reader.Read',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Reader interface {\n\tRead() int\n}\n\ntype Source Reader\n\ntype File struct{}\n\nfunc (File) Read() int {\n\treturn 1\n}\n\nfunc Use(s Source) int {\n\treturn s.Read()\n}\n'},
     [('Use', 'calls', 'Reader.Read')], [('Use', 'calls', 'File.Read')],
     source='spec Underlying_types, Method_sets (method set of an interface type); go/types under.go')
case('go', 'core-type-of-a-type-parameter', 'an element of `S ~[]*Repo` is a *Repo',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Repo struct{}\n\nfunc (*Repo) Find() int {\n\treturn 1\n}\n\ntype Other struct{}\n\nfunc (*Other) Find() int {\n\treturn 2\n}\n\nfunc First[S ~[]*Repo](s S) int {\n\treturn s[0].Find()\n}\n'},
     [('First', 'calls', 'Repo.Find')], [('First', 'calls', 'Other.Find')],
     source='go/types under.go CommonUnderlying, commonUnder', since='1.18')
case('go', 'defined-type-has-only-its-own-methods', "`type Derived Base` does not inherit Base's methods: `d.Name()` is Derived.Name",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Base struct{}\n\nfunc (Base) Name() string {\n\treturn "base"\n}\n\ntype Derived Base\n\nfunc (Derived) Name() string {\n\treturn "derived"\n}\n\nfunc Use(d Derived) string {\n\treturn d.Name()\n}\n'},
     [('Use', 'calls', 'Derived.Name')], [('Use', 'calls', 'Base.Name')],
     source='spec Method_sets, Type_declarations; go/types methodset.go NewMethodSet')
case('go', 'defined-struct-keeps-promoted-methods', "a struct type defined from another struct type has its embedded fields, so a promoted method is called on it: `c.Close()` on a `Copy` is Base's",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Base struct{}\n\nfunc (b *Base) Close() int { return 1 }\n\ntype Other struct{}\n\nfunc (o *Other) Close() int { return 2 }\n\ntype File struct {\n\tBase\n}\n\ntype Copy File\n\nfunc Run(c *Copy) int {\n\treturn c.Close()\n}\n'},
     [('Run', 'calls', 'Base.Close')], [('Run', 'calls', 'Other.Close')],
     source='spec Underlying_types; spec Struct_types (promoted methods); go/types under.go')
case('go', 'embedded-instantiated-interface', "an interface embedding an instantiated generic interface has its methods: `r.Get()` is the generic interface's",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Store[V any] interface {\n\tGet() V\n\tPut(v V)\n}\n\ntype Other interface{ Get() string }\n\ntype Repo interface {\n\tStore[int]\n}\n\nfunc Run(r Repo) int {\n\treturn r.Get()\n}\n'},
     [('Repo', 'embeds', 'Store'), ('Run', 'calls', 'Store.Get')], [('Run', 'calls', 'Other.Get')],
     source='spec Embedded_interfaces; go/types interface.go')
case('go', 'address-of-a-value-is-a-pointer', "a pointer method called on `&x` or `&T{}` is T's",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Repo struct{ n int }\n\nfunc (r *Repo) Save() { r.n++ }\n\ntype Other struct{}\n\nfunc (o *Other) Save() {}\n\nfunc Run() {\n\tr := Repo{}\n\tp := &r\n\tp.Save()\n\t(&Repo{}).Save()\n}\n'},
     [('Run', 'calls', 'Repo.Save')], [('Run', 'calls', 'Other.Save')],
     source='spec Method_sets; spec Address_operators')
case('go', 'type-literal-component-types', 'the names inside map, channel and array type literals of a field are references',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Key struct{}\ntype Val struct{}\ntype Msg struct{}\ntype Arr struct{}\n\ntype Index struct {\n\tm map[Key][]Val\n\tc chan Msg\n\ta [2]Arr\n}\n'},
     [('Index', 'references', 'Key'), ('Index', 'references', 'Val'), ('Index', 'references', 'Msg'), ('Index', 'references', 'Arr')], [],
     source='spec Map_types; spec Channel_types; spec Array_types')
case('go', 'method-declared-on-an-alias', 'a method whose receiver is written with an alias is a method of the aliased type: `t.Bump()` on a T is that method',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype T struct{ n int }\n\ntype A = T\n\nfunc (a *A) Bump() { a.n++ }\n\ntype Other struct{}\n\nfunc (o *Other) Bump() {}\n\nfunc Run(t *T) {\n\tt.Bump()\n}\n'},
     [('Run', 'calls', 'A.Bump')], [('Run', 'calls', 'Other.Bump')],
     source='spec Types (alias denotes the same type); spec Method_declarations (receiver base type)')
case('go', 'embedded-alias', "an embedded alias is a field named by the alias; the aliased type's methods are promoted: `f.Alias.Close()` and `f.Close()` are Base's",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Base struct{}\n\nfunc (b *Base) Close() int { return 1 }\n\ntype Other struct{}\n\nfunc (o *Other) Close() int { return 2 }\n\ntype Alias = Base\n\ntype File struct {\n\tAlias\n}\n\nfunc Run(f *File) int {\n\treturn f.Alias.Close() + f.Close()\n}\n'},
     [('Run', 'calls', 'Base.Close')], [('Run', 'calls', 'Other.Close')],
     source='spec Struct_types; spec Types (alias)')
case('go', 'embedded-pointer-field-promotes', 'an embedded `*T` field promotes the methods of T and *T',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Base struct{ n int }\n\nfunc (b *Base) Close() int { return b.n }\n\nfunc (b Base) Size() int { return b.n }\n\ntype Other struct{}\n\nfunc (o *Other) Close() int { return 0 }\n\nfunc (o Other) Size() int { return 0 }\n\ntype File struct {\n\t*Base\n}\n\nfunc Run(f File) int {\n\treturn f.Close() + f.Size()\n}\n'},
     [('Run', 'calls', 'Base.Close'), ('Run', 'calls', 'Base.Size')], [('Run', 'calls', 'Other.Close'), ('Run', 'calls', 'Other.Size')],
     source='spec Struct_types (promoted methods of an embedded *T)')
case('go', 'range-map-value', "the value variable of `for _, v := range m` is the map's element type, not its key type",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Item struct{ n int }\n\nfunc (i Item) Name() int { return i.n }\n\ntype Key struct{ k int }\n\nfunc (k Key) Name() int { return k.k }\n\nfunc Run(m map[Key]Item) int {\n\tt := 0\n\tfor _, v := range m {\n\t\tt += v.Name()\n\t}\n\treturn t\n}\n'},
     [('Run', 'calls', 'Item.Name')], [('Run', 'calls', 'Key.Name')],
     source='spec Map_types, For_statements (range over a map: key, element)')
case('go', 'method-of-a-defined-function-type', 'a method declared on a defined function type is called on a value of that type',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype HandlerFunc func(int) int\n\nfunc (f HandlerFunc) Serve(x int) int { return f(x) }\n\ntype Router struct{}\n\nfunc (r Router) Serve(x int) int { return x }\n\nfunc Run(h HandlerFunc) int {\n\treturn h.Serve(1)\n}\n'},
     [('Run', 'calls', 'HandlerFunc.Serve')], [('Run', 'calls', 'Router.Serve')],
     source='spec Underlying_types, Method_declarations')
case('go', 'generic-field-has-the-type-argument', '`b.V` and `b.Vs[0]` of a `Box[Item]` are Items',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Item struct{ n int }\n\nfunc (i Item) Name() int { return i.n }\n\ntype Other struct{ n int }\n\nfunc (o Other) Name() int { return o.n }\n\ntype Box[T any] struct {\n\tV  T\n\tVs []T\n}\n\nfunc Run(b Box[Item]) int {\n\treturn b.V.Name() + b.Vs[0].Name()\n}\n'},
     [('Run', 'calls', 'Item.Name'), ('Run', 'references', 'Item')], [('Run', 'calls', 'Other.Name')],
     source='spec Types (instantiation); go/types typexpr.go instantiatedType', since='1.18')
case('go', 'nested-anonymous-struct-field', "`c.DB.Pool.Close()` goes through a field of anonymous struct type to Pool's method",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Pool struct{ n int }\n\nfunc (p *Pool) Close() int { return p.n }\n\ntype Config struct {\n\tDB struct {\n\t\tPool *Pool\n\t}\n}\n\nfunc Run(c *Config) int {\n\treturn c.DB.Pool.Close()\n}\n'},
     [('Run', 'calls', 'Pool.Close'), ('Config', 'references', 'Pool')], [],
     source='spec Struct_types')
case('go', 'underlying-through-a-chain', 'a defined type of a defined slice type is indexed to the element type',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Item struct{ n int }\n\nfunc (i Item) Name() int { return i.n }\n\ntype Other struct{ n int }\n\nfunc (o Other) Name() int { return o.n }\n\ntype base []Item\n\ntype view base\n\nfunc Run(v view) int {\n\treturn v[0].Name()\n}\n'},
     [('Run', 'calls', 'Item.Name')], [('Run', 'calls', 'Other.Name')],
     source='spec Underlying_types; go/types under.go')
case('go', 'instantiated-generic-function', '`util.Map[int, string](…)` and `Map[int, int](…)` are calls of the function written, qualified or not',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'util/u.go': 'package util\n\nfunc Map[T, U any](xs []T, f func(T) U) []U {\n\treturn nil\n}\n', 'a.go': 'package app\n\nimport "example.com/app/util"\n\nfunc Map[T, U any](xs []T, f func(T) U) []U {\n\treturn nil\n}\n\nfunc Run(xs []int) ([]string, func([]int, func(int) bool) []bool) {\n\treturn util.Map[int, string](xs, nil), util.Map[int, bool]\n}\n\nfunc Local(xs []int) []int {\n\treturn Map[int, int](xs, nil)\n}\n'},
     [('Run', 'calls', 'util/u.go:Map'), ('Local', 'calls', 'a.go:Map')], [('Run', 'calls', 'a.go:Map'), ('Local', 'calls', 'util/u.go:Map')],
     source='spec Operands (OperandName [ TypeArgs ]); go/types index.go indexExpr (function instantiation)')
case('go', 'parenthesized-operand', '`(helper)()` is a call of helper and `((r)).Find()` a call on r',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Repo struct{}\n\nfunc (r *Repo) Find() int {\n\treturn 1\n}\n\ntype Other struct{}\n\nfunc (o *Other) Find() int {\n\treturn 2\n}\n\nfunc helper() int {\n\treturn 1\n}\n\nfunc Run(r *Repo) int {\n\treturn (helper)() + ((r)).Find()\n}\n'},
     [('Run', 'calls', 'helper'), ('Run', 'calls', 'Repo.Find')], [('Run', 'calls', 'Other.Find')],
     source='spec Operands (Operand = ... "(" Expression ")"); go/types expr.go exprInternal (case *ast.ParenExpr)')
case('go', 'qualified-type-is-the-packages', "`util.Config` is the imported package's type; a bare `Config` is the importing package's",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'util/u.go': 'package util\n\ntype Config struct {\n\tName string\n}\n', 'a.go': 'package app\n\nimport "example.com/app/util"\n\ntype Config struct {\n\tName string\n}\n\nfunc Run() util.Config {\n\treturn util.Config{}\n}\n\nfunc Local() Config {\n\treturn Config{}\n}\n'},
     [('Run', 'references', 'util/u.go:Config'), ('Local', 'references', 'a.go:Config')], [('Run', 'references', 'a.go:Config'), ('Local', 'references', 'util/u.go:Config')],
     source='spec Qualified_identifiers; go/types selector in call.go (package-qualified identifier)')
case('go', 'qualified-function-value', "`apply(util.Handler)` hands over the imported package's Handler, not the importing package's",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'util/u.go': 'package util\n\nfunc Handler(x int) int {\n\treturn x\n}\n', 'a.go': 'package app\n\nimport "example.com/app/util"\n\nfunc Handler(x int) int {\n\treturn x + 1\n}\n\nfunc apply(f func(int) int) int {\n\treturn f(1)\n}\n\nfunc Run() int {\n\treturn apply(util.Handler)\n}\n'},
     [('Run', 'indirect_call', 'util/u.go:Handler')], [('Run', 'indirect_call', 'a.go:Handler')],
     source='spec Qualified_identifiers (an identifier declared in the package block of that package)')
case('go', 'qualified-variable-and-constant', "`util.Limit` and `util.Max` are the imported package's variable and constant, not the importing package's of the same names",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'util/u.go': 'package util\n\nvar Limit = 3\n\nconst Max = 9\n', 'a.go': 'package app\n\nimport "example.com/app/util"\n\nvar Limit = 1\n\nconst Max = 2\n\nfunc Run() int {\n\treturn util.Limit + util.Max\n}\n\nfunc Local() int {\n\treturn Limit + Max\n}\n'},
     [('Run', 'reads', 'util/u.go:Limit'), ('Run', 'reads', 'util/u.go:Max'), ('Local', 'reads', 'a.go:Limit')], [('Run', 'reads', 'a.go:Limit'), ('Run', 'reads', 'a.go:Max')],
     source='spec Qualified_identifiers')
case('go', 'struct-literal-key-is-a-field', '`Config{helper: 1}` names the field helper, not the function helper in scope',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Config struct {\n\thelper int\n\tName   string\n}\n\nfunc helper() int {\n\treturn 1\n}\n\nfunc Run() Config {\n\treturn Config{helper: 1, Name: "x"}\n}\n'},
     [('Run', 'references', 'Config')], [('Run', 'references', 'a.go:helper'), ('Run', 'calls', 'a.go:helper'), ('Run', 'indirect_call', 'a.go:helper')],
     source='spec Composite_literals; go/types literals.go compositeLit (check.recordUse(key, fld))')
case('go', 'literal-key-field-or-value', 'in `map[int]string{Key: "k"}` Key is the constant; in `Config{Key: 2}` it is the field',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nconst Key = 1\n\ntype Config struct {\n\tKey int\n}\n\nfunc Field() Config {\n\treturn Config{Key: 2}\n}\n\nfunc Table() map[int]string {\n\treturn map[int]string{Key: "k"}\n}\n'},
     [('Table', 'reads', 'a.go:Key')], [('Field', 'reads', 'a.go:Key')],
     source='spec Composite_literals (Array and slice literals, Map literals); go/types literals.go compositeLit')
case('go', 'address-of-literal-types-the-value', "a local assigned `&Repo{}` or `new(Repo)`, or the literal called directly, answers Repo's methods",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Repo struct{}\n\nfunc (r *Repo) Find() int {\n\treturn 1\n}\n\ntype Other struct{}\n\nfunc (o *Other) Find() int {\n\treturn 2\n}\n\nfunc Run() int {\n\tr := &Repo{}\n\treturn r.Find()\n}\n\nfunc Direct() int {\n\treturn (&Repo{}).Find()\n}\n\nfunc Fresh() int {\n\tr := new(Repo)\n\treturn r.Find()\n}\n'},
     [('Run', 'calls', 'Repo.Find'), ('Direct', 'calls', 'Repo.Find'), ('Fresh', 'calls', 'Repo.Find')], [('Run', 'calls', 'Other.Find'), ('Direct', 'calls', 'Other.Find'), ('Fresh', 'calls', 'Other.Find')],
     source='spec Address_of_composite_literals; Address_operators (typed composite literal); go/types expr.go unary (&)')
case('go', 'elided-element-type-keys', "in `[]*Pair{{Key: 1}}` the key is Pair's field, not the function Key in scope",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Pair struct {\n\tKey int\n}\n\nfunc Key() int {\n\treturn 1\n}\n\nfunc Run() []*Pair {\n\treturn []*Pair{{Key: 1}}\n}\n'},
     [('Run', 'references', 'Pair')], [('Run', 'calls', 'a.go:Key'), ('Run', 'indirect_call', 'a.go:Key'), ('Run', 'references', 'a.go:Key')],
     source='spec Omitting_composite_literal_types (the element type of an array, slice or map literal); go/types literals.go compositeLit (isElem = T.hint)')
case('go', 'closure-sees-the-enclosing-local', '`helper()` in a literal is the local helper of the surrounding function, not the package function',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nfunc helper() int {\n\treturn 1\n}\n\nfunc Run() int {\n\thelper := func() int {\n\t\treturn 2\n\t}\n\tf := func() int {\n\t\treturn helper()\n\t}\n\treturn f()\n}\n'},
     [], [('Run', 'calls', 'helper')],
     source='spec Function_literals; go/types expr.go (case *ast.FuncLit: check.funcLit)')
case('go', 'closure-sees-only-earlier-locals', '`helper()` in a literal written before `helper := 2` is the package function',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nfunc helper() int {\n\treturn 1\n}\n\nfunc Run() int {\n\tf := func() int {\n\t\treturn helper()\n\t}\n\thelper := 2\n\treturn f() + helper\n}\n'},
     [('Run', 'calls', 'helper')], [],
     source='spec Function_literals; Declarations_and_scope (scope begins after the declaration); go/types scope.go LookupParent (pos)')
case('go', 'closure-parameter-hides-a-function', 'in `func(helper func() int) int { return helper() }` helper is the parameter',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nfunc helper() int {\n\treturn 1\n}\n\nfunc Run() int {\n\treturn func(helper func() int) int {\n\t\treturn helper()\n\t}(nil)\n}\n'},
     [], [('Run', 'calls', 'helper'), ('Run', 'indirect_call', 'helper')],
     source="spec Function_literals; Declarations_and_scope (a parameter's scope is the function body)")
case('go', 'literal-invoked-in-place', 'a call inside `go func() { helper() }()` is a call of helper made by Run',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nfunc helper() int {\n\treturn 1\n}\n\nfunc Run() {\n\tgo func() {\n\t\thelper()\n\t}()\n}\n'},
     [('Run', 'calls', 'helper')], [],
     source='spec Function_literals (A function literal can be assigned to a variable or invoked directly)')
case('go', 'index-of-a-call-result', "`repos()[0].Find()` is Repo's method: the call's result is a slice of *Repo",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Repo struct{}\n\nfunc (r *Repo) Find() int {\n\treturn 1\n}\n\ntype Other struct{}\n\nfunc (o *Other) Find() int {\n\treturn 2\n}\n\nfunc repos() []*Repo {\n\treturn nil\n}\n\nfunc Run() int {\n\treturn repos()[0].Find()\n}\n'},
     [('Run', 'calls', 'Repo.Find'), ('Run', 'calls', 'repos')], [('Run', 'calls', 'Other.Find')],
     source='spec Primary_expressions (PrimaryExpr Index / Selector / Arguments)')
case('go', 'embedded-pointer-is-promoted', "through `*T0` and `T1` embedded in T2, p.M0(), p.M1(), p.x and p.y are T0's and T1's",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype T0 struct {\n\tx int\n}\n\nfunc (*T0) M0() {}\n\ntype T1 struct {\n\ty int\n}\n\nfunc (T1) M1() {}\n\ntype T2 struct {\n\tz int\n\tT1\n\t*T0\n}\n\nfunc Run(p *T2) int {\n\tp.M0()\n\tp.M1()\n\treturn p.x + p.y + p.z\n}\n'},
     [('Run', 'calls', 'T0.M0'), ('Run', 'calls', 'T1.M1'), ('Run', 'reads', 'T0.x'), ('Run', 'reads', 'T1.y'), ('Run', 'reads', 'T2.z')], [],
     source='spec Selectors (the T0 / T1 / T2 example); go/types lookup.go lookupFieldOrMethodImpl (embedded types collected for the next depth)')
case('go', 'struct-embeds-interface', "`s.Log()` on a struct embedding Logger is Logger's method",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Logger interface {\n\tLog() int\n}\n\ntype Other interface {\n\tLog() int\n}\n\ntype Service struct {\n\tLogger\n}\n\nfunc Run(s Service) int {\n\treturn s.Log()\n}\n'},
     [('Run', 'calls', 'Logger.Log')], [('Run', 'calls', 'Other.Log')],
     source='spec Selectors (embedded field of T); go/types lookup.go lookupFieldOrMethodImpl')
case('go', 'field-at-a-shallower-depth-hides-a-method', '`o.Name()` on a struct with a func-typed field Name that embeds a type with method Name is the field',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Inner struct{}\n\nfunc (Inner) Name() string {\n\treturn "inner"\n}\n\ntype Outer struct {\n\tInner\n\tName func() string\n}\n\nfunc Run(o Outer) string {\n\treturn o.Name()\n}\n'},
     [], [('Run', 'calls', 'Inner.Name')],
     source='spec Selectors (depth); go/types lookup.go lookupFieldOrMethodImpl (fields and methods searched together at each depth)')
case('go', 'local-hides-the-package-name', "`util.Sin()` where util is a parameter is the parameter type's method, not the package's function",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'util/u.go': 'package util\n\nfunc Sin() int {\n\treturn 1\n}\n', 'a.go': 'package app\n\nimport "example.com/app/util"\n\ntype Box struct{}\n\nfunc (b *Box) Sin() int {\n\treturn 2\n}\n\nfunc Run(util *Box) int {\n\treturn util.Sin()\n}\n\nfunc Other() int {\n\treturn util.Sin()\n}\n'},
     [('Run', 'calls', 'Box.Sin'), ('Other', 'calls', 'util/u.go:Sin')], [('Run', 'calls', 'util/u.go:Sin')],
     source='spec Selectors (x that is not a package name); Declarations_and_scope (the file block holds the import, the function block the parameter)')
case('go', 'defined-type-keeps-fields-and-promotions', "`type Copy Base`: c.ID is Base's field; `type Wrapped Wrapper` (Wrapper embeds Base): w.Show() is Base's method",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Base struct {\n\tID int\n}\n\nfunc (Base) Show() int {\n\treturn 1\n}\n\ntype Copy Base\n\ntype Wrapper struct {\n\tBase\n}\n\ntype Wrapped Wrapper\n\nfunc Field(c Copy) int {\n\treturn c.ID\n}\n\nfunc Promoted(w Wrapped) int {\n\treturn w.Show()\n}\n'},
     [('Field', 'reads', 'Base.ID'), ('Promoted', 'calls', 'Base.Show')], [],
     source="go/types lookup.go lookupFieldOrMethodImpl (the underlying struct's embedded fields are searched after the named type's own methods)")
case('go', 'alias-has-the-methods', "`type Alias = Repo`: a.Find() is Repo's method",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Repo struct{}\n\nfunc (r *Repo) Find() int {\n\treturn 1\n}\n\ntype Other struct{}\n\nfunc (o *Other) Find() int {\n\treturn 2\n}\n\ntype Alias = Repo\n\nfunc Run(a Alias) int {\n\treturn a.Find()\n}\n'},
     [('Run', 'calls', 'Repo.Find')], [('Run', 'calls', 'Other.Find')],
     source='go/types lookup.go lookupFieldOrMethodImpl (asNamed unwraps an alias)')
case('go', 'unexported-names-of-another-package', "V embeds util.T (unexported method run) and A (method run): `v.run()` is A's",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'util/u.go': 'package util\n\ntype T struct{}\n\nfunc (T) run() int {\n\treturn 1\n}\n\nfunc (t T) Go() int {\n\treturn t.run()\n}\n', 'a.go': 'package app\n\nimport "example.com/app/util"\n\ntype A struct{}\n\nfunc (A) run() int {\n\treturn 2\n}\n\ntype V struct {\n\tutil.T\n\tA\n}\n\nfunc Run(v V) int {\n\treturn v.run()\n}\n'},
     [('Run', 'calls', 'A.run')], [('Run', 'calls', 'T.run')],
     source='go/types lookup.go lookupFieldOrMethodImpl (sameId(pkg, name)); spec Uniqueness_of_identifiers')
case('go', 'method-expression-called', '`Counter.Value(c)`, `(Counter).Value(c)` and `(*Counter).Inc(&c)` are calls of the methods',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Counter struct{ n int }\n\nfunc (c Counter) Value() int {\n\treturn c.n\n}\n\nfunc (c *Counter) Inc() {\n\tc.n++\n}\n\nfunc Run(c Counter) int {\n\tCounter.Value(c)\n\t(*Counter).Inc(&c)\n\treturn (Counter).Value(c)\n}\n'},
     [('Run', 'calls', 'Counter.Value'), ('Run', 'calls', 'Counter.Inc')], [],
     source='spec Method_expressions (five equivalent invocations); go/types selection.go MethodExpr')
case('go', 'pointer-method-expression', '`(*Counter).Inc` and `(*Counter).Get` (Get has a value receiver) are uses of the methods',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Counter struct{ n int }\n\nfunc (c *Counter) Inc() {\n\tc.n++\n}\n\nfunc (c Counter) Get() int {\n\treturn c.n\n}\n\nfunc Run() (func(*Counter), func(*Counter) int) {\n\treturn (*Counter).Inc, (*Counter).Get\n}\n'},
     [('Run', 'references', 'Counter.Inc'), ('Run', 'references', 'Counter.Get')], [],
     source='spec Method_expressions ((*T).Mv, (*T).Mp)')
case('go', 'method-expression-promoted', '`Wrapper.Value` with Counter embedded in Wrapper is Counter.Value',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Counter struct{ n int }\n\nfunc (c Counter) Value() int {\n\treturn c.n\n}\n\ntype Wrapper struct {\n\tCounter\n}\n\nfunc Run() func(Wrapper) int {\n\treturn Wrapper.Value\n}\n'},
     [('Run', 'references', 'Counter.Value')], [],
     source='spec Method_expressions; go/types lookup.go (method sets)')
case('go', 'interface-method-expression', "`Reader.Read` is Reader's method, not another interface's of the same name",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Reader interface {\n\tRead() int\n}\n\ntype Other interface {\n\tRead() int\n}\n\nfunc Run() func(Reader) int {\n\treturn Reader.Read\n}\n'},
     [('Run', 'references', 'Reader.Read')], [('Run', 'references', 'Other.Read')],
     source='spec Method_expressions (a method of an interface type)')
case('go', 'imported-method-expression', "`(*util.Repo).Find` is util's method, not the importing package's Repo.Find",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'util/u.go': 'package util\n\ntype Repo struct{}\n\nfunc (r *Repo) Find() int {\n\treturn 1\n}\n', 'a.go': 'package app\n\nimport "example.com/app/util"\n\ntype Repo struct{}\n\nfunc (r *Repo) Find() int {\n\treturn 2\n}\n\nfunc Run() func(*util.Repo) int {\n\treturn (*util.Repo).Find\n}\n'},
     [('Run', 'references', 'util/u.go:Repo.Find')], [('Run', 'references', 'a.go:Repo.Find')],
     source='spec Method_expressions (ReceiverType is a Type, qualified or not)')
case('go', 'method-value-through-embedding', '`s.M` with *T embedded in S is T.M',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype T int\n\nfunc (t T) M() {}\n\ntype Other int\n\nfunc (o Other) M() {}\n\ntype S struct {\n\t*T\n}\n\nfunc Run(s S) func() {\n\treturn s.M\n}\n'},
     [('Run', 'references', 'T.M')], [('Run', 'references', 'Other.M')],
     source='spec Method_values (type S struct { *T })')
case('go', 'method-value-of-interface-and-pointer', '`r.Read` on an interface and `c.Inc` on an addressable Counter are uses of Reader.Read and Counter.Inc',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Reader interface {\n\tRead() int\n}\n\ntype Counter struct{ n int }\n\nfunc (c *Counter) Inc() {\n\tc.n++\n}\n\nfunc FromInterface(r Reader) func() int {\n\treturn r.Read\n}\n\nfunc FromValue() func() {\n\tvar c Counter\n\treturn c.Inc\n}\n'},
     [('FromInterface', 'references', 'Reader.Read'), ('FromValue', 'references', 'Counter.Inc')], [],
     source='spec Method_values (t.Mp is (&t).Mp; a value of interface type)')
case('go', 'array-element', '`a[0]` of `[3]*Repo` is a *Repo',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Repo struct{}\n\nfunc (r *Repo) Find() int {\n\treturn 1\n}\n\ntype Other struct{}\n\nfunc (o *Other) Find() int {\n\treturn 2\n}\n\nfunc Run() int {\n\tvar a [3]*Repo\n\treturn a[0].Find()\n}\n'},
     [('Run', 'calls', 'Repo.Find')], [('Run', 'calls', 'Other.Find')],
     source='spec Index_expressions (a of array type A); go/types index.go indexExpr (case *Array)')
case('go', 'pointer-to-array-element', '`p[0]` of `*[3]*Repo` is a *Repo',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Repo struct{}\n\nfunc (r *Repo) Find() int {\n\treturn 1\n}\n\ntype Other struct{}\n\nfunc (o *Other) Find() int {\n\treturn 2\n}\n\nfunc Run(p *[3]*Repo) int {\n\treturn p[0].Find()\n}\n'},
     [('Run', 'calls', 'Repo.Find')], [('Run', 'calls', 'Other.Find')],
     source='spec Index_expressions; go/types index.go indexExpr (case *Pointer)')
case('go', 'index-of-index', '`grid[0][1]` of `[][]*Repo` is a *Repo',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Repo struct{}\n\nfunc (r *Repo) Find() int {\n\treturn 1\n}\n\ntype Other struct{}\n\nfunc (o *Other) Find() int {\n\treturn 2\n}\n\nfunc Run(grid [][]*Repo) int {\n\treturn grid[0][1].Find()\n}\n'},
     [('Run', 'calls', 'Repo.Find')], [('Run', 'calls', 'Other.Find')],
     source='spec Index_expressions (the type of a[x] is the element type)')
case('go', 'map-comma-ok', '`r, ok := m["a"]` types r as the map\'s *Repo',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Repo struct{}\n\nfunc (r *Repo) Find() int {\n\treturn 1\n}\n\ntype Other struct{}\n\nfunc (o *Other) Find() int {\n\treturn 2\n}\n\nfunc Run(m map[string]*Repo) int {\n\tr, ok := m["a"]\n\tif !ok {\n\t\treturn 0\n\t}\n\treturn r.Find()\n}\n'},
     [('Run', 'calls', 'Repo.Find')], [('Run', 'calls', 'Other.Find')],
     source='spec Index_expressions (v, ok = a[x]); go/types index.go')
case('go', 'slice-expression-keeps-the-type', '`repos[1:][0]` and `a[:2][0]` are *Repo',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Repo struct{}\n\nfunc (r *Repo) Find() int {\n\treturn 1\n}\n\ntype Other struct{}\n\nfunc (o *Other) Find() int {\n\treturn 2\n}\n\nfunc Run(repos []*Repo) int {\n\treturn repos[1:][0].Find()\n}\n\nfunc Fixed(a [4]*Repo) int {\n\treturn a[:2][0].Find()\n}\n'},
     [('Run', 'calls', 'Repo.Find'), ('Fixed', 'calls', 'Repo.Find')], [('Run', 'calls', 'Other.Find'), ('Fixed', 'calls', 'Other.Find')],
     source='spec Slice_expressions; go/types index.go sliceExpr')
case('go', 'slice-of-a-named-slice-keeps-its-type', '`n[1:].Count()` on `type Names []string` is Names.Count',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Names []string\n\nfunc (n Names) Count() int {\n\treturn len(n)\n}\n\ntype Other []string\n\nfunc (n Other) Count() int {\n\treturn 0\n}\n\nfunc Run(n Names) int {\n\treturn n[1:].Count()\n}\n'},
     [('Run', 'calls', 'Names.Count')], [('Run', 'calls', 'Other.Count')],
     source='spec Slice_expressions (same type as the operand); go/types index.go sliceExpr')
case('go', 'type-assertion-comma-ok', '`r, ok := x.(*Repo)` types r as *Repo',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Repo struct{}\n\nfunc (r *Repo) Find() int {\n\treturn 1\n}\n\ntype Other struct{}\n\nfunc (o *Other) Find() int {\n\treturn 2\n}\n\nfunc Run(x interface{}) int {\n\tr, ok := x.(*Repo)\n\tif !ok {\n\t\treturn 0\n\t}\n\treturn r.Find()\n}\n'},
     [('Run', 'calls', 'Repo.Find')], [('Run', 'calls', 'Other.Find')],
     source='spec Type_assertions (special form v, ok = x.(T))')
case('go', 'type-assertion-to-an-interface', "`x.(Finder).Find()` is Finder's method",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Finder interface {\n\tFind() int\n}\n\ntype Other interface {\n\tFind() int\n}\n\nfunc Run(x interface{}) int {\n\treturn x.(Finder).Find()\n}\n'},
     [('Run', 'calls', 'Finder.Find'), ('Run', 'references', 'Finder')], [('Run', 'calls', 'Other.Find')],
     source='spec Type_assertions (the type of x.(T) is T)')
case('go', 'type-switch-binding', 'in `case *Repo:` of `switch v := x.(type)`, v is a *Repo',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Repo struct{}\n\nfunc (r *Repo) Find() int {\n\treturn 1\n}\n\ntype Other struct{}\n\nfunc (o *Other) Find() int {\n\treturn 2\n}\n\nfunc Run(x interface{}) int {\n\tswitch v := x.(type) {\n\tcase *Repo:\n\t\treturn v.Find()\n\tcase *Other:\n\t\treturn 0\n\t}\n\treturn 0\n}\n'},
     [('Run', 'calls', 'Repo.Find')], [('Run', 'calls', 'Other.Find')],
     source='spec Type_switches (the variable has the type of the case); Type_assertions')
case('go', 'conversion-types-the-value', "`Celsius(f).String()` is Celsius's method and a reference to the type, never a call of it",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Celsius float64\n\nfunc (c Celsius) String() string {\n\treturn "c"\n}\n\ntype Kelvin float64\n\nfunc (k Kelvin) String() string {\n\treturn "k"\n}\n\nfunc Run(f float64) string {\n\treturn Celsius(f).String()\n}\n'},
     [('Run', 'calls', 'Celsius.String'), ('Run', 'references', 'Celsius')], [('Run', 'calls', 'Kelvin.String'), ('Run', 'calls', 'Celsius')],
     source='spec Conversions; go/types conversions.go conversion (the result has type T)')
case('go', 'parenthesized-pointer-conversion', "`(*Repo)(m).Find()` on a *Mid is Repo's method",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Repo struct{}\n\nfunc (r *Repo) Find() int {\n\treturn 1\n}\n\ntype Other struct{}\n\nfunc (o *Other) Find() int {\n\treturn 2\n}\n\ntype Mid Repo\n\nfunc Run(m *Mid) int {\n\treturn (*Repo)(m).Find()\n}\n'},
     [('Run', 'calls', 'Repo.Find')], [('Run', 'calls', 'Other.Find')],
     source='spec Conversions (*Point)(p)')
case('go', 'indirection-and-address-of', "`(*pp).Norm()` with pp **Point and `(&v).Norm()` are Point's method",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Point struct{}\n\nfunc (Point) Norm() int {\n\treturn 1\n}\n\ntype Other struct{}\n\nfunc (Other) Norm() int {\n\treturn 2\n}\n\nfunc Run(pp **Point, v Point) int {\n\treturn (*pp).Norm() + (&v).Norm()\n}\n'},
     [('Run', 'calls', 'Point.Norm')], [('Run', 'calls', 'Other.Norm')],
     source='spec Address_operators; go/types expr.go unary (&) and StarExpr')
case('go', 'receive-element-type', '`r := <-ch`, `v, ok := <-ch` and `(<-ch).Find()` on `chan *Repo` give a *Repo',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Repo struct{}\n\nfunc (r *Repo) Find() int {\n\treturn 1\n}\n\ntype Other struct{}\n\nfunc (o *Other) Find() int {\n\treturn 2\n}\n\nfunc Run(ch chan *Repo) int {\n\tr := <-ch\n\tv, ok := <-ch\n\tif ok {\n\t\tv.Find()\n\t}\n\treturn r.Find() + (<-ch).Find()\n}\n'},
     [('Run', 'calls', 'Repo.Find')], [('Run', 'calls', 'Other.Find')],
     source='spec Receive_operator; go/types expr.go unary (token.ARROW: the element type)')
case('go', 'channel-range-and-select', '`for r := range ch` and `case s := <-ch:` on `<-chan *Repo` give *Repo',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Repo struct{}\n\nfunc (r *Repo) Find() int {\n\treturn 1\n}\n\ntype Other struct{}\n\nfunc (o *Other) Find() int {\n\treturn 2\n}\n\nfunc Run(ch <-chan *Repo) int {\n\tfor r := range ch {\n\t\tr.Find()\n\t}\n\tselect {\n\tcase s := <-ch:\n\t\treturn s.Find()\n\t}\n}\n'},
     [('Run', 'calls', 'Repo.Find')], [('Run', 'calls', 'Other.Find')],
     source="spec Receive_operator; For_range (a channel's element)")
case('go', 'type-switch-multi-type-clause', "in a type switch clause listing several types, the variable has the switched expression's type: v.M() is the interface's method",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Shape interface{ M() }\n\ntype A struct{}\n\nfunc (A) M() {}\n\ntype B struct{}\n\nfunc (B) M() {}\n\nfunc f(x Shape) {\n\tswitch v := x.(type) {\n\tcase A, B:\n\t\tv.M()\n\t}\n}\n'},
     [('f', 'calls', 'Shape.M')], [('f', 'calls', 'A.M'), ('f', 'calls', 'B.M')],
     source='spec Type_switches')
case('go', 'instantiated-generic-method', 'x.Get() on a value of Box[int] is the method of the generic type Box',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Box[T any] struct{ v T }\n\nfunc (b Box[T]) Get() T { return b.v }\n\ntype Other struct{}\n\nfunc (Other) Get() int { return 0 }\n\nfunc use(b Box[int]) int {\n\treturn b.Get()\n}\n'},
     [('use', 'calls', 'Box.Get')], [('use', 'calls', 'Other.Get')],
     source='spec Selectors')
case('go', 'named-result-local', 'a named result is a typed local of the function body',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Repo struct{}\n\nfunc (r *Repo) Save() {}\n\ntype Other struct{}\n\nfunc (o *Other) Save() {}\n\nfunc build() (r *Repo, err error) {\n\tr = &Repo{}\n\tr.Save()\n\treturn\n}\n'},
     [('build', 'calls', 'Repo.Save')], [('build', 'calls', 'Other.Save')],
     source='spec Declarations_and_scope')
case('go', 'package-name-is-not-directory', 'util.F() with import "example.com/app/tools" (package util) is tools\' F, not the importing package\'s F; an alias names the same package',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'tools/t.go': 'package util\n\nfunc F() {}\n', 'a.go': 'package app\n\nimport (\n\t"example.com/app/tools"\n\tali "example.com/app/tools"\n)\n\nfunc F() {}\n\nfunc g() {\n\tutil.F()\n}\n\nfunc h() {\n\tali.F()\n}\n'},
     [('g', 'calls', 'tools/t.go:F'), ('h', 'calls', 'tools/t.go:F')], [('g', 'calls', 'a.go:F'), ('h', 'calls', 'a.go:F')],
     source='spec Import_declarations')
case('go', 'closure-local-shadows-outer-local', "x := B{} inside a function literal hides the surrounding function's x, typed A",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype A struct{}\n\nfunc (A) M() {}\n\ntype B struct{}\n\nfunc (B) M() {}\n\nfunc g() {\n\tx := A{}\n\tfunc() {\n\t\tx := B{}\n\t\tx.M()\n\t}()\n\t_ = x\n}\n'},
     [('g', 'calls', 'B.M')], [('g', 'calls', 'A.M')],
     source='go/types expr.go funcLit scope')
case('go', 'var-declaration-comma-ok-map', "in `var v, ok = m[k]` v is the map's element type: `v.Find()` is Repo's, never Other's",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Repo struct{}\n\nfunc (r *Repo) Find() int { return 1 }\n\ntype Other struct{}\n\nfunc (o *Other) Find() int { return 2 }\n\nfunc Run(m map[string]*Repo) int {\n\tvar v, ok = m["a"]\n\tif !ok {\n\t\treturn 0\n\t}\n\treturn v.Find()\n}\n'},
     [('Run', 'calls', 'Repo.Find')], [('Run', 'calls', 'Other.Find')],
     source="spec Index_expressions ('var v, ok = a[x]')")
case('go', 'var-declaration-comma-ok-assertion', "in `var v, ok = x.(*T)` v is a *T: `v.Find()` is Repo's, never Other's",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Repo struct{}\n\nfunc (r *Repo) Find() int { return 1 }\n\ntype Other struct{}\n\nfunc (o *Other) Find() int { return 2 }\n\nfunc Run(x interface{}) int {\n\tvar v, ok = x.(*Repo)\n\tif !ok {\n\t\treturn 0\n\t}\n\treturn v.Find()\n}\n'},
     [('Run', 'calls', 'Repo.Find')], [('Run', 'calls', 'Other.Find')],
     source="spec Type_assertions ('var v, ok = x.(T)')")
case('go', 'var-declaration-comma-ok-receive', "in `var v, ok = <-ch` v is the channel's element type: `v.Find()` is Repo's, never Other's",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Repo struct{}\n\nfunc (r *Repo) Find() int { return 1 }\n\ntype Other struct{}\n\nfunc (o *Other) Find() int { return 2 }\n\nfunc Run(ch chan *Repo) int {\n\tvar v, ok = <-ch\n\tif !ok {\n\t\treturn 0\n\t}\n\treturn v.Find()\n}\n'},
     [('Run', 'calls', 'Repo.Find')], [('Run', 'calls', 'Other.Find')],
     source="spec Receive_operator ('var x, ok = <-ch')")
case('go', 'slice-of-a-pointer-to-an-array', "`p[1:3]` of a `*[4]*Repo` is a []*Repo: `s[0].Find()` is Repo's, never Other's",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Repo struct{}\n\nfunc (r *Repo) Find() int { return 1 }\n\ntype Other struct{}\n\nfunc (o *Other) Find() int { return 2 }\n\nfunc Run(p *[4]*Repo) int {\n\ts := p[1:3]\n\treturn s[0].Find()\n}\n'},
     [('Run', 'calls', 'Repo.Find')], [('Run', 'calls', 'Other.Find')],
     source="spec Slice_expressions ('Simple slice expressions')")
case('go', 'full-slice-expression', "`a[1:2:3]` of a `[]*Repo` is a []*Repo: `a[1:2:3][0].Find()` is Repo's, never Other's",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Repo struct{}\n\nfunc (r *Repo) Find() int { return 1 }\n\ntype Other struct{}\n\nfunc (o *Other) Find() int { return 2 }\n\nfunc Run(a []*Repo) int {\n\treturn a[1:2:3][0].Find()\n}\n'},
     [('Run', 'calls', 'Repo.Find')], [('Run', 'calls', 'Other.Find')],
     source="spec Slice_expressions ('Full slice expressions')")
case('go', 'address-of-an-element', "`p := &items[0]` is a *Repo: `p.Find()` is Repo's, never Other's",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Repo struct{}\n\nfunc (r *Repo) Find() int { return 1 }\n\ntype Other struct{}\n\nfunc (o *Other) Find() int { return 2 }\n\nfunc Run(items []Repo) int {\n\tp := &items[0]\n\treturn p.Find()\n}\n'},
     [('Run', 'calls', 'Repo.Find')], [('Run', 'calls', 'Other.Find')],
     source="spec Address_operators ('For an operand x of type T, &x generates a pointer of type *T')")
case('go', 'elided-pointer-element-literal', 'in `[]*Point{{x: 1}}` the key `x` is a field of Point: it reads no package variable `x`',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Point struct{ x, y int }\n\nvar x = 1\n\nfunc Run() []*Point {\n\treturn []*Point{{x: 1}}\n}\n'},
     [('Run', 'references', 'Point')], [('Run', 'reads', 'a.go:x')],
     source='spec Omitting_composite_literal_types; go/types literals.go compositeLit (hint: deref of a pointer type)')
case('go', 'elided-map-key-literal', 'in `map[Point]string{{x: 1}: "a"}` the inner `x` is a field of Point, the map\'s key type: it reads no package variable `x`',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Point struct{ x, y int }\n\nvar x = 1\n\nfunc Run() map[Point]string {\n\treturn map[Point]string{{x: 1}: "a"}\n}\n'},
     [('Run', 'references', 'Point')], [('Run', 'reads', 'a.go:x')],
     source='spec Omitting_composite_literal_types; go/types literals.go compositeLit (case *Map: exprWithHint of the key)')
case('go', 'parenthesized-method-expression', "`(Repo).Find(r)` is a use of Repo's method, never of Other's",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Repo struct{}\n\nfunc (r Repo) Find() int { return 1 }\n\ntype Other struct{}\n\nfunc (o Other) Find() int { return 2 }\n\nfunc Run(r Repo) int {\n\treturn (Repo).Find(r)\n}\n'},
     [('Run', 'calls', 'Repo.Find')], [('Run', 'calls', 'Other.Find')],
     source="spec Method_expressions ('(T).Mv(t, 7)')")
case('go', 'embedded-imported-type-promotes', "`s.Find()` on a struct embedding `store.Base` is Base's method, never the other type's of the same name",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'store/s.go': 'package store\n\ntype Base struct{}\n\nfunc (b *Base) Find() int { return 1 }\n', 'a.go': 'package app\n\nimport "example.com/app/store"\n\ntype Other struct{}\n\nfunc (o *Other) Find() int { return 2 }\n\ntype Service struct {\n\tstore.Base\n}\n\nfunc Run(s *Service) int {\n\treturn s.Find()\n}\n'},
     [('Run', 'calls', 'Base.Find')], [('Run', 'calls', 'Other.Find')],
     source='spec Selectors; spec Struct_types (an embedded field is named by its unqualified type name)')
case('go', 'imported-variable-member', "`store.Default.Find()` is the method of the type of the imported variable `Default`, never another type's method of that name",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'store/s.go': 'package store\n\ntype Repo struct{}\n\nfunc (r *Repo) Find() int { return 1 }\n\nvar Default = &Repo{}\n', 'a.go': 'package app\n\nimport "example.com/app/store"\n\ntype Other struct{}\n\nfunc (o *Other) Find() int { return 2 }\n\nfunc Run() int {\n\treturn store.Default.Find()\n}\n'},
     [('Run', 'calls', 'Repo.Find')], [('Run', 'calls', 'Other.Find')],
     source='spec Qualified_identifiers; spec Selectors (a selector on a primary expression is typed step by step)')
case('go', 'package-level-function-literal', 'the call inside `var handler = func() int { return helper() }` is made by `handler`',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nfunc helper() int { return 1 }\n\nfunc other() int { return 2 }\n\nvar handler = func() int {\n\treturn helper()\n}\n'},
     [('handler', 'calls', 'helper')], [('other', 'calls', 'helper')],
     source='spec Function_literals; spec Package_initialization (a package-level variable is initialized from its value)')
case('go', 'conversion-is-a-use-of-the-type', 'a call whose function expression is a type is a conversion: a use of the type, not a call of a function of the same name',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Celsius float64\n\nfunc (c Celsius) Fahrenheit() float64 {\n\treturn float64(c)\n}\n\nfunc Fahrenheit(f float64) float64 {\n\treturn f\n}\n\nfunc Run(f float64) float64 {\n\treturn Celsius(f).Fahrenheit()\n}\n'},
     [('Run', 'references', 'Celsius'), ('Run', 'calls', 'Celsius.Fahrenheit')], [('Run', 'calls', 'Celsius'), ('Run', 'calls', 'a.go:Fahrenheit')],
     source='spec Calls; go/types call.go callExpr (case typexpr: conversion)')
case('go', 'instantiated-generic-call', '`Ident[int](1)` calls the generic function Ident; a method Ident of a type is not that function',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Box struct{}\n\nfunc (b Box) Ident(x int) int {\n\treturn x\n}\n\nfunc Ident[T any](x T) T {\n\treturn x\n}\n\nfunc Run() int {\n\treturn Ident[int](1)\n}\n'},
     [('Run', 'calls', 'Ident')], [('Run', 'calls', 'Box.Ident')],
     source='spec Calls; go/types call.go funcInst, callExpr (unpackIndexedExpr)')
case('go', 'call-as-the-only-argument', "a call that is the only argument of another call binds like any call: a package-qualified name is the imported package's, not a same-named function of this package",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'util/u.go': 'package util\n\nfunc Split(s string) (string, string) {\n\treturn s, s\n}\n\nfunc Join(a, b string) string {\n\treturn a + b\n}\n', 'a.go': 'package app\n\nimport "example.com/app/util"\n\nfunc Split(s string) (string, string) {\n\treturn s, s\n}\n\nfunc Run(v string) string {\n\treturn util.Join(util.Split(v))\n}\n'},
     [('Run', 'calls', 'util/u.go:Join'), ('Run', 'calls', 'util/u.go:Split')], [('Run', 'calls', 'a.go:Split')],
     source='spec Calls (f(g(parameters_of_g))); go/types call.go selector (PkgName: pkg.scope.Lookup(sel))')
case('go', 'package-function-hides-a-builtin', "a package-level function named like a built-in (`min`) hides the universe's: the bare call is the project's function, not a method of that name",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype S struct{}\n\nfunc (s S) min(a, b int) int {\n\treturn a\n}\n\nfunc min(a, b int) int {\n\tif a < b {\n\t\treturn a\n\t}\n\treturn b\n}\n\nfunc Run() int {\n\treturn min(1, 2)\n}\n'},
     [('Run', 'calls', 'min')], [('Run', 'calls', 'S.min')],
     source='spec Built-in_functions, Declarations_and_scope; go/types universe.go (Universe is the outermost scope of any chain)')
case('go', 'new-type-argument', '`new(T)` is a use of the type T',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Opt struct {\n\tn int\n}\n\ntype Other struct {\n\tn int\n}\n\nfunc Run() int {\n\tp := new(Opt)\n\treturn p.n\n}\n'},
     [('Run', 'references', 'Opt')], [('Run', 'references', 'Other')],
     source='spec Allocation; go/types builtins.go (_New: the argument is a type)')
case('go', 'conversion-is-a-use-of-the-type-2', 'duplicate of conversion-is-a-use-of-the-type and instantiated-generic-call',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Celsius float64\n\nfunc Run(f float64) Celsius {\n\treturn Celsius(f)\n}\n'},
     [('Run', 'references', 'Celsius')], [],
     source='go/types call.go callExpr')
case('go', 'new-type-argument-of-another-package', "`new(pkg.T)` is a use of the imported package's type, not of a same-named type of this package",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'store/s.go': 'package store\n\ntype Opt struct {\n\tN int\n}\n', 'a.go': 'package app\n\nimport "example.com/app/store"\n\ntype Opt struct {\n\tn int\n}\n\nfunc Run() int {\n\tp := new(store.Opt)\n\treturn p.N\n}\n'},
     [('Run', 'references', 'store/s.go:Opt')], [('Run', 'references', 'a.go:Opt')],
     source='go/types builtins.go _New, selector (PkgName)')
case('go', 'conversion-result-is-the-type', "the result of a conversion `T(x)` is of type T: `Celsius(x).String()` and `HandlerFunc(f).Serve()` are those types' methods",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Celsius float64\n\nfunc (c Celsius) String() string { return "c" }\n\ntype Other float64\n\nfunc (o Other) String() string { return "o" }\n\ntype HandlerFunc func()\n\nfunc (h HandlerFunc) Serve() { h() }\n\ntype Plain func()\n\nfunc (p Plain) Serve() {}\n\nfunc run(f func(), x float64) string {\n\tHandlerFunc(f).Serve()\n\treturn Celsius(x).String()\n}\n'},
     [('run', 'calls', 'Celsius.String'), ('run', 'calls', 'HandlerFunc.Serve')], [('run', 'calls', 'Other.String'), ('run', 'calls', 'Plain.Serve')],
     source='spec Conversions / Calls; go/types call.go callExpr (conversion branch)')
case('go', 'new-gives-a-pointer-local', "a local assigned `new(T)` is a *T: `p.Run()` is T's method, not another type's of the same name",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype T struct{}\n\nfunc (t *T) Run() {}\n\ntype U struct{}\n\nfunc (u *U) Run() {}\n\nfunc use() {\n\tp := new(T)\n\tp.Run()\n}\n'},
     [('use', 'calls', 'T.Run')], [('use', 'calls', 'U.Run')],
     source='spec Allocation; go/types builtins.go _New')
case('go', 'make-and-append-keep-the-slice-type', "a local made by `make([]*T, n)` or `append(s, x)` is a slice of *T: its elements' methods are T's",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype T struct{}\n\nfunc (t *T) Run() {}\n\ntype U struct{}\n\nfunc (u *U) Run() {}\n\nfunc use(extra *T) {\n\ts := make([]*T, 0, 4)\n\ts = append(s, extra)\n\ts[0].Run()\n\tt := append(s, extra)\n\tt[1].Run()\n}\n'},
     [('use', 'calls', 'T.Run')], [('use', 'calls', 'U.Run')],
     source='spec Appending_and_copying_slices, Making_slices_maps_and_channels; go/types builtins.go _Append, _Make')
case('go', 'generic-type-instance-method', '`NewStack[int]()` gives a *Stack[int] and `var q Queue[string]` a Queue: `Push` is the method of the generic type of the value',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Stack[E any] struct{ items []E }\n\nfunc (s *Stack[E]) Push(v E) { s.items = append(s.items, v) }\n\ntype Queue[E any] struct{ items []E }\n\nfunc (q *Queue[E]) Push(v E) { q.items = append(q.items, v) }\n\nfunc NewStack[E any]() *Stack[E] { return &Stack[E]{} }\n\nfunc use() {\n\ts := NewStack[int]()\n\ts.Push(1)\n}\n\nfunc other() {\n\tvar q Queue[string]\n\tq.Push("a")\n}\n'},
     [('use', 'calls', 'Stack.Push'), ('other', 'calls', 'Queue.Push')], [('use', 'calls', 'Queue.Push'), ('other', 'calls', 'Stack.Push')],
     source='spec Calls, Instantiations; go/types call.go funcInst / callExpr')
case('go', 'func-field-call-is-no-function', '`s.run()` where run is a field of function type selects the field, not the package function run',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'b.go': 'package app\n\ntype Server struct {\n\trun func() int\n}\n\nfunc run() int { return 1 }\n\nfunc (s *Server) Start() int {\n\treturn s.run()\n}\n'},
     [('Server.Start', 'reads', 'Server.run')], [('Server.Start', 'calls', 'b.go:run')],
     source='spec Calls; go/types call.go selector (FieldVal)')
case('go', 'composite-literal-receiver', "the type of a composite literal is the type written, so a method called on it is that type's method",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'd.go': 'package app\n\ntype Point struct{ X, Y int }\n\nfunc (p *Point) Scale(k int) { p.X *= k }\n\nfunc (p Point) Norm() int { return p.X + p.Y }\n\ntype Other struct{}\n\nfunc (Other) Norm() int { return 0 }\n\nfunc Use() int {\n\tn := Point{1, 2}.Norm()\n\t(&Point{3, 4}).Scale(2)\n\treturn n\n}\n'},
     [('Use', 'calls', 'Point.Norm'), ('Use', 'calls', 'Point.Scale')], [('Use', 'calls', 'Other.Norm')],
     source='spec Calls; go/types call.go selector, lookupFieldOrMethod')
case('go', 'error-value-is-no-project-type', "`err.Error()` on a value typed by the predeclared `error` is no call of a project type's Error method",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'c.go': 'package app\n\ntype MyErr struct{}\n\nfunc (MyErr) Error() string { return "mine" }\n\nfunc fail() error { return nil }\n\nfunc Report() string {\n\terr := fail()\n\treturn err.Error()\n}\n'},
     [('Report', 'calls', 'fail')], [('Report', 'calls', 'MyErr.Error')],
     source='go/types universe.go (error); call.go selector')
case('go', 'call-inside-builtin-argument', "calls written inside a built-in's arguments are calls, bound like any other",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Box struct{ items []int }\n\nfunc helper() []int { return nil }\n\nfunc size() int { return 3 }\n\nfunc (b *Box) Fill() {\n\t_ = len(helper())\n\tb.items = append(b.items, helper()...)\n\tcopy(b.items, helper())\n\tdefer close(make(chan int, size()))\n}\n\ntype Other struct{}\n\nfunc (Other) helper() []int { return nil }\n'},
     [('Box.Fill', 'calls', 'helper'), ('Box.Fill', 'calls', 'size')], [('Box.Fill', 'calls', 'Other.helper')],
     source='go/types builtins.go; call.go callExpr (builtin case)')
case('go', 'variadic-parameter-type-use', "`who ...util.Person` names util's Person, a use of that type by the function",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'util/u.go': 'package util\n\ntype Person struct{ Name string }\n', 'g.go': 'package app\n\nimport "example.com/app/util"\n\ntype Person struct{}\n\nfunc Greet(prefix string, who ...util.Person) int {\n\treturn len(prefix) + len(who)\n}\n'},
     [('Greet', 'references', 'util/u.go:Person')], [('Greet', 'references', 'g.go:Person')],
     source='spec Passing_arguments_to_..._parameters; go/types signature parameter types')
case('go', 'var-declaration-scope-starts-after', 'a `var` declaration statement declares its name after its spec: `var helper = helper()` calls the package function, and a later use is the local',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nfunc helper() int { return 1 }\n\nfunc take(v interface{}) {}\n\nfunc Run() int {\n\tvar helper = helper()\n\treturn helper\n}\n\nfunc Hidden() {\n\tvar helper = 2\n\ttake(helper)\n}\n'},
     [('Run', 'calls', 'helper')], [('Hidden', 'indirect_call', 'helper'), ('Hidden', 'calls', 'helper'), ('Hidden', 'references', 'helper'), ('Run', 'indirect_call', 'helper')],
     source='spec Statements (Declaration is a Statement); go/types stmt.go `case *ast.DeclStmt`')
case('go', 'label-is-no-name-of-the-block', 'a label named like a package function is no use of it and hides nothing: `helper:` with `break helper` and `continue helper`, and `helper()` is the function',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nfunc helper() int { return 1 }\n\nfunc take(v interface{}) {}\n\nfunc Run(xs []int) int {\n\tn := 0\nhelper:\n\tfor _, x := range xs {\n\t\tfor _, y := range xs {\n\t\t\tif y > x {\n\t\t\t\tcontinue helper\n\t\t\t}\n\t\t\tif y < 0 {\n\t\t\t\tbreak helper\n\t\t\t}\n\t\t\tn += helper()\n\t\t}\n\t}\n\treturn n\n}\n'},
     [('Run', 'calls', 'helper')], [('Run', 'indirect_call', 'helper'), ('Run', 'references', 'helper')],
     source='spec Labeled_statements, Label_scopes (labels do not conflict with identifiers that are not labels); go/types labels.go `all := NewScope(nil, body.Pos(), body.End(), "label")`')
case('go', 'send-evaluates-the-channel-field', '`h.out <- 1` reads the field `out` of Hub (the channel is an operand, the send changes the channel, not the field)',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Hub struct{ out chan int }\n\ntype Spare struct{ out chan int }\n\nfunc Run(h *Hub) {\n\th.out <- 1\n}\n'},
     [('Run', 'reads', 'Hub.out')], [('Run', 'reads', 'Spare.out'), ('Run', 'writes', 'Hub.out')],
     source='spec Send_statements (Channel = Expression)')
case('go', 'incdec-reads-and-writes-the-field', "`r.n++` and `r.n += 2` use the field n of r's type, written (and read, as `x = x op 1` does)",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Repo struct{ n int }\n\nfunc (r *Repo) Find() int { return 1 }\n\ntype Other struct{ n int }\n\nfunc (o *Other) Find() int { return 2 }\n\nfunc Run(r *Repo) int {\n\tr.n++\n\treturn 0\n}\n\nfunc Add(r *Repo) {\n\tr.n += 2\n}\n'},
     [('Run', 'writes', 'Repo.n'), ('Run', 'reads', 'Repo.n'), ('Add', 'writes', 'Repo.n')], [('Run', 'writes', 'Other.n'), ('Add', 'writes', 'Other.n')],
     source='spec IncDec_statements, Assignment_statements (x op= y is x = x op (y))')
case('go', 'assignment-writes-the-field', "`r.n = 1` writes the field n of r's type, `return r.n` reads it",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Repo struct{ n int }\n\nfunc (r *Repo) Find() int { return 1 }\n\ntype Other struct{ n int }\n\nfunc (o *Other) Find() int { return 2 }\n\nfunc Set(r *Repo) {\n\tr.n = 1\n}\n\nfunc Get(r *Repo) int {\n\treturn r.n\n}\n'},
     [('Set', 'writes', 'Repo.n'), ('Get', 'reads', 'Repo.n')], [('Set', 'writes', 'Other.n'), ('Get', 'reads', 'Other.n'), ('Set', 'reads', 'Repo.n')],
     source='spec Assignment_statements')
case('go', 'assignment-keeps-the-declared-type', "`var f Finder; f = &Repo{}; f.Find()` is the interface's method: the variable's type is the declared one",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Finder interface{ Find() int }\n\ntype Repo struct{}\n\nfunc (r *Repo) Find() int { return 1 }\n\nfunc Run() int {\n\tvar f Finder\n\tf = &Repo{}\n\treturn f.Find()\n}\n'},
     [('Run', 'calls', 'Finder.Find')], [('Run', 'calls', 'Repo.Find')],
     source="spec Assignment_statements (assignability to the operand's type)")
case('go', 'if-init-var-is-seen-by-else', "a name declared by an `if`'s init statement hides a package function in the `if` and its `else`, and no longer after the statement",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nfunc helper() int { return 1 }\n\nfunc take(v interface{}) {}\n\nfunc Run(ok bool) int {\n\tif helper := 2; ok {\n\t\treturn helper\n\t} else {\n\t\ttake(helper)\n\t}\n\treturn helper()\n}\n\nfunc Hidden(ok bool) int {\n\tif helper := 2; ok {\n\t\treturn 0\n\t} else {\n\t\ttake(helper)\n\t\treturn helper\n\t}\n}\n'},
     [('Run', 'calls', 'helper')], [('Hidden', 'indirect_call', 'helper'), ('Hidden', 'calls', 'helper'), ('Hidden', 'references', 'helper')],
     source='spec If_statements, Blocks; go/types stmt.go `check.openScope(s, "if")`')
case('go', 'else-if-sees-the-init-var-of-the-if', "`if r := load(); r == nil {...} else if r.Find() > 0` types r by load's result",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Repo struct{ n int }\n\nfunc (r *Repo) Find() int { return 1 }\n\ntype Other struct{ n int }\n\nfunc (o *Other) Find() int { return 2 }\n\nfunc load() *Repo { return &Repo{} }\n\nfunc Run() int {\n\tif r := load(); r == nil {\n\t\treturn 0\n\t} else if r.Find() > 0 {\n\t\treturn 1\n\t}\n\treturn 2\n}\n'},
     [('Run', 'calls', 'Repo.Find'), ('Run', 'calls', 'load')], [('Run', 'calls', 'Other.Find')],
     source='spec If_statements; go/types stmt.go IfStmt: `check.stmt(inner, s.Else)` inside the scope opened for the if')
case('go', 'switch-init-var-scope', 'a name declared in a `switch` init statement hides a package function in the switch only',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nfunc helper() int { return 1 }\n\nfunc take(v interface{}) {}\n\nfunc Run(n int) int {\n\tswitch helper := n + 1; helper {\n\tcase 1:\n\t\ttake(helper)\n\tdefault:\n\t\treturn helper\n\t}\n\treturn helper()\n}\n\nfunc Hidden(n int) {\n\tswitch helper := n + 1; helper {\n\tcase 1:\n\t\ttake(helper)\n\t}\n}\n'},
     [('Run', 'calls', 'helper')], [('Hidden', 'indirect_call', 'helper'), ('Hidden', 'calls', 'helper'), ('Hidden', 'references', 'helper')],
     source='spec Switch_statements; go/types stmt.go `check.openScope(s, "switch")`')
case('go', 'expression-switch-case-is-a-use', '`case limit:` reads the constant and `case helper():` calls the function',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nfunc helper() int { return 1 }\n\nfunc take(v interface{}) {}\n\nconst limit = 3\n\nfunc Run(n int) int {\n\tswitch n {\n\tcase limit:\n\t\treturn 1\n\tcase helper():\n\t\treturn 2\n\t}\n\treturn 0\n}\n'},
     [('Run', 'calls', 'helper'), ('Run', 'reads', 'limit')], [],
     source='spec Expression_switches')
case('go', 'type-switch-binding-is-typed-by-its-clause', 'in `switch v := f.(type)`, v is *Repo in `case *Repo` and the interface Finder in a clause with two types',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Finder interface{ Find() int }\n\ntype Repo struct{ n int }\n\nfunc (r *Repo) Find() int { return 1 }\n\ntype Other struct{ n int }\n\nfunc (o *Other) Find() int { return 2 }\n\ntype Third struct{}\n\nfunc (t *Third) Find() int { return 3 }\n\nfunc Run(f Finder) int {\n\tswitch v := f.(type) {\n\tcase *Repo:\n\t\treturn v.Find()\n\tcase *Other, *Third:\n\t\treturn v.Find()\n\t}\n\treturn 0\n}\n'},
     [('Run', 'calls', 'Repo.Find'), ('Run', 'calls', 'Finder.Find')], [('Run', 'calls', 'Other.Find'), ('Run', 'calls', 'Third.Find')],
     source='spec Type_switches; go/types stmt.go typeSwitchStmt `obj := newVar(LocalVar, lhs.Pos(), check.pkg, lhs.Name, T)`')
case('go', 'type-switch-binding-hides-a-function', '`switch helper := helper().(type)` calls the package function in the guard and names the variable in the clauses',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nfunc helper() interface{} { return 1 }\n\nfunc take(v interface{}) {}\n\nfunc Run() {\n\tswitch helper := helper().(type) {\n\tcase int:\n\t\ttake(helper)\n\t}\n}\n'},
     [('Run', 'calls', 'helper')], [('Run', 'indirect_call', 'helper')],
     source='spec Type_switches; go/types stmt.go `check.declare(check.scope, nil, obj, clause.Colon)`')
case('go', 'for-clause-var-scope', '`for helper := 0; ...` hides a package function in the loop and `helper()` after the loop is the function',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nfunc helper() int { return 1 }\n\nfunc take(v interface{}) {}\n\nfunc Run() int {\n\tfor helper := 0; helper < 3; helper++ {\n\t\ttake(helper)\n\t}\n\treturn helper()\n}\n\nfunc Hidden() {\n\tfor helper := 0; helper < 3; helper++ {\n\t\ttake(helper)\n\t}\n}\n'},
     [('Run', 'calls', 'helper')], [('Hidden', 'indirect_call', 'helper'), ('Hidden', 'calls', 'helper'), ('Hidden', 'references', 'helper')],
     source='spec For_clause, Blocks; go/types stmt.go `check.openScope(s, "for")`')
case('go', 'range-over-channel', 'the variable of `for r := range ch` over `chan *Repo` is a *Repo',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Repo struct{ n int }\n\nfunc (r *Repo) Find() int { return 1 }\n\ntype Other struct{ n int }\n\nfunc (o *Other) Find() int { return 2 }\n\nfunc Run(ch chan *Repo) int {\n\tn := 0\n\tfor r := range ch {\n\t\tn += r.Find()\n\t}\n\treturn n\n}\n'},
     [('Run', 'calls', 'Repo.Find')], [('Run', 'calls', 'Other.Find')],
     source='spec For_range (channel c: chan E, <-chan E: element e E)')
case('go', 'range-over-map-key-and-value', 'in `for k, v := range m` over `map[Key]*Val`, k is a Key and v a *Val',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Key struct{}\n\nfunc (k Key) Name() string { return "k" }\nfunc (k Key) ID() int      { return 1 }\n\ntype Val struct{}\n\nfunc (v *Val) Name() string { return "v" }\nfunc (v *Val) ID() int      { return 2 }\n\nfunc Run(m map[Key]*Val) (string, int) {\n\tvar s string\n\tvar n int\n\tfor k, v := range m {\n\t\ts = k.Name()\n\t\tn = v.ID()\n\t}\n\treturn s, n\n}\n'},
     [('Run', 'calls', 'Key.Name'), ('Run', 'calls', 'Val.ID')], [('Run', 'calls', 'Val.Name'), ('Run', 'calls', 'Key.ID')],
     source='spec For_range (map m: key k K, value m[k] V)')
case('go', 'range-over-array-pointer', 'the value of `for _, r := range a` with `a *[2]*Repo` is a *Repo',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Repo struct{ n int }\n\nfunc (r *Repo) Find() int { return 1 }\n\ntype Other struct{ n int }\n\nfunc (o *Other) Find() int { return 2 }\n\nfunc Run(a *[2]*Repo) int {\n\tn := 0\n\tfor _, r := range a {\n\t\tn += r.Find()\n\t}\n\treturn n\n}\n'},
     [('Run', 'calls', 'Repo.Find')], [('Run', 'calls', 'Other.Find')],
     source='spec For_range (*[n]E)')
case('go', 'range-over-function-yield-type', '`for r := range t.All` where All takes `yield func(*Repo) bool`: r is a *Repo, and t.All is a method value',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.23\n', 'a.go': 'package app\n\ntype Repo struct{ n int }\n\nfunc (r *Repo) Find() int { return 1 }\n\ntype Other struct{ n int }\n\nfunc (o *Other) Find() int { return 2 }\n\ntype Tree struct{}\n\nfunc (t *Tree) All(yield func(*Repo) bool) {\n\tyield(&Repo{})\n}\n\nfunc Run(t *Tree) int {\n\tn := 0\n\tfor r := range t.All {\n\t\tn += r.Find()\n\t}\n\treturn n\n}\n'},
     [('Run', 'calls', 'Repo.Find'), ('Run', 'references', 'Tree.All')], [('Run', 'calls', 'Other.Find')],
     source='spec For_range (function, 1 value)', since='1.23')
case('go', 'range-assignment-declares-nothing', '`for _, current = range repos` writes the package variable current and keeps its type',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Repo struct{ n int }\n\nfunc (r *Repo) Find() int { return 1 }\n\ntype Other struct{ n int }\n\nfunc (o *Other) Find() int { return 2 }\n\nvar current *Repo\n\nfunc Run(repos []*Repo) int {\n\tfor _, current = range repos {\n\t}\n\treturn current.Find()\n}\n'},
     [('Run', 'writes', 'current'), ('Run', 'calls', 'Repo.Find')], [('Run', 'calls', 'Other.Find')],
     source='spec For_range (RangeClause = [ ExpressionList "=" | IdentifierList ":=" ]); go/types range.go `ordinary assignment`')
case('go', 'defer-and-go-methods-and-closures', '`defer r.Find()`, `go r.Find()` and the calls in a `defer func() {...}()` are calls of the enclosing function',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Repo struct{ n int }\n\nfunc (r *Repo) Find() int { return 1 }\n\ntype Other struct{ n int }\n\nfunc (o *Other) Find() int { return 2 }\n\nfunc work() {}\n\nfunc Run(r *Repo) {\n\tdefer r.Find()\n\tgo r.Find()\n\tdefer func() {\n\t\twork()\n\t}()\n}\n'},
     [('Run', 'calls', 'Repo.Find'), ('Run', 'calls', 'work')], [('Run', 'calls', 'Other.Find')],
     source='spec Go_statements, Defer_statements')
case('go', 'select-receive-binding', '`case r := <-ch:` over `chan *Repo` declares r as a *Repo',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Repo struct{ n int }\n\nfunc (r *Repo) Find() int { return 1 }\n\ntype Other struct{ n int }\n\nfunc (o *Other) Find() int { return 2 }\n\nfunc Run(ch chan *Repo, quit chan bool) int {\n\tselect {\n\tcase r := <-ch:\n\t\treturn r.Find()\n\tcase <-quit:\n\t\treturn 0\n\t}\n}\n'},
     [('Run', 'calls', 'Repo.Find')], [('Run', 'calls', 'Other.Find')],
     source='spec Select_statements; go/types stmt.go SelectStmt `check.stmt(inner, clause.Comm)`')
case('go', 'named-result-is-a-local', 'a result named like a package function hides it in the function body',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nfunc helper() int { return 1 }\n\nfunc take(v interface{}) {}\n\nfunc Run() (helper int) {\n\ttake(helper)\n\treturn\n}\n\nfunc Other() int {\n\treturn helper()\n}\n'},
     [('Other', 'calls', 'helper')], [('Run', 'indirect_call', 'helper'), ('Run', 'references', 'helper'), ('Run', 'calls', 'helper')],
     source='spec Return_statements')
case('go', 'named-result-is-typed', "`func Run() (r *Repo) { r.Find() }`: r.Find is Repo's method",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Repo struct{ n int }\n\nfunc (r *Repo) Find() int { return 1 }\n\ntype Other struct{ n int }\n\nfunc (o *Other) Find() int { return 2 }\n\nfunc Run() (r *Repo) {\n\tr = &Repo{}\n\tr.Find()\n\treturn\n}\n'},
     [('Run', 'calls', 'Repo.Find')], [('Run', 'calls', 'Other.Find')],
     source='spec Return_statements')
case('go', 'goto-label-is-no-name-of-the-block', '`goto helper` and a `helper:` label beside a package function `helper`: `helper()` is the function and the label is no reference to it',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nfunc helper() int { return 1 }\n\nfunc take(v interface{}) {}\n\nfunc Run(n int) int {\n\tif n > 0 {\n\t\tgoto helper\n\t}\n\tn = helper()\nhelper:\n\treturn n\n}\n'},
     [('Run', 'calls', 'helper')], [('Run', 'indirect_call', 'helper'), ('Run', 'references', 'helper')],
     source='spec Goto_statements, Label_scopes; go/types labels.go')
case('go', 'range-expression-is-outside-its-variables', "`for _, helper := range helper()` calls the package function, and the body's `helper` is the variable",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nfunc helper() []int { return nil }\n\nfunc take(v interface{}) {}\n\nfunc Run() {\n\tfor _, helper := range helper() {\n\t\ttake(helper)\n\t}\n}\n\nfunc Hidden(xs []int) {\n\tfor _, helper := range xs {\n\t\ttake(helper)\n\t}\n}\n'},
     [('Run', 'calls', 'helper')], [('Hidden', 'indirect_call', 'helper'), ('Hidden', 'calls', 'helper'), ('Hidden', 'references', 'helper'), ('Run', 'indirect_call', 'helper')],
     source='go/types range.go rangeStmt (`check.openScope(rangeStmt, "range")` after the range expression; `scopePos := rangeStmt.Body.Pos()`)')
case('go', 'range-over-integer-has-its-type', "`for i := range n` with `n Count` makes i a Count: i.Next() is Count's method",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Count int\n\nfunc (c Count) Next() Count { return c + 1 }\n\ntype Step int\n\nfunc (s Step) Next() Step { return s + 1 }\n\nfunc Run(n Count) Count {\n\tvar last Count\n\tfor i := range n {\n\t\tlast = i.Next()\n\t}\n\treturn last\n}\n'},
     [('Run', 'calls', 'Count.Next')], [('Run', 'calls', 'Step.Next')],
     source='spec For_range (integer value); go/types range.go `rangeOverInt`', since='1.22')
case('go', 'plain-assignment-is-no-read', 'a plain assignment writes a variable without reading it; `x += y` reads and writes it; a local of that name is its own variable',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nvar total int\n\nfunc reset() {\n\ttotal = 0\n}\n\nfunc add(n int) {\n\ttotal += n\n}\n\nfunc local() int {\n\ttotal := 0\n\ttotal++\n\treturn total\n}\n'},
     [('reset', 'writes', 'total'), ('add', 'writes', 'total'), ('add', 'reads', 'total')], [('reset', 'reads', 'total'), ('local', 'writes', 'total'), ('local', 'reads', 'total')],
     source='spec Assignment_statements; go/types assignment.go lhsVar (an assigned identifier is not a use of the variable)')
case('go', 'blank-identifier-declares-nothing', 'the blank identifier binds no name: `var _ I = T{}`, `_, v := f()` and `_ = g` are uses of I, T, f and g',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Doer interface {\n\tDo() int\n}\n\ntype Impl struct{}\n\nfunc (i Impl) Do() int {\n\treturn 1\n}\n\nfunc helper() int {\n\treturn 2\n}\n\nfunc pair() (int, int) {\n\treturn 1, 2\n}\n\nfunc Check() int {\n\tvar _ Doer = Impl{}\n\t_, v := pair()\n\t_ = helper\n\treturn v\n}\n\nfunc Other() int {\n\treturn helper()\n}\n'},
     [('Check', 'references', 'Doer'), ('Check', 'calls', 'Impl'), ('Check', 'calls', 'pair'), ('Check', 'references', 'helper'), ('Other', 'calls', 'helper')], [],
     source='spec Assignment_statements (blank identifier); spec Declarations_and_scope (the blank identifier is not introduced in any scope)')
case('go', 'element-assignment-writes-the-field', "assigning to an element `s.items[k] = v` writes the field `items` of s's type; reading `s.items[k]` reads it",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Store struct {\n\titems map[string]int\n}\n\nfunc (s *Store) Put(k string, v int) {\n\ts.items[k] = v\n}\n\nfunc (s *Store) Get(k string) int {\n\treturn s.items[k]\n}\n'},
     [('Store.Put', 'writes', 'Store.items'), ('Store.Get', 'reads', 'Store.items')], [('Store.Get', 'writes', 'Store.items')],
     source='spec Assignment_statements; spec Index_expressions')
case('go', 'promoted-field-incdec', "`p.hits++` through an embedded struct is a read and a write of the embedded type's field `hits`",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Base struct {\n\thits int\n}\n\ntype Page struct {\n\tBase\n}\n\ntype Other struct {\n\thits int\n}\n\nfunc Touch(p *Page) {\n\tp.hits++\n}\n\nfunc Count(o *Other) {\n\to.hits++\n}\n'},
     [('Touch', 'writes', 'Base.hits'), ('Touch', 'reads', 'Base.hits'), ('Count', 'writes', 'Other.hits')], [('Touch', 'writes', 'Other.hits'), ('Count', 'writes', 'Base.hits')],
     source='spec IncDec_statements; spec Selectors (promoted fields)')
case('go', 'select-receive-assigns-a-field', 'a receive case written with `=` assigns an existing operand (a field is written) and declares nothing',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Job struct{}\n\nfunc (j *Job) Run() int {\n\treturn 1\n}\n\ntype Pool struct {\n\tjobs chan *Job\n\tlast *Job\n}\n\nfunc (p *Pool) Loop(out chan<- int) {\n\tselect {\n\tcase j := <-p.jobs:\n\t\tout <- j.Run()\n\tcase p.last = <-p.jobs:\n\t\tout <- p.last.Run()\n\t}\n}\n'},
     [('Pool.Loop', 'writes', 'Pool.last'), ('Pool.Loop', 'reads', 'Pool.jobs'), ('Pool.Loop', 'calls', 'Job.Run')], [],
     source="spec Select_statements; go/types stmt.go (SelectStmt: the RecvStmt's assignment)")
case('go', 'type-switch-nil-and-default-keep-the-guard-type', "in `case nil` and `default` clauses the type switch variable has the guard expression's type (the interface), not the type of another clause",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Shape interface {\n\tArea() int\n}\n\ntype Square struct{}\n\nfunc (s Square) Area() int {\n\treturn 1\n}\n\nfunc Nil(s Shape) int {\n\tswitch v := s.(type) {\n\tcase nil:\n\t\treturn v.Area()\n\tcase Square:\n\t\treturn 0\n\t}\n\treturn 1\n}\n\nfunc Default(s Shape) int {\n\tswitch v := s.(type) {\n\tcase Square:\n\t\treturn 0\n\tdefault:\n\t\treturn v.Area()\n\t}\n}\n'},
     [('Nil', 'calls', 'Shape.Area'), ('Default', 'calls', 'Shape.Area')], [('Nil', 'calls', 'Square.Area'), ('Default', 'calls', 'Square.Area')],
     source="spec Type_switches; go/types stmt.go typeSwitchStmt (T == nil or several types: the variable gets x's type)")
case('go', 'else-if-redeclares-a-name', 'a name redeclared by an `else if` init statement is a new variable there, with its own type',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype A struct{}\n\nfunc (a A) Name() string {\n\treturn "a"\n}\n\ntype B struct{}\n\nfunc (b B) Name() string {\n\treturn "b"\n}\n\nfunc makeA() A {\n\treturn A{}\n}\n\nfunc makeB() B {\n\treturn B{}\n}\n\nfunc Run(n int) string {\n\tif v := makeA(); n == 0 {\n\t\t_ = v\n\t} else if v := makeB(); n == 1 {\n\t\treturn v.Name()\n\t}\n\treturn ""\n}\n'},
     [('Run', 'calls', 'B.Name'), ('Run', 'calls', 'makeA'), ('Run', 'calls', 'makeB')], [('Run', 'calls', 'A.Name')],
     source='spec If_statements; spec Blocks; go/types stmt.go IfStmt (openScope, then the else statement is checked inside it)')
case('go', 'local-type-scope-starts-at-its-name', 'the scope of a type declared in a function starts at its identifier: `next *node` inside the local `node` is the local type',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype node struct {\n\tnext *node\n\tsize int\n}\n\nfunc Run() int {\n\ttype node struct {\n\t\tnext *node\n\t\tsize int\n\t}\n\tn := &node{}\n\treturn n.next.size\n}\n\nfunc Other() int {\n\tn := &node{}\n\treturn n.next.size\n}\n'},
     [('Run', 'reads', 'Run.node.size'), ('Other', 'reads', 'a.go:node.size')], [('Run', 'reads', 'a.go:node.size'), ('Other', 'reads', 'Run.node.size')],
     source='spec Declarations_and_scope (type identifier declared inside a function); go/types decl.go declStmt TypeDecl (declared before its type is checked)')
case('go', 'range-over-an-array-and-a-named-map', 'a range value over `[3]*T` or over a named `map[K]*T` type is a *T',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Repo struct{}\n\nfunc (r *Repo) Find() int {\n\treturn 1\n}\n\ntype Index map[string]*Repo\n\ntype Cache struct{}\n\nfunc (c *Cache) Find() int {\n\treturn 2\n}\n\nfunc Arr(rs [3]*Repo) int {\n\tn := 0\n\tfor _, r := range rs {\n\t\tn += r.Find()\n\t}\n\treturn n\n}\n\nfunc Named(ix Index) int {\n\tn := 0\n\tfor _, r := range ix {\n\t\tn += r.Find()\n\t}\n\treturn n\n}\n'},
     [('Arr', 'calls', 'Repo.Find'), ('Named', 'calls', 'Repo.Find')], [('Arr', 'calls', 'Cache.Find'), ('Named', 'calls', 'Cache.Find')],
     source='spec For_range (the table of range expressions); go/types range.go rangeKeyVal (core type of the range expression)')
case('go', 'range-over-a-field-and-a-call-result', "`range t.children` and `range t.Kids()` iterate the field's and the declared result's element type",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Node struct{}\n\nfunc (n *Node) Visit() int {\n\treturn 1\n}\n\ntype Tree struct {\n\tchildren []*Node\n}\n\ntype Cache struct{}\n\nfunc (c *Cache) Visit() int {\n\treturn 2\n}\n\nfunc (t *Tree) Kids() []*Node {\n\treturn t.children\n}\n\nfunc (t *Tree) Walk() int {\n\tn := 0\n\tfor _, c := range t.children {\n\t\tn += c.Visit()\n\t}\n\tfor _, k := range t.Kids() {\n\t\tn += k.Visit()\n\t}\n\treturn n\n}\n'},
     [('Tree.Walk', 'calls', 'Node.Visit'), ('Tree.Walk', 'calls', 'Tree.Kids'), ('Tree.Walk', 'reads', 'Tree.children')], [('Tree.Walk', 'calls', 'Cache.Visit')],
     source='spec For_range; go/types range.go rangeStmt (the range expression is checked as an ordinary expression)')
case('go', 'short-var-redeclaration-keeps-the-type', "`v, ok := f()` in the scope that declared `v` assigns it (v keeps its declared interface type); in a nested block it declares a new `v` of the value's type",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype I interface{ Run() int }\n\ntype T struct{}\n\nfunc (T) Run() int { return 1 }\n\nfunc make2() (T, bool) { return T{}, true }\n\nfunc Reuse(v I) int {\n\tv, ok := make2()\n\t_ = ok\n\treturn v.Run()\n}\n\nfunc Fresh(v I) int {\n\tif true {\n\t\tv, ok := make2()\n\t\t_ = ok\n\t\treturn v.Run()\n\t}\n\treturn 0\n}\n'},
     [('Reuse', 'calls', 'I.Run'), ('Fresh', 'calls', 'T.Run')], [('Reuse', 'calls', 'T.Run'), ('Fresh', 'calls', 'I.Run')],
     source='spec Short_variable_declarations (redeclaration); go/types assignments.go shortVarDecl')
case('go', 'type-switch-pointer-clause-type', 'in `case *Sq:` of a type switch the guard variable is a `*Sq`, so `v.Area()` is Sq.Area',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Shape interface{ Area() int }\n\ntype Sq struct{}\n\nfunc (*Sq) Area() int { return 1 }\n\ntype Ci struct{}\n\nfunc (*Ci) Area() int { return 2 }\n\nfunc Run(s Shape) int {\n\tswitch v := s.(type) {\n\tcase *Sq:\n\t\treturn v.Area()\n\t}\n\treturn 0\n}\n'},
     [('Run', 'calls', 'Sq.Area')], [('Run', 'calls', 'Shape.Area'), ('Run', 'calls', 'Ci.Area')],
     source="spec Type_switches; go/types stmt.go typeSwitchStmt (T = the case's type when one type is listed)")
case('go', 'import-name-is-file-scoped', "an import's name is declared in the file block: two files of one package may import different packages under the same name",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a/u.go': 'package util\n\nfunc F() int {\n\treturn 1\n}\n', 'b/u.go': 'package util\n\nfunc F() int {\n\treturn 2\n}\n', 'x.go': 'package app\n\nimport "example.com/app/a"\n\nfunc RunA() int {\n\treturn util.F()\n}\n', 'y.go': 'package app\n\nimport "example.com/app/b"\n\nfunc RunB() int {\n\treturn util.F()\n}\n'},
     [('RunA', 'calls', 'a/u.go:F'), ('RunB', 'calls', 'b/u.go:F')], [('RunA', 'calls', 'b/u.go:F'), ('RunB', 'calls', 'a/u.go:F')],
     source='spec Import_declarations (declared in the file block); go/types resolver.go collectObjects (fileScope)')
case('go', 'import-path-picks-the-package', 'of two packages with the same clause name, `util.F()` is the F of the one whose path the file imports',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a/u.go': 'package util\n\nfunc F() int {\n\treturn 1\n}\n', 'b/u.go': 'package util\n\nfunc F() int {\n\treturn 2\n}\n', 'x.go': 'package app\n\nimport "example.com/app/b"\n\nfunc Run() int {\n\treturn util.F()\n}\n'},
     [('Run', 'calls', 'b/u.go:F')], [('Run', 'calls', 'a/u.go:F')],
     source='spec Import_declarations (ImportPath specifies the package to be imported)')
case('go', 'local-hides-an-import', 'a local declared with the name of an imported package hides it inside its block only',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'util/u.go': 'package util\n\nfunc F() int {\n\treturn 1\n}\n', 'a.go': 'package app\n\nimport "example.com/app/util"\n\ntype T struct{}\n\nfunc (T) F() int {\n\treturn 2\n}\n\nfunc Run() int {\n\tutil := T{}\n\treturn util.F()\n}\n\nfunc Other() int {\n\treturn util.F()\n}\n'},
     [('Run', 'calls', 'T.F'), ('Other', 'calls', 'util/u.go:F')], [('Run', 'calls', 'util/u.go:F')],
     source='spec Import_declarations / Declarations_and_scope (file block encloses function blocks)')
case('go', 'init-is-not-a-name', 'several `init` functions are declared in a package yet none is a name: `t.init()` is the method',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype T struct{}\n\nfunc (T) init() int {\n\treturn 1\n}\n\nfunc init() {\n\tprintln("a")\n}\n\nfunc init() {\n\tprintln("b")\n}\n\nfunc Run(t T) int {\n\treturn t.init()\n}\n', 'b.go': 'package app\n\nfunc init() {\n\tprintln("c")\n}\n'},
     [('Run', 'calls', 'T.init')], [('Run', 'calls', 'a.go:init'), ('Run', 'calls', 'b.go:init')],
     source='spec Package_initialization (init functions); go/types resolver.go collectObjects (init is not declared)')
case('go', 'package-variable-initializer-uses', "a package variable's initializer is a use made by the variable: its calls and reads of other package variables",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nfunc seed() int {\n\treturn 1\n}\n\nvar base = seed()\n\nvar total = base + 1\n\nfunc Run() int {\n\treturn total\n}\n'},
     [('base', 'calls', 'seed'), ('total', 'reads', 'base'), ('Run', 'reads', 'total')], [],
     source='spec Package_initialization (dependency analysis); go/types initorder.go dependencyGraph, check.go addDeclDep')
case('go', 'unsafe-is-the-builtin-package', '`import "unsafe"` names the built-in package: a project directory called unsafe is only reached by its full path',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'unsafe/u.go': 'package unsafe\n\nfunc Sizeof(x int) int {\n\treturn x\n}\n', 'a.go': 'package app\n\nimport "unsafe"\n\nfunc Run(x int) uintptr {\n\treturn unsafe.Sizeof(x)\n}\n'},
     [], [('Run', 'calls', 'unsafe/u.go:Sizeof')],
     source='spec Package_unsafe; go/types resolver.go (importer for "unsafe" is Unsafe)')
case('go', 'external-test-package-is-another-package', 'files of one directory whose package clauses differ form two packages: a_test.go (package app_test) calls its own helper, and reaches app only through its import',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nfunc helper() int { return 1 }\n\nfunc Run() int { return helper() }\n', 'a_test.go': 'package app_test\n\nimport (\n\t"testing"\n\n\t"example.com/app"\n)\n\nfunc helper() int { return 2 }\n\nfunc TestRun(t *testing.T) {\n\tif helper() != 2 || app.Run() != 1 {\n\t\tt.Fatal("x")\n\t}\n}\n'},
     [('Run', 'calls', 'a.go:helper'), ('TestRun', 'calls', 'a_test.go:helper'), ('TestRun', 'calls', 'Run')], [('TestRun', 'calls', 'a.go:helper'), ('Run', 'calls', 'a_test.go:helper')],
     source='spec Package_clause')
case('go', 'method-declared-in-another-file', 'a method is declared in any file of the package: s.Count() in a.go is the method written in b.go, not the package function Count',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Store struct{ n int }\n\nfunc Use(s *Store) int { return s.Count() }\n', 'b.go': 'package app\n\nfunc (s *Store) Count() int { return s.n }\n\nfunc Count() int { return 0 }\n'},
     [('Use', 'calls', 'Store.Count')], [('Use', 'calls', 'b.go:Count')],
     source='spec Packages')
case('go', 'package-function-hides-a-new-builtin', 'a package-level max hides the builtin max of Go 1.21: the call binds to the package function',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.21\n', 'a.go': 'package app\n\nfunc max(a, b int) int {\n\tif a > b {\n\t\treturn a\n\t}\n\treturn b\n}\n\nfunc Top() int { return max(1, 2) }\n'},
     [('Top', 'calls', 'max')], [],
     source='spec Language_versions', since='1.21')
case('go', 'go-parameter-named-like-import', 'a parameter named like an import hides the package only in the function body: its type in the signature still names the package',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'util/u.go': 'package util\n\ntype T struct{}\n\nfunc (t T) Method() int { return 1 }\n\nfunc Method() int { return 2 }\n', 'a.go': 'package app\n\nimport "example.com/app/util"\n\nfunc Run(util util.T) int {\n\treturn util.Method()\n}\n\nfunc Other() int {\n\treturn util.Method()\n}\n'},
     [('Run', 'references', 'util/u.go:T'), ('Run', 'calls', 'util/u.go:T.Method'), ('Other', 'calls', 'util/u.go:Method')], [('Run', 'calls', 'util/u.go:Method'), ('Other', 'calls', 'util/u.go:T.Method')],
     source='spec Import_declarations, Declarations_and_scope (parameter scope is the function body); go/types resolver.go collectObjects (file scope), stmt/signature scopes')
case('go', 'go-field-named-like-import', "a field named like an imported package is no name of the file block: h.util.Run() is the field type's method",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'util/u.go': 'package util\n\nfunc Run() int { return 2 }\n', 'a.go': 'package app\n\nimport "example.com/app/util"\n\ntype Worker struct{}\n\nfunc (w *Worker) Run() int { return 1 }\n\ntype Host struct {\n\tutil *Worker\n}\n\nfunc (h *Host) Go() int {\n\treturn h.util.Run()\n}\n\nfunc Plain() int {\n\treturn util.Run()\n}\n'},
     [('Host.Go', 'calls', 'a.go:Worker.Run'), ('Plain', 'calls', 'util/u.go:Run')], [('Host.Go', 'calls', 'util/u.go:Run'), ('Plain', 'calls', 'a.go:Worker.Run')],
     source='spec Import_declarations, Selectors; go/types call.go selector (package qualifier only when the operand identifier denotes a PkgName)')
case('go', 'go-local-hides-dot-import', 'a local declared in a function hides a dot-imported name inside that function',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'util/u.go': 'package util\n\nfunc Sin() int { return 1 }\n', 'a.go': 'package app\n\nimport . "example.com/app/util"\n\nfunc Run() int {\n\treturn Sin()\n}\n\nfunc Hidden() int {\n\tSin := func() int { return 2 }\n\treturn Sin()\n}\n'},
     [('Run', 'calls', 'util/u.go:Sin')], [('Hidden', 'calls', 'util/u.go:Sin'), ('Hidden', 'references', 'util/u.go:Sin')],
     source='spec Import_declarations, Declarations_and_scope (inner block redeclaration); go/types resolver.go dot-imports declared in the file scope')
case('go', 'go-local-named-init', 'a local named init is called as a local: the package init functions are no names',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nfunc init() {}\n\nfunc Run() int {\n\tinit := func() int { return 1 }\n\treturn init()\n}\n'},
     [], [('Run', 'calls', 'init'), ('Run', 'references', 'init')],
     source='spec Package_initialization; go/types resolver.go collectObjects (init functions are not declared in the package scope)')
case('go', 'go-func-literal-in-package-var', 'the calls inside a function literal assigned to a package-level variable belong to that variable',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nfunc helper() int { return 1 }\n\nvar handler = func() int {\n\treturn helper()\n}\n'},
     [('handler', 'calls', 'helper')], [],
     source="spec Package_initialization; go/types initorder.go dependencyGraph (uses recorded in the declaration's initializer, closures included)")
case('go', 'go-generic-alias-method', "a method called on a value of a generic alias is the aliased generic type's method",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.24\n', 'a.go': 'package app\n\ntype Box[T any] struct{ v T }\n\nfunc (b Box[T]) Get() T { return b.v }\n\ntype Cell[T any] = Box[T]\n\nfunc Run(c Cell[int]) int {\n\treturn c.Get()\n}\n'},
     [('Run', 'calls', 'Box.Get'), ('Run', 'references', 'Cell')], [],
     source='spec Alias_declarations, Language_versions (Go 1.24 generic aliases); go/types decl.go aliasDecl', since='1.24')
case('go', 'go-external-test-package-qualifier', "in package app_test, app.Run is the package under test and a bare Run is app_test's own function",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nfunc Run() int { return 1 }\n', 'x_test.go': 'package app_test\n\nimport (\n\t"testing"\n\n\t"example.com/app"\n)\n\nfunc Run() int { return 2 }\n\nfunc TestA(t *testing.T) { _ = app.Run() }\n\nfunc TestB(t *testing.T) { _ = Run() }\n'},
     [('TestA', 'calls', 'a.go:Run'), ('TestB', 'calls', 'x_test.go:Run')], [('TestA', 'calls', 'x_test.go:Run'), ('TestB', 'calls', 'a.go:Run')],
     source='spec Package_clause, Import_declarations; go/types resolver.go collectObjects (each package clause name is its own package)')

# (the third round of gap hunting)
case('go', 'blank-var-type-uses', "a blank variable declares no name, yet the types written in its declaration are uses of the project's types",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Iface interface{ Run() }\n\ntype Impl struct{}\n\nfunc (Impl) Run() {}\n\ntype Other struct{}\n\nfunc (Other) Run() {}\n\nvar _ Iface = (*Impl)(nil)\n'},
     [('a.go', 'references', 'Iface'), ('a.go', 'references', 'Impl')], [('a.go', 'references', 'Other'), ('a.go', 'references', '_')],
     source='spec Blank_identifier')
case('go', 'alias-typed-value-call', 'a method called on a value of an alias type is a method of the aliased type',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Impl struct{}\n\nfunc (Impl) Run() {}\n\ntype Other struct{}\n\nfunc (Other) Run() {}\n\ntype Alias = Impl\n\nfunc useAlias(a Alias) { a.Run() }\n'},
     [('useAlias', 'calls', 'Impl.Run'), ('useAlias', 'references', 'Alias')], [('useAlias', 'calls', 'Other.Run')],
     source='spec Declarations_and_scope (alias declarations); go/types resolver.go')
case('go', 'anonymous-struct-embeds-a-type', "a struct literal type that embeds a type promotes its methods: x.Hello() on a variable of it is Base.Hello, not another type's",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Base struct{}\n\nfunc (Base) Hello() int { return 1 }\n\ntype Other struct{}\n\nfunc (Other) Hello() int { return 2 }\n\nfunc Run() int {\n\tvar x struct {\n\t\tBase\n\t\tn int\n\t}\n\treturn x.Hello() + x.n\n}\n'},
     [('Run', 'calls', 'Base.Hello')], [('Run', 'calls', 'Other.Hello')],
     source='spec Struct_types (promoted methods); go/types lookup.go lookupFieldOrMethodImpl')
case('go', 'defined-from-an-instance-has-its-fields', 'type ItemBox Box[Item]: the field v of an ItemBox is an Item, so b.v.Size() is Item.Size',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Item struct{}\n\nfunc (Item) Size() int { return 1 }\n\ntype Other struct{}\n\nfunc (Other) Size() int { return 2 }\n\ntype Box[T any] struct{ v T }\n\nfunc (b Box[T]) Get() T { return b.v }\n\ntype ItemBox Box[Item]\n\nfunc Run(b ItemBox) int {\n\treturn b.v.Size()\n}\n'},
     [('Run', 'calls', 'Item.Size')], [('Run', 'calls', 'Other.Size')],
     source='spec Underlying_types, Type_definitions; go/types under.go under')
case('go', 'range-over-a-function-iterator', 'for it := range All() with All returning func(yield func(Item) bool): it is an Item, so it.Size() is Item.Size',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.23\n', 'a.go': 'package app\n\ntype Item struct{}\n\nfunc (Item) Size() int { return 1 }\n\ntype Other struct{}\n\nfunc (Other) Size() int { return 2 }\n\nfunc All() func(yield func(Item) bool) {\n\treturn func(yield func(Item) bool) { yield(Item{}) }\n}\n\nfunc Run() int {\n\tn := 0\n\tfor it := range All() {\n\t\tn += it.Size()\n\t}\n\treturn n\n}\n'},
     [('Run', 'calls', 'Item.Size')], [('Run', 'calls', 'Other.Size')],
     source='spec Function_types and For_range; go/types stmt.go rangeStmt (range over func, Go 1.23)', since='1.23')
case('go', 'embedded-field-named-by-its-type', "the embedded field of `util.Base` is named Base: `Svc{Base: b}` and `s.Base.Touch()` are the type's, not the importing package's variable Base",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nimport "example.com/app/util"\n\nvar Base = 1\n\ntype Svc struct {\n\tutil.Base\n}\n\nfunc Use(b util.Base) {\n\ts := Svc{Base: b}\n\ts.Base.Touch()\n}\n', 'util/u.go': 'package util\n\ntype Base struct{}\n\nfunc (b *Base) Touch() {}\n'},
     [('Use', 'calls', 'Base.Touch')], [('Use', 'reads', 'a.go:Base')],
     source='spec Struct_types, Selectors; go/types lookup.go lookupFieldOrMethodImpl')
case('go', 'function-typed-field-called', 'h.run() on a field of function type reads the field Hook.run; a method run of another type is not called',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Hook struct {\n\trun func()\n}\n\ntype Other struct{}\n\nfunc (Other) run() {}\n\nfunc Fire(h Hook) {\n\th.run()\n}\n'},
     [('Fire', 'reads', 'Hook.run')], [('Fire', 'calls', 'Other.run')],
     source='spec Selectors; go/types lookup.go (a field found by LookupFieldOrMethod is a variable, a call of it is a call of a value)')
case('go', 'short-var-literal-sees-outer-name', 'a literal assigned with := does not see the variable it initializes: its own name inside the body is the outer (package) one',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\nfunc step() {}\n\nfunc Walk() {\n\tstep := func() {\n\t\tstep()\n\t}\n\t_ = step\n}\n'},
     [('Walk', 'calls', 'step')], [],
     source='spec Declarations_and_scope; go/types stmt.go declStmt / assignment.go shortVarDecl (declared after the right-hand side is checked)')
case('go', 'method-expression-on-an-instance', '`Box[int].Get` is a use of Box.Get, not of Other.Get',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Box[T any] struct{ v T }\n\nfunc (b Box[T]) Get() T { return b.v }\n\ntype Other struct{}\n\nfunc (Other) Get() int { return 0 }\n\nfunc Use() {\n\tf := Box[int].Get\n\t_ = f\n}\n'},
     [('Use', 'references', 'Box.Get')], [('Use', 'references', 'Other.Get')],
     source='spec Method_expressions; go/types call.go selector (method expression on an instantiated type)')
case('go', 'go-import-name-is-per-file', 'an import name is a name of its file block: the same name in two files of a package means two packages',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'one/o.go': 'package one\n\nfunc F() int {\n\treturn 1\n}\n', 'two/t.go': 'package two\n\nfunc F() int {\n\treturn 2\n}\n', 'a.go': 'package app\n\nimport p "example.com/app/one"\n\nfunc RunA() int {\n\treturn p.F()\n}\n', 'b.go': 'package app\n\nimport p "example.com/app/two"\n\nfunc RunB() int {\n\treturn p.F()\n}\n'},
     [('RunA', 'calls', 'one/o.go:F'), ('RunB', 'calls', 'two/t.go:F')], [('RunA', 'calls', 'two/t.go:F'), ('RunB', 'calls', 'one/o.go:F')],
     source='spec Declarations_and_scope; go/types resolver.go collectObjects (file scope of imports)')
case('go', 'go-embedded-interface-in-struct', "a call of a method promoted from an interface embedded in a struct is the interface's method",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Store interface {\n\tFind() int\n}\n\ntype Other struct{}\n\nfunc (o *Other) Find() int {\n\treturn 2\n}\n\ntype Svc struct {\n\tStore\n}\n\nfunc Run(s *Svc) int {\n\treturn s.Find()\n}\n'},
     [('Run', 'calls', 'Store.Find')], [('Run', 'calls', 'Other.Find')],
     source='spec Struct_types (promoted); go/types call.go selector -> lookupFieldOrMethod')
case('go', 'go-parameter-hides-an-import-name', "a parameter hides an imported package's name in the function body only",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'util/u.go': 'package util\n\nfunc F() int {\n\treturn 1\n}\n', 'a.go': 'package app\n\nimport "example.com/app/util"\n\ntype Helper struct{}\n\nfunc (h Helper) F() int {\n\treturn 2\n}\n\nfunc Run(util Helper) int {\n\treturn util.F()\n}\n\nfunc Other() int {\n\treturn util.F()\n}\n'},
     [('Run', 'calls', 'Helper.F'), ('Other', 'calls', 'util/u.go:F')], [('Run', 'calls', 'util/u.go:F'), ('Other', 'calls', 'Helper.F')],
     source='spec Declarations_and_scope; go/types call.go selector (package-name branch only when the identifier denotes a PkgName)')
case('go', 'go-call-of-function-typed-variable', 'a call through a package variable of function type is a use of the variable; a multi-value call forwarded as arguments is a call',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype T struct{}\n\nfunc (t T) handler() int {\n\treturn 2\n}\n\nvar handler = func() int {\n\treturn 1\n}\n\nfunc pair() (int, int) {\n\treturn 1, 2\n}\n\nfunc sum(a, b int) int {\n\treturn a + b\n}\n\nfunc Run() int {\n\treturn handler() + sum(pair())\n}\n'},
     [('Run', 'reads', 'a.go:handler'), ('Run', 'calls', 'sum'), ('Run', 'calls', 'pair')], [('Run', 'calls', 'T.handler')],
     source='spec Calls; go/types call.go callExpr (a variable of function type is an ordinary operand)')
case('go', 'go-defer-closure-sees-named-result', "a function literal run by a defer statement sees the enclosing function's named result as the local it is: a method call on it is its type's method, not that of a package variable of the same name",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Tool struct{}\n\nfunc (Tool) Do() {}\n\ntype Other struct{}\n\nfunc (Other) Do() {}\n\nvar t Other\n\nfunc Run() (t Tool) {\n\tdefer func() { t.Do() }()\n\treturn\n}\n'},
     [('Run', 'calls', 'Tool.Do')], [('Run', 'calls', 'Other.Do')],
     source='spec Defer_statements, Return_statements: the function literal is in the scope of the result parameters')
case('go', 'go-func-literal-param-hides-outer-local', 'a parameter of a function literal hides a local of the enclosing function with the same name and has its own declared type',
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype Tool struct{}\n\nfunc (Tool) Do() {}\n\ntype Other struct{}\n\nfunc (Other) Do() {}\n\nfunc Lit() {\n\tx := Other{}\n\tfunc(x Tool) { x.Do() }(Tool{})\n\t_ = x\n}\n'},
     [('Lit', 'calls', 'Tool.Do')], [('Lit', 'calls', 'Other.Do')],
     source='spec Declarations_and_scope: the scope of a parameter is the function body')
case('go', 'same-clause-name-in-two-directories-is-two-packages', "two directories whose files share a package clause name are two packages: a bare helper() is the one of its own directory's package block",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'cmd/a/main.go': 'package main\n\nfunc helper() int { return 1 }\n\nfunc main() { helper() }\n', 'cmd/b/main.go': 'package main\n\nfunc helper() int { return 2 }\n\nfunc main() { helper() }\n'},
     [('cmd/a/main.go:main', 'calls', 'cmd/a/main.go:helper'), ('cmd/b/main.go:main', 'calls', 'cmd/b/main.go:helper')], [('cmd/a/main.go:main', 'calls', 'cmd/b/main.go:helper'), ('cmd/b/main.go:main', 'calls', 'cmd/a/main.go:helper')],
     source="spec Package_clause, Packages; go/types package.go (a package is one directory's files)")
case('go', 'dot-imported-function-result-types-a-local', "a dot-imported function's declared result gives the local its type: r.Find() is the imported type's method",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'util/u.go': 'package util\n\ntype Repo struct{}\n\nfunc (r *Repo) Find() int { return 1 }\n\nfunc New() *Repo { return &Repo{} }\n', 'a.go': 'package app\n\nimport . "example.com/app/util"\n\ntype Other struct{}\n\nfunc (o *Other) Find() int { return 2 }\n\nfunc Run() int {\n\tr := New()\n\treturn r.Find()\n}\n'},
     [('Run', 'calls', 'util/u.go:New'), ('Run', 'calls', 'Repo.Find')], [('Run', 'calls', 'Other.Find')],
     source='spec Import_declarations; go/types resolver.go collectObjects (dot-imports into the file scope)')
case('go', 'package-type-hides-the-predeclared-any', "a package type named any hides the universe's any: x.Run() on a parameter of type any is the package interface's method",
     GO, {'go.mod': 'module example.com/app\n\ngo 1.22\n', 'a.go': 'package app\n\ntype any interface{ Run() int }\n\ntype Job struct{}\n\nfunc (Job) Run() int { return 1 }\n\nfunc Use(x any) int { return x.Run() }\n'},
     [('Use', 'calls', 'any.Run'), ('Use', 'references', 'any')], [],
     source='spec Language_versions, Predeclared_identifiers; go/types scope.go (package scope is inside Universe)', since='1.18')

# C#, the sweep (docs/rule-sources.md, 'Phase 2b: the sweep'): the standard and
# Roslyn's binder read section by section by a smaller model, each case then
# given to the compiler, to Roslyn (`verify_cases.py --binding`) and to the
# engine before it was written here. A failing one is an open defect.
found_by("sweep")
case('csharp', 'namespace-and-generic-type-of-one-name', 'a namespace and a generic type share a name: `Result.Item` goes through the namespace, `Result<int>` is the type',
     CS, {'Result.cs': 'namespace App\n{\n    public class Result<T>\n    {\n        public int Get() { return 1; }\n    }\n}\n', 'Item.cs': 'namespace App.Result\n{\n    public class Item\n    {\n        public int Run() { return 2; }\n    }\n}\n', 'C.cs': 'namespace App\n{\n    public class C\n    {\n        public int M(Result<int> r) { return r.Get(); }\n\n        public int N(Result.Item i) { return i.Run(); }\n    }\n}\n'},
     [('C.M', 'calls', 'Result.Get'), ('C.N', 'calls', 'Item.Run')], [('C.N', 'calls', 'Result.Get')],
     source='csharpstandard basic-concepts.md §7.3 (last exception), §7.8.1 (x zero: a namespace of that name; else a type with x type parameters); Roslyn Binder_Symbols.cs BindNonGenericSimpleNamespaceOrTypeOrAliasSymbol, LookupGenericTypeName')
case('csharp', 'lambda-parameter-hides-local', 'a lambda parameter of a name hides a local of the method around, inside the lambda',
     CS, {'A.cs': 'using System;\n\nnamespace App\n{\n    public class A\n    {\n        public int Run() { return 1; }\n    }\n\n    public class B\n    {\n        public int Run() { return 2; }\n    }\n\n    public class C\n    {\n        public Func<B, int> M()\n        {\n            A x = new A();\n            Func<B, int> f = (B x) => x.Run();\n            return f;\n        }\n    }\n}\n'},
     [('C.M', 'calls', 'B.Run')], [('C.M', 'calls', 'A.Run')],
     source='csharpstandard basic-concepts.md §7.3 (declaration spaces of anonymous functions), §7.7.2.2 (HidingNesting1: the lambda parameter i hides the float i); Roslyn Binder_NameConflicts.cs ValidateNameConflictsInScope (IDS_FeatureNameShadowingInNestedFunctions)')
case('csharp', 'switch-section-local-scope', 'a local declared in one switch section is in scope in a later section, and hides a field of its name',
     CS, {'A.cs': 'namespace App\n{\n    public class A\n    {\n        public int Run() { return 1; }\n    }\n\n    public class B\n    {\n        public int Run() { return 2; }\n    }\n\n    public class C\n    {\n        private B y;\n\n        public int M(int k)\n        {\n            switch (k)\n            {\n                case 0:\n                    A y = null;\n                    break;\n                default:\n                    y = new A();\n                    return y.Run();\n            }\n            return 0;\n        }\n    }\n}\n'},
     [('C.M', 'calls', 'A.Run')], [('C.M', 'calls', 'B.Run')],
     source='csharpstandard basic-concepts.md §7.3 (the SwitchSurprise note: y is in scope within the default section)')
case('csharp', 'inaccessible-base-member-skipped', 'a private member of a base is not accessible in a derived class outside it, so a bare name is the member of the class around',
     CS, {'A.cs': 'namespace App\n{\n    public class Outer\n    {\n        private static int Helper() { return 1; }\n\n        public class Base\n        {\n            private static int Helper(string s) { return 2; }\n        }\n\n        public class Derived : Base\n        {\n            public int M() { return Helper(); }\n        }\n    }\n}\n'},
     [('Derived.M', 'calls', 'Outer.Helper')], [('Derived.M', 'calls', 'Base.Helper')],
     source="csharpstandard basic-concepts.md §7.7.1 (a class member's scope reaches derived classes in its accessibility domain), §7.5.3; Roslyn Binder_Lookup.cs CheckViability, LookupSymbolsInternal (the search goes on while the result is not viable)")
case('csharp', 'inaccessible-hider-hides-nothing', "a private member that hides an inherited one hides it only inside its own class: a further derived class names the base's",
     CS, {'A.cs': 'namespace App\n{\n    public class Base\n    {\n        public static int F(int x) { return 1; }\n    }\n\n    public class Derived : Base\n    {\n        private new static int F(string s) { return 2; }\n    }\n\n    public class MoreDerived : Derived\n    {\n        public static int G() { return F(1); }\n    }\n}\n'},
     [('MoreDerived.G', 'calls', 'Base.F')], [('MoreDerived.G', 'calls', 'Derived.F')],
     source='csharpstandard basic-concepts.md §7.7.2.3 (HidingInherit3); Roslyn Binder_Lookup.cs CheckViability')
case('csharp', 'inaccessible-nested-type-of-a-base', "a private nested type of a base class is not a meaning of a type name in the derived class: the namespace's type is",
     CS, {'A.cs': 'namespace App\n{\n    public class Item\n    {\n        public int Run() { return 1; }\n    }\n\n    public class Base\n    {\n        private class Item\n        {\n        }\n    }\n\n    public class Derived : Base\n    {\n        public int M(Item i) { return i.Run(); }\n    }\n}\n'},
     [('Derived.M', 'references', 'A.cs:App.Item'), ('Derived.M', 'calls', 'Item.Run')], [('Derived.M', 'references', 'Base.Item')],
     source='csharpstandard basic-concepts.md §7.8.1 (R0: a nested accessible type having name I); Roslyn Binder_Lookup.cs CheckViability')
case('csharp', 'derived-overload-of-another-signature', 'a derived method hides base methods of the same signature only: a base overload of another signature is still called',
     CS, {'A.cs': 'namespace App\n{\n    public class Base\n    {\n        public int Show(int n) { return 1; }\n    }\n\n    public class Derived : Base\n    {\n        public int Show(string s) { return 2; }\n\n        public int UseInt() { return Show(1); }\n\n        public int UseText() { return Show("x"); }\n    }\n}\n'},
     [('Derived.UseInt', 'calls', 'Base.Show'), ('Derived.UseText', 'calls', 'Derived.Show')], [('Derived.UseInt', 'calls', 'Derived.Show'), ('Derived.UseText', 'calls', 'Base.Show')],
     source='csharpstandard basic-concepts.md §7.7.2.3 (a method hides base methods of the same signature, §7.6); Roslyn Binder_Lookup.cs MergeHidingLookupResults (hiding by signature is left to overload lookup)')
case('csharp', 'class-type-parameter-hides-type', "a class's type parameter hides a namespace type of its name in the class, and only there",
     CS, {'A.cs': 'namespace App\n{\n    public interface IRunner\n    {\n        int Run();\n    }\n\n    public class T\n    {\n        public int Run() { return 1; }\n    }\n\n    public class Box<T> where T : IRunner\n    {\n        public int M(T t) { return t.Run(); }\n    }\n\n    public class Plain\n    {\n        public int N(T t) { return t.Run(); }\n    }\n}\n'},
     [('Box.M', 'calls', 'IRunner.Run'), ('Plain.N', 'calls', 'App.T.Run')], [('Box.M', 'calls', 'App.T.Run'), ('Plain.N', 'calls', 'IRunner.Run')],
     source="csharpstandard basic-concepts.md §7.7.1 (scope of a class's type parameter), §7.8.1 (R0: a type parameter of T first); Roslyn Binder_Lookup.cs LookupSymbolsInSingleBinder")
case('csharp', 'type-parameter-scope-ends-with-its-class', "a base class's type parameter is no name in a derived class: `T` there is the namespace's type",
     CS, {'A.cs': 'namespace App\n{\n    public class T\n    {\n        public int Run() { return 1; }\n    }\n\n    public class Base<T>\n    {\n        public T Value;\n    }\n\n    public class Derived : Base<int>\n    {\n        public int M(T t) { return t.Run(); }\n    }\n}\n'},
     [('Derived.M', 'calls', 'App.T.Run'), ('Derived.M', 'references', 'App.T')], [],
     source="csharpstandard basic-concepts.md §7.7.1 (note to the scope of a class's type parameter)")
case('csharp', 'local-scope-ends-with-its-block', "a local's scope ends with its block or its `for`: after it the name is the field again",
     CS, {'A.cs': 'namespace App\n{\n    public class A\n    {\n        public int Run() { return 1; }\n    }\n\n    public class B\n    {\n        public int Run() { return 2; }\n    }\n\n    public class C\n    {\n        private B x = new B();\n\n        public int M()\n        {\n            {\n                A x = new A();\n            }\n            return x.Run();\n        }\n\n        public int N()\n        {\n            for (A x = new A(); x == null; x = null)\n            {\n            }\n            return x.Run();\n        }\n    }\n}\n'},
     [('C.M', 'calls', 'B.Run'), ('C.N', 'calls', 'B.Run')], [('C.M', 'calls', 'A.Run'), ('C.N', 'calls', 'A.Run')],
     source='csharpstandard basic-concepts.md §7.7.1 (a local: the block; a for_initializer variable: the for statement); Roslyn Binder_Lookup.cs LookupSymbolsInternal (one binder per scope)')
case('csharp', 'local-hides-field-in-its-block', 'a local of a name hides the field of that name in its whole block, its own declarator included',
     CS, {'A.cs': 'namespace App\n{\n    public class A\n    {\n        public int Run() { return 1; }\n    }\n\n    public class B\n    {\n        public int Run() { return 2; }\n    }\n\n    public class C\n    {\n        private B x = new B();\n\n        public int M()\n        {\n            A x = new A();\n            int j = (j = 1);\n            return x.Run() + j;\n        }\n    }\n}\n'},
     [('C.M', 'calls', 'A.Run')], [('C.M', 'calls', 'B.Run')],
     source="csharpstandard basic-concepts.md §7.7.1 (ScopeGeneral2: G's use of j in its initializer is valid)")
case('csharp', 'field-named-like-a-type-is-no-type', 'where a type is written, a field of the same name is ignored',
     CS, {'A.cs': 'namespace App\n{\n    public class Item\n    {\n        public int Run() { return 1; }\n    }\n\n    public class C\n    {\n        public string Item;\n\n        public int M(Item i) { return i.Run(); }\n    }\n}\n'},
     [('C.M', 'calls', 'Item.Run'), ('C.M', 'references', 'App.Item')], [('C.M', 'reads', 'C.Item')],
     source='csharpstandard basic-concepts.md §7.8.1 (R0, the Note); Roslyn Binder_Lookup.cs LookupSymbolsInSingleBinder (LookupOptions.NamespacesOrTypesOnly), CheckViability')
case('csharp', 'inner-member-hides-outer-overloads', "a member of a nested type hides every overload of the outer type's of its name: `F(1)` is the inner `F(long)`, not the outer `F(int)`",
     CS, {'A.cs': 'namespace App\n{\n    public class Outer\n    {\n        public static int F(int i) { return 1; }\n\n        public class Inner\n        {\n            public static int F(long l) { return 3; }\n\n            public int G() { return F(1); }\n        }\n    }\n}\n'},
     [('Inner.G', 'calls', 'Inner.F')], [('Inner.G', 'calls', 'Outer.F')],
     source='csharpstandard basic-concepts.md §7.7.2.2 (HidingNesting2); Roslyn Binder_Lookup.cs LookupSymbolsInternal (the first scope with a viable result ends the search)')
case('csharp', 'interface-member-hides-base-interface-member', "an interface's `new` member hides the base interface's of its signature for a value typed by the derived interface",
     CS, {'A.cs': 'namespace App\n{\n    public interface IA\n    {\n        int Run();\n    }\n\n    public interface IB : IA\n    {\n        new int Run();\n    }\n\n    public class C\n    {\n        public int M(IB b) { return b.Run(); }\n\n        public int N(IA a) { return a.Run(); }\n    }\n}\n'},
     [('C.M', 'calls', 'IB.Run'), ('C.N', 'calls', 'IA.Run')], [('C.M', 'calls', 'IA.Run'), ('C.N', 'calls', 'IB.Run')],
     source='csharpstandard basic-concepts.md §7.7.2.3; Roslyn Binder_Lookup.cs MergeHidingLookupResults (hidingContainerIsInterface, IsDerivedType)')
case('csharp', 'namespace-before-outer-type', 'inside a namespace, `Sub` is its nested namespace, not a global type of that name',
     CS, {'Helper.cs': 'namespace App.Sub\n{\n    public class Helper\n    {\n        public static int Run() { return 1; }\n    }\n}\n', 'Sub.cs': 'public class Sub\n{\n    public class Helper\n    {\n        public static int Run() { return 2; }\n    }\n}\n', 'C.cs': 'namespace App\n{\n    public class C\n    {\n        public int M() { return Sub.Helper.Run(); }\n    }\n}\n'},
     [('C.M', 'calls', 'Helper.cs:App.Sub.Helper.Run')], [('C.M', 'calls', 'Sub.cs:Sub.Helper.Run')],
     source='csharpstandard basic-concepts.md §7.8.1 (for each namespace N: a namespace named I); Roslyn Binder_Symbols.cs BindNonGenericSimpleNamespaceOrTypeOrAliasSymbol, Binder_Lookup.cs LookupMembersInNamespace')
case('csharp', 'global-alias-qualifier', "`global::Item` is the global namespace's type, `Item` the one of the namespace around",
     CS, {'Item.cs': 'public class Item\n{\n    public int Run() { return 1; }\n}\n', 'A.cs': 'namespace App\n{\n    public class Item\n    {\n        public int Run() { return 2; }\n    }\n\n    public class C\n    {\n        public int M(global::Item i) { return i.Run(); }\n\n        public int N(Item i) { return i.Run(); }\n    }\n}\n'},
     [('C.M', 'calls', 'Item.cs:Item.Run'), ('C.N', 'calls', 'A.cs:App.Item.Run')], [('C.M', 'calls', 'A.cs:App.Item.Run'), ('C.N', 'calls', 'Item.cs:Item.Run')],
     source='csharpstandard basic-concepts.md §7.8.1 (leading part: a qualified_alias_member), namespaces.md §14.8; Roslyn Binder_Symbols.cs BindNamespaceAliasSymbol, BindQualifiedName')
case('csharp', 'using-alias-of-a-namespace', '`using L = Lib;` makes `L.Repo` the type of Lib, though `using Other;` imports another Repo',
     CS, {'Lib.cs': 'namespace Lib\n{\n    public class Repo\n    {\n        public int Find() { return 1; }\n    }\n}\n', 'Other.cs': 'namespace Other\n{\n    public class Repo\n    {\n        public int Find() { return 2; }\n    }\n}\n', 'A.cs': 'using L = Lib;\nusing Other;\n\nnamespace App\n{\n    public class C\n    {\n        public int M(L.Repo r) { return r.Find(); }\n    }\n}\n'},
     [('C.M', 'calls', 'Lib.Repo.Find')], [('C.M', 'calls', 'Other.Repo.Find')],
     source='csharpstandard basic-concepts.md §7.8.1 (R0 and the trailing part: a namespace containing an accessible type I); Roslyn Binder_Symbols.cs BindQualifiedName')
case('csharp', 'nested-type-of-a-constructed-type', "`G<int>.H` is the type nested in G, not the namespace's `H`",
     CS, {'A.cs': 'namespace App\n{\n    public class H\n    {\n        public int Go() { return 1; }\n    }\n\n    public class G<T>\n    {\n        public class H\n        {\n            public int Go() { return 2; }\n        }\n    }\n\n    public class C\n    {\n        public int M(G<int>.H h) { return h.Go(); }\n    }\n}\n'},
     [('C.M', 'references', 'G.H'), ('C.M', 'calls', 'G.H.Go')], [('C.M', 'references', 'A.cs:App.H'), ('C.M', 'calls', 'A.cs:App.H.Go')],
     source='csharpstandard basic-concepts.md §7.8.1 (trailing repetition on a constructed type); Roslyn Binder_Symbols.cs BindQualifiedName, Binder_Lookup.cs LookupMembersInType')
case('csharp', 'base-list-ignores-inherited-nested-types', "a name in a type's base list is looked up outside the type: an inherited nested type is not a meaning",
     CS, {'A.cs': 'namespace App\n{\n    public interface IHolder<T> where T : class\n    {\n    }\n\n    public class Inner\n    {\n    }\n\n    public class Base\n    {\n        public struct Inner\n        {\n        }\n    }\n\n    public class Derived : Base, IHolder<Inner>\n    {\n    }\n}\n'},
     [('Derived', 'references', 'A.cs:App.Inner')], [('Derived', 'references', 'Base.Inner')],
     source='csharpstandard basic-concepts.md §7.8.1 (the Note at the nested-type step); Roslyn Binder_Lookup.cs LookupMembersInClass (basesBeingResolved), Binder_Symbols.cs BindNamespaceOrTypeSymbol')
case('csharp', 'type-parameter-constraint-member', 'a member of a value typed by a type parameter is a member of its constraint (interface or base class)',
     CS, {'A.cs': 'namespace App\n{\n    public interface IA\n    {\n        int Run();\n    }\n\n    public interface IB\n    {\n        int Run();\n    }\n\n    public class Base\n    {\n        public int Id() { return 1; }\n    }\n\n    public class Svc\n    {\n        public int M<T>(T t) where T : IA { return t.Run(); }\n\n        public int N<T>(T t) where T : Base { return t.Id(); }\n    }\n}\n'},
     [('Svc.M', 'calls', 'IA.Run'), ('Svc.N', 'calls', 'Base.Id')], [('Svc.M', 'calls', 'IB.Run')],
     source='csharpstandard expressions.md §12.5 (member lookup in a type parameter); Roslyn Binder_Lookup.cs LookupMembersInTypeParameter')
case('csharp', 'attribute-suffix', '`[Cache]` names the attribute class `CacheAttribute`, not a class `Cache` that is no attribute',
     CS, {'A.cs': 'using System;\n\nnamespace App\n{\n    public class Cache\n    {\n    }\n\n    public class CacheAttribute : Attribute\n    {\n    }\n\n    public class C\n    {\n        [Cache]\n        public int M() { return 1; }\n    }\n}\n'},
     [('C.M', 'references', 'CacheAttribute')], [('C.M', 'references', 'A.cs:App.Cache')],
     source='csharpstandard attributes.md §22.3 (attribute names); Roslyn Binder_Lookup.cs LookupAttributeType, IsSingleViableAttributeType')
case('csharp', 'local-function-local-hides-outer-local', 'a local declared in a local function hides a local of the method around, inside the function only',
     CS, {'A.cs': 'namespace App\n{\n    public class A\n    {\n        public int Run() { return 1; }\n    }\n\n    public class B\n    {\n        public int Run() { return 2; }\n    }\n\n    public class C\n    {\n        public int M()\n        {\n            A x = new A();\n\n            int Inner()\n            {\n                B x = new B();\n                return x.Run();\n            }\n\n            return Inner() + x.Run();\n        }\n    }\n}\n'},
     [('C.M', 'calls', 'A.Run'), ('C.M.Inner', 'calls', 'B.Run')], [('C.M.Inner', 'calls', 'A.Run')],
     source='Roslyn Binder_NameConflicts.cs ValidateNameConflictsInScope (IDS_FeatureNameShadowingInNestedFunctions); csharpstandard basic-concepts.md §7.7.2.2 (HidingNesting1: inside the local function M1 the float i hides the immediate-outer i)')
case('csharp', 'setter-value-hides-field', "in a `set` accessor `value` is the property's implicit parameter, not a field of that name",
     CS, {'A.cs': 'namespace App\n{\n    public class A\n    {\n        public int Run() { return 1; }\n    }\n\n    public class B\n    {\n        public int Run() { return 2; }\n    }\n\n    public class C\n    {\n        private B value = new B();\n\n        public A Prop\n        {\n            set { value.Run(); }\n        }\n    }\n}\n'},
     [('C.Prop', 'calls', 'A.Run')], [('C.Prop', 'calls', 'B.Run')],
     source='csharpstandard basic-concepts.md §7.3 (local variable declaration space of a property accessor: the name value)')
case('csharp', 'using-static-nested-type', 'a `using static N.Outer;` makes the nested type Inner a name of the file',
     CS, {'A.cs': 'namespace Lib { public class Outer { public class Inner { public static void Go() { } } } }\nnamespace Elsewhere { public class Inner { public static void Go() { } } }\n', 'B.cs': 'using static Lib.Outer;\nnamespace App { class Use { void Run() { Inner.Go(); } } }\n'},
     [('Use.Run', 'calls', 'Outer.Inner.Go')], [('Use.Run', 'calls', 'Elsewhere.Inner.Go')],
     source='csharpstandard §7.8 / §14.5.4 using static directives')
case('csharp', 'explicit-interface-impl-not-found-by-name', 'an explicit interface implementation `void I.M()` is no member of its class by name: `M()` in the class means the inherited `B.M`',
     CS, {'A.cs': 'namespace App {\n  public interface I { void M(); }\n  public class B { public void M() {} }\n  public class C : B, I {\n    void I.M() {}\n    public void Run() { M(); }\n  }\n}\n'},
     [('C.Run', 'calls', 'B.M')], [('C.Run', 'calls', 'C.M'), ('C.Run', 'calls', 'I.M')],
     source='csharpstandard §7.4 / Roslyn Binder_Lookup.cs LookupMembersInClass (explicit implementations are not looked up by name)')
case('csharp', 'local-function-hides-method-in-whole-block', 'a local function hides a method of its name throughout its block, including a call written before its declaration',
     CS, {'A.cs': 'namespace App {\n  public class E {\n    public void Helper() {}\n    public void Run() { Helper(); void Helper() {} }\n  }\n}\n'},
     [('E.Run', 'calls', 'Run.Helper')], [('E.Run', 'calls', 'E.Helper')],
     source='csharpstandard §7.7 (block scope), Roslyn Binder_NameConflicts.cs')
case('csharp', 'csharp-pattern-variable-if-scope', '`if (o is Box x)` declares x for the if statement only: inside it x is the local, after it x is the field',
     CS, {'A.cs': 'namespace App {\n  public class Box { public void Open() {} }\n  public class Holder {\n    Box x;\n    public void Inside(object o) { if (o is Box x) { x.Open(); } }\n    public void After(object o) { if (o is Box b) { } x.Open(); }\n  }\n}\n'},
     [('Holder.After', 'reads', 'Holder.x'), ('Holder.Inside', 'calls', 'Box.Open')], [('Holder.Inside', 'reads', 'Holder.x')],
     source='csharpstandard §7.7 (scopes of local variables and expression variables)')
case('csharp', 'csharp-out-variable-block-scope', '`Make(out var x);` declares x in the enclosing block: a later `x.Open()` is the local, not the field x',
     CS, {'A.cs': 'namespace App {\n  public class Box { public void Open() {} }\n  public class Holder {\n    Box x;\n    static void Make(out Box b) { b = new Box(); }\n    public void Leaks() { Make(out var x); x.Open(); }\n  }\n}\n'},
     [('Holder.Leaks', 'calls', 'Holder.Make'), ('Holder.Leaks', 'calls', 'Box.Open')], [('Holder.Leaks', 'reads', 'Holder.x')],
     source='csharpstandard §7.7 (scope of expression variables)')
case('csharp', 'global-namespace-type', 'a type of the global namespace is visible from every namespace',
     CS, {'G.cs': 'public class Tool\n{\n    public int Use() { return 1; }\n}\n', 'App/A.cs': 'namespace App.Core\n{\n    public class A\n    {\n        public int Run(Tool t) { return t.Use(); }\n    }\n}\n', 'Other/T.cs': 'namespace Other\n{\n    public class Tool\n    {\n        public int Use() { return 2; }\n    }\n}\n'},
     [('A.Run', 'calls', 'Tool.Use')], [('A.Run', 'calls', 'Other.Tool.Use')],
     source='csharpstandard §14.2 (namespaces.md)')
case('csharp', 'using-is-per-file', 'the usings of a compilation unit have no effect on another',
     CS, {'A.cs': 'using Lib;\n\nnamespace App\n{\n    public class A\n    {\n        public int Run(Repo r) { return r.Find(); }\n    }\n}\n', 'B.cs': 'using Elsewhere;\n\nnamespace App\n{\n    public class B\n    {\n        public int Run(Repo r) { return r.Find(); }\n    }\n}\n', 'Lib/Repo.cs': 'namespace Lib\n{\n    public class Repo\n    {\n        public int Find() { return 1; }\n        public static int Make() { return 1; }\n    }\n}\n', 'Elsewhere/Repo.cs': 'namespace Elsewhere\n{\n    public class Repo\n    {\n        public int Find() { return 2; }\n        public static int Make() { return 2; }\n    }\n}\n'},
     [('A.Run', 'calls', 'Lib.Repo.Find'), ('B.Run', 'calls', 'Elsewhere.Repo.Find')], [('A.Run', 'calls', 'Elsewhere.Repo.Find'), ('B.Run', 'calls', 'Lib.Repo.Find')],
     source='csharpstandard §14.2 (namespaces.md)')
case('csharp', 'namespace-open-ended', "a namespace declared in several files is one: its types are visible before a `using`'s",
     CS, {'Lib/Repo.cs': 'namespace Lib\n{\n    public class Repo\n    {\n        public int Find() { return 1; }\n        public static int Make() { return 1; }\n    }\n}\n', 'App/A.cs': 'using Lib;\n\nnamespace App\n{\n    public class A\n    {\n        public int Run(Repo r) { return r.Find(); }\n    }\n}\n', 'App/Repo.cs': 'namespace App\n{\n    public class Repo\n    {\n        public int Find() { return 2; }\n        public static int Make() { return 2; }\n    }\n}\n'},
     [('A.Run', 'calls', 'App.Repo.Find')], [('A.Run', 'calls', 'Lib.Repo.Find')],
     source='csharpstandard §14.3 (namespaces.md)')
case('csharp', 'alias-to-namespace', "a `using` alias names a namespace, and `R.Repo` is that namespace's type",
     CS, {'Lib/Repo.cs': 'namespace Lib.Data\n{\n    public class Repo\n    {\n        public int Find() { return 1; }\n        public static int Make() { return 1; }\n    }\n}\n', 'App/A.cs': 'using R = Lib.Data;\n\nnamespace App\n{\n    public class A\n    {\n        public int Run(R.Repo r)\n        {\n            return r.Find();\n        }\n    }\n}\n', 'Other/Repo.cs': 'namespace Other.Data\n{\n    public class Repo\n    {\n        public int Find() { return 2; }\n        public static int Make() { return 2; }\n    }\n}\n'},
     [('A.Run', 'calls', 'Lib/Repo.cs:Lib.Data.Repo.Find')], [('A.Run', 'calls', 'Other/Repo.cs:Other.Data.Repo.Find')],
     source='csharpstandard §14.5.2 (namespaces.md)')
case('csharp', 'alias-hidden-by-namespace-member', 'a using alias is hidden by a type of the same name in a nested namespace',
     CS, {'Lib/Repo.cs': 'namespace Lib\n{\n    public class Repo\n    {\n        public int Find() { return 1; }\n        public static int Make() { return 1; }\n    }\n}\n', 'App/A.cs': 'using R = Lib.Repo;\n\nnamespace App\n{\n    public class R\n    {\n        public static int Find() { return 2; }\n    }\n\n    public class A\n    {\n        public int Run()\n        {\n            return R.Find();\n        }\n    }\n}\n'},
     [('A.Run', 'calls', 'App.R.Find')], [('A.Run', 'calls', 'Lib.Repo.Find')],
     source='csharpstandard §14.5.2 (namespaces.md)')
case('csharp', 'alias-innermost-first', 'an alias of an inner namespace body hides one of the same name of the file, which holds elsewhere',
     CS, {'A.cs': 'using R = Lib.Repo;\n\nnamespace App\n{\n    using R = Other.Repo;\n\n    public class A\n    {\n        public int Run(R r) { return r.Find(); }\n    }\n}\n\nnamespace Elsewhere\n{\n    public class B\n    {\n        public int Run(R r) { return r.Find(); }\n    }\n}\n', 'Lib/Repo.cs': 'namespace Lib\n{\n    public class Repo\n    {\n        public int Find() { return 1; }\n        public static int Make() { return 1; }\n    }\n}\n', 'Other/Repo.cs': 'namespace Other\n{\n    public class Repo\n    {\n        public int Find() { return 2; }\n        public static int Make() { return 2; }\n    }\n}\n'},
     [('A.Run', 'calls', 'Other/Repo.cs:Other.Repo.Find'), ('B.Run', 'calls', 'Lib/Repo.cs:Lib.Repo.Find')], [('A.Run', 'calls', 'Lib/Repo.cs:Lib.Repo.Find'), ('B.Run', 'calls', 'Other/Repo.cs:Other.Repo.Find')],
     source='csharpstandard §14.5.2 (namespaces.md)')
case('csharp', 'alias-per-partial-part', 'each part of a partial type sees the aliases of its own namespace body',
     CS, {'A.cs': 'namespace App\n{\n    using Store = Lib.Repo;\n\n    public partial class A\n    {\n        public int First(Store s) { return s.Find(); }\n    }\n}\n', 'B.cs': 'namespace App\n{\n    using Store = Other.Repo;\n\n    public partial class A\n    {\n        public int Second(Store s) { return s.Find(); }\n    }\n}\n', 'Lib/Repo.cs': 'namespace Lib\n{\n    public class Repo\n    {\n        public int Find() { return 1; }\n        public static int Make() { return 1; }\n    }\n}\n', 'Other/Repo.cs': 'namespace Other\n{\n    public class Repo\n    {\n        public int Find() { return 1; }\n        public static int Make() { return 1; }\n    }\n}\n'},
     [('A.First', 'calls', 'Lib.Repo.Find'), ('A.Second', 'calls', 'Other.Repo.Find')], [('A.First', 'calls', 'Other.Repo.Find'), ('A.Second', 'calls', 'Lib.Repo.Find')],
     source='csharpstandard §14.5.2 (namespaces.md)')
case('csharp', 'alias-closed-generic', 'a using alias of a closed constructed type is that type',
     CS, {'A.cs': 'using IntBox = Lib.Box<int>;\n\nnamespace App\n{\n    public class A\n    {\n        public int Run(IntBox b) { return b.Get(); }\n    }\n}\n', 'Lib/Box.cs': 'namespace Lib\n{\n    public class Box<T>\n    {\n        public T Get() { return default(T); }\n    }\n\n    public class Other\n    {\n        public int Get() { return 1; }\n    }\n}\n'},
     [('A.Run', 'calls', 'Box.Get')], [('A.Run', 'calls', 'Other.Get')],
     source='csharpstandard §14.5.2 (namespaces.md)')
case('csharp', 'using-does-not-import-namespaces', '`using N;` imports no namespace of N: `Inner.Part` is the global Inner',
     CS, {'A.cs': 'using Lib;\n\nnamespace App\n{\n    public class A\n    {\n        public int Run(Inner.Part p) { return p.Find(); }\n    }\n}\n', 'Inner.cs': 'namespace Inner\n{\n    public class Part\n    {\n        public int Find() { return 2; }\n    }\n}\n', 'Lib/Inner.cs': 'namespace Lib.Inner\n{\n    public class Part\n    {\n        public int Find() { return 1; }\n    }\n}\n'},
     [('A.Run', 'calls', 'Inner.cs:Inner.Part.Find')], [('A.Run', 'calls', 'Lib/Inner.cs:Lib.Inner.Part.Find')],
     source='csharpstandard §14.5.3 (namespaces.md)')
case('csharp', 'inner-using-before-outer-type', 'a using of the inner namespace body comes before a type of the namespace around',
     CS, {'A.cs': 'namespace Outer\n{\n    public class Repo\n    {\n        public int Find() { return 2; }\n    }\n\n    namespace Inner\n    {\n        using Lib;\n\n        public class C\n        {\n            public int Run(Repo r) { return r.Find(); }\n        }\n    }\n}\n', 'Lib/Repo.cs': 'namespace Lib\n{\n    public class Repo\n    {\n        public int Find() { return 1; }\n        public static int Make() { return 1; }\n    }\n}\n'},
     [('C.Run', 'calls', 'Lib.Repo.Find')], [('C.Run', 'calls', 'Outer.Repo.Find')],
     source='csharpstandard §14.5.3 (namespaces.md)')
case('csharp', 'using-static-extension', "`using static C;` makes C's extension methods callable as extensions",
     CS, {'Lib/E.cs': 'namespace Lib\n{\n    public static class Ext\n    {\n        public static string Shout(this string s) { return s; }\n    }\n}\n', 'App/A.cs': 'using static Lib.Ext;\n\nnamespace App\n{\n    public class A\n    {\n        public string Run()\n        {\n            return "b".Shout();\n        }\n    }\n}\n', 'Other/E.cs': 'namespace Other\n{\n    public static class Ext2\n    {\n        public static string Shout(this string s) { return s + s; }\n    }\n}\n'},
     [('A.Run', 'calls', 'Ext.Shout')], [('A.Run', 'calls', 'Ext2.Shout')],
     source='csharpstandard §14.5.4 (namespaces.md)')
case('csharp', 'using-static-not-inherited', '`using static T;` imports what T declares, not what its bases declare',
     CS, {'Lib/A.cs': 'namespace Lib\n{\n    public class Base\n    {\n        public static int Twice(int x) { return 2 * x; }\n    }\n\n    public class Derived : Base\n    {\n        public static int Thrice(int x) { return 3 * x; }\n    }\n}\n', 'App/A.cs': 'using static Lib.Derived;\nusing static Other.Util;\n\nnamespace App\n{\n    public class A\n    {\n        public int Run()\n        {\n            return Twice(2) + Thrice(2);\n        }\n    }\n}\n', 'Other/U.cs': 'namespace Other\n{\n    public static class Util\n    {\n        public static int Twice(int x) { return 4 * x; }\n    }\n}\n'},
     [('A.Run', 'calls', 'Util.Twice'), ('A.Run', 'calls', 'Derived.Thrice')], [('A.Run', 'calls', 'Base.Twice')],
     source='csharpstandard §14.5.4 (namespaces.md)')
case('csharp', 'global-qualifier', '`global::Repo` is the type of the global namespace, not the one of the namespace around',
     CS, {'A.cs': 'namespace App\n{\n    public class Repo\n    {\n        public int Find() { return 2; }\n    }\n\n    public class A\n    {\n        public int Run(global::Repo r)\n        {\n            return r.Find();\n        }\n    }\n}\n', 'G.cs': 'public class Repo\n{\n    public int Find() { return 1; }\n}\n'},
     [('A.Run', 'calls', 'Repo.Find')], [('A.Run', 'calls', 'App.Repo.Find')],
     source='csharpstandard §14.8.1 (namespaces.md)')
case('csharp', 'alias-qualifier', '`S::Repo` looks S up only as an alias, never as the type S of the namespace',
     CS, {'A.cs': 'using S = Lib;\n\nnamespace App\n{\n    public class S\n    {\n        public static int Find() { return 2; }\n        public int Find2() { return 3; }\n    }\n\n    public class A\n    {\n        public int Run(S::Repo r)\n        {\n            return r.Find();\n        }\n    }\n}\n', 'Lib/Repo.cs': 'namespace Lib\n{\n    public class Repo\n    {\n        public int Find() { return 1; }\n        public static int Make() { return 1; }\n    }\n}\n'},
     [('A.Run', 'references', 'Lib.Repo')], [('A.Run', 'references', 'App.S')],
     source='csharpstandard §14.8.1 (namespaces.md)')
case('csharp', 'alias-named-global', "`global.Tool` is the alias named global; `global::Tool` is still the global namespace's",
     CS, {'A.cs': 'using global = MyGlobalTypes;\n\nnamespace App\n{\n    public class A\n    {\n        public int Run(global.Tool t) { return t.Use(); }\n\n        public int Run2(global::Tool t) { return t.Use(); }\n    }\n}\n', 'Tool.cs': 'public class Tool\n{\n    public int Use() { return 2; }\n}\n', 'G/T.cs': 'namespace MyGlobalTypes\n{\n    public class Tool\n    {\n        public int Use() { return 1; }\n    }\n}\n'},
     [('A.Run', 'calls', 'MyGlobalTypes.Tool.Use'), ('A.Run2', 'calls', 'Tool.cs:Tool.Use')], [('A.Run', 'calls', 'Tool.cs:Tool.Use'), ('A.Run2', 'calls', 'MyGlobalTypes.Tool.Use')],
     source='csharpstandard §14.8.1 (namespaces.md)')
case('csharp', 'file-scoped-using-before-outer-namespace', 'a `using` after a file-scoped `namespace App.Sub;` is read before a type of the namespace App around it',
     CS, {'Lib.cs': 'namespace Lib\n{\n    public class Repo { public int Find() { return 1; } }\n}\n', 'Outer.cs': 'namespace App\n{\n    public class Repo { public string Other() { return ""; } }\n}\n', 'A.cs': 'namespace App.Sub;\n\nusing Lib;\n\npublic class C\n{\n    public int M(Repo r) { return r.Find(); }\n}\n'},
     [('C.M', 'references', 'Lib.Repo'), ('C.M', 'calls', 'Lib.Repo.Find')], [('C.M', 'references', 'App.Repo')],
     source='csharpstandard namespaces.md §14.3 and §14.5.1 (the scope of a using directive is the namespace body that contains it); Roslyn Imports.cs / InContainerBinder (usings of the inner namespace body come before types of the enclosing namespace)', since='10')
case('csharp', 'using-alias-as-qualifier', 'an alias is the type it names as the qualifier of a static call and in `new`',
     CS, {'Lib.cs': 'namespace Lib\n{\n    public class Repo\n    {\n        public static Repo Make() { return new Repo(); }\n        public int Find() { return 1; }\n    }\n}\n', 'Other.cs': 'namespace Other\n{\n    public class Repo\n    {\n        public static Repo Make() { return new Repo(); }\n        public int Find() { return 2; }\n    }\n}\n', 'A.cs': 'using Lib;\nusing R = Other.Repo;\n\nnamespace App\n{\n    public class C\n    {\n        public int M()\n        {\n            var a = R.Make();\n            var b = new R();\n            return a.Find() + b.Find();\n        }\n    }\n}\n'},
     [('C.M', 'calls', 'Other.cs:Other.Repo.Make'), ('C.M', 'calls', 'Other.cs:Other.Repo.Find')], [('C.M', 'calls', 'Lib.cs:Lib.Repo.Make'), ('C.M', 'calls', 'Lib.cs:Lib.Repo.Find')],
     source='csharpstandard namespaces.md §14.5.2 (a using_alias_directive makes an alias available within its compilation unit or namespace body); Roslyn Imports.cs')
case('csharp', 'using-static-fields-and-properties', "`using static T;` imports T's static fields and properties as simple names",
     CS, {'Lib.cs': 'namespace Lib\n{\n    public static class Defaults\n    {\n        public static int Limit = 10;\n\n        public static int Size { get { return 1; } }\n    }\n}\n\nnamespace Other\n{\n    public static class Defaults\n    {\n        public static int Limit = 20;\n\n        public static int Size { get { return 2; } }\n    }\n}\n', 'A.cs': 'using static Lib.Defaults;\n\nnamespace App\n{\n    public class C\n    {\n        public int M() { return Limit + Size; }\n    }\n}\n'},
     [('C.M', 'reads', 'Lib.Defaults.Limit'), ('C.M', 'reads', 'Lib.Defaults.Size')], [('C.M', 'reads', 'Other.Defaults.Limit'), ('C.M', 'reads', 'Other.Defaults.Size')],
     source='csharpstandard namespaces.md §14.5.4; Roslyn WithUsingNamespacesAndTypesBinder.cs LookupSymbolsInSingleBinder', since='6')
case('csharp', 'using-static-hidden-by-own-member', 'a member of the enclosing class comes before a name imported by `using static`',
     CS, {'Lib.cs': 'namespace Lib\n{\n    public static class Defaults\n    {\n        public static int Limit = 10;\n\n        public static int Size { get { return 1; } }\n    }\n}\n', 'B.cs': 'using static Lib.Defaults;\n\nnamespace App\n{\n    public class D\n    {\n        public static int Limit = 5;\n\n        public int M() { return Limit; }\n\n        public int N() { return Size; }\n    }\n}\n'},
     [('D.M', 'reads', 'D.Limit'), ('D.N', 'reads', 'Lib.Defaults.Size')], [('D.M', 'reads', 'Lib.Defaults.Limit')],
     source='csharpstandard basic-concepts.md §7.8.1 (members of the enclosing type before using directives of the namespace body); namespaces.md §14.5.4', since='6')
case('csharp', 'using-static-enum-members', "`using static Lib.Mode;` makes Mode's members simple names: `Fast` is Mode.Fast, not Other.Fast",
     CS, {'A.cs': 'namespace Lib { public enum Mode { Fast, Slow } public enum Other { Fast, Slow } }\n', 'B.cs': 'using static Lib.Mode;\nnamespace App { public class Use { public Lib.Mode M() { return Fast; } } }\n'},
     [('Use.M', 'references', 'Mode')], [('Use.M', 'references', 'Other')],
     source='csharpstandard §14.5.4 using static directives')
case('csharp', 'keyword-alias-is-system-type', "the keyword `string` is System.String whatever a project type is named `String`; the name `String` is the project's",
     CS, {'A.cs': 'namespace App\n{\n    public class String\n    {\n        public static string Join(string a, string b) { return a + b; }\n    }\n\n    public class C\n    {\n        public string Keyword() { return string.Join("a", "b"); }\n\n        public string Named() { return String.Join("a", "b"); }\n    }\n}\n'},
     [('C.Named', 'calls', 'String.Join')], [('C.Keyword', 'calls', 'String.Join')],
     source='csharpstandard §8.2.5 / §8.3.5 (a keyword is an alias for the predefined type in System)')
case('csharp', 'tuple-element-names', "a tuple's element is its field by element name or by ItemN, each with its own type",
     CS, {'A.cs': 'namespace App\n{\n    public class Repo\n    {\n        public int Find() { return 1; }\n    }\n\n    public class Other\n    {\n        public int Find() { return 2; }\n    }\n\n    public class C\n    {\n        public (Repo left, Other right) Pair() { return (new Repo(), new Other()); }\n\n        public int Left() { var p = Pair(); return p.left.Find(); }\n\n        public int Right() { var p = Pair(); return p.right.Find(); }\n\n        public int Item() { var p = Pair(); return p.Item2.Find(); }\n    }\n}\n'},
     [('C.Left', 'calls', 'Repo.Find'), ('C.Right', 'calls', 'Other.Find'), ('C.Item', 'calls', 'Other.Find')], [('C.Left', 'calls', 'Other.Find'), ('C.Right', 'calls', 'Repo.Find'), ('C.Item', 'calls', 'Repo.Find')],
     source='csharpstandard §8.3.11.1 (elements are public fields Item1 … ItemN; named elements)')
case('csharp', 'nullable-value-type-members', '`S?` is Nullable<S>: its `.Value` and the `?.` operand are S, whose members are the ones used',
     CS, {'A.cs': 'namespace App\n{\n    public struct Point\n    {\n        public int Len() { return 1; }\n    }\n\n    public class Other\n    {\n        public int Len() { return 2; }\n    }\n\n    public class C\n    {\n        public int Run(Point? p)\n        {\n            return p.Value.Len();\n        }\n\n        public int Cond(Point? p)\n        {\n            return p?.Len() ?? 0;\n        }\n    }\n}\n'},
     [('C.Run', 'calls', 'Point.Len'), ('C.Cond', 'calls', 'Point.Len')], [('C.Run', 'calls', 'Other.Len'), ('C.Cond', 'calls', 'Other.Len')],
     source='csharpstandard §8.3.12 (a nullable value type, its Value property); §12.8.8')
case('csharp', 'generic-outer-nested-type', "a nested type named inside a generic class is that class's, written without type arguments",
     CS, {'A.cs': 'namespace App\n{\n    public class Outer<T>\n    {\n        public class Inner\n        {\n            public int Go() { return 1; }\n        }\n\n        public Inner i;\n\n        public int Run() { return i.Go(); }\n    }\n\n    public class Other\n    {\n        public class Inner\n        {\n            public int Go() { return 2; }\n        }\n    }\n}\n'},
     [('Outer.Run', 'calls', 'Outer.Inner.Go')], [('Outer.Run', 'calls', 'Other.Inner.Go')],
     source='csharpstandard §8.4.1 (a type_name identifies a constructed type though it does not specify type arguments: Outer<T>.Inner)')
case('csharp', 'static-member-of-a-constructed-type', '`Box<int>.Make()` is the static member of the generic Box, `Box.Make()` of the non-generic one: the type arguments tell them apart in an expression too',
     CS, {'A.cs': 'namespace App\n{\n    public class Box<T>\n    {\n        public static int Make() { return 1; }\n    }\n\n    public class Box\n    {\n        public static int Make() { return 2; }\n    }\n\n    public class C\n    {\n        public int Run() { return Box<int>.Make(); }\n\n        public int Plain() { return Box.Make(); }\n    }\n}\n'},
     [('C.Run', 'calls', 'A.cs:App.Box.Make@5'), ('C.Plain', 'calls', 'A.cs:App.Box.Make@10')], [('C.Run', 'calls', 'A.cs:App.Box.Make@10'), ('C.Plain', 'calls', 'A.cs:App.Box.Make@5')],
     source='csharpstandard §8.4.1 (constructed types can be used in expressions as simple names or when accessing a member; only types with the right number of type parameters are considered)')
case('csharp', 'typeof-unbound-generic-arity', '`typeof(Pair<,>)` names the unbound generic type with as many type parameters as there are commas plus one',
     CS, {'A.cs': 'using System;\n\nnamespace App\n{\n    public class Pair<A, B>\n    {\n    }\n\n    public class Pair<A>\n    {\n    }\n\n    public class C\n    {\n        public Type Run() { return typeof(Pair<,>); }\n    }\n}\n'},
     [('C.Run', 'references', 'A.cs:App.Pair@5')], [('C.Run', 'references', 'A.cs:App.Pair@9')],
     source='csharpstandard §8.4.4 (the only construct in which an unbound generic type can be referenced is typeof)')
case('csharp', 'constraint-names-are-uses', 'the types a `where` clause names are uses of those types',
     CS, {'A.cs': 'namespace App\n{\n    public interface IRepo\n    {\n    }\n\n    public class Base\n    {\n    }\n\n    public class C<T, U> where T : IRepo where U : Base\n    {\n    }\n}\n'},
     [('C', 'references', 'App.IRepo'), ('C', 'references', 'App.Base')], [],
     source='csharpstandard §8.4.5 / §15.2.5 (constraints name types); Roslyn Binder_Constraints.cs (BindTypeParameterConstraints)')
case('csharp', 'class-type-parameter-first', 'in a generic class a name of its type parameters is that parameter, not a type of the namespace',
     CS, {'A.cs': 'namespace App\n{\n    public class T\n    {\n    }\n\n    public class Box<T>\n    {\n        public int Get(T x) { return 1; }\n    }\n\n    public class C\n    {\n        public int Plain(T x) { return 2; }\n    }\n}\n'},
     [('C.Plain', 'references', 'App.T')], [('Box.Get', 'references', 'App.T')],
     source='csharpstandard §8.5, §7.8.1 (the type parameters of the class are looked up before the namespace)')
case('csharp', 'type-parameter-constraint-members', 'a value of a type parameter has the members of the type its constraint names',
     CS, {'A.cs': 'namespace App\n{\n    public interface IRepo\n    {\n        int Find();\n    }\n\n    public class Other\n    {\n        public int Find() { return 2; }\n    }\n\n    public class C\n    {\n        public int Run<T>(T r) where T : IRepo\n        {\n            return r.Find();\n        }\n    }\n}\n'},
     [('C.Run', 'calls', 'IRepo.Find')], [('C.Run', 'calls', 'Other.Find')],
     source='csharpstandard §8.5, §12.5 (member lookup on a type parameter depends on its constraints)')
case('csharp', 'nullable-reference-annotation', '`T?` on a reference type is the type T: the annotation changes no meaning',
     CS, {'A.cs': '#nullable enable\nnamespace App\n{\n    public class Repo\n    {\n        public int Find() { return 1; }\n    }\n\n    public class Other\n    {\n        public int Find() { return 2; }\n    }\n\n    public class C\n    {\n        public int Run(Repo? r)\n        {\n            return r!.Find();\n        }\n\n        public int Local()\n        {\n            Repo? r = null;\n            return r?.Find() ?? 0;\n        }\n    }\n}\n'},
     [('C.Run', 'calls', 'Repo.Find'), ('C.Local', 'calls', 'Repo.Find')], [('C.Run', 'calls', 'Other.Find'), ('C.Local', 'calls', 'Other.Find')],
     source='csharpstandard §8.9.1 (R and R? are represented by the same underlying type)')
case('csharp', 'overload-base-conversion', 'an argument converts implicitly to a base class: only the overload taking it applies',
     CS, {'A.cs': 'namespace App\n{\n    public class Animal\n    {\n    }\n\n    public class Dog : Animal\n    {\n    }\n\n    public class Log\n    {\n        public int Write(Animal a) { return 1; }\n\n        public int Write(string s) { return 2; }\n    }\n\n    public class C\n    {\n        public int Run(Log l, Dog d) { return l.Write(d); }\n    }\n}\n'},
     [('C.Run', 'calls', 'A.cs:App.Log.Write@13')], [('C.Run', 'calls', 'A.cs:App.Log.Write@15')],
     source='csharpstandard §10.2.8 (implicit reference conversions: class to its base classes), §12.6.4.2')
case('csharp', 'overload-interface-conversion', 'an argument converts implicitly to an interface its class implements',
     CS, {'A.cs': 'namespace App\n{\n    public interface IShape\n    {\n    }\n\n    public class Circle : IShape\n    {\n    }\n\n    public class Log\n    {\n        public int Write(IShape a) { return 1; }\n\n        public int Write(string s) { return 2; }\n    }\n\n    public class C\n    {\n        public int Run(Log l) { var c = new Circle(); return l.Write(c); }\n    }\n}\n'},
     [('C.Run', 'calls', 'A.cs:App.Log.Write@13')], [('C.Run', 'calls', 'A.cs:App.Log.Write@15')],
     source='csharpstandard §10.2.8 (from any class type S to any interface type T, provided S implements T)')
case('csharp', 'overload-more-derived-parameter', 'of two parameter types the argument converts to, the more derived one is better',
     CS, {'A.cs': 'namespace App\n{\n    public class Animal\n    {\n    }\n\n    public class Dog : Animal\n    {\n    }\n\n    public class Puppy : Dog\n    {\n    }\n\n    public class Log\n    {\n        public int Write(Animal a) { return 1; }\n\n        public int Write(Dog d) { return 2; }\n    }\n\n    public class C\n    {\n        public int Run(Log l, Puppy p) { return l.Write(p); }\n    }\n}\n'},
     [('C.Run', 'calls', 'A.cs:App.Log.Write@19')], [('C.Run', 'calls', 'A.cs:App.Log.Write@17')],
     source='csharpstandard §12.6.4.4 (better conversion target), §10.2.8')
case('csharp', 'overload-numeric-conversion', 'an int variable or constant converts implicitly to long, never to string',
     CS, {'A.cs': 'namespace App\n{\n    public class Log\n    {\n        public int Write(long n) { return 1; }\n\n        public int Write(string s) { return 2; }\n    }\n\n    public class C\n    {\n        public int Local(Log l, int n) { return l.Write(n); }\n\n        public int Constant(Log l) { return l.Write(5); }\n    }\n}\n'},
     [('C.Local', 'calls', 'A.cs:App.Log.Write@5'), ('C.Constant', 'calls', 'A.cs:App.Log.Write@5')], [('C.Local', 'calls', 'A.cs:App.Log.Write@7'), ('C.Constant', 'calls', 'A.cs:App.Log.Write@7')],
     source='csharpstandard §10.2.3 (implicit numeric conversions), §12.6.4.2')
case('csharp', 'overload-constant-conversion', 'an int constant in range converts implicitly to byte',
     CS, {'A.cs': 'namespace App\n{\n    public class Log\n    {\n        public int Write(byte n) { return 1; }\n\n        public int Write(string s) { return 2; }\n    }\n\n    public class C\n    {\n        public int Run(Log l) { return l.Write(5); }\n    }\n}\n'},
     [('C.Run', 'calls', 'A.cs:App.Log.Write@5')], [('C.Run', 'calls', 'A.cs:App.Log.Write@7')],
     source='csharpstandard §10.2.11 (implicit constant expression conversions)')
case('csharp', 'overload-null-literal', 'the literal null converts to a reference type, never to a non-nullable value type',
     CS, {'A.cs': 'namespace App\n{\n    public class Log\n    {\n        public int Write(int n) { return 1; }\n\n        public int Write(string s) { return 2; }\n    }\n\n    public class C\n    {\n        public int Run(Log l) { return l.Write(null); }\n    }\n}\n'},
     [('C.Run', 'calls', 'A.cs:App.Log.Write@7')], [('C.Run', 'calls', 'A.cs:App.Log.Write@5')],
     source='csharpstandard §10.2.7 (null literal conversions)')
case('csharp', 'overload-type-parameter-bound', 'a value of a type parameter converts implicitly to its effective base class',
     CS, {'A.cs': 'namespace App\n{\n    public class Animal\n    {\n    }\n\n    public class Log\n    {\n        public int Write(Animal a) { return 1; }\n\n        public int Write(string s) { return 2; }\n    }\n\n    public class C\n    {\n        public int Run<T>(Log l, T t) where T : Animal { return l.Write(t); }\n    }\n}\n'},
     [('C.Run', 'calls', 'A.cs:App.Log.Write@9')], [('C.Run', 'calls', 'A.cs:App.Log.Write@11')],
     source='csharpstandard §10.2.12 (implicit conversions involving type parameters)')
case('csharp', 'lambda-parameter-from-declared-delegate', "a lambda's untyped parameter has the type of the declared delegate it is assigned to",
     CS, {'A.cs': 'using System;\n\nnamespace App\n{\n    public class Repo\n    {\n        public int Find() { return 1; }\n    }\n\n    public class Other\n    {\n        public int Find() { return 2; }\n    }\n\n    public class C\n    {\n        public Func<Repo, int> Make()\n        {\n            Func<Repo, int> f = r => r.Find();\n            return f;\n        }\n    }\n}\n'},
     [('C.Make', 'calls', 'Repo.Find')], [('C.Make', 'calls', 'Other.Find')],
     source='csharpstandard §10.7.1; Roslyn Binder_Conversions.cs CreateAnonymousFunctionConversion')
case('csharp', 'lambda-parameter-from-method-parameter', 'a lambda passed as an argument takes its parameter types from the delegate type of the parameter',
     CS, {'A.cs': 'using System;\n\nnamespace App\n{\n    public class Repo\n    {\n        public int Find() { return 1; }\n    }\n\n    public class Other\n    {\n        public int Find() { return 2; }\n    }\n\n    public class C\n    {\n        static int Apply(Func<Repo, int> f) { return f(new Repo()); }\n\n        public int Run() { return Apply(r => r.Find()); }\n    }\n}\n'},
     [('C.Run', 'calls', 'Repo.Find')], [('C.Run', 'calls', 'Other.Find')],
     source='csharpstandard §10.7.1, §12.6.4.2; Roslyn Binder_Conversions.cs CreateAnonymousFunctionConversion')
case('csharp', 'overload-by-lambda-arity', 'a lambda is compatible only with a delegate with as many parameters as it',
     CS, {'A.cs': 'using System;\n\nnamespace App\n{\n    public class Runner\n    {\n        public int Apply(Func<int> f) { return 1; }\n\n        public int Apply(Func<int, int> f) { return 2; }\n    }\n\n    public class C\n    {\n        public int Run(Runner r) { return r.Apply(x => x + 1); }\n    }\n}\n'},
     [('C.Run', 'calls', 'A.cs:App.Runner.Apply@9')], [('C.Run', 'calls', 'A.cs:App.Runner.Apply@7')],
     source='csharpstandard §10.7.1 (D and F have the same number of parameters)')
case('csharp', 'method-group-picks-overload-by-delegate', "a method group converted to a delegate is the overload applicable to the delegate's parameter types",
     CS, {'A.cs': 'using System;\n\nnamespace App\n{\n    public class C\n    {\n        void Handle(int x) { }\n\n        void Handle(string s) { }\n\n        public Action<string> Make()\n        {\n            Action<string> a = Handle;\n            return a;\n        }\n    }\n}\n'},
     [('C.Make', 'indirect_call', 'A.cs:App.C.Handle@9')], [('C.Make', 'indirect_call', 'A.cs:App.C.Handle@7')],
     source='csharpstandard §10.8; Roslyn Binder_Conversions.cs CreateMethodGroupConversion, ResolveMethodGroup')
case('csharp', 'method-group-extension', 'an extension method named through an instance is a method group that converts to a delegate',
     CS, {'A.cs': 'using System;\n\nnamespace App\n{\n    public class Repo\n    {\n    }\n\n    public static class RepoExt\n    {\n        public static int Size(this Repo r) { return 1; }\n    }\n\n    public class Other\n    {\n        public int Size() { return 2; }\n    }\n\n    public class C\n    {\n        public Func<int> Make(Repo r)\n        {\n            Func<int> f = r.Size;\n            return f;\n        }\n    }\n}\n'},
     [('C.Make', 'indirect_call', 'RepoExt.Size')], [('C.Make', 'indirect_call', 'Other.Size')],
     source='csharpstandard §10.8 (an extension method denoted by member access on an instance expression)')
case('csharp', 'method-group-overload-by-delegate-roslyn', "a method group converted to a delegate is the overload applicable to the delegate's parameter types (passed as an argument)",
     CS, {'A.cs': 'using System;\n\nnamespace App\n{\n    public class C\n    {\n        static void Handle(int x) { }\n\n        static void Handle(string s) { }\n\n        static void Take(Action<int> a) { }\n\n        public void Run() { Take(Handle); }\n    }\n}\n'},
     [('C.Run', 'indirect_call', 'A.cs:App.C.Handle@7')], [('C.Run', 'indirect_call', 'A.cs:App.C.Handle@9')],
     source='Roslyn Binder_Conversions.cs CreateMethodGroupConversion, MethodGroupConversionHasErrors; csharpstandard §10.8')
case('csharp', 'generic-method-constraint-is-a-use', "the types a generic method's `where` clause names are uses of those types",
     CS, {'A.cs': 'namespace App\n{\n    public interface IRepo\n    {\n    }\n\n    public class Base\n    {\n    }\n\n    public class C\n    {\n        public void Run<T, U>() where T : IRepo where U : Base\n        {\n        }\n    }\n}\n'},
     [('C.Run', 'references', 'App.IRepo'), ('C.Run', 'references', 'App.Base')], [],
     source='Roslyn Binder_Constraints.cs (constraint type syntax is bound as a type); csharpstandard §15.2.5')
case('csharp', 'extension-on-base-class', 'an extension method written for a base class is called on a value of a derived class; one written for a sibling class is not',
     CS, {'A.cs': 'namespace App {\n  public class Animal { }\n  public class Dog : Animal { }\n  public class Cat : Animal { }\n  public static class AnimalExt { public static void Show(this Animal a) { } }\n  public static class CatExt { public static void Show(this Cat c) { } }\n  public class Run { public void Go(Dog d) { d.Show(); } }\n}\n'},
     [('Run.Go', 'calls', 'AnimalExt.Show')], [('Run.Go', 'calls', 'CatExt.Show')],
     source='csharpstandard §10.2.8 and expressions.md §12.8.10.3 (extension method invocations: an implicit identity, reference or boxing conversion from the expression to the first parameter)')
case('csharp', 'extension-on-predefined-type', 'an extension written for `this string` is called on a string, one written for `this int` on an int',
     CS, {'A.cs': 'namespace App {\n  public static class StrExt { public static string Twice(this string s) { return s + s; } }\n  public static class IntExt { public static int Twice(this int n) { return n * 2; } }\n  public class Run { public string Go(string s) { return s.Twice(); } public int Go2(int n) { return n.Twice(); } }\n}\n'},
     [('Run.Go', 'calls', 'StrExt.Twice'), ('Run.Go2', 'calls', 'IntExt.Twice')], [('Run.Go', 'calls', 'IntExt.Twice'), ('Run.Go2', 'calls', 'StrExt.Twice')],
     source='csharpstandard §8.3.5 (simple types are aliases of System structs) and §12.8.10.3')
case('csharp', 'delegate-invocation-result', "calling a parameter of a declared delegate type gives the delegate's return type",
     CS, {'A.cs': 'namespace App {\n  public class Item { public int Weigh() { return 1; } }\n  public class Other { public int Weigh() { return 2; } }\n  public delegate Item Maker();\n  public class Run { public int Go(Maker mk) { return mk().Weigh(); } }\n}\n'},
     [('Run.Go', 'calls', 'Item.Weigh')], [('Run.Go', 'calls', 'Other.Weigh')],
     source="csharpstandard §8.2.8 and expressions.md §12.8.10.4 (delegate invocations: the result is the delegate's return type)")
case('csharp', 'array-element-type', "`xs[0].M()` on an `Item[]` is Item's M",
     CS, {'A.cs': 'namespace App {\n  public class Item { public int Weigh() { return 1; } }\n  public class Other { public int Weigh() { return 2; } }\n  public class Run { public int Go(Item[] xs) { return xs[0].Weigh(); } }\n}\n'},
     [('Run.Go', 'calls', 'Item.Weigh')], [('Run.Go', 'calls', 'Other.Weigh')],
     source='csharpstandard §8.2.7 and expressions.md §12.8.12.2 (array access)')
case('csharp', 'delegate-creation-method-group', "`new Handler(OnData)` is a use of the OnData overload that takes the delegate's parameter type",
     CS, {'A.cs': 'namespace App {\n  public delegate void Handler(int x);\n  public class Run {\n    void OnData(int x) { }\n    void OnData(string s) { }\n    public Handler Make() { return new Handler(OnData); }\n  }\n}\n'},
     [('Run.Make', 'indirect_call', 'A.cs:App.Run.OnData@4')], [('Run.Make', 'indirect_call', 'A.cs:App.Run.OnData@5')],
     source='csharpstandard §10.8 and expressions.md §12.8.17.6 (delegate creation expressions)')
case('csharp', 'lambda-parameter-from-generic-delegate', '`Func<Item,int> f = x => x.Weigh();` has x of type Item',
     CS, {'A.cs': 'using System;\nnamespace App {\n  public class Item { public int Weigh() { return 1; } }\n  public class Other { public int Weigh() { return 2; } }\n  public class Run { public Func<Item,int> Make() { Func<Item,int> f = x => x.Weigh(); return f; } }\n}\n'},
     [('Run.Make', 'calls', 'Item.Weigh')], [('Run.Make', 'calls', 'Other.Weigh')],
     source='csharpstandard §10.7.1 and expressions.md §12.19.3 (anonymous function conversion to a delegate type)')
case('csharp', 'cast-method-group-to-delegate', "`(Op)Inc` is a use of the Inc overload that takes Op's parameter type",
     CS, {'A.cs': 'namespace App {\n  public delegate int Op(int a);\n  public class Run {\n    static int Inc(int a) { return a + 1; }\n    static int Inc(string s) { return 0; }\n    public object Go() { return (Op)Inc; }\n  }\n}\n'},
     [('Run.Go', 'indirect_call', 'A.cs:App.Run.Inc@4')], [('Run.Go', 'indirect_call', 'A.cs:App.Run.Inc@5')],
     source='Roslyn Binder_Conversions.cs BindCastCore, CreateMethodGroupConversion')
case('csharp', 'type-parameter-several-constraints', "members of `t` typed `T : IShape, ISized`, `T : Node` (member of its base) and `T : U` with `U : IShape` are the constraints' members",
     CS, {'A.cs': 'namespace App {\n  public interface IShape { void Draw(); }\n  public interface ISized { int Size(); }\n  public class Base { public void Prepare() { } }\n  public class Node : Base { }\n  public class Other { public void Draw() { } public int Size() { return 0; } public void Prepare() { } }\n  public class Run {\n    public void Go<T>(T t) where T : IShape, ISized { t.Draw(); t.Size(); }\n    public void Go2<T>(T t) where T : Node { t.Prepare(); }\n    public void Go3<T, U>(T t) where T : U where U : IShape { t.Draw(); }\n  }\n}\n'},
     [('Run.Go', 'calls', 'IShape.Draw'), ('Run.Go', 'calls', 'ISized.Size'), ('Run.Go2', 'calls', 'Base.Prepare'), ('Run.Go3', 'calls', 'IShape.Draw')], [('Run.Go', 'calls', 'Other.Draw'), ('Run.Go', 'calls', 'Other.Size'), ('Run.Go2', 'calls', 'Other.Prepare'), ('Run.Go3', 'calls', 'Other.Draw')],
     source='csharpstandard §8.5 and expressions.md §12.5 (member lookup in type parameters); Roslyn Binder_Constraints.cs')
case('csharp', 'overload-nullable-conversion', '`Write(i)` with an int variable is the overload taking `int?`, not the one taking string',
     CS, {'A.cs': 'namespace App\n{\n    public class Log\n    {\n        public int Write(int? n) { return 1; }\n\n        public int Write(string s) { return 2; }\n    }\n\n    public class C\n    {\n        public int M(Log l, int i) { return l.Write(i); }\n    }\n}\n'},
     [('C.M', 'calls', 'A.cs:App.Log.Write@5')], [('C.M', 'calls', 'A.cs:App.Log.Write@7')],
     source='csharpstandard §10.2.6; Roslyn Conversions.cs ClassifyImplicitBuiltInConversion')
case('csharp', 'overload-enum-zero-literal', '`Write(0)` is the overload taking the enum, not the one taking string',
     CS, {'A.cs': 'namespace App\n{\n    public enum Color { Red }\n\n    public class Log\n    {\n        public int Write(Color c) { return 1; }\n\n        public int Write(string s) { return 2; }\n    }\n\n    public class C\n    {\n        public int M(Log l) { return l.Write(0); }\n    }\n}\n'},
     [('C.M', 'calls', 'A.cs:App.Log.Write@7')], [('C.M', 'calls', 'A.cs:App.Log.Write@9')],
     source='csharpstandard §10.2.4')
case('csharp', 'overload-boxing-conversion', '`Write(s)` with a struct is the overload taking the interface the struct implements, not the one taking string',
     CS, {'A.cs': 'namespace App\n{\n    public interface IFoo { }\n\n    public struct S : IFoo { }\n\n    public class Log\n    {\n        public int Write(IFoo f) { return 1; }\n\n        public int Write(string s) { return 2; }\n    }\n\n    public class C\n    {\n        public int M(Log l, S s) { return l.Write(s); }\n    }\n}\n'},
     [('C.M', 'calls', 'A.cs:App.Log.Write@9')], [('C.M', 'calls', 'A.cs:App.Log.Write@11')],
     source='csharpstandard §10.2.9')
case('csharp', 'overload-array-covariance', '`Write(ds)` with a `Dog[]` is the overload taking `Animal[]`, not the one taking string',
     CS, {'A.cs': 'namespace App\n{\n    public class Animal { }\n\n    public class Dog : Animal { }\n\n    public class Log\n    {\n        public int Write(Animal[] a) { return 1; }\n\n        public int Write(string s) { return 2; }\n    }\n\n    public class C\n    {\n        public int M(Log l, Dog[] ds) { return l.Write(ds); }\n    }\n}\n'},
     [('C.M', 'calls', 'A.cs:App.Log.Write@9')], [('C.M', 'calls', 'A.cs:App.Log.Write@11')],
     source='csharpstandard §10.2.8')
case('csharp', 'overload-base-interface-conversion', '`Write(b)` with an IBar (IBar : IFoo) is the overload taking IFoo, not the one taking string',
     CS, {'A.cs': 'namespace App\n{\n    public interface IFoo { }\n\n    public interface IBar : IFoo { }\n\n    public class Log\n    {\n        public int Write(IFoo f) { return 1; }\n\n        public int Write(string s) { return 2; }\n    }\n\n    public class C\n    {\n        public int M(Log l, IBar b) { return l.Write(b); }\n    }\n}\n'},
     [('C.M', 'calls', 'A.cs:App.Log.Write@9')], [('C.M', 'calls', 'A.cs:App.Log.Write@11')],
     source='csharpstandard §10.2.8')
case('csharp', 'generic-base-substitution', 'a base class written with type arguments gives its members, with each type parameter replaced by the argument written',
     CS, {'A.cs': 'namespace App\n{\n    public class Item\n    {\n        public int Id() { return 1; }\n    }\n\n    public class Other\n    {\n        public int Id() { return 2; }\n    }\n\n    public class Base<U>\n    {\n        public U Get() { return default(U); }\n    }\n\n    public class Box<T> : Base<T>\n    {\n    }\n\n    public class C\n    {\n        public int M(Box<Item> b) { return b.Get().Id(); }\n    }\n}\n'},
     [('C.M', 'calls', 'Base.Get'), ('C.M', 'calls', 'Item.Id')], [('C.M', 'calls', 'Other.Id')],
     source='csharpstandard classes.md §15.2.4.2, §15.3.4 (inherited members of a constructed class type)')
case('csharp', 'partial-interface-lists-union', 'the interfaces of a partial type are the union of those each part names',
     CS, {'A.cs': 'namespace App\n{\n    public interface IA { }\n\n    public interface IB { }\n\n    public partial class C : IA\n    {\n    }\n\n    public partial class C : IB\n    {\n    }\n}\n'},
     [('C', 'implements', 'IA'), ('C', 'implements', 'IB')], [],
     source='csharpstandard classes.md §15.2.4.3')
case('csharp', 'applicable-derived-method-hides-base', "of the methods applicable to a call, those declared in a base type of another candidate's type are dropped, even a better fit",
     CS, {'A.cs': 'namespace App\n{\n    public class Base\n    {\n        public int M(int x) { return 1; }\n    }\n\n    public class Derived : Base\n    {\n        public int M(double x) { return 2; }\n    }\n\n    public class C\n    {\n        public int Call(Derived d) { return d.M(1); }\n    }\n}\n'},
     [('C.Call', 'calls', 'Derived.M')], [('C.Call', 'calls', 'Base.M')],
     source='csharpstandard expressions.md §12.8.10.2 (candidate set reduced to the most derived types), classes.md §15.3.5; Roslyn OverloadResolution.cs RemoveLessDerivedMembers')
case('csharp', 'nested-type-hides-base-member', 'a nested type declared with `new` hides a member of a base class of its outer type, for `Derived.M.F()`',
     CS, {'A.cs': 'namespace App\n{\n    public class Base\n    {\n        public static void M() { }\n    }\n\n    public class Derived : Base\n    {\n        public new class M\n        {\n            public static int F() { return 1; }\n        }\n    }\n\n    public class Test\n    {\n        public int Run() { return Derived.M.F(); }\n    }\n}\n'},
     [('Test.Run', 'calls', 'Derived.M.F')], [('Test.Run', 'calls', 'Base.M')],
     source='csharpstandard classes.md §15.3.9.4 (7.7.2.2 hiding through nesting)')
case('csharp', 'nested-bare-name-outer-then-inherited', 'a bare name in a nested class is a member of the nested class, with its bases, before a member of the class around it',
     CS, {'A.cs': 'namespace App\n{\n    public class Helper\n    {\n        public static int F() { return 1; }\n    }\n\n    public class C\n    {\n        private static int F() { return 2; }\n\n        public class Plain\n        {\n            public static int G() { return F(); }\n        }\n\n        public class Derived : Helper\n        {\n            public static int G() { return F(); }\n        }\n    }\n}\n'},
     [('C.Plain.G', 'calls', 'C.F'), ('C.Derived.G', 'calls', 'Helper.F')], [('C.Derived.G', 'calls', 'C.F'), ('C.Plain.G', 'calls', 'Helper.F')],
     source='csharpstandard classes.md §15.3.9.6, expressions.md §12.8.4 (simple names: each enclosing type, innermost first)')
case('csharp', 'nested-generic-type-unqualified', "inside a generic class its generic nested type is named without the outer's type arguments, before a type of the namespace of that name",
     CS, {'A.cs': 'namespace App\n{\n    public class Inner<U>\n    {\n        public static int F(U u) { return 1; }\n    }\n\n    public class Outer<T>\n    {\n        public class Inner<U>\n        {\n            public static int F(T t, U u) { return 2; }\n        }\n\n        public static int Call(T t) { return Inner<string>.F(t, "abc"); }\n    }\n}\n'},
     [('Outer.Call', 'calls', 'Outer.Inner.F')], [('Outer.Call', 'calls', 'A.cs:App.Inner.F')],
     source='csharpstandard classes.md §15.3.9.7')
case('csharp', 'constant-reads-constant', "the value of a constant is read by a simple name or a member access, in another constant's expression too",
     CS, {'A.cs': 'namespace App\n{\n    public class A\n    {\n        public const int X = B.Z + 1;\n\n        public const int Y = 10;\n    }\n\n    public class B\n    {\n        public const int Z = A.Y + 1;\n    }\n\n    public class C\n    {\n        public const int Y = 4;\n    }\n}\n'},
     [('A', 'reads', 'B.Z'), ('B', 'reads', 'A.Y')], [('B', 'reads', 'C.Y')],
     source='csharpstandard classes.md §15.4')
case('csharp', 'multi-declarator-field', 'one declaration of several fields declares each as its own member',
     CS, {'A.cs': 'namespace App\n{\n    public class A\n    {\n        public static int P = 1, Q, R = 100;\n    }\n\n    public class B\n    {\n        public static int Reset()\n        {\n            A.Q = 2;\n            return A.R;\n        }\n    }\n}\n'},
     [('B.Reset', 'writes', 'A.Q'), ('B.Reset', 'reads', 'A.R')], [('B.Reset', 'reads', 'A.P'), ('B.Reset', 'writes', 'A.P')],
     source='csharpstandard classes.md §15.5.1')
case('csharp', 'field-initializer-uses', 'a variable initializer is bound in the class: its names are members of the class before a `using static` import',
     CS, {'Other.cs': 'namespace App\n{\n    public class Other\n    {\n        public static int b = 5;\n    }\n}\n', 'A.cs': 'using static App.Other;\n\nnamespace App\n{\n    public class Helper\n    {\n        public static int Make() { return 1; }\n    }\n\n    public class Test\n    {\n        static int a = b + 1;\n\n        static int b = Helper.Make();\n\n        Helper own = new Helper();\n    }\n}\n'},
     [('Test', 'reads', 'Test.b'), ('Test', 'calls', 'Helper.Make'), ('Test', 'calls', 'Helper')], [('Test', 'reads', 'Other.b')],
     source='csharpstandard classes.md §15.5.6; expressions.md §12.8.4')
case('csharp', 'params-expanded-form-yields-to-declared', 'the expanded form of a params method is not considered when a method of that very signature is declared',
     CS, {'A.cs': 'namespace App\n{\n    public class T\n    {\n        public static int F(params object[] a) { return 1; }\n\n        public static int F() { return 2; }\n\n        public static int F(object a0, object a1) { return 3; }\n\n        public int None() { return F(); }\n\n        public int Two() { return F(1, 2); }\n\n        public int One() { return F(1); }\n    }\n}\n'},
     [('T.None', 'calls', 'A.cs:App.T.F@7'), ('T.Two', 'calls', 'A.cs:App.T.F@9'), ('T.One', 'calls', 'A.cs:App.T.F@5')], [('T.None', 'calls', 'A.cs:App.T.F@5'), ('T.Two', 'calls', 'A.cs:App.T.F@5'), ('T.One', 'calls', 'A.cs:App.T.F@7'), ('T.One', 'calls', 'A.cs:App.T.F@9')],
     source='csharpstandard classes.md §15.6.2.4; Roslyn OverloadResolution.cs (params expanded form worse)')
case('csharp', 'default-argument-constant', "a default argument is an expression of the method's own scope",
     CS, {'A.cs': 'namespace App\n{\n    public class Defaults\n    {\n        public const int Size = 4;\n    }\n\n    public class C\n    {\n        const int Size = 9;\n\n        public int M(int n = Defaults.Size) { return n; }\n\n        public int N(int n = Size) { return n; }\n    }\n}\n'},
     [('C.M', 'reads', 'Defaults.Size'), ('C.N', 'reads', 'C.Size')], [('C.N', 'reads', 'Defaults.Size')],
     source='csharpstandard classes.md §15.6.2.1; expressions.md §12.8.4')
case('csharp', 'out-variable-type', "an `out` argument given as `out var x` is a local of the parameter's type",
     CS, {'A.cs': 'namespace App\n{\n    public class Item\n    {\n        public int Id() { return 1; }\n    }\n\n    public class Other\n    {\n        public int Id() { return 2; }\n    }\n\n    public class C\n    {\n        bool TryGet(string k, out Item item) { item = null; return true; }\n\n        public int Var(string k)\n        {\n            if (TryGet(k, out var it)) { return it.Id(); }\n            return 0;\n        }\n\n        public int Typed(string k)\n        {\n            Item found;\n            TryGet(k, out found);\n            return found.Id();\n        }\n    }\n}\n'},
     [('C.Var', 'calls', 'C.TryGet'), ('C.Var', 'calls', 'Item.Id'), ('C.Typed', 'calls', 'Item.Id')], [('C.Var', 'calls', 'Other.Id')],
     source='csharpstandard classes.md §15.6.2.3.4; expressions.md §12.17 (declaration expressions)')
case('csharp', 'static-type-picks-hidden-method', 'a non-virtual method is the one of the static type of the receiver, hiding or not',
     CS, {'A.cs': 'namespace App\n{\n    public class A\n    {\n        public void F() { }\n    }\n\n    public class B : A\n    {\n        public new void F() { }\n    }\n\n    public class Test\n    {\n        public void ViaBase(B b)\n        {\n            A a = b;\n            a.F();\n        }\n\n        public void ViaDerived(B b)\n        {\n            b.F();\n        }\n    }\n}\n'},
     [('Test.ViaBase', 'calls', 'A.F'), ('Test.ViaDerived', 'calls', 'B.F')], [('Test.ViaBase', 'calls', 'B.F'), ('Test.ViaDerived', 'calls', 'A.F')],
     source='csharpstandard classes.md §15.6.4')
case('csharp', 'override-call-binds-to-override', "a call of a virtual method is bound to the declaration of the receiver's static type, an override or a hiding `new virtual`",
     CS, {'A.cs': 'namespace App\n{\n    public class A\n    {\n        public virtual void F() { }\n    }\n\n    public class B : A\n    {\n        public override void F() { }\n    }\n\n    public class C : B\n    {\n        public new virtual void F() { }\n    }\n\n    public class D : C\n    {\n        public override void F() { }\n    }\n\n    public class Test\n    {\n        public void ViaB(D d)\n        {\n            B b = d;\n            b.F();\n        }\n\n        public void ViaC(D d)\n        {\n            C c = d;\n            c.F();\n        }\n\n        public void ViaD(D d)\n        {\n            d.F();\n        }\n    }\n}\n'},
     [('Test.ViaB', 'calls', 'B.F'), ('Test.ViaC', 'calls', 'C.F'), ('Test.ViaD', 'calls', 'D.F')], [('Test.ViaB', 'calls', 'A.F'), ('Test.ViaC', 'calls', 'B.F'), ('Test.ViaD', 'calls', 'C.F')],
     source='csharpstandard classes.md §15.6.4, §15.6.5')
case('csharp', 'partial-method-call', 'a call of a partial method is a use of the one method its two parts declare',
     CS, {'A.cs': 'namespace App\n{\n    public partial class Customer\n    {\n        string name;\n\n        public string Name\n        {\n            set\n            {\n                OnNameChanging(value);\n                name = value;\n            }\n        }\n\n        partial void OnNameChanging(string newName);\n    }\n\n    public class Other\n    {\n        public void OnNameChanging(string newName) { }\n    }\n}\n', 'B.cs': 'namespace App\n{\n    public partial class Customer\n    {\n        partial void OnNameChanging(string newName)\n        {\n            name = newName;\n        }\n    }\n}\n'},
     [('Customer.Name', 'calls', 'Customer.OnNameChanging')], [('Customer.Name', 'calls', 'Other.OnNameChanging')],
     source='csharpstandard classes.md §15.6.9')
case('csharp', 'extension-static-and-array-receiver', 'an extension method is the same method called as `Ext.M(x)`, and its first parameter may be an array type',
     CS, {'A.cs': 'namespace App\n{\n    public static class Ext\n    {\n        public static int Slice(this string[] source, int n) { return n; }\n\n        public static int Twice(this string s) { return 2; }\n    }\n\n    public class C\n    {\n        public int Sugar(string[] xs) { return xs.Slice(1); }\n\n        public int Plain(string[] xs) { return Ext.Slice(xs, 1); }\n\n        public int Str(string s) { return Ext.Twice(s) + s.Twice(); }\n    }\n}\n'},
     [('C.Sugar', 'calls', 'Ext.Slice'), ('C.Plain', 'calls', 'Ext.Slice'), ('C.Str', 'calls', 'Ext.Twice')], [],
     source='csharpstandard classes.md §15.6.10')
case('csharp', 'explicit-interface-member-is-no-class-member', "a method written `IFoo.Bar` is not the class's member `Bar`: `c.Bar()` is the public one, `((IFoo)c).Bar()` the interface's",
     CS, {'A.cs': 'namespace App\n{\n    public interface IFoo\n    {\n        int Bar();\n    }\n\n    public class C : IFoo\n    {\n        public int Bar() { return 1; }\n\n        int IFoo.Bar() { return 2; }\n    }\n\n    public class User\n    {\n        public int Direct(C c) { return c.Bar(); }\n\n        public int ViaInterface(C c) { return ((IFoo)c).Bar(); }\n    }\n}\n'},
     [('User.Direct', 'calls', 'A.cs:App.C.Bar@10'), ('User.ViaInterface', 'calls', 'IFoo.Bar')], [('User.Direct', 'calls', 'A.cs:App.C.Bar@12'), ('User.Direct', 'calls', 'IFoo.Bar'), ('User.ViaInterface', 'calls', 'A.cs:App.C.Bar@10'), ('User.ViaInterface', 'calls', 'A.cs:App.C.Bar@12')],
     source='csharpstandard classes.md §15.6.1; interfaces.md §19.6.2')
case('csharp', 'cast-reaches-hidden-property', "a property of the derived class hides the base's for reading and writing alike; a cast reaches the base's",
     CS, {'A.cs': 'namespace App\n{\n    public class A\n    {\n        public int P { set { } }\n    }\n\n    public class B : A\n    {\n        public new int P { get { return 1; } }\n    }\n\n    public class C\n    {\n        public int Read(B b) { return b.P; }\n\n        public void Write(B b) { ((A)b).P = 1; }\n    }\n}\n'},
     [('C.Read', 'reads', 'B.P'), ('C.Write', 'writes', 'A.P')], [('C.Read', 'reads', 'A.P'), ('C.Write', 'writes', 'B.P')],
     source='csharpstandard classes.md §15.7.3')
case('csharp', 'base-property-access', "`base.X` and `base.X = v` in an overriding property are the base property's reads and writes",
     CS, {'A.cs': 'namespace App\n{\n    public class A\n    {\n        int y;\n\n        public virtual int X { get { return 0; } }\n\n        public virtual int Y\n        {\n            get { return y; }\n            set { y = value; }\n        }\n    }\n\n    public class B : A\n    {\n        public override int X { get { return base.X + 1; } }\n\n        public override int Y\n        {\n            get { return base.Y; }\n            set { base.Y = value; }\n        }\n    }\n}\n'},
     [('B.X', 'reads', 'A.X'), ('B.Y', 'reads', 'A.Y'), ('B.Y', 'writes', 'A.Y')], [('B.X', 'reads', 'B.X'), ('B.Y', 'writes', 'B.Y')],
     source='csharpstandard classes.md §15.7.6; expressions.md §12.8.15')
case('csharp', 'auto-property-use', 'an auto-property is written in the constructor and read by its name; its initializer is bound in the class',
     CS, {'A.cs': 'namespace App\n{\n    public class Item\n    {\n        public int Id() { return 1; }\n    }\n\n    public class Point\n    {\n        public int X { get; }\n\n        public Item Current { get; } = new Item();\n\n        public Point(int x)\n        {\n            X = x;\n        }\n\n        public int Use() { return X + Current.Id(); }\n    }\n}\n'},
     [('Point.Point', 'writes', 'Point.X'), ('Point.Use', 'reads', 'Point.X'), ('Point.Use', 'reads', 'Point.Current'), ('Point.Use', 'calls', 'Item.Id')], [('Point.Use', 'writes', 'Point.X')],
     source='csharpstandard classes.md §15.7.4')
case('csharp', 'method-group-event-subscription', 'a method group attached to an event with `+=` or `new Handler(m)` is a use of the method',
     CS, {'A.cs': 'namespace App\n{\n    public delegate void Handler(object sender);\n\n    public class Button\n    {\n        public event Handler Click;\n    }\n\n    public class Dialog\n    {\n        void OkClicked(object sender) { }\n\n        void Cancelled(object sender) { }\n\n        public void Wire(Button b)\n        {\n            b.Click += OkClicked;\n            b.Click -= new Handler(Cancelled);\n        }\n    }\n}\n'},
     [('Dialog.Wire', 'indirect_call', 'Dialog.OkClicked'), ('Dialog.Wire', 'indirect_call', 'Dialog.Cancelled')], [],
     source='csharpstandard classes.md §15.8.1; expressions.md §12.23.6 (event assignment), §12.8.17.5 (delegate creation)')
case('csharp', 'indexer-use', 'an element access is a use of the indexer picked by the arguments, for reading or writing',
     CS, {'A.cs': 'namespace App\n{\n    public class Grid\n    {\n        int[] cells = new int[10];\n\n        public int this[int i]\n        {\n            get { return cells[i]; }\n            set { cells[i] = value; }\n        }\n\n        public int this[int row, int col]\n        {\n            get { return cells[row * 3 + col]; }\n        }\n    }\n\n    public class C\n    {\n        public int Read(Grid g) { return g[1]; }\n\n        public void Write(Grid g) { g[1] = 5; }\n\n        public int Two(Grid g) { return g[1, 2]; }\n    }\n}\n'},
     [('C.Read', 'reads', 'A.cs:App.Grid.this[]@7'), ('C.Write', 'writes', 'A.cs:App.Grid.this[]@7'), ('C.Two', 'reads', 'A.cs:App.Grid.this[]@13')], [('C.Two', 'reads', 'A.cs:App.Grid.this[]@7'), ('C.Read', 'reads', 'A.cs:App.Grid.this[]@13')],
     source='csharpstandard classes.md §15.9, §15.9.2; expressions.md §12.8.12.4')
case('csharp', 'base-indexer-access', '`base[i]` in an overriding indexer is the inherited indexer',
     CS, {'A.cs': 'namespace App\n{\n    public class A\n    {\n        public virtual int this[int i] { get { return i; } }\n    }\n\n    public class B : A\n    {\n        public override int this[int i] { get { return base[i] + 1; } }\n    }\n}\n'},
     [('B.this[]', 'reads', 'A.this[]')], [('B.this[]', 'reads', 'B.this[]')],
     source='csharpstandard classes.md §15.9.2')
case('csharp', 'user-defined-operator', 'an operator expression is a use of the user-defined operator of its operand types, also one a base class declares',
     CS, {'A.cs': 'namespace App\n{\n    public class Vec\n    {\n        public static Vec operator +(Vec a, Vec b) { return a; }\n\n        public static Vec operator -(Vec a, Vec b) { return b; }\n\n        public static Vec operator ++(Vec a) { return a; }\n    }\n\n    public class Special : Vec\n    {\n    }\n\n    public class C\n    {\n        public Vec Add(Vec a, Vec b) { return a + b; }\n\n        public Vec Sub(Vec a, Vec b) { return a - b; }\n\n        public Vec Inc(Vec a) { a++; return a; }\n\n        public Vec Inherited(Special a, Special b) { return a + b; }\n    }\n}\n'},
     [('C.Add', 'calls', 'Vec.operator+'), ('C.Sub', 'calls', 'Vec.operator-'), ('C.Inc', 'calls', 'Vec.operator++'), ('C.Inherited', 'calls', 'Vec.operator+')], [('C.Add', 'calls', 'Vec.operator-'), ('C.Sub', 'calls', 'Vec.operator+')],
     source='csharpstandard classes.md §15.10; expressions.md §12.4.4 (binary operator overload resolution)')
case('csharp', 'conversion-operator', 'an implicit or explicit conversion between a class and another type is a use of the conversion operator',
     CS, {'A.cs': 'namespace App\n{\n    public struct Digit\n    {\n        byte value;\n\n        public static implicit operator byte(Digit d) { return d.value; }\n\n        public static explicit operator Digit(byte b) { return new Digit(); }\n    }\n\n    public class C\n    {\n        public byte Widen(Digit d) { byte b = d; return b; }\n\n        public Digit Narrow(byte b) { return (Digit)b; }\n    }\n}\n'},
     [('C.Widen', 'calls', 'Digit.operator byte'), ('C.Narrow', 'calls', 'Digit.operator Digit')], [('C.Widen', 'calls', 'Digit.operator Digit'), ('C.Narrow', 'calls', 'Digit.operator byte')],
     source='csharpstandard classes.md §15.10.4; conversions.md §10.5')
case('csharp', 'implicit-base-constructor', "a constructor with no initializer calls the base class's parameterless constructor",
     CS, {'A.cs': 'namespace App\n{\n    public class A\n    {\n        public A() { }\n\n        public A(int n) { }\n    }\n\n    public class B : A\n    {\n        public B(string s) { }\n    }\n}\n'},
     [('B.B', 'calls', 'A.cs:App.A.A@5')], [('B.B', 'calls', 'A.cs:App.A.A@7')],
     source='csharpstandard classes.md §15.11.2')
case('csharp', 'constructor-initializer-parameter-hides-field', 'the parameters of a constructor are names of its initializer, before the fields',
     CS, {'A.cs': 'namespace App\n{\n    public class A\n    {\n        public A(int v) { }\n    }\n\n    public class B : A\n    {\n        int x;\n\n        public B(int x) : base(x) { }\n    }\n}\n'},
     [], [('B.B', 'reads', 'B.x')],
     source='csharpstandard classes.md §15.11.2')
case('csharp', 'foreach-over-iterator', "the variable of a `foreach` over an iterator method is the iterator's yield type",
     CS, {'A.cs': 'using System.Collections.Generic;\n\nnamespace App\n{\n    public class Item\n    {\n        public int Id() { return 1; }\n    }\n\n    public class Other\n    {\n        public int Id() { return 2; }\n    }\n\n    public class C\n    {\n        IEnumerable<Item> Items()\n        {\n            yield return new Item();\n        }\n\n        public int Sum()\n        {\n            int n = 0;\n            foreach (var i in Items())\n            {\n                n += i.Id();\n            }\n            return n;\n        }\n    }\n}\n'},
     [('C.Sum', 'calls', 'C.Items'), ('C.Sum', 'calls', 'Item.Id')], [('C.Sum', 'calls', 'Other.Id')],
     source='csharpstandard classes.md §15.15.4; statements.md §13.9.5')
case('csharp', 'override-is-no-candidate', "an `override` method is excluded from member lookup, so `d.M(1)` picks the derived class's own applicable `M(double)` over its override `M(int)`",
     CS, {'A.cs': 'namespace App\n{\n    public class Base\n    {\n        public virtual int M(int a) { return 1; }\n    }\n\n    public class Derived : Base\n    {\n        public override int M(int a) { return 2; }\n\n        public int M(double a) { return 3; }\n    }\n\n    public class C\n    {\n        public int Go(Derived d) { return d.M(1); }\n    }\n}\n'},
     [('C.Go', 'calls', 'A.cs:App.Derived.M@12')], [('C.Go', 'calls', 'A.cs:App.Derived.M@10'), ('C.Go', 'calls', 'A.cs:App.Base.M@5')],
     source='csharpstandard §15.6.5 and expressions.md §12.5 member lookup (members with an override modifier are removed)')
case('csharp', 'overload-by-ref-kind', '`Put(ref x)` is the overload whose parameter is a reference parameter, not the value one',
     CS, {'A.cs': 'namespace App\n{\n    public class Log\n    {\n        public int Put(int a) { return 1; }\n\n        public int Put(ref int a) { return 2; }\n    }\n\n    public class C\n    {\n        public int Go(Log l) { int x = 1; return l.Put(ref x); }\n    }\n}\n'},
     [('C.Go', 'calls', 'A.cs:App.Log.Put@7')], [('C.Go', 'calls', 'A.cs:App.Log.Put@5')],
     source='csharpstandard §15.6.1 (signature of a method) and §12.6.4.2 (an argument with ref corresponds to a reference parameter)')
case('csharp', 'constraint-through-type-parameter', "`t.Foo()` with `T : U` and `U : IFoo` is IFoo's member",
     CS, {'A.cs': 'namespace App\n{\n    public interface IFoo { int Foo(); }\n\n    public class Other { public int Foo() { return 2; } }\n\n    public class Box<T, U> where T : U where U : IFoo\n    {\n        public int Run(T t) { return t.Foo(); }\n    }\n}\n'},
     [('Box.Run', 'calls', 'IFoo.Foo')], [('Box.Run', 'calls', 'Other.Foo')],
     source='csharpstandard §15.2.5 (type parameter constraints) and §15.3.? member lookup in type parameters (§12.5)')
case('csharp', 'extension-on-constrained-type-parameter', '`t.Describe()` with `T : IFoo` is the extension written for IFoo',
     CS, {'A.cs': 'namespace App\n{\n    public interface IFoo { }\n\n    public static class Ext\n    {\n        public static int Describe(this IFoo f) { return 1; }\n    }\n\n    public class Box<T> where T : IFoo\n    {\n        public int Run(T t) { return t.Describe(); }\n    }\n}\n'},
     [('Box.Run', 'calls', 'Ext.Describe')], [],
     source='csharpstandard §15.6.10 and §12.8.10.3 extension method invocations (an implicit reference or boxing conversion from the type parameter)')
case('csharp', 'partial-base-in-other-part', "a bare name in a part that names no base is a member of the base another part names, before a type's member of that name elsewhere",
     CS, {'A.cs': 'namespace App\n{\n    public class Base\n    {\n        public int Inherited() { return 1; }\n    }\n\n    public partial class Part : Base\n    {\n    }\n}\n', 'B.cs': 'namespace App\n{\n    public class Decoy\n    {\n        public int Inherited() { return 2; }\n    }\n\n    public partial class Part\n    {\n        public int Run() { return Inherited(); }\n    }\n}\n'},
     [('Part.Run', 'calls', 'Base.Inherited')], [('Part.Run', 'calls', 'Decoy.Inherited')],
     source='csharpstandard §15.2.7 (partial types: the base class of the type is that named by any part)')
case('csharp', 'operator-forms-use-the-operator', "`a += b`, `a++`, `-a` and `a && b` call the operator methods of the operand's class",
     CS, {'A.cs': 'namespace App\n{\n    public class V\n    {\n        public static V operator +(V a, V b) { return a; }\n\n        public static V operator ++(V a) { return a; }\n\n        public static V operator -(V a) { return a; }\n\n        public static bool operator true(V a) { return true; }\n\n        public static bool operator false(V a) { return false; }\n\n        public static V operator &(V a, V b) { return a; }\n    }\n\n    public class C\n    {\n        public V Add(V a, V b) { a += b; return a; }\n\n        public V Inc(V a) { a++; return a; }\n\n        public V Neg(V a) { return -a; }\n\n        public V Both(V a, V b) { return a && b; }\n    }\n}\n'},
     [('C.Add', 'calls', 'V.operator+'), ('C.Inc', 'calls', 'V.operator++'), ('C.Neg', 'calls', 'V.operator-'), ('C.Both', 'calls', 'V.operator&'), ('C.Both', 'calls', 'V.operator false')], [('C.Add', 'calls', 'V.operator++'), ('C.Neg', 'calls', 'V.operator+'), ('C.Inc', 'calls', 'V.operator+')],
     source='csharpstandard §15.10.2 unary operators, §15.10.3 binary operators, §12.13.? conditional logical operators (user-defined)')
case('csharp', 'nested-type-reaches-private-of-container', "`o.secret` and `Shared()` in a nested class are the containing class's private field and static method, not a same-named member of another class",
     CS, {'A.cs': 'namespace App\n{\n    public class Other\n    {\n        public int secret;\n\n        public static int Shared() { return 9; }\n    }\n\n    public class Outer\n    {\n        private int secret;\n\n        private static int Shared() { return 1; }\n\n        public class Inner\n        {\n            public int Peek(Outer o) { return o.secret + Shared(); }\n        }\n    }\n}\n'},
     [('Outer.Inner.Peek', 'reads', 'Outer.secret'), ('Outer.Inner.Peek', 'calls', 'Outer.Shared')], [('Outer.Inner.Peek', 'reads', 'Other.secret'), ('Outer.Inner.Peek', 'calls', 'Other.Shared')],
     source='csharpstandard §15.3.9.6 Access to private and protected members of the containing type')
case('csharp', 'await-getawaiter-pattern', "`await j` where j's type declares GetAwaiter is a call of that type's GetAwaiter, not another type's",
     CS, {'A.cs': 'using System.Runtime.CompilerServices;\nnamespace App {\n  public class Awaiter : INotifyCompletion {\n    public bool IsCompleted => true;\n    public void OnCompleted(System.Action a) { a(); }\n    public int GetResult() { return 1; }\n  }\n  public class Job { public Awaiter GetAwaiter() { return new Awaiter(); } }\n  public class Other { public Awaiter GetAwaiter() { return new Awaiter(); } }\n  public class Runner {\n    public async System.Threading.Tasks.Task<int> Run(Job j) { return await j; }\n  }\n}\n'},
     [('Runner.Run', 'calls', 'Job.GetAwaiter')], [('Runner.Run', 'calls', 'Other.GetAwaiter')],
     source='csharpstandard §15.14.3 (async functions); Roslyn Binder_Await.cs BindAwait')
case('csharp', 'foreach-getenumerator-pattern', '`foreach (var i in b)` over a type with a GetEnumerator method calls it, and `i.V` is the field of the type of Cursor.Current',
     CS, {'A.cs': 'namespace App {\n  public class Item { public int V; }\n  public class Bag {\n    public Cursor GetEnumerator() { return new Cursor(); }\n  }\n  public class Cursor {\n    public bool MoveNext() { return false; }\n    public Item Current { get { return null; } }\n  }\n  public class Other { public Item Current { get { return null; } } }\n  public class Use {\n    public int Sum(Bag b) { int t = 0; foreach (var i in b) { t += i.V; } return t; }\n  }\n}\n'},
     [('Use.Sum', 'calls', 'Bag.GetEnumerator'), ('Use.Sum', 'reads', 'Item.V')], [('Use.Sum', 'reads', 'Other.Current')],
     source='csharpstandard §15.15.6.2; Roslyn ForEachLoopBinder.cs GetEnumeratorInfo')
case('csharp', 'setter-value-parameter', "inside a set accessor `value` is the implicit parameter of the property's type, not a field of that name",
     CS, {'A.cs': 'namespace App {\n  public class A { public void Run() {} }\n  public class B { public void Run() {} }\n  public class C {\n    private A value;\n    private B _p;\n    public B P { get { return _p; } set { value.Run(); } }\n  }\n}\n'},
     [('C.P', 'calls', 'B.Run')], [('C.P', 'calls', 'A.Run')],
     source='csharpstandard §15.7.3; Roslyn Binder/InMethodBinder (accessor value parameter)')
case('csharp', 'nested-type-parameter-hides-outer', "a nested generic class's type parameter named like the outer's is the nested one: members asked of it are those of its own constraint",
     CS, {'A.cs': 'namespace App {\n  public class A { public void Run() {} }\n  public class B { public void Run() {} }\n  public class Outer<T> where T : A {\n    public class Inner<T> where T : B {\n      public void F(T t) { t.Run(); }\n    }\n  }\n}\n'},
     [('Inner.F', 'calls', 'B.Run')], [('Inner.F', 'calls', 'A.Run')],
     source='csharpstandard §15.3.9.7')
case('csharp', 'params-normal-form-before-expanded', 'M(1, 2) calls M(int, int) rather than the expanded form of M(params int[])',
     CS, {'A.cs': 'namespace App {\n  public class C {\n    public void M(params int[] xs) {}\n    public void M(int a, int b) {}\n    public void Use() { M(1, 2); }\n  }\n}\n'},
     [('C.Use', 'calls', 'C.M')], [],
     source='csharpstandard §15.6.2.4; Roslyn OverloadResolution.BetterFunctionMember (expanded vs normal form)')
case('csharp', 'non-invocable-member-does-not-hide-method', "a field named like a base method, invoked, is no candidate: the call is the base class's method",
     CS, {'A.cs': 'namespace App {\n  public class Base { public void M() {} }\n  public class Derived : Base { public new int M; }\n  public class U { public void F(Derived d) { d.M(); } }\n}\n'},
     [('U.F', 'calls', 'Base.M')], [('U.F', 'reads', 'Derived.M')],
     source='csharpstandard §15.3.5 with §12.5; Roslyn Binder.LookupSymbolsInSingleBinder (LookupOptions.MustBeInvocableIfMember)')
case('csharp', 'await-foreach-pattern', '`await foreach (var x in b)` calls the GetAsyncEnumerator the type of b declares, and x has the Current type of what it returns',
     CS, {'A.cs': 'namespace App {\n  public class It { public int Current => 0; public System.Threading.Tasks.ValueTask<bool> MoveNextAsync() => default; }\n  public class Bag { public It GetAsyncEnumerator() => new It(); }\n  public class U { public async System.Threading.Tasks.Task F(Bag b) { await foreach (var x in b) { } } }\n}\n'},
     [('U.F', 'calls', 'Bag.GetAsyncEnumerator')], [],
     source='csharpstandard §15.15.6.2; Roslyn Binder_Statements ForEachLoopBinder (GetAsyncEnumerator)')
case('csharp', 'checked-operator', '`checked(a + b)` uses `operator checked +`, `unchecked(a + b)` the plain `operator +`',
     CS, {'A.cs': 'namespace App {\n  public class V {\n    public static V operator +(V a, V b) => a;\n    public static V operator checked +(V a, V b) => b;\n  }\n  public class U { public V F(V a, V b) { return checked(a + b); } public V G(V a, V b) { return unchecked(a + b); } }\n}\n'},
     [], [],
     source='csharpstandard §15.10; Roslyn Binder_Operators.BindUnaryOperator/UserDefinedBinaryOperators (checked variants)', since='11')
case('csharp', 'implicit-index-support', "`l[^1]` is a use of the int indexer of l's type and of its Count",
     CS, {'A.cs': 'namespace App {\n  public class L { public int Count => 0; public string this[int i] => ""; }\n  public class U { public string F(L l) { return l[^1]; } }\n}\n'},
     [('U.F', 'reads', 'L.Count')], [],
     source='csharpstandard §15.9; Roslyn Binder_Indexer.cs (implicit Index support, TryBindImplicitIndexer)', since='8')
case('csharp', 'object-initializer-member', '`new C { X = …, N = 1 }` writes C.X and C.N, the members of C, not names of the surrounding scope',
     CS, {'A.cs': 'namespace App {\n  public class A { public void Run() {} }\n  public class C { public A X { get; set; } public int N; }\n  public class D {\n    public void F() { var c = new C { X = new A(), N = 1 }; }\n  }\n}\n'},
     [('D.F', 'writes', 'C.X'), ('D.F', 'writes', 'C.N')], [],
     source='csharpstandard §15.7.3 with §12.8.17.3; Roslyn Binder_InitializerExpressions.BindInitializerMemberAssignment')
case('csharp', 'partial-struct', 'the members of a partial struct are those of all its parts',
     CS, {'A1.cs': 'namespace App\n{\n    public partial struct S { int Helper() { return 1; } }\n}\n', 'A2.cs': 'namespace App\n{\n    public partial struct S { public int Run() { return Helper(); } }\n}\n'},
     [('S.Run', 'calls', 'S.Helper')], [],
     source='csharpstandard §16.2.4')
case('csharp', 'class-and-interfaces-in-a-base-list', 'a base list names a class (inherits) and interfaces (implements); a struct names only interfaces',
     CS, {'A.cs': 'namespace App\n{\n    public interface IStore { }\n    public interface IOther { }\n    public class Base { }\n    public class Repo : Base, IStore { }\n    public struct Item : IStore { }\n}\n'},
     [('Repo', 'inherits', 'Base'), ('Repo', 'implements', 'IStore'), ('Item', 'implements', 'IStore')], [('Repo', 'implements', 'Base'), ('Repo', 'inherits', 'IStore'), ('Repo', 'implements', 'IOther')],
     source='csharpstandard §19.6.1, §16.2.5')
case('csharp', 'struct-overrides-a-valuetype-member', "a struct's own override of ToString is what `s.ToString()` means",
     CS, {'A.cs': 'namespace App\n{\n    public struct Counter\n    {\n        int value;\n        public override string ToString() { return value.ToString(); }\n    }\n    public class Other { public override string ToString() { return ""; } }\n    public class A\n    {\n        public string Run(Counter c) { return c.ToString(); }\n    }\n}\n'},
     [('A.Run', 'calls', 'Counter.ToString')], [('A.Run', 'calls', 'Other.ToString')],
     source='csharpstandard §16.3.1, §16.4.3, §16.4.7 MeaningOfThis1')
case('csharp', 'struct-default-constructor', '`new S()` of a struct without a parameterless constructor is the implicit one; a constructor with parameters is not called',
     CS, {'A.cs': 'namespace App\n{\n    public struct Point\n    {\n        int x, y;\n        public Point(int x, int y) { this.x = x; this.y = y; }\n    }\n    public class A\n    {\n        public Point Zero() { return new Point(); }\n        public Point Made() { return new Point(1, 2); }\n    }\n}\n'},
     [('A.Made', 'calls', 'Point.Point')], [('A.Zero', 'calls', 'Point.Point')],
     source='csharpstandard §16.4.9 Constructors1')
case('csharp', 'constraint-interface-member', 'a member asked of a type parameter is a member of its interface constraint',
     CS, {'A.cs': 'namespace App\n{\n    public interface ICounter { void Increment(); }\n    public interface IOther { void Increment(); }\n    public struct Counter : ICounter { public void Increment() { } }\n    public class A\n    {\n        public static void Test<T>(T x) where T : ICounter, new()\n        {\n            T y = new T();\n            x.Increment();\n            y.Increment();\n        }\n    }\n}\n'},
     [('A.Test', 'calls', 'ICounter.Increment')], [('A.Test', 'calls', 'IOther.Increment'), ('A.Test', 'calls', 'Counter.Increment')],
     source='csharpstandard §16.4.7 MeaningOfThis2')
case('csharp', 'struct-static-field-initializer', "the initializer of a static field of a struct names the struct's members",
     CS, {'A.cs': 'namespace App\n{\n    public struct Point\n    {\n        public int X;\n        static Point Make() { return new Point(); }\n        public static readonly Point Origin = Make();\n    }\n    public class Other { static Other Make() { return null; } }\n}\n'},
     [('Point', 'calls', 'Point.Make')], [('Point', 'calls', 'Other.Make')],
     source='csharpstandard §16.4.8')
case('csharp', 'array-element-access', "an element of an array is of the array's element type, whatever the rank and nesting",
     CS, {'A.cs': 'namespace App\n{\n    public class Repo { public int Find() { return 1; } }\n    public class Other { public int Find() { return 2; } }\n    public class A\n    {\n        public int Flat(Repo[] a) { return a[0].Find(); }\n        public int Grid(Repo[,] g) { return g[0, 1].Find(); }\n        public int Jagged(Repo[][] j) { return j[0][1].Find(); }\n        public int Mixed(Repo[][,] m) { return m[0][1, 2].Find(); }\n        public int Last(Repo[] a) { return a[^1].Find(); }\n    }\n}\n'},
     [('A.Flat', 'calls', 'Repo.Find'), ('A.Grid', 'calls', 'Repo.Find'), ('A.Jagged', 'calls', 'Repo.Find'), ('A.Mixed', 'calls', 'Repo.Find'), ('A.Last', 'calls', 'Repo.Find')], [('A.Flat', 'calls', 'Other.Find')],
     source='csharpstandard §17.2.1, §17.4')
case('csharp', 'extension-on-array-type', 'an extension written for an array type applies to values of that array type',
     CS, {'A.cs': 'namespace App\n{\n    public class Repo { }\n    public class Other { }\n    public static class RepoExt { public static int Size(this Repo[] r) { return 1; } }\n    public static class OtherExt { public static int Size(this Other[] o) { return 2; } }\n    public class A\n    {\n        public int Run(Repo[] r) { return r.Size(); }\n    }\n}\n'},
     [('A.Run', 'calls', 'RepoExt.Size')], [('A.Run', 'calls', 'OtherExt.Size')],
     source='csharpstandard §17.2, §12.8.10.3')
case('csharp', 'array-creation-element', '`new T[n]` is an array of T; its elements are T',
     CS, {'A.cs': 'namespace App\n{\n    public class Repo { public int Find() { return 1; } }\n    public class Other { public int Find() { return 2; } }\n    public class A\n    {\n        public int Run()\n        {\n            var a = new Repo[3];\n            return a[0].Find();\n        }\n    }\n}\n'},
     [('A.Run', 'calls', 'Repo.Find')], [('A.Run', 'calls', 'Other.Find')],
     source='csharpstandard §17.3')
case('csharp', 'array-slice-keeps-the-array-type', 'a range index on an array gives an array of the same element type',
     CS, {'A.cs': 'namespace App\n{\n    public class Repo { public int Find() { return 1; } }\n    public class Other { public int Find() { return 2; } }\n    public class A\n    {\n        public int Run(Repo[] a) { return a[1..3][0].Find(); }\n    }\n}\n'},
     [('A.Run', 'calls', 'Repo.Find')], [('A.Run', 'calls', 'Other.Find')],
     source='csharpstandard §17.4', since='8.0')
case('csharp', 'array-foreach-element', 'a `foreach` over an array of T gives T',
     CS, {'A.cs': 'namespace App\n{\n    public class Repo { public int Find() { return 1; } }\n    public class Other { public int Find() { return 2; } }\n    public class A\n    {\n        public void Run(Repo[] a)\n        {\n            foreach (var r in a) { r.Find(); }\n        }\n    }\n}\n'},
     [('A.Run', 'calls', 'Repo.Find')], [('A.Run', 'calls', 'Other.Find')],
     source='csharpstandard §17.4 (foreach), §13.9.5')
case('csharp', 'interface-inherited-member', 'a member of a base interface is a member of the derived interface',
     CS, {'A.cs': 'namespace App\n{\n    public interface IControl { void Paint(); }\n    public interface IOther { void Paint(); }\n    public interface ITextBox : IControl { void SetText(string t); }\n    public interface IListBox : IControl { void SetItems(string[] items); }\n    public interface IComboBox : ITextBox, IListBox { }\n\n    public class A\n    {\n        public void Run(IComboBox c) { c.Paint(); c.SetText("x"); c.SetItems(null); }\n    }\n}\n'},
     [('A.Run', 'calls', 'IControl.Paint'), ('A.Run', 'calls', 'ITextBox.SetText'), ('A.Run', 'calls', 'IListBox.SetItems')], [('A.Run', 'calls', 'IOther.Paint')],
     source='csharpstandard §19.2.4, §19.4.1')
case('csharp', 'interface-inherited-generic-member', 'a member inherited from a constructed interface has its type parameters replaced by the type arguments',
     CS, {'A.cs': 'namespace App\n{\n    public interface IBase<T> { T Get(); }\n    public interface IDerived : IBase<Repo> { }\n    public class Repo { public int Find() { return 1; } }\n    public class Other { public int Find() { return 2; } }\n\n    public class A\n    {\n        public int Run(IDerived d) { return d.Get().Find(); }\n    }\n}\n'},
     [('A.Run', 'calls', 'IBase.Get'), ('A.Run', 'calls', 'Repo.Find')], [('A.Run', 'calls', 'Other.Find')],
     source='csharpstandard §19.2.4 BaseInterfaces2')
case('csharp', 'extension-on-base-interface', 'an extension written for an interface applies to values of the interfaces derived from it',
     CS, {'A.cs': 'namespace App\n{\n    public interface IBase { }\n    public interface IDerived : IBase { }\n    public interface IUnrelated { }\n    public static class BaseExt { public static int Size(this IBase b) { return 1; } }\n    public static class UnrelatedExt { public static int Size(this IUnrelated b) { return 2; } }\n    public class A\n    {\n        public int Run(IDerived d) { return d.Size(); }\n    }\n}\n'},
     [('A.Run', 'calls', 'BaseExt.Size')], [('A.Run', 'calls', 'UnrelatedExt.Size')],
     source='csharpstandard §19.2.4, §12.8.10.3')
case('csharp', 'explicit-implementation-names-the-declaring-interface', 'an explicit implementation of an inherited member is qualified by the interface that declares it, not the derived one',
     CS, {'A.cs': 'namespace App\n{\n    public interface IControl { void Paint(); }\n    public interface ITextBox : IControl { void SetText(string s); }\n    public class TextBox : ITextBox\n    {\n        void IControl.Paint() { }\n        void ITextBox.SetText(string s) { }\n    }\n    public class A\n    {\n        public void Run(ITextBox t) { t.Paint(); }\n    }\n}\n'},
     [('A.Run', 'calls', 'IControl.Paint'), ('TextBox', 'implements', 'ITextBox')], [('TextBox', 'implements', 'IControl')],
     source='csharpstandard §19.6.2 ExplicitInterfaceMemberImplementations5, §19.6.1')
case('csharp', 'interface-member-hides-base', "a member a derived interface declares hides the base interface's member of its name",
     CS, {'A.cs': 'namespace App\n{\n    public interface IBase { int Find(); }\n    public interface IDerived : IBase { new int Find(); }\n\n    public class A\n    {\n        public int Run(IDerived d) { return d.Find(); }\n        public int RunBase(IDerived d) { return ((IBase)d).Find(); }\n    }\n}\n'},
     [('A.Run', 'calls', 'IDerived.Find'), ('A.RunBase', 'calls', 'IBase.Find')], [('A.Run', 'calls', 'IBase.Find'), ('A.RunBase', 'calls', 'IDerived.Find')],
     source='csharpstandard §19.4.1, §19.4.11 (hiding)')
case('csharp', 'interface-hidden-on-every-path', 'a member hidden on any access path is hidden on all of them',
     CS, {'A.cs': 'namespace App\n{\n    public interface IBase { void F(int i); }\n    public interface ILeft : IBase { new void F(int i); }\n    public interface IRight : IBase { void G(); }\n    public interface IDerived : ILeft, IRight { }\n\n    public class A\n    {\n        public void Run(IDerived d) { d.F(1); }\n        public void ViaRight(IDerived d) { ((IRight)d).F(1); }\n    }\n}\n'},
     [('A.Run', 'calls', 'ILeft.F'), ('A.ViaRight', 'calls', 'IBase.F')], [('A.Run', 'calls', 'IBase.F'), ('A.ViaRight', 'calls', 'ILeft.F')],
     source='csharpstandard §19.4.11 InterfaceMemberAccess3')
case('csharp', 'interface-overloads-of-two-bases', 'the members of unrelated base interfaces are all candidates of an invocation',
     CS, {'A.cs': 'namespace App\n{\n    public interface IInteger { void Add(int i); }\n    public interface IDouble { void Add(double d); }\n    public interface INumber : IInteger, IDouble { }\n\n    public class A\n    {\n        public void Run(INumber n) { n.Add(1); }\n        public void RunDouble(INumber n) { n.Add(1.0); }\n        public void Cast(INumber n) { ((IDouble)n).Add(1); }\n    }\n}\n'},
     [('A.Run', 'calls', 'IInteger.Add'), ('A.RunDouble', 'calls', 'IDouble.Add'), ('A.Cast', 'calls', 'IDouble.Add')], [('A.Run', 'calls', 'IDouble.Add'), ('A.RunDouble', 'calls', 'IInteger.Add'), ('A.Cast', 'calls', 'IInteger.Add')],
     source='csharpstandard §19.4.11 InterfaceMemberAccess2')
case('csharp', 'interface-default-method-names-base-member', 'a default method of an interface names the members of its base interfaces by their simple names',
     CS, {'A.cs': 'namespace App\n{\n    public interface IA { int M(); }\n    public interface IOther { int M(); }\n    public interface IB : IA\n    {\n        int Twice() { return M() + M(); }\n    }\n}\n'},
     [('IB.Twice', 'calls', 'IA.M')], [('IB.Twice', 'calls', 'IOther.M')],
     source='csharpstandard §19.4.1 (members of an interface include the inherited ones), InterfaceMember example', since='8.0')
case('csharp', 'partial-interface', 'the members of a partial interface are those of all its parts',
     CS, {'A1.cs': 'namespace App\n{\n    public partial interface IStore { int Find(); }\n}\n', 'A2.cs': 'namespace App\n{\n    public partial interface IStore { int Count(); }\n    public class A\n    {\n        public int Run(IStore s) { return s.Find() + s.Count(); }\n    }\n}\n'},
     [('A.Run', 'calls', 'IStore.Find'), ('A.Run', 'calls', 'IStore.Count')], [],
     source='csharpstandard §19.4.1 (partial interface)')
case('csharp', 'interface-static-member', 'a static member of an interface is named through the interface',
     CS, {'A.cs': 'namespace App\n{\n    public interface IFactory\n    {\n        public const int Limit = 3;\n        static IFactory Make() { return null; }\n        static int Count() { return Limit; }\n    }\n    public class Other { public static int Count() { return 0; } }\n    public class A\n    {\n        public int Run() { IFactory f = IFactory.Make(); return IFactory.Count() + IFactory.Limit; }\n    }\n}\n'},
     [('A.Run', 'calls', 'IFactory.Make'), ('A.Run', 'calls', 'IFactory.Count'), ('IFactory.Count', 'reads', 'IFactory.Limit')], [('A.Run', 'calls', 'Other.Count')],
     source='csharpstandard §19.4.2, §19.4.3 (static interface members)', since='8.0')
case('csharp', 'interface-nested-type', 'a type nested in an interface is a member of the interface, named through it',
     CS, {'A.cs': 'namespace App\n{\n    public interface IOuter\n    {\n        public class Inner { public int Find() { return 1; } }\n    }\n    public class Other { public int Find() { return 2; } }\n    public class A\n    {\n        public int Run(IOuter.Inner i) { return i.Find(); }\n    }\n}\n'},
     [('A.Run', 'calls', 'IOuter.Inner.Find'), ('A.Run', 'references', 'IOuter.Inner')], [('A.Run', 'calls', 'Other.Find')],
     source='csharpstandard §19.4.9, §19.4.1', since='8.0')
case('csharp', 'base-call-reaches-the-class-member', '`base.M()` binds to the member of the base class, never to the interface member it implements',
     CS, {'A.cs': 'namespace App\n{\n    public interface I { void M(); }\n    public class B : I { public virtual void M() { } }\n    public class D : B\n    {\n        public override void M() { base.M(); }\n    }\n}\n'},
     [('D.M', 'calls', 'B.M')], [('D.M', 'calls', 'I.M')],
     source='csharpstandard §19.4.11 (base.M() binds to an implementation in a class type)')
case('csharp', 'explicit-implementation-names-its-interface', "the interface in an explicit implementation's qualified name is the one the name means in that scope",
     CS, {'A.cs': 'namespace Lib\n{\n    public interface IShape { void Area(); }\n}\nnamespace App\n{\n    public interface IShape { void Area(); }\n    public class Circle : Lib.IShape\n    {\n        void Lib.IShape.Area() { }\n    }\n    public class Square : IShape\n    {\n        void IShape.Area() { }\n    }\n}\n'},
     [('Circle', 'implements', 'Lib.IShape'), ('Square', 'implements', 'App.IShape')], [('Circle', 'implements', 'App.IShape'), ('Square', 'implements', 'Lib.IShape')],
     source='csharpstandard §19.5, §19.6.2')
case('csharp', 'explicit-implementation-through-the-interface', 'an explicit interface member implementation is reached through the interface, never the class',
     CS, {'A.cs': 'namespace App\n{\n    public interface IControl { void Paint(); }\n    public class Control : IControl\n    {\n        void IControl.Paint() { PaintControl(); }\n        public void Paint() { }\n        protected virtual void PaintControl() { }\n    }\n\n    public class A\n    {\n        public void ViaClass(Control c) { c.Paint(); }\n        public void ViaInterface(IControl c) { c.Paint(); }\n        public void ViaCast(Control c) { ((IControl)c).Paint(); }\n    }\n}\n'},
     [('A.ViaClass', 'calls', 'Control.Paint'), ('A.ViaInterface', 'calls', 'IControl.Paint'), ('A.ViaCast', 'calls', 'IControl.Paint')], [('A.ViaClass', 'calls', 'IControl.Paint'), ('A.ViaInterface', 'calls', 'Control.Paint'), ('A.ViaCast', 'calls', 'Control.Paint')],
     source='csharpstandard §19.6.2, §19.6.5')
case('csharp', 'explicit-implementation-body', 'the body of an explicit interface member implementation sees the members of its class by their simple names',
     CS, {'A.cs': 'namespace App\n{\n    public interface IDisposable2 { void Dispose(); }\n    public class MyFile : IDisposable2\n    {\n        void IDisposable2.Dispose() => Close();\n        public void Close() { }\n    }\n    public class Other { public void Close() { } }\n}\n'},
     [('MyFile.Dispose', 'calls', 'MyFile.Close')], [('MyFile.Dispose', 'calls', 'Other.Close')],
     source='csharpstandard §19.6.2 ExplicitInterfaceMemberImplementations2')
case('csharp', 'interface-mapping-through-a-base-class', 'a public member of a base class implements an interface the derived class lists; a derived `new` member does not take its place',
     CS, {'A.cs': 'namespace App\n{\n    public interface Interface1 { void F(); }\n    public class Class1 { public void F() { } public void G() { } }\n    public class Class2 : Class1, Interface1 { public new void G() { } }\n\n    public interface IControl { void Paint(); }\n    public class Control : IControl { public void Paint() { } }\n    public class TextBox : Control { public new void Paint() { } }\n}\n'},
     [('Interface1.F', 'dispatches_to', 'Class1.F'), ('IControl.Paint', 'dispatches_to', 'Control.Paint')], [('IControl.Paint', 'dispatches_to', 'TextBox.Paint')],
     source='csharpstandard §19.6.5 InterfaceMapping8, §19.6.6')
case('csharp', 'new-member-overrides-nothing', 'a member declared `new` hides the base member: calls through the base do not reach it',
     CS, {'A.cs': 'namespace App\n{\n    public class Class1 { public void G() { } public virtual void V() { } }\n    public class Class2 : Class1 { public new void G() { } public override void V() { } }\n    public class A\n    {\n        public void Run(Class1 c) { c.G(); }\n    }\n}\n'},
     [('A.Run', 'calls', 'Class1.G'), ('Class1.V', 'dispatches_to', 'Class2.V')], [('Class1.G', 'dispatches_to', 'Class2.G'), ('A.Run', 'calls', 'Class2.G')],
     source='csharpstandard §19.6.6 InterfaceImplementationInheritance1, §15.3.5')
case('csharp', 'interface-mapping-one-member-for-two-interfaces', 'one public member implements the same-named members of every interface the class lists',
     CS, {'A.cs': 'namespace App\n{\n    public interface IControl { void Paint(); }\n    public interface IForm { void Paint(); }\n    public class Page : IControl, IForm { public void Paint() { } }\n}\n'},
     [('IControl.Paint', 'dispatches_to', 'Page.Paint'), ('IForm.Paint', 'dispatches_to', 'Page.Paint')], [],
     source='csharpstandard §19.6.5 InterfaceMapping4')
case('csharp', 'interface-reimplementation', 'a class that lists an inherited interface again maps it afresh: onto its own public member',
     CS, {'A.cs': 'namespace App\n{\n    public interface IControl { void Paint(); }\n    public class Control : IControl { public void Paint() { } }\n    public class MyControl : Control, IControl { public new void Paint() { } }\n}\n'},
     [('IControl.Paint', 'dispatches_to', 'MyControl.Paint')], [],
     source='csharpstandard §19.6.7')
case('csharp', 'enum-underlying-type-name', 'the integral type name of an enum is a type name, looked up like any other',
     CS, {'A.cs': 'namespace App\n{\n    using Level = System.Byte;\n    public class Code { }\n    public enum Color : Level { Red, Green }\n}\n'},
     [], [('Color', 'references', 'Code')],
     source='csharpstandard §20.2')
case('csharp', 'enum-member-initializer', "an enum member's initializer names the other members of its enum by their simple names, before a type of the same name around",
     CS, {'A.cs': 'namespace App\n{\n    public class Blue { }\n    public enum Color\n    {\n        Red,\n        Blue,\n        Max = Blue\n    }\n}\n'},
     [], [('Color.Max', 'references', 'App.Blue')],
     source='csharpstandard §20.4')
case('csharp', 'extension-on-enum-value', 'an enum member has the type of its enum: an extension of the enum applies to it',
     CS, {'A.cs': 'namespace App\n{\n    public enum Color { Red, Green }\n    public enum Size { Small, Large }\n    public static class ColorExt { public static int Code(this Color c) { return 1; } }\n    public static class SizeExt { public static int Code(this Size s) { return 2; } }\n    public class A\n    {\n        public int Direct() { return Color.Red.Code(); }\n        public int ViaLocal() { var c = Color.Green; return c.Code(); }\n    }\n}\n'},
     [('A.Direct', 'calls', 'ColorExt.Code'), ('A.ViaLocal', 'calls', 'ColorExt.Code')], [('A.Direct', 'calls', 'SizeExt.Code'), ('A.ViaLocal', 'calls', 'SizeExt.Code')],
     source='csharpstandard §20.6')
case('csharp', 'generic-delegate-type', 'a generic delegate type is named by its arity; type arguments give its parameter types',
     CS, {'A.cs': 'namespace App\n{\n    public delegate T Make<T>();\n    public class Repo { public int Find() { return 1; } }\n    public class Other { public int Find() { return 2; } }\n    public class A\n    {\n        public int Run(Make<Repo> m) { return m().Find(); }\n    }\n}\n'},
     [('A.Run', 'calls', 'Repo.Find')], [('A.Run', 'calls', 'Other.Find')],
     source='csharpstandard §21.2')
case('csharp', 'delegate-invoke-member', "`d.Invoke(…)` is the delegate type's Invoke, not a method of another type",
     CS, {'A.cs': 'namespace App\n{\n    public delegate int D(int x);\n    public class Other { public int Invoke(int x) { return x; } }\n    public class A\n    {\n        public int Run(D d) { return d.Invoke(1); }\n    }\n}\n'},
     [('A.Run', 'references', 'D')], [('A.Run', 'calls', 'Other.Invoke')],
     source='csharpstandard §21.3')
case('csharp', 'delegate-creation-names-a-method', "`new D(x.M)` names the method M of x's type",
     CS, {'A.cs': 'namespace App\n{\n    public delegate void D(int x);\n    public class C { public static void M1(int i) { } public void M2(int i) { } }\n    public class Other { public static void M1(int i) { } public void M2(int i) { } }\n    public class Test\n    {\n        public D Static() { return new D(C.M1); }\n        public D Instance(C t) { return new D(t.M2); }\n    }\n}\n'},
     [('Test.Static', 'indirect_call', 'C.M1'), ('Test.Instance', 'indirect_call', 'C.M2'), ('Test.Static', 'references', 'D')], [('Test.Static', 'indirect_call', 'Other.M1'), ('Test.Instance', 'indirect_call', 'Other.M2')],
     source='csharpstandard §21.5')
case('csharp', 'delegate-local-hides-method', 'a parameter of delegate type named like a method is what `name(args)` invokes',
     CS, {'A.cs': 'namespace App\n{\n    public delegate void D(int x);\n    public class A\n    {\n        void Log(int x) { }\n        public void Run(D Log) { Log(1); }\n    }\n}\n'},
     [('A.Run', 'references', 'D')], [('A.Run', 'calls', 'A.Log')],
     source='csharpstandard §21.6, §12.8.4')
case('csharp', 'delegate-field-invocation', 'invoking a field of delegate type is not a call of a method of its name',
     CS, {'A.cs': 'namespace App\n{\n    public delegate int D(int x);\n    public class A\n    {\n        D handler;\n        int handle(int x) { return x; }\n        public int Run() { return handler(1); }\n    }\n    public class Other { public int handler(int x) { return x; } }\n}\n'},
     [('A.Run', 'reads', 'A.handler')], [('A.Run', 'calls', 'Other.handler')],
     source='csharpstandard §21.6')
case('csharp', 'delegate-signature-types', "a delegate declaration's parameter and return types are uses of the types they name, the enclosing class's nested type before a namespace type",
     CS, {'A.cs': 'namespace App\n{\n    public class Host\n    {\n        public class Seen { }\n        public delegate Seen Make(Seen s);\n    }\n}\n', 'B.cs': 'namespace App\n{\n    public class Seen { }\n}\n'},
     [('Host.Make', 'references', 'Host.Seen')], [('Host.Make', 'references', 'B.cs:Seen')],
     source='csharpstandard §21.2')
case('csharp', 'extension-on-delegate', 'an extension written for a delegate type applies to a value of that delegate type',
     CS, {'A.cs': 'namespace App\n{\n    public delegate int Count(Other o);\n    public delegate int Total(Other o);\n    public class Other { }\n\n    public static class ExtA\n    {\n        public static int Twice(this Count c) { return 2; }\n    }\n\n    public static class ExtB\n    {\n        public static int Twice(this Total c) { return 3; }\n    }\n\n    public class A\n    {\n        public int Run(Count c)\n        {\n            return c.Twice();\n        }\n    }\n}\n'},
     [('A.Run', 'calls', 'ExtA.Twice')], [('A.Run', 'calls', 'ExtB.Twice')],
     source='csharpstandard §21.2, §12.8.10.3')
case('csharp', 'method-group-to-delegate', '`Op o = Target;` names the method Target; it is an indirect use',
     CS, {'A.cs': 'namespace App\n{\n    public delegate int Op(int x);\n\n    public class A\n    {\n        public int Target(int x) { return x; }\n        public int Decoy(int x) { return x; }\n        public int Run()\n        {\n            Op o = Target;\n            return o(1);\n        }\n    }\n}\n'},
     [('A.Run', 'indirect_call', 'A.Target')], [('A.Run', 'indirect_call', 'A.Decoy')],
     source='csharpstandard §21.3, §10.8')
case('csharp', 'interface-property-access', "`b.Current` on an IBox is IBox.Current, and the call on its result is the interface property's type's",
     CS, {'A.cs': 'namespace App\n{\n    public class Seen { public int Mark() { return 1; } }\n    public class Other { public int Mark() { return 2; } }\n\n    public interface IBox\n    {\n        Seen Current { get; set; }\n        Seen this[int i] { get; }\n    }\n\n    public class Box : IBox\n    {\n        public Other Current { get; set; }\n        Seen IBox.Current { get; set; }\n        public Other this[int i] { get { return null; } }\n        Seen IBox.this[int i] { get { return null; } }\n    }\n\n    public class A\n    {\n        public int Run(IBox b)\n        {\n            return b.Current.Mark() + b[0].Mark();\n        }\n    }\n}\n'},
     [('A.Run', 'reads', 'IBox.Current'), ('A.Run', 'calls', 'Seen.Mark')], [('A.Run', 'calls', 'Other.Mark'), ('A.Run', 'reads', 'Box.Current')],
     source='csharpstandard §19.4.11')
case('csharp', 'implicit-array-element', '`var xs = new[] { new Seen() }; xs[0].Mark()` is Seen.Mark',
     CS, {'A.cs': 'namespace App\n{\n    public class Seen { public int Mark() { return 1; } }\n    public class Other { public int Mark() { return 2; } }\n\n    public class A\n    {\n        public int Run()\n        {\n            var xs = new[] { new Seen(), new Seen() };\n            return xs[0].Mark();\n        }\n    }\n}\n'},
     [('A.Run', 'calls', 'Seen.Mark')], [('A.Run', 'calls', 'Other.Mark')],
     source='csharpstandard §17.3, §12.8.17.5')
case('csharp', 'extension-on-ref-struct-receiver', '`c.Bump()` on a Counter variable is the extension written `this ref Counter`',
     CS, {'A.cs': 'namespace App\n{\n    public struct Counter { public int N; }\n    public struct Other { public int N; }\n\n    public static class ExtA\n    {\n        public static void Bump(this ref Counter c) { c.N++; }\n    }\n    public static class ExtB\n    {\n        public static void Bump(this ref Other c) { c.N--; }\n    }\n\n    public class A\n    {\n        public void Run()\n        {\n            Counter c = default;\n            c.Bump();\n        }\n    }\n}\n'},
     [('A.Run', 'calls', 'ExtA.Bump')], [('A.Run', 'calls', 'ExtB.Bump')],
     source='csharpstandard §16.4, §15.6.10', since='7.2')
case('csharp', 'csharp-interface-static-abstract-through-type-parameter', 'T.Create() with T : IMake binds to IMake.Create, not Maker.Create or Other.Create',
     CS, {'A.cs': 'namespace App {\n  public interface IMake { static abstract int Create(); }\n  public class Maker : IMake { public static int Create() { return 1; } }\n  public class Other { public static int Create() { return 2; } }\n  public static class Use { public static int Run<T>() where T : IMake { return T.Create(); } }\n}\n'},
     [('Use.Run', 'calls', 'IMake.Create')], [('Use.Run', 'calls', 'Maker.Create'), ('Use.Run', 'calls', 'Other.Create')],
     source='csharpstandard §19.4 (static abstract interface members); Roslyn Binder_Invocation.cs type-parameter member lookup', since='11')
case('csharp', 'csharp-interface-default-method-via-interface', 'g.Hi() on an IGreet-typed local binds to IGreet.Hi (the default body), not to Plain2.Hi',
     CS, {'A.cs': 'namespace App {\n  public interface IGreet { string Hi() { return "hi"; } }\n  public class Plain : IGreet { }\n  public class Plain2 { public string Hi() { return "x"; } }\n  public static class Use2 { public static string Run(Plain p) { IGreet g = p; return g.Hi(); } }\n}\n'},
     [('Use2.Run', 'calls', 'IGreet.Hi')], [('Use2.Run', 'calls', 'Plain2.Hi')],
     source='csharpstandard §19.4 (default interface methods; a class does not inherit them)', since='8')
case('csharp', 'csharp-interface-two-closed-generic-implementations', "b.Get() on IBox<int> binds to the interface member IBox.Get, not to the class's explicit implementations",
     CS, {'A.cs': 'namespace App {\n  public interface IBox<T> { T Get(); }\n  public class Two : IBox<int>, IBox<string> {\n    int IBox<int>.Get() { return Helper.A(); }\n    string IBox<string>.Get() { return Helper.B(); }\n  }\n  public static class Helper { public static int A() { return 1; } public static string B() { return ""; } }\n  public static class Use3 { public static int Run(Two t) { IBox<int> b = t; return b.Get(); } }\n}\n'},
     [('Use3.Run', 'calls', 'IBox.Get')], [('Use3.Run', 'calls', 'Two.Get')],
     source='csharpstandard §19.6 (explicit interface member implementations of different constructed interfaces)')
case('csharp', 'invoked-name-skips-non-invocable', "when a name is invoked, a member of the derived type that is not invocable does not hide the base's method of that name",
     CS, {'A.cs': 'namespace App\n{\n    public class Base\n    {\n        public int Run() { return 1; }\n    }\n\n    public class Derived : Base\n    {\n        public int Run = 5;\n    }\n\n    public class C\n    {\n        public int M(Derived d)\n        {\n            return d.Run();\n        }\n    }\n}\n'},
     [('C.M', 'calls', 'Base.Run')], [('C.M', 'reads', 'Derived.Run')],
     source='csharpstandard §12.5.1 (next, if the member is invoked, all non-invocable members are removed from the set); Roslyn Binder_Lookup.cs LookupOptions.MustBeInvocableIfMember')
case('csharp', 'generic-method-by-type-arguments', 'a name written with K type arguments names only the methods with K type parameters',
     CS, {'A.cs': 'namespace App\n{\n    public class Maker\n    {\n        public int Make() { return 0; }\n\n        public T Make<T>() { return default(T); }\n    }\n\n    public class C\n    {\n        public int M(Maker m)\n        {\n            return m.Make<int>();\n        }\n\n        public int N(Maker m)\n        {\n            return m.Make();\n        }\n    }\n}\n'},
     [('C.M', 'calls', 'A.cs:App.Maker.Make@7'), ('C.N', 'calls', 'A.cs:App.Maker.Make@5')], [('C.M', 'calls', 'A.cs:App.Maker.Make@5'), ('C.N', 'calls', 'A.cs:App.Maker.Make@7')],
     source='csharpstandard §12.5.1 (if K is not zero, all members with a different number of type parameters are removed); Roslyn Binder_Lookup.cs CheckArity')
case('csharp', 'nested-type-sees-outer-static', "from a nested type a bare name is looked up in each enclosing type in turn, so an outer type's static member is in sight",
     CS, {'A.cs': 'namespace App\n{\n    public class Decoy\n    {\n        public static int Helper() { return 2; }\n    }\n\n    public class Outer\n    {\n        public static int Helper() { return 1; }\n\n        public class Inner\n        {\n            public int M()\n            {\n                return Helper();\n            }\n        }\n    }\n}\n'},
     [('Inner.M', 'calls', 'Outer.Helper')], [('Inner.M', 'calls', 'Decoy.Helper')],
     source='csharpstandard §12.8.4 (for each instance type T, starting with the instance type of the immediately enclosing type declaration and continuing with each enclosing class or struct declaration); Roslyn Binder_Expressions.cs BindIdentifier')
case('csharp', 'parenthesized-receiver', 'a parenthesized expression has the meaning of the expression inside',
     CS, {'A.cs': 'namespace App\n{\n    public class Part\n    {\n        public int Size() { return 1; }\n    }\n\n    public class Decoy\n    {\n        public int Size() { return 2; }\n    }\n\n    public class C\n    {\n        public int M(Part p)\n        {\n            return (p).Size();\n        }\n    }\n}\n'},
     [('C.M', 'calls', 'Part.Size')], [('C.M', 'calls', 'Decoy.Size')],
     source='csharpstandard §12.8.5 (the result of the parenthesized_expression is the result of the contained expression); Roslyn Binder_Expressions.cs BindParenthesizedExpression')
case('csharp', 'static-abstract-through-type-parameter', "`T.M()` with a type parameter constrained by an interface with a static abstract member names that interface's member",
     CS, {'A.cs': 'namespace App\n{\n    public interface IMaker\n    {\n        static abstract int Make();\n    }\n\n    public class Decoy\n    {\n        public static int Make() { return 2; }\n    }\n\n    public class C\n    {\n        public int M<T>() where T : IMaker\n        {\n            return T.Make();\n        }\n    }\n}\n'},
     [('C.M', 'calls', 'IMaker.Make')], [('C.M', 'calls', 'Decoy.Make')],
     source="Roslyn Binder_Expressions.cs BindMemberAccessWithBoundLeft (a member access on a type parameter looks up its constraints' static abstract members); csharpstandard §12.8.7.1", since='11')
case('csharp', 'null-conditional-dependent-access', 'after `a?.b` the dependent accesses `.c()` are members of the type of `a.b`',
     CS, {'A.cs': 'namespace App\n{\n    public class Engine\n    {\n        public void Start() { }\n    }\n\n    public class Decoy\n    {\n        public void Start() { }\n    }\n\n    public class Car\n    {\n        public Engine Engine { get; set; }\n    }\n\n    public class C\n    {\n        public void M(Car car)\n        {\n            car?.Engine.Start();\n        }\n    }\n}\n'},
     [('C.M', 'calls', 'Engine.Start'), ('C.M', 'reads', 'Car.Engine')], [('C.M', 'calls', 'Decoy.Start')],
     source='csharpstandard §12.8.8 (a null_conditional_member_access: primary_expression ?. identifier (dependent_access)*); Roslyn Binder_Expressions.cs BindConditionalAccessExpression')
case('csharp', 'indexer-by-argument', 'an indexer access picks, among the indexers of the type, the one applicable to the arguments',
     CS, {'A.cs': 'namespace App\n{\n    public class Table\n    {\n        public int this[int row] { get { return row; } }\n\n        public string this[string key] { get { return key; } }\n    }\n\n    public class C\n    {\n        public int M(Table t)\n        {\n            return t[1];\n        }\n\n        public string N(Table t)\n        {\n            return t["a"];\n        }\n    }\n}\n'},
     [('C.M', 'reads', 'A.cs:App.Table.this[]@5'), ('C.N', 'reads', 'A.cs:App.Table.this[]@7')], [('C.M', 'reads', 'A.cs:App.Table.this[]@7'), ('C.N', 'reads', 'A.cs:App.Table.this[]@5')],
     source='csharpstandard §12.8.12.4 (the best indexer of the set of candidate indexers is identified using overload resolution); Roslyn Binder_Expressions.cs BindIndexerOrIndexedPropertyAccess')
case('csharp', 'null-conditional-element-access', "`a?[i]` is an indexer access on a, and what follows it is a member of the indexer's type",
     CS, {'A.cs': 'namespace App\n{\n    public class Item\n    {\n        public int Weight() { return 1; }\n    }\n\n    public class Decoy\n    {\n        public int Weight() { return 2; }\n    }\n\n    public class Shelf\n    {\n        public Item this[int i] { get { return null; } }\n    }\n\n    public class C\n    {\n        public int M(Shelf s)\n        {\n            return s?[0].Weight() ?? 0;\n        }\n    }\n}\n'},
     [('C.M', 'calls', 'Item.Weight')], [('C.M', 'calls', 'Decoy.Weight')],
     source='csharpstandard §12.8.13 (null_conditional_element_access with dependent_access); Roslyn Binder_Expressions.cs BindConditionalAccessExpression')
case('csharp', 'base-indexer', '`base[i]` is an indexer access on the base class',
     CS, {'A.cs': 'namespace App\n{\n    public class Base\n    {\n        public virtual int this[int i] { get { return i; } }\n    }\n\n    public class Decoy\n    {\n        public int this[int i] { get { return 0; } }\n    }\n\n    public class Derived : Base\n    {\n        public override int this[int i] { get { return base[i] + 1; } }\n    }\n}\n'},
     [('Derived.this[]', 'reads', 'Base.this[]')], [('Derived.this[]', 'reads', 'Decoy.this[]')],
     source='csharpstandard §12.8.15 (base[E] is evaluated exactly as ((B)this)[E]); Roslyn Binder_Expressions.cs BindIndexerAccess')
case('csharp', 'object-initializer-targets', "an object initializer's member name is a member of the created type, written",
     CS, {'A.cs': 'namespace App\n{\n    public class Point\n    {\n        public int X { get; set; }\n    }\n\n    public class Other\n    {\n        public int X { get; set; }\n    }\n\n    public class C\n    {\n        public Point M()\n        {\n            return new Point { X = 1 };\n        }\n    }\n}\n'},
     [('C.M', 'writes', 'Point.X')], [('C.M', 'writes', 'Other.X')],
     source='csharpstandard §12.8.17.2.2 (an identifier shall name an accessible field or property of the object being initialized); Roslyn Binder_Expressions.cs BindObjectInitializerMember')
case('csharp', 'nested-object-initializer', "a nested object initializer's names are members of the type of the field or property it initializes",
     CS, {'A.cs': 'namespace App\n{\n    public class Inner\n    {\n        public int Size { get; set; }\n    }\n\n    public class Decoy\n    {\n        public int Size { get; set; }\n    }\n\n    public class Outer\n    {\n        public Inner Part { get; } = new Inner();\n    }\n\n    public class C\n    {\n        public Outer M()\n        {\n            return new Outer { Part = { Size = 3 } };\n        }\n    }\n}\n'},
     [('C.M', 'writes', 'Inner.Size')], [('C.M', 'writes', 'Decoy.Size')],
     source='csharpstandard §12.8.17.2.2 (a nested object initializer: the assignments are treated as assignments to members of the field or property); Roslyn Binder_Expressions.cs BindObjectInitializerMember')
case('csharp', 'collection-initializer-add', 'each element of a collection initializer calls the Add method of the collection',
     CS, {'A.cs': 'using System.Collections;\n\nnamespace App\n{\n    public class Bag : IEnumerable\n    {\n        public void Add(int n) { }\n\n        public IEnumerator GetEnumerator() { return null; }\n    }\n\n    public class Other\n    {\n        public void Add(int n) { }\n    }\n\n    public class C\n    {\n        public Bag M()\n        {\n            return new Bag { 1, 2 };\n        }\n    }\n}\n'},
     [('C.M', 'calls', 'Bag.Add')], [('C.M', 'calls', 'Other.Add')],
     source='csharpstandard §12.8.17.2.3 (normal member lookup is applied to find a member named Add); Roslyn Binder_Expressions.cs BindCollectionInitializerElementAddMethod')
case('csharp', 'typeof-names-a-type', '`typeof(T)` names the type the type-name lookup finds',
     CS, {'A.cs': 'namespace Lib\n{\n    public class Widget { }\n}\n\nnamespace Far\n{\n    public class Widget { }\n}\n\nnamespace App\n{\n    using Lib;\n\n    public class C\n    {\n        public System.Type M()\n        {\n            return typeof(Widget);\n        }\n    }\n}\n'},
     [('C.M', 'references', 'Lib.Widget')], [('C.M', 'references', 'Far.Widget')],
     source='csharpstandard §12.8.18 (typeof_expression: a type); Roslyn Binder_Expressions.cs BindTypeOf')
case('csharp', 'default-expression-names-a-type', '`default(T)` names the type the type-name lookup finds',
     CS, {'A.cs': 'namespace Lib\n{\n    public struct Slot { }\n}\n\nnamespace Far\n{\n    public struct Slot { }\n}\n\nnamespace App\n{\n    using Lib;\n\n    public class C\n    {\n        public Slot M()\n        {\n            return default(Slot);\n        }\n    }\n}\n'},
     [('C.M', 'references', 'Lib.Slot')], [('C.M', 'references', 'Far.Slot')],
     source="csharpstandard §12.8.21 (explicitly_typed_default: 'default' '(' type ')'); Roslyn Binder_Expressions.cs BindDefaultExpression")
case('csharp', 'nameof-names-its-target', '`nameof(T.M)` binds its named entity by the rules of simple names and member access',
     CS, {'A.cs': 'namespace App\n{\n    public class Order\n    {\n        public int Total { get; set; }\n    }\n\n    public class Invoice\n    {\n        public int Total { get; set; }\n    }\n\n    public class C\n    {\n        public string M()\n        {\n            return nameof(Order.Total);\n        }\n    }\n}\n'},
     [('C.M', 'references', 'Order.Total')], [('C.M', 'references', 'Invoice.Total')],
     source='csharpstandard §12.8.23 (simple name and member access lookups are performed on the named_entity at compile time); Roslyn Binder_Expressions.cs BindNameofOperatorInternal')
case('csharp', 'increment-calls-operator', "`v++` calls the user-defined operator ++ of the operand's type",
     CS, {'A.cs': 'namespace App\n{\n    public class Counter\n    {\n        public static Counter operator ++(Counter c) { return c; }\n    }\n\n    public class Other\n    {\n        public static Other operator ++(Other c) { return c; }\n    }\n\n    public class C\n    {\n        public Counter M(Counter c)\n        {\n            c++;\n            return c;\n        }\n    }\n}\n'},
     [('C.M', 'calls', 'Counter.operator++')], [('C.M', 'calls', 'Other.operator++')],
     source='csharpstandard §12.9.7 / §12.8.16 (unary operator overload resolution selects the operator implementation); Roslyn Binder_Operators.cs BindIncrementOperator')
case('csharp', 'await-custom-awaitable', "`await t` is `t.GetAwaiter().GetResult()`: a member of the awaited value is a member of GetResult's type",
     CS, {'A.cs': 'using System;\nusing System.Runtime.CompilerServices;\n\nnamespace App\n{\n    public class Reply\n    {\n        public int Code() { return 1; }\n    }\n\n    public class Decoy\n    {\n        public int Code() { return 2; }\n    }\n\n    public class Waiter : INotifyCompletion\n    {\n        public bool IsCompleted { get { return true; } }\n\n        public Reply GetResult() { return null; }\n\n        public void OnCompleted(Action continuation) { }\n    }\n\n    public class Call\n    {\n        public Waiter GetAwaiter() { return null; }\n    }\n\n    public class C\n    {\n        public async System.Threading.Tasks.Task<int> M(Call c)\n        {\n            var r = await c;\n            return r.Code();\n        }\n    }\n}\n'},
     [('C.M', 'calls', 'Reply.Code')], [('C.M', 'calls', 'Decoy.Code')],
     source='csharpstandard §12.9.9.3 (await t is classified the same way as (t).GetAwaiter().GetResult()); Roslyn Binder_Await.cs BindAwait')
case('csharp', 'switch-expression-type-pattern', "a switch expression arm's declaration pattern binds a variable of the type it names",
     CS, {'A.cs': 'namespace App\n{\n    public class Shape { }\n\n    public class Circle : Shape\n    {\n        public int Radius() { return 1; }\n    }\n\n    public class Decoy\n    {\n        public int Radius() { return 2; }\n    }\n\n    public class C\n    {\n        public int M(Shape s)\n        {\n            return s switch\n            {\n                Circle c => c.Radius(),\n                _ => 0\n            };\n        }\n    }\n}\n'},
     [('C.M', 'calls', 'Circle.Radius')], [('C.M', 'calls', 'Decoy.Radius')],
     source='csharpstandard §12.11 (switch_expression_arm: pattern case_guard? => expression); Roslyn Binder_Patterns.cs BindDeclarationPattern')
case('csharp', 'operator-by-operand-types', 'among the operators of one name, the one whose parameters fit the operand types is called',
     CS, {'A.cs': 'namespace App\n{\n    public class Money\n    {\n        public static Money operator +(Money a, Money b) { return a; }\n\n        public static Money operator +(Money a, int b) { return a; }\n    }\n\n    public class C\n    {\n        public Money M(Money a, int n)\n        {\n            return a + n;\n        }\n    }\n}\n'},
     [('C.M', 'calls', 'A.cs:App.Money.operator+@7')], [('C.M', 'calls', 'A.cs:App.Money.operator+@5')],
     source='csharpstandard §12.12.5 (binary operator overload resolution); Roslyn Binder_Operators.cs BinaryOperatorOverloadResolution')
case('csharp', 'unary-operator', "`-a` calls the user-defined unary operator of its operand's type",
     CS, {'A.cs': 'namespace App\n{\n    public class Vec\n    {\n        public static Vec operator -(Vec a) { return a; }\n\n        public static Vec operator -(Vec a, Vec b) { return a; }\n    }\n\n    public class C\n    {\n        public Vec M(Vec a)\n        {\n            return -a;\n        }\n    }\n}\n'},
     [('C.M', 'calls', 'A.cs:App.Vec.operator-@5')], [('C.M', 'calls', 'A.cs:App.Vec.operator-@7')],
     source='csharpstandard §12.9.3 / §12.4.4 (unary operator overload resolution); Roslyn Binder_Operators.cs BindUnaryOperator')
case('csharp', 'coalesce-type', '`a ?? b` has the type of a (its underlying type when nullable), so a member of it is a member of that type',
     CS, {'A.cs': 'namespace App\n{\n    public class Part\n    {\n        public int Size() { return 1; }\n    }\n\n    public class Decoy\n    {\n        public int Size() { return 2; }\n    }\n\n    public class C\n    {\n        public int M(Part a, Part b)\n        {\n            var p = a ?? b;\n            return p.Size();\n        }\n    }\n}\n'},
     [('C.M', 'calls', 'Part.Size')], [('C.M', 'calls', 'Decoy.Size')],
     source='csharpstandard §12.17 (the type of a ?? b is A0, A or B); Roslyn Binder_Operators.cs BindNullCoalescingOperator')
case('csharp', 'out-variable-declaration', 'an `out var` declared in an argument list has the declared type of the parameter',
     CS, {'A.cs': 'namespace App\n{\n    public class Part\n    {\n        public int Size() { return 1; }\n    }\n\n    public class Decoy\n    {\n        public int Size() { return 2; }\n    }\n\n    public class Store\n    {\n        public bool TryGet(out Part p) { p = null; return true; }\n    }\n\n    public class C\n    {\n        public int M(Store s)\n        {\n            s.TryGet(out var p);\n            return p.Size();\n        }\n    }\n}\n'},
     [('C.M', 'calls', 'Part.Size')], [('C.M', 'calls', 'Decoy.Size')],
     source='csharpstandard §12.19 (in an argument_list the inferred type of the variable is the declared type of the corresponding parameter); Roslyn Binder_Expressions.cs BindDeclarationExpression')
case('csharp', 'deconstruction-variable-types', 'a variable declared by `var (a, b) = ...` has the type of its tuple element',
     CS, {'A.cs': 'namespace App\n{\n    public class Part\n    {\n        public int Size() { return 1; }\n    }\n\n    public class Decoy\n    {\n        public int Size() { return 2; }\n    }\n\n    public class C\n    {\n        public int M(Part p)\n        {\n            var (a, b) = (p, 1);\n            return a.Size() + b;\n        }\n    }\n}\n'},
     [('C.M', 'calls', 'Part.Size')], [('C.M', 'calls', 'Decoy.Size')],
     source='csharpstandard §12.19 (in a deconstructor the inferred type of the variable is the type of the corresponding tuple element); Roslyn Binder_Deconstruct.cs')
case('csharp', 'conditional-type', '`c ? x : y` has the type of its arms, so a member of it is a member of that type',
     CS, {'A.cs': 'namespace App\n{\n    public class Part\n    {\n        public int Size() { return 1; }\n    }\n\n    public class Decoy\n    {\n        public int Size() { return 2; }\n    }\n\n    public class C\n    {\n        public int M(bool c, Part a, Part b)\n        {\n            var p = c ? a : b;\n            return p.Size();\n        }\n    }\n}\n'},
     [('C.M', 'calls', 'Part.Size')], [('C.M', 'calls', 'Decoy.Size')],
     source='csharpstandard §12.20 (the second and third operands control the type of the conditional expression); Roslyn Binder_Operators.cs BindConditionalOperator')
case('csharp', 'simple-assignment-writes-member', '`x.P = v` assigns the property or field the member lookup finds',
     CS, {'A.cs': 'namespace App\n{\n    public class Box\n    {\n        public int Width { get; set; }\n    }\n\n    public class Decoy\n    {\n        public int Width { get; set; }\n    }\n\n    public class C\n    {\n        public void M(Box b)\n        {\n            b.Width = 3;\n            b.Width += 1;\n        }\n    }\n}\n'},
     [('C.M', 'writes', 'Box.Width')], [('C.M', 'writes', 'Decoy.Width')],
     source='csharpstandard §12.23.2 / §12.23.5 (the property or indexer element given by the left operand); Roslyn Binder_Operators.cs BindAssignment')
case('csharp', 'compound-assignment-uses-operator', '`a += b` is `a = a + b`: it calls the user-defined operator +',
     CS, {'A.cs': 'namespace App\n{\n    public class Vec\n    {\n        public static Vec operator +(Vec a, Vec b) { return a; }\n    }\n\n    public class Other\n    {\n        public static Other operator +(Other a, Other b) { return a; }\n    }\n\n    public class C\n    {\n        public Vec M(Vec a, Vec b)\n        {\n            a += b;\n            return a;\n        }\n    }\n}\n'},
     [('C.M', 'calls', 'Vec.operator+')], [('C.M', 'calls', 'Other.operator+')],
     source='csharpstandard §12.23.5 (x op= y is processed by applying binary operator overload resolution as if written x op y); Roslyn Binder_Operators.cs BindCompoundAssignment')
case('csharp', 'event-assignment', '`e += h` adds to the event the lookup finds and `h` is the method its group names',
     CS, {'A.cs': 'using System;\n\nnamespace App\n{\n    public class Button\n    {\n        public event Action Click;\n    }\n\n    public class Decoy\n    {\n        public event Action Click;\n    }\n\n    public class C\n    {\n        public void OnClick() { }\n\n        public void M(Button b)\n        {\n            b.Click += OnClick;\n        }\n    }\n}\n'},
     [('C.M', 'references', 'C.OnClick')], [('C.M', 'references', 'Decoy')],
     source='csharpstandard §12.23.6 (event assignment: += with an event access as the left operand); Roslyn Binder_Operators.cs BindEventAssignment')
case('csharp', 'deconstruction-calls-deconstruct', "a deconstructing assignment calls the Deconstruct method of the right side's type",
     CS, {'A.cs': 'namespace App\n{\n    public class Point\n    {\n        public void Deconstruct(out int x, out int y) { x = 0; y = 0; }\n    }\n\n    public class Other\n    {\n        public void Deconstruct(out int x, out int y) { x = 1; y = 1; }\n    }\n\n    public class C\n    {\n        public int M(Point p)\n        {\n            var (a, b) = p;\n            return a + b;\n        }\n    }\n}\n'},
     [('C.M', 'calls', 'Point.Deconstruct')], [('C.M', 'calls', 'Other.Deconstruct')],
     source='csharpstandard §12.7 / §12.23.3 (the right operand is deconstructed); Roslyn Binder_Deconstruct.cs MakeDeconstructInvocationExpression')
case('csharp', 'deconstruct-overload-by-count', 'the Deconstruct overload is the one with as many out parameters as the left side has variables',
     CS, {'A.cs': 'namespace App\n{\n    public class Point\n    {\n        public void Deconstruct(out int x, out int y) { x = 0; y = 0; }\n\n        public void Deconstruct(out int x, out int y, out int z) { x = 0; y = 0; z = 0; }\n    }\n\n    public class C\n    {\n        public int M(Point p)\n        {\n            var (a, b, c) = p;\n            return a + b + c;\n        }\n    }\n}\n'},
     [('C.M', 'calls', 'A.cs:App.Point.Deconstruct@7')], [('C.M', 'calls', 'A.cs:App.Point.Deconstruct@5')],
     source='csharpstandard §12.23.3.1 (it is a compile-time error if m != n); Roslyn Binder_Deconstruct.cs MakeDeconstructInvocationExpression')
case('csharp', 'deconstruct-extension', 'a type with no Deconstruct member is deconstructed by an extension method',
     CS, {'A.cs': 'namespace App\n{\n    public class Point\n    {\n    }\n\n    public static class PointExtensions\n    {\n        public static void Deconstruct(this Point p, out int x, out int y) { x = 0; y = 0; }\n    }\n\n    public class C\n    {\n        public int M(Point p)\n        {\n            var (a, b) = p;\n            return a + b;\n        }\n    }\n}\n'},
     [('C.M', 'calls', 'PointExtensions.Deconstruct')], [('C.M', 'calls', 'Point.Deconstruct')],
     source='csharpstandard §12.7 (deconstruction of a value of a type with an instance or extension Deconstruct); Roslyn Binder_Deconstruct.cs MakeDeconstructInvocationExpression')
case('csharp', 'with-expression-members', 'a member named in a `with` initializer is a member of the type of the copied value, and the result has that type',
     CS, {'A.cs': 'namespace App\n{\n    public record Person\n    {\n        public int Age { get; init; }\n\n        public int Years() { return Age; }\n    }\n\n    public record Decoy\n    {\n        public int Age { get; init; }\n\n        public int Years() { return 0; }\n    }\n\n    public class C\n    {\n        public int M(Person p)\n        {\n            return (p with { Age = 3 }).Years();\n        }\n    }\n}\n'},
     [('C.M', 'writes', 'Person.Age'), ('C.M', 'calls', 'Person.Years')], [('C.M', 'writes', 'Decoy.Age'), ('C.M', 'calls', 'Decoy.Years')],
     source="Roslyn Binder_WithExpression.cs BindWithExpression (the initializer is bound as an object initializer of the receiver's type; the result has the receiver's type)", since='9')
case('csharp', 'lambda-parameter-shadows-enclosing-local', 'a lambda parameter may shadow a local of the enclosing method: inside the lambda the name is the parameter, typed as written',
     CS, {'A.cs': 'using System;\nnamespace App\n{\n    public class Foo { public void Run() { } }\n    public class Other { public void Run() { } }\n    public class U\n    {\n        public void Go()\n        {\n            Other n = new Other();\n            Action<Foo> a = (Foo n) => n.Run();\n        }\n    }\n}\n'},
     [('U.Go', 'calls', 'Foo.Run')], [('U.Go', 'calls', 'Other.Run')],
     source='csharpstandard §12.8.4 and §7.7.1 (name hiding in nested functions, C# 8); Roslyn Binder.BindIdentifier / WithLambdaParametersBinder', since='8')
case('csharp', 'qualified-alias-member-access', "alias::Type.Member: the alias names a namespace, so Type is that namespace's, not another namespace's type of the same name",
     CS, {'Lib.cs': 'namespace App.Lib { public static class Util { public static void F() { } } }\n', 'Other.cs': 'namespace App.Other { public static class Util { public static void F() { } } }\n', 'U.cs': 'namespace App\n{\n    using L = App.Lib;\n    public class U { public void Go() { L::Util.F(); } }\n}\n'},
     [('U.Go', 'calls', 'Lib.cs:Util.F')], [('U.Go', 'calls', 'Other.cs:Util.F')],
     source='csharpstandard §12.8.7.1 (qualified_alias_member) and §14.8; Roslyn BindMemberAccess / BindAliasQualifier')
case('csharp', 'await-extension-get-awaiter', 'await of a value whose GetAwaiter is an extension method: the call is a use of the extension and the result has the type GetResult returns',
     CS, {'A.cs': 'using System;\nusing System.Runtime.CompilerServices;\nnamespace App\n{\n    public class Num { public void Show() { } }\n    public class Other { public void Show() { } }\n    public class Waiter : INotifyCompletion\n    {\n        public bool IsCompleted { get { return true; } }\n        public Num GetResult() { return new Num(); }\n        public void OnCompleted(Action a) { }\n    }\n    public static class Ext { public static Waiter GetAwaiter(this int n) { return new Waiter(); } }\n    public class U\n    {\n        public async void Go() { var r = await 3; r.Show(); }\n    }\n}\n'},
     [('U.Go', 'calls', 'Ext.GetAwaiter'), ('U.Go', 'calls', 'Num.Show')], [('U.Go', 'calls', 'Other.Show')],
     source='csharpstandard §12.9.9.2 (GetAwaiter instance or extension method); Roslyn Binder_Await.cs BindAwait / GetAwaitableExpressionInfo')
case('csharp', 'cast-calls-explicit-conversion', 'a cast (T)s with a user-defined explicit conversion operator in T calls that operator, not that of another type',
     CS, {'A.cs': 'namespace App\n{\n    public class Src { }\n    public class Dst { public static explicit operator Dst(Src s) { return new Dst(); } }\n    public class Dst2 { public static explicit operator Dst2(Src s) { return new Dst2(); } }\n    public class U\n    {\n        public Dst Go(Src s) { return (Dst)s; }\n    }\n}\n'},
     [('U.Go', 'calls', 'Dst.operator Dst')], [('U.Go', 'calls', 'Dst2.operator Dst2')],
     source='csharpstandard §12.9.8 and §10.5.5 (user-defined explicit conversions); Roslyn BindCast / Conversions.ClassifyConversionFromExpression (UserDefined)')
case('csharp', 'lifted-user-operator', "a + b on Money? operands calls Money's operator+ (lifted), not another struct's",
     CS, {'A.cs': 'namespace App\n{\n    public struct Money { public static Money operator +(Money a, Money b) { return a; } }\n    public struct Other { public static Other operator +(Other a, Other b) { return a; } }\n    public class U\n    {\n        public Money? Go(Money? a, Money? b) { return a + b; }\n    }\n}\n'},
     [('U.Go', 'calls', 'Money.operator+')], [('U.Go', 'calls', 'Other.operator+')],
     source='csharpstandard §12.4.8 / §12.12.5 (lifted operators); Roslyn Binder_Operators.cs BindSimpleBinaryOperator / OperatorOverloadResolution (lifted)')
case('csharp', 'object-initializer-indexer', 'an indexer initializer `new Bag { ["x"] = 1 }` names the indexer of the created type',
     CS, {'A.cs': 'namespace App {\n  public class Bag { public int this[string k] { get { return 0; } set { } } }\n  public class Other { public int this[string k] { get { return 0; } set { } } }\n  public class Use { public Bag Make() { return new Bag { ["x"] = 1 }; } }\n}\n'},
     [('Use.Make', 'references', 'Bag.this[]')], [('Use.Make', 'references', 'Other.this[]')],
     source='csharpstandard §12.8.17 (object initializers)')
case('csharp', 'operator-of-base-class', "an operator is found in the base class of the operand's type",
     CS, {'A.cs': 'namespace App {\n  public class B { public static B operator +(B x, B y) { return x; } }\n  public class D : B { }\n  public class Use { public void Go(D a, D b) { var c = a + b; } }\n}\n'},
     [('Use.Go', 'calls', 'B.operator+')], [('Use.Go', 'calls', 'D.D')],
     source='csharpstandard §12.12.5 / Binder_Operators.cs GetUserDefinedOperators')
case('csharp', 'conditional-logical-operator', '`a && b` on a type with operator true/false and & calls operator false and operator &, not operator |',
     CS, {'A.cs': 'namespace App {\n  public class F {\n    public static F operator &(F x, F y) { return x; }\n    public static F operator |(F x, F y) { return y; }\n    public static bool operator true(F x) { return true; }\n    public static bool operator false(F x) { return false; }\n  }\n  public class Use { public void Go(F a, F b) { var c = a && b; } }\n}\n'},
     [('Use.Go', 'calls', 'F.operator&')], [('Use.Go', 'calls', 'F.operator|')],
     source='csharpstandard §12.14.? user-defined conditional logical operators; Binder_Operators.cs BindUserDefinedConditionalLogicalOperator')
case('csharp', 'deconstruction-declaration', '`var (a, b) = p;` calls Deconstruct of the type of p',
     CS, {'A.cs': 'namespace App {\n  public class P { public int X; public int Y;\n    public void Deconstruct(out int x, out int y) { x = X; y = Y; }\n  }\n  public class Q { public void Deconstruct(out int x, out int y) { x = 0; y = 0; } }\n  public class Use { public void Go(P p) { var (a, b) = p; } }\n}\n'},
     [('Use.Go', 'calls', 'P.Deconstruct')], [('Use.Go', 'calls', 'Q.Deconstruct')],
     source='Binder_Deconstruct.cs')
case('csharp', 'cs-operator-true-condition', 'if (f) with a struct declaring operator true calls it',
     CS, {'A.cs': 'namespace App {\n  public struct Flag { public static bool operator true(Flag f) => true; public static bool operator false(Flag f) => false; }\n  public class Decoy { public static bool Other(Flag f) => false; }\n  public class Use { public int Run(Flag f) { if (f) { return 1; } return 0; } }\n}\n'},
     [('Use.Run', 'calls', 'Flag.operator true')], [('Use.Run', 'calls', 'Decoy.Other')],
     source='csharpstandard §12.20 / §12.24 (operator true); Binder_Operators BindConditionalOperator')
case('csharp', 'cs-operator-not-equals', 'a != b calls operator != of the operand type',
     CS, {'A.cs': 'namespace App {\n  public class V { public static bool operator ==(V a, V b) => true; public static bool operator !=(V a, V b) => false;\n    public override bool Equals(object o) => true; public override int GetHashCode() => 0; }\n  public class Use { public bool Run(V a, V b) { return a != b; } }\n}\n'},
     [('Use.Run', 'calls', 'V.operator!=')], [('Use.Run', 'calls', 'V.operator==')],
     source='csharpstandard §12.12.8; Binder_Operators BindBinaryOperator')
case('csharp', 'cs-operator-shift', 'b << 2 calls operator << not >>',
     CS, {'A.cs': 'namespace App {\n  public struct B { public static B operator <<(B a, int n) => a; public static B operator >>(B a, int n) => a; }\n  public class Use { public B Run(B b) { return b << 2; } }\n}\n'},
     [('Use.Run', 'calls', 'B.operator<<')], [('Use.Run', 'calls', 'B.operator>>')],
     source='csharpstandard §12.11 shift operators; Binder_Operators')
case('csharp', 'cs-checked-operator', 'checked(a + b) calls operator checked +, unchecked(a + b) the plain operator +',
     CS, {'A.cs': 'namespace App {\n  public struct M { public static M operator +(M a, M b) => a; public static M operator checked +(M a, M b) => a; }\n  public class Use { public M Plain(M a, M b) { return unchecked(a + b); } public M Chk(M a, M b) { return checked(a + b); } }\n}\n'},
     [('Use.Plain', 'calls', 'M.operator+'), ('Use.Chk', 'calls', 'M.operator checked +')], [('Use.Chk', 'calls', 'M.operator+'), ('Use.Plain', 'calls', 'M.operator checked +')],
     source='csharpstandard §12.8.20 / §12.12; Binder_Operators (checked operators)', since='11')
case('csharp', 'cs-static-abstract-operator-typeparam', 'a + b on T : IAdd<T> calls IAdd.operator +, not the implementation',
     CS, {'A.cs': 'namespace App {\n  public interface IAdd<T> where T : IAdd<T> { static abstract T operator +(T a, T b); }\n  public struct N : IAdd<N> { public static N operator +(N a, N b) => a; }\n  public class Use { public T Sum<T>(T a, T b) where T : IAdd<T> { return a + b; } }\n}\n'},
     [('Use.Sum', 'calls', 'IAdd.operator+')], [('Use.Sum', 'calls', 'N.operator+')],
     source='csharpstandard §12.12.3; Binder_Operators (static abstract constraint lookup)', since='11')
case('csharp', 'cs-null-conditional-nullable-struct', 's?.Value() / s?.P with S? s are members of S',
     CS, {'A.cs': 'namespace App {\n  public struct S { public int Value() => 1; public int P => 2; }\n  public class Decoy { public int Value() => 3; public int P => 4; }\n  public class Use { public int? Run(S? s) { return s?.Value(); } public int? Run2(S? s) { return s?.P; } }\n}\n'},
     [('Use.Run', 'calls', 'S.Value'), ('Use.Run2', 'reads', 'S.P')], [('Use.Run', 'calls', 'Decoy.Value'), ('Use.Run2', 'reads', 'Decoy.P')],
     source='csharpstandard §12.8.8; Binder_Expressions BindConditionalAccessExpression')
case('csharp', 'cs-implicit-index-range-support', 'b[^1] reads Bag.Count and the int indexer; b[1..] calls Bag.Slice',
     CS, {'A.cs': 'namespace App {\n  public class Bag { public int Count => 3; public int this[int i] => i; public Bag Slice(int s, int l) => this; }\n  public class Decoy { public int Count => 1; public Decoy Slice(int s, int l) => null; }\n  public class Use { public int Run(Bag b) { return b[^1]; } public Bag Run2(Bag b) { return b[1..]; } }\n}\n'},
     [('Use.Run', 'reads', 'Bag.Count'), ('Use.Run2', 'calls', 'Bag.Slice')], [('Use.Run', 'reads', 'Decoy.Count'), ('Use.Run2', 'calls', 'Decoy.Slice')],
     source='csharpstandard §12.8.12 / ranges (implicit index support); Binder_Expressions BindImplicitIndexerAccess')
case('csharp', 'cs-property-pattern-member', 'p switch { { X: 0 } => ... } reads Pt.X',
     CS, {'A.cs': 'namespace App {\n  public class Pt { public int X { get; set; } public int Y { get; set; } }\n  public class Decoy { public int X { get; set; } }\n  public class Use { public int Run(Pt p) { return p switch { { X: 0 } => 1, _ => 2 }; } }\n}\n'},
     [('Use.Run', 'reads', 'Pt.X')], [('Use.Run', 'reads', 'Decoy.X'), ('Use.Run', 'reads', 'Pt.Y')],
     source='csharpstandard §11.2.5 property pattern, used in §12.11; Binder_Patterns via Binder_Expressions')
case('csharp', 'cs-foreach-deconstruct', "the deconstruction variable of foreach calls the element type's Deconstruct",
     CS, {'A.cs': 'using System.Collections.Generic;\nnamespace App {\n  public class Pair { public void Deconstruct(out int a, out int b) { a = 1; b = 2; } }\n  public class Decoy { public void Deconstruct(out int a, out int b) { a = 1; b = 2; } }\n  public class Use { public void Run(List<Pair> xs) { foreach (var (a, b) in xs) { } } }\n}\n'},
     [('Use.Run', 'calls', 'Pair.Deconstruct')], [('Use.Run', 'calls', 'Decoy.Deconstruct')],
     source='Binder_Deconstruct.cs BindDeconstruction (foreach variable)')
case('csharp', 'operator-use-is-a-call', "`a + b` with a user-defined operator is a call of that operator declaration of the operand's type, not another type's",
     CS, {'A.cs': 'using System;using System.Collections.Generic;using System.Linq;\nnamespace App\n{\n public struct Money { public int V; public static Money operator +(Money a, Money b) { return a; } }\n public struct Other { public static Other operator +(Other a, Other b) { return a; } }\n public class Use\n {\n     public Money Run(Money a, Money b) { return a + b; }\n     public Other Run2(Other a, Other b) { return a + b; }\n }\n}\n'},
     [('Use.Run', 'calls', 'Money.operator+'), ('Use.Run2', 'calls', 'Other.operator+')], [('Use.Run', 'calls', 'Other.operator+'), ('Use.Run2', 'calls', 'Money.operator+')],
     source='csharpstandard expressions.md §12.4.3, §12.4.5; Roslyn Binder_Operators.cs')
case('csharp', 'operator-compound-assignment', "`a += b` is an overload use of operator `+` of a's type",
     CS, {'A.cs': 'using System;using System.Collections.Generic;using System.Linq;\nnamespace App\n{\n public struct Money { public int V; public static Money operator +(Money a, Money b) { return a; } }\n public struct Other { public static Other operator +(Other a, Other b) { return a; } }\n public class Use\n {\n     public void Run(Money a, Money b) { a += b; }\n }\n}\n'},
     [('Use.Run', 'calls', 'Money.operator+')], [('Use.Run', 'calls', 'Other.operator+')],
     source='csharpstandard expressions.md §12.4.3 (an overload of operator * is also an overload of operator *=)')
case('csharp', 'operator-unary', "`-x` is a call of the unary operator of x's type",
     CS, {'A.cs': 'using System;using System.Collections.Generic;using System.Linq;\nnamespace App\n{\n public struct Temp { public int V; public static Temp operator -(Temp a) { return a; } }\n public struct Other { public static Other operator -(Other a) { return a; } }\n public class Use\n {\n     public Temp Run(Temp a) { return -a; }\n }\n}\n'},
     [('Use.Run', 'calls', 'Temp.operator-')], [('Use.Run', 'calls', 'Other.operator-')],
     source='csharpstandard expressions.md §12.4.4 (candidates provided by X)')
case('csharp', 'operator-from-the-second-operand', 'a binary operator is also looked for in the type of the right operand',
     CS, {'A.cs': 'using System;using System.Collections.Generic;using System.Linq;\nnamespace App\n{\n public struct Money { public int V; public static Money operator +(int n, Money m) { return m; } }\n public struct Other { public static Other operator +(int n, Other m) { return m; } }\n public class Use\n {\n     public Money Run(Money m) { return 1 + m; }\n }\n}\n'},
     [('Use.Run', 'calls', 'Money.operator+')], [('Use.Run', 'calls', 'Other.operator+')],
     source='csharpstandard expressions.md §12.4.5 (the union of the candidate operators provided by X and by Y)')
case('csharp', 'operator-of-a-base-class', 'an operator declared in a base class is a candidate for the derived type',
     CS, {'A.cs': 'using System;using System.Collections.Generic;using System.Linq;\nnamespace App\n{\n public class Base { public static Base operator +(Base a, Base b) { return a; } }\n public class Derived : Base { }\n public class Other { public static Other operator +(Other a, Other b) { return a; } }\n public class Use\n {\n     public Base Run(Derived a, Derived b) { return a + b; }\n }\n}\n'},
     [('Use.Run', 'calls', 'Base.operator+')], [('Use.Run', 'calls', 'Other.operator+')],
     source='csharpstandard expressions.md §12.4.6 (otherwise the candidates provided by the direct base class of T0)')
case('csharp', 'operator-of-the-derived-type-first', 'if an operator of T is applicable, those of its base classes are not candidates',
     CS, {'A.cs': 'using System;using System.Collections.Generic;using System.Linq;\nnamespace App\n{\n public class Base { public static Base operator +(Base a, Base b) { return a; } }\n public class Derived : Base { public static Derived operator +(Derived a, Derived b) { return a; } }\n public class Use\n {\n     public Derived Run(Derived a, Derived b) { return a + b; }\n     public Base Run2(Base a, Derived b) { return a + b; }\n }\n}\n'},
     [('Use.Run', 'calls', 'Derived.operator+'), ('Use.Run2', 'calls', 'Base.operator+')], [('Use.Run', 'calls', 'Base.operator+'), ('Use.Run2', 'calls', 'Derived.operator+')],
     source='csharpstandard expressions.md §12.4.6 (if at least one operator in T0 is applicable, the candidates are those of T0)')
case('csharp', 'operator-equality', "`a == b` and `a != b` are calls of the type's own `==` and `!=`",
     CS, {'A.cs': 'using System;using System.Collections.Generic;using System.Linq;\nnamespace App\n{\n public struct Money { public int V; public static bool operator ==(Money a, Money b) { return true; } public static bool operator !=(Money a, Money b) { return false; } public override bool Equals(object o) { return true; } public override int GetHashCode() { return 0; } }\n public struct Other { public static bool operator ==(Other a, Other b) { return true; } public static bool operator !=(Other a, Other b) { return false; } public override bool Equals(object o) { return true; } public override int GetHashCode() { return 0; } }\n public class Use\n {\n     public bool Run(Money a, Money b) { return a == b; }\n     public bool Run2(Money a, Money b) { return a != b; }\n }\n}\n'},
     [('Use.Run', 'calls', 'Money.operator=='), ('Use.Run2', 'calls', 'Money.operator!=')], [('Use.Run', 'calls', 'Other.operator=='), ('Use.Run2', 'calls', 'Other.operator!='), ('Use.Run', 'calls', 'Money.operator!=')],
     source='csharpstandard expressions.md §12.4.3 (overloadable binary operators), §12.4.5')
case('csharp', 'conversion-operator-cast', 'a cast `(T)x` is a use of the user-defined conversion operator that converts to T',
     CS, {'A.cs': 'using System;using System.Collections.Generic;using System.Linq;\nnamespace App\n{\n public struct Feet { public int V; }\n public struct Meters { public int V; public static explicit operator Meters(Feet f) { return new Meters(); } }\n public struct Yards { public static explicit operator Yards(Feet f) { return new Yards(); } }\n public class Use\n {\n     public Meters Run(Feet f) { return (Meters)f; }\n }\n}\n'},
     [('Use.Run', 'calls', 'Meters.operator Meters')], [('Use.Run', 'calls', 'Yards.operator Yards')],
     source='csharpstandard expressions.md §12.4.3 (cast operations are overloaded by user-defined conversions), conversions.md §10.5')
case('csharp', 'derived-applicable-method-hides-base', "a method of the most derived type that is applicable removes the base's methods from the candidates, even when a base method is the better match",
     CS, {'A.cs': 'using System;using System.Collections.Generic;using System.Linq;\nnamespace App\n{\n public class Base { public int G(int n) { return 1; } public int F(object o) { return 1; } }\n public class Derived : Base { public int G(object o) { return 2; } public int F(int n) { return 2; } }\n public class Use\n {\n     public int Run(Derived d) { return d.G(1); }\n     public int Run2(Derived d) { return ((Base)d).G(1); }\n     public int Run3(Derived d) { return d.F("s"); }\n     public int Run4(Derived d) { return d.F(1); }\n }\n}\n'},
     [('Use.Run', 'calls', 'Derived.G'), ('Use.Run2', 'calls', 'Base.G'), ('Use.Run3', 'calls', 'Base.F'), ('Use.Run4', 'calls', 'Derived.F')], [('Use.Run', 'calls', 'Base.G'), ('Use.Run2', 'calls', 'Derived.G'), ('Use.Run3', 'calls', 'Derived.F'), ('Use.Run4', 'calls', 'Base.F')],
     source='csharpstandard expressions.md §12.8.10.2 (the set of candidate methods is reduced to contain only methods from the most derived types); Roslyn OverloadResolution.cs RemoveLessDerivedMembers')
case('csharp', 'override-called-through-its-type', "a call through a receiver of the derived type is the override, and its result has the override's (covariant) return type",
     CS, {'A.cs': 'using System;using System.Collections.Generic;using System.Linq;\nnamespace App\n{\n public class Item { public void Go() { } }\n public class Base { public virtual Base Make() { return this; } public void Go() { } }\n public class Derived : Base { public override Derived Make() { return this; } public void Show() { } }\n public class Use\n {\n     public void Run(Derived d) { d.Make().Show(); }\n     public void Run2(Base b) { b.Make().Go(); }\n }\n}\n'},
     [('Use.Run', 'calls', 'Derived.Make'), ('Use.Run', 'calls', 'Derived.Show'), ('Use.Run2', 'calls', 'Base.Make'), ('Use.Run2', 'calls', 'Base.Go')], [('Use.Run', 'calls', 'Base.Make'), ('Use.Run2', 'calls', 'Derived.Make')],
     source='csharpstandard expressions.md §12.8.10.1 (the associated type is picked from the first declaration or override found starting with T); Roslyn OverloadResolution.cs RemoveLessDerivedMembers (member lookup keeps the overrides, so that the override is identified)', since='9')
case('csharp', 'extension-when-the-instance-method-is-inapplicable', 'an instance method of the name that does not take the arguments does not stop the search: the extension that does is called',
     CS, {'A.cs': 'using System;using System.Collections.Generic;using System.Linq;\nnamespace App\n{\n public class Repo { public int Find(int id) { return id; } }\n public static class RepoExt { public static int Find(this Repo r, string name) { return 0; } }\n public class Use\n {\n     public int Run(Repo r) { return r.Find("a"); }\n     public int Run2(Repo r) { return r.Find(1); }\n }\n}\n'},
     [('Use.Run', 'calls', 'RepoExt.Find'), ('Use.Run2', 'calls', 'Repo.Find')], [('Use.Run', 'calls', 'Repo.Find'), ('Use.Run2', 'calls', 'RepoExt.Find')],
     source='csharpstandard expressions.md §12.8.10.3 (if the normal processing of the invocation finds no applicable methods; example b.F("hello")); Roslyn Binder_Invocation.cs BindMethodGroupInvocation')
case('csharp', 'extension-better-this-type', 'of the extensions of one namespace, overload resolution chooses by the type of the first parameter: the one written for the more derived type',
     CS, {'A.cs': 'using System;using System.Collections.Generic;using System.Linq;\nnamespace App\n{\n public class Base { }\n public class Derived : Base { }\n public static class BaseExt { public static int Size(this Base b) { return 1; } }\n public static class DerivedExt { public static int Size(this Derived d) { return 2; } }\n public class Use\n {\n     public int Run(Derived d) { return d.Size(); }\n     public int Run2(Base b) { return b.Size(); }\n }\n}\n'},
     [('Use.Run', 'calls', 'DerivedExt.Size'), ('Use.Run2', 'calls', 'BaseExt.Size')], [('Use.Run', 'calls', 'BaseExt.Size'), ('Use.Run2', 'calls', 'DerivedExt.Size')],
     source='csharpstandard expressions.md §12.8.10.3 (overload resolution is applied to the candidate set), §12.6.4.5')
case('csharp', 'extension-declared-before-imported', "an extension declared in a namespace comes before one the namespace imports with `using`, which comes before the enclosing namespace's",
     CS, {'A.cs': 'namespace N1\n{\n    public static class D { public static int F(this int i) { return 1; } public static int G(this int i) { return 1; } }\n}\n\npublic static class C { public static int F(this int i) { return 0; } public static int G(this int i) { return 0; } public static int H(this int i) { return 0; } }\n\nnamespace N2\n{\n    using N1;\n\n    public static class E { public static int F(this int i) { return 2; } }\n\n    public class Use\n    {\n        public int Run() { return 1.F(); }\n        public int Run2() { return 2.G(); }\n        public int Run3() { return 3.H(); }\n    }\n}\n'},
     [('Use.Run', 'calls', 'E.F'), ('Use.Run2', 'calls', 'D.G'), ('Use.Run3', 'calls', 'C.H')], [('Use.Run', 'calls', 'D.F'), ('Use.Run', 'calls', 'C.F'), ('Use.Run2', 'calls', 'C.G')],
     source='csharpstandard expressions.md §12.8.10.3 (the example with C, D and E)')
case('csharp', 'extension-receiver-conversion', 'an extension is eligible only if an identity, reference or boxing conversion leads from the receiver to its first parameter: an `int` receiver does not reach a `long` one',
     CS, {'A.cs': 'using System;using System.Collections.Generic;using System.Linq;\nnamespace App\n{\n public static class Ext\n {\n     public static int Twice(this long n) { return 1; }\n     public static int Twice(this object o) { return 2; }\n }\n public static class Other { public static int Twice(this long n) { return 3; } }\n public class Use\n {\n     public int Run(int n) { return n.Twice(); }\n }\n}\n'},
     [('Use.Run', 'calls', 'A.cs:App.Ext.Twice@7')], [('Use.Run', 'calls', 'A.cs:App.Ext.Twice@6'), ('Use.Run', 'calls', 'Other.Twice')],
     source='csharpstandard expressions.md §12.8.10.3 (an implicit identity, reference or boxing conversion exists from expr to the type of the first parameter)')
case('csharp', 'type-argument-list-picks-the-generic', 'a call written with type arguments takes only generic methods with that many type parameters; one written without them prefers the non-generic',
     CS, {'A.cs': 'using System;using System.Collections.Generic;using System.Linq;\nnamespace App\n{\n public class Pick\n {\n     public int Take(int n) { return 1; }\n     public int Take<T>(T n) { return 2; }\n     public int Take<T, U>(T n) { return 3; }\n }\n public class Use\n {\n     public int Run(Pick p) { return p.Take(1); }\n     public int Run2(Pick p) { return p.Take<int>(1); }\n     public int Run3(Pick p) { return p.Take<int, string>(1); }\n }\n}\n'},
     [('Use.Run', 'calls', 'A.cs:App.Pick.Take@6'), ('Use.Run2', 'calls', 'A.cs:App.Pick.Take@7'), ('Use.Run3', 'calls', 'A.cs:App.Pick.Take@8')], [('Use.Run', 'calls', 'A.cs:App.Pick.Take@7'), ('Use.Run2', 'calls', 'A.cs:App.Pick.Take@6'), ('Use.Run3', 'calls', 'A.cs:App.Pick.Take@7'), ('Use.Run2', 'calls', 'A.cs:App.Pick.Take@8')],
     source='csharpstandard expressions.md §12.8.10.2 (a non-generic F is a candidate only when M has no type argument list; a generic F with a list needs as many type parameters)')
case('csharp', 'inferred-generic-result', "the type arguments of a generic method are inferred from the arguments: the result is the argument's type",
     CS, {'A.cs': 'using System;using System.Collections.Generic;using System.Linq;\nnamespace App\n{\n public class Item { public int Go() { return 1; } }\n public class Other { public int Go() { return 2; } }\n public class Use\n {\n     static T Id<T>(T x) { return x; }\n     public int Run() { return Id(new Item()).Go(); }\n }\n}\n'},
     [('Use.Run', 'calls', 'Use.Id'), ('Use.Run', 'calls', 'Item.Go')], [('Use.Run', 'calls', 'Other.Go')],
     source='csharpstandard expressions.md §12.6.3 (type inference), §12.8.10.2')
case('csharp', 'receiver-decides-static-or-instance', 'a call through a value does not consider static overloads and one through a type does not consider instance ones, even when they fit better',
     CS, {'A.cs': 'using System;using System.Collections.Generic;using System.Linq;\nnamespace App\n{\n public class Log\n {\n     public static int Write(int n) { return 1; }\n     public int Write(long n) { return 2; }\n }\n public class Use\n {\n     public int Run(Log l) { return l.Write(1); }\n     public int Run2() { return Log.Write(1); }\n }\n}\n'},
     [('Use.Run', 'calls', 'A.cs:App.Log.Write@7'), ('Use.Run2', 'calls', 'A.cs:App.Log.Write@6')], [('Use.Run', 'calls', 'A.cs:App.Log.Write@6')],
     source='csharpstandard expressions.md §12.6.4.2 (a static method is only applicable if the method group results from a simple_name or a member_access through a type; an instance method ... through a variable or value); Roslyn OverloadResolution.cs RemoveStaticInstanceMismatches')
case('csharp', 'generic-constraint-excludes-candidate', 'a generic method whose inferred type arguments do not satisfy their constraints is not applicable',
     CS, {'A.cs': 'using System;using System.Collections.Generic;using System.Linq;\nnamespace App\n{\n public class Pick\n {\n     public int Take<T>(T x) where T : struct { return 1; }\n     public int Take(object x) { return 2; }\n }\n public class Use\n {\n     public int Run(Pick p) { return p.Take("s"); }\n     public int Run2(Pick p) { return p.Take(1); }\n }\n}\n'},
     [('Use.Run', 'calls', 'A.cs:App.Pick.Take@7'), ('Use.Run2', 'calls', 'A.cs:App.Pick.Take@6')], [('Use.Run', 'calls', 'A.cs:App.Pick.Take@6')],
     source='csharpstandard expressions.md §12.6.4.2 (a generic method whose type arguments do not all satisfy their constraints is not applicable); Roslyn OverloadResolution.cs RemoveConstraintViolations')
case('csharp', 'overload-value-over-in', 'a value argument takes the value parameter where an `in` one fits too; an `in` argument only the `in` parameter',
     CS, {'A.cs': 'using System;using System.Collections.Generic;using System.Linq;\nnamespace App\n{\n public class M\n {\n     public int M1(int p) { return 1; }\n     public int M1(in int p) { return 2; }\n }\n public class Use\n {\n     public int Run(M m, int i) { return m.M1(i); }\n     public int Run2(M m, int i) { return m.M1(in i); }\n }\n}\n'},
     [('Use.Run', 'calls', 'A.cs:App.M.M1@6'), ('Use.Run2', 'calls', 'A.cs:App.M.M1@7')], [('Use.Run', 'calls', 'A.cs:App.M.M1@7'), ('Use.Run2', 'calls', 'A.cs:App.M.M1@6')],
     source='csharpstandard expressions.md §12.6.4.2, §12.6.4.4 (the method with the parameter-passing mode of value is the better choice)')
case('csharp', 'overload-params-more-declared-parameters', 'of two `params` candidates applicable only in the expanded form with the same parameter types, the one with more declared parameters (fewer elements in the array) is better',
     CS, {'A.cs': 'using System;using System.Collections.Generic;using System.Linq;\nnamespace App\n{\n public class Log\n {\n     public int Write(params int[] all) { return 1; }\n     public int Write(int first, params int[] rest) { return 2; }\n }\n public class Use\n {\n     public int Run(Log l) { return l.Write(1, 2); }\n }\n}\n'},
     [('Use.Run', 'calls', 'A.cs:App.Log.Write@7')], [('Use.Run', 'calls', 'A.cs:App.Log.Write@6')],
     source='csharpstandard expressions.md §12.6.4.3 (if both methods have params arrays and are applicable only in their expanded forms, the one whose params array has fewer elements is better)')
case('csharp', 'overload-more-specific-generic', 'of generic candidates with the same parameter types after inference, the one declared with the more specific parameter type is better',
     CS, {'A.cs': 'using System;using System.Collections.Generic;using System.Linq;\nnamespace App\n{\n public class Pick\n {\n     public int Take<T>(T x) { return 1; }\n     public int Take<T>(List<T> x) { return 2; }\n }\n public class Use\n {\n     public int Run(Pick p, List<int> xs) { return p.Take(xs); }\n }\n}\n'},
     [('Use.Run', 'calls', 'A.cs:App.Pick.Take@7')], [('Use.Run', 'calls', 'A.cs:App.Pick.Take@6')],
     source='csharpstandard expressions.md §12.6.4.3 (a type parameter is less specific than a non-type parameter; a constructed type is more specific)')
case('csharp', 'overload-null-takes-the-more-specific', "a `null` argument picks the parameter whose type converts to the other's",
     CS, {'A.cs': 'using System;using System.Collections.Generic;using System.Linq;\nnamespace App\n{\n public class Log\n {\n     public int Write(object o) { return 1; }\n     public int Write(string s) { return 2; }\n }\n public class Use\n {\n     public int Run(Log l) { return l.Write(null); }\n }\n}\n'},
     [('Use.Run', 'calls', 'A.cs:App.Log.Write@7')], [('Use.Run', 'calls', 'A.cs:App.Log.Write@6')],
     source='csharpstandard expressions.md §12.6.4.7 (better conversion target); Roslyn OverloadResolution.cs BetterConversionTarget')
case('csharp', 'generic-class-overload-original-signature', 'when substituting the type arguments of a generic class makes two overloads identical, the one declared with the more specific parameter type is chosen',
     CS, {'A.cs': 'using System;using System.Collections.Generic;using System.Linq;\nnamespace App\n{\n public class G1<U>\n {\n     public int F1(U u) { return 1; }\n     public int F1(int i) { return 2; }\n }\n public class Use\n {\n     public int Run(G1<int> g) { return g.F1(1); }\n }\n}\n'},
     [('Use.Run', 'calls', 'A.cs:App.G1.F1@7')], [('Use.Run', 'calls', 'A.cs:App.G1.F1@6')],
     source='csharpstandard expressions.md §12.6.4.8 (overload resolution will pick the most specific of the original signatures)')
case('csharp', 'indexer-overload', "an element access selects among the indexers of the receiver's type by overload resolution",
     CS, {'A.cs': 'using System;using System.Collections.Generic;using System.Linq;\nnamespace App\n{\n public class Table\n {\n     public int this[int i] { get { return i; } }\n     public string this[string k] { get { return k; } }\n }\n public class Use\n {\n     public int Run(Table t) { return t[1]; }\n     public string Run2(Table t) { return t["a"]; }\n }\n}\n'},
     [('Use.Run', 'reads', 'A.cs:App.Table.this[]@6'), ('Use.Run2', 'reads', 'A.cs:App.Table.this[]@7')], [('Use.Run', 'reads', 'A.cs:App.Table.this[]@7'), ('Use.Run2', 'reads', 'A.cs:App.Table.this[]@6')],
     source='csharpstandard expressions.md §12.6.1 (indexer access: overload resolution selects the best indexer), §12.6.4.1')
case('csharp', 'delegate-invocation', 'calling a value of a delegate type invokes that delegate type',
     CS, {'A.cs': 'using System;using System.Collections.Generic;using System.Linq;\nnamespace App\n{\n public delegate int Handler(int n);\n public delegate int Other(int n);\n public class Use\n {\n     public int Run(Handler h) { return h(1); }\n     public int Run2(Other o) { return o(1); }\n }\n}\n'},
     [('Use.Run', 'calls', 'Handler'), ('Use.Run2', 'calls', 'Other')], [('Use.Run', 'calls', 'Other'), ('Use.Run2', 'calls', 'Handler')],
     source='csharpstandard expressions.md §12.8.10.4; Roslyn Binder_Invocation.cs BindDelegateInvocation')
case('csharp', 'nameof-method-in-scope', 'where a method named `nameof` is in scope the call is a call of it, otherwise `nameof(X)` names X',
     CS, {'A.cs': 'using System;using System.Collections.Generic;using System.Linq;\nnamespace App\n{\n public class Use\n {\n     static int nameof(object o) { return 1; }\n     public int Run(int x) { return nameof(x); }\n }\n public class Item { public int Go() { return 1; } }\n public class Names\n {\n     public string Run() { return nameof(Item.Go); }\n }\n}\n'},
     [('Use.Run', 'calls', 'Use.nameof'), ('Names.Run', 'references', 'Item.Go')], [('Names.Run', 'calls', 'Use.nameof')],
     source='Roslyn Binder_Invocation.cs TryBindNameofOperator, InvocableNameofInScope')
case('csharp', 'deconstruct-method-and-element-types', 'a deconstruction calls the `Deconstruct` method of the type, and each new variable has the type of its out parameter',
     CS, {'A.cs': 'using System;using System.Collections.Generic;using System.Linq;\nnamespace App\n{\n public class Item { public void Go() { } }\n public class Thing { public void Go() { } }\n public class Pair\n {\n     public void Deconstruct(out Item a, out Thing b) { a = null; b = null; }\n }\n public class Use\n {\n     public void Run(Pair p)\n     {\n         var (a, b) = p;\n         a.Go();\n     }\n     public void Run2(Pair p)\n     {\n         (Item a, Thing b) = p;\n         b.Go();\n     }\n }\n}\n'},
     [('Use.Run', 'calls', 'Pair.Deconstruct'), ('Use.Run', 'calls', 'Item.Go'), ('Use.Run2', 'calls', 'Pair.Deconstruct'), ('Use.Run2', 'calls', 'Thing.Go')], [('Use.Run', 'calls', 'Thing.Go'), ('Use.Run2', 'calls', 'Item.Go')],
     source='csharpstandard expressions.md §12.7 (a unique instance or extension method S.Deconstruct with output parameters)')
case('csharp', 'deconstruct-extension-method', 'a type with no `Deconstruct` method is deconstructed by an extension method of that name',
     CS, {'A.cs': 'using System;using System.Collections.Generic;using System.Linq;\nnamespace App\n{\n public class Item { public void Go() { } }\n public class Thing { public void Go() { } }\n public class Pair { }\n public static class PairExt\n {\n     public static void Deconstruct(this Pair p, out Item a, out Thing b) { a = null; b = null; }\n }\n public class Use\n {\n     public void Run(Pair p)\n     {\n         var (a, b) = p;\n         b.Go();\n     }\n }\n}\n'},
     [('Use.Run', 'calls', 'PairExt.Deconstruct'), ('Use.Run', 'calls', 'Thing.Go')], [('Use.Run', 'calls', 'Item.Go')],
     source='csharpstandard expressions.md §12.7 (unique instance or extension method S.Deconstruct)')
case('csharp', 'deconstruct-tuple-types', 'a tuple deconstructs into its elements, each with its element type',
     CS, {'A.cs': 'using System;using System.Collections.Generic;using System.Linq;\nnamespace App\n{\n public class Item { public void Go() { } }\n public class Thing { public void Go() { } }\n public class Use\n {\n     public void Run((Item, Thing) t)\n     {\n         var (a, b) = t;\n         b.Go();\n     }\n }\n}\n'},
     [('Use.Run', 'calls', 'Thing.Go')], [('Use.Run', 'calls', 'Item.Go')],
     source='csharpstandard expressions.md §12.7 (a tuple type (T1, ..., Tn) is deconstructed as (E.Item1, ..., E.Itemn))')
case('csharp', 'null-conditional-chain', 'in `a?.b?.M()` and `a?.F().M()` each member is looked up in the type of the part before it',
     CS, {'A.cs': 'using System;using System.Collections.Generic;using System.Linq;\nnamespace App\n{\n public class Leaf { public void Go() { } }\n public class Other { public void Go() { } }\n public class Node { public Leaf Child { get { return null; } } public Leaf Find() { return null; } }\n public class Use\n {\n     public void Run(Node n) { n?.Child?.Go(); }\n     public void Run2(Node n) { n?.Find().Go(); }\n }\n}\n'},
     [('Use.Run', 'reads', 'Node.Child'), ('Use.Run', 'calls', 'Leaf.Go'), ('Use.Run2', 'calls', 'Node.Find'), ('Use.Run2', 'calls', 'Leaf.Go')], [('Use.Run', 'calls', 'Other.Go'), ('Use.Run2', 'calls', 'Other.Go')],
     source='csharpstandard expressions.md §12.8.11, §12.8.8 (P?A means the same as P.A when P is not null)')
case('csharp', 'lambda-explicit-parameter-type', 'a parameter of an anonymous function written with its type has that type in the body',
     CS, {'A.cs': 'using System;using System.Collections.Generic;using System.Linq;\nnamespace App\n{\n public class Item { public void Go() { } }\n public class Other { public void Go() { } }\n public class Use\n {\n     public void Run()\n     {\n         Action<Item> f = (Item i) => i.Go();\n         Action<Item> g = delegate (Item i) { i.Go(); };\n     }\n }\n}\n'},
     [('Use.Run', 'calls', 'Item.Go')], [('Use.Run', 'calls', 'Other.Go')],
     source='csharpstandard expressions.md §12.21.2, §12.21.3 (the parameters specified in the signature are available in the body)')
case('csharp', 'lambda-parameter-hides-member', 'the parameter of an anonymous function hides a field of its name in the body only',
     CS, {'A.cs': 'using System;using System.Collections.Generic;using System.Linq;\nnamespace App\n{\n public class Item { public void Go() { } }\n public class Other { public void Go() { } }\n public class Use\n {\n     Other x;\n     public void Run()\n     {\n         Action<Item> f = (Item x) => x.Go();\n     }\n     public void Run2()\n     {\n         x.Go();\n     }\n }\n}\n'},
     [('Use.Run', 'calls', 'Item.Go'), ('Use.Run2', 'calls', 'Other.Go')], [('Use.Run', 'calls', 'Other.Go'), ('Use.Run2', 'calls', 'Item.Go'), ('Use.Run', 'reads', 'Use.x')],
     source='csharpstandard expressions.md §12.21.2 (the scope of the parameters is the anonymous_function_body), §12.8.4')
case('csharp', 'lambda-uses-outer-local', 'a name in the body of an anonymous function that is no parameter of it is looked up outward: an outer local hides a field',
     CS, {'A.cs': 'using System;using System.Collections.Generic;using System.Linq;\nnamespace App\n{\n public class Item { public void Go() { } }\n public class Other { public void Go() { } }\n public class Use\n {\n     Other it;\n     public void Run()\n     {\n         Item it = new Item();\n         Action f = () => it.Go();\n         f();\n     }\n }\n}\n'},
     [('Use.Run', 'calls', 'Item.Go')], [('Use.Run', 'calls', 'Other.Go'), ('Use.Run', 'reads', 'Use.it')],
     source='csharpstandard expressions.md §12.21.3 (the body has access to the outer variables), §12.21.6')
case('csharp', 'lambda-discard-parameters', 'two or more parameters named `_` are discards that name nothing; a single `_` is a parameter',
     CS, {'A.cs': 'using System;using System.Collections.Generic;using System.Linq;\nnamespace App\n{\n public class Item { public void Go() { } }\n public class Other { public void Go() { } }\n public class Use\n {\n     Other _;\n     public void Run()\n     {\n         Func<Item, Item, int> f = (_, _) => { _ = null; return 1; };\n     }\n     public void Run2()\n     {\n         Action<Item> g = _ => _.Go();\n     }\n     public void Run3()\n     {\n         Func<Item, Item, int> h = (_, _) => { _.Go(); return 1; };\n     }\n }\n}\n'},
     [('Use.Run2', 'calls', 'Item.Go'), ('Use.Run3', 'calls', 'Other.Go')], [('Use.Run3', 'calls', 'Item.Go'), ('Use.Run2', 'calls', 'Other.Go')],
     source='Roslyn Binder_Lambda.cs (when there are two or more underscores, they are discards)', since='9')
case('csharp', 'query-range-variable', 'the range variable of a `from` clause has the element type of the source and hides a field of its name in the whole query',
     CS, {'A.cs': 'using System;using System.Collections;using System.Collections.Generic;using System.Linq;\nnamespace App\n{\n public class Customer { public bool Active() { return true; } public string Name() { return ""; } public int Id() { return 1; } public string Region() { return ""; } public Dept Dept() { return null; } public List<Order> Orders() { return null; } }\n public class Dept { public string Title() { return ""; } }\n public class Order { public int Total() { return 1; } public int CustId() { return 1; } }\n public class Other { public bool Active() { return true; } public string Name() { return ""; } }\n public class Use\n {\n     Other c;\n     public IEnumerable<string> Run(List<Customer> customers)\n     {\n         return from c in customers where c.Active() orderby c.Name() select c.Name();\n     }\n }\n}\n'},
     [('Use.Run', 'calls', 'Customer.Active'), ('Use.Run', 'calls', 'Customer.Name')], [('Use.Run', 'calls', 'Other.Active'), ('Use.Run', 'calls', 'Other.Name'), ('Use.Run', 'reads', 'Use.c')],
     source='csharpstandard expressions.md §12.22.1, §12.22.3.5; Roslyn Binder_Query.cs')
case('csharp', 'query-explicit-range-type', '`from T x in e` gives the range variable the type T',
     CS, {'A.cs': 'using System;using System.Collections;using System.Collections.Generic;using System.Linq;\nnamespace App\n{\n public class Customer { public bool Active() { return true; } }\n public class Other { public bool Active() { return true; } }\n public class Use\n {\n     public IEnumerable<Customer> Run(ArrayList customers)\n     {\n         return from Customer c in customers where c.Active() select c;\n     }\n }\n}\n'},
     [('Use.Run', 'calls', 'Customer.Active')], [('Use.Run', 'calls', 'Other.Active')],
     source='csharpstandard expressions.md §12.22.3.3 (translated into from x in (e).Cast<T>()); Roslyn Binder_Query.cs')
case('csharp', 'query-let-and-second-from', 'a `let` variable has the type of its expression, a second `from` ranges over the elements of its expression, and earlier range variables stay in scope',
     CS, {'A.cs': 'using System;using System.Collections;using System.Collections.Generic;using System.Linq;\nnamespace App\n{\n public class Customer { public int Id() { return 1; } public Dept Dept() { return null; } public List<Order> Orders() { return null; } }\n public class Dept { public string Title() { return ""; } }\n public class Order { public int Total() { return 1; } }\n public class Other { public int Total() { return 1; } public string Title() { return ""; } }\n public class Use\n {\n     public IEnumerable<string> Run(List<Customer> customers)\n     {\n         return from c in customers let d = c.Dept() where c.Id() > 0 select d.Title();\n     }\n     public IEnumerable<int> Run2(List<Customer> customers)\n     {\n         return from c in customers from o in c.Orders() select o.Total();\n     }\n }\n}\n'},
     [('Use.Run', 'calls', 'Customer.Dept'), ('Use.Run', 'calls', 'Customer.Id'), ('Use.Run', 'calls', 'Dept.Title'), ('Use.Run2', 'calls', 'Customer.Orders'), ('Use.Run2', 'calls', 'Order.Total')], [('Use.Run', 'calls', 'Other.Title'), ('Use.Run2', 'calls', 'Other.Total')],
     source='csharpstandard expressions.md §12.22.3.5, §12.22.3.8 (transparent identifiers)')
case('csharp', 'query-join-and-continuation', '`join` ranges over its source and compares keys of the two range variables; a continuation `into g` ranges over the groups',
     CS, {'A.cs': 'using System;using System.Collections;using System.Collections.Generic;using System.Linq;\nnamespace App\n{\n public class Customer { public int Id() { return 1; } public string Region() { return ""; } public string Name() { return ""; } }\n public class Order { public int CustId() { return 1; } public int Total() { return 1; } }\n public class Other { public int Total() { return 1; } public string Name() { return ""; } }\n public class Use\n {\n     public IEnumerable<int> Run(List<Customer> cs, List<Order> os)\n     {\n         return from c in cs join o in os on c.Id() equals o.CustId() select o.Total();\n     }\n     public IEnumerable<string> Run2(List<Customer> cs)\n     {\n         return from c in cs group c by c.Region() into g select g.First().Name();\n     }\n }\n}\n'},
     [('Use.Run', 'calls', 'Customer.Id'), ('Use.Run', 'calls', 'Order.CustId'), ('Use.Run', 'calls', 'Order.Total'), ('Use.Run2', 'calls', 'Customer.Region'), ('Use.Run2', 'calls', 'Customer.Name')], [('Use.Run', 'calls', 'Other.Total'), ('Use.Run2', 'calls', 'Other.Name')],
     source='csharpstandard expressions.md §12.22.3.2, §12.22.3.5')
case('csharp', 'query-pattern-methods', "a query clause is a call of the source's method of that name (`where` is `Where`, `select` is `Select`), found as any method, an instance one before an extension; its lambdas take their parameter types from it",
     CS, {'A.cs': 'using System;using System.Collections;using System.Collections.Generic;using System.Linq;\nnamespace App\n{\n public class Item { public bool Ok() { return true; } public int Val() { return 1; } }\n public class Other { public bool Ok() { return true; } public int Val() { return 1; } }\n public class Source\n {\n     public Source Where(Func<Item, bool> f) { return this; }\n     public Source Select(Func<Item, int> f) { return this; }\n }\n public static class SourceExt\n {\n     public static Source Where(this Source s, Func<Item, bool> f) { return s; }\n }\n public class Use\n {\n     public Source Run(Source src)\n     {\n         return from x in src where x.Ok() select x.Val();\n     }\n }\n}\n'},
     [('Use.Run', 'calls', 'Source.Where'), ('Use.Run', 'calls', 'Source.Select'), ('Use.Run', 'calls', 'Item.Ok'), ('Use.Run', 'calls', 'Item.Val')], [('Use.Run', 'calls', 'SourceExt.Where'), ('Use.Run', 'calls', 'Other.Ok')],
     source='csharpstandard expressions.md §12.22.3.1, §12.22.4 (the query-expression pattern); Roslyn Binder_Query.cs MakeQueryInvocation')
case('csharp', 'inaccessible-method-not-candidate', 'a private method of the derived type is not a candidate from outside it; the accessible base method is called',
     CS, {'A.cs': 'namespace App {\n  public class Base { public void Put(int x) {} }\n  public class Box : Base { private void Put(int x) {} }\n  public class User { public void Run(Box b) { b.Put(1); } }\n}\n'},
     [('User.Run', 'calls', 'Base.Put')], [('User.Run', 'calls', 'Box.Put')],
     source='csharpstandard §12.8.10.2; Roslyn OverloadResolution.cs (accessibility in candidate lookup)')
case('csharp', 'type-parameter-receiver-constraint', 't.Draw() with T : IShape is IShape.Draw',
     CS, {'A.cs': 'namespace App {\n  public interface IShape { void Draw(); }\n  public class Other { public void Draw() {} }\n  public class User { public void Run<T>(T t) where T : IShape { t.Draw(); } }\n}\n'},
     [('User.Run', 'calls', 'IShape.Draw')], [('User.Run', 'calls', 'Other.Draw')],
     source='csharpstandard §12.8.10.2 (member lookup on a type parameter, §12.5)')
case('csharp', 'static-abstract-operator-via-constraint', "a + b on T : IAdd<T> is a use of IAdd.operator+, not of the implementing class's operator",
     CS, {'A.cs': 'namespace App {\n  public interface IAdd<T> where T : IAdd<T> { static abstract T operator +(T a, T b); }\n  public class Money : IAdd<Money> { public static Money operator +(Money a, Money b) => a; }\n  public class User { public T Sum<T>(T a, T b) where T : IAdd<T> => a + b; }\n}\n'},
     [('User.Sum', 'calls', 'IAdd.operator+')], [('User.Sum', 'calls', 'Money.operator+')],
     source='csharpstandard §12.4.6, §12.4.4', since='11')
case('csharp', 'out-var-has-parameter-type', '`if (s.TryGet(out var n)) n.Show();` is Num.Show',
     CS, {'A.cs': 'namespace App {\n  public class Num { public void Show() {} }\n  public class Other { public void Show() {} }\n  public class Src { public bool TryGet(out Num n) { n = null; return true; } }\n  public class User { public void Run(Src s) { if (s.TryGet(out var n)) n.Show(); } }\n}\n'},
     [('User.Run', 'calls', 'Num.Show')], [('User.Run', 'calls', 'Other.Show')],
     source='csharpstandard §12.6.2 / §7.7.1; Roslyn Binder_Invocation.cs (out var inference)')
case('csharp', 'query-trivial-select-omitted', '`from x in s where .. orderby a, b select x` calls Where, OrderBy and ThenBy, and no Select',
     CS, {'A.cs': 'using System;\nnamespace App {\n  public class Src { public Src Where(Func<int,bool> f) => this; public Src Select(Func<int,int> f) => this; public Src OrderBy(Func<int,int> f) => this; public Src ThenBy(Func<int,int> f) => this; }\n  public class User { public void Run(Src s) { var q = from x in s where x > 0 orderby x, x select x; } }\n}\n'},
     [('User.Run', 'calls', 'Src.Where'), ('User.Run', 'calls', 'Src.OrderBy'), ('User.Run', 'calls', 'Src.ThenBy')], [('User.Run', 'calls', 'Src.Select')],
     source='csharpstandard §12.22.2.4; Roslyn Binder_Query.cs (select omitted when identity)')
case('csharp', 'checked-operator-by-context', 'x + y inside checked(...) is the `operator checked +`, inside unchecked(...) the plain `operator +`',
     CS, {'A.cs': 'namespace App {\n  public struct Money {\n    public static Money operator +(Money a, Money b) => a;\n    public static Money operator checked +(Money a, Money b) => a;\n  }\n  public class Host {\n    public Money Strict(Money x, Money y) { return checked(x + y); }\n    public Money Loose(Money x, Money y) { return unchecked(x + y); }\n  }\n}\n'},
     [('Host.Strict', 'calls', 'Money.operator checked +'), ('Host.Loose', 'calls', 'Money.operator+')], [('Host.Strict', 'calls', 'Money.operator+'), ('Host.Loose', 'calls', 'Money.operator checked +')],
     source='Roslyn Binder_Operators.cs / OverloadResolution.cs (checked operator preference); csharpstandard §12.4.3', since='11')
case('csharp', 'static-abstract-call-on-type-parameter', '`T.Create()` with `where T : IMake<T>` binds to IMake.Create, never to the Create of an implementing class',
     CS, {'A.cs': 'namespace App {\n  public interface IMake<T> { static abstract T Create(); }\n  public class Widget : IMake<Widget> { public static Widget Create() => new Widget(); }\n  public class Gadget { public static Gadget Create() => new Gadget(); }\n  public class Host {\n    public T Make<T>() where T : IMake<T> { return T.Create(); }\n  }\n}\n'},
     [('Host.Make', 'calls', 'IMake.Create')], [('Host.Make', 'calls', 'Widget.Create'), ('Host.Make', 'calls', 'Gadget.Create')],
     source='csharpstandard §12.8.10, §12.8.7 (member access on a type parameter); Roslyn Binder_Invocation.cs', since='11')
case('csharp', 'lambda-parameter-hides-outer-local', "in `x => x.Hit()` the `x` is the lambda's parameter (type B), not the enclosing method's local `x` (type A)",
     CS, {'A.cs': 'using System;\nnamespace App {\n  public class A { public void Hit() {} }\n  public class B { public void Hit() {} }\n  public class Host {\n    public void Shadow() {\n      A x = new A();\n      Func<B, int> f = x => { x.Hit(); return 1; };\n    }\n  }\n}\n'},
     [('Host.Shadow', 'calls', 'B.Hit')], [('Host.Shadow', 'calls', 'A.Hit')],
     source='csharpstandard §12.21; Roslyn Binder_Lambda.cs, LocalScopeBinder (name shadowing in nested functions)', since='8')
case('csharp', 'derived-method-not-applicable-base-used', "a derived class's method of the name that does not take the arguments does not hide the base's that does",
     CS, {'A.cs': 'namespace App\n{\n    public class Base\n    {\n        public int M(int x) { return x; }\n    }\n\n    public class Derived : Base\n    {\n        public int M(string s) { return 0; }\n\n        public int Run() { return M(1); }\n    }\n}\n'},
     [('Derived.Run', 'calls', 'Base.M')], [('Derived.Run', 'calls', 'Derived.M')],
     source='csharpstandard §12.8.10.2 (candidates are only the applicable methods, then reduced to the most derived types); Roslyn OverloadResolution.cs RemoveLessDerivedMembers')
case('csharp', 'non-invocable-member-skipped', 'a field that is not of a delegate type does not hide a base method of its name when the name is invoked',
     CS, {'A.cs': 'namespace App\n{\n    public class Base\n    {\n        public int Size() { return 1; }\n    }\n\n    public class Derived : Base\n    {\n        public int Size = 2;\n\n        public int Run() { return Size(); }\n    }\n}\n'},
     [('Derived.Run', 'calls', 'Base.Size')], [('Derived.Run', 'reads', 'Derived.Size')],
     source="csharpstandard §12.5 (member lookup, step 'if the member is invoked'), used by §12.8.10.2; Roslyn Binder_Lookup.cs MustBeInvocableIfMember")
case('csharp', 'deconstruct-by-arity', '`var (x, y) = p` is a call of the two-out-parameter Deconstruct, not the three-out one',
     CS, {'A.cs': 'namespace App\n{\n    public class P\n    {\n        public void Deconstruct(out int a, out int b) { a = 1; b = 2; }\n        public void Deconstruct(out int a, out int b, out int c) { a = 1; b = 2; c = 3; }\n    }\n\n    public class C\n    {\n        public int Run(P p) { var (x, y) = p; return x + y; }\n    }\n}\n'},
     [('C.Run', 'calls', 'A.cs:App.P.Deconstruct@5')], [('C.Run', 'calls', 'A.cs:App.P.Deconstruct@6')],
     source='csharpstandard §12.7 (Deconstruction: S.Deconstruct with n output parameters); Roslyn Binder_Deconstruct.cs')
case('csharp', 'null-conditional-element-access-member', "`b?[0].Weight()` calls Weight of the indexer's type",
     CS, {'A.cs': 'namespace App\n{\n    public class Item { public int Weight() { return 1; } }\n\n    public class Crate { public int Weight() { return 2; } }\n\n    public class Bag\n    {\n        public Item this[int i] { get { return null; } }\n    }\n\n    public class C\n    {\n        public Crate Weight;\n\n        public int Run(Bag b) { return b?[0].Weight() ?? 0; }\n    }\n}\n'},
     [('C.Run', 'calls', 'Item.Weight')], [('C.Run', 'calls', 'Crate.Weight'), ('C.Run', 'reads', 'C.Weight')],
     source='csharpstandard §12.8.11 and §12.8.13 (null-conditional element access: P?[A] is the element access on P, then the dependent accesses); Roslyn Binder_Expressions.cs BindConditionalAccessExpression')
case('csharp', 'anonymous-method-parameter-type', "`delegate (Foo f) { f.Go(); }` calls Foo.Go, not the Go of the field f's type",
     CS, {'A.cs': 'using System;\n\nnamespace App\n{\n    public class Foo { public int Go() { return 1; } }\n\n    public class Bar { public int Go() { return 2; } }\n\n    public class C\n    {\n        Bar f;\n\n        int Tick() { return 0; }\n\n        public void Run()\n        {\n            Action<Foo> a = delegate (Foo f) { f.Go(); };\n            Action b = delegate { Tick(); };\n        }\n    }\n}\n'},
     [('C.Run', 'calls', 'Foo.Go'), ('C.Run', 'calls', 'C.Tick')], [('C.Run', 'calls', 'Bar.Go')],
     source='csharpstandard §12.8.24 (anonymous method expressions; the explicit signature) with §12.21 (parameters in the body); Roslyn Binder_Lambda.cs')
case('csharp', 'increment-operator-call', "`c++` is a call of `operator ++` of c's type",
     CS, {'A.cs': 'namespace App\n{\n    public struct Counter\n    {\n        public int V;\n\n        public static Counter operator ++(Counter c) { c.V++; return c; }\n\n        public static Counter operator +(Counter c, int n) { c.V += n; return c; }\n    }\n\n    public class C\n    {\n        public Counter Run(Counter c) { c++; return c; }\n    }\n}\n'},
     [('C.Run', 'calls', 'Counter.operator++')], [('C.Run', 'calls', 'Counter.operator+')],
     source='csharpstandard §12.4.4 (unary operator overload resolution) and §12.9.6 (postfix increment: the user-defined operator); Roslyn Binder_Operators.cs BindIncrementOperator')
case('csharp', 'query-continuation-ends-range-variables', '`x` after `into y` is the field x, not the range variable',
     CS, {'A.cs': 'using System.Linq;\nusing System.Collections.Generic;\n\nnamespace App\n{\n    public class Row { public int Id; public int Size; }\n\n    public class Cfg { public int Size; }\n\n    public class C\n    {\n        Cfg x = null;\n\n        public IEnumerable<int> Run(List<Row> rows)\n        {\n            return from x in rows select x.Id into y select y + x.Size;\n        }\n    }\n}\n'},
     [('C.Run', 'reads', 'Cfg.Size'), ('C.Run', 'reads', 'C.x')], [('C.Run', 'reads', 'Row.Size')],
     source='csharpstandard §12.22.2.? (query continuations: `from x in e select v into y ...` is `from y in (from x in e select v) ...`); Roslyn Binder_Query.cs')
case('csharp', 'inference-through-interface-and-array', '`First(list).Go()` and `Head(array).Go()` call Foo.Go',
     CS, {'A.cs': 'using System.Collections.Generic;\n\nnamespace App\n{\n    public class Foo { public int Go() { return 1; } }\n\n    public class Bar { public int Go() { return 2; } }\n\n    public static class U\n    {\n        public static T First<T>(IEnumerable<T> xs) { return default(T); }\n\n        public static T Head<T>(T[] xs) { return xs[0]; }\n    }\n\n    public class C\n    {\n        public int Run(List<Foo> l, Foo[] a) { return U.First(l).Go() + U.Head(a).Go(); }\n    }\n}\n'},
     [('C.Run', 'calls', 'Foo.Go')], [('C.Run', 'calls', 'Bar.Go')],
     source="csharpstandard §12.6.3.10 (lower-bound inferences: constructed interface the argument's type implements; arrays)")
case('csharp', 'constructor-overload-by-type', '`new T("a")` constructs with T(string), not T(int)',
     CS, {'A.cs': 'namespace App\n{\n    public class T\n    {\n        public T(int x) { }\n        public T(string s) { }\n    }\n\n    public class C\n    {\n        public T Run() { return new T("a"); }\n    }\n}\n'},
     [('C.Run', 'calls', 'A.cs:App.T.T@6')], [('C.Run', 'calls', 'A.cs:App.T.T@5')],
     source='csharpstandard §12.8.17.2 (object creation: overload resolution of the instance constructors) with §12.6.4; Roslyn Binder_Expressions.cs BindClassCreationExpression')
case('csharp', 'nested-lambda-outer-parameter', "`a` in the inner lambda is the outer lambda's `Foo a`, not the field a",
     CS, {'A.cs': 'using System;\n\nnamespace App\n{\n    public class Foo { public int Go() { return 1; } }\n\n    public class Bar { public int Go() { return 2; } }\n\n    public class C\n    {\n        Bar a = null;\n\n        public void Run()\n        {\n            Func<Foo, Func<int, int>> f = (Foo a) => (int n) => a.Go() + n;\n        }\n    }\n}\n'},
     [('C.Run', 'calls', 'Foo.Go')], [('C.Run', 'calls', 'Bar.Go')],
     source='csharpstandard §12.21.1 (scope of a parameter is the body, which contains nested functions); Roslyn Binder_Lambda.cs')
case('csharp', 'local-function-hides-method', '`Step(1)` beside a local function Step is the local function, not the method Step',
     CS, {'A.cs': 'namespace App\n{\n    public class C\n    {\n        int Step(int x) { return x; }\n\n        public int Run()\n        {\n            int Step(int y) { return y + 1; }\n            return Step(1);\n        }\n    }\n}\n'},
     [('C.Run', 'calls', 'Run.Step')], [('C.Run', 'calls', 'C.Step')],
     source='csharpstandard §12.8.4 (simple names: local variable declaration space first) used by §12.8.10; Roslyn Binder_Lookup.cs LookupSymbolsInSingleBinder in LocalScopeBinder')
case('csharp', 'nested-class-calls-enclosing-static', "`Helper()` in a nested class is the enclosing class's static; `Count()` is the nested class's own, not the enclosing one's",
     CS, {'A.cs': 'namespace App\n{\n    public class Other { public static int Count() { return 9; } }\n\n    public class Outer\n    {\n        static int Helper() { return 1; }\n\n        static int Count() { return 2; }\n\n        public class Inner\n        {\n            static int Count() { return 3; }\n\n            public int Run() { return Helper() + Count(); }\n        }\n    }\n}\n'},
     [('Inner.Run', 'calls', 'Outer.Helper'), ('Inner.Run', 'calls', 'Inner.Count')], [('Inner.Run', 'calls', 'Outer.Count'), ('Inner.Run', 'calls', 'Other.Count')],
     source='csharpstandard §12.8.4 (simple names: each enclosing type in turn) used by §12.8.10.2; Roslyn Binder_Invocation.cs / Binder_Lookup.cs')
case('csharp', 'property-pattern-member', 'the name left of the colon in a property pattern is a field or property of the input type, not of the enclosing class',
     CS, {'A.cs': 'namespace App\n{\n    public class Person { public string Name; public int Age; }\n    public class Checker\n    {\n        public string Name;\n        public bool Run(Person p) { return p is { Name: "x" }; }\n        public bool Typed(object o) { return o is Person { Age: > 3 }; }\n    }\n}\n'},
     [('Checker.Run', 'reads', 'Person.Name'), ('Checker.Typed', 'reads', 'Person.Age')], [('Checker.Run', 'reads', 'Checker.Name')],
     source='csharpstandard patterns.md §11.2.6 property pattern; Roslyn Binder_Patterns.cs BindPropertyPatternClause', since='9.0')
case('csharp', 'extended-property-pattern', "in `{ Home.City: ... }` Home is a member of the input type and City a member of Home's type",
     CS, {'A.cs': 'namespace App\n{\n    public class Address { public string City; }\n    public class Person { public Address Home; }\n    public class Other { public string City; }\n    public class Checker\n    {\n        public bool Run(Person p) { return p is { Home.City: "Oslo" }; }\n    }\n}\n'},
     [('Checker.Run', 'reads', 'Person.Home'), ('Checker.Run', 'reads', 'Address.City')], [('Checker.Run', 'reads', 'Other.City')],
     source='csharpstandard patterns.md §11.2.6; Roslyn Binder_Patterns.cs BindPropertyPatternClause (ExpressionColon, IDS_FeatureExtendedPropertyPatterns)', since='10.0')
case('csharp', 'positional-pattern-deconstruct', '`p is (var a, var b)` calls the instance Deconstruct of that arity; three subpatterns call the extension Deconstruct',
     CS, {'A.cs': 'namespace App\n{\n    public class P\n    {\n        public void Deconstruct(out int a, out int b) { a = 1; b = 2; }\n    }\n    public static class Ext\n    {\n        public static void Deconstruct(this P p, out int a, out int b, out int c) { a = 1; b = 2; c = 3; }\n    }\n    public class Checker\n    {\n        public bool Two(P p) { return p is (var a, var b); }\n        public bool Three(P p) { return p is (var a, var b, var c); }\n    }\n}\n'},
     [('Checker.Two', 'calls', 'P.Deconstruct'), ('Checker.Three', 'calls', 'Ext.Deconstruct')], [('Checker.Two', 'calls', 'Ext.Deconstruct'), ('Checker.Three', 'calls', 'P.Deconstruct')],
     source='csharpstandard patterns.md §11.2.5 positional pattern (Deconstruct form); Roslyn Binder_Patterns.cs BindRecursivePattern, MakeDeconstructInvocationExpression', since='8.0')
case('csharp', 'constant-pattern-before-type', '`x is B` where B names a type of the namespace and a constant of the class: the type pattern (Roslyn binds the type)',
     CS, {'A.cs': 'namespace App\n{\n    public class B { }\n    public class A\n    {\n        public const int B = 1;\n        public bool Run(int x) { return x is B; }\n    }\n}\n'},
     [('A.Run', 'references', 'App.B')], [('A.Run', 'reads', 'A.B')],
     source='csharpstandard patterns.md §11.2.2; Roslyn Binder_Patterns.cs BindTypeOrConstantPattern / BindExpressionOrTypeForPattern')
case('csharp', 'constant-pattern-case-label', '`case Limits.Max:` and `case Zero:` read the constants they name',
     CS, {'A.cs': 'namespace App\n{\n    public static class Limits { public const int Max = 10; }\n    public class A\n    {\n        public const int Zero = 0;\n        public int Run(int x)\n        {\n            switch (x)\n            {\n                case Limits.Max: return 1;\n                case Zero: return 2;\n                default: return 3;\n            }\n        }\n    }\n}\n'},
     [('A.Run', 'reads', 'Limits.Max'), ('A.Run', 'reads', 'A.Zero')], [],
     source='csharpstandard patterns.md §11.2.2; statements.md §13.8.3')
case('csharp', 'var-pattern-input-type', '`c is var d` gives d the type of c',
     CS, {'A.cs': 'namespace App\n{\n    public class Circle { public int Area() { return 1; } }\n    public class Square { public int Area() { return 2; } }\n    public class C\n    {\n        public int Run(Circle c) { if (c is var d) { return d.Area(); } return 0; }\n    }\n}\n'},
     [('C.Run', 'calls', 'Circle.Area')], [('C.Run', 'calls', 'Square.Area')],
     source='csharpstandard patterns.md §11.2.4 var pattern; Roslyn Binder_Patterns.cs BindVarPattern')
case('csharp', 'pattern-combinators', '`o is Circle or Square` and `o is not Tri` are uses of the three types',
     CS, {'A.cs': 'namespace App\n{\n    public class Circle { }\n    public class Square { }\n    public class Tri { }\n    public class C\n    {\n        public bool Or(object o) { return o is Circle or Square; }\n        public bool Not(object o) { return o is not Tri; }\n    }\n}\n'},
     [('C.Or', 'references', 'Circle'), ('C.Or', 'references', 'Square'), ('C.Not', 'references', 'Tri')], [],
     source='csharpstandard patterns.md §11.2.8 pattern combinators; Roslyn Binder_Patterns.cs BindBinaryPattern / BindUnaryPattern', since='9.0')
case('csharp', 'block-local-scope', 'a local declared in an inner block hides the field only inside that block',
     CS, {'A.cs': 'namespace App\n{\n    public class Item { public int A() { return 1; } }\n    public class Other { public int A() { return 2; } }\n    public class C\n    {\n        public Other x = new Other();\n        public int In() { { Item x = new Item(); return x.A(); } }\n        public int Out()\n        {\n            { Item x = new Item(); x.A(); }\n            return x.A();\n        }\n    }\n}\n'},
     [('C.In', 'calls', 'Item.A'), ('C.Out', 'calls', 'Other.A')], [('C.In', 'calls', 'Other.A')],
     source='csharpstandard statements.md §13.3.1; Roslyn LocalScopeBinder / BlockBinder')
case('csharp', 'for-variable-scope', 'a `for` variable hides the field of its name only inside the loop',
     CS, {'A.cs': 'namespace App\n{\n    public class Item { public int A() { return 1; } }\n    public class Other { public int B() { return 2; } public int A() { return 3; } }\n    public class C\n    {\n        public Other i = new Other();\n        public int Run()\n        {\n            for (var i = new Item(); i != null; i = null) { i.A(); }\n            return i.B();\n        }\n    }\n}\n'},
     [('C.Run', 'calls', 'Item.A'), ('C.Run', 'calls', 'Other.B')], [('C.Run', 'calls', 'Other.A')],
     source='csharpstandard statements.md §13.6.2.1, §13.9.4; Roslyn ForLoopBinder')
case('csharp', 'local-function-before-declaration', "a call written before a local function's declaration still means the local function, not the method of the class",
     CS, {'A.cs': 'namespace App\n{\n    public class C\n    {\n        public int Helper() { return 1; }\n        public int Run()\n        {\n            return Helper();\n            int Helper() { return 2; }\n        }\n        public int Own() { return Helper(); }\n    }\n}\n'},
     [('C.Run', 'calls', 'Run.Helper'), ('C.Own', 'calls', 'C.Helper')], [('C.Run', 'calls', 'C.Helper'), ('C.Own', 'calls', 'Run.Helper')],
     source='csharpstandard statements.md §13.6.4; Roslyn LocalScopeBinder.BuildLocalFunctions')
case('csharp', 'type-named-var', 'with a class named `var` in scope, `var v = new var();` declares a local of that class',
     CS, {'A.cs': '#pragma warning disable CS8981\nnamespace App\n{\n    public class var { public int A() { return 1; } }\n    public class Other { public int A() { return 2; } }\n    public class C\n    {\n        public int Run() { var v = new var(); return v.A(); }\n    }\n}\n'},
     [('C.Run', 'references', 'var'), ('C.Run', 'calls', 'var.A')], [('C.Run', 'calls', 'Other.A')],
     source='csharpstandard statements.md §13.6.2.1; Roslyn Binder_Statements.cs BindDeclarationStatement (isVar via BindTypeOrVarKeyword)')
case('csharp', 'switch-block-local-scope', 'a local declared in one switch section is the name in the other sections too',
     CS, {'A.cs': 'namespace App\n{\n    public class Item { public int A() { return 1; } public int B() { return 3; } }\n    public class Other { public int A() { return 2; } public int B() { return 4; } }\n    public class C\n    {\n        public Other x = new Other();\n        public int Run(int k)\n        {\n            switch (k)\n            {\n                case 1:\n                    Item x = new Item();\n                    return x.A();\n                case 2:\n                    x = new Item();\n                    return x.B();\n            }\n            return 0;\n        }\n    }\n}\n'},
     [('C.Run', 'calls', 'Item.A'), ('C.Run', 'calls', 'Item.B')], [('C.Run', 'calls', 'Other.B'), ('C.Run', 'calls', 'Other.A')],
     source='csharpstandard statements.md §13.8.3; Roslyn SwitchBinder.BuildLocals')
case('csharp', 'switch-section-pattern-scope', '`case Circle c when c.R > 0:` binds c in its section only; elsewhere c is the field',
     CS, {'A.cs': 'namespace App\n{\n    public class Circle { public int R; public int Area() { return R; } public int Name() { return 1; } }\n    public class Other { public int Area() { return 0; } public int Name() { return 0; } }\n    public class Shapes\n    {\n        public Other c = new Other();\n        public int Run(object o)\n        {\n            switch (o)\n            {\n                case Circle c when c.R > 0:\n                    return c.Area();\n                case int n:\n                    return c.Name();\n                default:\n                    return 0;\n            }\n        }\n        public int Arm(object o) => o switch { Circle c => c.Area(), _ => c.Name() };\n    }\n}\n'},
     [('Shapes.Run', 'calls', 'Circle.Area'), ('Shapes.Run', 'reads', 'Circle.R'), ('Shapes.Run', 'calls', 'Other.Name'), ('Shapes.Arm', 'calls', 'Circle.Area'), ('Shapes.Arm', 'calls', 'Other.Name')], [('Shapes.Run', 'calls', 'Circle.Name'), ('Shapes.Arm', 'calls', 'Circle.Name')],
     source='csharpstandard statements.md §13.8.3, expressions.md §12.11; Roslyn SwitchBinder, SwitchExpressionArmBinder')
case('csharp', 'pattern-variable-leaks-from-if', 'after `if (!(o is Foo s)) return;` s is the Foo local; after `while (o is Foo s)` s is the field again',
     CS, {'A.cs': 'namespace App\n{\n    public class Foo { public int Hello() { return 1; } }\n    public class Other { public int Hello() { return 2; } }\n    public class Checker\n    {\n        public Other s = new Other();\n        public int Leaks(object o)\n        {\n            if (!(o is Foo s)) return 0;\n            return s.Hello();\n        }\n        public int Stays(object o)\n        {\n            while (o is Foo s) { return 1; }\n            return s.Hello();\n        }\n    }\n}\n'},
     [('Checker.Leaks', 'calls', 'Foo.Hello'), ('Checker.Stays', 'calls', 'Other.Hello')], [('Checker.Leaks', 'calls', 'Other.Hello'), ('Checker.Stays', 'calls', 'Foo.Hello')],
     source='csharpstandard basic-concepts.md §7.7, statements.md §13.8.2; Roslyn LocalScopeBinder.BuildLocals (ExpressionVariableFinder: if / switch leak, while does not)')
case('csharp', 'foreach-project-ienumerable', '`foreach (var it in bag)` where Bag : IEnumerable<Item> makes it an Item',
     CS, {'A.cs': 'using System.Collections;\nusing System.Collections.Generic;\nnamespace App\n{\n    public class Item { public int A() { return 1; } }\n    public class Bag : IEnumerable<Item>\n    {\n        public IEnumerator<Item> GetEnumerator() { return null; }\n        IEnumerator IEnumerable.GetEnumerator() { return null; }\n    }\n    public class C\n    {\n        public int Run(Bag bag)\n        {\n            int n = 0;\n            foreach (var it in bag) { n += it.A(); }\n            return n;\n        }\n    }\n}\n'},
     [('C.Run', 'calls', 'Item.A')], [],
     source='csharpstandard statements.md §13.9.5.2; Roslyn ForEachLoopBinder')
case('csharp', 'foreach-extension-getenumerator', "foreach over a type whose only GetEnumerator is an extension method types the variable by that enumerator's Current",
     CS, {'A.cs': 'namespace App\n{\n    public class Item { public int A() { return 1; } }\n    public class BagEnumerator\n    {\n        public Item Current { get { return null; } }\n        public bool MoveNext() { return false; }\n    }\n    public class Bag { }\n    public static class BagExt\n    {\n        public static BagEnumerator GetEnumerator(this Bag b) { return new BagEnumerator(); }\n    }\n    public class C\n    {\n        public int Run(Bag bag)\n        {\n            int n = 0;\n            foreach (var it in bag) { n += it.A(); }\n            return n;\n        }\n    }\n}\n'},
     [('C.Run', 'calls', 'Item.A')], [],
     source='csharpstandard statements.md §13.9.5.2 (C# 9 extension GetEnumerator); Roslyn ForEachLoopBinder.TryGetGetEnumeratorInfo', since='9.0')
case('csharp', 'foreach-deconstruct', "the variables of a deconstructing foreach have the types of Deconstruct's out parameters",
     CS, {'A.cs': 'namespace App\n{\n    public class Alpha { public int Go() { return 1; } }\n    public class Beta { public int Go() { return 2; } }\n    public class Pair { public void Deconstruct(out Alpha a, out Beta b) { a = null; b = null; } }\n    public class C\n    {\n        public int Run(Pair[] pairs)\n        {\n            int n = 0;\n            foreach (var (a, b) in pairs) { n += a.Go(); }\n            return n;\n        }\n    }\n}\n'},
     [('C.Run', 'calls', 'Alpha.Go')], [('C.Run', 'calls', 'Beta.Go')],
     source='csharpstandard statements.md §13.9.5.4; Roslyn ForEachLoopBinder (deconstruction iteration variable)', since='7.0')
case('csharp', 'catch-variable-type', '`catch (AppError e) when (e.Code > 0) { e.Log(); }`: e is an AppError in the filter and block, the field e after them',
     CS, {'A.cs': 'namespace App\n{\n    public class AppError : System.Exception { public int Code; public int Log() { return 1; } }\n    public class Other { public int Log() { return 2; } public int Code; }\n    public class C\n    {\n        public Other e = new Other();\n        public int Run()\n        {\n            try { return 0; }\n            catch (AppError e) when (e.Code > 0) { return e.Log(); }\n        }\n        public int After() { return e.Log(); }\n    }\n}\n'},
     [('C.Run', 'calls', 'AppError.Log'), ('C.Run', 'reads', 'AppError.Code'), ('C.After', 'calls', 'Other.Log')], [('C.Run', 'calls', 'Other.Log'), ('C.Run', 'reads', 'Other.Code'), ('C.After', 'calls', 'AppError.Log')],
     source='csharpstandard statements.md §13.11; Roslyn Binder_Statements.cs BindCatchBlock, CatchClauseBinder')
case('csharp', 'using-resource-variable', '`using (var r = new Res())` and `using var r = new Res();` declare r as a Res, hiding the field r',
     CS, {'A.cs': 'namespace App\n{\n    public class Res : System.IDisposable { public void Dispose() { } public int Run() { return 1; } }\n    public class Other { public int Run() { return 2; } }\n    public class C\n    {\n        public Other r = new Other();\n        public int Stmt() { using (var r = new Res()) { return r.Run(); } }\n        public int Decl() { using var r = new Res(); return r.Run(); }\n        public int Field() { return r.Run(); }\n    }\n}\n'},
     [('C.Stmt', 'calls', 'Res.Run'), ('C.Decl', 'calls', 'Res.Run'), ('C.Field', 'calls', 'Other.Run')], [('C.Stmt', 'calls', 'Other.Run'), ('C.Decl', 'calls', 'Other.Run')],
     source='csharpstandard statements.md §13.14.1, §13.14.2; Roslyn Binder_Statements.cs BindUsingStatementOrDeclarationParts', since='8.0')
case('csharp', 'foreach-variable-scope', 'the iteration variable of a foreach is a name of the loop only; after it the name is the member again',
     CS, {'A.cs': 'using System;\nusing System.Collections.Generic;\nusing System.Threading.Tasks;\nnamespace App\n{\n    public class Repo\n    {\n        public int Find() { return 1; }\n    }\n\n    public class Other\n    {\n        public int Find() { return 2; }\n    }\n\n    public class A\n    {\n        Other r;\n\n        public void Run(List<Repo> repos)\n        {\n            foreach (var r in repos)\n            {\n            }\n            r.Find();\n        }\n    }\n}\n'},
     [('A.Run', 'calls', 'Other.Find')], [('A.Run', 'calls', 'Repo.Find')],
     source="csharpstandard §13.9 foreach statement; Roslyn Binder_Statements.cs BindForEach (the iteration variable is declared in the loop's own scope)")
case('csharp', 'foreach-pattern-before-interface', "a public GetEnumerator method of the collection type wins over its IEnumerable<T> implementation: the iteration variable is the pattern enumerator's Current",
     CS, {'A.cs': 'using System;\nusing System.Collections.Generic;\nusing System.Threading.Tasks;\nnamespace App\n{\n    public class Repo\n    {\n        public int Find() { return 1; }\n    }\n\n    public class Other\n    {\n        public int Find() { return 2; }\n    }\n\n    public class BagEnum\n    {\n        public Repo Current { get { return null; } }\n        public bool MoveNext() { return false; }\n    }\n\n    public class Mixed : IEnumerable<Other>\n    {\n        public BagEnum GetEnumerator() { return null; }\n        IEnumerator<Other> IEnumerable<Other>.GetEnumerator() { return null; }\n        System.Collections.IEnumerator System.Collections.IEnumerable.GetEnumerator() { return null; }\n    }\n\n    public class A\n    {\n        public void Run(Mixed m)\n        {\n            foreach (var x in m)\n            {\n                x.Find();\n            }\n        }\n    }\n}\n'},
     [('A.Run', 'calls', 'Repo.Find')], [('A.Run', 'calls', 'Other.Find')],
     source='csharpstandard §13.9.5.1 foreach (collection type determination); Roslyn Binder_Statements.cs GetEnumeratorInfoAndInferCollectionElementType (pattern before interface)')
case('csharp', 'await-foreach-variable', 'the iteration variable of an `await foreach` over IAsyncEnumerable<T> is a T',
     CS, {'A.cs': 'using System;\nusing System.Collections.Generic;\nusing System.Threading.Tasks;\nnamespace App\n{\n    public class Repo\n    {\n        public int Find() { return 1; }\n    }\n\n    public class Other\n    {\n        public int Find() { return 2; }\n    }\n\n    public class Feed\n    {\n        public async IAsyncEnumerable<Repo> Items()\n        {\n            await Task.Yield();\n            yield return null;\n        }\n    }\n\n    public class A\n    {\n        public async Task Run(Feed f)\n        {\n            await foreach (var x in f.Items())\n            {\n                x.Find();\n            }\n        }\n    }\n}\n'},
     [('A.Run', 'calls', 'Repo.Find')], [('A.Run', 'calls', 'Other.Find')],
     source='csharpstandard §13.9.5 asynchronous foreach; Roslyn Binder_Statements.cs BindForEach (isAsync: GetAsyncEnumerator)')
case('csharp', 'deconstruction-declaration-types', "in `var (a, b) = p;` a and b are typed by the out parameters of p's Deconstruct method",
     CS, {'A.cs': 'using System;\nusing System.Collections.Generic;\nusing System.Threading.Tasks;\nnamespace App\n{\n    public class Repo\n    {\n        public int Find() { return 1; }\n    }\n\n    public class Other\n    {\n        public int Find() { return 2; }\n    }\n\n    public class Pair\n    {\n        public void Deconstruct(out Repo a, out Other b) { a = null; b = null; }\n    }\n\n    public class A\n    {\n        public void Run(Pair p)\n        {\n            var (a, b) = p;\n            a.Find();\n        }\n    }\n}\n'},
     [('A.Run', 'calls', 'Repo.Find')], [('A.Run', 'calls', 'Other.Find')],
     source='csharpstandard §13.6.2 / §12.21.2 deconstruction; Roslyn Binder_Statements.cs BindDeconstruction (MakeDeconstructInvocationExpression)')
case('csharp', 'out-var-types-and-hides', '`s.TryGet(out var r)` declares r, typed by the out parameter, in the enclosing block; a later `r.Find()` is on that local, not on the field r',
     CS, {'A.cs': 'using System;\nusing System.Collections.Generic;\nusing System.Threading.Tasks;\nnamespace App\n{\n    public class Repo\n    {\n        public int Find() { return 1; }\n    }\n\n    public class Other\n    {\n        public int Find() { return 2; }\n    }\n\n    public class Source\n    {\n        public bool TryGet(out Repo r) { r = null; return true; }\n    }\n\n    public class A\n    {\n        Other r;\n\n        public void Run(Source s)\n        {\n            s.TryGet(out var r);\n            r.Find();\n        }\n    }\n}\n'},
     [('A.Run', 'calls', 'Repo.Find')], [('A.Run', 'calls', 'Other.Find')],
     source="csharpstandard §13.6 / §7.7 scopes of expression variables; Roslyn Binder_Statements.cs / ExpressionVariableFinder (out var declared in the enclosing statement's scope)")
case('csharp', 'is-not-pattern-variable', '`if (o is not Repo r) return;` leaves r, a Repo, in scope after the statement, hiding a field r',
     CS, {'A.cs': 'using System;\nusing System.Collections.Generic;\nusing System.Threading.Tasks;\nnamespace App\n{\n    public class Repo\n    {\n        public int Find() { return 1; }\n    }\n\n    public class Other\n    {\n        public int Find() { return 2; }\n    }\n\n    public class A\n    {\n        Other r;\n\n        public void Run(object o)\n        {\n            if (o is not Repo r) return;\n            r.Find();\n        }\n    }\n}\n'},
     [('A.Run', 'calls', 'Repo.Find')], [('A.Run', 'calls', 'Other.Find')],
     source='csharpstandard §11.2.1 / §13.8.2 pattern variables of an if condition leak into the block; Roslyn Binder_Patterns.cs BindDeclarationPattern under BindUnaryPattern')
case('csharp', 'attribute-suffix-omitted', '`[Mark]` means `MarkAttribute`, not a non-attribute class `Mark`',
     CS, {'A.cs': 'using System;\nnamespace App\n{\n    public class Mark\n    {\n    }\n\n    public class MarkAttribute : Attribute\n    {\n    }\n\n    [Mark]\n    public class Target\n    {\n    }\n}\n'},
     [('Target', 'references', 'MarkAttribute')], [('Target', 'references', 'Mark')],
     source='csharpstandard §23.3 (attribute_name resolution); Roslyn Binder_Lookup.LookupAttributeType')
case('csharp', 'attribute-named-argument-member', '`[Help(Topic = ..., Level = ...)]`: the names are members of the attribute class (own or inherited), not of the decorated type',
     CS, {'A.cs': 'using System;\nnamespace App\n{\n    public class BaseAttr : Attribute\n    {\n        public string Topic { get; set; }\n    }\n\n    public class HelpAttribute : BaseAttr\n    {\n        public string Level;\n    }\n\n    [Help(Topic = "t", Level = "l")]\n    public class Doc\n    {\n        public int Topic;\n        public int Level;\n    }\n}\n'},
     [('Doc', 'writes', 'BaseAttr.Topic'), ('Doc', 'writes', 'HelpAttribute.Level')], [('Doc', 'writes', 'Doc.Topic'), ('Doc', 'writes', 'Doc.Level'), ('Doc', 'reads', 'Doc.Topic'), ('Doc', 'reads', 'Doc.Level')],
     source='csharpstandard §23.2.3, §23.4.2; Roslyn Binder_Attributes.BindNamedAttributeArgumentName (LookupMembersWithFallback on the attribute type)')
case('csharp', 'attribute-verbatim-name', '`[@Example]` is the class `Example`, `[ExampleAttribute]` is `ExampleAttribute`, though both exist',
     CS, {'A.cs': 'using System;\nnamespace App\n{\n    public class Example : Attribute\n    {\n        public int Only1 { get; set; }\n    }\n\n    public class ExampleAttribute : Attribute\n    {\n        public int Only2 { get; set; }\n    }\n\n    [@Example(Only1 = 1)]\n    public class OnPlain\n    {\n    }\n\n    [ExampleAttribute(Only2 = 2)]\n    public class OnSuffixed\n    {\n    }\n}\n'},
     [('OnPlain', 'references', 'Example'), ('OnSuffixed', 'references', 'ExampleAttribute')], [('OnPlain', 'references', 'ExampleAttribute'), ('OnSuffixed', 'references', 'Example')],
     source='csharpstandard §23.3; Roslyn Binder_Lookup.LookupAttributeType (VerbatimNameAttributeTypeOnly)')
case('csharp', 'attribute-name-per-file-usings', '`[Mark]` means the `MarkAttribute` of the namespace its own file imports',
     CS, {'Marks.cs': 'using System;\nnamespace Left\n{\n    public class MarkAttribute : Attribute\n    {\n    }\n}\n\nnamespace Right\n{\n    public class MarkAttribute : Attribute\n    {\n    }\n}\n', 'A.cs': 'using Left;\nnamespace App\n{\n    [Mark]\n    public class OnLeft\n    {\n    }\n}\n', 'B.cs': 'using Right;\nnamespace App\n{\n    [Mark]\n    public class OnRight\n    {\n    }\n}\n'},
     [('OnLeft', 'references', 'Left.MarkAttribute'), ('OnRight', 'references', 'Right.MarkAttribute')], [('OnLeft', 'references', 'Right.MarkAttribute'), ('OnRight', 'references', 'Left.MarkAttribute')],
     source='csharpstandard §23.3, §7.8')
case('csharp', 'attribute-partial-parts-own-usings', "each part of a partial type decorates the type, its attribute name resolved by that part's usings",
     CS, {'Marks.cs': 'using System;\nnamespace Left\n{\n    public class MarkAttribute : Attribute\n    {\n    }\n}\n\nnamespace Right\n{\n    public class MarkAttribute : Attribute\n    {\n    }\n}\n\nnamespace Elsewhere\n{\n    public class MarkAttribute : Attribute\n    {\n    }\n}\n', 'A.cs': 'using Left;\nnamespace App\n{\n    [Mark]\n    public partial class P\n    {\n    }\n}\n', 'B.cs': 'using Right;\nnamespace App\n{\n    [Mark]\n    public partial class P\n    {\n    }\n}\n'},
     [('P', 'references', 'Left.MarkAttribute'), ('P', 'references', 'Right.MarkAttribute')], [('P', 'references', 'Elsewhere.MarkAttribute')],
     source='csharpstandard §23.3 (attributes of a type declared in multiple parts)')
case('csharp', 'assembly-attribute', '`[assembly: Stamp]` means the `StampAttribute` of the namespace the file imports (the file is its source)',
     CS, {'Marks.cs': 'using System;\nnamespace Marks\n{\n    public class StampAttribute : Attribute\n    {\n    }\n}\n\nnamespace Elsewhere\n{\n    public class StampAttribute : Attribute\n    {\n    }\n}\n', 'A.cs': 'using Marks;\n[assembly: Stamp]\nnamespace App\n{\n    public class X\n    {\n    }\n}\n'},
     [('A.cs', 'references', 'Marks.StampAttribute')], [('A.cs', 'references', 'Elsewhere.StampAttribute')],
     source='csharpstandard §23.3 (global_attribute_section)')
case('csharp', 'attribute-target-specifier', '`[return: Mark]`, `[type: Mark]`, `[field: Mark]` and a parameter attribute name the same attribute class, on the declaration they are written on',
     CS, {'A.cs': 'using System;\nnamespace App\n{\n    public class Mark\n    {\n    }\n\n    public class MarkAttribute : Attribute\n    {\n    }\n\n    [type: Mark]\n    public class Holder\n    {\n        [return: Mark]\n        public int M() { return 1; }\n\n        [field: Mark]\n        public int P { get; set; }\n\n        public void N([Mark] int x) { }\n    }\n}\n'},
     [('Holder', 'references', 'MarkAttribute'), ('Holder.M', 'references', 'MarkAttribute'), ('Holder.P', 'references', 'MarkAttribute'), ('Holder.N', 'references', 'MarkAttribute')], [('Holder', 'references', 'Mark'), ('Holder.M', 'references', 'Mark'), ('Holder.P', 'references', 'Mark'), ('Holder.N', 'references', 'Mark')],
     source='csharpstandard §23.3 (attribute_target_specifier); Roslyn Binder_Attributes (attributes filtered by MatchAttributeTarget, then bound alike)')
case('csharp', 'attribute-argument-scope-member', "`[Limit(Limit)]` on a method or on the class itself: the argument is the class's own constant, not one a `using static` brings",
     CS, {'A.cs': 'using System;\nusing static App.Other;\nnamespace App\n{\n    public static class Other\n    {\n        public const int Limit = 5;\n    }\n\n    public class LimitAttribute : Attribute\n    {\n        public LimitAttribute(string s) { }\n    }\n\n    public class Svc\n    {\n        public const string Limit = "one";\n\n        [Limit(Limit)]\n        public void Run() { }\n    }\n\n    [Limit(Limit)]\n    public class Plain\n    {\n        public const string Limit = "two";\n    }\n}\n'},
     [('Svc.Run', 'reads', 'Svc.Limit'), ('Plain', 'reads', 'Plain.Limit')], [('Svc.Run', 'reads', 'Other.Limit'), ('Plain', 'reads', 'Other.Limit')],
     source='csharpstandard §23.3, §12.8.4; Roslyn Binder_Attributes.BindAttributeArguments (binder of the attributed symbol, ContextualAttributeBinder)')
case('csharp', 'attribute-constructor', '`[Help("x")]` calls `HelpAttribute`\'s constructor, not its base attribute\'s',
     CS, {'A.cs': 'using System;\nnamespace App\n{\n    public class BaseAttr : Attribute\n    {\n        public BaseAttr(string s) { }\n    }\n\n    public class HelpAttribute : BaseAttr\n    {\n        public HelpAttribute(string s) : base(s) { }\n    }\n\n    [Help("x")]\n    public class Doc\n    {\n    }\n}\n'},
     [('Doc', 'calls', 'HelpAttribute.HelpAttribute')], [('Doc', 'calls', 'BaseAttr.BaseAttr')],
     source='csharpstandard §23.4.2; Roslyn Binder_Attributes.BindAttributeCore (TryPerformConstructorOverloadResolution)')
case('csharp', 'attribute-lookup-skips-non-attribute', 'a class `Mark` that is no attribute is not what `[Mark]` means when `MarkAttribute` is declared in an outer namespace',
     CS, {'A.cs': 'using System;\nnamespace App\n{\n    public class MarkAttribute : Attribute\n    {\n    }\n\n    namespace Inner\n    {\n        public class Mark\n        {\n        }\n\n        [Mark]\n        public class Target\n        {\n        }\n    }\n}\n'},
     [('Target', 'references', 'MarkAttribute')], [('Target', 'references', 'Inner.Mark')],
     source='Roslyn Binder_Lookup.LookupAttributeType / IsSingleViableAttributeType')
case('csharp', 'csharp-attribute-argument-uses', "typeof(T) and a constant named in an attribute's arguments are uses, from the decorated method, of that type and constant",
     CS, {'A.cs': 'using System;\nnamespace App {\n  public class Target { }\n  public class Other { }\n  public static class Consts { public const string Name = "n"; }\n  public class InfoAttribute : Attribute {\n    public InfoAttribute(Type t, string s) { }\n  }\n  public class Holder {\n    [Info(typeof(Target), Consts.Name)]\n    public void Run() { }\n  }\n}\n'},
     [('Holder.Run', 'references', 'Target'), ('Holder.Run', 'reads', 'Consts.Name')], [('Holder.Run', 'references', 'Other')],
     source='csharpstandard §23.3 (attribute arguments are expressions); Binder_Attributes.cs (BindAttributeArguments)')
case('csharp', 'attribute-nested-class-shadows-outer', 'an attribute on a type is looked up in the scope of that type: its nested attribute class hides the outer one of the same name',
     CS, {'A.cs': 'using System;\nnamespace App {\n  public class MarkAttribute : Attribute { public MarkAttribute(int n) {} }\n  [Mark("x")] public class Host {\n    public class MarkAttribute : Attribute { public MarkAttribute(string s) {} }\n    [Mark("y")] public void M() {}\n  }\n  [Mark(1)] public class Other {}\n}\n'},
     [('Host', 'references', 'Host.MarkAttribute'), ('Host.M', 'references', 'Host.MarkAttribute'), ('Other', 'references', 'A.cs:MarkAttribute')], [('Host', 'references', 'A.cs:MarkAttribute'), ('Host.M', 'references', 'A.cs:MarkAttribute')],
     source="csharpstandard §23.3; Roslyn Binder_Attributes.cs (the attributed symbol's binder); compiled: net8.0 builds with the nested constructor taking string")
case('csharp', 'attribute-using-alias', 'an attribute written with a using alias name means the aliased attribute class',
     CS, {'A.cs': 'using System;\nnamespace App {\n  public class StampAttribute : Attribute { }\n  public class Alias : Attribute { }\n}\nnamespace Use {\n  using Alias = App.StampAttribute;\n  [Alias] public class Target { }\n}\n'},
     [('Target', 'references', 'StampAttribute')], [('Target', 'references', 'Alias')],
     source='csharpstandard §23.3 (attribute_name is a type_name; using alias)')
case('csharp', 'attribute-qualified-name', 'a namespace-qualified attribute name finds the suffixed class in that namespace only',
     CS, {'A.cs': 'using System;\nnamespace One { public class MarkAttribute : Attribute { } }\nnamespace Two { public class MarkAttribute : Attribute { } }\nnamespace App {\n  [One.Mark] public class A { }\n  [global::Two.Mark] public class B { }\n}\n'},
     [('A', 'references', 'One.MarkAttribute'), ('B', 'references', 'Two.MarkAttribute')], [('A', 'references', 'Two.MarkAttribute'), ('B', 'references', 'One.MarkAttribute')],
     source='csharpstandard §23.3; Roslyn Binder_Attributes.cs (BindAttributeType, qualified name)')
case('csharp', 'attribute-generic-arity', "a generic attribute's type arguments choose the class of that arity, not the non-generic class of the same name",
     CS, {'A.cs': 'using System;\nnamespace App {\n  public class MarkAttribute : Attribute { }\n  public class MarkAttribute<T> : Attribute { }\n  public class Item { }\n  [Mark<Item>] public class Target { }\n  [Mark] public class Plain { }\n}\n'},
     [('Target', 'references', 'MarkAttribute<T>'), ('Target', 'references', 'Item'), ('Plain', 'references', 'MarkAttribute')], [('Target', 'references', 'MarkAttribute')],
     source='csharpstandard §23.2 (generic attribute classes since C# 11); Roslyn Binder_Attributes.cs', since='11')

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


# Found comparing the two engines again before graphify goes (compare_engines.py
# on the 31 audit repositories, graphify's EXTRACTED calls the native graph
# lacked, read at their sites): faraday's `Response#to_hash`.
found_by("comparison")
case("ruby", "bare-name-as-a-hash-value", "a bare name that is no local is a method call as a hash's value too (`{ status: status }`); the key names nothing",
     RB, {"a.rb": "class Resp\n  def status\n    1\n  end\n\n  def body\n    2\n  end\n\n  def to_hash\n    { status: status, :body => body }\n  end\nend\n\nclass Other\n  def status\n    3\n  end\nend\n"},
     [("Resp.to_hash", "calls", "Resp.status"), ("Resp.to_hash", "calls", "Resp.body")], [("Resp.to_hash", "calls", "Other.status")],
     source="Ruby documentation 'Calling Methods' (a method called with no receiver and no arguments needs no parentheses); faraday lib/faraday/response.rb to_hash")

# Found by profiling the engine on kubernetes: a generated deepcopy function
# shadows `in` and `out` in every `if` block (`in, out := &in.X, &out.X`); the
# value of each is written where it is declared, where the earlier blocks'
# locals are out of scope and `in` is the receiver.
found_by("profile")
GO_SPEC = "The Go Programming Language Specification, 'Declarations and scope'"
case("go", "local-value-is-read-where-it-is-declared", "a local's value names what is in scope where the local is declared: `in := in.B` in a later block is the receiver's field, not the earlier block's `in`",
     GO_SPEC, {"go.mod": "module example.com/app\n\ngo 1.22\n",
               "app/a.go": "package app\n\ntype Inner struct{}\n\nfunc (i *Inner) Copy() {}\n\n"
                           "type Leaf struct{}\n\nfunc (l *Leaf) Copy() {}\n\n"
                           "type Outer struct {\n\tA *Leaf\n\tB *Inner\n}\n\n"
                           "func (in *Outer) Copy() {\n\tif in.A != nil {\n\t\tin := in.A\n\t\tin.Copy()\n\t}\n"
                           "\tif in.B != nil {\n\t\tin := in.B\n\t\tin.Copy()\n\t}\n}\n"},
     [("Outer.Copy", "calls", "Inner.Copy"), ("Outer.Copy", "calls", "Leaf.Copy")], [("Outer.Copy", "calls", "Outer.Copy")],
     source="go/types: a local's scope begins after its ShortVarDecl (Scope.Insert in assignment.go shortVarDecl)")


# the SCIP indexer of a language, and what a case's project needs for it
SCIP_GO_MOD = "module example.com/conformance\n\ngo 1.22\n"
SCIP_CSPROJ = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <OutputType>Library</OutputType>
    <LangVersion>preview</LangVersion>
    <Nullable>enable</Nullable>
  </PropertyGroup>
</Project>
"""


def _scip_tool(name: str) -> str | None:
    found = shutil.which(name)
    for d in (Path.home() / "go" / "bin", Path.home() / ".dotnet" / "tools"):
        if not found and (d / name).exists():
            found = str(d / name)
    return found


def _scip_index(lang: str, root: Path) -> str | None:
    """Run the language's indexer on a case's project: None, or why not."""
    if lang == "go":
        tool = _scip_tool("scip-go")
        if not (root / "go.mod").exists():
            (root / "go.mod").write_text(SCIP_GO_MOD, encoding="utf-8")
        argv = [tool, "--output", "index.scip"] if tool else []
    elif lang == "csharp":
        tool = _scip_tool("scip-dotnet")
        if not any(root.rglob("*.csproj")):
            (root / "Case.csproj").write_text(SCIP_CSPROJ, encoding="utf-8")
        argv = [tool, "index", "--output", "index.scip"] if tool else []
    else:
        return f"no SCIP indexer for {lang} here"
    if not argv:
        return f"the {lang} indexer is not installed"
    done = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=600)
    if not (root / "index.scip").exists():
        return "the indexer failed: " + (done.stderr or done.stdout).strip()[-200:]
    return None


def _precise_edges(root: Path) -> list[dict[str, Any]]:
    """The edges graphlore's tools read in precise mode, the contradicted
    ones left out."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from graphlore import config, resolve, server
    config.PROJECT_DIR = root
    server._GRAPH_CACHE.clear()
    graph = server._load_graph()
    if isinstance(graph, str):
        return []
    nodes, edges = server._nodes_edges(graph)
    return [e for e in resolve._resolved(nodes, edges).edges if e.get("scip") != "contradicted"]


def _prepare(c: dict[str, Any], root: Path, out: Path) -> dict[str, Any] | str:
    """Write a case's project and build its graph: the graph, or an error."""
    for rel, src in c["files"].items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(src, encoding="utf-8")
    done = subprocess.run([sys.executable, "-m", "graphlore_engine", "build", str(root),
                           "--out", str(out)], capture_output=True, text=True)
    if done.returncode != 0 or not out.exists():
        return done.stderr.strip()[-200:] or "no graph"
    graph: dict[str, Any] = json.loads(out.read_text(encoding="utf-8"))
    return graph


def _judge(c: dict[str, Any], graph: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(graph, str):
        return {"id": c["id"], "ok": False, "missing": [], "unwanted": [], "error": graph}
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


def run_case(c: dict[str, Any]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        return _judge(c, _prepare(c, Path(tmp) / "project", Path(tmp) / "graph.json"))


def _run_scip(cases: list[dict[str, Any]], jobs: int) -> dict[str, dict[str, Any]]:
    def index(c: dict[str, Any]) -> tuple[str, dict[str, Any] | str]:
        tmp = tempfile.mkdtemp(prefix="conformance-")
        root = Path(tmp) / "project"
        graph = _prepare(c, root, root / "graphify-out" / "graph.json")
        if not isinstance(graph, str):
            why = _scip_index(c["lang"], root)
            if why:
                graph = why
        return tmp, graph

    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(jobs) as pool:
        for c, (tmp, graph) in zip(cases, pool.map(index, cases), strict=True):
            try:
                if not isinstance(graph, str):
                    graph["links"] = _precise_edges(Path(tmp) / "project")
                results[c["id"]] = _judge(c, graph)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--only", default="", help="comma-separated languages or case ids")
    ap.add_argument("--failed", action="store_true", help="print only the cases that fail")
    ap.add_argument("--json", help="write the result of every case here")
    ap.add_argument("--baseline", help="an earlier --json: say what changed")
    ap.add_argument("--markdown", action="store_true", help="print the table as Markdown")
    ap.add_argument("--scip", action="store_true",
                    help="read each case in precise mode, with the language's SCIP indexer")
    ap.add_argument("--jobs", type=int, default=1, help="indexers to run at once (--scip)")
    args = ap.parse_args()
    only = {x for x in args.only.split(",") if x}
    cases = [c for c in CASES if not only or c["lang"] in only or c["id"] in only]
    if args.scip:
        # the indexers run in parallel; graphlore reads each index in turn
        # (its project directory is one setting of the process)
        results = _run_scip(cases, max(1, args.jobs))
    else:
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
                   for h in ("specification", "compiler", "comparison", "grammar", "source", "sweep", "profile")}
            read = sum(1 for c in mine if c["source"])
            print(f"\n### {lang} ({passed} of {len(mine)})\n\n{mine[0]['where']}.\n")
            print(f"Found by: the specification {how['specification']}, a compiler {how['compiler']}, "
                  f"comparison {how['comparison']}, the grammar {how['grammar']}, "
                  f"the language's own tools {how['source']}"
                  + (f", a sweep of them {how['sweep']}" if how["sweep"] else "")
                  + (f", profiling the engine {how['profile']}" if how["profile"] else "") + ". "
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

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"passed": total, "total": len(results), "cases": list(results.values())}, indent=1)
            + "\n", encoding="utf-8")
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
