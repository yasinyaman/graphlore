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
