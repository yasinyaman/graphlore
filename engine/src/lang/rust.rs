//! Rust. `impl Type` prefixes its methods with the type (no definition of its
//! own), inline `mod` blocks only prefix names, `mod foo;` is skipped, and
//! `#[cfg(test)]` modules and `#[test]` functions are test regions.

use std::sync::LazyLock;

use regex::Regex;
use tree_sitter::Node;

use super::LanguageModule;
use crate::generic::{end_line, kids, line, text};

pub struct Rust;

static CFG_TEST: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\bcfg\s*\((?:[^()]|\([^()]*\))*\btest\b").expect("valid regex"));
static CFG_NOT_TEST: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\bnot\s*\(\s*test\s*\)").expect("valid regex"));
static TEST_ATTR: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^#\s*\[\s*(?:[\w:]+::)?test\s*(?:\(|\])").expect("valid regex"));

fn is_cfg_test(attr: &str) -> bool {
    CFG_TEST.is_match(attr) && !CFG_NOT_TEST.is_match(attr)
}

/// Texts of the `#[...]` attributes stacked right above `node`.
fn attributes(node: Node, src: &[u8]) -> Vec<String> {
    let mut out = Vec::new();
    let mut prev = node.prev_named_sibling();
    while let Some(p) = prev {
        if p.kind() == "attribute_item" {
            out.push(text(p, src));
        } else if !p.kind().contains("comment") {
            break;
        }
        prev = p.prev_named_sibling();
    }
    out
}

impl LanguageModule for Rust {
    /// `spans._test_regions_rust`.
    fn test_regions(&self, root: Node, src: &[u8]) -> Vec<(u32, u32)> {
        if kids(root)
            .iter()
            .any(|c| c.kind() == "inner_attribute_item" && is_cfg_test(&text(*c, src)))
        {
            return vec![(1, end_line(root))];
        }
        let mut regions = Vec::new();
        let mut stack = vec![root];
        while let Some(node) = stack.pop() {
            for child in kids(node).into_iter().rev() {
                let span = (line(child), end_line(child));
                if child.kind() == "mod_item" {
                    let inner = child.child_by_field_name("body").is_some_and(|b| {
                        kids(b).iter().any(|c| {
                            c.kind() == "inner_attribute_item" && is_cfg_test(&text(*c, src))
                        })
                    });
                    if inner || attributes(child, src).iter().any(|a| is_cfg_test(a)) {
                        regions.push(span);
                        continue;
                    }
                } else if child.kind() == "function_item"
                    && attributes(child, src).iter().any(|a| TEST_ATTR.is_match(a))
                {
                    regions.push(span);
                    continue;
                }
                stack.push(child);
            }
        }
        regions.sort_unstable();
        regions
    }
}
