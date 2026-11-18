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
