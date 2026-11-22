//! Span goldens: the cases graphlore's `tests/test_tools.py` holds for
//! `spans.py`, so both engines answer them the same. Each is a real repository's
//! failure cut down to a few lines (see docs/benchmark-polyglot.md).

use std::collections::BTreeSet;

use graphlore_engine::{file_facts, file_spans, DefKind};

fn quals(rel: &str, src: &str) -> BTreeSet<String> {
    file_spans(src.as_bytes(), rel).into_iter().map(|s| s.3).collect()
}

fn span(rel: &str, src: &str, qual: &str) -> (u32, u32, u32) {
    file_spans(src.as_bytes(), rel)
        .into_iter()
        .find(|s| s.3 == qual)
        .map(|s| (s.0, s.1, s.2))
        .unwrap_or_else(|| panic!("no span {qual} in {rel}"))
}

fn set(items: &[&str]) -> BTreeSet<String> {
    items.iter().map(|s| s.to_string()).collect()
}

#[track_caller]
fn has_all(got: &BTreeSet<String>, want: &[&str]) {
    let missing: Vec<_> = want.iter().filter(|w| !got.contains(**w)).collect();
    assert!(missing.is_empty(), "missing {missing:?} in {got:?}");
}

#[test]
fn python_is_decorator_aware_and_ends_on_code() {
    let src =
        "import functools\n@functools.cache\ndef decorated():\n    return 1\n    # trailing\n";
    assert_eq!(span("u.py", src, "decorated"), (2, 4, 3));
    // a comment above an undecorated def is not part of it (stdlib ast agrees)
    assert_eq!(span("v.py", "# doc\ndef f():\n    pass\n", "f"), (2, 3, 2));
    // a lambda bound to a name is no definition
    assert_eq!(
        quals("w.py", "f = lambda: 1\nclass A:\n    def m(self): pass\n"),
        set(&["A", "A.m"])
    );
}

#[test]
fn rust_excludes_type_params_and_prefixes_impl_methods() {
    let got = quals(
        "x.rs",
        "type Alias = u32;\n\
        fn parse<T: Clone>(x: T) -> impl Iterator<Item = T> { std::iter::once(x) }\n\
        struct S;\nimpl S { fn run(&self) {} }\n",
    );
    has_all(&got, &["Alias", "parse", "S", "S.run"]);
    assert!(!got.iter().any(|q| ["T", "Item"].contains(&q.rsplit('.').next().unwrap())));
    let got = quals(
        "lib.rs",
        "use std::io;\nstruct Pool { size: i32 }\n\
        fn make() -> io::Result<Pool> {\n    Ok(Pool { size: 1 })\n}\n",
    );
    assert_eq!(got, set(&["Pool", "make"]));
}

#[test]
fn rust_test_regions_and_modules() {
    let src = "pub fn acquire() -> i32 {\n    1\n}\nmod helpers;\n#[cfg(not(test))]\nmod real {\n    \
        pub fn used() { super::acquire(); }\n}\n#[cfg(test)]\nmod tests {\n    use super::*;\n    \
        fn setup() -> i32 { acquire() }\n    #[test]\n    fn it_acquires() {\n        \
        assert_eq!(setup(), 1);\n    }\n}\n#[tokio::test]\nasync fn top_level_test() { acquire(); }\n\
        #[cfg(test)]\npub fn only_for_tests() -> i32 { 2 }\n";
    let got = quals("src/pool.rs", src);
    has_all(
        &got,
        &[
            "acquire",
            "real",
            "real.used",
            "tests",
            "tests.setup",
            "tests.it_acquires",
            "top_level_test",
        ],
    );
    assert!(!got.contains("helpers"));
    let facts = file_facts(src.as_bytes(), "src/pool.rs");
    // (`#[cfg(test)] fn`: a helper only the tests are compiled with)
    assert_eq!(facts.test_regions, vec![(10, 17), (19, 19), (21, 21)]);
    let tests: BTreeSet<_> =
        facts.defs.iter().filter(|d| d.is_test).map(|d| d.qualname.as_str()).collect();
    assert_eq!(
        tests,
        ["only_for_tests", "tests", "tests.it_acquires", "tests.setup", "top_level_test"].into()
    );
    assert!(facts.defs.iter().all(|d| d.name != "tests" || d.kind == DefKind::Namespace));
}

