//! PHP. A `namespace X;` statement is the file's package; the block form only
//! prefixes names. Closures are not definitions. `use A\B\C [as D]` (and the
//! grouped `use A\{B, C}`) names classes; `require` / `include` with a literal
//! path name a file. `extends`, `implements` and a trait `use` in a class body
//! are the bases.

use tree_sitter::Node;

use super::LanguageModule;
use crate::facts::{DefKind, ImportFact, ImportForm, RefKind};
use crate::generic::{kids, text};
use crate::refs::{import, path_of, string_value};

pub struct Php;

fn split_name(full: &str) -> (String, String) {
    let full = full.trim_start_matches('\\');
    match full.rsplit_once('\\') {
        Some((a, b)) => (a.to_string(), b.to_string()),
        None => (String::new(), full.to_string()),
    }
}

fn use_clause(n: Node, clause: Node, prefix: &str, src: &[u8]) -> Option<ImportFact> {
    let name = kids(clause)
        .into_iter()
        .find(|c| matches!(c.kind(), "qualified_name" | "name" | "namespace_name"))?;
    let full =
        if prefix.is_empty() { text(name, src) } else { format!("{prefix}\\{}", text(name, src)) };
    let (module, last) = split_name(&full);
    let mut f = import(n, ImportForm::From, module);
    f.names.push((last, clause.child_by_field_name("alias").map(|a| text(a, src))));
    Some(f)
}

impl LanguageModule for Php {
    /// `public static $total` (shared by every instance) and `const MAX`.
    fn state_defs<'t>(&self, n: Node<'t>, _src: &[u8]) -> Vec<(Node<'t>, Node<'t>, DefKind)> {
        match n.kind() {
            "property_declaration" => kids(n)
                .into_iter()
                .filter(|c| c.kind() == "property_element")
                .filter_map(|e| e.child_by_field_name("name"))
                .filter_map(|v| kids(v).into_iter().find(|c| c.kind() == "name"))
                .map(|name| (n, name, DefKind::Property))
                .collect(),
            "const_declaration" => kids(n)
                .into_iter()
                .filter(|c| c.kind() == "const_element")
                .filter_map(|e| kids(e).into_iter().find(|c| c.kind() == "name"))
                .map(|name| (n, name, DefKind::Constant))
                .collect(),
            _ => Vec::new(),
        }
    }

    fn imports_of(&self, n: Node, src: &[u8]) -> Option<Vec<ImportFact>> {
        match n.kind() {
            "namespace_use_declaration" => {
                let mut out = Vec::new();
                let prefix = kids(n)
                    .into_iter()
                    .find(|c| c.kind() == "namespace_name")
                    .map(|c| text(c, src))
                    .unwrap_or_default();
                for c in kids(n) {
                    match c.kind() {
                        "namespace_use_clause" => out.extend(use_clause(n, c, "", src)),
                        "namespace_use_group" => {
                            for g in kids(c) {
                                if g.kind() == "namespace_use_clause" {
                                    out.extend(use_clause(n, g, &prefix, src));
                                }
                            }
                        }
                        _ => {}
                    }
                }
                Some(out)
            }
            k if k.starts_with("require") || k.starts_with("include") => {
                let mut stack = vec![n];
                while let Some(x) = stack.pop() {
                    if matches!(x.kind(), "string" | "encapsed_string") {
                        let mut f = import(n, ImportForm::Module, string_value(x, src));
                        f.system = false;
                        return Some(vec![f]);
                    }
                    stack.extend(kids(x));
                }
                Some(Vec::new())
            }
            _ => None,
        }
    }

    fn package_of(&self, n: Node, src: &[u8]) -> Option<String> {
        (n.kind() == "namespace_definition" && n.child_by_field_name("body").is_none())
            .then(|| n.child_by_field_name("name").map(|c| text(c, src)))
            .flatten()
    }

    fn heritage_of(&self, n: Node, src: &[u8]) -> Vec<(RefKind, Vec<String>)> {
        let mut out = Vec::new();
        for c in kids(n) {
            let kind = match c.kind() {
                "base_clause" => RefKind::Inherits,
                "class_interface_clause" => RefKind::Implements,
                "declaration_list" => {
                    for u in kids(c) {
                        if u.kind() == "use_declaration" {
                            out.extend(
                                kids(u)
                                    .into_iter()
                                    .filter_map(|t| path_of(t, src))
                                    .map(|p| (RefKind::MixesIn, p)),
                            );
                        }
                    }
                    continue;
                }
                _ => continue,
            };
            out.extend(kids(c).into_iter().filter_map(|t| path_of(t, src)).map(|p| (kind, p)));
        }
        out
    }
}
