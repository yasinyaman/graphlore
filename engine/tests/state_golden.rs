//! Shared-state goldens: per language, a small project whose functions read
//! and write module variables, class-level fields and constants without
//! calling each other, built for real, and the `reads` / `writes` edges the
//! source proves (and the ones it does not: a local of the same name).

use std::collections::BTreeSet;
use std::path::PathBuf;

use graphlore_engine::{build_graph, BuildOptions};
use serde_json::Value;

fn project(name: &str, files: &[(&str, &str)]) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("graphlore-state-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    for (rel, src) in files {
        let path = dir.join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, src).unwrap();
    }
    dir
}

type Edges = BTreeSet<(String, String, String, String)>;

/// `(source label, relation, target label, confidence)` of every reads /
/// writes edge, and the graph.
fn state(name: &str, files: &[(&str, &str)]) -> (Edges, Value) {
    let dir = project(name, files);
    let (g, _) = build_graph(&BuildOptions::new(&dir)).unwrap();
    std::fs::remove_dir_all(&dir).unwrap();
    let label = |id: &str| {
        g["nodes"]
            .as_array()
            .unwrap()
            .iter()
            .find(|n| n["id"] == id)
            .map(|n| n["label"].as_str().unwrap().to_string())
            .unwrap_or_default()
    };
    let edges = g["links"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|e| matches!(e["relation"].as_str(), Some("reads" | "writes")))
        .map(|e: &Value| {
            (
                label(e["source"].as_str().unwrap()),
                e["relation"].as_str().unwrap().to_string(),
                label(e["target"].as_str().unwrap()),
                e["confidence"].as_str().unwrap().to_string(),
            )
        })
        .collect();
    (edges, g)
}

#[track_caller]
fn has(got: &Edges, want: &[(&str, &str, &str)]) {
    for (s, r, t) in want {
        assert!(
            got.iter().any(|(a, b, c, _)| a == s && b == r && c == t),
            "missing {s} -{r}-> {t} in {got:#?}"
        );
    }
}

#[track_caller]
fn lacks(got: &Edges, unwanted: &[(&str, &str, &str)]) {
    for (s, r, t) in unwanted {
        assert!(
            !got.iter().any(|(a, b, c, _)| a == s && b == r && c == t),
            "unexpected {s} -{r}-> {t} in {got:#?}"
        );
    }
}

#[track_caller]
fn kind(g: &Value, label: &str) -> String {
    g["nodes"]
        .as_array()
        .unwrap()
        .iter()
        .find(|n| n["label"] == label)
        .unwrap_or_else(|| panic!("no node {label}"))["kind"]
        .as_str()
        .unwrap_or("")
        .to_string()
}

#[track_caller]
fn confidence(got: &Edges, s: &str, r: &str, t: &str) -> String {
    got.iter()
        .find(|(a, b, c, _)| a == s && b == r && c == t)
        .unwrap_or_else(|| panic!("no {s} -{r}-> {t}"))
        .3
        .clone()
}

#[test]
fn python() {
    let (got, g) = state(
        "py",
        &[
            ("pkg/__init__.py", ""),
            (
                "pkg/store.py",
                "CACHE = {}\nMAX = 3\nhits = 0\n\nclass Counter:\n    total = 0\n\n    def bump(self):\n        Counter.total += 1\n\n\ndef put(k, v):\n    global hits\n    hits += 1\n    CACHE[k] = v\n\n\ndef reset():\n    CACHE.clear()\n\n\ndef shadow():\n    hits = 5\n    return hits\n",
            ),
            (
                "pkg/view.py",
                "from pkg.store import CACHE, MAX\nfrom pkg import store\n\n\ndef get(k):\n    return CACHE.get(k)\n\n\ndef limit():\n    return MAX + store.hits\n",
            ),
        ],
    );
    assert_eq!(kind(&g, "CACHE"), "variable");
    assert_eq!(kind(&g, "MAX"), "constant");
    assert_eq!(kind(&g, "total"), "property");
    has(
        &got,
        &[
            ("put()", "writes", "CACHE"),
            ("put()", "writes", "hits"),
            ("put()", "reads", "hits"),
            ("reset()", "writes", "CACHE"),
            ("get()", "reads", "CACHE"),
            ("limit()", "reads", "MAX"),
            ("limit()", "reads", "hits"),
            (".bump()", "writes", "total"),
        ],
    );
    // a name the function assigns without `global` is its own
    lacks(&got, &[("shadow()", "writes", "hits"), ("shadow()", "reads", "hits")]);
    // `CACHE[k] = v` is syntax; `CACHE.clear()` is a guess from the name
    assert_eq!(confidence(&got, "put()", "writes", "CACHE"), "EXTRACTED");
    assert_eq!(confidence(&got, "reset()", "writes", "CACHE"), "INFERRED");
    lacks(&got, &[("get()", "writes", "CACHE")]);
}