#[test]
fn go_doc_comments_receivers_and_types() {
    assert_eq!(
        span(
            "x.go",
            "package m\n\n// Handler does the thing.\nfunc Handler() int {\n\treturn 1\n}\n",
            "Handler"
        ),
        (3, 6, 4)
    );
    let src =
        "package m\ntype Client struct{}\nfunc (c *Client) Get(u string) error { return nil }\n\
               func Helper() int { return 1 }\ntype Doer interface { Do() }\n";
    has_all(&quals("c.go", src), &["Client", "Client.Get", "Helper", "Doer", "Doer.Do"]);
    // a function type written in a map type names nothing
    let map = "package m\nvar hashes = map[string]func() int{\n\t\"a\": f,\n}\n";
    assert!(quals("m.go", map).is_empty());
    let facts = file_facts(src.as_bytes(), "c.go");
    let kind = |n: &str| facts.defs.iter().find(|d| d.name == n).unwrap().kind;
    assert_eq!(kind("Client"), DefKind::Struct);
    assert_eq!(kind("Doer"), DefKind::Interface);
    assert_eq!(kind("Get"), DefKind::Method);
    assert_eq!(
        facts.defs.iter().find(|d| d.name == "Get").unwrap().owner.as_deref(),
        Some("Client")
    );
}

#[test]
fn js_bound_and_member_assigned_functions() {
    has_all(
        &quals(
            "a.js",
            "const fetchUser = (id) => {\n  return get(id);\n};\n\
                        const retry = async function () {\n  return 1;\n};\n",
        ),
        &["fetchUser", "retry"],
    );
    has_all(
        &quals(
            "z.js",
            "const obj = { arrowProp: (x) => x + 1, shorthand() { return 1; } };\n\
                        class Service { handler = (req) => { return req; }; }\n",
        ),
        &["arrowProp", "shorthand", "Service", "Service.handler"],
    );
    has_all(&quals("z.js", "const x = function bar() {\n  return 1;\n};\n"), &["bar"]);
    assert_eq!(
        quals(
            "response.js",
            "exports.normalize = function (type) {\n  return type;\n};\n\
               res.accepts = function () {\n  return true;\n};\nvar x = 1;\nx = 2;\n"
        ),
        set(&["normalize", "accepts"])
    );
    let src = "class Service {\n  fetch(u) {\n    return get(u);\n  }\n}\nfunction helper() {\n  return 1;\n}\n";
    assert_eq!(span("app.js", src, "Service.fetch").2, 2);
    assert_eq!(span("app.js", src, "helper").2, 6);
}

#[test]
fn typescript_interfaces_and_signature_types() {
    has_all(
        &quals(
            "client.ts",
            "interface Fetcher {\n  fetch(url: string): number;\n}\nclass Api {\n  \
               async send(url: string): Promise<number> {\n    return get(url);\n  }\n}\n",
        ),
        &["Fetcher", "Api", "Api.send"],
    );
    assert_eq!(
        quals(
            "form.tsx",
            "function FormMessage({ className }: React.ComponentProps<\"p\">) {\n  \
               return null\n}\n"
        ),
        set(&["FormMessage"])
    );
}

#[test]
fn java_kotlin_scala_csharp() {
    let src = "class Api {\n    int fetch(String u) {\n        return get(u);\n    }\n}\n";
    assert_eq!(span("Api.java", src, "Api.fetch").2, 2);
    let kt = quals(
        "Greeter.kt",
        "package demo\n\nclass Greeter(val name: String) {\n  @JvmStatic\n  \
        fun greet(): String {\n    return name\n  }\n}\n\nfun String.shout(): String {\n  \
        return uppercase()\n}\n\nobject Registry {\n}\n",
    );
    has_all(&kt, &["Greeter", "Greeter.greet", "shout", "Registry"]);
    assert!(!kt.contains("demo"));
    assert_eq!(
        quals(
            "Model.scala",
            "class ResponseBlob(val bytes: Array[Byte]) {\n  def size = bytes.length\n}\n"
        ),
        set(&["ResponseBlob", "ResponseBlob.size"])
    );
    has_all(
        &quals("Parameter.cs", "public record Parameter(string Name) {\n  public int Size() {\n    return 1;\n  }\n}\n"),
        &["Parameter", "Parameter.Size"],
    );
}

