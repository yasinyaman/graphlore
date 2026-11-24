//! A workspace: a folder of repositories built as one graph. Each repository
//! is asked for its own tracked files and commit, an import means a module of
//! the importer's own repository first, and a URL to a host the workspace
//! file gives a repository reaches that repository's routes.

use std::path::{Path, PathBuf};
use std::process::Command;

use graphlore_engine::{build_graph, BuildOptions};
use serde_json::Value;

fn git(dir: &Path, args: &[&str]) -> bool {
    Command::new("git")
        .arg("-C")
        .arg(dir)
        .args(["-c", "user.name=t", "-c", "user.email=t@example.com"])
        .args(args)
        .output()
        .is_ok_and(|o| o.status.success())
}

/// A folder holding the files, with each of `repos` a directory that holds
/// a `.git` (a real repository with one commit when git is there).
fn workspace(name: &str, repos: &[&str], files: &[(&str, &str)]) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("graphlore-ws-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    for (rel, src) in files {
        let path = dir.join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, src).unwrap();
    }
    for repo in repos {
        let at = dir.join(repo);
        std::fs::create_dir_all(&at).unwrap();
        if !(git(&at, &["init", "-q"])
            && git(&at, &["add", "-A"])
            && git(&at, &["commit", "-q", "-m", "init"]))
        {
            std::fs::create_dir_all(at.join(".git")).unwrap();
        }
    }
    dir
}

fn build(dir: &Path) -> Value {
    let (g, _) = build_graph(&BuildOptions::new(dir)).unwrap();
    g
}

/// `(source file, relation, target id)` of every edge of `relation`.
fn links(g: &Value, relation: &str) -> Vec<(String, String)> {
    let file = |id: &str| {
        g["nodes"]
            .as_array()
            .unwrap()
            .iter()
            .find(|n| n["id"] == id)
            .and_then(|n| n["source_file"].as_str())
            .unwrap_or("")
            .to_string()
    };
    g["links"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|e| e["relation"] == relation)
        .map(|e| {
            let (s, t) = (e["source"].as_str().unwrap(), e["target"].as_str().unwrap());
            (format!("{} {s}", file(s)), format!("{} {t}", file(t)))
        })
        .collect()
}

#[test]
fn a_workspace_header_names_each_repository() {
    let dir = workspace(
        "header",
        &["svc-a", "libs/common"],
        &[
            ("svc-a/a.py", "def a():\n    return 1\n"),
            ("libs/common/c.py", "def c():\n    return 1\n"),
        ],
    );
    let g = build(&dir);
    let repos = g["graph"]["repos"].as_object().expect("graph.repos");
    assert_eq!(repos.keys().collect::<Vec<_>>(), ["libs/common", "svc-a"]);
    if git(&dir.join("svc-a"), &["rev-parse", "HEAD"]) {
        assert_eq!(repos["svc-a"]["commit"].as_str().map(str::len), Some(40));
    }
    // a single repository has no such header
    let one = workspace("single", &[], &[("a.py", "def a():\n    return 1\n")]);
    std::fs::create_dir_all(one.join(".git")).unwrap();
    std::fs::create_dir_all(one.join("vendor/sub/.git")).unwrap();
    assert!(build(&one)["graph"].get("repos").is_none());
    std::fs::remove_dir_all(&dir).unwrap();
    std::fs::remove_dir_all(&one).unwrap();
}

#[test]
fn a_tracked_file_of_a_repository_the_folder_ignores_is_read() {
    let dir = workspace(
        "tracked",
        &["svc-a"],
        &[("svc-a/build/gen.py", "def gen():\n    return 1\n"), ("svc-a/.gitignore", "")],
    );
    if !git(&dir.join("svc-a"), &["rev-parse", "HEAD"]) {
        return; // no git: nothing is tracked
    }
    std::fs::write(dir.join("svc-a/.gitignore"), "build\n").unwrap();
    let g = build(&dir);
    let read =
        g["nodes"].as_array().unwrap().iter().any(|n| n["source_file"] == "svc-a/build/gen.py");
    assert!(read, "svc-a/build/gen.py is tracked by svc-a");
    std::fs::remove_dir_all(&dir).unwrap();
}

