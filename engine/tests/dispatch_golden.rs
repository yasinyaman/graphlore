//! Dispatch goldens: a method written for a method of a base, an interface
//! or a trait is what a call of that method reaches (`dispatches_to`, from
//! the base's method to it), and no call names it.

use std::collections::BTreeSet;
use std::path::PathBuf;

use graphlore_engine::{build_graph, BuildOptions};

fn project(name: &str, files: &[(&str, &str)]) -> PathBuf {
    let dir =
        std::env::temp_dir().join(format!("graphlore-dispatch-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    for (rel, src) in files {
        let path = dir.join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, src).unwrap();
    }
    dir
}

/// `(base method, implementing method)` by qualified name, and the edges'
/// confidence and context checked on the way.
fn dispatch(name: &str, files: &[(&str, &str)]) -> BTreeSet<(String, String)> {
    dispatch_lines(name, files).into_iter().map(|(a, _, b, _)| (a, b)).collect()
}

/// The same with each end's line: overloads share a qualified name.
fn dispatch_lines(name: &str, files: &[(&str, &str)]) -> BTreeSet<(String, u64, String, u64)> {
    let dir = project(name, files);
    let (g, _) = build_graph(&BuildOptions::new(&dir)).unwrap();
    std::fs::remove_dir_all(&dir).unwrap();
    let qual = |id: &str| {
        g["nodes"]
            .as_array()
            .unwrap()
            .iter()
            .find(|n| n["id"] == id)
            .map(|n| {
                let at = n["source_location"].as_str().unwrap_or_default();
                let line = at.trim_start_matches('L').split('-').next().unwrap_or_default();
                (n["qualname"].as_str().unwrap_or_default().to_string(), line.parse().unwrap_or(0))
            })
            .unwrap_or_default()
    };
    g["links"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|e| e["relation"] == "dispatches_to")
        .map(|e| {
            assert_eq!(e["confidence"], "EXTRACTED", "{e}");
            assert_eq!(e["context"], "override", "{e}");
            let (a, la) = qual(e["source"].as_str().unwrap());
            let (b, lb) = qual(e["target"].as_str().unwrap());
            (a, la, b, lb)
        })
        .collect()
}

fn set(pairs: &[(&str, &str)]) -> BTreeSet<(String, String)> {
    pairs.iter().map(|(a, b)| (a.to_string(), b.to_string())).collect()
}

#[test]
fn rust_a_trait_method_reaches_the_methods_written_under_its_impls() {
    // each `impl Trait for Type` in whatever file; the type need not be the
    // project's (`&File`); a default method nobody writes again, an inherent
    // method and a method of another trait's `impl` give nothing
    let got = dispatch("rust", &[
        ("Cargo.toml", "[package]\nname = \"demo\"\nversion = \"0.1.0\"\n"),
        ("src/lib.rs", "pub mod tcp;\nuse std::fs::File;\n\npub trait Transport {\n    fn open(&self) -> bool;\n    fn close(&self) {}\n}\n\npub trait Named {\n    fn open(&self) -> bool;\n}\n\nimpl Transport for &File {\n    fn open(&self) -> bool {\n        true\n    }\n}\n"),
        ("src/tcp.rs", "use crate::{Named, Transport};\n\npub struct Tcp;\n\nimpl Tcp {\n    pub fn connect(&self) {}\n}\n\nimpl Transport for Tcp {\n    fn open(&self) -> bool {\n        true\n    }\n}\n\nimpl Named for Tcp {\n    fn open(&self) -> bool {\n        false\n    }\n}\n"),
    ]);
    assert_eq!(got.len(), 3, "{got:#?}");
    let from = |base: &str| got.iter().filter(|(b, _)| b == base).count();
    assert_eq!((from("Transport.open"), from("Named.open")), (2, 1), "{got:#?}");
    assert!(got.iter().all(|(_, m)| m.ends_with(".open")), "{got:#?}");
}

