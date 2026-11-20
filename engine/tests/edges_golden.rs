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

#[test]
fn extension_functions_are_members_to_their_callers() {
    let got = edges("extensions", &[
        // Kotlin: an extension called on a lambda parameter, on a typed local
        // whose class has no such member, and on a call's result
        ("src/main/kotlin/app/Specs.kt", "package app\n\nclass KmProperty\nclass TypeSpec { fun build(): TypeSpec = this }\n\nfun render(props: List<KmProperty>): String {\n    val spec: TypeSpec = TypeSpec()\n    props.map { property -> property.toPropertySpec() }\n    spec.build().trimmedText()\n    return spec.trimmedText()\n}\n\nprivate fun KmProperty.toPropertySpec(): String = \"\"\n\nprivate fun TypeSpec.trimmedText(): String = \"\"\n"),
        // C#: an extension method on a string, from a file that uses its namespace
        ("src/Extensions/StringExtensions.cs", "namespace App.Extensions;\n\npublic static class StringExtensions\n{\n    internal static bool IsBlank(this string? value) => string.IsNullOrWhiteSpace(value);\n}\n"),
        ("src/Auth/Signer.cs", "using App.Extensions;\n\nnamespace App.Auth;\n\npublic class Signer\n{\n    public bool Sign(string realm)\n    {\n        return !realm.IsBlank();\n    }\n}\n"),
        // Rust: `self.x()` in an impl for a type of another crate
        ("Cargo.toml", "[package]\nname = \"x\"\nversion = \"0.1.0\"\n"),
        ("src/lib.rs", "use http::uri::Authority;\n\npub(crate) trait AuthorityExt {\n    fn userinfo(&self) -> Option<&str>;\n    fn username(&self) -> Option<&str>;\n}\n\nimpl AuthorityExt for Authority {\n    fn userinfo(&self) -> Option<&str> { None }\n    fn username(&self) -> Option<&str> { self.userinfo() }\n}\n\nfn first() {\n    let a = http::uri::Authority::from_static(\"x\");\n    let a = a.userinfo();\n}\n"),
    ]);
    has(
        &got,
        &[
            ("render()", "calls", "toPropertySpec()"),
            ("render()", "calls", "trimmedText()"),
            (".Sign()", "calls", ".IsBlank()"),
            (".username()", "calls", ".userinfo()"),
            // a local of that external type, rebound from itself
            ("first()", "calls", ".userinfo()"),
        ],
    );
}

#[test]
fn a_bare_ruby_name_is_a_call_unless_a_local_has_it() {
    let got = edges("ruby-bare", &[
        (
            "app/articles_controller.rb",
            "class ArticlesController\n  def create\n    @article = Article.new(article_params)\n    render json: @article, status: status_code\n  end\n\n  def index\n    items.each do |headers|\n      log(headers)\n    end\n    build(response_headers: headers)\n  rescue => status_code\n    log(status_code)\n  end\n\n  def headers=(hash)\n    @headers = hash\n  end\n\n  def headers; end\n  def items; end\n  def log(x); end\n  def build(h); end\n  def status_code; end\n\n  private\n\n  def article_params; end\nend\n",
        ),
        // a method at the top level of a file this one does not require is
        // not this file's `response`
        ("spec/a_spec.rb", "RSpec.describe Thing do\n  def response(_env)\n    1\n  end\nend\n"),
        ("spec/b_spec.rb", "RSpec.describe Thing do\n  let(:response) { 1 }\n  it 'works' do\n    expect(response.status).to eq(200)\n  end\nend\n"),
    ]);
    assert!(!got.iter().any(|(_, _, t)| t == ".response()"), "{got:#?}");
    has(
        &got,
        &[
            // an argument, a pair's value, a receiver: each is a call
            (".create()", "calls", ".article_params()"),
            (".create()", "calls", ".status_code()"),
            (".index()", "calls", ".items()"),
            (".index()", "calls", ".headers()"),
            (".index()", "calls", ".log()"),
        ],
    );
    // `rescue => status_code` and the block's `|headers|` are locals there:
    // one call of `headers` (line 11), none of `status_code` in `index`
    assert!(
        !got.contains(&(".index()".into(), "calls".into(), ".status_code()".into())),
        "{got:#?}"
    );
    assert!(!got.iter().any(|(_, r, _)| r == "indirect_call"), "{got:#?}");
}

#[test]
fn a_ruby_module_is_every_file_that_opens_it() {
    let got = edges("ruby-open", &[
        ("lib/far.rb", "require 'far/utils'\nrequire 'far/error'\nmodule Far\n  class Conn\n    def run\n      h = Utils::Headers.new\n      h.fetch_it\n      raise Far::ParsingError.new(1)\n    end\n    def again\n      self.class.from(1)\n    end\n    def self.from(x); end\n  end\nend\n"),
        ("lib/far/utils.rb", "require 'far/utils/headers'\nmodule Far\n  module Utils\n    def self.helper; end\n  end\nend\n"),
        ("lib/far/utils/headers.rb", "module Far\n  module Utils\n    class Headers\n      def fetch_it; end\n    end\n  end\nend\n"),
        // `class Far::Compact` is `Compact` in `Far`
        ("lib/far/error.rb", "module Far\n  class Error < StandardError; end\n  class ParsingError < Error; end\nend\nclass Far::Compact\n  def go; Far::Utils.helper; end\nend\n"),
    ]);
    has(
        &got,
        &[
            (".run()", "calls", "Headers"),
            (".run()", "calls", ".fetch_it()"),
            (".run()", "calls", "ParsingError"),
            (".again()", "calls", ".from()"),
            (".go()", "calls", ".helper()"),
        ],
    );
}

#[test]
fn an_anonymous_class_is_built_by_the_method_around_it() {
    let got = edges("anonymous", &[
        ("A.java", "interface Callback<T> { void onResponse(T body); }\nclass Helper {\n  Helper(String name) {}\n  void run() {}\n}\n\nclass Client {\n  void enqueue(Callback<String> cb) {}\n\n  void go() {\n    final Helper helper = new Helper(\"a\");\n    enqueue(new Callback<String>() {\n      @Override public void onResponse(String body) {\n        helper.run();\n        finish();\n      }\n    });\n    Runnable r = new Runnable() { public void run() { finish(); } };\n  }\n  void finish() {}\n}\n"),
        ("K.kt", "package app\n\ninterface Listener<T> { fun onDone(v: T) }\nclass Bus { fun add(l: Listener<String>) {} }\nclass Screen(val bus: Bus) {\n    fun start() {\n        bus.add(object : Listener<String> {\n            override fun onDone(v: String) { stop() }\n        })\n    }\n    fun stop() {}\n}\n"),
    ]);
    has(
        &got,
        &[
            // the method constructs; the class has the type as its base
            (".go()", "calls", "Callback"),
            ("Callback<String>", "implements", "Callback"),
            // a type the project does not define: the anonymous class is not it
            ("Runnable", "inherits", "Runnable"),
            // `new Helper(…)` runs the constructor Helper declares
            (".go()", "calls", "Helper"),
            (".go()", "calls", ".Helper()"),
            // inside, the names of the class around
            (".onResponse()", "calls", ".finish()"),
            (".onResponse()", "calls", ".run()"),
            ("Listener", "implements", "Listener"),
            (".onDone()", "calls", ".stop()"),
        ],
    );
    assert!(!got.contains(&(".go()".into(), "calls".into(), "Runnable".into())), "{got:#?}");
}

#[test]
fn a_construction_typed_by_its_declaration() {
    let got = edges("declared-new", &[
        ("B.cs", "namespace App;\npublic class Box {\n    public Box(int w, int h) {}\n}\npublic class UseBox {\n    public void Target() { Box b = new(1, 2); }\n}\n"),
        ("g.cpp", "class Buf {\n public:\n  Buf(int n) {}\n};\nvoid fill() { Buf b(3); }\nvoid plain() { int x(3); Buf* p = nullptr; }\n"),
    ]);
    has(
        &got,
        &[
            (".Target()", "calls", "Box"),
            (".Target()", "calls", ".Box()"),
            ("fill()", "calls", "Buf"),
            ("fill()", "calls", ".Buf()"),
        ],
    );
    assert!(!got.iter().any(|(s, r, _)| s == "plain()" && r == "calls"), "{got:#?}");
}

#[test]
fn a_function_returns_what_its_body_constructs() {
    let got = edges("returns", &[
        // an expression body, and a chain through it
        ("a.kt", "package app\nclass Builder(val n: String) {\n    fun named(t: String): Builder { return this }\n    fun build() = Spec()\n}\nclass Spec { fun emit() {} }\nfun builder(n: String) = Builder(n)\nfun use() {\n    val b = builder(\"x\")\n    b.named(\"t\")\n    builder(\"y\").build().emit()\n}\n"),
        // `return Conn()` beside `return None`, `return self`; two types: none
        ("b.py", "class Conn:\n    def open(self):\n        return self\n\n    def close(self):\n        pass\n\n\nclass Other:\n    def stop(self):\n        pass\n\n\ndef dial(host):\n    if not host:\n        return None\n    return Conn()\n\n\ndef mixed(x):\n    if x:\n        return Conn()\n    return Other()\n\n\ndef run():\n    dial(\"h\").open().close()\n\n\ndef unsure():\n    mixed(1).stop()\n"),
        // a closure's `return` is the closure's
        ("c.js", "class Queue { drain() {} }\nclass Other { halt() {} }\nclass Third { halt() {} }\nfunction makeQueue() { return new Queue(); }\nfunction each(xs) { xs.forEach(function (x) { return new Other(); }); }\nfunction run() {\n  const q = makeQueue();\n  q.drain();\n}\nfunction not() {\n  each([]).halt();\n}\n"),
        // Ruby: the last expression, and calls without parentheses
        ("d.rb", "class Stubs\n  def match(env); end\nend\nclass Client\n  def stubs\n    Stubs.new\n  end\n  def run\n    stubs.match(1)\n  end\n  def later\n    s = stubs\n    s.match(2)\n  end\nend\n"),
    ]);
    has(
        &got,
        &[
            ("use()", "calls", ".named()"),
            ("use()", "calls", ".build()"),
            ("use()", "calls", ".emit()"),
            ("run()", "calls", ".open()"),
            ("run()", "calls", ".close()"),
            ("run()", "calls", ".drain()"),
            (".run()", "calls", ".match()"),
            (".later()", "calls", ".match()"),
        ],
    );
    assert!(!got.contains(&("unsure()".into(), "calls".into(), ".stop()".into())), "{got:#?}");
    assert!(!got.contains(&("not()".into(), "calls".into(), ".halt()".into())), "{got:#?}");
    // the typed local `s` is no call of the type
    assert!(!got.contains(&(".later()".into(), "calls".into(), "Stubs".into())), "{got:#?}");
}

#[test]
fn a_kotlin_local_is_what_its_value_gives() {
    let got = edges("kotlin-locals", &[(
        "a.kt",
        "package app\n\ninterface Taggable {\n    fun tag(x: Any): Taggable\n}\nclass Writer(val indent: String) {\n    fun close() {}\n}\nclass Spec {\n    class Builder : Taggable {\n        override fun tag(x: Any): Builder = this\n        fun addMember(s: String): Builder = this\n    }\n    companion object {\n        fun builder(n: String): Builder = Builder()\n        fun get(a: String): Spec {\n            val builder = builder(a).tag(a)\n            builder.addMember(\"x\")\n            return Spec()\n        }\n    }\n}\nfun shut(indent: String) {\n    val c = Writer(indent)\n    c.close()\n}\n",
    )]);
    has(
        &got,
        &[
            // the value sits beside the declaration: `val c = Writer(…)`
            ("shut()", "calls", "Writer"),
            ("shut()", "calls", ".close()"),
            // in its own initializer the name is the outer one; afterwards
            // the local, typed through the chain
            (".get()", "calls", ".builder()"),
            (".get()", "calls", ".tag()"),
            (".get()", "calls", ".addMember()"),
        ],
    );
}

#[test]
fn a_bare_name_in_a_receiver_lambda_is_the_receivers() {
    let got = edges("receivers", &[(
        "a.kt",
        "package app\n\nclass Spec {\n    fun addMember(s: String): Spec = this\n    fun build(): Spec = this\n}\nclass Block {\n    class Builder {\n        fun add(s: String): Builder = this\n        fun indent(): Builder = this\n        fun withIndent(block: Builder.() -> Unit): Builder { block(); return this }\n    }\n}\nfun buildBlock(action: Block.Builder.() -> Unit): Block = Block()\n\nclass Maker {\n    fun add(s: String) {}\n    fun indent() {}\n    fun build() {}\n\n    fun typed(action: Int) {\n        buildBlock { indent() }\n    }\n    fun scoped(args: List<String>): Spec {\n        return Spec().apply { args.forEach { a -> addMember(a) } }\n    }\n    fun nested(b: Block.Builder) {\n        b.withIndent { add(\"x\") }\n        with(b) { indent() }\n    }\n    fun it(): Spec = Spec().also { build() }\n}\n",
    )]);
    let to = |from: &str, name: &str| -> Vec<String> {
        got.iter()
            .filter(|(s, r, t)| s == from && r == "calls" && t == name)
            .map(|(_, _, t)| t.clone())
            .collect()
    };
    // which `indent` / `add` / `build`: the receiver's, not the class's around
    let targets = |from: &str| -> BTreeSet<String> {
        got.iter()
            .filter(|(s, r, _)| s == from && r == "calls")
            .map(|(_, _, t)| t.clone())
            .collect()
    };
    assert_eq!(to(".typed()", ".indent()").len(), 1, "{got:#?}");
    assert!(targets(".scoped()").contains(".addMember()"), "{got:#?}");
    assert!(targets(".nested()").contains(".add()"), "{got:#?}");
    assert!(targets(".it()").contains(".build()"), "{got:#?}");
}

/// `(source label, target id)` of the call edges of a project, to tell two
/// methods of one name apart.
fn call_ids(name: &str, files: &[(&str, &str)]) -> BTreeSet<(String, String)> {
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
        .filter(|e| e["relation"] == "calls")
        .map(|e| (label(e["source"].as_str().unwrap()), e["target"].as_str().unwrap().to_string()))
        .collect()
}

#[test]
fn the_receiver_comes_before_the_class_around() {
    let got = call_ids("receiver-order", &[(
        "a.kt",
        "package app\n\nclass Builder {\n    fun indent(): Builder = this\n}\nfun buildBlock(action: Builder.() -> Unit) {}\n\nclass Maker {\n    fun indent() {}\n    fun inside() {\n        buildBlock { indent() }\n    }\n    fun outside() {\n        indent()\n    }\n    fun also(b: Builder) {\n        b.also { indent() }\n    }\n}\nclass Other {\n    fun indent(to: Builder, by: Int) {}\n    fun hands() {\n        buildBlock { indent(this, 2) }\n    }\n}\nclass Third {\n    fun indent(to: Builder) {}\n    fun gives() {\n        buildBlock { indent(this) }\n    }\n}\n",
    )]);
    let of = |from: &str| -> Vec<&str> {
        got.iter()
            .filter(|(s, t)| s == from && t.ends_with("_indent"))
            .map(|(_, t)| t.as_str())
            .collect()
    };
    assert_eq!(of(".inside()"), ["a_builder_indent"], "{got:#?}");
    // the receiver's `indent` takes nothing: two arguments go to the class's
    assert_eq!(of(".hands()"), ["a_other_indent"], "{got:#?}");
    // and the receiver handed over as `this` goes to the class's as well
    assert_eq!(of(".gives()"), ["a_third_indent"], "{got:#?}");
    assert_eq!(of(".outside()"), ["a_maker_indent"], "{got:#?}");
    // `also` passes the value as `it`: the lambda has no receiver
    assert_eq!(of(".also()"), ["a_maker_indent"], "{got:#?}");
}

