# ruff: noqa: E501
"""Are the engine's name tables read only where the language means them?

`engine/src/` keeps tables of names that mean something in one language's
library: wrappers (`Box`, `Optional`), methods of collections (`map`,
`first`), unwrappers (`unwrap`, `get`), core methods (`toString`), receiver
words (`self`, `base`, `parent`). The reader and the resolver are shared by
the 14 languages, so a table consulted without asking which language is
being read makes a project's own `Box<T>` a wrapper in Java, or drops the
`$` of a TypeScript name because PHP writes variables with one.

This script finds such leaks by differential testing, not by reading the
code: for every name of every table, and for every language, it builds the
same small project twice, once with a neutral name (`zorp`, `Zorp`) and once
with the table's name, and compares the edges. The project always *defines*
the name itself, on a typed receiver, so whatever a library means by it, the
project's own definition is what the source names:

    method   two classes each with a method NAME; `run(r: Repo)` calls
             `r.NAME()`: an edge to `Repo.NAME`, none to `Other.NAME`
    self     a class whose `run` calls its own `NAME`
    function a function NAME called bare
    type     a generic class NAME<T> with a member; `run(b: NAME<Item>)`
             calls `b.lead().size()`: `NAME.lead` and `Item.size`

A row is printed where the control passes and the name does not. A name the
language reserves (`default`, `in`) does not parse: the row says so and is
not counted.

    python benchmarks/neutrality.py                       # every table, every language
    python benchmarks/neutrality.py --only java,kotlin    # some languages
    python benchmarks/neutrality.py --tables WRAPPERS,UNWRAPPERS
    python benchmarks/neutrality.py --json out.json --baseline benchmarks/results-neutrality.json
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import conformance  # noqa: E402

ENGINE = Path(__file__).resolve().parent.parent / "engine" / "src"
SOURCES = ["refs.rs", "resolve/mod.rs", "state.rs", "lang/ecma.rs", "lang/go.rs", "lang/python.rs",
           "lang/ruby.rs"]
# tables of node kinds, hosts, directories: no names a project could define
NOT_NAMES = {"NAME_KINDS", "SELF_KINDS", "SUPER_KINDS", "TYPE_KINDS", "VALUE_FIELDS",
             "DECORATOR_KINDS", "UNTYPED_RECEIVER_REASONS", "ASSIGNMENTS", "BARE_KINDS",
             "DECLARATIONS", "MEMBER_KINDS", "PASS_THROUGH", "TARGET_LISTS", "UPDATES"}
TYPE_TABLES = {"WRAPPERS", "SEALED", "RUST_WRAPPERS", "COLLECTIONS", "MAPS", "MARKER_BASES",
               "WRAPPERS_OF_WHAT_THEY_ARE_GIVEN"}
# words the reader treats specially where it sees them as a name
WORDS = ["this", "self", "cls", "base", "parent", "static", "init", "create", "of", "from",
         "build", "make", "builder", "instance", "shared", "value", "get", "set", "apply", "also",
         "let", "run", "with", "new_", "Type", "Protocol", "class_", "it", "invoke", "call",
         "default", "empty", "none", "some", "ok", "err"]


# shapes of names, and words other languages reserve: what one language's rule
# about spelling (a sigil, a capital, a keyword) would do to another's names
SHAPES = ["$zorp", "$Zorp", "_zorp", "zorp_", "__zorp__", "Zorp", "ZORP", "zorp2", "zOrp",
          "constructor", "initialize", "__init__", "__call__", "init", "deinit", "new", "fn",
          "def", "func", "fun", "let", "val", "var", "end", "type", "module", "package",
          "namespace", "import", "use", "require", "include", "extend", "async", "await",
          "yield", "lambda", "delegate", "object", "companion", "it", "args", "kwargs", "main",
          "test", "describe", "expect", "is", "as", "in", "to", "do", "then", "when", "where",
          "match", "case", "typeof", "sizeof", "print", "puts", "echo", "len", "size", "count",
          "length", "id", "name", "data", "list", "map", "string", "int", "error", "result"]


def tables() -> dict[str, list[str]]:
    """Every `const NAME: &[&str] = &[…]` of the shared sources, by name."""
    out: dict[str, list[str]] = {}
    for rel in SOURCES:
        text = (ENGINE / rel).read_text(encoding="utf-8")
        for m in re.finditer(r"const ([A-Z_]+): &\[&str\] =\s*&\[(.*?)\];", text, re.S):
            name, body = m.group(1), m.group(2)
            if name in NOT_NAMES:
                continue
            names = re.findall(r'"([^"\\]+)"', body)
            key = name if name not in out else f"{name}@{rel}"
            out[key] = names
    out["WORDS"] = WORDS
    out["SHAPES"] = SHAPES
    return out


IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*[?!]?$")
DOLLAR = re.compile(r"^\$[A-Za-z_][A-Za-z0-9_]*$")  # a name in JS / TS
# names the language reserves: a project cannot define them, so they say nothing
RESERVED = {
    "python": "and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield None True False cls self print type",
    "javascript": "await break case catch class const continue debugger default delete do else export extends finally for function if import in instanceof new return super switch this throw try typeof var void while with yield let static enum null true false",
    "go": "break case chan const continue default defer else fallthrough for func go goto if import interface map package range return select struct switch type var new make len cap append copy delete panic recover print println close init",
    "java": "abstract assert boolean break byte case catch char class const continue default do double else enum extends final finally float for goto if implements import instanceof int interface long native new package private protected public return short static strictfp super switch synchronized this throw throws transient try void volatile while var record yield",
    "kotlin": "as break class continue do else false for fun if in interface is null object package return super this throw true try typealias typeof val var when while init",
    "scala": "abstract case catch class def do else extends false final finally for forSome if implicit import lazy match new null object override package private protected return sealed super this throw trait try true type val var while with yield given then",
    "csharp": "abstract as base bool break byte case catch char checked class const continue decimal default delegate do double else enum event explicit extern false finally fixed float for foreach goto if implicit in int interface internal is lock long namespace new null object operator out override params private protected public readonly ref return sbyte sealed short sizeof stackalloc static string struct switch this throw true try typeof uint ulong unchecked unsafe ushort using virtual void volatile while await async",
    "rust": "as async await break const continue crate dyn else enum extern false fn for if impl in let loop match mod move mut pub ref return self Self static struct super trait true type unsafe use where while abstract become box do final macro override priv try typeof unsized virtual yield",
    "swift": "associatedtype class deinit enum extension fileprivate func import init inout internal let open operator private protocol public rethrows static struct subscript typealias var break case continue default defer do else fallthrough for guard if in repeat return switch where while as catch false is nil super self Self throw throws true try await Type Protocol some any each",
    "c": "auto break case char const continue default do double else enum extern float for goto if inline int long register restrict return short signed sizeof static struct switch typedef union unsigned void volatile while",
    "cpp": "auto break case char const continue default do double else enum extern float for goto if inline int long register return short signed sizeof static struct switch typedef union unsigned void volatile while class delete new this throw try catch namespace template typename using virtual public private protected operator friend explicit export bool true false and or not xor union final override concept requires",
    "ruby": "alias and begin break case class def defined do else elsif end ensure false for if in module next nil not or redo rescue retry return self super then true undef unless until when while yield new initialize",
    "php": "abstract and array as break callable case catch class clone const continue declare default do echo else elseif empty enddeclare endfor endforeach endif endswitch endwhile eval exit extends final finally fn for foreach function global goto if implements include instanceof insteadof interface isset list match namespace new or print private protected public readonly require return static switch throw trait try unset use var while xor yield parent self static this",
}
RESERVED["typescript"] = RESERVED["javascript"] + " type interface namespace declare abstract"
RESERVED_SETS = {k: set(v.split()) for k, v in RESERVED.items()}


def usable(name: str, lang: str, test: str) -> bool:
    # (PHP's keywords and function names are read without regard to case)
    word = name.lower() if lang == "php" else name
    if DOLLAR.match(name):
        return lang in ("javascript", "typescript")
    if not IDENT.match(name) or word in RESERVED_SETS.get(lang, set()):
        return False
    # (a Ruby method named in capitals is called like a constant is read)
    if lang == "ruby" and test != "type" and name[:1].isupper():
        return False
    return lang == "ruby" or not name.endswith(("?", "!"))


# --- the projects: (files, expect, absent), per test and language --------------
GOMOD = "module example.com/app\n\ngo 1.21\n"
CARGO = "[package]\nname = \"demo\"\nversion = \"0.1.0\"\n"


def method(lang: str, m: str) -> tuple[dict[str, str], list, list] | None:
    both = ([("run", "calls", f"Repo.{m}")], [("run", "calls", f"Other.{m}")])
    if lang == "python":
        return ({"a.py": f"class Repo:\n    def {m}(self):\n        return 1\n\n\nclass Other:\n    def {m}(self):\n        return 2\n\n\ndef run(r: Repo):\n    return r.{m}()\n"}, *both)
    if lang == "javascript":
        return ({"a.js": f"class Repo {{\n  {m}() {{\n    return 1\n  }}\n}}\n\nclass Other {{\n  {m}() {{\n    return 2\n  }}\n}}\n\nexport function run() {{\n  const r = new Repo()\n  return r.{m}()\n}}\n"}, *both)
    if lang == "typescript":
        return ({"a.ts": f"export class Repo {{\n  {m}(): number {{\n    return 1\n  }}\n}}\n\nexport class Other {{\n  {m}(): number {{\n    return 2\n  }}\n}}\n\nexport function run(r: Repo): number {{\n  return r.{m}()\n}}\n"}, *both)
    if lang == "go":
        return ({"go.mod": GOMOD, "a.go": f"package app\n\ntype Repo struct{{}}\n\nfunc (r *Repo) {m}() int {{\n\treturn 1\n}}\n\ntype Other struct{{}}\n\nfunc (o *Other) {m}() int {{\n\treturn 2\n}}\n\nfunc run(r *Repo) int {{\n\treturn r.{m}()\n}}\n"}, *both)
    if lang == "java":
        return ({"app/A.java": f"package app;\n\nclass Repo {{\n    int {m}() {{\n        return 1;\n    }}\n}}\n\nclass Other {{\n    int {m}() {{\n        return 2;\n    }}\n}}\n\npublic class A {{\n    int run(Repo r) {{\n        return r.{m}();\n    }}\n}}\n"},
                [("A.run", "calls", f"Repo.{m}")], [("A.run", "calls", f"Other.{m}")])
    if lang == "kotlin":
        return ({"A.kt": f"package app\n\nclass Repo {{\n    fun {m}(): Int = 1\n}}\n\nclass Other {{\n    fun {m}(): Int = 2\n}}\n\nfun run(r: Repo): Int = r.{m}()\n"}, *both)
    if lang == "scala":
        return ({"A.scala": f"package app\n\nclass Repo {{\n  def {m}(): Int = 1\n}}\n\nclass Other {{\n  def {m}(): Int = 2\n}}\n\nobject Main {{\n  def run(r: Repo): Int = r.{m}()\n}}\n"},
                [("Main.run", "calls", f"Repo.{m}")], [("Main.run", "calls", f"Other.{m}")])
    if lang == "csharp":
        return ({"A.cs": f"namespace App\n{{\n    public class Repo\n    {{\n        public int {m}() {{ return 1; }}\n    }}\n\n    public class Other\n    {{\n        public int {m}() {{ return 2; }}\n    }}\n\n    public class A\n    {{\n        public int Run(Repo r)\n        {{\n            return r.{m}();\n        }}\n    }}\n}}\n"},
                [("A.Run", "calls", f"Repo.{m}")], [("A.Run", "calls", f"Other.{m}")])
    if lang == "rust":
        return ({"Cargo.toml": CARGO, "src/lib.rs": f"pub struct Repo;\n\nimpl Repo {{\n    pub fn {m}(&self) -> i32 {{\n        1\n    }}\n}}\n\npub struct Other;\n\nimpl Other {{\n    pub fn {m}(&self) -> i32 {{\n        2\n    }}\n}}\n\npub fn run(r: &Repo) -> i32 {{\n    r.{m}()\n}}\n"}, *both)
    if lang == "swift":
        return ({"a.swift": f"class Repo {{\n  func {m}() -> Int {{\n    return 1\n  }}\n}}\n\nclass Other {{\n  func {m}() -> Int {{\n    return 2\n  }}\n}}\n\nfunc run(r: Repo) -> Int {{\n  return r.{m}()\n}}\n"}, *both)
    if lang == "cpp":
        return ({"a.cc": f"class Repo {{\n public:\n  int {m}() {{\n    return 1;\n  }}\n}};\n\nclass Other {{\n public:\n  int {m}() {{\n    return 2;\n  }}\n}};\n\nint run(Repo& r) {{\n  return r.{m}();\n}}\n"}, *both)
    if lang == "ruby":
        return ({"a.rb": f"class Repo\n  def {m}\n    1\n  end\nend\n\nclass Other\n  def {m}\n    2\n  end\nend\n\ndef run\n  r = Repo.new\n  r.{m}\nend\n"}, *both)
    if lang == "php":
        return ({"a.php": f"<?php\nclass Repo\n{{\n    public function {m}(): int\n    {{\n        return 1;\n    }}\n}}\n\nclass Other\n{{\n    public function {m}(): int\n    {{\n        return 2;\n    }}\n}}\n\nfunction run(Repo $r): int\n{{\n    return $r->{m}();\n}}\n"}, *both)
    return None


def own(lang: str, m: str) -> tuple[dict[str, str], list, list] | None:
    """A class whose `run` calls its own NAME."""
    e = [("A.run", "calls", f"A.{m}")]
    if lang == "python":
        return ({"a.py": f"class A:\n    def {m}(self):\n        return 1\n\n    def run(self):\n        return self.{m}()\n"}, e, [])
    if lang in ("javascript", "typescript"):
        ext = "js" if lang == "javascript" else "ts"
        return ({f"a.{ext}": f"export class A {{\n  {m}() {{\n    return 1\n  }}\n\n  run() {{\n    return this.{m}()\n  }}\n}}\n"}, e, [])
    if lang == "go":
        return ({"go.mod": GOMOD, "a.go": f"package app\n\ntype A struct{{}}\n\nfunc (a *A) {m}() int {{\n\treturn 1\n}}\n\nfunc (a *A) run() int {{\n\treturn a.{m}()\n}}\n"}, e, [])
    if lang == "java":
        return ({"app/A.java": f"package app;\n\npublic class A {{\n    int {m}() {{\n        return 1;\n    }}\n\n    int run() {{\n        return {m}();\n    }}\n}}\n"}, e, [])
    if lang == "kotlin":
        return ({"A.kt": f"package app\n\nclass A {{\n    fun {m}(): Int = 1\n\n    fun run(): Int = {m}()\n}}\n"}, e, [])
    if lang == "scala":
        return ({"A.scala": f"package app\n\nclass A {{\n  def {m}(): Int = 1\n\n  def run(): Int = {m}()\n}}\n"}, e, [])
    if lang == "csharp":
        return ({"A.cs": f"namespace App\n{{\n    public class A\n    {{\n        public int {m}() {{ return 1; }}\n\n        public int run()\n        {{\n            return {m}();\n        }}\n    }}\n}}\n"}, e, [])
    if lang == "rust":
        return ({"Cargo.toml": CARGO, "src/lib.rs": f"pub struct A;\n\nimpl A {{\n    pub fn {m}(&self) -> i32 {{\n        1\n    }}\n\n    pub fn run(&self) -> i32 {{\n        self.{m}()\n    }}\n}}\n"}, e, [])
    if lang == "swift":
        return ({"a.swift": f"class A {{\n  func {m}() -> Int {{\n    return 1\n  }}\n\n  func run() -> Int {{\n    return {m}()\n  }}\n}}\n"}, e, [])
    if lang == "cpp":
        return ({"a.cc": f"class A {{\n public:\n  int {m}() {{\n    return 1;\n  }}\n\n  int run() {{\n    return {m}();\n  }}\n}};\n"}, e, [])
    if lang == "ruby":
        return ({"a.rb": f"class A\n  def {m}\n    1\n  end\n\n  def run\n    {m}\n  end\nend\n"}, e, [])
    if lang == "php":
        return ({"a.php": f"<?php\nclass A\n{{\n    public function {m}(): int\n    {{\n        return 1;\n    }}\n\n    public function run(): int\n    {{\n        return $this->{m}();\n    }}\n}}\n"}, e, [])
    return None


def function(lang: str, m: str) -> tuple[dict[str, str], list, list] | None:
    """A function NAME, called bare."""
    e = [("run", "calls", m)]
    if lang == "python":
        return ({"a.py": f"def {m}():\n    return 1\n\n\ndef run():\n    return {m}()\n"}, e, [])
    if lang in ("javascript", "typescript"):
        ext = "js" if lang == "javascript" else "ts"
        return ({f"a.{ext}": f"function {m}() {{\n  return 1\n}}\n\nexport function run() {{\n  return {m}()\n}}\n"}, e, [])
    if lang == "go":
        return ({"go.mod": GOMOD, "a.go": f"package app\n\nfunc {m}() int {{\n\treturn 1\n}}\n\nfunc run() int {{\n\treturn {m}()\n}}\n"}, e, [])
    if lang == "kotlin":
        return ({"A.kt": f"package app\n\nfun {m}(): Int = 1\n\nfun run(): Int = {m}()\n"}, e, [])
    if lang == "rust":
        return ({"Cargo.toml": CARGO, "src/lib.rs": f"fn {m}() -> i32 {{\n    1\n}}\n\npub fn run() -> i32 {{\n    {m}()\n}}\n"}, e, [])
    if lang == "swift":
        return ({"a.swift": f"func {m}() -> Int {{\n  return 1\n}}\n\nfunc run() -> Int {{\n  return {m}()\n}}\n"}, e, [])
    if lang == "c":
        return ({"a.c": f"static int {m}(void) {{\n    return 1;\n}}\n\nint run(void) {{\n    return {m}();\n}}\n"}, e, [])
    if lang == "cpp":
        return ({"a.cc": f"int {m}() {{\n  return 1;\n}}\n\nint run() {{\n  return {m}();\n}}\n"}, e, [])
    if lang == "ruby":
        return ({"a.rb": f"def {m}\n  1\nend\n\ndef run\n  {m}\nend\n"}, e, [])
    if lang == "php":
        return ({"a.php": f"<?php\nfunction {m}(): int\n{{\n    return 1;\n}}\n\nfunction run(): int\n{{\n    return {m}();\n}}\n"}, e, [])
    return None


def generic(lang: str, t: str) -> tuple[dict[str, str], list, list] | None:
    """A generic class NAME<T> of the project's own."""
    both = ([("run", "calls", f"{t}.lead"), ("run", "calls", "Item.size")], [("run", "calls", "Spare.size")])
    if lang == "typescript":
        return ({"a.ts": f"export class Item {{\n  size(): number {{\n    return 1\n  }}\n}}\n\nexport class Spare {{\n  size(): number {{\n    return 2\n  }}\n}}\n\nexport class {t}<T> {{\n  held!: T\n  lead(): T {{\n    return this.held\n  }}\n}}\n\nexport function run(b: {t}<Item>): number {{\n  return b.lead().size()\n}}\n"}, *both)
    if lang == "java":
        return ({"app/A.java": f"package app;\n\nclass Item {{\n    int size() {{\n        return 1;\n    }}\n}}\n\nclass Spare {{\n    int size() {{\n        return 2;\n    }}\n}}\n\nclass {t}<T> {{\n    T held;\n\n    T lead() {{\n        return held;\n    }}\n}}\n\npublic class A {{\n    int run({t}<Item> b) {{\n        return b.lead().size();\n    }}\n}}\n"},
                [("A.run", "calls", f"{t}.lead"), ("A.run", "calls", "Item.size")], [("A.run", "calls", "Spare.size")])
    if lang == "kotlin":
        return ({"A.kt": f"package app\n\nclass Item {{\n    fun size(): Int = 1\n}}\n\nclass Spare {{\n    fun size(): Int = 2\n}}\n\nclass {t}<T>(val held: T) {{\n    fun lead(): T = held\n}}\n\nfun run(b: {t}<Item>): Int = b.lead().size()\n"}, *both)
    if lang == "scala":
        return ({"A.scala": f"package app\n\nclass Item {{\n  def size(): Int = 1\n}}\n\nclass Spare {{\n  def size(): Int = 2\n}}\n\nclass {t}[T](val held: T) {{\n  def lead(): T = held\n}}\n\nobject Main {{\n  def run(b: {t}[Item]): Int = b.lead().size()\n}}\n"},
                [("Main.run", "calls", f"{t}.lead"), ("Main.run", "calls", "Item.size")], [("Main.run", "calls", "Spare.size")])
    if lang == "csharp":
        return ({"A.cs": f"namespace App\n{{\n    public class Item\n    {{\n        public int Size() {{ return 1; }}\n    }}\n\n    public class Spare\n    {{\n        public int Size() {{ return 2; }}\n    }}\n\n    public class {t}<T>\n    {{\n        public T Held;\n        public T Lead() {{ return Held; }}\n    }}\n\n    public class A\n    {{\n        public int Run({t}<Item> b)\n        {{\n            return b.Lead().Size();\n        }}\n    }}\n}}\n"},
                [("A.Run", "calls", f"{t}.Lead"), ("A.Run", "calls", "Item.Size")], [("A.Run", "calls", "Spare.Size")])
    if lang == "rust":
        return ({"Cargo.toml": CARGO, "src/lib.rs": f"pub struct Item;\n\nimpl Item {{\n    pub fn size(&self) -> i32 {{\n        1\n    }}\n}}\n\npub struct Spare;\n\nimpl Spare {{\n    pub fn size(&self) -> i32 {{\n        2\n    }}\n}}\n\npub struct {t}<T> {{\n    pub held: T,\n}}\n\nimpl<T> {t}<T> {{\n    pub fn lead(&self) -> &T {{\n        &self.held\n    }}\n}}\n\npub fn run(b: &{t}<Item>) -> i32 {{\n    b.lead().size()\n}}\n"}, *both)
    if lang == "swift":
        return ({"a.swift": f"class Item {{\n  func size() -> Int {{\n    return 1\n  }}\n}}\n\nclass Spare {{\n  func size() -> Int {{\n    return 2\n  }}\n}}\n\nstruct {t}<T> {{\n  let held: T\n\n  func lead() -> T {{\n    return held\n  }}\n}}\n\nfunc run(b: {t}<Item>) -> Int {{\n  return b.lead().size()\n}}\n"}, *both)
    if lang == "cpp":
        return ({"a.cc": f"class Item {{\n public:\n  int size() {{\n    return 1;\n  }}\n}};\n\nclass Spare {{\n public:\n  int size() {{\n    return 2;\n  }}\n}};\n\ntemplate <typename T>\nclass {t} {{\n public:\n  T lead() {{\n    return held;\n  }}\n  T held;\n}};\n\nint run({t}<Item>& b) {{\n  return b.lead().size();\n}}\n"}, *both)
    return None


