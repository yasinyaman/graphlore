//! `cargo run --example sexp -- <file> [as-name]`: print the tree with field names.
use graphlore_engine::{lang, parse};
use tree_sitter::Node;

fn dump(n: Node, src: &[u8], field: Option<&str>, depth: usize) {
    let text = if n.named_child_count() == 0 {
        format!(" {:?}", String::from_utf8_lossy(&src[n.byte_range()]))
    } else {
        String::new()
    };
    let f = field.map(|f| format!("{f}: ")).unwrap_or_default();
    if n.is_named() {
        println!("{}{f}{}{text}", "  ".repeat(depth), n.kind());
    } else if field.is_some() {
        println!("{}{f}'{}'", "  ".repeat(depth), n.kind());
    }
    let mut c = n.walk();
    for (i, child) in n.children(&mut c).enumerate() {
        dump(child, src, n.field_name_for_child(i as u32), depth + 1);
    }
}

fn main() {
    let path = std::env::args().nth(1).expect("file");
    let rel = std::env::args().nth(2).unwrap_or_else(|| path.clone());
    let src = std::fs::read(&path).unwrap();
    let l = lang::detect(&rel, Some(&src)).expect("language");
    let tree = parse::parse(l, &src, None).ok().unwrap();
    dump(tree.root_node(), &src, None, 0);
}