#[test]
fn an_overload_is_the_one_the_arguments_are_for() {
    // by a literal's kind, an argument's type (a subclass counts), the count
    // with defaults and varargs, and the name an argument is passed under
    let got = call_ids("overloads", &[
        ("A.java", "class Shape {}\nclass Circle extends Shape {}\nclass Painter {\n  void draw(String name) {}\n  void draw(int count) {}\n  void draw(Shape s) {}\n  void draw(Shape s, int times) {}\n  void draw(String fmt, Object... args) {}\n  Painter(String title) {}\n  Painter(Shape seed) {}\n}\nclass Use {\n  void byString(Painter p) { p.draw(\"x\"); }\n  void byNumber(Painter p) { p.draw(3); }\n  void byObject(Painter p) { Circle c = new Circle(); p.draw(c); }\n  void byCount(Painter p, Shape s) { p.draw(s, 2); }\n  void varargs(Painter p) { p.draw(\"%s %s\", 1, 2); }\n  void ctorS() { new Painter(\"t\"); }\n  void ctorO(Circle c) { new Painter(c); }\n}\n"),
        ("b.kt", "package app\nclass TypeName\nclass KClassish\nclass Builder {\n    fun returns(t: TypeName, kdoc: String = \"\"): Builder = this\n    fun returns(t: KClassish, kdoc: String = \"\"): Builder = this\n    fun returns(t: KClassish, kdoc: String, vararg args: Any): Builder = this\n    fun named(a: Int = 0, b: String = \"\"): Builder = this\n    fun named(c: Boolean): Builder = this\n}\nfun byType(b: Builder, t: TypeName) { b.returns(t) }\nfun byOther(b: Builder) { val k = KClassish(); b.returns(k) }\nfun byName(b: Builder) { b.named(b = \"x\") }\nfun byBool(b: Builder) { b.named(true) }\n"),
        ("c.swift", "struct Frame {}\nstruct View {\n    init(frame: Frame) {}\n    init(name: String) {}\n    func add(_ v: View) {}\n    func add(named n: String) {}\n}\nfunc a() { let v = View(name: \"x\"); v.add(named: \"y\") }\nfunc b(f: Frame, w: View) { let v = View(frame: f); v.add(w) }\n"),
        ("d.cs", "namespace App;\npublic class Req {}\npublic class Client {\n    public void Send(string url) {}\n    public void Send(Req r) {}\n    public void Send(Req r, int retries = 1) {}\n}\npublic class U {\n    public void S(Client c) { c.Send(\"u\"); }\n    public void R(Client c, Req r) { c.Send(r); }\n    public void N(Client c, Req r) { c.Send(r, retries: 3); }\n}\n"),
    ]);
    for (from, to) in [
        (".byString()", "a_painter_draw"),
        (".byNumber()", "a_painter_draw_2"),
        (".byObject()", "a_painter_draw_3"),
        (".byCount()", "a_painter_draw_4"),
        (".varargs()", "a_painter_draw_5"),
        (".ctorS()", "a_painter_painter"),
        (".ctorO()", "a_painter_painter_2"),
        ("byType()", "b_builder_returns"),
        ("byOther()", "b_builder_returns_2"),
        ("byName()", "b_builder_named"),
        ("byBool()", "b_builder_named_2"),
        ("a()", "c_view_init_2"),
        ("a()", "c_view_add_2"),
        ("b()", "c_view_init"),
        ("b()", "c_view_add"),
        (".S()", "d_client_send"),
        (".R()", "d_client_send_2"),
        (".N()", "d_client_send_3"),
    ] {
        assert!(got.contains(&(from.to_string(), to.to_string())), "{from} -> {to}: {got:#?}");
    }
    // and no other overload of the name from the same caller
    for (from, to) in &got {
        let stem =
            |id: &str| id.trim_end_matches(|c: char| c == '_' || c.is_ascii_digit()).to_string();
        let same: Vec<_> = got.iter().filter(|(f, t)| f == from && stem(t) == stem(to)).collect();
        assert_eq!(same.len(), 1, "{from}: {same:?}");
    }
}

#[test]
fn a_result_that_is_a_type_parameter() {
    let got = edges("generic-results", &[
        // `tag` returns the type parameter its interface binds to itself:
        // the builder it was called on
        ("a.kt", "package app\n\ninterface Taggable<out T : Taggable<T>> {\n    fun tag(x: Any): T = TODO()\n}\nclass Builder : Taggable<Builder> {\n    fun addMember(s: String): Builder = this\n}\nfun make(): Builder = Builder()\nfun chain() {\n    val b = make().tag(\"x\")\n    b.addMember(\"m\")\n    make().tag(\"y\").addMember(\"n\")\n}\n"),
        // a call handed a type gives it, when it returns its own type parameter
        ("b.kt", "package app\n\nclass Retrofit {\n    fun <T> create(service: Class<T>): T = TODO()\n    inline fun <reified T> make(): T = TODO()\n    fun <T> listOfThem(service: Class<T>): List<T> = TODO()\n}\ninterface Service { fun body(): String }\ninterface Empty { fun nothing() }\ninterface Sized { fun size(): Int }\nfun generic(retrofit: Retrofit) {\n    val example = retrofit.create(Service::class.java)\n    example.body()\n    val e = retrofit.make<Empty>()\n    e.nothing()\n}\nfun wrapped(retrofit: Retrofit) {\n    val many = retrofit.listOfThem(Sized::class.java)\n    many.size()\n    val more = listOf<Sized>()\n    more.size()\n}\n"),
        // a function of a library, handed a type
        ("c.cs", "namespace App;\npublic interface IFoo { void Run(); }\npublic class U {\n    public void M(System.IServiceProvider sp) {\n        var s = sp.GetRequiredService<IFoo>();\n        s.Run();\n    }\n}\n"),
        // `expect` / `actual`: one declaration per source set, the nearest
        ("common/Text.kt", "package text\nexpect fun String.shout(): String\n"),
        ("jvm/Text.kt", "package text\nactual fun String.shout(): String = this\n"),
        ("js/Text.kt", "package text\nactual fun String.shout(): String = this\n"),
        ("jvm/Use.kt", "package text\nfun useJvm(s: String) = s.shout()\n"),
    ]);
    has(
        &got,
        &[
            ("chain()", "calls", ".addMember()"),
            ("generic()", "calls", ".body()"),
            ("generic()", "calls", ".nothing()"),
            (".M()", "calls", ".Run()"),
            ("useJvm()", "calls", "shout()"),
        ],
    );
    assert!(!got.contains(&("wrapped()".into(), "calls".into(), ".size()".into())), "{got:#?}");
}

#[test]
fn an_untyped_ruby_receiver_and_the_names_it_may_be_asked() {
    let got = edges("ruby-untyped", &[(
        "lib/far.rb",
        "class Env < Struct.new(:status, :body)\n  def needs_body?; end\nend\nclass Response\n  attr_reader :headers\n  def status; end\n  def finished?; end\n  def shift; end\nend\nclass Request\n  def headers; end\nend\nclass Adapter\n  def call(env, res, list)\n    env.needs_body?\n    res.finished?\n    env.status\n    res.headers\n    list.shift\n  end\nend\n",
    )]);
    has(
        &got,
        &[
            // only one type has a method of the name
            (".call()", "calls", ".needs_body?()"),
            (".call()", "calls", ".finished?()"),
        ],
    );
    // `status` is a member of the struct too, `headers` an attribute of
    // another class, `shift` what every array answers to
    for name in [".status()", ".headers()", ".shift()"] {
        assert!(
            !got.contains(&(".call()".into(), "calls".into(), name.into())),
            "{name}: {got:#?}"
        );
    }
}

#[test]
fn kotlin_constructors_primary_secondary_and_deprecated() {
    let got = call_ids("kotlin-ctors", &[(
        "a.kt",
        "package app\n\nclass Name internal constructor(names: List<String>, nullable: Boolean = false) {\n  @Deprecated(\"old\", level = DeprecationLevel.HIDDEN)\n  public constructor(packageName: String, simpleName: String, vararg simpleNames: String) :\n    this(listOf(packageName, simpleName, *simpleNames))\n\n  public constructor(packageName: String, vararg simpleNames: String) :\n    this(listOf(packageName, *simpleNames))\n\n  public constructor(packageName: String, simpleNames: List<String>) :\n    this(mutableListOf(packageName))\n}\nfun two() { Name(\"a\", \"b\") }\nfun flag() { Name(listOf(\"a\"), true) }\n",
    )]);
    let ctors = |from: &str| -> Vec<&str> {
        got.iter()
            .filter(|(s, t)| s == from && t.contains("constructor"))
            .map(|(_, t)| t.as_str())
            .collect()
    };
    // two strings: the vararg constructor that is not deprecated
    assert_eq!(ctors("two()"), ["a_name_constructor_2"], "{got:#?}");
    // a flag second: only the primary constructor takes one, and it is the
    // class's header, no definition of its own
    assert!(ctors("flag()").is_empty(), "{got:#?}");
}

#[test]
fn an_untyped_receiver_is_the_one_type_that_answers_all_asked_of_it() {
    let got = call_ids("structural", &[
        ("far.rb", "class Env < Struct.new(:status, :body, :url)\n  def needs_body?; end\n  def parallel?; end\nend\nclass Response\n  attr_reader :headers\n  def status; end\n  def body; end\n  def finished?; end\nend\nclass Request\n  attr_accessor :headers, :body\n  def url; end\nend\nclass Adapter\n  def call(env)\n    env.status\n    env.body\n    env.parallel?\n  end\n  def finish(res)\n    res.status\n    res.finished?\n  end\n  def one(x)\n    x.status\n  end\n  def both(y)\n    y.status\n    y.body\n  end\nend\n"),
        ("a.py", "class Order:\n    def total(self): ...\n    def lines(self): ...\n    def cancel(self): ...\n\n\nclass Cart:\n    def total(self): ...\n    def lines(self): ...\n    def checkout(self): ...\n\n\ndef bill(order):\n    order.total()\n    order.cancel()\n\n\ndef show(thing):\n    thing.total()\n    thing.lines()\n\n\ndef pay(cart):\n    cart.checkout()\n    cart.lines()\n    return cart.items()\n"),
        ("b.js", "class Queue { drain() {} flush() {} size() {} }\nclass Pipe { drain() {} pipe() {} }\nfunction run(q) { q.drain(); q.flush(); q.push(1); }\nfunction amb(q) { q.drain(); }\n"),
    ]);
    for (from, to) in [
        // `status` and `body` are members of the struct `Env` inherits, and
        // only `Env` answers `parallel?` as well
        (".call()", "far_env_parallel_pred"),
        (".finish()", "far_response_status"),
        (".finish()", "far_response_finished_pred"),
        ("bill()", "a_order_total"),
        ("bill()", "a_order_cancel"),
        ("pay()", "a_cart_checkout"),
        ("run()", "b_queue_drain"),
    ] {
        assert!(got.contains(&(from.to_string(), to.to_string())), "{from} -> {to}: {got:#?}");
    }
    // one name asked, or names two types answer alike: nothing is guessed
    for from in [".one()", ".both()", "show()", "amb()"] {
        assert!(!got.iter().any(|(s, _)| s == from), "{from}: {got:#?}");
    }
}

#[test]
fn overloads_by_fields_expressions_bases_and_ties() {
    let got = call_ids("overloads-more", &[("a.kt", "package app\nopen class TypeName\nclass ClassName : TypeName()\nval STRING: ClassName = ClassName()\nopen class Base {\n    fun returns(a: Int, b: Int): Base = this\n}\nclass Builder : Base() {\n    val owner: ClassName = ClassName()\n    val label: String = \"\"\n    fun returns(t: TypeName): Builder = this\n    fun returns(t: kotlin.reflect.KClass<*>): Builder = this\n    fun returns(t: String): Builder = this\n    fun returns(block: () -> Unit): Builder = this\n}\nfun byConstant(b: Builder) { b.returns(STRING) }\nfun byClassLiteral(b: Builder) { b.returns(String::class) }\nfun byField(b: Builder) { b.returns(b.owner) }\nfun byWritten(b: Builder) { b.returns(b.label) }\nfun byConcat(b: Builder, n: Any) { b.returns(\"a\" + n) }\nfun byLambda(b: Builder) { b.returns { } }\nfun byBase(b: Builder) { b.returns(1, 2) }\nfun undecided(b: Builder, x: Any) { b.returns(x) }\n")]);
    let to = |from: &str| -> Vec<&str> {
        got.iter()
            .filter(|(s, t)| s == from && t.contains("returns"))
            .map(|(_, t)| t.as_str())
            .collect()
    };
    // a constant's declared type, a class literal, a field's type, a field's
    // written `String`, a concatenation, a lambda, an overload of the base
    assert_eq!(to("byConstant()"), ["a_builder_returns"], "{got:#?}");
    assert_eq!(to("byClassLiteral()"), ["a_builder_returns_2"], "{got:#?}");
    assert_eq!(to("byField()"), ["a_builder_returns"], "{got:#?}");
    assert_eq!(to("byWritten()"), ["a_builder_returns_3"], "{got:#?}");
    assert_eq!(to("byConcat()"), ["a_builder_returns_3"], "{got:#?}");
    assert_eq!(to("byLambda()"), ["a_builder_returns_4"], "{got:#?}");
    assert_eq!(to("byBase()"), ["a_base_returns"], "{got:#?}");
    // an argument that says nothing: the call is of one of those that take
    // one argument, and each is named
    assert_eq!(to("undecided()").len(), 4, "{got:#?}");
}

#[test]
fn an_element_of_a_typed_collection_and_a_result_bound_by_an_argument() {
    let got = call_ids("elements", &[
        ("a.kt", "package app\nclass Foo { fun a() {}; fun b() {}; fun c() {}; fun d() {}; fun e() {}; fun g() {}; fun h() {} }\nclass Repo { fun all(): List<Foo> = emptyList(); val cached: List<Foo> = emptyList() }\nfun <T> pick(x: T): T = x\nfun <T> head(xs: List<T>): T = xs[0]\nfun loops(xs: List<Foo>, repo: Repo) {\n    for (x in xs) { x.a() }\n    xs.forEach { it.b() }\n    xs.map { y -> y.c() }\n    val z = xs[0]\n    z.d()\n    val w = xs.first()\n    w.e()\n    for (r in repo.all()) { r.g() }\n    repo.cached.forEach { it.h() }\n}\nfun bound(xs: List<Foo>, f: Foo) {\n    val p = pick(f)\n    p.a()\n    val q = head(xs)\n    q.b()\n}\n"),
        ("B.java", "import java.util.List;\nclass Item { void run() {} void stop() {} }\nclass Use {\n  void each(List<Item> items, Item[] arr) {\n    for (var i : items) { i.run(); }\n    for (var j : arr) { j.stop(); }\n    items.forEach(it -> it.run());\n  }\n}\n"),
        ("c.go", "package x\n\ntype Node struct{}\n\nfunc (n *Node) Visit() {}\nfunc (n *Node) Leave() {}\nfunc nodes() []*Node { return nil }\nfunc Walk(ns []*Node) {\n\tfor _, n := range ns {\n\t\tn.Visit()\n\t}\n\tfor _, m := range nodes() {\n\t\tm.Leave()\n\t}\n}\n"),
        ("d.py", "class Job:\n    def start(self): ...\n    def halt(self): ...\n\n\ndef run(jobs: list[Job]):\n    for j in jobs:\n        j.start()\n    first = jobs[0]\n    first.halt()\n"),
        ("e.ts", "export class Task { exec() {} cancel() {} }\nexport function go(tasks: Task[], more: Array<Task>) {\n  for (const t of tasks) { t.exec(); }\n  more.forEach(m => m.cancel());\n}\n"),
    ]);
    for (from, to) in [
        // a loop variable, `it`, a lambda's parameter, `xs[0]`, `xs.first()`,
        // a loop over a call's result, a field's `forEach`
        ("loops()", "a_foo_a"),
        ("loops()", "a_foo_b"),
        ("loops()", "a_foo_c"),
        ("loops()", "a_foo_d"),
        ("loops()", "a_foo_e"),
        ("loops()", "a_foo_g"),
        ("loops()", "a_foo_h"),
        // `fun <T> pick(x: T): T` and `fun <T> head(xs: List<T>): T`
        ("bound()", "a_foo_a"),
        ("bound()", "a_foo_b"),
        // Java `var` in a for-each over a list and over an array
        (".each()", "b_item_run"),
        (".each()", "b_item_stop"),
        // Go `range` over a slice and over a call's result
        ("Walk()", "c_node_visit"),
        ("Walk()", "c_node_leave"),
        ("run()", "d_job_start"),
        ("run()", "d_job_halt"),
        ("go()", "e_task_exec"),
        ("go()", "e_task_cancel"),
    ] {
        assert!(got.contains(&(from.to_string(), to.to_string())), "{from} -> {to}: {got:#?}");
    }
}

#[test]
fn typing_a_local_named_like_what_it_is_built_from_ends() {
    // `session` is assigned a call of `session`, and typing `request` comes
    // back to it through its arguments: this used to recurse without end
    let got = edges("self-typed", &[(
        "a.swift",
        "final class S {\n    func request(_ r: Int) -> S { self }\n    func exists(for r: S) -> Bool { true }\n}\nfunc session(using c: Int) -> S { S() }\nfunc test() {\n    let session = session(using: 1)\n    let request = session.request(2).request(3)\n    _ = session.exists(for: request)\n}\n",
    )]);
    has(&got, &[("test()", "calls", "session()"), ("test()", "calls", ".request()")]);
}

