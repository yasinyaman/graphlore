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