#[test]
fn go() {
    let (got, g) = state(
        "go",
        &[
            ("go.mod", "module example.com/m\n"),
            (
                "store/store.go",
                "package store\n\nvar hits = 0\nvar a, b = 1, 2\nconst Max = 3\n\nvar (\n\tcache = map[string]int{}\n)\n\nfunc Put(k string) {\n\thits++\n\tcache[k] = Max\n}\n",
            ),
            (
                "store/read.go",
                "package store\n\nfunc Hits() int {\n\treturn hits + a\n}\n\nfunc Local() int {\n\thits := 3\n\treturn hits\n}\n",
            ),
        ],
    );
    assert_eq!(kind(&g, "hits"), "variable");
    assert_eq!(kind(&g, "Max"), "constant");
    assert_eq!(kind(&g, "b"), "variable");
    has(
        &got,
        &[
            ("Put()", "writes", "hits"),
            ("Put()", "writes", "cache"),
            ("Put()", "reads", "Max"),
            ("Hits()", "reads", "hits"),
            ("Hits()", "reads", "a"),
        ],
    );
    lacks(&got, &[("Local()", "reads", "hits")]);
}

#[test]
fn typescript() {
    let (got, g) = state(
        "ts",
        &[
            (
                "src/store.ts",
                "export let hits = 0;\nexport const cache = new Map<string, number>();\nexport const MAX = 3;\n\nexport function put(k: string) {\n  hits++;\n  cache.set(k, MAX);\n}\n",
            ),
            (
                "src/view.ts",
                "import { hits, cache } from './store';\n\nexport function get(k: string) {\n  return cache.get(k);\n}\n\nexport function local() {\n  const hits = 2;\n  return hits;\n}\n",
            ),
        ],
    );
    assert_eq!(kind(&g, "hits"), "variable");
    has(
        &got,
        &[
            ("put()", "writes", "hits"),
            ("put()", "writes", "cache"),
            ("put()", "reads", "MAX"),
            ("get()", "reads", "cache"),
        ],
    );
    lacks(&got, &[("local()", "reads", "hits"), ("get()", "writes", "cache")]);
}

#[test]
fn rust() {
    let (got, g) = state(
        "rs",
        &[
            ("Cargo.toml", "[package]\nname = \"m\"\nversion = \"0.1.0\"\n"),
            (
                "src/lib.rs",
                "use std::sync::Mutex;\n\nstatic mut HITS: u32 = 0;\nstatic CACHE: Mutex<Vec<u32>> = Mutex::new(Vec::new());\nconst MAX: u32 = 3;\n\npub fn put() {\n    unsafe { HITS += 1; }\n    CACHE.lock().unwrap().push(MAX);\n}\n\npub fn count() -> usize {\n    CACHE.lock().unwrap().len()\n}\n",
            ),
        ],
    );
    assert_eq!(kind(&g, "HITS"), "variable");
    assert_eq!(kind(&g, "MAX"), "constant");
    has(
        &got,
        &[
            ("put()", "writes", "HITS"),
            ("put()", "writes", "CACHE"),
            ("put()", "reads", "MAX"),
            ("count()", "reads", "CACHE"),
        ],
    );
    lacks(&got, &[("count()", "writes", "CACHE")]);
}

#[test]
fn java() {
    let (got, g) = state(
        "java",
        &[(
            "src/K.java",
            "class K {\n  static int hits = 0;\n  static final int MAX = 3;\n  void put() { hits++; K.hits += MAX; }\n  int get() { int hits = 1; return hits; }\n}\nclass Other {\n  int read() { return K.hits; }\n}\n",
        )],
    );
    assert_eq!(kind(&g, "hits"), "property");
    assert_eq!(kind(&g, "MAX"), "constant");
    has(
        &got,
        &[(".put()", "writes", "hits"), (".put()", "reads", "MAX"), (".read()", "reads", "hits")],
    );
    lacks(&got, &[(".get()", "reads", "hits")]);
}