#[test]
fn a_chain_of_calls_on_a_collection() {
    // every method has a namesake on a `Decoy`, so its name alone says nothing
    let got = call_ids("chains", &[
        ("a.kt", "package app\nclass Decoy { fun a() {}; fun b() {}; fun c() {}; fun d() {}; fun run() {}; fun stop() {}; fun ping() {}; fun own() {} }\nclass Owner { fun run() {}; fun stop() {}; fun ping() {}; fun own() {} }\nclass Foo(val owner: Owner, val ok: Boolean) { fun a() {}; fun b() {}; fun c() {}; fun d() {}; fun boss(): Owner = owner; fun kids(): List<Foo> = emptyList() }\nfun chains(xs: List<Foo>) {\n    xs.filter { it.ok }.first().a()\n    xs.filter { it.ok }.forEach { it.b() }\n    xs.map { it.owner }.forEach { it.run() }\n    xs.map { it.boss() }.first().stop()\n    xs.sortedBy { it.ok }.map { x -> x.owner }.filter { true }.forEach { o -> o.ping() }\n    xs.flatMap { it.kids() }.forEach { it.c() }\n    val owners = xs.map { it.owner }\n    for (o in owners) { o.own() }\n    xs.filter { it.ok }.forEachIndexed { i, x -> x.d() }\n}\nfun unknown(xs: List<Foo>) {\n    xs.map { it.toString() }.forEach { it.a() }\n    xs.map { 1 }.first().b()\n}\n"),
        ("B.java", "import java.util.List;\nclass Bait { void run() {} void go() {} void end() {} }\nclass Boss { void run() {} void stop() {} }\nclass Item { Boss boss() { return null; } boolean ok() { return true; } void go() {} void end() {} }\nclass Use {\n  void chain(List<Item> items) {\n    items.stream().filter(i -> i.ok()).forEach(i -> i.go());\n    items.stream().map(i -> i.boss()).forEach(o -> o.run());\n    items.stream().filter(i -> i.ok()).findFirst().get().end();\n  }\n}\n"),
        ("e.ts", "export class Lure { run() {} exec() {} }\nexport class Lead { run() {} }\nexport class Task { lead: Lead; ok: boolean; exec() {} }\nexport function go(tasks: Task[]) {\n  tasks.filter(t => t.ok).forEach(t => t.exec());\n  tasks.filter(t => t.ok).map((t, i) => t.lead).forEach((o, i) => o.run());\n}\n"),
        ("d.py", "class Dummy:\n    def run(self): ...\n\n\nclass Chief:\n    def run(self): ...\n\n\nclass Job:\n    chief: Chief\n\n    def start(self): ...\n\n\ndef run(jobs: list[Job]):\n    chiefs = [j.chief for j in jobs]\n    for c in chiefs:\n        c.run()\n"),
        ("f.rs", "pub struct Fake;\nimpl Fake {\n    pub fn run(&self) {}\n    pub fn go(&self) {}\n}\npub struct Head;\nimpl Head { pub fn run(&self) {} }\npub struct Part { pub head: Head }\nimpl Part {\n    pub fn go(&self) {}\n    pub fn head(&self) -> &Head { &self.head }\n}\npub fn chain(parts: Vec<Part>) {\n    parts.iter().filter(|p| true).for_each(|p| p.go());\n    parts.iter().map(|p| p.head()).for_each(|h| h.run());\n}\n"),
        ("G.cs", "using System.Collections.Generic;\nusing System.Linq;\nclass Mock { public void Run() {} public void Go() {} public void End() {} }\nclass Chef { public void Run() {} }\nclass Dish { public Chef Chef; public bool Ok; public void Go() {} public void End() {} }\nclass Kitchen {\n  void Chain(List<Dish> dishes) {\n    dishes.Where(d => d.Ok).ToList().ForEach(d => d.Go());\n    foreach (var c in dishes.Select(d => d.Chef)) { c.Run(); }\n    dishes.Where(d => d.Ok).First().End();\n  }\n}\n"),
        ("h.swift", "class Ghost { func run() {}; func go() {} }\nclass Pilot { func run() {} }\nclass Plane { var pilot = Pilot(); var ok = true; func go() {} }\nfunc chain(planes: [Plane]) {\n    planes.filter { $0.ok }.forEach { $0.go() }\n    planes.map { $0.pilot }.forEach { $0.run() }\n    planes.map { p in p.pilot }.first!.run()\n}\n"),
    ]);
    let mut missing = Vec::new();
    for (from, to) in [
        ("chains()", "a_foo_a"),
        ("chains()", "a_foo_b"),
        ("chains()", "a_owner_run"),
        ("chains()", "a_owner_stop"),
        ("chains()", "a_owner_ping"),
        ("chains()", "a_foo_c"),
        ("chains()", "a_owner_own"),
        // the element is the second parameter of `forEachIndexed`'s lambda
        ("chains()", "a_foo_d"),
        (".chain()", "b_item_go"),
        (".chain()", "b_boss_run"),
        (".chain()", "b_item_end"),
        ("go()", "e_task_exec"),
        ("go()", "e_lead_run"),
        ("run()", "d_chief_run"),
        ("chain()", "f_part_go"),
        ("chain()", "f_head_run"),
        (".Chain()", "g_dish_go"),
        (".Chain()", "g_chef_run"),
        (".Chain()", "g_dish_end"),
        ("chain()", "h_plane_go"),
        ("chain()", "h_pilot_run"),
    ] {
        if !got.contains(&(from.to_string(), to.to_string())) {
            missing.push((from, to));
        }
    }
    assert!(missing.is_empty(), "missing {missing:?}\n{got:?}");
    // nothing goes to a namesake, a mapping whose result has no name is not
    // a collection of the same elements, and `first()` called on a collection
    // is the library's method, not the element's type
    for (from, to) in &got {
        assert!(
            from != "unknown()"
                && !["decoy", "bait", "lure", "dummy", "fake", "mock", "ghost"]
                    .iter()
                    .any(|d| to.contains(d))
                && !["a_foo", "a_owner", "b_item", "g_dish", "e_task", "h_plane"]
                    .contains(&to.as_str()),
            "{from} -> {to}: {got:?}"
        );
    }
}

#[test]
fn an_extension_on_a_closure_parameter_and_none_in_java() {
    let got = call_ids("closure-extension", &[
        // `error` is the closure's, not the property of that name; the
        // extension of a type the project does not define answers, and the
        // label says which overload
        ("a.swift", "struct AFError: Error {}\nextension Error {\n    func asAFError(orFailWith message: String) -> AFError { AFError() }\n    func asAFError(or other: AFError) -> AFError { other }\n}\nclass Task {\n    var error: AFError?\n    func mapError(_ f: (Error) -> AFError) {}\n    func run() {\n        mapError { error in\n            error.asAFError(or: AFError())\n        }\n    }\n}\n"),
        // a Kotlin extension is no method to Java: `latch.await()` is the
        // library's
        ("K.kt", "package app\nclass Call<T>\nfun <T> Call<T>.await(): T = TODO()\nfun <T> Call<T?>.await(): T? = null\n"),
        ("J.java", "package app;\nimport java.util.concurrent.CountDownLatch;\nclass J {\n  void wait(CountDownLatch latch) throws Exception { latch.await(); }\n}\n"),
    ]);
    assert!(got.contains(&(".run()".to_string(), "a_asaferror_2".to_string())), "{got:?}");
    assert!(!got.iter().any(|(from, _)| from == ".wait()"), "{got:?}");
}

#[test]
fn what_a_wrapper_holds_once_it_is_opened() {
    // every method has a namesake on a decoy type and on the wrapper's own
    // API, so a name alone says nothing
    let got = call_ids("wrappers", &[
        ("a.rs", "use std::sync::{Arc, Mutex};\npub struct Decoy;\nimpl Decoy {\n    pub fn get(&self) {}\n    pub fn run(&self) {}\n    pub fn stop(&self) {}\n    pub fn ping(&self) {}\n}\npub struct Pool;\nimpl Pool {\n    pub fn get(&self) {}\n    pub fn is_some(&self) {}\n}\npub struct Conn;\nimpl Conn {\n    pub fn run(&self) {}\n    pub fn stop(&self) {}\n    pub fn ping(&self) {}\n}\npub fn open() -> Result<Conn, String> { Ok(Conn) }\npub struct Client { pool: Arc<Mutex<Pool>>, conn: Option<Conn> }\nimpl Client {\n    pub fn go(&self) -> Result<(), String> {\n        self.pool.lock().unwrap().get();\n        self.conn.as_ref().unwrap().run();\n        open()?.stop();\n        let c = open();\n        c.unwrap().ping();\n        self.conn.is_some();\n        Ok(())\n    }\n    pub fn take_one(&self) {\n        let pool = self.pool.lock().unwrap();\n        pool.get();\n    }\n}\n"),
        ("B.java", "import java.util.Optional;\nclass Lure { void run() {} void get() {} }\nclass User { void run() {} void stop() {} void get() {} }\nclass Repo {\n  Optional<User> find() { return Optional.empty(); }\n  void use(Optional<User> u) {\n    find().get().run();\n    find().orElseThrow().stop();\n    u.ifPresent(x -> x.run());\n    find().isPresent();\n  }\n}\n"),
        ("c.ts", "export class Bait { load() {} save() {} }\nexport class Doc { load() {} save() {} then() {} }\nexport async function fetchDoc(): Promise<Doc> { return new Doc(); }\nexport async function go() {\n  (await fetchDoc()).load();\n  const d = await fetchDoc();\n  d.save();\n  fetchDoc().then();\n}\n"),
        // `var x = await f()` (no field names a C# declarator's value), and a
        // generic class whose base is the plain one of its name
        ("E.cs", "using System.Threading.Tasks;\npublic abstract class ResBase { public void Check() {} }\npublic class Res<T> : Res { }\npublic class Res : ResBase { }\npublic class Api { public async Task<Res> Load() { return null; } }\nclass Job {\n  async Task Run(Api api) {\n    var r = await api.Load();\n    r.Check();\n  }\n}\n"),
        ("D.cs", "using System.Threading.Tasks;\nclass Fake { public void Send() {} }\nclass Mail { public void Send() {} public void Wait() {} }\nclass Box {\n  Task<Mail> Make() { return null; }\n  async Task Go() {\n    (await Make()).Send();\n    Make().Result.Send();\n    Make().Wait();\n  }\n}\n"),
    ]);
    let mut missing = Vec::new();
    for (from, to) in [
        (".go()", "a_pool_get"),
        // a local assigned the opened wrapper (and `get` is no map's here)
        (".take_one()", "a_pool_get"),
        (".go()", "a_conn_run"),
        (".go()", "a_conn_stop"),
        (".go()", "a_conn_ping"),
        (".use()", "b_user_run"),
        (".use()", "b_user_stop"),
        ("go()", "c_doc_load"),
        ("go()", "c_doc_save"),
        (".Go()", "d_mail_send"),
        (".Run()", "e_resbase_check"),
    ] {
        if !got.contains(&(from.to_string(), to.to_string())) {
            missing.push((from, to));
        }
    }
    assert!(missing.is_empty(), "missing {missing:?}\n{got:?}");
    // the wrapper's own methods are the library's: `is_some()`, `isPresent()`,
    // `then()`, `Wait()`, `get()` on the optional
    for bad in ["a_pool_is_some", "b_user_get", "c_doc_then", "d_mail_wait"] {
        assert!(!got.iter().any(|(_, to)| to == bad), "{bad}: {got:?}");
    }
    for (from, to) in &got {
        assert!(
            !["decoy", "lure", "bait", "fake"].iter().any(|d| to.contains(d)),
            "{from} -> {to}"
        );
    }
}

#[test]
fn the_values_of_a_map_and_the_element_beside_an_index() {
    let got = call_ids("maps", &[
        ("a.kt", "package app\nclass Decoy { fun a() {}; fun b() {}; fun c() {}; fun d() {}; fun e() {}; fun g() {}; fun h() {} }\nclass Foo { fun a() {}; fun b() {}; fun c() {}; fun d() {}; fun e() {}; fun g() {}; fun h() {}; fun key() {} }\nfun maps(m: Map<String, Foo>, xs: List<Foo>) {\n    m[\"x\"]!!.a()\n    m.getValue(\"x\").b()\n    for ((k, v) in m) { v.c() }\n    m.values.forEach { it.d() }\n    m.forEach { (k, v) -> v.e() }\n    for ((i, x) in xs.withIndex()) { x.g() }\n    for (v in m.values) { v.h() }\n}\nfun entries(m: Map<String, Foo>) {\n    for (e in m) { e.key() }\n    m.forEach { it.key() }\n    m.first().key()\n}\n"),
        ("b.py", "class Dummy:\n    def run(self): ...\n    def lower(self): ...\n\n\nclass Job:\n    def run(self): ...\n    def lower(self): ...\n\n\ndef jobs(d: dict[str, Job]):\n    for k, v in d.items():\n        v.run()\n\n\ndef keys(d: dict[str, Job]):\n    for k in d:\n        k.lower()\n"),
        ("c.rs", "use std::collections::HashMap;\npub struct Fake;\nimpl Fake { pub fn go(&self) {} pub fn run(&self) {} }\npub struct Part;\nimpl Part { pub fn go(&self) {} pub fn run(&self) {} }\npub fn parts(m: HashMap<String, Part>, xs: Vec<Part>) {\n    for (k, v) in &m { v.go(); }\n    for (i, x) in xs.iter().enumerate() { x.run(); }\n}\n"),
        ("d.go", "package x\n\ntype Ghost struct{}\n\nfunc (g *Ghost) Visit() {}\n\ntype Node struct{}\n\nfunc (n *Node) Visit() {}\nfunc Walk(m map[string]*Node) {\n\tfor _, n := range m {\n\t\tn.Visit()\n\t}\n}\n"),
        ("E.cs", "using System.Collections.Generic;\nclass Mock { public void Send() {} }\nclass Mail { public void Send() {} }\nclass Box {\n  void Go(Dictionary<string, Mail> m) {\n    foreach (var (k, v) in m) { v.Send(); }\n  }\n}\n"),
        ("f.swift", "class Ghost { func fly() {} }\nclass Bird { func fly() {} }\nfunc birds(m: [String: Bird]) {\n    for (k, v) in m { v.fly() }\n}\n"),
    ]);
    let mut missing = Vec::new();
    for (from, to) in [
        ("maps()", "a_foo_a"),
        ("maps()", "a_foo_b"),
        ("maps()", "a_foo_c"),
        ("maps()", "a_foo_d"),
        ("maps()", "a_foo_e"),
        ("maps()", "a_foo_g"),
        ("maps()", "a_foo_h"),
        ("jobs()", "b_job_run"),
        ("parts()", "c_part_go"),
        ("parts()", "c_part_run"),
        ("Walk()", "d_node_visit"),
        (".Go()", "e_mail_send"),
        ("birds()", "f_bird_fly"),
    ] {
        if !got.contains(&(from.to_string(), to.to_string())) {
            missing.push((from, to));
        }
    }
    assert!(missing.is_empty(), "missing {missing:?}\n{got:?}");
    // iterating a map gives its entries (Python: its keys), and its
    // `first()` is an entry: none is a value
    for (from, to) in &got {
        assert!(from != "entries()" && from != "keys()", "{from} -> {to}");
        assert!(
            !["decoy", "dummy", "fake", "ghost", "mock"].iter().any(|d| to.contains(d)),
            "{from} -> {to}"
        );
    }
}

