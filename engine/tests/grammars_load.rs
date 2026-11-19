//! Every grammar compiled in loads under the linked tree-sitter core (the ABI
//! guard) and parses a line of its language.

use graphlore_engine::lang::{Lang, SPECS};
use graphlore_engine::parse;

#[test]
fn every_grammar_loads_and_parses() {
    let samples: &[(Lang, &str)] = &[
        (Lang::Python, "def f():\n    return 1\n"),
        (Lang::JavaScript, "function f() { return 1; }\n"),
        (Lang::TypeScript, "function f(): number { return 1; }\n"),
        (Lang::Tsx, "const A = () => <div/>;\n"),
        (Lang::Go, "package m\nfunc F() int { return 1 }\n"),
        (Lang::Java, "class A { int f() { return 1; } }\n"),
        (Lang::Kotlin, "fun f(): Int { return 1 }\n"),
        (Lang::Scala, "object A { def f = 1 }\n"),
        (Lang::CSharp, "class A { int F() { return 1; } }\n"),
        (Lang::Rust, "fn f() -> i32 { 1 }\n"),
        (Lang::Swift, "func f() -> Int { return 1 }\n"),
        (Lang::C, "int f(void) { return 1; }\n"),
        (Lang::Cpp, "int A::f() { return 1; }\n"),
        (Lang::Ruby, "def f\n  1\nend\n"),
        (Lang::Php, "<?php function f() { return 1; }\n"),
    ];
    assert_eq!(samples.len(), SPECS.len(), "a sample per registered language");
    for (lang, src) in samples {
        let tree = parse::parse(*lang, src.as_bytes(), None)
            .unwrap_or_else(|_| panic!("{} did not parse", lang.name()));
        assert!(!tree.root_node().has_error(), "{} parsed with errors", lang.name());
        let facts = graphlore_engine::file_facts(
            src.as_bytes(),
            &format!("x.{}", lang.spec().extensions[0]),
        );
        assert!(!facts.defs.is_empty(), "{} found no definition", lang.name());
    }
}

#[test]
fn markdown_grammar_loads() {
    let facts = graphlore_engine::file_facts(b"# Title\n\ntext\n\n## Part\n", "README.md");
    let got: Vec<(u32, &str, u32, u32)> =
        facts.headings.iter().map(|h| (h.level, h.text.as_str(), h.line, h.end_line)).collect();
    assert_eq!(got, vec![(1, "Title", 1, 5), (2, "Part", 5, 5)]);
}