#[test]
fn swift_types_inits_protocols_and_extensions() {
    let src = "protocol DataDecoder {\n  func decode(_ data: Data) -> Value\n}\n\
        class OfflineRetrier {\n  let monitor: Monitor = Monitor()\n  init(monitor: Monitor) {\n    \
        self.monitor = monitor\n  }\n  func retry(request: Request) -> Outcome {\n    return Outcome()\n  }\n}\n\
        extension OfflineRetrier {\n  func reset() {}\n}\n";
    let got = quals("Retrier.swift", src);
    has_all(
        &got,
        &["DataDecoder", "OfflineRetrier", "OfflineRetrier.init", "OfflineRetrier.retry"],
    );
    assert!(got.iter().all(
        |q| !["Monitor", "Request", "Outcome", "Data"].contains(&q.rsplit('.').next().unwrap())
    ));
    let facts = file_facts(src.as_bytes(), "Retrier.swift");
    let ext = facts.defs.iter().find(|d| d.kind == DefKind::Extension).expect("extension");
    assert_eq!(ext.name, "OfflineRetrier");
    let reset = facts.defs.iter().find(|d| d.name == "reset").unwrap();
    assert_eq!((reset.kind, reset.owner.as_deref()), (DefKind::Method, Some("OfflineRetrier")));
}

#[test]
fn php_signature_types_are_no_spans() {
    assert_eq!(
        quals("Stack.php", "<?php\nclass Stack {\n  public function push(callable $middleware, string $name) {\n    \
               return 1;\n  }\n}\n"),
        set(&["Stack", "Stack.push"])
    );
}

#[test]
fn c_family_declarators_headers_and_prototypes() {
    let got = quals(
        "t.cpp",
        "namespace cpr {\nResponse Session::Get() {\n  return holds_alternative<int>(r);\n}\n\
                              int helper(int x){ return x; }\n}\n",
    );
    has_all(&got, &["cpr.Session.Get", "cpr.helper"]);
    assert!(!got.iter().any(|q| q.contains("holds_alternative") || q.ends_with("Get.Session.Get")));
    let cpp = quals("session.h", "namespace cpr {\nclass Session {\n public:\n  void SetUrl(const Url& url);\n  \
        const std::string& GetUrl() const {\n    return url_;\n  }\n private:\n  std::string url_;\n};\n}\n");
    has_all(&cpp, &["cpr", "cpr.Session", "cpr.Session.SetUrl", "cpr.Session.GetUrl"]);
    let c = quals(
        "hiredis.h",
        "typedef struct {\n  int type;\n} redisOptions;\ntypedef int redisFD;\n\
                               redisContext *redisConnect(const char *ip, int port);\n",
    );
    has_all(&c, &["redisOptions", "redisFD", "redisConnect"]);
    has_all(
        &quals("cookies.cpp", "const std::string& Cookie::GetDomain() const {\n  return domain_;\n}\n\
                               Cookie& Cookie::operator=(const Cookie& other) {\n  return *this;\n}\n"),
        &["Cookie.GetDomain", "Cookie.operator="],
    );
    assert_eq!(
        quals(
            "test.c",
            "struct config { int port; };\nstatic int do_connect(struct config config) {\n  \
               return config.port;\n}\n"
        ),
        set(&["config", "do_connect"])
    );
}

#[test]
fn ruby_modules_classes_and_struct_blocks() {
    let src = "module Shop\n  Line = Struct.new(:sku, :qty) do\n    def total(price)\n      qty * price\n    end\n  end\n\n  \
               class Cart\n    def empty?\n      @lines.empty?\n    end\n  end\nend\n";
    has_all(
        &quals("cart.rb", src),
        &["Shop", "Shop.Line", "Shop.Line.total", "Shop.Cart", "Shop.Cart.empty?"],
    );
}