#[test]
fn csharp() {
    let (got, _) = state(
        "cs",
        &[(
            "K.cs",
            "class K {\n  static int hits = 0;\n  const int MAX = 3;\n  void Put() { hits++; K.hits += MAX; }\n}\nclass Other {\n  int Read() { return K.hits; }\n}\n",
        )],
    );
    has(
        &got,
        &[(".Put()", "writes", "hits"), (".Put()", "reads", "MAX"), (".Read()", "reads", "hits")],
    );
}

#[test]
fn kotlin_scala_swift() {
    let (got, _) = state(
        "kt",
        &[(
            "a.kt",
            "var hits = 0\nval MAX = 3\n\nfun put() {\n  hits++\n  hits += MAX\n}\n\nfun get(): Int = hits\n",
        )],
    );
    has(
        &got,
        &[("put()", "writes", "hits"), ("put()", "reads", "MAX"), ("get()", "reads", "hits")],
    );
    let (got, _) = state(
        "scala",
        &[(
            "a.scala",
            "object Store {\n  var hits = 0\n  val Max = 3\n  def put(): Unit = { hits += Max }\n  def get(): Int = hits\n}\n",
        )],
    );
    has(
        &got,
        &[(".put()", "writes", "hits"), (".put()", "reads", "Max"), (".get()", "reads", "hits")],
    );
    let (got, _) = state(
        "swift",
        &[(
            "a.swift",
            "var hits = 0\nlet maxHits = 3\n\nclass K {\n  static var total = 0\n  func put() {\n    hits += 1\n    K.total += maxHits\n  }\n}\n\nfunc get() -> Int { return hits }\n",
        )],
    );
    has(
        &got,
        &[
            (".put()", "writes", "hits"),
            (".put()", "writes", "total"),
            (".put()", "reads", "maxHits"),
            ("get()", "reads", "hits"),
        ],
    );
}

#[test]
fn c_and_cpp() {
    let (got, g) = state(
        "c",
        &[(
            "a.c",
            "static int hits = 0;\nconst int MAX = 3;\nextern int other;\nint level, b = 2;\n\nvoid put(void) { hits++; level = MAX; }\nint get(void) { int hits = 1; return hits + b; }\n",
        )],
    );
    assert_eq!(kind(&g, "hits"), "variable");
    assert!(!g["nodes"].as_array().unwrap().iter().any(|n| n["label"] == "other"));
    has(
        &got,
        &[
            ("put()", "writes", "hits"),
            ("put()", "writes", "level"),
            ("put()", "reads", "MAX"),
            ("get()", "reads", "b"),
        ],
    );
    lacks(&got, &[("get()", "reads", "hits")]);
    let (got, _) = state(
        "cpp",
        &[(
            "a.cpp",
            "int g = 0;\nclass K {\n  static int total;\n  void put() { total++; g = 2; }\n};\nint read() { return g; }\n",
        )],
    );
    has(
        &got,
        &[(".put()", "writes", "total"), (".put()", "writes", "g"), ("read()", "reads", "g")],
    );
}

#[test]
fn ruby_and_php() {
    let (got, _) = state(
        "rb",
        &[(
            "a.rb",
            "$hits = 0\nMAX = 3\n\nclass K\n  @@total = 0\n\n  def put\n    $hits += 1\n    @@total += MAX\n  end\n\n  def get\n    @@total\n  end\nend\n",
        )],
    );
    has(
        &got,
        &[
            (".put()", "writes", "hits"),
            (".put()", "writes", "@@total"),
            (".put()", "reads", "MAX"),
            (".get()", "reads", "@@total"),
        ],
    );
    let (got, _) = state(
        "php",
        &[(
            "K.php",
            "<?php\nclass K {\n  public static $total = 0;\n  const MAX = 3;\n  function put() { self::$total++; static::$total += self::MAX; }\n  function get() { return self::$total; }\n}\n",
        )],
    );
    has(
        &got,
        &[(".put()", "writes", "total"), (".put()", "reads", "MAX"), (".get()", "reads", "total")],
    );
}

