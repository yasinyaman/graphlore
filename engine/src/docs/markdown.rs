//! Markdown headings through tree-sitter-md's block grammar, so a `#` inside a
//! fenced code block is not a heading.

use crate::parse::Fields;
use crate::parse::Kinds;
use tree_sitter::{Node, Parser};

use super::{code_spans, mention};
use crate::facts::{DocMention, Heading, MentionForm};
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
        match node.kind_str() {
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
    let marker = kids(node).into_iter().find(|c| c.kind_str().starts_with("atx_h"))?;
    let level = marker.kind_str().as_bytes().get(5).and_then(|b| (*b as char).to_digit(10))?;
    let content = node.by_field("heading_content")?;
    heading(level, &text(content, src), node)
}

fn setext(node: Node, src: &[u8]) -> Option<Heading> {
    let ks = kids(node);
    let level = if ks.iter().any(|c| c.kind_str() == "setext_h1_underline") { 1 } else { 2 };
    let content = node.by_field("heading_content").or_else(|| ks.first().copied())?;
    heading(level, &text(content, src).replace('\n', " "), node)
}

/// The code spans of the document's text (paragraphs, headings, lists,
/// tables): never what a fenced or indented code block holds, which the block
/// grammar keeps out of its `inline` nodes.
pub fn mentions(src: &[u8]) -> Vec<DocMention> {
    let mut parser = Parser::new();
    if parser.set_language(&tree_sitter_md::LANGUAGE.into()).is_err() {
        return Vec::new();
    }
    let Some(tree) = parser.parse(src, None) else { return Vec::new() };
    let mut out = Vec::new();
    let mut stack = vec![tree.root_node()];
    while let Some(node) = stack.pop() {
        if node.kind_str() == "inline" {
            for (raw, at) in code_spans(&text(node, src), line(node)) {
                out.extend(mention(&raw, at, MentionForm::Literal, None));
            }
            continue;
        }
        stack.extend(kids(node).into_iter().rev());
    }
    out
}
