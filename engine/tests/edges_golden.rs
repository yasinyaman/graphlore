//! Edge goldens: a two- or three-file project per language, built for real,
//! and the import / call / base edges its source proves.

use std::collections::BTreeSet;
use std::path::PathBuf;

use graphlore_engine::{build_graph, BuildOptions};
use serde_json::Value;

fn project(name: &str, files: &[(&str, &str)]) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("graphlore-edges-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    for (rel, src) in files {
        let path = dir.join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, src).unwrap();
    }
    dir
}

/// `(source label, relation, target label)` of every non-containment edge.
fn edges(name: &str, files: &[(&str, &str)]) -> BTreeSet<(String, String, String)> {
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
    g["links"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|e| {
            !matches!(e["relation"].as_str(), Some("contains" | "method" | "case_of" | "defines"))
        })
        .map(|e: &Value| {
            (
                label(e["source"].as_str().unwrap()),
                e["relation"].as_str().unwrap().to_string(),
                label(e["target"].as_str().unwrap()),
            )
        })
        .collect()
}

#[track_caller]
fn has(got: &BTreeSet<(String, String, String)>, want: &[(&str, &str, &str)]) {
    for (s, r, t) in want {
        assert!(
            got.contains(&(s.to_string(), r.to_string(), t.to_string())),
            "missing {s} -{r}-> {t} in {got:#?}"
        );
    }
}

#[test]
fn python() {
    let got = edges("py", &[
        ("pkg/__init__.py", ""),
        ("pkg/base.py", "class Base:\n    def run(self):\n        return self.step()\n\n    def step(self):\n        return 1\n"),
        ("pkg/app.py", "from pkg.base import Base\nimport json\n\n\nclass App(Base):\n    def go(self):\n        return self.run() + json.dumps(1)\n"),
    ]);
    has(
        &got,
        &[
            ("app.py", "imports_from", "base.py"),
            ("app.py", "imports", "Base"),
            ("app.py", "imports", "json"),
            ("App", "inherits", "Base"),
            (".go()", "calls", ".run()"),
            (".run()", "calls", ".step()"),
            (".go()", "calls", "json"),
        ],
    );
}

#[test]
fn typescript() {
    let got = edges("ts", &[
        ("src/dto/index.ts", "export * from './create';\n"),
        ("src/dto/create.ts", "export class CreateDto {\n  describe(): string { return 'x'; }\n}\n"),
        ("src/app.ts", "import { CreateDto } from './dto';\nimport express from 'express';\n\nexport function app(): CreateDto {\n  const d = new CreateDto();\n  d.describe();\n  return d;\n}\n"),
    ]);
    has(
        &got,
        &[
            ("app.ts", "imports_from", "index.ts"),
            ("app.ts", "imports", "CreateDto"),
            ("app.ts", "imports_from", "express"),
            ("index.ts", "re_exports", "create.ts"),
            ("app()", "calls", "CreateDto"),
            ("app()", "calls", ".describe()"),
            ("app()", "references", "CreateDto"),
        ],
    );
}

#[test]
fn go() {
    let got = edges("go", &[
        ("go.mod", "module example.com/m\n"),
        ("store/store.go", "package store\n\ntype Store struct{}\n\nfunc New() *Store { return &Store{} }\n\nfunc (s *Store) Get(k string) string { return k }\n"),
        ("main.go", "package main\n\nimport \"example.com/m/store\"\n\nfunc main() {\n\ts := store.New()\n\ts.Get(\"a\")\n}\n"),
    ]);
    has(
        &got,
        &[
            ("main.go", "imports_from", "store.go"),
            ("main()", "calls", "New()"),
            ("main()", "calls", ".Get()"),
            ("New()", "calls", "Store"),
        ],
    );
}

#[test]
fn java() {
    let got = edges("java", &[
        ("src/a/Repo.java", "package a;\npublic interface Repo { int find(); }\n"),
        ("src/a/Db.java", "package a;\npublic class Db implements Repo { public int find() { return 1; } }\n"),
        ("src/b/Svc.java", "package b;\nimport a.Repo;\nimport a.Db;\npublic class Svc {\n  private Repo repo = new Db();\n  int run() { return repo.find(); }\n}\n"),
    ]);
    has(
        &got,
        &[
            ("Svc.java", "imports", "Repo"),
            ("Db", "implements", "Repo"),
            (".run()", "calls", ".find()"),
            ("Svc", "calls", "Db"),
        ],
    );
}

#[test]
fn csharp() {
    let got = edges("cs", &[
        ("Models/Item.cs", "namespace Shop.Models;\npublic class Item { public int Price() { return 1; } }\n"),
        ("Svc.cs", "using Shop.Models;\nnamespace Shop;\npublic class Svc : IDisposable {\n  public Item Item { get; set; }\n  public int Total() { return Item.Price(); }\n  public void Dispose() {}\n}\n"),
    ]);
    has(
        &got,
        &[
            ("Svc.cs", "imports", "Item.cs"),
            ("Svc", "implements", "IDisposable"),
            (".Total()", "calls", ".Price()"),
            ("Svc", "references", "Item"),
        ],
    );
}

