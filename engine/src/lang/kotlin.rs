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