#[test]
fn what_is_not_a_write() {
    // a mutating method's name on something that is no collection, a call
    // further down the chain, a constructor building its own object
    let (got, g) = state(
        "notw",
        &[(
            "src/app.ts",
            "export let client = createClient();\nexport let seen = new Set<string>();\n\nexport function remove(u: string) {\n  client.delete(u);\n  seen.add(u);\n}\n\nexport function header(r: Req) {\n  return client.get(r).replace('a', 'b');\n}\n\nexport class Box {\n  size = 0;\n  constructor(n: number) { this.size = n; }\n  grow() { this.size += 1; }\n}\n",
        )],
    );
    has(
        &got,
        &[
            ("remove()", "writes", "seen"),
            ("remove()", "reads", "client"),
            ("header()", "reads", "client"),
            (".grow()", "writes", "size"),
        ],
    );
    lacks(
        &got,
        &[
            ("remove()", "writes", "client"),
            ("header()", "writes", "client"),
            (".constructor()", "writes", "size"),
        ],
    );
    let (got, g2) = state(
        "blank",
        &[
            ("go.mod", "module example.com/m\n"),
            ("a.go", "package m\n\nvar _ = 1\n\nfunc f() {\n\t_ = 2\n}\n"),
        ],
    );
    assert!(!g2["nodes"].as_array().unwrap().iter().any(|n| n["label"] == "_"));
    assert!(got.is_empty(), "{got:?}");
    let _ = g;
}

#[test]
fn instance_fields() {
    // set in the constructor (which builds, and couples nothing), changed and
    // read in the other methods
    let (got, g) = state(
        "pyfield",
        &[(
            "a.py",
            "class Conn:\n    def __init__(self):\n        self.open = False\n        self.sent = 0\n\n    def connect(self):\n        self.open = True\n\n    def send(self):\n        if self.open:\n            self.sent += 1\n",
        )],
    );
    assert_eq!(kind(&g, "open"), "property");
    has(
        &got,
        &[
            (".connect()", "writes", "open"),
            (".send()", "reads", "open"),
            (".send()", "writes", "sent"),
        ],
    );
    lacks(&got, &[(".__init__()", "writes", "open")]);
    let (got, _) = state(
        "jsfield",
        &[(
            "k.js",
            "class K {\n  constructor() { this.n = 0; }\n  inc() { this.n += 1; }\n  get() { return this.n; }\n}\n",
        )],
    );
    has(&got, &[(".inc()", "writes", "n"), (".get()", "reads", "n")]);
    let (got, _) = state(
        "rbfield",
        &[(
            "k.rb",
            "class K\n  def initialize\n    @n = 0\n  end\n\n  def inc\n    @n += 1\n  end\n\n  def get\n    @n\n  end\nend\n",
        )],
    );
    has(&got, &[(".inc()", "writes", "@n"), (".get()", "reads", "@n")]);
    let (got, _) = state(
        "gofield",
        &[
            ("go.mod", "module example.com/m\n"),
            (
                "s.go",
                "package m\n\ntype S struct {\n\tn int\n}\n\nfunc (s *S) Inc() { s.n++ }\n\nfunc (s *S) Get() int { return s.n }\n",
            ),
        ],
    );
    has(&got, &[(".Inc()", "writes", "n"), (".Get()", "reads", "n")]);
    let (got, _) = state(
        "golit",
        &[
            ("go.mod", "module example.com/m\n"),
            (
                "s.go",
                "package m\n\ntype S struct {\n\tn int\n}\n\nfunc New() S { return S{n: 1} }\n",
            ),
        ],
    );
    lacks(&got, &[("New()", "reads", "n")]);
    let (got, _) = state(
        "rsfield",
        &[
            ("Cargo.toml", "[package]\nname = \"m\"\nversion = \"0.1.0\"\n"),
            (
                "src/lib.rs",
                "pub struct S {\n    n: u32,\n}\n\nimpl S {\n    pub fn inc(&mut self) {\n        self.n += 1;\n    }\n\n    pub fn get(&self) -> u32 {\n        self.n\n    }\n}\n",
            ),
        ],
    );
    has(&got, &[(".inc()", "writes", "n"), (".get()", "reads", "n")]);
    let (got, _) = state(
        "javafield",
        &[(
            "K.java",
            "class K {\n  private int n;\n  K() { n = 0; }\n  void inc() { n++; this.n += 1; }\n  int get() { return n; }\n}\n",
        )],
    );
    has(&got, &[(".inc()", "writes", "n"), (".get()", "reads", "n")]);
}