#[test]
fn java_the_nearest_base_that_declares_the_method_answers() {
    // an interface's method, a method two classes up, and nothing for a
    // constructor or for a method of the name taking other parameters
    let got = dispatch("java", &[
        ("src/p/Shape.java", "package p;\n\npublic interface Shape {\n    double area();\n}\n"),
        ("src/p/Base.java", "package p;\n\npublic class Base {\n    public Base() {}\n    public String name() { return \"base\"; }\n    public int scale(int by) { return by; }\n}\n"),
        ("src/p/Mid.java", "package p;\n\npublic class Mid extends Base {\n    public Mid() {}\n}\n"),
        ("src/p/Square.java", "package p;\n\npublic class Square extends Mid implements Shape {\n    public Square() {}\n    public double area() { return 1.0; }\n    public String name() { return \"square\"; }\n    public int scale(int by, int times) { return by * times; }\n}\n"),
    ]);
    assert_eq!(
        got,
        set(&[("Shape.area", "Square.area"), ("Base.name", "Square.name")]),
        "{got:#?}"
    );
}

#[test]
fn python_a_subclass_method_and_not_its_constructor() {
    let got = dispatch("py", &[
        ("pkg/__init__.py", ""),
        ("pkg/base.py", "class Base:\n    def __init__(self):\n        self.n = 0\n\n    def run(self):\n        return self.step()\n\n    def step(self):\n        return 1\n"),
        ("pkg/app.py", "from pkg.base import Base\n\n\nclass App(Base):\n    def __init__(self):\n        super().__init__()\n\n    def step(self):\n        return 2\n\n    def extra(self):\n        return 3\n"),
    ]);
    assert_eq!(got, set(&[("Base.step", "App.step")]), "{got:#?}");
}

#[test]
fn typescript_an_interface_and_an_abstract_class() {
    let got = dispatch("ts", &[
        ("src/store.ts", "export interface Store {\n  get(key: string): string;\n}\n\nexport abstract class Cache {\n  abstract clear(): void;\n  size(): number {\n    return 0;\n  }\n}\n"),
        ("src/memory.ts", "import { Cache, Store } from './store';\n\nexport class Memory extends Cache implements Store {\n  constructor() {\n    super();\n  }\n  get(key: string): string {\n    return key;\n  }\n  clear(): void {}\n}\n"),
    ]);
    assert_eq!(
        got,
        set(&[("Store.get", "Memory.get"), ("Cache.clear", "Memory.clear")]),
        "{got:#?}"
    );
}

#[test]
fn go_says_nothing() {
    // an interface is satisfied by shape and an embedded type's method of the
    // same name is hidden, not dispatched to: no type says which it means
    let got = dispatch("go", &[
        ("go.mod", "module demo\n\ngo 1.21\n"),
        ("a.go", "package demo\n\ntype Reader interface {\n\tRead() int\n}\n\ntype Base struct{}\n\nfunc (b Base) Read() int { return 1 }\n\ntype File struct {\n\tBase\n}\n\nfunc (f File) Read() int { return 2 }\n"),
    ]);
    assert!(got.is_empty(), "{got:#?}");
}

#[test]
fn swift_the_labels_are_part_of_the_name() {
    // a protocol with two `request`s told apart by their second label: each
    // goes to the method written with its labels, not to both
    let got = dispatch_lines("swift", &[
        ("Package.swift", "// swift-tools-version:5.9\nimport PackageDescription\nlet package = Package(name: \"Demo\", targets: [.target(name: \"Demo\")])\n"),
        ("Sources/Demo/Monitor.swift", "protocol Monitor {\n    func request(_ r: Int, didStart at: Int)\n    func request(_ r: Int, didFinish at: Int)\n}\n\nfinal class Logger: Monitor {\n    func request(_ r: Int, didStart at: Int) {}\n    func request(_ r: Int, didFinish at: Int) {}\n    func request(_ r: Int, didFail at: Int) {}\n}\n"),
    ]);
    let lines: BTreeSet<(u64, u64)> = got.iter().map(|(_, a, _, b)| (*a, *b)).collect();
    assert_eq!(lines, BTreeSet::from([(2, 7), (3, 8)]), "{got:#?}");
}

#[test]
fn csharp_overloads_of_one_count_go_by_their_types() {
    // `Serialize(object)` and `Serialize(Parameter)` in one class: the
    // interface's method reaches the one written with its type
    let got = dispatch("cs", &[
        ("ISerializer.cs", "namespace Demo;\n\npublic interface ISerializer {\n    string Serialize(object obj);\n}\n"),
        ("Stub.cs", "namespace Demo;\n\npublic class Parameter {}\n\npublic class Stub : ISerializer {\n    public string Serialize(object obj) { return \"\"; }\n    public string Serialize(Parameter p) { return \"\"; }\n}\n"),
    ]);
    assert_eq!(got.len(), 1, "{got:#?}");
}
