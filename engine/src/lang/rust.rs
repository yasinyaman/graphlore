//! Rust. `impl Type` prefixes its methods with the type (no definition of its
//! own), inline `mod` blocks only prefix names, `mod foo;` is skipped, and
//! `#[cfg(test)]` modules and `#[test]` functions are test regions.
//!
//! `use` trees are flattened into one fact per name (`use a::{b::C, d as e}`
//! is `C` from `a::b` and `d` from `a`, as `e`); `impl Trait for Type` makes
//! `Type` implement `Trait`, and `trait A: B` makes `A` extend `B`.

use std::sync::LazyLock;

use regex::Regex;
use tree_sitter::Node;

use super::LanguageModule;
use crate::facts::{ImportFact, ImportForm, RefKind};
use crate::generic::{end_line, kids, line, text};
use crate::refs::{import, path_of};

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

/// Flatten a `use` tree under `prefix` into `(path, alias, wildcard)` leaves.
fn use_leaves(
    n: Node,
    src: &[u8],
    prefix: &[String],
    out: &mut Vec<(Vec<String>, Option<String>, bool)>,
) {
    match n.kind() {
        "use_list" => {
            for c in kids(n) {
                use_leaves(c, src, prefix, out);
            }
        }
        "scoped_use_list" => {
            let mut p = prefix.to_vec();
            if let Some(path) = n.child_by_field_name("path").and_then(|x| path_of(x, src)) {
                p.extend(path);
            }
            if let Some(list) = n.child_by_field_name("list") {
                use_leaves(list, src, &p, out);
            }
        }
        "use_as_clause" => {
            let mut p = prefix.to_vec();
            if let Some(path) = n.child_by_field_name("path").and_then(|x| path_of(x, src)) {
                p.extend(path);
            }
            out.push((p, n.child_by_field_name("alias").map(|a| text(a, src)), false));
        }
        "use_wildcard" => {
            let mut p = prefix.to_vec();
            if let Some(path) = kids(n).into_iter().next().and_then(|x| path_of(x, src)) {
                p.extend(path);
            }
            out.push((p, None, true));
        }
        _ => {
            if let Some(path) = path_of(n, src) {
                let mut p = prefix.to_vec();
                p.extend(path);
                out.push((p, None, false));
            }
        }
    }
}

impl LanguageModule for Rust {
    fn imports_of(&self, n: Node, src: &[u8]) -> Option<Vec<ImportFact>> {
        match n.kind() {
            "use_declaration" => {
                let mut leaves = Vec::new();
                if let Some(arg) = n.child_by_field_name("argument") {
                    use_leaves(arg, src, &[], &mut leaves);
                }
                let mut out = Vec::new();
                for (path, alias, wildcard) in leaves {
                    if wildcard {
                        out.push(import(n, ImportForm::Wildcard, path.join("::")));
                        continue;
                    }
                    let Some((last, module)) = path.split_last() else { continue };
                    if last == "self" {
                        let mut f = import(n, ImportForm::Module, module.join("::"));
                        f.alias = alias;
                        out.push(f);
                    } else {
                        let mut f = import(n, ImportForm::From, module.join("::"));
                        f.names.push((last.clone(), alias));
                        out.push(f);
                    }
                }
                Some(out)
            }
            "extern_crate_declaration" => {
                let name = n.child_by_field_name("name").map(|x| text(x, src))?;
                let mut f = import(n, ImportForm::Module, name);
                f.alias = n.child_by_field_name("alias").map(|x| text(x, src));
                Some(vec![f])
            }
            _ => None,
        }
    }

    fn heritage_of(&self, n: Node, src: &[u8]) -> Vec<(RefKind, Vec<String>)> {
        match n.kind() {
            "impl_item" => n
                .child_by_field_name("trait")
                .and_then(|t| path_of(t, src))
                .map(|p| vec![(RefKind::Implements, p)])
                .unwrap_or_default(),
            "trait_item" => n
                .child_by_field_name("bounds")
                .map(|b| {
                    kids(b)
                        .into_iter()
                        .filter_map(|c| path_of(c, src))
                        .map(|p| (RefKind::Inherits, p))
                        .collect()
                })
                .unwrap_or_default(),
            _ => Vec::new(),
        }
    }

    fn heritage_owner(&self, n: Node, src: &[u8]) -> Option<String> {
        let t = n.child_by_field_name("type")?;
        let base = t.child_by_field_name("type").unwrap_or(t);
        path_of(base, src).and_then(|p| p.last().cloned())
    }

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
