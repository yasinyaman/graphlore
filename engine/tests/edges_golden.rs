//! Edge goldens: a two- or three-file project per language, built for real,
//! and the import / call / base edges its source proves.

use std::collections::BTreeSet;
use std::path::PathBuf;

use graphlore_engine::{build_graph, BuildOptions};
use serde_json::Value;

fn project(name: &str, files: &[(&str, &str)]) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("graphlore-edges-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    for (rel, src) in files {
        let path = dir.join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, src).unwrap();
    }
    dir
}

/// `(source label, relation, target label)` of every non-containment edge.
fn edges(name: &str, files: &[(&str, &str)]) -> BTreeSet<(String, String, String)> {
    let dir = project(name, files);
    let (g, _) = build_graph(&BuildOptions::new(&dir)).unwrap();
    std::fs::remove_dir_all(&dir).unwrap();
    let label = |id: &str| {
        g["nodes"]
            .as_array()
            .unwrap()
            .iter()
            .find(|n| n["id"] == id)
            .map(|n| n["label"].as_str().unwrap().to_string())
            .unwrap_or_default()
    };
    g["links"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|e| {
            !matches!(e["relation"].as_str(), Some("contains" | "method" | "case_of" | "defines"))
        })
        .map(|e: &Value| {
            (
                label(e["source"].as_str().unwrap()),
                e["relation"].as_str().unwrap().to_string(),
                label(e["target"].as_str().unwrap()),
            )
        })
        .collect()
}

#[track_caller]
fn has(got: &BTreeSet<(String, String, String)>, want: &[(&str, &str, &str)]) {
    for (s, r, t) in want {
        assert!(
            got.contains(&(s.to_string(), r.to_string(), t.to_string())),
            "missing {s} -{r}-> {t} in {got:#?}"
        );
    }
}

#[test]
fn python() {
    let got = edges("py", &[
        ("pkg/__init__.py", ""),
        ("pkg/base.py", "class Base:\n    def run(self):\n        return self.step()\n\n    def step(self):\n        return 1\n"),
        ("pkg/app.py", "from pkg.base import Base\nimport json\n\n\nclass App(Base):\n    def go(self):\n        return self.run() + json.dumps(1)\n"),
    ]);
    has(
        &got,
        &[
            ("app.py", "imports_from", "base.py"),
            ("app.py", "imports", "Base"),
            ("app.py", "imports", "json"),
            ("App", "inherits", "Base"),
            (".go()", "calls", ".run()"),
            (".run()", "calls", ".step()"),
            (".go()", "calls", "json"),
        ],
    );
}

#[test]
fn typescript() {
    let got = edges("ts", &[
        ("src/dto/index.ts", "export * from './create';\n"),
        ("src/dto/create.ts", "export class CreateDto {\n  describe(): string { return 'x'; }\n}\n"),
        ("src/app.ts", "import { CreateDto } from './dto';\nimport express from 'express';\n\nexport function app(): CreateDto {\n  const d = new CreateDto();\n  d.describe();\n  return d;\n}\n"),
    ]);
    has(
        &got,
        &[
            ("app.ts", "imports_from", "index.ts"),
            ("app.ts", "imports", "CreateDto"),
            ("app.ts", "imports_from", "express"),
            ("index.ts", "re_exports", "create.ts"),
            ("app()", "calls", "CreateDto"),
            ("app()", "calls", ".describe()"),
            ("app()", "references", "CreateDto"),
        ],
    );
}

#[test]
fn go() {
    let got = edges("go", &[
        ("go.mod", "module example.com/m\n"),
        ("store/store.go", "package store\n\ntype Store struct{}\n\nfunc New() *Store { return &Store{} }\n\nfunc (s *Store) Get(k string) string { return k }\n"),
        ("main.go", "package main\n\nimport \"example.com/m/store\"\n\nfunc main() {\n\ts := store.New()\n\ts.Get(\"a\")\n}\n"),
    ]);
    has(
        &got,
        &[
            ("main.go", "imports_from", "store.go"),
            ("main()", "calls", "New()"),
            ("main()", "calls", ".Get()"),
            ("New()", "calls", "Store"),
        ],
    );
}