#[test]
fn what_the_type_arguments_of_a_declaration_bind() {
    // every method has a namesake on a decoy, so a name alone says nothing
    let got = call_ids("generics", &[
        ("G.java", "import java.util.List;\nclass Decoy { String getName() { return \"\"; } void ping() {} void stop() {} }\nclass Contact { String getName() { return \"\"; } void ping() {} void stop() {} }\nfinal class Response<T> { T body() { return null; } List<T> all() { return null; } T value; }\ninterface Call<T> { Response<T> execute(); }\nclass Use {\n  void body(Response<Contact> r) { r.body().getName(); }\n  void field(Response<Contact> r) { r.value.ping(); }\n  void list(Response<Contact> r) { for (var c : r.all()) { c.stop(); } }\n  void chain(Call<Contact> call) { call.execute().body().getName(); }\n  void declared(Call<Contact> call) {\n    Response<Contact> response = call.execute();\n    response.body().ping();\n  }\n}\n"),
        ("b.kt", "package app\nclass Ghost { fun run() {}; fun stop() {} }\nclass Owner { fun run() {}; fun stop() {} }\nclass Crate<T>(val item: T) { fun get(): T = item }\nfun boxes(b: Crate<Owner>) {\n    b.get().run()\n    b.item.stop()\n}\n"),
        ("c.ts", "export class Lure { greet() {} wave() {} }\nexport class User { greet() {} wave() {} }\nexport class Page<T> { items: T[] = []; first(): T { return this.items[0]; } }\nexport function pages(p: Page<User>) {\n  p.first().greet();\n  p.items.forEach(u => u.wave());\n}\n"),
        ("D.cs", "class Fake { public void Send() {} public void Open() {} }\nclass Mail { public void Send() {} public void Open() {} }\nclass Envelope<T> { public T Value; public T Get() { return Value; } }\nclass Post {\n  void Go(Envelope<Mail> e) {\n    e.Get().Send();\n    e.Value.Open();\n  }\n}\n"),
        ("e.swift", "class Ghost { func fly() {} }\nclass Bird { func fly() {} }\nstruct Wrapper<T> { let item: T\n    func get() -> T { item }\n}\nfunc birds(w: Wrapper<Bird>) {\n    w.get().fly()\n}\n"),
        ("f.rs", "pub struct Fake;\nimpl Fake { pub fn go(&self) {} }\npub struct Part;\nimpl Part { pub fn go(&self) {} }\npub struct Holder<T> { item: T }\nimpl<T> Holder<T> {\n    pub fn get(&self) -> &T { &self.item }\n}\npub fn parts(h: Holder<Part>) {\n    h.get().go();\n}\n"),
    ]);
    let mut missing = Vec::new();
    for (from, to) in [
        (".body()", "g_contact_getname"),
        (".field()", "g_contact_ping"),
        (".list()", "g_contact_stop"),
        (".chain()", "g_contact_getname"),
        (".declared()", "g_contact_ping"),
        ("boxes()", "b_owner_run"),
        ("boxes()", "b_owner_stop"),
        ("pages()", "c_user_greet"),
        ("pages()", "c_user_wave"),
        (".Go()", "d_mail_send"),
        (".Go()", "d_mail_open"),
        ("birds()", "e_bird_fly"),
        ("parts()", "f_part_go"),
    ] {
        if !got.contains(&(from.to_string(), to.to_string())) {
            missing.push((from, to));
        }
    }
    assert!(missing.is_empty(), "missing {missing:?}\n{got:?}");
    for (from, to) in &got {
        assert!(
            !["decoy", "ghost", "lure", "fake"].iter().any(|d| to.contains(d)),
            "{from} -> {to}: {got:?}"
        );
    }
}

#[test]
fn lambda_bodies_references_groups_and_lists_of_lists() {
    let got = call_ids("mapping", &[
        ("a.kt", "package app\nclass Decoy { fun a() {}; fun b() {}; fun c() {}; fun d() {}; fun e() {}; fun g() {}; fun h() {}; fun k() {} }\nclass Owner { fun a() {}; fun b() {}; fun c() {}; fun d() {}; fun e() {}; fun g() {}; fun h() {}; fun k() {} }\nclass Foo(val owner: Owner, val id: String) { fun a() {}; fun d() {}; fun e() {}; fun g() {}; fun h() {}; fun k() {} }\nfun mapping(xs: List<Foo>) {\n    xs.map { x ->\n        val o = x.owner\n        o\n    }.forEach { it.a() }\n    xs.map(Foo::owner).forEach { it.b() }\n    xs.map { x ->\n        println(x)\n        x.owner\n    }.first().c()\n    xs.chunked(2).forEach { chunk -> chunk.forEach { it.d() } }\n    xs.groupBy { it.id }.values.forEach { group -> group.first().e() }\n    xs.associateBy { it.id }.getValue(\"x\").g()\n    xs.reduce { a, b -> a }.h()\n    xs.chunked(2).flatten().forEach { it.k() }\n}\n"),
        ("B.java", "import java.util.List;\nclass Bait { void run() {} void go() {} }\nclass Boss { void run() {} void go() {} }\nclass Item { Boss getBoss() { return null; } }\nclass Use {\n  void chain(List<Item> items) {\n    items.stream().map(Item::getBoss).forEach(b -> b.run());\n    items.stream().map(i -> { var b = i.getBoss(); return b; }).forEach(b -> b.go());\n  }\n}\n"),
        ("c.swift", "class Ghost { func fly() {} }\nclass Pilot { func fly() {} }\nclass Plane { var pilot = Pilot() }\nfunc planes(ps: [Plane]) {\n    ps.map(\\.pilot).forEach { $0.fly() }\n}\n"),
        ("d.rs", "pub struct Fake;\nimpl Fake { pub fn go(&self) {} pub fn run(&self) {} }\npub struct Head;\nimpl Head { pub fn go(&self) {} pub fn run(&self) {} }\npub struct Part { head: Head }\nimpl Part {\n    pub fn head(&self) -> &Head { &self.head }\n}\npub fn parts(xs: Vec<Part>) {\n    xs.iter().map(Part::head).for_each(|h| h.go());\n    xs.iter().map(|p| { let h = p.head(); h }).for_each(|h| h.run());\n}\n"),
    ]);
    let mut missing = Vec::new();
    for (from, to) in [
        ("mapping()", "a_owner_a"),
        ("mapping()", "a_owner_b"),
        ("mapping()", "a_owner_c"),
        ("mapping()", "a_foo_d"),
        ("mapping()", "a_foo_e"),
        ("mapping()", "a_foo_g"),
        ("mapping()", "a_foo_h"),
        ("mapping()", "a_foo_k"),
        (".chain()", "b_boss_run"),
        (".chain()", "b_boss_go"),
        ("planes()", "c_pilot_fly"),
        ("parts()", "d_head_go"),
        ("parts()", "d_head_run"),
    ] {
        if !got.contains(&(from.to_string(), to.to_string())) {
            missing.push((from, to));
        }
    }
    assert!(missing.is_empty(), "missing {missing:?}\n{got:?}");
    for (from, to) in &got {
        assert!(
            !["decoy", "bait", "ghost", "fake"].iter().any(|d| to.contains(d)),
            "{from} -> {to}: {got:?}"
        );
    }
}

#[test]
fn a_tuple_taken_apart() {
    // every method has a namesake on the other part and on a decoy
    let got = call_ids("tuples", &[
        ("a.rs", "pub struct Fake;\nimpl Fake { pub fn go(&self) {} pub fn stop(&self) {} }\npub struct Agent;\nimpl Agent { pub fn go(&self) {} pub fn stop(&self) {} }\npub struct Clock;\nimpl Clock { pub fn go(&self) {} pub fn stop(&self) {} }\nfn setup() -> (Agent, Clock) { (Agent, Clock) }\npub fn run() {\n    let (mut agent, clock) = setup();\n    agent.go();\n    clock.stop();\n}\n"),
        ("B.cs", "class Mock { public void Send() {} public void Check() {} }\nclass Client { public void Send() {} public void Check() {} }\nclass Probe { public void Send() {} public void Check() {} }\nclass T {\n  (Client, Probe) Setup() { return (null, null); }\n  void Run() {\n    var (client, probe) = Setup();\n    client.Send();\n    probe.Check();\n  }\n}\n"),
        ("c.kt", "package app\nclass Ghost { fun a() {}; fun b() {} }\nclass Left { fun a() {}; fun b() {} }\nclass Right { fun a() {}; fun b() {} }\nfun two(): Pair<Left, Right> = Pair(Left(), Right())\nfun pairs() {\n    val (l, r) = two()\n    l.a()\n    r.b()\n}\n"),
        ("d.swift", "class Ghost { func up() {}; func down() {} }\nclass Top { func up() {}; func down() {} }\nclass Bottom { func up() {}; func down() {} }\nfunc split() -> (Top, Bottom) { (Top(), Bottom()) }\nfunc halves() {\n    let (t, b) = split()\n    t.up()\n    b.down()\n}\n"),
        ("e.ts", "export class Lure { open() {} shut() {} }\nexport class Door { open() {} shut() {} }\nexport class Lock { open() {} shut() {} }\nfunction parts(): [Door, Lock] { return [new Door(), new Lock()]; }\nexport function house() {\n  const [door, lock] = parts();\n  door.open();\n  lock.shut();\n}\n"),
        ("f.go", "package x\n\ntype Ghost struct{}\n\nfunc (g *Ghost) Ping() {}\nfunc (g *Ghost) Pong() {}\n\ntype Ping struct{}\n\nfunc (p *Ping) Ping() {}\nfunc (p *Ping) Pong() {}\n\ntype Pong struct{}\n\nfunc (p *Pong) Ping() {}\nfunc (p *Pong) Pong() {}\nfunc both() (*Ping, *Pong) { return nil, nil }\nfunc Play() {\n\ta, b := both()\n\ta.Ping()\n\tb.Pong()\n}\n"),
    ]);
    let mut missing = Vec::new();
    for (from, to) in [
        ("run()", "a_agent_go"),
        ("run()", "a_clock_stop"),
        (".Run()", "b_client_send"),
        (".Run()", "b_probe_check"),
        ("pairs()", "c_left_a"),
        ("pairs()", "c_right_b"),
        ("halves()", "d_top_up"),
        ("halves()", "d_bottom_down"),
        ("house()", "e_door_open"),
        ("house()", "e_lock_shut"),
        ("Play()", "f_ping_ping"),
        ("Play()", "f_pong_pong"),
    ] {
        if !got.contains(&(from.to_string(), to.to_string())) {
            missing.push((from, to));
        }
    }
    assert!(missing.is_empty(), "missing {missing:?}\n{got:?}");
    // no part is the other one, and nothing goes to a decoy
    for bad in [
        "a_agent_stop",
        "a_clock_go",
        "b_client_check",
        "b_probe_send",
        "c_left_b",
        "c_right_a",
        "d_top_down",
        "d_bottom_up",
        "e_door_shut",
        "e_lock_open",
        "f_ping_pong",
        "f_pong_ping",
    ] {
        assert!(!got.iter().any(|(_, to)| to == bad), "{bad}: {got:?}");
    }
    for (from, to) in &got {
        assert!(
            !["fake", "mock", "ghost", "lure"].iter().any(|d| to.contains(d)),
            "{from} -> {to}"
        );
    }
}

#[test]
fn numbered_fields_and_the_parts_of_a_tuple_a_local_holds() {
    let got = call_ids("tuple-fields", &[
        ("a.rs", "use std::sync::{Arc, Mutex};\npub struct Fake;\nimpl Fake { pub fn buffers(&self) {} pub fn bump(&self) {} pub fn go(&self) {} pub fn stop(&self) {} }\npub trait Transport { fn buffers(&self); }\npub struct State;\nimpl State { pub fn bump(&self) {} }\npub struct Tunnel(Box<dyn Transport>);\nimpl Tunnel {\n    pub fn bufs(&self) { self.0.buffers(); }\n}\npub struct Script(Arc<Mutex<State>>);\nimpl Script {\n    pub fn step(&self) { self.0.lock().unwrap().bump(); }\n}\npub struct Agent;\nimpl Agent { pub fn go(&self) {} pub fn stop(&self) {} }\npub struct Clock;\nimpl Clock { pub fn go(&self) {} pub fn stop(&self) {} }\nfn setup() -> (Agent, Clock) { (Agent, Clock) }\npub fn run() {\n    let pair = setup();\n    pair.0.go();\n    pair.1.stop();\n}\n"),
        ("b.swift", "class Ghost { func up() {}; func down() {} }\nclass Top { func up() {}; func down() {} }\nclass Bottom { func up() {}; func down() {} }\nfunc split() -> (Top, Bottom) { (Top(), Bottom()) }\nfunc halves() {\n    let both = split()\n    both.1.down()\n}\n"),
        ("C.cs", "class Mock { public void Send() {} public void Check() {} }\nclass Client { public void Send() {} public void Check() {} }\nclass Probe { public void Send() {} public void Check() {} }\nclass T {\n  (Client, Probe) Setup() { return (null, null); }\n  void Run() {\n    var pair = Setup();\n    pair.Item2.Check();\n  }\n}\n"),
        ("d.kt", "package app\nclass Ghost { fun a() {}; fun b() {} }\nclass Left { fun a() {}; fun b() {} }\nclass Right { fun a() {}; fun b() {} }\nfun two(): Pair<Left, Right> = Pair(Left(), Right())\nfun pairs() {\n    val p = two()\n    p.second.b()\n}\n"),
    ]);
    let mut missing = Vec::new();
    for (from, to) in [
        (".bufs()", "a_transport_buffers"),
        (".step()", "a_state_bump"),
        ("run()", "a_agent_go"),
        ("run()", "a_clock_stop"),
        ("halves()", "b_bottom_down"),
        (".Run()", "c_probe_check"),
        ("pairs()", "d_right_b"),
    ] {
        if !got.contains(&(from.to_string(), to.to_string())) {
            missing.push((from, to));
        }
    }
    assert!(missing.is_empty(), "missing {missing:?}\n{got:?}");
    for bad in ["a_agent_stop", "a_clock_go", "b_top_down", "c_client_check", "d_left_b"] {
        assert!(!got.iter().any(|(_, to)| to == bad), "{bad}: {got:?}");
    }
    for (from, to) in &got {
        assert!(!["fake", "mock", "ghost"].iter().any(|d| to.contains(d)), "{from} -> {to}");
    }
}

#[test]
fn generics_through_values_nested_arguments_and_bases() {
    let got = call_ids("generics2", &[
        ("A.java", "import java.util.List;\nclass Decoy { void hi() {} void wave() {} void nod() {} }\nclass User { void hi() {} void wave() {} void nod() {} }\nfinal class Response<T> { T body() { return null; } }\ninterface Call<T> { Response<T> execute(); }\nclass Use {\n  void value(Call<User> call) {\n    var resp = call.execute();\n    resp.body().hi();\n  }\n  void nested(Call<List<User>> call) {\n    call.execute().body().get(0).wave();\n    Response<List<User>> r = call.execute();\n    for (var u : r.body()) { u.nod(); }\n  }\n}\n"),
        ("b.kt", "package app\nclass Ghost { fun a() {}; fun hi() {}; fun up() {} }\nclass Foo { fun a() {} }\nclass Person { fun hi() {}; fun up() {} }\nclass Repo { fun all(): List<Foo> = emptyList() }\nopen class Store<T> { fun find(): T? = null }\nopen class Mid<T> : Store<T>()\nclass People : Store<Person>()\nclass Crowd : Mid<Person>()\nfun loop(repo: Repo) {\n    val xs = repo.all()\n    for (x in xs) { x.a() }\n}\nfun bases(p: People, c: Crowd) {\n    p.find()!!.hi()\n    c.find()!!.up()\n}\n"),
        ("C.cs", "class Fake { public void Hi() {} }\nclass Member { public void Hi() {} }\nclass Repo<T> { public T Find() { return default; } }\nclass Members : Repo<Member> { }\nclass Use { void Go(Members m) { m.Find().Hi(); } }\n"),
        ("d.ts", "export class Lure { greet() {} }\nexport class Guest { greet() {} }\nexport class Repo<T> { find(): T { return null as any; } }\nexport class Guests extends Repo<Guest> {}\nexport function party(g: Guests) { g.find().greet(); }\n"),
    ]);
    let mut missing = Vec::new();
    for (from, to) in [
        (".value()", "a_user_hi"),
        (".nested()", "a_user_wave"),
        (".nested()", "a_user_nod"),
        ("loop()", "b_foo_a"),
        ("bases()", "b_person_hi"),
        ("bases()", "b_person_up"),
        (".Go()", "c_member_hi"),
        ("party()", "d_guest_greet"),
    ] {
        if !got.contains(&(from.to_string(), to.to_string())) {
            missing.push((from, to));
        }
    }
    assert!(missing.is_empty(), "missing {missing:?}\n{got:?}");
    for (from, to) in &got {
        assert!(
            !["decoy", "ghost", "fake", "lure"].iter().any(|d| to.contains(d)),
            "{from} -> {to}: {got:?}"
        );
    }
}