#[test]
fn a_python_import_means_the_importer_s_own_repository() {
    let dir = workspace(
        "py",
        &["svc-a", "svc-b"],
        &[
            ("svc-a/app/__init__.py", ""),
            ("svc-a/app/models.py", "class User:\n    pass\n"),
            (
                "svc-a/app/main.py",
                "from app.models import User\nimport utils\n\n\ndef go():\n    return User()\n",
            ),
            ("svc-b/app/__init__.py", ""),
            ("svc-b/app/models.py", "class Order:\n    pass\n"),
            ("svc-b/src/utils.py", "def helper():\n    return 1\n"),
        ],
    );
    let g = build(&dir);
    let imports = links(&g, "imports_from");
    assert!(
        imports
            .iter()
            .any(|(s, t)| s.starts_with("svc-a/app/main.py ")
                && t.starts_with("svc-a/app/models.py ")),
        "{imports:#?}"
    );
    assert!(
        !imports.iter().any(|(s, t)| s.starts_with("svc-a/") && t.starts_with("svc-b/")),
        "no import crosses to svc-b: {imports:#?}"
    );
    let calls = links(&g, "calls");
    assert!(calls.iter().any(|(s, t)| s.starts_with("svc-a/app/main.py ") && t.ends_with("_user")));
    std::fs::remove_dir_all(&dir).unwrap();
}

#[test]
fn a_rust_crate_name_means_the_importer_s_own_repository() {
    let dir = workspace(
        "rs",
        &["svc-a", "svc-b"],
        &[
            ("svc-a/shared/Cargo.toml", "[package]\nname = \"shared\"\n"),
            ("svc-a/shared/src/lib.rs", "pub fn run() {}\n"),
            ("svc-a/app/Cargo.toml", "[package]\nname = \"app\"\n"),
            ("svc-a/app/src/main.rs", "use shared::run;\n\nfn main() {\n    run();\n}\n"),
            ("svc-b/shared/Cargo.toml", "[package]\nname = \"shared\"\n"),
            ("svc-b/shared/src/lib.rs", "pub fn run() {}\n"),
        ],
    );
    let g = build(&dir);
    let calls = links(&g, "calls");
    assert!(
        calls.iter().any(|(s, t)| s.starts_with("svc-a/app/src/main.rs ")
            && t.starts_with("svc-a/shared/src/lib.rs ")),
        "{calls:#?}"
    );
    assert!(!calls.iter().any(|(_, t)| t.starts_with("svc-b/")), "{calls:#?}");
    std::fs::remove_dir_all(&dir).unwrap();
}

#[test]
fn a_url_to_a_repository_s_host_reaches_its_routes() {
    let files = [
        (
            "svc-a/client.py",
            "import httpx\n\n\ndef list_users():\n    return httpx.get(\"https://svc-b.internal/users\")\n\n\ndef elsewhere():\n    return httpx.get(\"https://example.com/users\")\n",
        ),
        (
            "svc-b/api.py",
            "from fastapi import FastAPI\n\napp = FastAPI()\n\n\n@app.get(\"/users\")\ndef users():\n    return []\n",
        ),
        (
            "svc-c/api.py",
            "from fastapi import FastAPI\n\napp = FastAPI()\n\n\n@app.get(\"/users\")\ndef users():\n    return []\n",
        ),
        (
            ".graphlore-workspace.json",
            "{\"repos\": {\"svc-a\": {}, \"svc-b\": {\"hosts\": [\"svc-b.internal\"]}, \"svc-c\": {}}}",
        ),
    ];
    let dir = workspace("http", &[], &files);
    let g = build(&dir);
    let calls: Vec<(String, String, String)> = g["links"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|e| e["relation"] == "http_calls")
        .map(|e| {
            (
                e["source"].as_str().unwrap().to_string(),
                e["target"].as_str().unwrap().to_string(),
                e["confidence"].as_str().unwrap().to_string(),
            )
        })
        .collect();
    assert_eq!(
        calls,
        [(
            "svc_a_client_list_users".to_string(),
            "route_svc_b_get_users".to_string(),
            "EXTRACTED".to_string()
        )],
        "only the request to svc-b's host, to svc-b's route"
    );
    // the route ids lead with the repository, so svc-c's route renames none
    let routes: Vec<&str> = g["nodes"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|n| n["id"].as_str().filter(|id| id.starts_with("route_")))
        .collect();
    assert!(routes.contains(&"route_svc_c_get_users"), "{routes:?}");
    std::fs::remove_dir_all(&dir).unwrap();
}

