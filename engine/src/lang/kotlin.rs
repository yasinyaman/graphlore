//! Kotlin (the fwcd grammar, as `tree-sitter-kotlin-sg`). It has no `name`
//! fields: the generic walk names a declaration by its identifier child. One
//! `class_declaration` covers classes, interfaces and enum classes. A base
//! called as a constructor (`: Base()`) is the superclass; the others are
//! interfaces unless the target says otherwise.

use crate::parse::Kinds;
use tree_sitter::Node;

use super::java::dotted_import;
use super::LanguageModule;
use crate::facts::{DefKind, ImportFact, RefKind};
use crate::generic::{self, all_kids, kids, text};
use crate::refs::path_of;

pub struct Kotlin;

impl LanguageModule for Kotlin {
    /// The grammar drops some comments from the tree (a KDoc block before a
    /// declaration with modifiers, a `//` line after one): the comment lines
    /// directly above the region the tree gives are read from the source.
    fn region_start_in(&self, node: Node, def_line: u32, src: &[u8]) -> u32 {
        let mut start = self.region_start(node, def_line);
        if start < 2 {
            return start;
        }
        let lines: Vec<&[u8]> = src.split(|b| *b == b'\n').collect();
        // where each line begins, and whether the tree has a comment there:
        // one it has and did not give is no part of the region (the first
        // comment of a block, which is no sibling of the declaration)
        let offset = |l: u32| -> usize {
            lines.iter().take(l as usize - 1).map(|x| x.len() + 1).sum::<usize>()
                + lines
                    .get(l as usize - 1)
                    .and_then(|x| x.iter().position(|c| !c.is_ascii_whitespace()))
                    .unwrap_or(0)
        };
        let mut root = node;
        while let Some(p) = root.parent() {
            root = p;
        }
        let in_tree = |l: u32| -> bool {
            let at = offset(l);
            root.descendant_for_byte_range(at, at + 1)
                .is_some_and(|n| n.kind_str().contains("comment"))
        };
        let trimmed = |l: u32| -> &[u8] {
            let line = lines.get(l as usize - 1).copied().unwrap_or(b"");
            let lo = line.iter().position(|c| !c.is_ascii_whitespace()).unwrap_or(line.len());
            let hi = line.iter().rposition(|c| !c.is_ascii_whitespace()).map_or(lo, |i| i + 1);
            &line[lo..hi]
        };
        while start > 1 {
            let above = trimmed(start - 1);
            if (above.starts_with(b"//") || above.ends_with(b"*/")) && in_tree(start - 1) {
                break;
            }
            if above.starts_with(b"//") {
                start -= 1;
            } else if above.ends_with(b"*/") {
                // the block comment that ends here, when it begins a line
                let mut l = start - 1;
                while l > 1 && !trimmed(l).windows(2).any(|w| w == b"/*") {
                    l -= 1;
                }
                if !trimmed(l).starts_with(b"/*") {
                    break;
                }
                start = l;
            } else {
                break;
            }
        }
        start
    }

    /// `fun KmProperty.toPropertySpec()`: the receiver's type.
    fn receiver_of(&self, node: Node, src: &[u8]) -> Option<String> {
        if node.kind_str() != "function_declaration" {
            return None;
        }
        let recv = kids(node).into_iter().find(|c| c.kind_str() == "receiver_type")?;
        let mut queue: std::collections::VecDeque<Node> = kids(recv).into();
        while let Some(n) = queue.pop_front() {
            if n.kind_str() == "type_identifier" {
                return Some(text(n, src));
            }
            queue.extend(kids(n));
        }
        None
    }

    /// Top-level `var` / `val` (class and companion properties are
    /// `extra_def`'s).
    fn state_defs<'t>(&self, n: Node<'t>, src: &[u8]) -> Vec<(Node<'t>, Node<'t>, DefKind)> {
        // `class Holder(val parameters: List<String>)`: a constructor
        // parameter written `val` / `var` declares a property of the class
        if n.kind_str() == "class_parameter" {
            let property = kids(n).iter().any(|c| c.kind_str() == "binding_pattern_kind");
            let name = kids(n).into_iter().find(|c| c.kind_str() == "simple_identifier");
            return match name {
                Some(name) if property => vec![(n, name, DefKind::Property)],
                _ => Vec::new(),
            };
        }
        if n.kind_str() != "property_declaration"
            || !n.parent().is_some_and(|p| p.kind_str() == "source_file")
        {
            return Vec::new();
        }
        let val = kids(n)
            .iter()
            .any(|c| c.kind_str() == "binding_pattern_kind" && text(*c, src) == "val");
        let kind = if val { DefKind::Constant } else { DefKind::Variable };
        kids(n)
            .into_iter()
            .filter(|c| c.kind_str() == "variable_declaration")
            .filter_map(|v| kids(v).into_iter().find(|c| c.kind_str() == "simple_identifier"))
            .map(|name| (n, name, kind))
            .collect()
    }

    fn kind_of(&self, node: Node, src: &[u8]) -> DefKind {
        if node.kind_str() == "class_declaration" {
            for c in all_kids(node) {
                match c.kind_str() {
                    "interface" => return DefKind::Interface,
                    "modifiers" if text(c, src).split_whitespace().any(|w| w == "enum") => {
                        return DefKind::Enum
                    }
                    _ => {}
                }
            }
            return DefKind::Class;
        }
        generic::kind_of(node.kind_str())
    }

    fn extra_def<'t>(&self, n: Node<'t>, _src: &[u8]) -> Option<(Node<'t>, DefKind)> {
        if n.kind_str() != "property_declaration"
            || !n.parent().is_some_and(|p| p.kind_str() == "class_body")
        {
            return None;
        }
        let decl = kids(n).into_iter().find(|c| c.kind_str() == "variable_declaration")?;
        let name = kids(decl).into_iter().find(|c| c.kind_str() == "simple_identifier")?;
        Some((name, DefKind::Property))
    }

    fn imports_of(&self, n: Node, src: &[u8]) -> Option<Vec<ImportFact>> {
        match n.kind_str() {
            "import_list" => None,
            "import_header" => {
                let ks = kids(n);
                let path =
                    ks.iter().find(|c| c.kind_str() == "identifier").map(|c| text(*c, src))?;
                let wildcard = ks.iter().any(|c| c.kind_str() == "wildcard_import");
                let alias = ks
                    .iter()
                    .find(|c| c.kind_str() == "import_alias")
                    .and_then(|a| kids(*a).into_iter().next())
                    .map(|a| text(a, src));
                Some(vec![dotted_import(n, &path, wildcard, alias)])
            }
            _ => None,
        }
    }

    fn package_of(&self, n: Node, src: &[u8]) -> Option<String> {
        (n.kind_str() == "package_header")
            .then(|| {
                kids(n).into_iter().find(|c| c.kind_str() == "identifier").map(|c| text(c, src))
            })
            .flatten()
    }

    fn heritage_of(&self, n: Node, src: &[u8]) -> Vec<(RefKind, Vec<String>)> {
        let mut out = Vec::new();
        for c in kids(n) {
            if c.kind_str() != "delegation_specifier" {
                continue;
            }
            for d in kids(c) {
                let (kind, t) = match d.kind_str() {
                    "constructor_invocation" => (RefKind::Inherits, kids(d).into_iter().next()),
                    "user_type" => (RefKind::Base, Some(d)),
                    "explicit_delegation" => (RefKind::Base, kids(d).into_iter().next()),
                    _ => continue,
                };
                if let Some(p) = t.and_then(|t| path_of(t, src)) {
                    out.push((kind, p));
                }
            }
        }
        out
    }
}
