//! A file git tracks is read whatever an ignore rule says of its name.

use std::path::Path;
use std::process::Command;

use graphlore_engine::{build_graph, BuildOptions};

fn git(dir: &Path, args: &[&str]) -> bool {
    Command::new("git")
        .arg("-C")
        .arg(dir)
        .args(["-c", "user.name=t", "-c", "user.email=t@example.com"])
        .args(args)
        .output()
        .is_ok_and(|o| o.status.success())
}

fn files(dir: &Path) -> Vec<String> {
    let (g, _) = build_graph(&BuildOptions::new(dir)).unwrap();
    let mut out: Vec<String> = g["nodes"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|n| n["source_file"].as_str().map(str::to_string))
        .collect();
    out.sort();
    out.dedup();
    out
}

#[test]
fn a_tracked_file_an_ignore_rule_matches_is_read() {
    let dir = std::env::temp_dir().join(format!("graphlore-tracked-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(dir.join("tests/unity/test")).unwrap();
    std::fs::create_dir_all(dir.join("build")).unwrap();
    std::fs::create_dir_all(dir.join("node_modules/x")).unwrap();
    std::fs::write(dir.join(".gitignore"), "test\nbuild\n").unwrap();
    std::fs::write(dir.join("a.c"), "int a(void) {\n    return 1;\n}\n").unwrap();
    std::fs::write(dir.join("tests/unity/test/t.c"), "int t(void) {\n    return 1;\n}\n").unwrap();
    std::fs::write(dir.join("build/out.c"), "int o(void) {\n    return 1;\n}\n").unwrap();
    std::fs::write(dir.join("node_modules/x/i.js"), "function i() {}\n").unwrap();
    // without git, the ignore rules are all there is to go by
    assert_eq!(files(&dir), ["a.c"]);
    if !git(&dir, &["init", "-q"]) {
        return; // no git on this machine: nothing more to check
    }
    // tracked all the same (`git add -f`): part of the repository
    assert!(git(&dir, &["add", "-f", "a.c", "tests/unity/test/t.c", "node_modules/x/i.js"]));
    assert!(git(&dir, &["commit", "-q", "-m", "x"]));
    // `build/out.c` is ignored and untracked; `node_modules` is never walked
    assert_eq!(files(&dir), ["a.c", "tests/unity/test/t.c"]);
    // the engine's own ignore file still holds
    std::fs::write(dir.join(".graphloreignore"), "tests/\n").unwrap();
    assert_eq!(files(&dir), ["a.c"]);
    std::fs::remove_dir_all(&dir).unwrap();
}

#[test]
fn a_go_vendor_directory_is_left_out_unless_asked() {
    let dir = std::env::temp_dir().join(format!("graphlore-vendor-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(dir.join("vendor/example.com/dep")).unwrap();
    std::fs::create_dir_all(dir.join("internal/vendor")).unwrap();
    std::fs::write(dir.join("go.mod"), "module example.com/app\n\ngo 1.22\n").unwrap();
    std::fs::write(dir.join("vendor/modules.txt"), "# example.com/dep v1.0.0\nexample.com/dep\n")
        .unwrap();
    std::fs::write(
        dir.join("vendor/example.com/dep/dep.go"),
        "package dep\n\nfunc Do() int {\n\treturn 1\n}\n",
    )
    .unwrap();
    std::fs::write(
        dir.join("main.go"),
        "package main\n\nimport \"example.com/dep\"\n\nfunc main() {\n\tdep.Do()\n}\n",
    )
    .unwrap();
    // a directory named vendor that is no `go mod vendor` output is the project's
    std::fs::write(dir.join("internal/vendor/v.go"), "package vendor\n\nfunc V() {}\n").unwrap();
    let tracked = git(&dir, &["init", "-q"])
        && git(&dir, &["add", "-A"])
        && git(&dir, &["commit", "-q", "-m", "x"]);
    let (g, report) = build_graph(&BuildOptions::new(&dir)).unwrap();
    let mut read: Vec<&str> =
        g["nodes"].as_array().unwrap().iter().filter_map(|n| n["source_file"].as_str()).collect();
    read.retain(|f| !f.is_empty());
    read.sort();
    read.dedup();
    assert_eq!(read, ["internal/vendor/v.go", "main.go"], "tracked: {tracked}");
    assert_eq!(report.vendored_skipped, ["vendor"]);
    assert_eq!(report.vendored_skipped_count, 1);
    // the import of a vendored package is an external module's
    let ext: Vec<&str> = g["nodes"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|n| n["type"] == "external")
        .filter_map(|n| n["label"].as_str())
        .collect();
    assert!(ext.iter().any(|l| l.contains("example.com/dep")), "{ext:?}");
    let mut opts = BuildOptions::new(&dir);
    opts.read_vendor = true;
    let (g, report) = build_graph(&opts).unwrap();
    assert!(g["nodes"]
        .as_array()
        .unwrap()
        .iter()
        .any(|n| n["source_file"] == "vendor/example.com/dep/dep.go"));
    assert!(report.vendored_skipped.is_empty());
    std::fs::remove_dir_all(&dir).unwrap();
}
