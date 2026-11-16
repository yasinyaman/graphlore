//! Java. Anonymous classes (`new Callback() { … }`) are named after the type
//! they implement by the generic walk; `@interface` declarations are interfaces.
//! Imports name a type (`import a.b.C` is `C` from package `a.b`), a package
//! (`a.b.*`) or a static member (`import static a.b.C.m`).

use tree_sitter::Node;

use super::{paths_in, LanguageModule};
use crate::facts::{DefKind, ImportFact, ImportForm, RefKind};
use crate::generic::{self, all_kids, kids, text};
use crate::refs::import;

pub struct Java;

/// `a.b.C` / `a.b.*` / `static a.b.C.m` as an import fact (shared with Kotlin
/// and Scala, whose imports read the same).
pub fn dotted_import(n: Node, path: &str, wildcard: bool, alias: Option<String>) -> ImportFact {
    if wildcard {
        return import(n, ImportForm::Wildcard, path);
    }
    let (module, last) = path.rsplit_once('.').unwrap_or(("", path));
    let mut f = import(n, ImportForm::From, module);
    f.names.push((last.to_string(), alias));
    f
}

impl LanguageModule for Java {
    /// `static` fields: one value the whole program shares (`final` ones are
    /// constants).
    fn state_defs<'t>(&self, n: Node<'t>, src: &[u8]) -> Vec<(Node<'t>, Node<'t>, DefKind)> {
        if n.kind() != "field_declaration" {
            return Vec::new();
        }
        let mods = kids(n)
            .into_iter()
            .find(|c| c.kind() == "modifiers")
            .map(|m| text(m, src))
            .unwrap_or_default();
        let words: Vec<&str> = mods.split_whitespace().collect();
        if !words.contains(&"static") {
            return Vec::new();
        }
        let kind = if words.contains(&"final") { DefKind::Constant } else { DefKind::Property };
        let mut cursor = n.walk();
        n.children_by_field_name("declarator", &mut cursor)
            .filter_map(|d| d.child_by_field_name("name"))
            .map(|name| (n, name, kind))
            .collect()
    }

    fn kind_of(&self, node: Node, _src: &[u8]) -> DefKind {
        match node.kind() {
            "annotation_type_declaration" => DefKind::Interface,
            k => generic::kind_of(k),
        }
    }

    fn imports_of(&self, n: Node, src: &[u8]) -> Option<Vec<ImportFact>> {
        if n.kind() != "import_declaration" {
            return None;
        }
        let ks = kids(n);
        let path = ks
            .iter()
            .find(|c| matches!(c.kind(), "scoped_identifier" | "identifier"))
            .map(|c| text(*c, src))?;
        let wildcard = ks.iter().any(|c| c.kind() == "asterisk");
        let mut f = dotted_import(n, &path, wildcard, None);
        f.is_static = all_kids(n).iter().any(|c| c.kind() == "static");
        Some(vec![f])
    }

    fn package_of(&self, n: Node, src: &[u8]) -> Option<String> {
        if n.kind() != "package_declaration" {
            return None;
        }
        kids(n)
            .into_iter()
            .find(|c| matches!(c.kind(), "scoped_identifier" | "identifier"))
            .map(|c| text(c, src))
    }

    fn heritage_of(&self, n: Node, src: &[u8]) -> Vec<(RefKind, Vec<String>)> {
        let mut out = Vec::new();
        for c in kids(n) {
            let (kind, list) = match c.kind() {
                "superclass" => (RefKind::Inherits, c),
                "super_interfaces" => (RefKind::Implements, c),
                "extends_interfaces" => (RefKind::Inherits, c),
                _ => continue,
            };
            for item in kids(list) {
                let items = if item.kind() == "type_list" {
                    paths_in(item, src)
                } else {
                    crate::refs::path_of(item, src).into_iter().collect()
                };
                out.extend(items.into_iter().map(|p| (kind, p)));
            }
        }
        out
    }
}
