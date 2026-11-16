//! Kotlin (the fwcd grammar, as `tree-sitter-kotlin-sg`). It has no `name`
//! fields: the generic walk names a declaration by its identifier child. One
//! `class_declaration` covers classes, interfaces and enum classes. A base
//! called as a constructor (`: Base()`) is the superclass; the others are
//! interfaces unless the target says otherwise.

use tree_sitter::Node;

use super::java::dotted_import;
use super::LanguageModule;
use crate::facts::{DefKind, ImportFact, RefKind};
use crate::generic::{self, all_kids, kids, text};
use crate::refs::path_of;

pub struct Kotlin;

impl LanguageModule for Kotlin {
    /// Top-level `var` / `val` (class and companion properties are
    /// `extra_def`'s).
    fn state_defs<'t>(&self, n: Node<'t>, src: &[u8]) -> Vec<(Node<'t>, Node<'t>, DefKind)> {
        if n.kind() != "property_declaration"
            || !n.parent().is_some_and(|p| p.kind() == "source_file")
        {
            return Vec::new();
        }
        let val =
            kids(n).iter().any(|c| c.kind() == "binding_pattern_kind" && text(*c, src) == "val");
        let kind = if val { DefKind::Constant } else { DefKind::Variable };
        kids(n)
            .into_iter()
            .filter(|c| c.kind() == "variable_declaration")
            .filter_map(|v| kids(v).into_iter().find(|c| c.kind() == "simple_identifier"))
            .map(|name| (n, name, kind))
            .collect()
    }

    fn kind_of(&self, node: Node, src: &[u8]) -> DefKind {
        if node.kind() == "class_declaration" {
            for c in all_kids(node) {
                match c.kind() {
                    "interface" => return DefKind::Interface,
                    "modifiers" if text(c, src).split_whitespace().any(|w| w == "enum") => {
                        return DefKind::Enum
                    }
                    _ => {}
                }
            }
            return DefKind::Class;
        }
        generic::kind_of(node.kind())
    }

    fn extra_def<'t>(&self, n: Node<'t>, _src: &[u8]) -> Option<(Node<'t>, DefKind)> {
        if n.kind() != "property_declaration"
            || !n.parent().is_some_and(|p| p.kind() == "class_body")
        {
            return None;
        }
        let decl = kids(n).into_iter().find(|c| c.kind() == "variable_declaration")?;
        let name = kids(decl).into_iter().find(|c| c.kind() == "simple_identifier")?;
        Some((name, DefKind::Property))
    }

    fn imports_of(&self, n: Node, src: &[u8]) -> Option<Vec<ImportFact>> {
        match n.kind() {
            "import_list" => None,
            "import_header" => {
                let ks = kids(n);
                let path = ks.iter().find(|c| c.kind() == "identifier").map(|c| text(*c, src))?;
                let wildcard = ks.iter().any(|c| c.kind() == "wildcard_import");
                let alias = ks
                    .iter()
                    .find(|c| c.kind() == "import_alias")
                    .and_then(|a| kids(*a).into_iter().next())
                    .map(|a| text(a, src));
                Some(vec![dotted_import(n, &path, wildcard, alias)])
            }
            _ => None,
        }
    }

    fn package_of(&self, n: Node, src: &[u8]) -> Option<String> {
        (n.kind() == "package_header")
            .then(|| kids(n).into_iter().find(|c| c.kind() == "identifier").map(|c| text(c, src)))
            .flatten()
    }

    fn heritage_of(&self, n: Node, src: &[u8]) -> Vec<(RefKind, Vec<String>)> {
        let mut out = Vec::new();
        for c in kids(n) {
            if c.kind() != "delegation_specifier" {
                continue;
            }
            for d in kids(c) {
                let (kind, t) = match d.kind() {
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
