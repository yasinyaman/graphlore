# graphlore-engine

The native code-graph extractor behind [graphlore](https://github.com/yasinyaman/graphlore).
It walks a project, parses every source file with a tree-sitter grammar compiled
into the wheel, and writes a graphify-compatible `graph.json`: a node per file,
per definition (with its real `start..end` span), property, constant and
document heading, the containment edges between them, and the imports, calls,
bases and type references it resolves from the source.

Languages: Python, JavaScript, TypeScript, TSX, Go, Java, Kotlin, Scala, C#,
Rust, Swift, C, C++, Ruby and PHP; plus Markdown and reStructuredText headings.
Nothing is downloaded at run time and nothing reaches the network.

It is experimental. Communities and incremental updates come in later
releases; until then graphlore uses it only when `GRAPHLORE_ENGINE=native` (or
`auto`) is set.

```bash
python -m graphlore_engine build /path/to/repo      # writes graphify-out/graph.json
python -m graphlore_engine spans src/app.ts         # one file's definitions
python -m graphlore_engine languages
```

## Developing

```bash
pip install maturin
maturin develop -m engine/Cargo.toml --release       # from the repository root
cargo test --manifest-path engine/Cargo.toml
```

Adding a language: add its grammar crate to `Cargo.toml`, a `Lang` variant and a
row in `SPECS` (`src/lang/mod.rs`), and a module in `src/lang/` that overrides
only what its grammar does differently from the generic walk (`src/generic.rs`).
Then add golden snippets to `tests/spans_golden.rs`, a small project to
`tests/edges_golden.rs`, and the language's module paths to
`src/resolve/modules.rs`.
