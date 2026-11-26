//! Rust. `impl Type` prefixes its methods with the type (no definition of its
//! own), inline `mod` blocks only prefix names, `mod foo;` is skipped, and
//! `#[cfg(test)]` modules and `#[test]` functions are test regions.
//!
//! `use` trees are flattened into one fact per name (`use a::{b::C, d as e}`
//! is `C` from `a::b` and `d` from `a`, as `e`); `impl Trait for Type` makes
//! `Type` implement `Trait`, and `trait A: B` makes `A` extend `B`.

use crate::parse::Fields;
use crate::parse::Kinds;
use std::sync::LazyLock;

use regex::Regex;
use tree_sitter::Node;

use super::LanguageModule;
use crate::facts::{DefKind, ImportFact, ImportForm, RefKind};
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
        if p.kind_str() == "attribute_item" {
            out.push(text(p, src));
        } else if !p.kind_str().contains("comment") {
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
    match n.kind_str() {
        "use_list" => {
            for c in kids(n) {
                use_leaves(c, src, prefix, out);
            }
        }
        "scoped_use_list" => {
            let mut p = prefix.to_vec();
            if let Some(path) = n.by_field("path").and_then(|x| path_of(x, src)) {
                p.extend(path);
            }
            if let Some(list) = n.by_field("list") {
                use_leaves(list, src, &p, out);
            }
        }
        "use_as_clause" => {
            let mut p = prefix.to_vec();
            if let Some(path) = n.by_field("path").and_then(|x| path_of(x, src)) {
                p.extend(path);
            }
            out.push((p, n.by_field("alias").map(|a| text(a, src)), false));
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

/// The names of the inline modules (`mod m { … }`) around a node, outermost
/// first. Climbs with `parent()`: called for `use` declarations only.
fn inline_mods(n: Node, src: &[u8]) -> Vec<String> {
    let mut out = Vec::new();
    let mut cur = n.parent();
    while let Some(p) = cur {
        if p.kind_str() == "mod_item" && p.by_field("body").is_some() {
            out.push(p.by_field("name").map(|x| text(x, src)).unwrap_or_default());
        }
        cur = p.parent();
    }
    out.reverse();
    out
}

/// The block a `use` is written in, when that is inside a function (`fn f()
/// { use m::g; }`) rather than in a module: its names are the block's.
fn use_block(n: Node) -> Option<(usize, usize)> {
    let mut cur = n.parent();
    while let Some(p) = cur {
        match p.kind_str() {
            "block" => return Some((p.start_byte(), p.end_byte())),
            "mod_item" | "source_file" => return None,
            _ => cur = p.parent(),
        }
    }
    None
}

/// A `use` path written inside inline modules, as the file's module sees it:
/// `super::x` in `mod tests` is `self::x`, `self::x` is `self::tests::x`.
/// `None` when it only names the file's own module or one of its items, which
/// the file's scope holds already.
fn out_of_inline(path: Vec<String>, inline: &[String], wildcard: bool) -> Option<Vec<String>> {
    if inline.is_empty() {
        return Some(path);
    }
    let supers = path.iter().take_while(|s| *s == "super").count();
    let out: Vec<String> = if supers > inline.len() {
        path[inline.len()..].to_vec()
    } else if supers > 0 {
        let mut v = vec!["self".to_string()];
        v.extend(inline[..inline.len() - supers].iter().cloned());
        v.extend(path[supers..].iter().cloned());
        v
    } else if path.first().is_some_and(|s| s == "self") {
        let mut v = vec!["self".to_string()];
        v.extend(inline.iter().cloned());
        v.extend(path[1..].iter().cloned());
        v
    } else {
        return Some(path);
    };
    // `use super::*` / `use super::Thing` in `mod tests`: already in scope
    let own = if wildcard { out.len() == 1 } else { out.len() == 2 };
    (!(own && out[0] == "self")).then_some(out)
}

impl LanguageModule for Rust {
    fn needs_import(&self) -> bool {
        true
    }

    /// A struct's named fields: `self.n` in its `impl` reads or writes them.
    fn state_defs<'t>(&self, n: Node<'t>, _src: &[u8]) -> Vec<(Node<'t>, Node<'t>, DefKind)> {
        if n.kind_str() != "field_declaration"
            || !n.parent().and_then(|l| l.parent()).is_some_and(|s| s.kind_str() == "struct_item")
        {
            return Vec::new();
        }
        n.by_field("name").map(|name| vec![(n, name, DefKind::Property)]).unwrap_or_default()
    }

    fn extra_def<'t>(&self, n: Node<'t>, _src: &[u8]) -> Option<(Node<'t>, DefKind)> {
        // `const X: T = …;` / `static X: T = …;` outside a function body
        if !matches!(n.kind_str(), "const_item" | "static_item") {
            return None;
        }
        let in_fn = {
            let mut p = n.parent();
            let mut inside = false;
            while let Some(x) = p {
                if x.kind_str() == "function_item" {
                    inside = true;
                    break;
                }
                p = x.parent();
            }
            inside
        };
        // a `static` is one place in memory every function shares; a `const`
        // is copied where it is used
        let kind =
            if n.kind_str() == "static_item" { DefKind::Variable } else { DefKind::Constant };
        (!in_fn).then(|| n.by_field("name").map(|x| (x, kind))).flatten()
    }

    fn imports_of(&self, n: Node, src: &[u8]) -> Option<Vec<ImportFact>> {
        match n.kind_str() {
            "use_declaration" => {
                let mut leaves = Vec::new();
                if let Some(arg) = n.by_field("argument") {
                    use_leaves(arg, src, &[], &mut leaves);
                }
                // inside an inline `mod tests { … }`, `super` is this file's own
                // module, not its parent's file
                let inline = inline_mods(n, src);
                let public = kids(n).iter().any(|c| c.kind_str() == "visibility_modifier");
                let mut out: Vec<ImportFact> = Vec::new();
                for (path, alias, wildcard) in leaves {
                    let Some(path) = out_of_inline(path, &inline, wildcard) else { continue };
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
                let block = use_block(n);
                for f in &mut out {
                    f.public = public;
                    f.scope = block;
                    // (a plain first name: since 2018 a name in scope where
                    // the `use` is, a child of the inline module first)
                    let first = f.module.split("::").next().unwrap_or("");
                    let plain = !first.is_empty()
                        && !matches!(first, "crate" | "self" | "super" | "std" | "core" | "alloc");
                    if plain && !inline.is_empty() {
                        f.within = inline.clone();
                    }
                }
                Some(out)
            }
            "extern_crate_declaration" => {
                let name = n.by_field("name").map(|x| text(x, src))?;
                let mut f = import(n, ImportForm::Module, name);
                f.alias = n.by_field("alias").map(|x| text(x, src));
                Some(vec![f])
            }
            _ => None,
        }
    }

    fn heritage_of(&self, n: Node, src: &[u8]) -> Vec<(RefKind, Vec<String>)> {
        match n.kind_str() {
            "impl_item" => {
                let Some(t) = n.by_field("trait") else { return Vec::new() };
                let mut out: Vec<(RefKind, Vec<String>)> =
                    path_of(t, src).map(|p| (RefKind::Implements, p)).into_iter().collect();
                // `impl From<Config> for Agent`: the types the trait is
                // written with are the implementing type's uses
                let mut stack: Vec<Node> =
                    t.by_field("type_arguments").map(kids).unwrap_or_default();
                while let Some(a) = stack.pop() {
                    match a.kind_str() {
                        "type_identifier" | "scoped_type_identifier" => {
                            if let Some(p) = path_of(a, src) {
                                out.push((RefKind::Type, p));
                            }
                        }
                        "lifetime" | "primitive_type" => {}
                        _ => stack.extend(kids(a)),
                    }
                }
                out
            }
            "trait_item" => n
                .by_field("bounds")
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
        let t = n.by_field("type")?;
        let base = t.by_field("type").unwrap_or(t);
        path_of(base, src).and_then(|p| p.last().cloned())
    }

    /// `spans._test_regions_rust`.
    fn test_regions(&self, root: Node, src: &[u8]) -> Vec<(u32, u32)> {
        if kids(root)
            .iter()
            .any(|c| c.kind_str() == "inner_attribute_item" && is_cfg_test(&text(*c, src)))
        {
            return vec![(1, end_line(root))];
        }
        let mut regions = Vec::new();
        let mut stack = vec![root];
        while let Some(node) = stack.pop() {
            for child in kids(node).into_iter().rev() {
                let span = (line(child), end_line(child));
                if child.kind_str() == "mod_item" {
                    let inner = child.by_field("body").is_some_and(|b| {
                        kids(b).iter().any(|c| {
                            c.kind_str() == "inner_attribute_item" && is_cfg_test(&text(*c, src))
                        })
                    });
                    if inner || attributes(child, src).iter().any(|a| is_cfg_test(a)) {
                        regions.push(span);
                        continue;
                    }
                } else if child.kind_str() == "function_item"
                    // (`#[cfg(test)] fn get_limited(&self)`: a helper only
                    // the tests are compiled with)
                    && attributes(child, src).iter().any(|a| TEST_ATTR.is_match(a) || is_cfg_test(a))
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
