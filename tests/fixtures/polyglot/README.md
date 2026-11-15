# polyglot fixture

A tiny project in five languages whose `graph.json` was built by graphify
0.9 (tests copy it to `graphify-out/` in a temp directory). It carries what the
5-node fixture cannot: `file_type`, `community_name`, `case_of` and `re_exports`
edges, closure nodes, a Rust `#[cfg(test)]` module and a JS import through a
barrel. Rebuild it with `graphify update .` in a copy of this directory.