#[test]
fn keys_partitions_zips_folds_and_a_returned_function() {
    let got = call_ids("rest", &[
        ("a.kt", "package app\nclass Decoy { fun a() {}; fun b() {}; fun c() {}; fun d() {}; fun e() {}; fun g() {}; fun h() {}; fun k() {}; fun m() {} }\nclass Key { fun a() {}; fun b() {}; fun c() {} }\nclass Foo(val owner: Owner) { fun d() {}; fun e() {}; fun g() {}; fun k() {} }\nclass Owner { fun h() {}; fun m() {} }\nclass Acc { fun k() {} }\nfun keys(m: Map<Key, Foo>, xs: List<Foo>, ys: List<Owner>) {\n    for ((k, v) in m) { k.a() }\n    m.keys.forEach { it.b() }\n    m.forEach { (k, v) -> k.c() }\n    val (ok, bad) = xs.partition { true }\n    ok.forEach { it.d() }\n    for (b in bad) { b.e() }\n    for ((x, y) in xs.zip(ys)) { x.g(); y.h() }\n    xs.fold(Acc()) { acc, x -> acc }.k()\n    xs.associateWith { it.owner }.values.forEach { it.m() }\n}\n"),
        ("b.ts", "export class Lure { copy() {} }\ntype CopyFn = (text: string) => boolean;\nexport function useCopy(): [string, CopyFn] {\n  const copy: CopyFn = (text) => true;\n  return [\"\", copy];\n}\nexport function button() {\n  const [text, copy] = useCopy();\n  copy(text);\n}\n"),
        ("C.java", "import java.util.Map;\nclass Bait { void a() {} void b() {} }\nclass Id { void a() {} }\nclass Row { void b() {} }\nclass Use {\n  void go(Map<Id, Row> m) {\n    m.forEach((k, v) -> { k.a(); v.b(); });\n    for (Id k : m.keySet()) { k.a(); }\n  }\n}\n"),
    ]);
    let mut missing = Vec::new();
    for (from, to) in [
        ("keys()", "a_key_a"),
        ("keys()", "a_key_b"),
        ("keys()", "a_key_c"),
        ("keys()", "a_foo_d"),
        ("keys()", "a_foo_e"),
        ("keys()", "a_foo_g"),
        ("keys()", "a_owner_h"),
        ("keys()", "a_acc_k"),
        ("keys()", "a_owner_m"),
        ("button()", "b_usecopy_copy"),
        (".go()", "c_id_a"),
        (".go()", "c_row_b"),
    ] {
        if !got.contains(&(from.to_string(), to.to_string())) {
            missing.push((from, to));
        }
    }
    assert!(missing.is_empty(), "missing {missing:?}\n{got:?}");
    // a key is no value, and nothing goes to a decoy
    for bad in ["a_foo_a", "a_foo_b", "a_foo_c"] {
        assert!(!got.iter().any(|(_, to)| to == bad), "{bad}: {got:?}");
    }
    for (from, to) in &got {
        assert!(
            !["decoy", "lure", "bait"].iter().any(|d| to.contains(d)),
            "{from} -> {to}: {got:?}"
        );
    }
}

#[test]
fn the_calls_in_a_rust_macros_arguments() {
    let got = call_ids("macros", &[(
        "a.rs",
        "pub struct Pool;\nimpl Pool {\n    pub fn count(&self) -> usize { 0 }\n    pub fn label(&self) -> String { String::new() }\n}\npub enum Kind { A(u8), B }\nfn handle_count(p: &Pool) -> usize { p.count() }\nfn make_pool() -> Pool { Pool }\nfn kind_of(p: &Pool) -> Kind { Kind::B }\nfn never_called() {}\npub fn checks() {\n    let pool = make_pool();\n    assert_eq!(handle_count(&pool), 2);\n    assert!(pool.count() > 0, \"empty: {}\", pool.label());\n    let pools = vec![make_pool(), make_pool()];\n    assert!(matches!(kind_of(&pool), Kind::A(never_called)));\n    println!(\"{}\", pools.len());\n}\n",
    )]);
    let mut missing = Vec::new();
    for (from, to) in [
        ("checks()", "a_handle_count"),
        ("checks()", "a_pool_count"),
        ("checks()", "a_pool_label"),
        ("checks()", "a_make_pool"),
        // inside a macro inside a macro
        ("checks()", "a_kind_of"),
    ] {
        if !got.contains(&(from.to_string(), to.to_string())) {
            missing.push((from, to));
        }
    }
    assert!(missing.is_empty(), "missing {missing:?}\n{got:?}");
    // the pattern of `matches!` is no expression: nothing in it is called
    assert!(!got.iter().any(|(_, to)| to == "a_never_called" || to.contains("kind_a")), "{got:?}");
}

#[test]
fn a_swift_implicit_member_is_typed_by_its_place() {
    // `Decoy` has every static name too: only the place says which type
    let got = call_ids("implicit", &[(
        "a.swift",
        "struct Decoy {\n    static func status(_ code: Int) -> Decoy { Decoy() }\n    static func method(_ m: Int) -> Decoy { Decoy() }\n    static func named(_ n: Int) -> Decoy { Decoy() }\n    static func made(_ n: Int) -> Decoy { Decoy() }\n    static func given(_ n: Int) -> Decoy { Decoy() }\n    static func lost(_ n: Int) -> Decoy { Decoy() }\n}\nstruct Endpoint {\n    static func status(_ code: Int) -> Endpoint { Endpoint() }\n    static func method(_ m: Int) -> Endpoint { Endpoint() }\n    static func named(_ n: Int) -> Endpoint { Endpoint() }\n    static func made(_ n: Int) -> Endpoint { Endpoint() }\n    static func given(_ n: Int) -> Endpoint { Endpoint() }\n    static func lost(_ n: Int) -> Endpoint { Endpoint() }\n}\nclass Session {\n    func request(_ endpoint: Endpoint, retries: Int = 0) -> Session { self }\n    func send(to endpoint: Endpoint) {}\n}\nfunc positional(session: Session) {\n    session.request(.status(301))\n    session.request(.method(1), retries: 2)\n}\nfunc labelled(session: Session) {\n    session.send(to: .named(1))\n}\nfunc declared() {\n    let e: Endpoint = .made(1)\n    print(e)\n}\nfunc returned() -> Endpoint {\n    return .given(2)\n}\nfunc unknown(x: Int) {\n    somethingElse(.lost(1))\n}\n",
    )]);
    let mut missing = Vec::new();
    for (from, to) in [
        ("positional()", "a_endpoint_status"),
        ("positional()", "a_endpoint_method"),
        ("labelled()", "a_endpoint_named"),
        ("declared()", "a_endpoint_made"),
        ("returned()", "a_endpoint_given"),
    ] {
        if !got.contains(&(from.to_string(), to.to_string())) {
            missing.push((from, to));
        }
    }
    assert!(missing.is_empty(), "missing {missing:?}\n{got:?}");
    // a place that gives no type names nothing, and nothing is the decoy's
    for (from, to) in &got {
        assert!(from != "unknown()" && !to.contains("decoy_"), "{from} -> {to}: {got:?}");
    }
}

#[test]
fn an_implicit_member_picks_the_overload_an_extension_adds() {
    // the type's own `request` takes a protocol; its extension's takes the
    // `Endpoint` whose static member the argument is
    let got = call_ids("implicit-overload", &[(
        "a.swift",
        "protocol Convertible {}\nstruct Other { static func status(_ c: Int) -> Other { Other() } }\nstruct Endpoint: Convertible {\n    static func status(_ code: Int) -> Endpoint { Endpoint() }\n}\nclass Session {\n    func request(_ convertible: any Convertible, retries: Int = 0) -> Session { self }\n}\nextension Session {\n    func request(_ endpoint: Endpoint, retries: Int = 0) -> Session { self }\n}\nfunc run(session: Session) {\n    session.request(.status(401), retries: 1)\n}\n",
    )]);
    let to: Vec<&str> =
        got.iter().filter(|(from, _)| from == "run()").map(|(_, to)| to.as_str()).collect();
    assert_eq!(to, ["a_endpoint_status", "a_session_request_2"], "{got:?}");
}

#[test]
fn a_trailing_closure_picks_the_parameter_that_asks_for_no_label() {
    // the type's own `validate` takes a closure under no label; its
    // extension's take a labelled sequence, a labelled autoclosure, nothing
    let got = call_ids("trailing", &[(
        "a.swift",
        "typealias Validation = (Int) -> Bool\nclass Request {\n    func validate(_ validation: @escaping Validation) -> Self { self }\n    func check() {\n        validate { _ in true }\n    }\n}\nextension Request {\n    func validate<S: Sequence>(statusCode codes: S) -> Self { self }\n    func validate<S: Sequence>(contentType types: @escaping @autoclosure () -> S) -> Self { self }\n    func validate() -> Self { self }\n}\n",
    )]);
    let to: Vec<&str> =
        got.iter().filter(|(from, _)| from == ".check()").map(|(_, to)| to.as_str()).collect();
    assert_eq!(to, ["a_request_validate"], "{got:?}");
}

/// `(source label, target id, confidence)` of every `calls` edge.
fn calls_conf(name: &str, files: &[(&str, &str)]) -> BTreeSet<(String, String, String)> {
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
        .filter(|e| e["relation"] == "calls")
        .map(|e| {
            (
                label(e["source"].as_str().unwrap()),
                e["target"].as_str().unwrap().to_string(),
                e["confidence"].as_str().unwrap().to_string(),
            )
        })
        .collect()
}

#[test]
fn a_commonjs_module_is_what_it_assigns_to_its_exports() {
    // `require` gives what the module assigns to `module.exports`: a class,
    // a function, or the module another one passes on. A module that only
    // names its exports is called by none of this
    let got = calls_conf("cjsdefault", &[
        ("foo.js", "class Foo {\n  run() { return 1; }\n  static create() { return new Foo(); }\n}\nmodule.exports = Foo;\n"),
        ("make.js", "module.exports = function makeThing() { return 2; };\n"),
        ("lib/app.js", "var proto = require('./proto');\nexports = module.exports = createApp;\nfunction createApp() { return {}; }\nexports.proto = proto;\n"),
        ("lib/proto.js", "var proto = module.exports = {};\nproto.boot = function boot() { return 3; };\n"),
        ("index.js", "module.exports = require('./lib/app');\n"),
        ("names.js", "exports.one = function one() { return 1; };\n"),
        ("user.js", "const Foo = require('./foo');\nconst make = require('./make');\nconst app = require('./');\nconst names = require('./names');\n\nfunction go() {\n  const f = new Foo();\n  f.run();\n  Foo.create();\n  make();\n  app();\n  app.proto.boot();\n  names();\n}\n"),
    ]);
    let from_go: BTreeSet<(&str, &str)> = got
        .iter()
        .filter(|(from, _, _)| from == "go()")
        .map(|(_, to, conf)| (to.as_str(), conf.as_str()))
        .collect();
    let want: BTreeSet<(&str, &str)> = [
        ("foo_foo", "EXTRACTED"),
        ("foo_foo_run", "INFERRED"),
        ("foo_foo_create", "EXTRACTED"),
        ("make_makething", "EXTRACTED"),
        ("lib_app_createapp", "EXTRACTED"),
        ("lib_proto_boot", "EXTRACTED"),
    ]
    .into_iter()
    .collect();
    assert_eq!(from_go, want, "{got:#?}");
}

#[test]
fn a_method_named_on_what_a_test_file_cannot_type() {
    // the package's entry passes its files on: a test that requires it has
    // their methods in sight. A closure's parameter and a module variable
    // holding what the walk cannot type are receivers it cannot name: a
    // distinctive name says what is called. Not on a library's value, not by
    // a name every emitter answers to, not for a function the module exports,
    // and not from a file that imports none of it
    let lib = [
        ("index.js", "module.exports = require('./lib/app');\n"),
        ("lib/app.js", "var proto = require('./proto');\nvar res = require('./response');\nvar util = require('./util');\nexports = module.exports = createApp;\nfunction createApp() {\n  var app = function () {};\n  return app;\n}\nexports.proto = proto;\nexports.response = res;\nexports.util = util;\n"),
        ("lib/proto.js", "var app = module.exports = {};\napp.enable = function enable(name) { return name; };\napp.listen = function listen() { return 1; };\napp.once = function once() { return 2; };\n"),
        ("lib/response.js", "var res = module.exports = {};\nres.sendFile = function sendFile(path) { return path; };\n"),
        ("lib/util.js", "exports.helper = function helper() { return 1; };\n"),
    ];
    let mut files = lib.to_vec();
    files.push((
        "test/send.js",
        "var make = require('../');\nvar http = require('http');\nvar app = module.exports = make();\nvar server = http.createServer(app);\n\napp.enable('x');\nserver.listen(3000);\napp.once('close');\n\ndescribe('res', function () {\n  it('sends', function (done) {\n    app.use(function (req, res) {\n      res.sendFile('x');\n      res.helper();\n    });\n  });\n});\n",
    ));
    files.push(("other/alone.js", "function run(res) {\n  res.sendFile('x');\n}\n"));
    let got = calls_conf("cjssight", &files);
    let from = |label: &str| -> BTreeSet<(&str, &str)> {
        got.iter()
            .filter(|(from, _, _)| from == label)
            .map(|(_, to, conf)| (to.as_str(), conf.as_str()))
            .collect()
    };
    let want: BTreeSet<(&str, &str)> = [
        ("lib_app_createapp", "EXTRACTED"),
        ("ext_npm_http", "EXTRACTED"),
        ("lib_proto_enable", "INFERRED"),
        ("lib_response_sendfile", "INFERRED"),
    ]
    .into_iter()
    .collect();
    assert_eq!(from("send.js"), want, "{got:#?}");
    assert!(from("run()").is_empty(), "{got:#?}");
}

#[test]
fn csharp_extensions_through_blocks_bases_and_library_types() {
    // an `extension(T x) { … }` block's members are written for `T`; a
    // receiver has the extensions of its bases, a partial type the bases and
    // members of all its declarations, and a namespace sees the ones around
    // it. A field or property of a type the project does not define is
    // answered by the extension written for that type, never by a name
    // .NET's own lists answer to
    let got = calls_conf("csextensions", &[
        ("Shop/Client.cs", "namespace Shop;\n\npublic interface IClient { Uri Make(Request r); }\n\npublic partial class Client : IClient {\n    public Uri Make(Request r) => null;\n}\n\npublic class Request {\n    public string Name { get; set; }\n}\n"),
        ("Shop/Client.Async.cs", "namespace Shop;\n\npublic partial class Client {\n    public int Later() => 1;\n}\n"),
        ("Shop/ClientExtensions.cs", "namespace Shop;\n\npublic static class ClientExtensions {\n    extension(IClient client) {\n        public Uri BuildUri(Request request) {\n            return client.Make(request);\n        }\n\n        public Uri BuildUri(Request request, bool full) {\n            return client.Make(request);\n        }\n    }\n\n    public static int Count(this IClient client) => 1;\n}\n\npublic static class StringExtensions {\n    extension(string input) {\n        public bool IsBlank() {\n            return input.Length == 0;\n        }\n    }\n\n    public static bool IsNotEmpty(this string? value) => value != null;\n}\n"),
        ("Shop/WireExtensions.cs", "using Wire;\n\nnamespace Shop;\n\npublic static class WireExtensions {\n    public static int Capture(this WireServer server, int n) => n;\n    public static int Capture(this WireMessage message) => 1;\n}\n\npublic static class ListExtensions {\n    public static void ForEach<T>(this IEnumerable<T> items) {}\n}\n"),
        ("Shop/Use.cs", "using Wire;\n\nnamespace Shop;\n\npublic class Use {\n    readonly Client _client = new();\n    readonly WireServer _server = new();\n    readonly List<int> _items = new();\n\n    public void Run(Request request, IClient other) {\n        _client.BuildUri(request);\n        other.BuildUri(request, true);\n        _client.Count();\n        _server.Capture(1);\n        request.Name.IsNotEmpty();\n        request.Name.IsBlank();\n        _items.ForEach();\n    }\n\n    public void Send() {\n        var message = new WireMessage();\n        message.Capture();\n    }\n}\n"),
        ("Shop.Tests/UseTests.cs", "namespace Shop.Tests;\n\npublic class UseTests {\n    public void Builds(Client client, Request request) {\n        client.BuildUri(request);\n        client.Later();\n        client.Make(request);\n    }\n}\n"),
    ]);
    let from = |label: &str| -> BTreeSet<(&str, &str)> {
        got.iter()
            .filter(|(from, _, _)| from == label)
            .map(|(_, to, conf)| (to.as_str(), conf.as_str()))
            .collect()
    };
    let block = "shop_clientextensions_clientextensions_extension_builduri";
    let two = "shop_clientextensions_clientextensions_extension_builduri_2";
    let want: BTreeSet<(&str, &str)> = [
        (block, "EXTRACTED"),
        (two, "EXTRACTED"),
        ("shop_clientextensions_clientextensions_count", "EXTRACTED"),
        ("shop_wireextensions_wireextensions_capture", "EXTRACTED"),
        ("shop_clientextensions_stringextensions_isnotempty", "EXTRACTED"),
        ("shop_clientextensions_stringextensions_extension_isblank", "EXTRACTED"),
    ]
    .into_iter()
    .collect();
    assert_eq!(from(".Run()"), want, "{got:#?}");
    // (`var message = new WireMessage()`: the type it constructs says which
    // `Capture`)
    let want: BTreeSet<(&str, &str)> =
        [("shop_wireextensions_wireextensions_capture_2", "EXTRACTED")].into_iter().collect();
    assert_eq!(from(".Send()"), want, "{got:#?}");
    let want: BTreeSet<(&str, &str)> = [
        (block, "EXTRACTED"),
        ("shop_client_async_client_later", "EXTRACTED"),
        ("shop_client_client_make", "EXTRACTED"),
    ]
    .into_iter()
    .collect();
    assert_eq!(from(".Builds()"), want, "{got:#?}");
}

