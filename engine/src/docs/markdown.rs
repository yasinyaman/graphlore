//! Markdown headings through tree-sitter-md's block grammar, so a `#` inside a
//! fenced code block is not a heading.

use tree_sitter::{Node, Parser};

use crate::facts::Heading;
use crate::generic::{kids, line, text};

pub fn headings(src: &[u8]) -> Vec<Heading> {
    let mut parser = Parser::new();
    if parser.set_language(&tree_sitter_md::LANGUAGE.into()).is_err() {
        return Vec::new();
    }
    let Some(tree) = parser.parse(src, None) else { return Vec::new() };
    let mut out = Vec::new();
    let mut stack = vec![tree.root_node()];
    while let Some(node) = stack.pop() {
        match node.kind() {
            "atx_heading" => {
                if let Some(h) = atx(node, src) {
                    out.push(h);
                }
            }
            "setext_heading" => {
                if let Some(h) = setext(node, src) {
                    out.push(h);
                }
            }
            _ => stack.extend(kids(node).into_iter().rev()),
        }
    }
    out
}

fn heading(level: u32, raw: &str, at: Node) -> Option<Heading> {
    let text = raw.trim().trim_end_matches('#').trim_end().to_string();
    if text.is_empty() {
        return None;
    }
    Some(Heading { level, text, line: line(at), end_line: line(at) })
}

fn atx(node: Node, src: &[u8]) -> Option<Heading> {
    let marker = kids(node).into_iter().find(|c| c.kind().starts_with("atx_h"))?;
    let level = marker.kind().as_bytes().get(5).and_then(|b| (*b as char).to_digit(10))?;
    let content = node.child_by_field_name("heading_content")?;
    heading(level, &text(content, src), node)
}

fn setext(node: Node, src: &[u8]) -> Option<Heading> {
    let ks = kids(node);
    let level = if ks.iter().any(|c| c.kind() == "setext_h1_underline") { 1 } else { 2 };
    let content = node.child_by_field_name("heading_content").or_else(|| ks.first().copied())?;
    heading(level, &text(content, src).replace('\n', " "), node)
}