#[test]
fn properties_and_constants_are_extra_definitions() {
    let cs = "class A { public Svc Svc { get; set; } public int M() { return 1; } }\n";
    let facts = file_facts(cs.as_bytes(), "A.cs");
    let prop = facts.defs.iter().find(|d| d.name == "Svc").expect("property");
    assert!(prop.extra && prop.kind == DefKind::Property);
    // spans.py does not report them, so file_spans leaves them out
    assert_eq!(quals("A.cs", cs), set(&["A", "A.M"]));
    let ts = "export const ICON_MAP = { a: 1 };\nconst helper = () => 1;\nclass K { x = 1; f = () => 2; }\n";
    let facts = file_facts(ts.as_bytes(), "k.ts");
    let extra: BTreeSet<(&str, DefKind)> =
        facts.defs.iter().filter(|d| d.extra).map(|d| (d.name.as_str(), d.kind)).collect();
    assert_eq!(extra, [("ICON_MAP", DefKind::Constant), ("x", DefKind::Property)].into());
    let rs = "const MAX: usize = 3;\nfn f() { const LOCAL: u8 = 1; }\n";
    let facts = file_facts(rs.as_bytes(), "a.rs");
    let extra: Vec<&str> = facts.defs.iter().filter(|d| d.extra).map(|d| d.name.as_str()).collect();
    assert_eq!(extra, ["MAX"]);
}

#[test]
fn cpp_an_if_written_once_per_configuration_is_read_once() {
    // each branch opens `if (code) {`, closed once after `#endif`: the
    // parser is given the first branch, so the methods after are the
    // class's, not the first method's; a balanced `#if` and a one-branch
    // `#ifdef` (an `extern "C" {` guard) are read as before
    let src = "namespace m {\nvoid Pool::Run() {\n    int code = 0;\n#if NEW_API\n    code = poll(1);\n    if (code) {\n        log(\"poll\");\n#else\n    code = wait(1);\n    if (code) {\n        log(\"wait\");\n#endif\n        return;\n    }\n}\n#if FAST\nvoid fast() {}\n#else\nvoid slow() {}\n#endif\nvoid Pool::Stop() {\n}\n}\n";
    let got = quals("pool.cpp", src);
    has_all(&got, &["m.Pool.Run", "m.Pool.Stop", "m.fast", "m.slow"]);
    assert!(!got.iter().any(|q| q.starts_with("m.Pool.Run.")), "{got:?}");
    assert_eq!(span("pool.cpp", src, "m.Pool.Run"), (2, 15, 2));
    assert_eq!(span("pool.cpp", src, "m.Pool.Stop"), (21, 22, 21));
}

#[test]
fn cpp_an_alias_declaration_is_a_definition() {
    // `using X = …;` has no hint word in its node type: it is named exactly
    let src = "namespace cpr {\nusing Header = std::map<std::string, std::string>;\nusing AsyncResponse = AsyncWrapper<Response>;\n}\n";
    has_all(&quals("api.h", src), &["cpr.Header", "cpr.AsyncResponse"]);
    assert_eq!(span("api.h", src, "cpr.AsyncResponse"), (3, 3, 3));
}

#[test]
fn definitions_with_no_hint_word_or_no_name() {
    // a C# delegate and a Rust macro carry no hint word in their node type;
    // C# operators and indexers and a Swift subscript carry no name of their
    // own; a Rust macro is `name!`, never the function `name`
    let cs = "public delegate void Cb(int x);\npublic class C\n{\n    public static implicit operator string(C c) => \"\";\n    public static C operator +(C a, C b) => a;\n    public int this[int i] => i;\n}\n";
    assert_eq!(
        quals("Ops.cs", cs),
        set(&["Cb", "C", "C.operator string", "C.operator+", "C.this[]"])
    );
    let facts = file_facts(cs.as_bytes(), "Ops.cs");
    let kind = |n: &str| facts.defs.iter().find(|d| d.name == n).map(|d| d.kind);
    assert_eq!(kind("Cb"), Some(DefKind::TypeAlias));
    assert_eq!(kind("operator+"), Some(DefKind::Method));
    let rs = "macro_rules! ext {\n    () => {};\n}\nfn ext() {}\n";
    assert_eq!(quals("m.rs", rs), set(&["ext!", "ext"]));
    let swift = "struct H {\n    subscript(_ name: String) -> String? { nil }\n}\n";
    assert_eq!(quals("H.swift", swift), set(&["H", "H.subscript"]));
}