#[test]
fn receiver_words_are_each_languages_own() {
    // `parent`, `base`, `cls` and `this` mean the object or its base only
    // where the language says so: a Go parameter `parent`, a Python
    // parameter `base` and Java parameters `cls` and `base` are names like
    // any other,
    // while C# `base.Run()`, PHP `parent::run()` and Python `super()` still
    // reach the base
    let got = calls_conf("receiverwords", &[
        ("tree.go", "package tree\n\ntype node struct{ kids []*node }\n\nfunc (n *node) addChild(c *node) *node { return c }\n\nfunc insert(parent *node, base *node) {\n\tparent.addChild(base)\n}\n"),
        ("app.py", "class Base:\n    def run(self):\n        return 1\n\n\nclass Child(Base):\n    def run(self):\n        return super().run()\n\n\ndef go(base: Base, parent: Base):\n    base.run()\n    parent.run()\n"),
        ("Shapes.cs", "class A {\n    public virtual int Run() => 1;\n}\n\nclass B : A {\n    public override int Run() => base.Run();\n}\n"),
        ("web.php", "<?php\nclass P {\n    public function run() { return 1; }\n}\n\nclass Q extends P {\n    public function run() { return parent::run(); }\n}\n"),
        ("Maker.java", "class Maker {\n    Object make(Class<?> cls, Helper base) {\n        return base.build(cls);\n    }\n}\n\nclass Helper {\n    Object build(Class<?> c) { return c; }\n}\n"),
    ]);
    let has = |from: &str, to: &str, conf: &str| {
        got.contains(&(from.to_string(), to.to_string(), conf.to_string()))
    };
    assert!(has("insert()", "tree_node_addchild", "EXTRACTED"), "{got:#?}");
    assert!(has("go()", "app_base_run", "EXTRACTED"), "{got:#?}");
    assert!(has(".run()", "app_base_run", "EXTRACTED"), "{got:#?}");
    assert!(has(".Run()", "shapes_a_run", "EXTRACTED"), "{got:#?}");
    assert!(!has(".Run()", "shapes_b_run", "EXTRACTED"), "{got:#?}");
    assert!(has(".run()", "web_p_run", "EXTRACTED"), "{got:#?}");
    assert!(has(".make()", "maker_helper_build", "EXTRACTED"), "{got:#?}");
}

#[test]
fn a_guess_by_name_never_links_a_definition_to_itself() {
    // `inner.evaluate()` inside `evaluate()` is a wrapper handing on to what
    // it wraps: the one `evaluate` of the project is the caller, not the
    // callee. A distinctive name on another object still gets its guess
    let got = calls_conf("selfguess", &[(
        "wrap.py",
        "class Wrapper:\n    def evaluate(self, inner, other):\n        other.prepare_all()\n        return inner.evaluate()\n\n    def prepare_all(self):\n        return 1\n",
    )]);
    let from_evaluate: Vec<(&str, &str)> = got
        .iter()
        .filter(|(from, _, _)| from == ".evaluate()")
        .map(|(_, to, conf)| (to.as_str(), conf.as_str()))
        .collect();
    assert_eq!(from_evaluate, [("wrap_wrapper_prepare_all", "INFERRED")], "{got:#?}");
}

#[test]
fn names_bound_with_a_type_the_source_states() {
    // a loop variable written with its type, a type pattern, and a name
    // bound to what a wrapper holds are locals of that type. `Err(e)` holds
    // no type the source names, and neither does a name with no pattern
    let got = calls_conf("patternbindings", &[
        ("Owner.java", "import java.util.List;\n\nclass Pet {\n    boolean isNew() { return true; }\n    String label() { return \"\"; }\n}\n\nclass Owner {\n    List<Pet> pets;\n\n    List<Pet> getPets() { return pets; }\n\n    void find(Object o) {\n        for (Pet pet : getPets()) {\n            pet.isNew();\n        }\n        if (o instanceof Pet p) {\n            p.label();\n        }\n    }\n}\n"),
        ("Keeper.cs", "class Animal {\n    public bool IsNew() => true;\n    public string Label() => \"\";\n}\n\nclass Keeper {\n    List<Animal> animals;\n\n    void Find(object o) {\n        foreach (Animal animal in animals) {\n            animal.IsNew();\n        }\n        if (o is Animal a) {\n            a.Label();\n        }\n    }\n}\n"),
        ("yard.rs", "pub struct Dog;\n\nimpl Dog {\n    pub fn bark(&self) {}\n    pub fn fetch(&self) {}\n    pub fn feed(&self) {}\n    pub fn walk(&self) {}\n    pub fn len(&self) -> usize { 0 }\n}\n\npub enum Slot {\n    Full(Dog),\n    Empty,\n}\n\npub struct Yard {\n    dog: Option<Dog>,\n}\n\nimpl Yard {\n    pub fn dog(&self) -> Option<&Dog> {\n        self.dog.as_ref()\n    }\n\n    pub fn load(&self) -> Result<Dog, String> {\n        Err(String::new())\n    }\n\n    pub fn find(&self, slot: Slot) {\n        if let Some(p) = self.dog() {\n            p.bark();\n        }\n        match self.load() {\n            Ok(found) => found.fetch(),\n            Err(e) => {\n                e.len();\n            }\n        }\n        match slot {\n            Slot::Full(inner) => inner.feed(),\n            Slot::Empty => {}\n        }\n        while let Some(next) = &self.dog {\n            next.walk();\n        }\n    }\n}\n"),
        ("Zoo.swift", "class Cat {\n    func purr() {}\n    func nap() {}\n}\n\nclass Zoo {\n    var cat: Cat?\n\n    func load() -> Cat? { cat }\n\n    func find() {\n        if let p = load() {\n            p.purr()\n        }\n        guard let q = cat else { return }\n        q.nap()\n    }\n}\n"),
    ]);
    // every call that reaches a method of each animal, whoever makes it
    let reached = |prefix: &str| -> BTreeSet<&str> {
        got.iter()
            .filter(|(_, to, _)| to.starts_with(prefix))
            .map(|(_, to, _)| to.as_str())
            .collect()
    };
    let want: BTreeSet<&str> = ["owner_pet_isnew", "owner_pet_label"].into_iter().collect();
    assert_eq!(reached("owner_pet_"), want, "{got:#?}");
    let want: BTreeSet<&str> = ["keeper_animal_isnew", "keeper_animal_label"].into_iter().collect();
    assert_eq!(reached("keeper_animal_"), want, "{got:#?}");
    let want: BTreeSet<&str> =
        ["yard_dog_bark", "yard_dog_feed", "yard_dog_fetch", "yard_dog_walk"].into_iter().collect();
    assert_eq!(reached("yard_dog_"), want, "{got:#?}");
    let want: BTreeSet<&str> = ["zoo_cat_nap", "zoo_cat_purr"].into_iter().collect();
    assert_eq!(reached("zoo_cat_"), want, "{got:#?}");
}

#[test]
fn types_a_declaration_writes_and_the_overload_a_chain_asks_for() {
    // a Go package variable and a Kotlin property are what their declaration
    // writes, whatever they are assigned; a Kotlin constructor parameter with
    // no `val` is the parameter in the class header, not the function of its
    // name; of two `reply`s the one whose result has what is asked next is
    // called; and a C# name is looked up in the namespaces around the file's
    let got = calls_conf("declaredtypes", &[
        ("rooms.go", "package main\n\ntype Manager struct{}\n\nfunc NewRoomManager() *Manager { return &Manager{} }\n\nfunc (m *Manager) Submit(id string) {}\n\nvar roomManager *Manager\n\nfunc setup() {\n\troomManager = NewRoomManager()\n\troomManager.Submit(\"a\")\n}\n"),
        ("Spec.kt", "package shop\n\nclass TagMap\n\nclass Kind {\n    fun implicitModifiers(): Int = 1\n}\n\nclass Builder {\n    val kind: Kind = Kind()\n    fun buildTagMap(): TagMap = TagMap()\n}\n\nclass Spec private constructor(builder: Builder, private val tagMap: TagMap = builder.buildTagMap()) {\n    val kind: Kind = builder.kind\n\n    fun emit() {\n        kind.implicitModifiers()\n    }\n\n    companion object {\n        fun builder(name: String): Builder = Builder()\n    }\n}\n"),
        ("Result.java", "class Reply<T> {\n    boolean isSuccessful() { return true; }\n}\n\nfinal class Result<T> {\n    static <T> Result<T> reply(Reply<T> reply) { return new Result<>(); }\n\n    Reply<T> reply() { return null; }\n}\n\nclass Use {\n    void check(Result<String> result) {\n        result.reply().isSuccessful();\n    }\n}\n"),
        ("app/features/users/UserHelpers.cs", "namespace App.Features.Users;\n\npublic static class UserHelpers {\n    public static int CreateDefaultUser(object fixture) => 1;\n}\n"),
        ("app/features/articles/ListTests.cs", "namespace App.Features.Articles;\n\npublic class ListTests {\n    public void Run() {\n        Users.UserHelpers.CreateDefaultUser(this);\n    }\n}\n"),
    ]);
    let has = |from: &str, to: &str, conf: &str| {
        got.contains(&(from.to_string(), to.to_string(), conf.to_string()))
    };
    assert!(has("setup()", "rooms_manager_submit", "EXTRACTED"), "{got:#?}");
    assert!(has(".emit()", "spec_kind_implicitmodifiers", "EXTRACTED"), "{got:#?}");
    assert!(has("Spec", "spec_builder_buildtagmap", "EXTRACTED"), "{got:#?}");
    // (the result of the `reply` that takes no argument: a choice between
    // overloads the chain made, so a guess)
    assert!(has(".check()", "result_reply_issuccessful", "INFERRED"), "{got:#?}");
    assert!(
        has(".Run()", "app_features_users_userhelpers_userhelpers_createdefaultuser", "EXTRACTED"),
        "{got:#?}"
    );
}

#[test]
fn a_call_with_type_arguments_is_of_the_generic_callable() {
    // `c.Get<int>(r)` writes a type argument: the callable that declares
    // one. `c.Get(r)` writes none, and between `Get(Request)` and its
    // generic twin of the same parameters the plain one is called. Where the
    // parameters differ, in their type arguments too, the arguments decide
    // as before, or nothing does
    let got = calls_conf("generictwins", &[(
        "Calls.cs",
        "class Request {}\n\nclass Client {\n    public int Get(Request r) => 1;\n    public T Get<T>(Request r) => default;\n    public T Get<T>(string resource) => default;\n}\n\nclass Fixture {\n    public Task<T> Send<T>(IRequest<T> request) => null;\n    public Task Send(IRequest request) => null;\n}\n\nclass Use {\n    void Plain(Client c, Request r) {\n        c.Get(r);\n    }\n\n    void Generic(Client c, Request r) {\n        c.Get<int>(r);\n    }\n\n    void ByName(Client c) {\n        c.Get<int>(\"x\");\n    }\n\n    void Either(Fixture f, Request r) {\n        f.Send(r);\n    }\n}\n",
    )]);
    let from = |label: &str| -> Vec<(&str, &str)> {
        got.iter()
            .filter(|(from, _, _)| from == label)
            .map(|(_, to, conf)| (to.as_str(), conf.as_str()))
            .collect()
    };
    assert_eq!(from(".Plain()"), [("calls_client_get", "EXTRACTED")], "{got:#?}");
    assert_eq!(from(".Generic()"), [("calls_client_get_2", "EXTRACTED")], "{got:#?}");
    assert_eq!(from(".ByName()"), [("calls_client_get_3", "EXTRACTED")], "{got:#?}");
    // (`Send<T>(IRequest<T>)` and `Send(IRequest)` take different things:
    // the argument's type would say which, and the walk does not know it)
    assert_eq!(
        from(".Either()"),
        [("calls_fixture_send", "INFERRED"), ("calls_fixture_send_2", "INFERRED")],
        "{got:#?}"
    );
}

#[test]
fn primary_constructors_returned_objects_properties_and_top_level_programs() {
    // C# primary constructor parameters are typed names of the class; a
    // name taken out of the object a hook returns is the function it names;
    // a Python `@property` read with more after it is what it returns; and a
    // C# top-level variable that names nothing in sight, called by an
    // extension's name, is that extension's receiver — never a library's
    // static class (`HttpUtility.UrlEncode`)
    let got = calls_conf("returnedmembers", &[
        ("Handler.cs", "namespace App;\n\npublic class ConduitContext {\n    public int SaveChangesAsync() => 1;\n}\n\npublic class QueryHandler(ConduitContext context) {\n    public int Handle() {\n        return context.SaveChangesAsync();\n    }\n}\n"),
        ("Services.cs", "namespace App;\n\npublic static class ServicesExtensions {\n    public static IServiceCollection AddConduit(this IServiceCollection services) => services;\n    public static string UrlEncode(this string input) => input;\n}\n"),
        ("Program.cs", "using App;\n\nvar builder = WebApplication.CreateBuilder(args);\nbuilder.Services.AddConduit();\nvar encoded = HttpUtility.UrlEncode(\"x\");\n"),
        ("web/useToast.ts", "const useCustomToast = () => {\n  const showSuccessToast = (description: string) => {\n    return description\n  }\n  const unused = () => 1\n  return { showSuccessToast, hide: unused }\n}\nexport default useCustomToast\n"),
        ("web/Delete.tsx", "import useCustomToast from \"./useToast\"\n\nconst DeleteUser = () => {\n  const { showSuccessToast, hide } = useCustomToast()\n  const onDone = () => {\n    showSuccessToast(\"deleted\")\n    hide()\n  }\n  return onDone\n}\nexport default DeleteUser\n"),
        ("client.py", "class Cookies:\n    def extract_cookies(self, response):\n        return None\n\n\nclass Client:\n    @property\n    def cookies(self) -> Cookies:\n        return Cookies()\n\n    def send(self, response):\n        self.cookies.extract_cookies(response)\n"),
    ]);
    let has = |from: &str, to: &str, conf: &str| {
        got.contains(&(from.to_string(), to.to_string(), conf.to_string()))
    };
    assert!(has(".Handle()", "handler_conduitcontext_savechangesasync", "EXTRACTED"), "{got:#?}");
    assert!(has("Program.cs", "services_servicesextensions_addconduit", "INFERRED"), "{got:#?}");
    assert!(
        !got.iter().any(|(_, to, _)| to == "services_servicesextensions_urlencode"),
        "{got:#?}"
    );
    assert!(
        has("onDone()", "web_usetoast_usecustomtoast_showsuccesstoast", "INFERRED"),
        "{got:#?}"
    );
    assert!(has("onDone()", "web_usetoast_usecustomtoast_unused", "INFERRED"), "{got:#?}");
    assert!(has(".send()", "client_cookies_extract_cookies", "EXTRACTED"), "{got:#?}");
}

