//! The diagnostics behind `benchmarks/unresolved.py`: a call left without an
//! edge whose name is one definition of the project is written with the
//! reason, and asking for them changes nothing in the graph.

use graphlore_engine::{build_graph, BuildOptions};
use serde_json::Value;

fn project(name: &str, files: &[(&str, &str)]) -> std::path::PathBuf {
    let dir = std::env::temp_dir().join(format!("graphlore-unres-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    for (rel, src) in files {
        let path = dir.join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, src).unwrap();
    }
    dir
}

#[test]
fn an_unresolved_call_is_written_with_its_reason() {
    let dir = project(
        "rows",
        &[
            // `settle` is defined once, in a directory `use.py` neither imports
            // nor sits in: an untyped parameter gives no evidence for it
            ("lib/ledger.py", "class Ledger:\n    def settle(self): ...\n"),
            ("app/use.py", "def run(book):\n    book.settle()\n    print(book)\n"),
        ],
    );
    let out = dir.join("unresolved.json");
    let mut opts = BuildOptions::new(&dir);
    let (plain, _) = build_graph(&opts).unwrap();
    opts.unresolved = Some(out.clone());
    let (asked, _) = build_graph(&opts).unwrap();
    let rows: Vec<Value> = serde_json::from_str(&std::fs::read_to_string(&out).unwrap()).unwrap();
    std::fs::remove_dir_all(&dir).unwrap();

    // one row: `print` is no definition of the project
    assert_eq!(rows.len(), 1, "{rows:#?}");
    let row = &rows[0];
    assert_eq!(row["file"], "app/use.py");
    assert_eq!(row["line"], 2);
    assert_eq!(row["path"], "book.settle");
    assert_eq!(row["reason"], "untyped local or parameter");
    assert_eq!(row["in_definition"], true);
    assert_eq!(row["candidate"]["file"], "lib/ledger.py");
    assert_eq!(row["candidate_visible"], false);
    assert_eq!(row["library_like"], Value::Null);
    // the graph is the same whether the rows were asked for or not
    assert_eq!(plain["nodes"], asked["nodes"]);
    assert_eq!(plain["links"], asked["links"]);
}

#[test]
fn a_row_that_is_a_librarys_name_says_so() {
    let dir = project(
        "library",
        &[
            // a test double has the one `header_line` of the project: the
            // call in `app/` is of a library's response
            ("tests/fake.py", "class FakeReply:\n    def header_line(self): ...\n"),
            ("app/use.py", "def run(reply):\n    reply.header_line()\n"),
            // `once` is every emitter's; the project's one is a namesake
            (
                "lib/bus.js",
                "class Bus {\n  once(name) { return name; }\n}\nmodule.exports = Bus;\n",
            ),
            ("lib/wire.js", "function wire(socket) {\n  socket.once('close');\n}\n"),
        ],
    );
    let out = dir.join("unresolved.json");
    let mut opts = BuildOptions::new(&dir);
    opts.unresolved = Some(out.clone());
    build_graph(&opts).unwrap();
    let rows: Vec<Value> = serde_json::from_str(&std::fs::read_to_string(&out).unwrap()).unwrap();
    std::fs::remove_dir_all(&dir).unwrap();
    let why = |file: &str| {
        rows.iter()
            .find(|r| r["file"] == file)
            .map(|r| r["library_like"].clone())
            .unwrap_or_default()
    };
    assert_eq!(why("app/use.py"), "the one definition is test code, the call is not", "{rows:#?}");
    assert_eq!(why("lib/wire.js"), "a name the language's own types answer to", "{rows:#?}");
}