#[test]
fn rust() {
    let got = edges("rs", &[
        ("Cargo.toml", "[package]\nname = \"demo\"\n"),
        ("src/lib.rs", "pub mod pool;\nuse crate::pool::Pool;\n\npub fn run() -> i32 {\n    let p = Pool::new();\n    p.acquire()\n}\n"),
        ("src/pool.rs", "pub trait Acquire { fn acquire(&self) -> i32; }\npub struct Pool;\nimpl Pool {\n    pub fn new() -> Self { Pool }\n}\nimpl Acquire for Pool {\n    fn acquire(&self) -> i32 { 1 }\n}\n"),
    ]);
    has(
        &got,
        &[
            ("lib.rs", "imports_from", "Pool"),
            ("run()", "calls", ".new()"),
            ("Pool", "implements", "Acquire"),
        ],
    );
}

#[test]
fn php() {
    let got = edges("php", &[
        ("src/Base.php", "<?php\nnamespace Shop;\nabstract class Base { public function boot(): void {} }\n"),
        ("src/Cart.php", "<?php\nnamespace Shop;\nuse Psr\\Log\\LoggerInterface;\nclass Cart extends Base {\n  public function add(LoggerInterface $log): void { $this->boot(); }\n}\n"),
    ]);
    has(
        &got,
        &[
            ("Cart", "inherits", "Base"),
            (".add()", "calls", ".boot()"),
            ("Cart.php", "imports", "Psr\\Log\\LoggerInterface"),
        ],
    );
}

#[test]
fn ruby() {
    let got = edges("rb", &[
        ("lib/shop/base.rb", "module Shop\n  class Base\n    def boot\n      1\n    end\n  end\nend\n"),
        ("lib/shop/cart.rb", "require_relative 'base'\nmodule Shop\n  class Cart < Base\n    include Enumerable\n    def add\n      boot\n      Line.new\n    end\n  end\n  class Line\n  end\nend\n"),
    ]);
    has(
        &got,
        &[
            ("cart.rb", "imports", "base.rb"),
            ("Cart", "inherits", "Base"),
            ("Cart", "mixes_in", "Enumerable"),
            (".add()", "calls", ".boot()"),
            (".add()", "calls", "Line"),
        ],
    );
}

#[test]
fn swift() {
    let got = edges("swift", &[
        ("Package.swift", "let package = Package(name: \"Kit\", targets: [.target(name: \"Kit\")])\n"),
        ("Sources/Kit/Session.swift", "public protocol Retrier { func retry() }\npublic class Session: Retrier {\n  public func retry() {}\n  func start() { self.retry() }\n}\n"),
        ("Sources/Kit/Use.swift", "import Foundation\nfunc make() -> Session {\n  let s = Session()\n  s.start()\n  return s\n}\n"),
    ]);
    has(
        &got,
        &[
            ("Session", "implements", "Retrier"),
            (".start()", "calls", ".retry()"),
            ("make()", "calls", "Session"),
            ("make()", "calls", ".start()"),
            ("Use.swift", "imports", "Foundation"),
        ],
    );
}

#[test]
fn c_and_cpp() {
    let got = edges("c", &[
        ("include/util.h", "int helper(int x);\n"),
        ("src/util.c", "#include \"util.h\"\nint helper(int x) { return x; }\n"),
        ("src/main.c", "#include \"../include/util.h\"\n#include <stdio.h>\nint main(void) { return helper(1); }\n"),
        ("src/shape.cpp", "class Shape { public: virtual int area(); };\nclass Square : public Shape { public: int area() override { return 1; } int twice() { return area() * 2; } };\n"),
    ]);
    has(
        &got,
        &[
            ("main.c", "imports", "util.h"),
            ("main.c", "imports", "stdio.h"),
            ("main()", "calls", "helper()"),
            ("Square", "inherits", "Shape"),
            (".twice()", "calls", ".area()"),
        ],
    );
    // the call lands on the definition, not the prototype
    let dir = project(
        "c2",
        &[
            ("util.h", "int helper(int x);\n"),
            ("util.c", "#include \"util.h\"\nint helper(int x) { return x; }\n"),
            ("main.c", "#include \"util.h\"\nint main(void) { return helper(1); }\n"),
        ],
    );
    let (g, _) = build_graph(&BuildOptions::new(&dir)).unwrap();
    std::fs::remove_dir_all(&dir).unwrap();
    let call = g["links"].as_array().unwrap().iter().find(|e| e["relation"] == "calls").unwrap();
    assert_eq!(call["target"], "util_c_helper");
}

