//! Python through tree-sitter-python. graphlore's own span engine reads Python
//! with the stdlib `ast`; this module keeps to what `ast` reports: a region
//! starts at the first decorator (never at a comment above the `def`), and a
//! lambda bound to a name is not a definition.

use tree_sitter::Node;

use super::LanguageModule;
use crate::generic::{all_kids, end_line, line};

pub struct Python;

/// The end of the last token that is not a comment, as `ast`'s `end_lineno`.
fn code_end(node: Node) -> u32 {
    let mut n = node;
    loop {
        let last = all_kids(n).into_iter().rev().find(|c| c.kind() != "comment");
        match last {
            Some(c) => n = c,
            None => return end_line(n),
        }
    }
}

impl LanguageModule for Python {
    fn bound_functions(&self) -> bool {
        false
    }

    fn end_line(&self, node: Node) -> u32 {
        code_end(node)
    }

    fn region_start(&self, node: Node, def_line: u32) -> u32 {
        match node.parent() {
            Some(p) if p.kind() == "decorated_definition" => line(p).min(def_line),
            _ => def_line,
        }
    }
}