#[test]
fn swift_one_case_line_declares_each_name() {
    let src = "enum K {\n    case a, b, c\n    case d\n}\n";
    assert_eq!(quals("K.swift", src), set(&["K", "K.a", "K.b", "K.c", "K.d"]));
    assert_eq!(span("K.swift", src, "K.b"), (2, 2, 2));
}

#[test]
fn cpp_a_lock_annotation_is_no_declarator() {
    // `int num GUARDED_BY(mu);` is a field, `void f() REQUIRES(mu);` the
    // function f: the macro is blanked; a function in capitals and a call
    // statement stay
    let src = "class S {\n  int num GUARDED_BY(mu);\n  int stats_[4] GUARDED_BY(mu);\n  void f() EXCLUSIVE_LOCKS_REQUIRED(mu);\n  int Recover(int* edit,\n              bool* save)\n      EXCLUSIVE_LOCKS_REQUIRED(mu);\n  void g() const LOCKS_EXCLUDED(mu) {\n  }\n};\nint ADD(int a, int b) {\n  if (a) LOG(b);\n  return ADD2(a);\n}\n";
    assert_eq!(quals("s.cc", src), set(&["S", "S.f", "S.Recover", "S.g", "ADD"]));
    let facts = file_facts(src.as_bytes(), "s.cc");
    assert!(facts.defs.iter().any(|d| d.name == "num" && d.kind == DefKind::Property));
    assert!(facts.defs.iter().any(|d| d.name == "stats_" && d.kind == DefKind::Property));
    let calls: BTreeSet<String> =
        facts.refs.iter().filter_map(|r| r.path.last().cloned()).collect();
    assert!(calls.contains("LOG") && calls.contains("ADD2"), "{calls:?}");
}

#[test]
fn c_a_typedef_named_in_parentheses() {
    let src = "typedef void (Cb)(int);\ntypedef void (*Cb2)(int);\n";
    assert_eq!(quals("a.h", src), set(&["Cb", "Cb2"]));
}

#[test]
fn cpp_a_gtest_test_is_named_by_its_two_names() {
    // `TEST(Suite, Name) { … }`: a function with no return type, named in
    // capitals, given two bare names; a method of the fixture for `TEST_F`
    let src = "class Fix {\n};\nTEST(Suite, Works) {\n  int x = 1;\n}\nTEST_F(Fix, Reads) { Do(1); }\nALPN(bool on) {\n}\n";
    assert_eq!(quals("a_test.cc", src), set(&["Fix", "Suite.Works", "Fix.Reads", "ALPN"]));
    let facts = file_facts(src.as_bytes(), "a_test.cc");
    let reads = facts.defs.iter().find(|d| d.name == "Reads").expect("the test");
    assert_eq!(reads.owner.as_deref(), Some("Fix"));
    assert_eq!(reads.kind, DefKind::Method);
}

#[test]
fn c_a_macro_alone_on_a_line_is_no_type() {
    // `EXPECT_ABORT_BEGIN` / `TEST_ASSERT(0);` is two statements: the macro is
    // blanked, the assert is a call and the functions after are found
    let src = "void testA(void)\n{\n    EXPECT_ABORT_BEGIN\n    TEST_ASSERT(0);\n    VERIFY_FAILS_END\n}\nvoid testB(void)\n{\n}\n";
    assert_eq!(quals("t.c", src), set(&["testA", "testB"]));
    let facts = file_facts(src.as_bytes(), "t.c");
    assert!(facts.refs.iter().any(|r| r.path.last().is_some_and(|n| n == "TEST_ASSERT")));
}

#[test]
fn ts_a_namespace_constant_is_a_definition() {
    let src = "export namespace util {\n  export const keys: number = 1;\n  let count = 0;\n  export const f = () => 1;\n}\n";
    let facts = file_facts(src.as_bytes(), "util.ts");
    let extra: BTreeSet<(&str, DefKind)> =
        facts.defs.iter().filter(|d| d.extra).map(|d| (d.qualname.as_str(), d.kind)).collect();
    assert_eq!(extra, [("util.keys", DefKind::Constant), ("util.count", DefKind::Variable)].into());
}