#[test]
fn a_cast_says_what_a_local_is() {
    // `(Foo)x`, `x as Foo`, `<Foo>x`: the type the cast writes, in each
    // language that writes one
    let got = calls_conf("casts", &[
        ("Use.cs", "class Box {\n    public int Open() => 1;\n    public int Shut() => 1;\n}\n\nclass Use {\n    void Run(object o) {\n        var a = (Box)o;\n        var b = o as Box;\n        a.Open();\n        b.Shut();\n    }\n}\n"),
        ("Use.java", "class Lid {\n    int lift() { return 1; }\n}\n\nclass UseLid {\n    void run(Object o) {\n        var a = (Lid) o;\n        a.lift();\n    }\n}\n"),
        ("use.ts", "class Cup {\n  fill() { return 1 }\n  pour() { return 1 }\n}\n\nfunction run(o: unknown) {\n  const a = o as Cup\n  const b = <Cup>o\n  a.fill()\n  b.pour()\n}\n"),
        ("Use.kt", "class Jar {\n    fun seal() = 1\n    fun crack() = 1\n}\n\nfun run(o: Any) {\n    val a = o as Jar\n    val b = o as? Jar\n    a.seal()\n    b?.crack()\n}\n"),
    ]);
    for target in [
        "use_cs_box_open",
        "use_cs_box_shut",
        "use_java_lid_lift",
        "use_ts_cup_fill",
        "use_ts_cup_pour",
        "use_kt_jar_seal",
        "use_kt_jar_crack",
    ] {
        assert!(
            got.iter().any(|(_, to, conf)| to == target && conf == "EXTRACTED"),
            "{target}: {got:#?}"
        );
    }
}

#[test]
fn lombok_accessors_read_and_write_their_fields() {
    // `@Data` / `@Getter` / `@Setter` write accessors the source does not:
    // `getTags()` reads the field `tags`, `setTags(…)` writes it; a class
    // without the annotation has no such accessor
    let got = edges("lombok", &[
        ("Filter.java", "import lombok.Data;\n\n@Data\nclass Filter {\n    private String tags;\n    private boolean active;\n}\n"),
        ("Plain.java", "class Plain {\n    private String name;\n}\n"),
        ("Use.java", "class Use {\n    void run(Filter filter, Plain plain) {\n        filter.getTags();\n        filter.isActive();\n        filter.setTags(\"a\");\n        plain.getName();\n    }\n}\n"),
    ]);
    has(
        &got,
        &[(".run()", "reads", "tags"), (".run()", "reads", "active"), (".run()", "writes", "tags")],
    );
    assert!(!got.iter().any(|(_, r, t)| r == "reads" && t == "name"), "{got:#?}");
}

#[test]
fn laravels_authenticated_user_is_the_authenticatable_model() {
    let got = calls_conf("laravelauth", &[
        ("app/Models/User.php", "<?php\nnamespace App\\Models;\n\nuse Illuminate\\Foundation\\Auth\\User as Authenticatable;\n\nclass User extends Authenticatable\n{\n    public function articles() { return 1; }\n}\n"),
        ("app/Http/Controllers/ArticleController.php", "<?php\nnamespace App\\Http\\Controllers;\n\nclass ArticleController\n{\n    public function store()\n    {\n        return auth()->user()->articles();\n    }\n}\n"),
    ]);
    assert!(
        got.iter().any(|(from, to, conf)| from == ".store()"
            && to == "app_models_user_user_articles"
            && conf == "INFERRED"),
        "{got:#?}"
    );
}

#[test]
fn a_rust_error_binding_and_a_name_a_prelude_passes_on() {
    // `Err(e)` binds a local the source gives no type: a distinctive method
    // name says what it is, and the file that defines it is in sight through
    // the `pub use` of a prelude the caller star-imports
    let got = calls_conf("rusterr", &[
        ("Cargo.toml", "[package]\nname = \"app\"\nversion = \"0.1.0\"\n"),
        ("src/lib.rs", "pub mod error;\npub mod prelude;\npub mod app;\n"),
        ("src/error.rs", "pub struct Error;\n\nimpl Error {\n    pub fn error_response(&self) -> u16 { 500 }\n}\n"),
        ("src/prelude.rs", "pub use crate::error::Error;\n"),
        ("src/app.rs", "use crate::prelude::*;\n\npub fn handle(res: Result<u16, Error>) -> u16 {\n    match res {\n        Ok(v) => v,\n        Err(e) => e.error_response(),\n    }\n}\n"),
    ]);
    assert!(
        got.iter()
            .any(|(from, to, _)| from == "handle()" && to == "src_error_error_error_response"),
        "{got:#?}"
    );
}

#[test]
fn swift_implicit_members_of_closure_initializers_library_types_and_memberwise_inits() {
    let got = calls_conf("swiftplaces", &[(
        "a.swift",
        "import Foundation\n\nstruct HTTPHeader {\n    static func contentType(_ value: String) -> HTTPHeader { HTTPHeader() }\n    static func userAgent(_ value: String) -> HTTPHeader { HTTPHeader() }\n    static let defaultAgent: HTTPHeader = {\n        return .userAgent(\"x\")\n    }()\n}\n\nstruct HTTPHeaders {\n    mutating func update(_ header: HTTPHeader) {}\n}\n\nextension URLRequest {\n    var headers: HTTPHeaders {\n        get { HTTPHeaders() }\n        set {}\n    }\n}\n\nextension DispatchQueue {\n    static func completionQueue(for id: Int) -> DispatchQueue { .main }\n}\n\nstruct Endpoint {\n    struct Path {\n        static func status(_ code: Int) -> Path { Path() }\n    }\n\n    var path = Path.status(200)\n}\n\nfunc respond(on queue: DispatchQueue) {}\n\nfunc encode(into request: URLRequest) {\n    var request = request\n    request.headers.update(.contentType(\"json\"))\n    respond(on: .completionQueue(for: 1))\n    _ = Endpoint(path: .status(404))\n}\n",
    )]);
    for target in [
        "a_httpheader_useragent",
        "a_httpheader_contenttype",
        "a_completionqueue",
        "a_endpoint_path_status",
    ] {
        assert!(got.iter().any(|(_, to, _)| to == target), "{target}: {got:#?}");
    }
}

#[test]
fn rspec_lets_subjects_and_configured_helpers() {
    // `let(:conn) { … }` is a method of the block it is written in: the
    // innermost around a use, none outside; what its block gives is its
    // result. `config.include M` puts M's methods in every spec, not in the
    // library's own files
    let got = calls_conf("rspeclet", &[
        ("lib/client.rb", "class Client\n  def initialize(x); end\n  def fetch; end\n  def build; end\nend\n\nclass Other\n  def conn; end\nend\n"),
        ("lib/plain.rb", "def run\n  helper_call\nend\n"),
        ("spec/support/helpers.rb", "module Shop\n  module Helpers\n    def helper_call; end\n  end\nend\n\nRSpec.configure do |config|\n  config.include Shop::Helpers, type: :integration\nend\n"),
        ("spec/client_spec.rb", "shared_examples \"x\" do\n  it { conn.build }\nend\n\ndescribe Client do\n  subject { conn }\n  context \"a\" do\n    let(:conn) { Client.new(1) }\n    subject(:request) { conn.build }\n    it(\"a\") { conn.fetch }\n  end\n  context \"b\" do\n    let(:conn) { Other.new }\n    it(\"b\") { conn.conn }\n  end\n  it \"c\" do\n    helper_call\n  end\nend\n"),
    ]);
    let has = |from: &str, to: &str| got.iter().any(|(f, t, _)| f == from && t == to);
    // the two `let(:conn)` are two definitions, each the one its block sees
    assert!(has("conn()", "lib_client_client"), "{got:#?}");
    assert!(has("conn()", "lib_client_other"), "{got:#?}");
    assert!(has("client_spec.rb", "spec_client_spec_conn"), "{got:#?}");
    assert!(has("client_spec.rb", "spec_client_spec_conn_2"), "{got:#?}");
    assert!(has("client_spec.rb", "lib_client_client_fetch"), "{got:#?}");
    assert!(has("client_spec.rb", "lib_client_other_conn"), "{got:#?}");
    assert!(has("request()", "lib_client_client_build"), "{got:#?}");
    assert!(has("client_spec.rb", "spec_support_helpers_shop_helpers_helper_call"), "{got:#?}");
    assert!(!has("run()", "spec_support_helpers_shop_helpers_helper_call"), "{got:#?}");
    // `subject { conn }` declares; it does not call itself. A use outside
    // every block that declares the name (the outer subject, shared
    // examples) names nothing: which `conn` runs is the example's
    assert!(!has("subject()", "spec_client_spec_subject"), "{got:#?}");
    let at_l2 =
        got.iter().filter(|(f, t, _)| f == "client_spec.rb" && t == "lib_client_client_build");
    assert_eq!(at_l2.count(), 0, "{got:#?}");
}

#[test]
fn cpp_plain_declarations_and_misread_constructions_are_locals() {
    // `Ledger book;` is a local of its type; `Pool pool(book);` beside the
    // local `book` is a variable constructed from it, which the grammar reads
    // as the declaration of a function `pool`
    let got = calls_conf("cpplocals", &[
        ("src/shop.h", "namespace shop {\nclass Ledger {\n  public:\n    void settle();\n};\nclass Pool {\n  public:\n    explicit Pool(Ledger& l);\n    int Submit(int x);\n};\nint pool(int);\nvoid Submit();\n}\n"),
        ("src/other.h", "class Other {\n  public:\n    void settle();\n};\n"),
        ("test/use.cpp", "#include \"../src/shop.h\"\n#include \"../src/other.h\"\nvoid run() {\n    shop::Ledger book;\n    book.settle();\n    shop::Pool pool(book);\n    pool.Submit(1);\n    int helper(shop::Ledger);\n}\n"),
    ]);
    let has = |from: &str, to: &str| got.iter().any(|(f, t, _)| f == from && t == to);
    assert!(has("run()", "src_shop_ledger_settle"), "{got:#?}");
    assert!(!has("run()", "src_other_other_settle"), "{got:#?}");
    assert!(has("run()", "src_shop_pool"), "{got:#?}");
    assert!(has("run()", "src_shop_pool_pool"), "{got:#?}");
    assert!(has("run()", "src_shop_pool_submit"), "{got:#?}");
    // the local hides the functions of its name
    assert!(!has("run()", "src_shop_pool_2"), "{got:#?}");
    assert!(!has("run()", "src_shop_submit"), "{got:#?}");
    // no node for the misread declaration; a real one keeps its node
    assert!(!got.iter().any(|(f, _, _)| f == "pool()"), "{got:#?}");
}

#[test]
fn cpp_a_namespace_is_every_block_that_opens_it() {
    // `shop::File` is looked up in every `namespace shop { }`, not only the
    // block the caller's file opens; a name called is the function of that
    // name, a name with `::` after it the class
    let got = calls_conf("cppns", &[
        ("include/file.h", "namespace shop {\nstruct File {\n    explicit File(int p) {}\n};\n}\n"),
        ("include/async.h", "namespace shop {\ntemplate <class Fn> auto task(Fn fn) { return fn(); }\nclass task {\n  public:\n    static void stop() {}\n};\nvoid run() {}\n}\n"),
        ("include/other.h", "namespace other {\nstruct File {\n    explicit File(int p) {}\n};\nvoid run() {}\n}\n"),
        // a method defined for a type inside a block of the namespace is the
        // type's: `shop::run()` is not `Session::run`
        ("base/session.cpp", "#include \"../include/async.h\"\nnamespace shop {\nclass Session {\n  public:\n    void run();\n};\nvoid Session::run() {}\n}\n"),
        ("test/use.cpp", "#include \"../include/file.h\"\n#include \"../include/async.h\"\n#include \"../include/other.h\"\nnamespace shop {\nvoid here();\n}\nvoid go() {\n    auto f = shop::File(1);\n    shop::run();\n    shop::task([] { return 1; });\n    shop::task::stop();\n}\n"),
    ]);
    let has = |to: &str| got.iter().any(|(f, t, _)| f == "go()" && t == to);
    assert!(has("include_file_file"), "{got:#?}");
    assert!(has("include_file_file_file"), "{got:#?}");
    assert!(!has("include_other_file"), "{got:#?}");
    assert!(has("include_async_run"), "{got:#?}");
    assert!(!has("include_other_run"), "{got:#?}");
    assert!(!has("base_session_session_run"), "{got:#?}");
    // the function, not the class of its name
    assert!(has("include_async_task"), "{got:#?}");
    assert!(!has("include_async_task_2"), "{got:#?}");
    assert!(has("include_async_task_stop"), "{got:#?}");
}

#[test]
fn cpp_aliases_and_specializations_name_their_template() {
    // `using Cancellable = Wrapper<int, true>` is the specialization its
    // arguments fit; a bare `Wrapper` does not say which one, so a member
    // only one specialization declares is that one's, `INFERRED`; a
    // deduction guide declares no function
    let got = calls_conf("cppspec", &[
        ("include/wrap.h", "namespace shop {\ntemplate <typename T, bool cancellable = false>\nclass Wrapper;\ntemplate <typename T>\nclass Wrapper<T, false> {\n  public:\n    bool valid() const { return true; }\n    int wait() { return 0; }\n};\ntemplate <typename T>\nclass Wrapper<T, true> : public Wrapper<T, false> {\n  public:\n    int cancel() { return 0; }\n    int wait() { return 1; }\n};\ntemplate <typename T>\nWrapper(T&&, bool) -> Wrapper<T, true>;\n}\n"),
        ("include/other.h", "struct Other {\n    int cancel() { return 0; }\n};\n"),
        ("include/conn.h", "struct conn {\n    int fd;\n};\ntypedef struct conn conn_t;\nint close_conn(conn_t* c);\n"),
        ("test/use.cpp", "#include \"../include/wrap.h\"\n#include \"../include/other.h\"\nusing namespace shop;\nusing Cancellable = Wrapper<int, true>;\nusing Plain = Wrapper<int, false>;\nvoid exact() {\n    Cancellable c{1, true};\n    c.cancel();\n    c.wait();\n    Plain p{1};\n    p.wait();\n}\nvoid bare() {\n    Wrapper w{1, true};\n    w.cancel();\n    w.wait();\n}\n"),
    ]);
    let edge = |from: &str, to: &str| {
        got.iter().find(|(f, t, _)| f == from && t == to).map(|(_, _, c)| c.as_str())
    };
    assert_eq!(
        edge("exact()", "include_wrap_wrapper_t_true_cancel"),
        Some("EXTRACTED"),
        "{got:#?}"
    );
    assert_eq!(edge("exact()", "include_wrap_wrapper_t_true_wait"), Some("EXTRACTED"), "{got:#?}");
    assert_eq!(edge("exact()", "include_wrap_wrapper_t_false_wait"), Some("EXTRACTED"), "{got:#?}");
    assert_eq!(edge("exact()", "include_other_other_cancel"), None, "{got:#?}");
    // the bare name: `cancel` is one specialization's only
    assert_eq!(edge("bare()", "include_wrap_wrapper_t_true_cancel"), Some("INFERRED"), "{got:#?}");
    // `wait` both declare: which one is not known, so no edge names one
    assert_eq!(edge("bare()", "include_wrap_wrapper_t_false_wait"), None, "{got:#?}");
    assert_eq!(edge("bare()", "include_wrap_wrapper_t_true_wait"), None, "{got:#?}");
    // the guide is no function, and no call of it is made
    assert!(!got.iter().any(|(_, t, _)| t == "include_wrap_wrapper"), "{got:#?}");
}

#[test]
fn c_a_typedef_struct_is_the_struct_written_inside_it() {
    // `typedef struct reader { … } reader;` is the struct it writes, whose
    // members the alias holds no copy of; a bodiless `typedef struct conn
    // conn_t;` names `conn`; an anonymous struct's fields are the alias's.
    // A function returning a pointer types its parameters as any other.
    let files: &[(&str, &str)] = &[
        ("read.h", "typedef struct funcs {\n    void *(*create)(int);\n} funcs;\n\ntypedef struct reader {\n    funcs *fn;\n    int pos;\n} reader;\n\nstruct conn {\n    int fd;\n};\ntypedef struct conn conn_t;\n\ntypedef struct {\n    int port;\n} opts;\n\ntypedef struct other {\n    void *(*create)(int);\n    int pos;\n} other;\n"),
        ("read.c", "#include \"read.h\"\nvoid *run(reader *r, conn_t *c, opts *o) {\n    r->pos = c->fd + o->port;\n    return r->fn->create(1);\n}\n"),
    ];
    let calls = calls_conf("ctypedef", files);
    assert!(
        calls.contains(&("run()".into(), "read_h_funcs_funcs_create".into(), "EXTRACTED".into())),
        "{calls:#?}"
    );
    assert!(!calls.iter().any(|(_, t, _)| t == "read_h_other_other_create"), "{calls:#?}");
    let got = edges("ctypedef2", files);
    has(
        &got,
        &[
            ("run()", "writes", "pos"),
            ("run()", "reads", "fd"),
            ("run()", "reads", "port"),
            ("run()", "reads", "fn"),
        ],
    );
}

