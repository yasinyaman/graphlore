//! `update=True`: the facts cache. An update parses only what changed and
//! writes the graph a full build writes.

use std::fs;
use std::path::{Path, PathBuf};

use graphlore_engine::{build_graph, BuildOptions};
use serde_json::Value;

fn project(name: &str) -> PathBuf {
    let root = std::env::temp_dir().join(format!("graphlore-incr-{name}-{}", std::process::id()));
    let _ = fs::remove_dir_all(&root);
    let files = [
        ("pkg/__init__.py", "from .core import Engine\n"),
        ("pkg/core.py", "from .util import double\n\nclass Engine:\n    def run(self, x):\n        return double(x)\n"),
        ("pkg/util.py", "def double(x):\n    return 2 * x\n\ndef triple(x):\n    return 3 * x\n"),
        ("app.py", "from pkg import Engine\n\ndef main():\n    Engine().run(1)\n"),
        ("README.md", "# Demo\n\n## Usage\n"),
    ];
    for (rel, text) in files {
        let p = root.join(rel);
        fs::create_dir_all(p.parent().unwrap()).unwrap();
        fs::write(p, text).unwrap();
    }
    root
}

fn opts(root: &Path, incremental: bool) -> BuildOptions {
    let mut o = BuildOptions::new(root);
    o.out = Some(root.join("graphify-out/graph.json"));
    o.incremental = incremental;
    o
}

/// The graph without what differs between any two builds.
fn content(mut g: Value) -> Value {
    g.as_object_mut().unwrap().remove("built_at_commit");
    g
}

/// Make the cache's timestamps trustworthy: files written well before it.
fn age(root: &Path) {
    let old = std::time::SystemTime::now() - std::time::Duration::from_secs(60);
    for entry in walk(root) {
        let f = fs::File::options().write(true).open(&entry).unwrap();
        f.set_modified(old).unwrap();
    }
}

fn walk(dir: &Path) -> Vec<PathBuf> {
    let mut out = Vec::new();
    for e in fs::read_dir(dir).unwrap() {
        let p = e.unwrap().path();
        if p.ends_with("graphify-out") {
            continue;
        }
        if p.is_dir() {
            out.extend(walk(&p));
        } else {
            out.push(p);
        }
    }
    out
}

#[test]
fn an_update_parses_what_changed_and_writes_the_same_graph() {
    let root = project("same");
    age(&root);
    let (_, first) = build_graph(&opts(&root, false)).unwrap();
    assert!(first.incremental.cache_written);
    assert_eq!(first.incremental.parsed, 5);

    // nothing changed: everything comes from the cache
    let (_, again) = build_graph(&opts(&root, true)).unwrap();
    assert!(again.incremental.cache);
    assert_eq!((again.incremental.reused, again.incremental.parsed), (5, 0));

    // one file changed, one deleted, one added
    let graph_path = root.join("graphify-out/graph.json");
    let before = fs::read(&graph_path).unwrap();
    fs::write(root.join("pkg/util.py"), "def double(x):\n    return x + x\n").unwrap();
    fs::remove_file(root.join("README.md")).unwrap();
    fs::write(root.join("pkg/extra.py"), "from .util import double\n\nX = double(2)\n").unwrap();
    let (updated, report) = build_graph(&opts(&root, true)).unwrap();
    assert_eq!(report.incremental.parsed, 2, "{:?}", report.incremental.parsed);
    assert_eq!(report.incremental.reused, 3);
    assert_eq!(report.incremental.removed, 1);
    let ids: Vec<&str> =
        updated["nodes"].as_array().unwrap().iter().map(|n| n["id"].as_str().unwrap()).collect();
    assert!(!ids.contains(&"pkg_util_triple") && !ids.iter().any(|i| i.starts_with("readme")));

    // the same graph as a full build of the same tree from the same previous graph
    // (whose communities both start from)
    fs::write(&graph_path, before).unwrap();
    let (full, _) = build_graph(&opts(&root, false)).unwrap();
    assert_eq!(content(updated), content(full));
    fs::remove_dir_all(&root).unwrap();
}

#[test]
fn a_touched_file_is_checked_by_its_content() {
    let root = project("touch");
    age(&root);
    build_graph(&opts(&root, false)).unwrap();
    // a new timestamp, the same bytes
    let f = fs::File::options().write(true).open(root.join("app.py")).unwrap();
    f.set_modified(std::time::SystemTime::now()).unwrap();
    let (_, report) = build_graph(&opts(&root, true)).unwrap();
    assert_eq!((report.incremental.reused, report.incremental.parsed), (5, 0));
    fs::remove_dir_all(&root).unwrap();
}

#[test]
fn a_cache_that_cannot_be_used_means_a_full_parse() {
    let root = project("bad");
    build_graph(&opts(&root, false)).unwrap();
    let cache = root.join("graphify-out/cache/graphlore-native.bin");
    assert!(cache.is_file());
    fs::write(&cache, b"not a cache").unwrap();
    let (_, report) = build_graph(&opts(&root, true)).unwrap();
    assert!(!report.incremental.cache);
    assert_eq!(report.incremental.parsed, 5);
    // other options: the cache is not for this build
    let mut code_only = opts(&root, true);
    code_only.documents = false;
    let (_, report) = build_graph(&code_only).unwrap();
    assert!(!report.incremental.cache);
    // and a plain build never reads it
    let (_, report) = build_graph(&opts(&root, false)).unwrap();
    assert!(!report.incremental.cache && report.incremental.reused == 0);
    fs::remove_dir_all(&root).unwrap();
}