/// One file `$repo/$rel` of each repository, and an importer in the second:
/// the second is the one a first-match rule would miss.
fn importer_s_own(name: &str, files: &[(&str, &str)], importer: &str, relation: &str) {
    let dir = workspace(name, &["svc-a", "svc-b"], files);
    let g = build(&dir);
    let edges = links(&g, relation);
    assert!(
        edges.iter().any(|(s, t)| s.starts_with(importer) && t.starts_with("svc-b/")),
        "{edges:#?}"
    );
    assert!(
        !edges.iter().any(|(s, t)| s.starts_with("svc-b/") && t.starts_with("svc-a/")),
        "nothing of svc-b reaches svc-a: {edges:#?}"
    );
    std::fs::remove_dir_all(&dir).unwrap();
}

#[test]
fn a_js_package_name_means_the_importer_s_own_repository() {
    importer_s_own(
        "js",
        &[
            ("svc-a/shared/package.json", "{\"name\": \"shared\", \"main\": \"index.js\"}\n"),
            ("svc-a/shared/index.js", "export function run() { return 1; }\n"),
            ("svc-b/shared/package.json", "{\"name\": \"shared\", \"main\": \"index.js\"}\n"),
            ("svc-b/shared/index.js", "export function run() { return 2; }\n"),
            (
                "svc-b/app/main.js",
                "import { run } from 'shared';\n\nexport function go() {\n  return run();\n}\n",
            ),
        ],
        "svc-b/app/main.js ",
        "calls",
    );
}

#[test]
fn a_go_module_path_means_the_importer_s_own_repository() {
    importer_s_own(
        "go",
        &[
            ("svc-a/go.mod", "module example.com/shop\n\ngo 1.21\n"),
            ("svc-a/util/util.go", "package util\n\nfunc Run() int { return 1 }\n"),
            ("svc-b/go.mod", "module example.com/shop\n\ngo 1.21\n"),
            ("svc-b/util/util.go", "package util\n\nfunc Run() int { return 2 }\n"),
            (
                "svc-b/main.go",
                "package main\n\nimport \"example.com/shop/util\"\n\nfunc main() {\n\tutil.Run()\n}\n",
            ),
        ],
        "svc-b/main.go ",
        "calls",
    );
}

#[test]
fn a_ruby_require_means_the_importer_s_own_repository() {
    importer_s_own(
        "rb",
        &[
            ("svc-a/lib/models/user.rb", "class User\n  def name\n    1\n  end\nend\n"),
            ("svc-b/lib/models/user.rb", "class User\n  def name\n    2\n  end\nend\n"),
            ("svc-b/lib/app.rb", "require 'models/user'\n\ndef go\n  User.new.name\nend\n"),
        ],
        "svc-b/lib/app.rb ",
        "calls",
    );
}

#[test]
fn a_php_namespace_means_the_importer_s_own_repository() {
    importer_s_own(
        "php",
        &[
            (
                "svc-a/src/Models/User.php",
                "<?php\nnamespace App\\Models;\n\nclass User {\n    public function name() { return 1; }\n}\n",
            ),
            (
                "svc-b/src/Models/User.php",
                "<?php\nnamespace App\\Models;\n\nclass User {\n    public function name() { return 2; }\n}\n",
            ),
            (
                "svc-b/src/App.php",
                "<?php\nnamespace App;\n\nuse App\\Models\\User;\n\nfunction go() {\n    return (new User())->name();\n}\n",
            ),
        ],
        "svc-b/src/App.php ",
        "calls",
    );
}
