//! A build of graphlore's polyglot fixture (tests/fixtures/polyglot, whose
//! graph.json graphify 0.9 built): the same ids, labels and containment edges
//! for every definition both engines see.

use std::collections::BTreeSet;
use std::path::PathBuf;

use graphlore_engine::{build_graph, BuildOptions};
use serde_json::Value;

fn fixture() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../tests/fixtures/polyglot")
}

fn edges(graph: &Value, relations: &[&str]) -> BTreeSet<(String, String, String)> {
    graph["links"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|e| relations.contains(&e["relation"].as_str().unwrap()))
        .map(|e| {
            (
                e["source"].as_str().unwrap().to_string(),
                e["relation"].as_str().unwrap().to_string(),
                e["target"].as_str().unwrap().to_string(),
            )
        })
        .collect()
}

fn graphify() -> Value {
    serde_json::from_str(&std::fs::read_to_string(fixture().join("graph.json")).unwrap()).unwrap()
}

#[test]
fn ids_labels_and_containment_match_graphify() {
    let (native, report) = build_graph(&BuildOptions::new(fixture())).unwrap();
    let theirs = graphify();
    let containment = ["contains", "method", "case_of"];
    // variables, constants and class-level fields are the native engine's own
    // nodes (graphify has none): left out of the comparison
    let state: BTreeSet<String> = native["nodes"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|n| matches!(n["kind"].as_str(), Some("variable" | "constant" | "property")))
        .map(|n| n["id"].as_str().unwrap().to_string())
        .collect();
    let ours: BTreeSet<_> =
        edges(&native, &containment).into_iter().filter(|(_, _, t)| !state.contains(t)).collect();
    // Two known differences. graphify's PHP closure node (`{closure#1}()`) is a
    // definition the native engine does not name. And graphify hangs Ruby's
    // `Line = Struct.new … do` off the file although it sits inside
    // `module Shop`; the native engine puts it in the module, as it does `Cart`.
    let mut expected: BTreeSet<_> = edges(&theirs, &containment)
        .into_iter()
        .filter(|(_, _, t)| t != "php_src_stack_closure_1")
        .collect();
    let line = (
        "lib_shop_cart".to_string(),
        "contains".to_string(),
        "lib_shop_cart_shop_line".to_string(),
    );
    assert!(expected.remove(&line));
    expected.insert((
        "lib_shop_cart_shop".into(),
        "contains".into(),
        "lib_shop_cart_shop_line".into(),
    ));
    let missing: Vec<_> = expected.difference(&ours).collect();
    let extra: Vec<_> = ours.difference(&expected).collect();
    assert!(missing.is_empty() && extra.is_empty(), "missing {missing:#?}\nextra {extra:#?}");

    let label = |g: &Value, id: &str| {
        g["nodes"].as_array().unwrap().iter().find(|n| n["id"] == id).map(|n| n["label"].clone())
    };
    for n in theirs["nodes"].as_array().unwrap() {
        let id = n["id"].as_str().unwrap();
        if let Some(ours) = label(&native, id) {
            if id != "lib_shop_cart_shop_cart" && id != "lib_shop_cart_shop_line" {
                assert_eq!(ours, n["label"], "label of {id}");
            }
        }
    }
    assert_eq!(report.languages.len(), 4 + 1, "python, rust, ruby, php, typescript");
    assert_eq!(report.documents, 1);
    assert!(native.get("built_at_commit").is_none());
    assert_eq!(native["graph"]["engine"], "graphlore");
}

#[test]
fn nodes_carry_their_span_and_build_is_deterministic() {
    let mut opts = BuildOptions::new(fixture());
    opts.commit = Some("abc123".into());
    let (a, _) = build_graph(&opts).unwrap();
    opts.threads = Some(1);
    let (b, _) = build_graph(&opts).unwrap();
    assert_eq!(a, b, "same graph whatever the thread count");
    assert_eq!(a["built_at_commit"], "abc123");
    let acquire = a["nodes"]
        .as_array()
        .unwrap()
        .iter()
        .find(|n| n["id"] == "core_src_lib_fixed_acquire")
        .unwrap();
    assert_eq!(acquire["source_location"], "L13-L15");
    assert_eq!(acquire["qualname"], "Fixed.acquire");
    assert_eq!(acquire["region_start"], 13);
    assert_eq!(acquire["end_line"], 15);
    let runs =
        a["nodes"].as_array().unwrap().iter().find(|n| n["id"] == "core_src_lib_runs").unwrap();
    assert_eq!(runs["test"], true);
}

#[test]
fn writes_atomically_and_skips_the_out_dir() {
    let tmp = std::env::temp_dir().join(format!("graphlore-engine-test-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&tmp);
    std::fs::create_dir_all(tmp.join("graphlore-out")).unwrap();
    std::fs::write(tmp.join("a.py"), "def f():\n    pass\n").unwrap();
    std::fs::write(tmp.join("graphlore-out/stale.py"), "def g():\n    pass\n").unwrap();
    // the output directory graphify and graphlore before 0.9.0 wrote is left out too
    std::fs::create_dir_all(tmp.join("graphify-out")).unwrap();
    std::fs::write(tmp.join("graphify-out/older.py"), "def k():\n    pass\n").unwrap();
    std::fs::create_dir_all(tmp.join("node_modules/x")).unwrap();
    std::fs::write(tmp.join("node_modules/x/i.js"), "function h() {}\n").unwrap();
    std::fs::write(tmp.join("notes.lua"), "-- not read\n").unwrap();
    let mut opts = BuildOptions::new(&tmp);
    opts.out = Some(tmp.join("graphlore-out/graph.json"));
    let report = graphlore_engine::build(&opts).unwrap();
    assert_eq!(report.files, 1);
    assert_eq!(report.not_read.get(".lua"), Some(&1));
    let written: Value = serde_json::from_str(
        &std::fs::read_to_string(tmp.join("graphlore-out/graph.json")).unwrap(),
    )
    .unwrap();
    let ids: Vec<&str> =
        written["nodes"].as_array().unwrap().iter().map(|n| n["id"].as_str().unwrap()).collect();
    assert_eq!(ids, vec!["a", "a_f"]);
    let leftovers: Vec<_> = std::fs::read_dir(tmp.join("graphlore-out"))
        .unwrap()
        .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
        .filter(|n| n.contains(".tmp-"))
        .collect();
    assert!(leftovers.is_empty());
    std::fs::remove_dir_all(&tmp).unwrap();
}

#[test]
fn reads_names_saved_under_graphify_s_file_name() {
    let tmp = std::env::temp_dir().join(format!("graphlore-engine-labels-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&tmp);
    std::fs::create_dir_all(tmp.join("graphlore-out")).unwrap();
    std::fs::write(tmp.join("a.py"), "def f():\n    pass\n").unwrap();
    // a name saved for a community the graph does not have: dropped, and the
    // names that are left written under the new file name
    std::fs::write(tmp.join("graphlore-out/.graphify_labels.json"), r#"{"99": "Gone"}"#).unwrap();
    let mut opts = BuildOptions::new(&tmp);
    opts.out = Some(tmp.join("graphlore-out/graph.json"));
    let report = graphlore_engine::build(&opts).unwrap();
    assert_eq!(report.communities.renamed_labels, 1);
    let kept: Value = serde_json::from_str(
        &std::fs::read_to_string(tmp.join("graphlore-out/.graphlore_labels.json")).unwrap(),
    )
    .unwrap();
    assert_eq!(kept, serde_json::json!({}));
    std::fs::remove_dir_all(&tmp).unwrap();
}