#[test]
fn c_an_extern_variable_declares_the_definition_elsewhere() {
    // the header's `extern allocs fns;` puts `fns` and its type in scope for
    // the header's own inline functions; it is no node: reads go to the
    // definition in the `.c` file
    let files: &[(&str, &str)] = &[
        // (inside an `#ifndef`, as a platform guard writes it)
        ("alloc.h", "#ifdef __cplusplus\nextern \"C\" {\n#endif\ntypedef struct allocs {\n    void *(*mallocFn)(int);\n} allocs;\n\n#ifndef _WIN32\nextern allocs fns;\n\nhi_static hi_inline\nvoid *hi_free(void *p) {\n    return p;\n}\n\nstatic inline void *hi_malloc(int n) {\n    return fns.mallocFn(n);\n}\n#endif\n#ifdef __cplusplus\n}\n#endif\n"),
        ("alloc.c", "#include \"alloc.h\"\nallocs fns = { 0 };\n"),
    ];
    let calls = calls_conf("cextern", files);
    assert!(
        calls.contains(&(
            "hi_malloc()".into(),
            "alloc_h_allocs_allocs_mallocfn".into(),
            "EXTRACTED".into()
        )),
        "{calls:#?}"
    );
    let dir = project("cextern2", files);
    let (g, _) = build_graph(&BuildOptions::new(&dir)).unwrap();
    std::fs::remove_dir_all(&dir).unwrap();
    let ids: Vec<&str> =
        g["nodes"].as_array().unwrap().iter().filter_map(|n| n["id"].as_str()).collect();
    assert!(ids.contains(&"alloc_c_fns") && !ids.contains(&"alloc_h_fns"), "{ids:?}");
    // macro words above a function are no variable
    assert!(!ids.contains(&"alloc_h_hi_inline"), "{ids:?}");
    let reads = g["links"].as_array().unwrap().iter().any(|e| {
        e["relation"] == "reads"
            && e["source"] == "alloc_h_hi_malloc"
            && e["target"] == "alloc_c_fns"
    });
    assert!(reads);
}

#[test]
fn c_a_function_put_in_a_slot_is_what_the_slot_dispatches_to() {
    // a struct of function pointers is an interface; what an initializer
    // (by name or by position, data fields counted) or an assignment puts
    // in a slot is an implementation of it. NULL, a library's function and
    // a data field give nothing
    let got = edges("cslots", &[
        ("net.h", "typedef struct funcs {\n    int (*close)(int);\n    int size;\n    int (*read)(int);\n} funcs;\ntypedef struct ctx {\n    funcs f;\n    funcs *fp;\n} ctx;\n"),
        // a field of an anonymous struct type: its members are the body's
            ("ev.h", "typedef struct actx {\n    void *data;\n    struct {\n        void *data;\n        void (*addRead)(void *);\n    } ev;\n} actx;\nvoid addReadImpl(void *p) {}\nvoid attach(actx *ac) {\n    ac->ev.addRead = addReadImpl;\n}\nvoid go(actx *ac) {\n    ac->ev.addRead(ac->ev.data);\n}\n"),
            ("net.c", "#include \"net.h\"\nint netClose(int x) { return x; }\nint netRead(int x) { return x; }\nint posClose(int x) { return x; }\nint posRead(int x) { return x; }\nint setRead(int x) { return x; }\nint setClose(int x) { return x; }\nint three = 3;\nstatic funcs byName = { .close = netClose, .read = netRead };\nstatic funcs byPos = { posClose, three, posRead };\nstatic funcs none = { .close = NULL, .read = abort };\nvoid set(ctx *c) {\n    c->f.read = setRead;\n    c->fp->close = setClose;\n    c->f.size = three;\n}\nint use(ctx *c) {\n    return c->fp->read(1);\n}\n"),
    ]);
    has(
        &got,
        &[
            (".close()", "dispatches_to", "netClose()"),
            (".read()", "dispatches_to", "netRead()"),
            (".close()", "dispatches_to", "posClose()"),
            (".read()", "dispatches_to", "posRead()"),
            (".read()", "dispatches_to", "setRead()"),
            (".close()", "dispatches_to", "setClose()"),
            ("use()", "calls", ".read()"),
            (".addRead()", "dispatches_to", "addReadImpl()"),
            ("go()", "calls", ".addRead()"),
        ],
    );
    let wrong = |to: &str| got.iter().any(|(_, r, t)| r == "dispatches_to" && t == to);
    assert!(!wrong("three") && !wrong("abort"), "{got:#?}");
    assert!(!got.iter().any(|(f, r, _)| f == "size" && r == "dispatches_to"), "{got:#?}");
    // positional: `three` is the data field's, so `posRead` is `read`'s
    assert!(
        !got.contains(&(".close()".into(), "dispatches_to".into(), "posRead()".into())),
        "{got:#?}"
    );
}

#[test]
fn js_this_and_what_a_framework_hands_a_function() {
    // express builds its own request and response objects: a handler's
    // untyped `req` / `res` is one of them (`INFERRED`), and what the app
    // adds to `app.response` too
    let got = calls_conf("jsframework", &[
        ("lib/request.js", "var http = require('http');\nvar req = Object.create(http.IncomingMessage.prototype);\nmodule.exports = req;\nreq.is = function is(types) { return types; };\n"),
        ("lib/response.js", "var http = require('http');\nvar res = Object.create(http.ServerResponse.prototype);\nmodule.exports = res;\nres.redirect = function redirect(url) { return url; };\n"),
        ("lib/application.js", "var app = exports = module.exports = {};\napp.route = function route(path) { return path; };\n['get', 'put'].forEach(function (method) {\n  app[method] = function (path) {\n    return this.route(path);\n  };\n});\n"),
        ("lib/view.js", "function View(name) {\n  this.path = this.lookup(name);\n}\nView.prototype.lookup = function lookup(name) { return name; };\n"),
        ("examples/index.js", "var app = require('../lib/application');\napp.response.message = function (msg) { return msg; };\n"),
        ("test/req.js", "var app = require('../lib/application');\napp.use(function (req, res) {\n  req.is('json');\n  res.redirect('/');\n  res.message('hi');\n});\n"),
    ]);
    let has = |from: &str, to: &str| {
        got.iter().find(|(f, t, _)| f == from && t == to).map(|(_, _, c)| c.clone())
    };
    assert_eq!(has("req.js", "lib_request_is").as_deref(), Some("INFERRED"), "{got:#?}");
    assert_eq!(has("req.js", "lib_response_redirect").as_deref(), Some("INFERRED"), "{got:#?}");
    assert_eq!(has("req.js", "examples_index_message").as_deref(), Some("INFERRED"), "{got:#?}");
    // `this` in `app[method] = function` is `app`; in an ES5 constructor,
    // its prototype's functions
    assert_eq!(
        has("application.js", "lib_application_route").as_deref(),
        Some("EXTRACTED"),
        "{got:#?}"
    );
    assert_eq!(has("View()", "lib_view_lookup").as_deref(), Some("EXTRACTED"), "{got:#?}");

    // a project that builds no request object: `req` is the framework's
    let other = calls_conf(
        "jsframework2",
        &[("lib/users.js", "exports.logout = function logout(req, res) {\n  req.logout();\n};\n")],
    );
    assert!(!other.iter().any(|(_, t, _)| t == "lib_users_logout"), "{other:#?}");
}

#[test]
fn js_a_mongoose_document_has_its_schemas_methods() {
    // `this` in a callback a schema is handed runs on a document; `new
    // Article(…)` of `mongoose.model('Article')` is one, of the schema the
    // model is registered with
    let got = calls_conf("jsmongoose", &[
        ("app/models/user.js", "const mongoose = require('mongoose');\nconst Schema = mongoose.Schema;\nconst UserSchema = new Schema({});\nUserSchema.path('name').validate(function (name) {\n  if (this.skipValidation()) return true;\n  return name;\n});\nUserSchema.methods = {\n  skipValidation: function () { return false; }\n};\nmongoose.model('User', UserSchema);\n"),
        ("app/models/article.js", "const mongoose = require('mongoose');\nconst ArticleSchema = new mongoose.Schema({});\nArticleSchema.methods = {\n  uploadAndSave: function (file) { return file; }\n};\nmongoose.model('Article', ArticleSchema);\n"),
        ("app/controllers/articles.js", "const mongoose = require('mongoose');\nconst Article = mongoose.model('Article');\nexports.create = function (req, res) {\n  const article = new Article(req.body);\n  return article.uploadAndSave(req.file);\n};\n"),
        // the document a model's static hands its callback (second parameter)
        ("config/passport.js", "const mongoose = require('mongoose');\nconst User = mongoose.model('User');\nUser.findOne({}, function (err, user) {\n  user.skipValidation();\n});\n"),
        // a callback a non-schema call is handed: its `this` is not a document
        ("app/other.js", "const emitter = require('events');\nemitter.on('x', function () {\n  this.uploadAndSave();\n});\n"),
    ]);
    let has = |from: &str, to: &str| {
        got.iter().find(|(f, t, _)| f == from && t == to).map(|(_, _, c)| c.clone())
    };
    assert_eq!(
        has("user.js", "app_models_user_skipvalidation").as_deref(),
        Some("INFERRED"),
        "{got:#?}"
    );
    assert_eq!(
        has("passport.js", "app_models_user_skipvalidation").as_deref(),
        Some("INFERRED"),
        "{got:#?}"
    );
    assert_eq!(
        has("create()", "app_models_article_uploadandsave").as_deref(),
        Some("INFERRED"),
        "{got:#?}"
    );
    assert!(!got.iter().any(|(f, t, _)| f == "other.js" && t.starts_with("app_")), "{got:#?}");
}

#[test]
fn rust_field_beside_a_method_of_its_name() {
    // `self.input.fill()`: read without a call, `input` is the field, not
    // the trait method of the same name beside it
    let got = edges("rs-field-method", &[
        ("Cargo.toml", "[package]\nname = \"demo\"\nversion = \"0.1.0\"\n"),
        ("src/lib.rs", "mod buf;\nmod util;\n"),
        ("src/util.rs", "pub struct ConsumeBuf {\n    filled: usize,\n}\n\nimpl ConsumeBuf {\n    pub fn fill(&mut self) {\n        self.filled += 1;\n    }\n}\n"),
        ("src/buf.rs", "use crate::util::ConsumeBuf;\n\npub trait Buffers {\n    fn input(&self) -> &[u8];\n}\n\npub struct LazyBuffers {\n    input: ConsumeBuf,\n}\n\nimpl Buffers for LazyBuffers {\n    fn input(&self) -> &[u8] {\n        &[]\n    }\n}\n\nimpl LazyBuffers {\n    fn ensure(&mut self) {\n        self.input.fill();\n    }\n}\n"),
    ]);
    has(&got, &[(".ensure()", "calls", ".fill()")]);
}

#[test]
fn an_extension_for_a_library_type_on_a_receiver_the_walk_cannot_type() {
    // the receiver is a library's member, or a chain of them: the one
    // extension in sight written for a type the project does not define
    let cs = edges("cs-lib-ext", &[
        ("Shop/Text.cs", "namespace Shop.Extensions;\n\npublic static class Text {\n    public static string Squash(this string s) => s.Replace(\"_\", \"\");\n}\n"),
        ("Shop/Reader.cs", "using System.Xml.Linq;\nusing Shop.Extensions;\n\nnamespace Shop;\n\npublic class Reader {\n    public bool Same(XElement e, string name) {\n        return e.Name.LocalName.Squash() == name;\n    }\n}\n"),
    ]);
    has(&cs, &[(".Same()", "calls", ".Squash()")]);
    let kt = edges("kt-lib-ext", &[
        ("src/main/kotlin/app/ext/Strings.kt", "package app.ext\n\nfun String.isEmailValid(): Boolean = this.contains(\"@\")\n"),
        ("src/main/kotlin/app/domain/User.kt", "package app.domain\n\nimport app.ext.isEmailValid\nimport lib.Account\n\nclass Check(val account: Account) {\n    fun ok(): Boolean {\n        return account.owner.email.isEmailValid()\n    }\n}\n"),
    ]);
    has(&kt, &[(".ok()", "calls", "isEmailValid()")]);
}

#[test]
fn a_callback_no_definition_holds_has_locals_of_its_own() {
    // `test('…', () => { const client = new Client() })`: each callback is a
    // scope; `client` in one is not `client` in the next (both types have a
    // `run`, so only the local's type says which), and a callback inside
    // another sees the outer one's locals
    let ts = calls_conf("block-locals", &[
        ("src/client.ts", "export class Client {\n  run(): number {\n    return 1\n  }\n}\n\nexport class Server {\n  run(): number {\n    return 2\n  }\n  stop(): number {\n    return 3\n  }\n}\n"),
        ("test/client.test.ts", "import { Client, Server } from '../src/client'\n\ntest('sends', () => {\n  const client = new Client()\n  client.run()\n})\n\ntest('listens', () => {\n  const client = new Server()\n  client.run()\n})\n\ndescribe('outer', () => {\n  const shared = new Server()\n  it('inner', () => {\n    shared.stop()\n  })\n})\n"),
    ]);
    let has = |to: &str| ts.iter().any(|(from, t, _)| from == "client.test.ts" && t == to);
    assert!(has("src_client_client_run"), "{ts:#?}");
    assert!(has("src_client_server_run"), "{ts:#?}");
    assert!(has("src_client_server_stop"), "{ts:#?}");
    // a function written in a callback is a name of that callback and of
    // the ones inside it; a module wrapped in a function still calls its own
    let js = calls_conf("block-defs", &[
        ("test/helper.test.js", "describe('outer', () => {\n  const make = () => 1\n  it('inner', () => {\n    make()\n  })\n})\n"),
        ("lib/wrapped.js", "(function () {\n  function helper() {\n    return 1\n  }\n  function run() {\n    return helper()\n  }\n  run()\n})()\n"),
    ]);
    assert!(js.iter().any(|(_, t, _)| t == "test_helper_test_make"), "{js:#?}");
    assert!(js.iter().any(|(f, t, _)| f == "run()" && t == "lib_wrapped_helper"), "{js:#?}");
    assert!(js.iter().any(|(f, t, _)| f == "wrapped.js" && t == "lib_wrapped_run"), "{js:#?}");
    let rb = calls_conf("block-locals-rb", &[
        ("lib/conn.rb", "class Conn\n  def build_url\n    1\n  end\nend\n\nclass Pool\n  def build_url\n    2\n  end\nend\n"),
        ("spec/conn_spec.rb", "require_relative '../lib/conn'\n\nRSpec.describe Conn do\n  it 'builds' do\n    conn = Conn.new\n    conn.build_url\n  end\n\n  it 'pools' do\n    conn = Pool.new\n    conn.build_url\n  end\nend\n"),
    ]);
    assert!(rb.iter().any(|(_, t, _)| t == "lib_conn_conn_build_url"), "{rb:#?}");
    assert!(rb.iter().any(|(_, t, _)| t == "lib_conn_pool_build_url"), "{rb:#?}");
}

#[test]
fn ruby_a_reopened_class_has_what_its_other_opening_extends() {
    // `Shop::Order.register(…)` in a file that opens `class Order` again:
    // `extend Registry` is written where the class is declared
    let got = calls_conf("rb-reopened-extend", &[
        ("lib/shop/registry.rb", "module Shop\n  module Registry\n    def register(x)\n      x\n    end\n  end\nend\n"),
        ("lib/shop/order.rb", "require_relative 'registry'\n\nmodule Shop\n  class Order\n    extend Registry\n  end\nend\n"),
        ("lib/shop/order/json.rb", "require_relative '../order'\n\nmodule Shop\n  class Order\n    class Json\n    end\n  end\nend\n\nShop::Order.register(json: Shop::Order::Json)\n"),
        // a class written as a constant assigned `Struct.new(…) do … end`
        ("lib/shop/line.rb", "require_relative 'registry'\n\nmodule Shop\n  Line = Struct.new(:sku, :count) do\n    extend Registry\n  end\nend\n"),
        ("lib/shop/line/csv.rb", "require_relative '../line'\n\nShop::Line.register(csv: 1)\n"),
    ]);
    assert!(
        got.iter()
            .any(|(f, t, _)| f == "csv.rb" && t == "lib_shop_registry_shop_registry_register"),
        "{got:#?}"
    );
    assert!(
        got.iter()
            .any(|(f, t, _)| f == "json.rb" && t == "lib_shop_registry_shop_registry_register"),
        "{got:#?}"
    );
}