fn write_files(root: &Path, files: &[(&str, &str)]) {
    for (rel, text) in files {
        let p = root.join(rel);
        fs::create_dir_all(p.parent().unwrap()).unwrap();
        fs::write(p, text).unwrap();
    }
}

/// An update and a full build of the same tree, from the same previous graph.
fn update_and_full(root: &Path) -> (Value, graphlore_engine::BuildReport, Value) {
    let graph_path = root.join("graphify-out/graph.json");
    let before = fs::read(&graph_path).unwrap();
    let (updated, report) = build_graph(&opts(root, true)).unwrap();
    let after = fs::read(&graph_path).unwrap();
    fs::write(&graph_path, before).unwrap();
    let (full, _) = build_graph(&opts(root, false)).unwrap();
    fs::write(&graph_path, after).unwrap();
    (content(updated), report, content(full))
}

#[test]
fn a_file_is_resolved_again_only_when_what_it_read_changed() {
    let root = std::env::temp_dir().join(format!("graphlore-incr-reuse-{}", std::process::id()));
    let _ = fs::remove_dir_all(&root);
    write_files(
        &root,
        &[
            ("pkg/__init__.py", "from .core import Engine\n"),
            ("pkg/core.py", "from .util import double\n\nclass Engine:\n    def run(self, x):\n        return double(x)\n"),
            ("pkg/util.py", "def double(x):\n    return 2 * x\n"),
            ("other.py", "def alone():\n    return 1\n"),
        ],
    );
    age(&root);
    let (_, first) = build_graph(&opts(&root, false)).unwrap();
    assert!(first.incremental.resolve_cache_written);
    // nothing changed: every file's resolution is the last build's
    let (_, again) = build_graph(&opts(&root, true)).unwrap();
    assert_eq!(again.incremental.resolve_reused, 4);
    // a number changed in place: the facts are as they were (a line or a
    // byte more would move what is after it), so is every file's resolution
    fs::write(root.join("pkg/util.py"), "def double(x):\n    return 3 * x\n").unwrap();
    let (updated, report, full) = update_and_full(&root);
    assert_eq!(report.incremental.resolve_reused, 4);
    assert_eq!(updated, full);
    // what `double` calls changed: the files that read util.py resolve again
    // (util.py, and core.py, which calls `double`), other.py does not
    fs::write(
        root.join("pkg/util.py"),
        "def double(x):\n    return twice(x)\n\ndef twice(x):\n    return 2 * x\n",
    )
    .unwrap();
    let (updated, report, full) = update_and_full(&root);
    assert!(report.incremental.resolve_reused >= 1, "reused {}", report.incremental.resolve_reused);
    assert!(report.incremental.resolve_reused < 4, "reused {}", report.incremental.resolve_reused);
    assert_eq!(updated, full);
    fs::remove_dir_all(&root).unwrap();
}

#[test]
fn a_definition_appearing_elsewhere_resolves_the_file_that_asked_for_it_again() {
    let root = std::env::temp_dir().join(format!("graphlore-incr-key-{}", std::process::id()));
    let _ = fs::remove_dir_all(&root);
    write_files(
        &root,
        &[
            ("go.mod", "module example.com/app\n\ngo 1.22\n"),
            ("app/a.go", "package app\n\nfunc Run() int {\n\treturn Helper()\n}\n"),
            ("app/b.go", "package app\n\nfunc Other() int {\n\treturn 1\n}\n"),
        ],
    );
    age(&root);
    build_graph(&opts(&root, false)).unwrap();
    // `Helper` appears in b.go: a.go never read b.go, but it asked for the name
    fs::write(
        root.join("app/b.go"),
        "package app\n\nfunc Other() int {\n\treturn 1\n}\n\nfunc Helper() int {\n\treturn 2\n}\n",
    )
    .unwrap();
    let (updated, report, full) = update_and_full(&root);
    assert_eq!(
        report.incremental.resolve_reused, 0,
        "reused {}",
        report.incremental.resolve_reused
    );
    assert_eq!(updated, full);
    let calls = updated["links"]
        .as_array()
        .unwrap()
        .iter()
        .any(|e| e["relation"] == "calls" && e["target"].as_str().unwrap().ends_with("helper"));
    assert!(calls, "a.go's call of Helper resolves after the update");
    fs::remove_dir_all(&root).unwrap();
}

#[test]
fn an_unchanged_graph_keeps_its_communities() {
    let root = project("kept");
    age(&root);
    let (first, report) = build_graph(&opts(&root, false)).unwrap();
    assert_ne!(report.communities.settle_end, "kept");
    // the same tree again, as an update and as a full build: the communities
    // detection would give from the same input are the ones there are
    let (again, report) = build_graph(&opts(&root, true)).unwrap();
    assert_eq!(report.communities.settle_end, "kept");
    assert_eq!(content(again), content(first.clone()));
    let (full, report) = build_graph(&opts(&root, false)).unwrap();
    assert_eq!(report.communities.settle_end, "kept");
    assert_eq!(content(full), content(first));
    // a changed file is a new input: detection runs
    fs::write(
        root.join("pkg/util.py"),
        "def double(x):\n    return twice(x)\n\ndef twice(x):\n    return x\n",
    )
    .unwrap();
    let (_, report) = build_graph(&opts(&root, true)).unwrap();
    assert_ne!(report.communities.settle_end, "kept");
    fs::remove_dir_all(&root).unwrap();
}
