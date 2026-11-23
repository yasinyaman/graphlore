//! Documents: their headings, the names of code they write, and the
//! `documents` edges to the one definition such a name means.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use graphlore_engine::{build_graph, BuildOptions};
use serde_json::Value;

fn project(name: &str, files: &[(&str, &str)]) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("graphlore-docs-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    for (rel, text) in files {
        let path = dir.join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }
    dir
}

/// `(source, target, confidence, context)` of every `documents` edge.
fn documents(dir: &Path) -> BTreeSet<(String, String, String, String)> {
    let (g, report) = build_graph(&BuildOptions::new(dir)).unwrap();
    let out: BTreeSet<_> = g["links"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|e| e["relation"] == "documents")
        .map(|e| {
            let s = |k: &str| e[k].as_str().unwrap_or("").to_string();
            (s("source"), s("target"), s("confidence"), s("context"))
        })
        .collect();
    assert_eq!(report.doc_links, out.len());
    let _ = std::fs::remove_dir_all(dir);
    out
}

fn edge(s: &str, t: &str, confidence: &str, context: &str) -> (String, String, String, String) {
    (s.into(), t.into(), confidence.into(), context.into())
}

const CLIENT: &str = "class Client:\n    def send(self, request):\n        return request\n\n\
                      def send_request(client):\n    return client.send(None)\n";

#[test]
fn markdown_code_spans_name_the_one_definition_they_mean() {
    let dir = project(
        "md",
        &[
            ("pkg/_client.py", CLIENT),
            // a namesake in test code is no candidate
            ("tests/test_client.py", "def send_request():\n    pass\n"),
            (
                "README.md",
                "# Use\n\nMake a `pkg.Client` and call `Client.send()`.\n\n\
                 ## Helpers\n\n`send_request` sends; `client` is a plain word.\n\n\
                 ```python\nClient.send()  # in a code block: not a mention\n```\n",
            ),
        ],
    );
    assert_eq!(
        documents(&dir),
        BTreeSet::from([
            edge("readme_use", "pkg_client_client", "INFERRED", "literal"),
            edge("readme_use", "pkg_client_client_send", "INFERRED", "literal"),
            edge("readme_helpers", "pkg_client_send_request", "INFERRED", "literal"),
        ])
    );
}

#[test]
fn a_name_two_definitions_answer_to_is_no_edge() {
    let dir = project(
        "ambiguous",
        &[
            ("a.py", "def parse_config():\n    pass\n"),
            ("b.py", "def parse_config():\n    pass\n"),
            ("README.md", "Call `parse_config()` or `a.parse_config()`.\n"),
            // a package is no definition, whatever it holds
            ("lib/auth/context.py", "def auth(request):\n    pass\n"),
            // a bare literal is no name of a variable: `max_retries` is a word here
            ("lib/conf.py", "max_retries = 3\n"),
            (
                "docs.md",
                "Add `lib.auth` to your apps; it calls `lib.auth.context.auth()`.\n\
                 Pass `max_retries`, or set `conf.max_retries`.\n",
            ),
        ],
    );
    assert_eq!(
        documents(&dir),
        BTreeSet::from([
            edge("readme", "a_parse_config", "INFERRED", "literal"),
            edge("docs", "lib_auth_context_auth", "INFERRED", "literal"),
            edge("docs", "lib_conf_max_retries", "INFERRED", "literal"),
        ])
    );
}

#[test]
fn sphinx_txt_sources_roles_and_directives_under_their_module() {
    let dir = project(
        "sphinx",
        &[
            ("pkg/_client.py", CLIENT),
            ("docs/conf.py", "project = 'p'\nsource_suffix = '.txt'\n"),
            ("docs/requirements.txt", "sphinx\n"),
            (
                "docs/ref.txt",
                "=========\nReference\n=========\n\n\
                 .. currentmodule:: pkg\n\n\
                 .. class:: Client()\n\n    A client.\n\n    .. method:: send(request)\n\n\
                 Calls\n=====\n\nSee :func:`~pkg.send_request` and ``Client.send``.\n\n\
                 Example::\n\n    send_request(Client())\n\n\
                 .. code-block:: python\n\n    Client.send\n",
            ),
        ],
    );
    assert_eq!(
        documents(&dir),
        BTreeSet::from([
            edge("docs_ref_reference", "pkg_client_client", "EXTRACTED", "directive"),
            edge("docs_ref_reference", "pkg_client_client_send", "EXTRACTED", "directive"),
            edge("docs_ref_calls", "pkg_client_send_request", "INFERRED", "role"),
            edge("docs_ref_calls", "pkg_client_client_send", "INFERRED", "literal"),
        ])
    );
}

#[test]
fn a_txt_file_outside_a_sphinx_project_is_no_document() {
    let dir =
        project("plain", &[("notes.txt", "Title\n=====\n"), ("a.py", "def f():\n    pass\n")]);
    let (g, report) = build_graph(&BuildOptions::new(&dir)).unwrap();
    let _ = std::fs::remove_dir_all(&dir);
    assert_eq!(report.documents, 0);
    assert!(g["nodes"].as_array().unwrap().iter().all(|n: &Value| n["source_file"] != "notes.txt"));
}