#[test]
fn a_library_interfaces_one_implementation_is_marked_on_an_untyped_receiver() {
    let dir = project(
        "interface",
        &[
            // the project's one `getUri` implements PSR-7's interface: an
            // untyped `$request` may be any request
            (
                "src/Request.php",
                "<?php\nnamespace App;\nuse Psr\\Http\\Message\\RequestInterface;\nclass Unvalidated implements RequestInterface {\n    public function getUri() { return 1; }\n}\n",
            ),
            // a type of the project's own with no outside base: still a gap
            ("src/Ledger.php", "<?php\nnamespace App;\nclass Ledger {\n    public function settle() { return 1; }\n}\n"),
            // a marker interface declares no member a call names: a gap
            (
                "src/Tag.php",
                "<?php\nnamespace App;\nclass Tag implements \\JsonSerializable {\n    public function label() { return 1; }\n    public function jsonSerialize(): mixed { return 1; }\n}\n",
            ),
            // a typed receiver of a project type without the member: a gap
            ("src/Plain.php", "<?php\nnamespace App;\nclass Plain {\n}\n"),
            (
                "app/use.php",
                "<?php\nfunction send($request) {\n    return $request->getUri();\n}\nfunction pay($book) {\n    return $book->settle();\n}\nfunction typed(\\App\\Plain $p) {\n    return $p->getUri();\n}\nfunction show($tag) {\n    return $tag->label();\n}\n",
            ),
        ],
    );
    let out = dir.join("unresolved.json");
    let mut opts = BuildOptions::new(&dir);
    opts.unresolved = Some(out.clone());
    build_graph(&opts).unwrap();
    let rows: Vec<Value> = serde_json::from_str(&std::fs::read_to_string(&out).unwrap()).unwrap();
    std::fs::remove_dir_all(&dir).unwrap();
    let row = |line: u64| rows.iter().find(|r| r["line"] == line).cloned();
    let untyped = row(3).expect("$request->getUri() is a row");
    assert_eq!(untyped["library_like"], "the one definition implements a library type's member");
    let own = row(6).expect("$book->settle() is a row");
    assert_eq!(own["library_like"], Value::Null, "{own:#?}");
    let typed = row(9).expect("$p->getUri() is a row");
    assert_eq!(typed["library_like"], Value::Null, "{typed:#?}");
    let marker = row(12).expect("$tag->label() is a row");
    assert_eq!(marker["library_like"], Value::Null, "{marker:#?}");
}

#[test]
fn cpp_std_names_are_the_librarys_even_where_the_project_opens_std() {
    let dir = project(
        "cppstd",
        &[
            // the project opens `namespace std` for a specialization, and has
            // one `async` and one `share` of its own
            (
                "include/wrap.h",
                "namespace shop {\ntemplate <class T> class Wrapper {\n  public:\n    T share() { return future.share(); }\n  private:\n    std::future<T> future;\n};\ntemplate <class Fn> auto async(Fn fn) { return fn(); }\n}\nnamespace std {\ntemplate <> struct hash<shop::Wrapper<int>> {};\n}\n",
            ),
            ("src/run.cpp", "#include \"../include/wrap.h\"\nint run() {\n    auto f = std::async(std::launch::async, [] { return 1; });\n    std::promise<void> release;\n    auto gate = release.get_future().share();\n    return 0;\n}\n"),
        ],
    );
    let out = dir.join("unresolved.json");
    let mut opts = BuildOptions::new(&dir);
    opts.unresolved = Some(out.clone());
    build_graph(&opts).unwrap();
    let rows: Vec<Value> = serde_json::from_str(&std::fs::read_to_string(&out).unwrap()).unwrap();
    std::fs::remove_dir_all(&dir).unwrap();
    let row = |path: &str| rows.iter().find(|r| r["path"] == path).cloned();
    let call = row("std.async").expect("std::async is a row");
    assert_eq!(call["library_like"], "the head is a library's type or global", "{call:#?}");
    let local = row("release.get_future.().share").expect("release's share is a row");
    assert_eq!(local["library_like"], "the receiver is a library's", "{local:#?}");
    let held = row("future.share").expect("future.share is a row");
    assert_eq!(held["library_like"], "the receiver is a library's", "{held:#?}");
}