TESTS = {"method": method, "self": own, "function": function, "type": generic}
LANGS = ["python", "javascript", "typescript", "go", "java", "kotlin", "scala", "csharp", "rust",
         "swift", "c", "cpp", "ruby", "php"]
CONTROL = {"method": "zorp", "self": "zorp", "function": "zorp", "type": "Zorp"}


def defined(result_files: dict[str, str], name: str) -> bool:
    return any(name in src for src in result_files.values())


def run(test: str, lang: str, name: str) -> dict[str, Any] | None:
    made = TESTS[test](lang, name)
    if made is None:
        return None
    files, expect, absent = made
    got = conformance.run_case({"id": f"{lang}/{test}/{name}", "files": files,
                                "expect": expect, "absent": absent})
    got.update(test=test, lang=lang, name=name)
    return got


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--only", default="", help="comma-separated languages")
    ap.add_argument("--tables", default="", help="comma-separated table names")
    ap.add_argument("--tests", default="", help="comma-separated tests: method, self, function, type")
    ap.add_argument("--json", help="write every leak here")
    ap.add_argument("--baseline", help="an earlier --json: say what changed")
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args()
    langs = [x for x in LANGS if not args.only or x in args.only.split(",")]
    tabs = tables()
    if args.tables:
        tabs = {k: v for k, v in tabs.items() if k.split("@")[0] in args.tables.split(",")}
    tests = [t for t in TESTS if not args.tests or t in args.tests.split(",")]
    # a name, the tables that hold it, and the tests it is for
    where: dict[str, set[str]] = collections.defaultdict(set)
    for table, names in tabs.items():
        for n in names:
            where[n].add(table)
    jobs: list[tuple[str, str, str]] = []
    for name, held in sorted(where.items()):
        is_type = any(t.split("@")[0] in TYPE_TABLES for t in held) or name[:1].isupper()
        for lang in langs:
            for test in tests:
                if not usable(name, lang, test):
                    continue
                if test == "type" and not is_type:
                    continue
                if test != "type" and name[:1].isupper() and all(t.split("@")[0] in TYPE_TABLES for t in held):
                    continue
                jobs.append((test, lang, name))
    controls = {(t, lang): run(t, lang, CONTROL[t]) for t in tests for lang in langs}
    for (t, lang), c in sorted(controls.items()):
        if c is not None and not c["ok"]:
            print(f"control fails: {t} in {lang}: {c}")
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(lambda j: run(*j), jobs))
    leaks = []
    for r in results:
        if r is None or r["ok"]:
            continue
        c = controls.get((r["test"], r["lang"]))
        if c is None or not c["ok"]:
            continue
        r["tables"] = sorted(where[r["name"]])
        leaks.append(r)
    by_name: dict[tuple[str, str], list[str]] = collections.defaultdict(list)
    for r in leaks:
        by_name[(r["name"], r["test"])].append(r["lang"])
    print(f"{len(jobs)} projects built; {len(leaks)} differ from the control "
          f"({len({r['name'] for r in leaks})} names)")
    for (name, test), ls in sorted(by_name.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        print(f"  {name:22} {test:9} {','.join(sorted(where[name]))[:44]:44} {' '.join(ls)}")
    if args.json:
        Path(args.json).write_text(json.dumps(
            [{k: r[k] for k in ("test", "lang", "name", "tables", "missing", "unwanted")}
             for r in leaks], indent=1) + "\n", encoding="utf-8")
    if args.baseline:
        old = {(r["test"], r["lang"], r["name"]) for r in json.loads(Path(args.baseline).read_text())}
        new = {(r["test"], r["lang"], r["name"]) for r in leaks}
        print(f"against the baseline: {len(old - new)} closed, {len(new - old)} new")
        for row in sorted(new - old):
            print("  new:", *row)
        return 1 if new - old else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