#[test]
fn kotlin_and_scala() {
    let got = edges("jvm", &[
        ("src/a/Repo.kt", "package a\ninterface Repo { fun find(): Int }\nclass Db : Repo { override fun find(): Int = 1 }\n"),
        ("src/b/Svc.kt", "package b\nimport a.Repo\nclass Svc(private val repo: Repo) { fun run(): Int = repo.find() }\n"),
        ("src/c/Model.scala", "package c\ntrait Named { def name: String }\nclass Item extends Named { def name = \"x\" }\n"),
    ]);
    has(
        &got,
        &[
            ("Db", "implements", "Repo"),
            ("Svc.kt", "imports", "Repo"),
            (".run()", "calls", ".find()"),
            ("Item", "inherits", "Named"),
        ],
    );
}

#[test]
fn imports_carry_when_they_run_and_externals_are_nodes() {
    let dir = project("runs", &[
        ("a.py", "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from b import B\n\n\ndef f():\n    import b\n    return b.g()\n"),
        ("b.py", "def g():\n    return 1\n\n\nclass B:\n    pass\n"),
    ]);
    let (g, _) = build_graph(&BuildOptions::new(&dir)).unwrap();
    std::fs::remove_dir_all(&dir).unwrap();
    let runs: BTreeSet<(String, String)> = g["links"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|e| {
            Some((e["relation"].as_str()?.to_string(), e["runs"].as_str()?.to_string()))
        })
        .collect();
    assert!(runs.contains(&("imports_from".into(), "type-checking".into())));
    assert!(runs.contains(&("imports".into(), "deferred".into())));
    let typing = g["nodes"].as_array().unwrap().iter().find(|n| n["label"] == "typing").unwrap();
    assert_eq!(typing["type"], "external");
    assert_eq!(typing["source_file"], "");
    // one edge per (source, target, relation)
    let mut seen = BTreeSet::new();
    for e in g["links"].as_array().unwrap() {
        let key = (e["source"].to_string(), e["target"].to_string(), e["relation"].to_string());
        assert!(seen.insert(key), "duplicate edge {e}");
    }
}

#[test]
fn a_call_in_a_field_assignment_is_the_methods() {
    // `self.x = make()` in `__init__`: the field is a node of the class, the
    // call is `__init__`'s (not the class's, where the field hangs)
    let got = edges("fieldcall", &[
        ("parse.py", "def urlparse(url):\n    return url\n"),
        ("urls.py", "from parse import urlparse\n\n\nclass URL:\n    def __init__(self, url):\n        self._uri = urlparse(url)\n        self.raw = self._clean(url)\n\n    def _clean(self, url):\n        return url\n"),
        ("box.js", "function make() { return 1; }\n\nclass Box {\n  constructor() {\n    this.value = make();\n  }\n}\n"),
    ]);
    has(
        &got,
        &[
            (".__init__()", "calls", "urlparse()"),
            (".__init__()", "calls", "._clean()"),
            (".constructor()", "calls", "make()"),
        ],
    );
    assert!(!got.iter().any(|(s, r, _)| (s == "URL" || s == "Box") && r == "calls"), "{got:#?}");
}

#[test]
fn a_name_bound_to_a_required_member_is_what_it_names() {
    // `var parse = require('./utils').parse` is an import, not a variable
    let got = edges("requiremember", &[
        ("lib/utils.js", "exports.parse = function parse(v) { return v; };\n"),
        ("lib/app.js", "var parse = require('./utils').parse;\nconst utils = require('./utils');\n\nfunction run(v) {\n  return parse(v) + utils.parse(v);\n}\n"),
    ]);
    has(&got, &[("run()", "calls", "parse()"), ("app.js", "imports", "parse()")]);
    assert!(
        !got.iter().any(|(_, r, t)| r == "reads" && (t == "parse" || t == "utils")),
        "{got:#?}"
    );
}

#[test]
fn php_variables_are_typed_by_what_they_are_assigned() {
    let got = edges("phpvars", &[
        ("src/Factory.php", "<?php\nnamespace App;\n\nclass Factory\n{\n    public function create($r) { return 1; }\n    public function release($e): void {}\n}\n"),
        ("src/Runner.php", "<?php\nnamespace App;\n\nclass Runner\n{\n    public function run(Factory $given)\n    {\n        $factory = new Factory();\n        $easy = $factory->create(1);\n        $given->release($easy);\n    }\n}\n\nfunction factory() {}\n"),
    ]);
    has(&got, &[(".run()", "calls", ".create()"), (".run()", "calls", ".release()")]);
    // `$factory` is a variable: never the function of that name
    assert!(!got.contains(&(".run()".into(), "calls".into(), "factory()".into())), "{got:#?}");
}