#[test]
fn java() {
    let got = edges("java", &[
        ("src/a/Repo.java", "package a;\npublic interface Repo { int find(); }\n"),
        ("src/a/Db.java", "package a;\npublic class Db implements Repo { public int find() { return 1; } }\n"),
        ("src/b/Svc.java", "package b;\nimport a.Repo;\nimport a.Db;\npublic class Svc {\n  private Repo repo = new Db();\n  int run() { return repo.find(); }\n}\n"),
    ]);
    has(
        &got,
        &[
            ("Svc.java", "imports", "Repo"),
            ("Db", "implements", "Repo"),
            (".run()", "calls", ".find()"),
            ("Svc", "calls", "Db"),
        ],
    );
}

#[test]
fn csharp() {
    let got = edges("cs", &[
        ("Models/Item.cs", "namespace Shop.Models;\npublic class Item { public int Price() { return 1; } }\n"),
        ("Svc.cs", "using Shop.Models;\nnamespace Shop;\npublic class Svc : IDisposable {\n  public Item Item { get; set; }\n  public int Total() { return Item.Price(); }\n  public void Dispose() {}\n}\n"),
    ]);
    has(
        &got,
        &[
            ("Svc.cs", "imports", "Item.cs"),
            ("Svc", "implements", "IDisposable"),
            (".Total()", "calls", ".Price()"),
            ("Svc", "references", "Item"),
        ],
    );
}

#[test]
fn rust() {
    let got = edges("rs", &[
        ("Cargo.toml", "[package]\nname = \"demo\"\n"),
        ("src/lib.rs", "pub mod pool;\nuse crate::pool::Pool;\n\npub fn run() -> i32 {\n    let p = Pool::new();\n    p.acquire()\n}\n"),
        ("src/pool.rs", "pub trait Acquire { fn acquire(&self) -> i32; }\npub struct Pool;\nimpl Pool {\n    pub fn new() -> Self { Pool }\n}\nimpl Acquire for Pool {\n    fn acquire(&self) -> i32 { 1 }\n}\n"),
    ]);
    has(
        &got,
        &[
            ("lib.rs", "imports_from", "Pool"),
            ("run()", "calls", ".new()"),
            ("Pool", "implements", "Acquire"),
        ],
    );
}

#[test]
fn php() {
    let got = edges("php", &[
        ("src/Base.php", "<?php\nnamespace Shop;\nabstract class Base { public function boot(): void {} }\n"),
        ("src/Cart.php", "<?php\nnamespace Shop;\nuse Psr\\Log\\LoggerInterface;\nclass Cart extends Base {\n  public function add(LoggerInterface $log): void { $this->boot(); }\n}\n"),
    ]);
    has(
        &got,
        &[
            ("Cart", "inherits", "Base"),
            (".add()", "calls", ".boot()"),
            ("Cart.php", "imports", "Psr\\Log\\LoggerInterface"),
        ],
    );
}

#[test]
fn ruby() {
    let got = edges("rb", &[
        ("lib/shop/base.rb", "module Shop\n  class Base\n    def boot\n      1\n    end\n  end\nend\n"),
        ("lib/shop/cart.rb", "require_relative 'base'\nmodule Shop\n  class Cart < Base\n    include Enumerable\n    def add\n      boot\n      Line.new\n    end\n  end\n  class Line\n  end\nend\n"),
    ]);
    has(
        &got,
        &[
            ("cart.rb", "imports", "base.rb"),
            ("Cart", "inherits", "Base"),
            ("Cart", "mixes_in", "Enumerable"),
            (".add()", "calls", ".boot()"),
            (".add()", "calls", "Line"),
        ],
    );
}

