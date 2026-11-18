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
    // the graph is the same whether the rows were asked for or not
    assert_eq!(plain["nodes"], asked["nodes"]);
    assert_eq!(plain["links"], asked["links"]);
}