#[test]
fn js_rows_that_are_no_gap_say_why() {
    let dir = project(
        "jsmarks",
        &[
            // a candidate in minified code; a project function `listen` beside
            // an app that holds a library's value; `logout` beside passport's
            ("public/js/lib.min.js", "var q = {};\nq.param = function param(a) { return a; };\n"),
            ("server.js", "const express = require('express');\nconst app = express();\nfunction listen() {\n  app.listen(3000);\n}\n"),
            ("routes.js", "module.exports = function (app) {\n  app.param('id', 1);\n};\n"),
            ("users.js", "exports.logout = function logout(req, res) {\n  req.logout();\n};\n"),
            // a callback the caller is handed
            ("redirect.js", "function test(app, done) {\n  return done(null, app);\n}\n"),
            ("finish.js", "function done(x) { return x; }\n"),
            // a field holding a function; a property defined at run time
            ("view.js", "function engine(a) { return a; }\nfunction route(p) { return p; }\nfunction View(opts) {\n  this.engine = opts.engines.html;\n  Object.defineProperty(this, 'router', { get: function () { return 1; } });\n}\nView.prototype.render = function render() {\n  this.engine(1);\n  this.router.route('/');\n};\n"),
            // a property middleware attaches to the request
            ("articles.js", "exports.load = function (req, res, next, id) {\n  req.article = { id: id };\n  next();\n};\nexports.destroy = function (req, res) {\n  req.article.removeComment(1);\n  const article = req.article;\n  article.addComment(2);\n};\n"),
            ("lib/models.js", "var proto = {};\nproto.removeComment = function removeComment(i) { return i; };\nproto.addComment = function addComment(i) { return i; };\n"),
        ],
    );
    let out = dir.join("unresolved.json");
    let mut opts = BuildOptions::new(&dir);
    opts.unresolved = Some(out.clone());
    build_graph(&opts).unwrap();
    let rows: Vec<Value> = serde_json::from_str(&std::fs::read_to_string(&out).unwrap()).unwrap();
    std::fs::remove_dir_all(&dir).unwrap();
    let why = |path: &str| {
        rows.iter()
            .find(|r| r["path"] == path)
            .map(|r| r["library_like"].clone())
            .unwrap_or(Value::Null)
    };
    assert_eq!(why("app.param"), "the one definition is in vendored or minified code", "{rows:#?}");
    assert_eq!(why("app.listen"), "the receiver holds a library's value", "{rows:#?}");
    assert_eq!(
        why("req.logout"),
        "the receiver is what a framework hands the function",
        "{rows:#?}"
    );
    assert_eq!(why("done"), "the name is a local or parameter of the caller", "{rows:#?}");
    assert_eq!(
        why("self.engine"),
        "the member is a field assigned a value, not a method",
        "{rows:#?}"
    );
    assert_eq!(
        why("self.router.route"),
        "the receiver is a property defined at run time",
        "{rows:#?}"
    );
    assert_eq!(
        why("req.article.removeComment"),
        "the receiver is a property middleware attaches to a request",
        "{rows:#?}"
    );
    assert_eq!(
        why("article.addComment"),
        "the receiver is a property middleware attaches to a request",
        "{rows:#?}"
    );
}

