//! Scala. The generic rule covers classes, objects, traits, enums and their
//! cases, and functions. Imports: `import a.b.C`, `import a.b.{C, D => E}`,
//! `import a.b._` (or `*`). In `extends A with B`, `A` is the superclass and
//! the rest are mixed in.

use tree_sitter::Node;

use super::java::dotted_import;
use super::LanguageModule;
use crate::facts::{ImportFact, ImportForm, RefKind};
use crate::generic::{kids, text};
use crate::refs::{import, path_of};

pub struct Scala;

impl LanguageModule for Scala {
    fn imports_of(&self, n: Node, src: &[u8]) -> Option<Vec<ImportFact>> {
        if n.kind() != "import_declaration" {
            return None;
        }
        let mut path = Vec::new();
        let mut cursor = n.walk();
        for (i, c) in n.children(&mut cursor).enumerate() {
            if n.field_name_for_child(i as u32) == Some("path") && c.is_named() {
                path.push(text(c, src));
            }
        }
        let base = path.join(".");
        let mut out = Vec::new();
        let mut selectors = false;
        for c in kids(n) {
            match c.kind() {
                "namespace_wildcard" => {
                    out.push(import(n, ImportForm::Wildcard, base.clone()));
                    selectors = true;
                }
                "namespace_selectors" => {
                    selectors = true;
                    let mut f = import(n, ImportForm::From, base.clone());
                    for s in kids(c) {
                        match s.kind() {
                            "identifier" => f.names.push((text(s, src), None)),
                            "arrow_renamed_identifier" | "as_renamed_identifier" => {
                                let name = s.child_by_field_name("name").map(|x| text(x, src));
                                let alias = s.child_by_field_name("alias").map(|x| text(x, src));
                                if let Some(name) = name {
                                    f.names.push((name, alias));
                                }
                            }
                            "namespace_wildcard" => {
                                out.push(import(n, ImportForm::Wildcard, base.clone()))
                            }
                            _ => {}
                        }
                    }
                    out.push(f);
                }
                _ => {}
            }
        }
        if !selectors && !base.is_empty() {
            out.push(dotted_import(n, &base, false, None));
        }
        Some(out)
    }

    fn package_of(&self, n: Node, src: &[u8]) -> Option<String> {
        (n.kind() == "package_clause")
            .then(|| n.child_by_field_name("name").map(|c| text(c, src)))
            .flatten()
    }

    fn heritage_of(&self, n: Node, src: &[u8]) -> Vec<(RefKind, Vec<String>)> {
        let Some(ext) = n.child_by_field_name("extend") else { return Vec::new() };
        let mut out = Vec::new();
        let mut cursor = ext.walk();
        let mut first = true;
        for (i, c) in ext.children(&mut cursor).enumerate() {
            if ext.field_name_for_child(i as u32) != Some("type") || !c.is_named() {
                continue;
            }
            if let Some(p) = path_of(c, src) {
                out.push((if first { RefKind::Inherits } else { RefKind::MixesIn }, p));
                first = false;
            }
        }
        out
    }
}
