//! Swift. One `class_declaration` covers class, struct, enum, actor and
//! extension; an extension adds members to a type defined elsewhere, so it is
//! no node of its own and its members belong to that type. `import M` is a
//! module; a type's bases do not say which is the class.

use crate::parse::Fields;
use crate::parse::Kinds;
use tree_sitter::Node;

use super::LanguageModule;
use crate::facts::{DefKind, ImportFact, ImportForm, RefKind};
use crate::generic::{self, all_kids, kids, text};
use crate::refs::{import, path_of};

pub struct Swift;

impl LanguageModule for Swift {
    /// Top-level `var` / `let` (properties of a type are `extra_def`'s).
    fn state_defs<'t>(&self, n: Node<'t>, src: &[u8]) -> Vec<(Node<'t>, Node<'t>, DefKind)> {
        if n.kind_str() != "property_declaration"
            || !n.parent().is_some_and(|p| p.kind_str() == "source_file")
        {
            return Vec::new();
        }
        let is_let = kids(n)
            .iter()
            .any(|c| c.kind_str() == "value_binding_pattern" && text(*c, src) == "let");
        let kind = if is_let { DefKind::Constant } else { DefKind::Variable };
        let name = n
            .by_field("name")
            .and_then(|p| p.by_field("bound_identifier").or_else(|| kids(p).into_iter().next()));
        name.map(|x| vec![(n, x, kind)]).unwrap_or_default()
    }

    fn kind_of(&self, node: Node, src: &[u8]) -> DefKind {
        if node.kind_str() == "class_declaration" {
            let keyword = node.by_field("declaration_kind").map(|k| text(k, src)).or_else(|| {
                all_kids(node).into_iter().map(|c| c.kind_str().to_string()).find(|k| {
                    matches!(k.as_str(), "class" | "struct" | "enum" | "extension" | "actor")
                })
            });
            return match keyword.as_deref() {
                Some("struct") => DefKind::Struct,
                Some("enum") => DefKind::Enum,
                Some("extension") => DefKind::Extension,
                _ => DefKind::Class,
            };
        }
        generic::kind_of(node.kind_str())
    }

    fn extra_def<'t>(&self, n: Node<'t>, _src: &[u8]) -> Option<(Node<'t>, DefKind)> {
        // a stored or computed property of a type (not a local `let`)
        if n.kind_str() != "property_declaration" && n.kind_str() != "protocol_property_declaration"
        {
            return None;
        }
        let in_type = n.parent().is_some_and(|p| {
            matches!(p.kind_str(), "class_body" | "protocol_body" | "enum_class_body")
        });
        if !in_type {
            return None;
        }
        let pattern = n.by_field("name")?;
        let name = if pattern.kind_str() == "pattern" {
            pattern.by_field("bound_identifier").or_else(|| kids(pattern).into_iter().next())?
        } else {
            pattern
        };
        Some((name, DefKind::Property))
    }

    fn imports_of(&self, n: Node, src: &[u8]) -> Option<Vec<ImportFact>> {
        if n.kind_str() != "import_declaration" {
            return None;
        }
        let name =
            kids(n).into_iter().find(|c| c.kind_str() == "identifier").map(|c| text(c, src))?;
        Some(vec![import(n, ImportForm::Module, name)])
    }

    fn heritage_of(&self, n: Node, src: &[u8]) -> Vec<(RefKind, Vec<String>)> {
        kids(n)
            .into_iter()
            .filter(|c| c.kind_str() == "inheritance_specifier")
            .filter_map(|c| c.by_field("inherits_from").or_else(|| kids(c).into_iter().next()))
            .filter_map(|t| path_of(t, src))
            .map(|p| (RefKind::Base, p))
            .collect()
    }

    fn builtins(&self) -> &'static [&'static str] {
        &[
            "print",
            "debugPrint",
            "fatalError",
            "precondition",
            "preconditionFailure",
            "assert",
            "assertionFailure",
            "min",
            "max",
            "abs",
            "zip",
            "stride",
            "type",
            "withExtendedLifetime",
        ]
    }
}