#[test]
fn rust_rows_on_library_values_say_so() {
    let dir = project(
        "rsmarks",
        &[
            (
                "Cargo.toml",
                "[package]\nname = \"app\"\nversion = \"0.1.0\"\n\n[dependencies]\nserde_json = \"1\"\nureq-proto = { version = \"0.1\" }\n\n[dependencies.http]\nversion = \"1\"\n",
            ),
            ("src/lib.rs", "pub mod req;\npub mod ext;\npub mod run;\npub mod own;\n"),
            // a field and a tuple field of a library type
            ("src/req.rs", "use http::request::Builder;\nuse http::Response;\n\npub struct RequestBuilder {\n    builder: Builder,\n}\n\nimpl RequestBuilder {\n    pub fn method_ref(&self) -> u8 {\n        self.builder.method_ref()\n    }\n    pub fn version(&self) -> u8 {\n        0\n    }\n    pub fn header(self) -> Self {\n        self\n    }\n}\n\npub struct DebugResponse<'a>(&'a Response<u8>);\n\nimpl<'a> DebugResponse<'a> {\n    pub fn show(&self) -> u8 {\n        self.0.version()\n    }\n}\n"),
            // `self` in an impl for a library type
            ("src/ext.rs", "use http::Uri;\n\npub trait UriExt {\n    fn port_of(&self) -> u8;\n}\n\nimpl UriExt for Uri {\n    fn port_of(&self) -> u8 {\n        self.authority_text()\n    }\n}\n\npub struct Tests;\n\nimpl Tests {\n    pub fn authority_text(&self) -> u8 {\n        0\n    }\n}\n"),
            // an alias of a library type; paths headed by `std` and a dependency
            ("src/run.rs", "type Call<T> = ureq_proto::client::Call<T>;\n\npub fn go(call: Call<u8>) {\n    call.header();\n    serde_json::from_reader(1);\n    std::thread::spawn(1);\n}\n"),
            // the project's own field: no mark
            ("src/own.rs", "use crate::req::RequestBuilder;\n\npub struct SendBody;\n\nimpl SendBody {\n    pub fn from_reader() {}\n    pub fn spawn() {}\n    pub fn lonely(&self) {}\n}\n\npub struct Holder {\n    inner: RequestBuilder,\n}\n\nimpl Holder {\n    pub fn f(&self) {\n        self.inner.lonely();\n    }\n}\n"),
        ],
    );
    let out = dir.join("unresolved.json");
    let mut opts = BuildOptions::new(&dir);
    opts.unresolved = Some(out.clone());
    build_graph(&opts).unwrap();
    let rows: Vec<Value> = serde_json::from_str(&std::fs::read_to_string(&out).unwrap()).unwrap();
    std::fs::remove_dir_all(&dir).unwrap();
    let why = |path: &str| {
        rows.iter()
            .find(|r| r["path"] == path)
            .map(|r| r["library_like"].clone())
            .unwrap_or_else(|| panic!("no row {path}: {rows:#?}"))
    };
    let receiver = "the receiver is a library's";
    let head = "the head is a library's type or global";
    assert_eq!(why("self.builder.method_ref"), receiver, "{rows:#?}");
    assert_eq!(why("self.[#0].version"), receiver, "{rows:#?}");
    assert_eq!(why("self.authority_text"), receiver, "{rows:#?}");
    assert_eq!(why("call.header"), receiver, "{rows:#?}");
    assert_eq!(why("serde_json.from_reader"), head, "{rows:#?}");
    assert_eq!(why("std.thread.spawn"), head, "{rows:#?}");
    assert_eq!(why("self.inner.lonely"), Value::Null, "{rows:#?}");
}

#[test]
fn rust_closure_parameters_type_parameters_and_own_types_of_the_language() {
    let dir = project(
        "rsmarks2",
        &[
            ("Cargo.toml", "[package]\nname = \"app\"\nversion = \"0.1.0\"\n\n[dependencies]\nureq-proto = \"0.1\"\n"),
            ("src/lib.rs", "pub mod handler;\npub mod part;\npub mod time;\npub use ureq_proto::http;\n"),
            // closure parameters the callee hands a library's types; one it
            // hands a project type (no mark)
            ("src/handler.rs", "use crate::http::{Request, Uri};\n\npub struct Req;\nimpl Req {\n    pub fn mine(&self) -> u8 { 0 }\n}\npub struct Part;\nimpl Part {\n    pub fn headers(&self) -> u8 { 0 }\n}\npub struct Builder;\nimpl Builder {\n    pub fn query(&self) -> u8 { 0 }\n}\npub struct TestHandler;\nimpl TestHandler {\n    fn new(pattern: &'static str, handler: impl Fn(Uri, Request<()>) -> u8) -> Self { TestHandler }\n    fn own<F: Fn(Req) -> u8>(pattern: &'static str, handler: F) -> Self { TestHandler }\n}\npub fn setup() {\n    TestHandler::new(\"/post\", |uri, req| { req.headers(); uri.query() });\n    TestHandler::own(\"/own\", |r| r.lonely());\n}\n"),
            // a value of the function's own type parameter, a `&str`; a
            // parameter of the project's own `Duration` (no mark)
            ("src/part.rs", "use std::path::Path;\nuse crate::time::Duration;\n\npub struct Form;\nimpl Form {\n    pub fn file_name(&self) -> u8 { 0 }\n    pub fn authority_of(&self) -> u8 { 0 }\n    pub fn lonely(&self) -> u8 { 0 }\n    pub fn far(&self) -> u8 { 0 }\n}\npub fn file<P: AsRef<Path>>(path: P) -> u8 {\n    path.as_ref().file_name()\n}\npub fn authority(uri: &str) -> u8 {\n    uri.parse::<u8>().unwrap().authority_of()\n}\npub fn wait(d: Duration) -> u8 {\n    d.far()\n}\n"),
            ("src/time.rs", "pub struct Duration;\n"),
        ],
    );
    let out = dir.join("unresolved.json");
    let mut opts = BuildOptions::new(&dir);
    opts.unresolved = Some(out.clone());
    build_graph(&opts).unwrap();
    let rows: Vec<Value> = serde_json::from_str(&std::fs::read_to_string(&out).unwrap()).unwrap();
    std::fs::remove_dir_all(&dir).unwrap();
    let why = |name: &str| {
        rows.iter()
            .find(|r| r["name"] == name)
            .map(|r| r["library_like"].clone())
            .unwrap_or_else(|| panic!("no row {name}: {rows:#?}"))
    };
    let receiver = "the receiver is a library's";
    assert_eq!(why("headers"), receiver, "{rows:#?}");
    assert_eq!(why("query"), receiver, "{rows:#?}");
    assert_eq!(why("file_name"), receiver, "{rows:#?}");
    assert_eq!(why("authority_of"), receiver, "{rows:#?}");
    assert_eq!(why("lonely"), Value::Null, "{rows:#?}");
    assert_eq!(why("far"), Value::Null, "{rows:#?}");
}

