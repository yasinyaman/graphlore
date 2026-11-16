//! C#. The generic rule covers types, members and enum members; block
//! namespaces only prefix names, and a file-scoped `namespace X;` is the
//! file's package. `using N;` brings a namespace's types into scope,
//! `using A = N.T;` names one, `using static N.T;` brings a type's members. A
//! base list does not say which base is the class: the target decides.

use tree_sitter::Node;

use super::LanguageModule;
use crate::facts::{DefKind, ImportFact, ImportForm, RefKind};
use crate::generic::{all_kids, kids, text};
use crate::refs::{import, path_of};

pub struct CSharp;

impl LanguageModule for CSharp {
    /// `static` fields (shared by the whole program) and `const` / `static
    /// readonly` ones (constants).
    fn state_defs<'t>(&self, n: Node<'t>, src: &[u8]) -> Vec<(Node<'t>, Node<'t>, DefKind)> {
        if n.kind() != "field_declaration" {
            return Vec::new();
        }
        let mods: Vec<String> =
            kids(n).into_iter().filter(|c| c.kind() == "modifier").map(|c| text(c, src)).collect();
        let has = |w: &str| mods.iter().any(|m| m == w);
        let kind = if has("const") || (has("static") && has("readonly")) {
            DefKind::Constant
        } else if has("static") {
            DefKind::Property
        } else {
            return Vec::new();
        };
        let Some(decl) = kids(n).into_iter().find(|c| c.kind() == "variable_declaration") else {
            return Vec::new();
        };
        kids(decl)
            .into_iter()
            .filter(|c| c.kind() == "variable_declarator")
            .filter_map(|d| d.child_by_field_name("name"))
            .map(|name| (n, name, kind))
            .collect()
    }

    fn imports_of(&self, n: Node, src: &[u8]) -> Option<Vec<ImportFact>> {
        if n.kind() != "using_directive" {
            return None;
        }
        let alias = n.child_by_field_name("name").map(|a| text(a, src));
        let target = kids(n)
            .into_iter()
            .filter(|c| matches!(c.kind(), "qualified_name" | "identifier" | "generic_name"))
            .find(|c| n.child_by_field_name("name").is_none_or(|a| a.id() != c.id()))?;
        let module = text(target, src);
        let is_static = all_kids(n).iter().any(|c| c.kind() == "static");
        let mut f = match alias {
            Some(a) => {
                let mut f = import(n, ImportForm::Module, module);
                f.alias = Some(a);
                f
            }
            None => import(n, ImportForm::Wildcard, module),
        };
        f.is_static = is_static;
        Some(vec![f])
    }

    fn package_of(&self, n: Node, src: &[u8]) -> Option<String> {
        (n.kind() == "file_scoped_namespace_declaration")
            .then(|| n.child_by_field_name("name").map(|c| text(c, src)))
            .flatten()
    }

    fn extra_def<'t>(&self, n: Node<'t>, _src: &[u8]) -> Option<(Node<'t>, DefKind)> {
        (n.kind() == "property_declaration")
            .then(|| n.child_by_field_name("name").map(|x| (x, DefKind::Property)))
            .flatten()
    }

    fn heritage_of(&self, n: Node, src: &[u8]) -> Vec<(RefKind, Vec<String>)> {
        kids(n)
            .into_iter()
            .filter(|c| c.kind() == "base_list")
            .flat_map(kids)
            .filter_map(|c| path_of(c, src))
            .map(|p| (RefKind::Base, p))
            .collect()
    }
}