#[test]
fn an_untyped_local_hides_outer_names_and_super_in_a_test_module_is_the_file() {
    // `pool.get(…)` on a local the walk cannot type is not the crate's `get`,
    // which `use super::*` in `mod tests` used to bring into the file's scope
    let got = edges("rustlocal", &[
        ("Cargo.toml", "[package]\nname = \"x\"\nversion = \"0.1.0\"\n"),
        ("src/lib.rs", "mod pool;\npub fn get(uri: &str) -> u32 { 1 }\n"),
        ("src/pool.rs", "use std::sync::{Arc, Mutex};\n\nstruct Pool { closed: bool }\n\nimpl Pool {\n    fn get(&mut self, key: &str) -> Option<u32> { None }\n    fn purge_idle(&mut self) {}\n}\n\npub struct ConnectionPool { pool: Arc<Mutex<Pool>> }\n\nimpl ConnectionPool {\n    pub fn connect(&self) -> u32 {\n        let mut pool = self.pool.lock().unwrap();\n        pool.purge_idle();\n        if let Some(conn) = pool.get(\"k\") { return conn; }\n        0\n    }\n}\n\n#[cfg(test)]\nmod tests {\n    use super::*;\n\n    #[test]\n    fn connects() { let _ = Pool { closed: false }; }\n}\n"),
    ]);
    has(&got, &[(".connect()", "calls", ".purge_idle()")]);
    assert!(!got.contains(&(".connect()".into(), "calls".into(), "get()".into())), "{got:#?}");
    assert!(
        !got.iter().any(|(s, r, t)| s == "pool.rs" && r.starts_with("imports") && t == "lib.rs"),
        "{got:#?}"
    );
}

#[test]
fn an_extension_in_another_module_does_not_hide_the_type() {
    // Tests only extend `Session`: `Session()` there is still the class the
    // module imports, and a local it types reaches the class's methods
    let got = edges("swiftext", &[
        ("Package.swift", "// swift-tools-version:5.9\nimport PackageDescription\n\nlet package = Package(name: \"Net\",\n                      targets: [.target(name: \"Net\", path: \"Source\"),\n                                .testTarget(name: \"NetTests\", dependencies: [\"Net\"], path: \"Tests\")])\n"),
        ("Source/Session.swift", "open class Session {\n    public init() {}\n    open func request(_ url: String) -> Int { 1 }\n}\n"),
        ("Tests/Helpers.swift", "import Net\n\nextension Session {\n    func stub() {}\n}\n"),
        ("Tests/SessionTests.swift", "import Net\nimport XCTest\n\nfinal class SessionTests: XCTestCase {\n    func testRequest() {\n        let session = Session()\n        _ = session.request(\"x\")\n    }\n}\n"),
    ]);
    has(&got, &[(".testRequest()", "calls", "Session"), (".testRequest()", "calls", ".request()")]);
}

#[test]
fn what_a_local_hides_and_what_it_does_not() {
    let got = edges("shadow", &[
        // a typed local beside a method of its name: the call is the method's
        ("B.java", "class B {\n  B baseUrl(String baseUrl) {\n    return baseUrl(parse(baseUrl));\n  }\n  B baseUrl(Object url) { return this; }\n  Object parse(String s) { return s; }\n}\n"),
        // a field assigned in a method is still the field
        ("T.java", "class Service { void go() {} }\nclass T {\n  private Service service;\n  void setUp(Object retrofit) {\n    service = make(retrofit);\n  }\n  Service make(Object o) { return null; }\n  void run() { service.go(); }\n}\n"),
        // `let agent = agent();`: the right-hand side is the function
        ("src/lib.rs", "pub struct Agent;\nimpl Agent {\n    pub fn shutdown(&self) {}\n}\nfn agent() -> Agent { Agent }\nfn stops() {\n    let agent = agent();\n    agent.shutdown();\n}\n"),
        // a parameter named like a function elsewhere is the parameter
        ("conftest.py", "def token_headers():\n    return {}\n"),
        ("test_api.py", "from conftest import token_headers as th\n\n\ndef send(headers):\n    return headers\n\n\ndef test_login(token_headers: dict):\n    send(token_headers)\n"),
    ]);
    has(
        &got,
        &[
            (".baseUrl()", "calls", ".parse()"),
            (".setUp()", "writes", "service"),
            (".run()", "calls", ".go()"),
            ("stops()", "calls", "agent()"),
            ("stops()", "calls", ".shutdown()"),
            ("test_login()", "calls", "send()"),
        ],
    );
    assert!(
        got.iter().any(|(s, r, t)| s == ".baseUrl()" && r == "calls" && t == ".baseUrl()"),
        "{got:#?}"
    );
    assert!(
        !got.contains(&("test_login()".into(), "indirect_call".into(), "token_headers()".into())),
        "{got:#?}"
    );
}