#[test]
fn typescript_rows_on_library_values_and_run_time_members_say_so() {
    let dir = project(
        "tsmarks",
        &[
            ("package.json", "{\"name\":\"app\",\"version\":\"1.0.0\"}\n"),
            // a member a function puts on a value it makes, called on a value
            // the walk cannot type
            ("src/as-promise.ts", "export default function asPromise(): Promise<string> {\n\tconst promise = Promise.resolve('x') as any;\n\tpromise.bufferOf = function () { return 1; };\n\treturn promise;\n}\n"),
            ("test/promise.ts", "const test = (n: string, f: (got: unknown) => void) => f(null);\ntest('x', async got => {\n\tconst promise = (got as any)();\n\tpromise.bufferOf();\n});\n"),
            // a property of a library type; a typed local whose type is partly
            // a library's; a parameter written with a library's qualified type
            ("test/server.ts", "import http from 'node:http';\nimport net from 'node:net';\nexport type TestServer = {\n\thttp: http.Server;\n\tmine: Own;\n};\nexport type Extended = http.Server & {url: string};\nexport class Own {\n\tfar(): number { return 0; }\n}\nexport function start(server: TestServer, other: Extended) {\n\tserver.http.listenOn();\n\tother.closeAll();\n\tserver.mine.lonely();\n}\nexport const bind = (socket: net.Server) => socket.boundTo();\n"),
            ("src/helpers.ts", "export function listenOn() {}\nexport function closeAll() {}\nexport function lonely() {}\nexport function boundTo() {}\n"),
        ],
    );
    let out = dir.join("unresolved.json");
    let mut opts = BuildOptions::new(&dir);
    opts.unresolved = Some(out.clone());
    build_graph(&opts).unwrap();
    let rows: Vec<Value> = serde_json::from_str(&std::fs::read_to_string(&out).unwrap()).unwrap();
    std::fs::remove_dir_all(&dir).unwrap();
    let why = |name: &str| {
        rows.iter()
            .find(|r| r["name"] == name)
            .map(|r| r["library_like"].clone())
            .unwrap_or_else(|| panic!("no row {name}: {rows:#?}"))
    };
    assert_eq!(why("bufferOf"), "the member is put on a value at run time", "{rows:#?}");
    assert_eq!(why("listenOn"), "the receiver is a library's", "{rows:#?}");
    assert_eq!(why("closeAll"), "the receiver is a library's", "{rows:#?}");
    assert_eq!(why("boundTo"), "the receiver is a library's", "{rows:#?}");
    assert_eq!(why("lonely"), Value::Null, "{rows:#?}");
}

