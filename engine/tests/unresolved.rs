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
fn rows_a_librarys_type_or_another_scope_explains_say_so() {
    let dir = project(
        "library-step",
        &[
            (
                "src/Stream.php",
                "<?php\nnamespace App;\nuse Psr\\Http\\Message\\ResponseInterface;\nclass Stream {\n    public ?ResponseInterface $response = null;\n    public function rewind(): void {}\n    public function getValue(): int { return 1; }\n    public function getBody(): int { return 1; }\n}\n",
            ),
            (
                "app/use.php",
                "<?php\nnamespace App\\Jobs;\nfunction run($sink, \\App\\Stream $s) {\n    \\rewind($sink);\n    $p = new \\ReflectionProperty($s, 'x');\n    $p->getValue();\n    return $s->response->getBody();\n}\n",
            ),
            // a `let` is a bare name of its block: no member of anything, and
            // no name of another block of its file
            ("lib/conn.rb", "class Conn\nend\n"),
            (
                "spec/conn_spec.rb",
                "RSpec.describe Conn do\n  describe 'a' do\n    let(:uri) { 1 }\n  end\n\n  it 'b' do\n    expect(uri.to_s).to eq('x')\n    expect(conn.uri).to eq(1)\n  end\nend\n",
            ),
        ],
    );
    let out = dir.join("unresolved.json");
    let mut opts = BuildOptions::new(&dir);
    opts.unresolved = Some(out.clone());
    build_graph(&opts).unwrap();
    let rows: Vec<Value> = serde_json::from_str(&std::fs::read_to_string(&out).unwrap()).unwrap();
    std::fs::remove_dir_all(&dir).unwrap();
    let row = |file: &str, path: &str| {
        rows.iter()
            .find(|r| r["file"] == file && r["path"] == path)
            .cloned()
            .unwrap_or_else(|| panic!("no row {file} {path} in {rows:#?}"))
    };
    // PHP: a method is not in scope by its bare name
    // (`\\rewind`, fully qualified, keeps the global namespace's `\\` in its
    // path, and is as bare: the global function, never `Stream::rewind`)
    assert_eq!(row("app/use.php", "\\.rewind")["library_like"], "a bare name is no method here");
    // a local constructed from a type the project does not define
    assert_eq!(row("app/use.php", "$p.getValue")["library_like"], "the receiver is a library's");
    // a field written with a library's type: what hangs off it is the library's
    assert_eq!(
        row("app/use.php", "$s.response.getBody")["library_like"],
        "the receiver is a library's"
    );
    assert_eq!(
        row("spec/conn_spec.rb", "uri")["library_like"],
        "the one definition is a let of another block"
    );
    // `conn.uri`: the `let(:uri)` is no member of `conn`, so no candidate
    assert!(rows.iter().all(|r| r["path"] != "conn.uri"), "{rows:#?}");
}

#[test]
fn a_name_a_type_declares_as_an_attribute_is_no_gap_on_an_untyped_receiver() {
    let dir = project(
        "attribute",
        &[
            // `status` is a member of the struct `Env` and a method of
            // `Reply`: an untyped `env` may be either
            ("lib/env.rb", "Env = Struct.new(:status, :body)\n"),
            (
                "lib/reply.rb",
                "class Reply\n  def status\n    200\n  end\n\n  def finish\n    1\n  end\nend\n",
            ),
            ("app/use.rb", "def show(env, reply)\n  env.status\n  reply.finish\nend\n"),
        ],
    );
    let out = dir.join("unresolved.json");
    let mut opts = BuildOptions::new(&dir);
    opts.unresolved = Some(out.clone());
    build_graph(&opts).unwrap();
    let rows: Vec<Value> = serde_json::from_str(&std::fs::read_to_string(&out).unwrap()).unwrap();
    std::fs::remove_dir_all(&dir).unwrap();
    let row = |path: &str| rows.iter().find(|r| r["path"] == path).cloned();
    let status = row("env.status").expect("env.status is a row");
    assert_eq!(status["library_like"], "a type declares an attribute of the name", "{status:#?}");
    // a name only a `def` declares stays a gap
    if let Some(finish) = row("reply.finish") {
        assert_eq!(finish["library_like"], Value::Null, "{finish:#?}");
    }
}