#[test]
fn swift() {
    let got = edges("swift", &[
        ("Package.swift", "let package = Package(name: \"Kit\", targets: [.target(name: \"Kit\")])\n"),
        ("Sources/Kit/Session.swift", "public protocol Retrier { func retry() }\npublic class Session: Retrier {\n  public func retry() {}\n  func start() { self.retry() }\n}\n"),
        ("Sources/Kit/Use.swift", "import Foundation\nfunc make() -> Session {\n  let s = Session()\n  s.start()\n  return s\n}\n"),
    ]);
    has(
        &got,
        &[
            ("Session", "implements", "Retrier"),
            (".start()", "calls", ".retry()"),
            ("make()", "calls", "Session"),
            ("make()", "calls", ".start()"),
            ("Use.swift", "imports", "Foundation"),
        ],
    );
}

#[test]
fn c_and_cpp() {
    let got = edges("c", &[
        ("include/util.h", "int helper(int x);\n"),
        ("src/util.c", "#include \"util.h\"\nint helper(int x) { return x; }\n"),
        ("src/main.c", "#include \"../include/util.h\"\n#include <stdio.h>\nint main(void) { return helper(1); }\n"),
        ("src/shape.cpp", "class Shape { public: virtual int area(); };\nclass Square : public Shape { public: int area() override { return 1; } int twice() { return area() * 2; } };\n"),
    ]);
    has(
        &got,
        &[
            ("main.c", "imports", "util.h"),
            ("main.c", "imports", "stdio.h"),
            ("main()", "calls", "helper()"),
            ("Square", "inherits", "Shape"),
            (".twice()", "calls", ".area()"),
        ],
    );
    // the call lands on the definition, not the prototype
    let dir = project(
        "c2",
        &[
            ("util.h", "int helper(int x);\n"),
            ("util.c", "#include \"util.h\"\nint helper(int x) { return x; }\n"),
            ("main.c", "#include \"util.h\"\nint main(void) { return helper(1); }\n"),
        ],
    );
    let (g, _) = build_graph(&BuildOptions::new(&dir)).unwrap();
    std::fs::remove_dir_all(&dir).unwrap();
    let call = g["links"].as_array().unwrap().iter().find(|e| e["relation"] == "calls").unwrap();
    assert_eq!(call["target"], "util_c_helper");
}

#[test]
fn kotlin_and_scala() {
    let got = edges("jvm", &[
        ("src/a/Repo.kt", "package a\ninterface Repo { fun find(): Int }\nclass Db : Repo { override fun find(): Int = 1 }\n"),
        ("src/b/Svc.kt", "package b\nimport a.Repo\nclass Svc(private val repo: Repo) { fun run(): Int = repo.find() }\n"),
        ("src/c/Model.scala", "package c\ntrait Named { def name: String }\nclass Item extends Named { def name = \"x\" }\n"),
    ]);
    has(
        &got,
        &[
            ("Db", "implements", "Repo"),
            ("Svc.kt", "imports", "Repo"),
            (".run()", "calls", ".find()"),
            ("Item", "inherits", "Named"),
        ],
    );
}

#[test]
fn imports_carry_when_they_run_and_externals_are_nodes() {
    let dir = project("runs", &[
        ("a.py", "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from b import B\n\n\ndef f():\n    import b\n    return b.g()\n"),
        ("b.py", "def g():\n    return 1\n\n\nclass B:\n    pass\n"),
    ]);
    let (g, _) = build_graph(&BuildOptions::new(&dir)).unwrap();
    std::fs::remove_dir_all(&dir).unwrap();
    let runs: BTreeSet<(String, String)> = g["links"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|e| {
            Some((e["relation"].as_str()?.to_string(), e["runs"].as_str()?.to_string()))
        })
        .collect();
    assert!(runs.contains(&("imports_from".into(), "type-checking".into())));
    assert!(runs.contains(&("imports".into(), "deferred".into())));
    let typing = g["nodes"].as_array().unwrap().iter().find(|n| n["label"] == "typing").unwrap();
    assert_eq!(typing["type"], "external");
    assert_eq!(typing["source_file"], "");
    // one edge per (source, target, relation)
    let mut seen = BTreeSet::new();
    for e in g["links"].as_array().unwrap() {
        let key = (e["source"].to_string(), e["target"].to_string(), e["relation"].to_string());
        assert!(seen.insert(key), "duplicate edge {e}");
    }
}