#[test]
fn go_rows_on_library_fields_results_and_function_locals_say_so() {
    let dir = project(
        "gomarks",
        &[
            ("go.mod", "module example.com/m\n\ngo 1.22\n"),
            // a field declared with a library's type, a function declared to
            // give one, a local function value, and a call that is a gap
            ("a.go", "package m\n\nimport (\n\t\"bytes\"\n\t\"net/http\"\n\n\t\"github.com/dustin/go-broadcast\"\n)\n\ntype Request struct {\n\tbodyBuf    *bytes.Buffer\n\tRawRequest *http.Request\n\tmine       *Own\n}\n\ntype Own struct{}\n\nfunc (r *Request) Send() {\n\t_ = r.bodyBuf.Len()\n\tr.RawRequest.WithContext()\n\th := func(s string) string { return s }\n\t_ = h(\"x\")\n\tr.mine.lonely()\n}\n\nfunc room(id string) broadcast.Broadcaster { return nil }\n\nfunc Post() {\n\troom(\"x\").Submit(1)\n}\n"),
            ("b.go", "package m\n\nfunc Len() int { return 0 }\nfunc WithContext() {}\nfunc h(s string) string { return s }\nfunc Submit(x int) {}\nfunc lonely() {}\n"),
        ],
    );
    let out = dir.join("unresolved.json");
    let mut opts = BuildOptions::new(&dir);
    opts.unresolved = Some(out.clone());
    build_graph(&opts).unwrap();
    let rows: Vec<Value> = serde_json::from_str(&std::fs::read_to_string(&out).unwrap()).unwrap();
    std::fs::remove_dir_all(&dir).unwrap();
    let why = |name: &str| {
        rows.iter()
            .find(|r| r["name"] == name)
            .map(|r| r["library_like"].clone())
            .unwrap_or_else(|| panic!("no row {name}: {rows:#?}"))
    };
    assert_eq!(why("Len"), "the receiver is a library's", "{rows:#?}");
    assert_eq!(why("WithContext"), "the receiver is a library's", "{rows:#?}");
    assert_eq!(why("Submit"), "the receiver is a library's", "{rows:#?}");
    assert_eq!(why("h"), "the name is a local or parameter of the caller", "{rows:#?}");
    assert_eq!(why("lonely"), Value::Null, "{rows:#?}");
}

#[test]
fn java_rows_on_library_fields_java_lang_and_qualified_names_say_so() {
    let dir = project(
        "javamarks",
        &[
            // a field of a library type, of a `java.lang` type, of a fully
            // qualified type, a field beside a method of its name, a library
            // result, a qualified construction; and a call that is a gap
            ("src/app/Calls.java", "package app;\n\nimport java.util.List;\nimport okhttp3.Request;\nimport okhttp3.mockwebserver.MockWebServer;\n\npublic final class Calls {\n  private static final String SET = \"abc\";\n  private final okhttp3.Response rawResponse = null;\n  public final MockWebServer server = new MockWebServer();\n  final List<String> factories = null;\n  public List<String> factories() { return factories; }\n  Request request() { return null; }\n  private final Own mine = null;\n\n  void go() {\n    SET.codePointCount(0, 1);\n    rawResponse.isSuccessful();\n    server.takeRequest().getRequestUrl().queryParameter(\"n\");\n    factories.lastIndexOf(\"x\");\n    request().tag(Object.class);\n    new okhttp3.Response.Builder().code(200).message(\"m\");\n    mine.lonely();\n  }\n}\n"),
            ("src/app/Own.java", "package app;\nclass Own {}\n"),
            ("src/app/Utils.java", "package app;\nfinal class Utils {\n  static int codePointCount(int a, int b) { return 0; }\n  boolean isSuccessful() { return true; }\n  String queryParameter(String n) { return n; }\n  int lastIndexOf(Object o) { return 0; }\n  Object tag(Object c) { return c; }\n  Object message(String m) { return m; }\n  void lonely() {}\n}\n"),
        ],
    );
    let out = dir.join("unresolved.json");
    let mut opts = BuildOptions::new(&dir);
    opts.unresolved = Some(out.clone());
    build_graph(&opts).unwrap();
    let rows: Vec<Value> = serde_json::from_str(&std::fs::read_to_string(&out).unwrap()).unwrap();
    std::fs::remove_dir_all(&dir).unwrap();
    let why = |name: &str| {
        rows.iter()
            .find(|r| r["name"] == name)
            .map(|r| r["library_like"].clone())
            .unwrap_or_else(|| panic!("no row {name}: {rows:#?}"))
    };
    for name in ["codePointCount", "isSuccessful", "queryParameter", "lastIndexOf", "tag"] {
        assert_eq!(why(name), "the receiver is a library's", "{name}: {rows:#?}");
    }
    assert_eq!(why("message"), "the head is a library's type or global", "{rows:#?}");
    assert_eq!(why("lonely"), Value::Null, "{rows:#?}");
}
