//! Swift. One `class_declaration` covers class, struct, enum, actor and
//! extension; an extension adds members to a type defined elsewhere, so it is
//! no node of its own and its members belong to that type. `import M` is a
//! module; a type's bases do not say which is the class.

use tree_sitter::Node;

use super::LanguageModule;
use crate::facts::{DefKind, ImportFact, ImportForm, RefKind};
use crate::generic::{self, all_kids, kids, text};
use crate::refs::{import, path_of};

pub struct Swift;

impl LanguageModule for Swift {
    fn kind_of(&self, node: Node, src: &[u8]) -> DefKind {
        if node.kind() == "class_declaration" {
            let keyword =
                node.child_by_field_name("declaration_kind").map(|k| text(k, src)).or_else(|| {
                    all_kids(node).into_iter().map(|c| c.kind().to_string()).find(|k| {
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
        generic::kind_of(node.kind())
    }

    fn imports_of(&self, n: Node, src: &[u8]) -> Option<Vec<ImportFact>> {
        if n.kind() != "import_declaration" {
            return None;
        }
        let name = kids(n).into_iter().find(|c| c.kind() == "identifier").map(|c| text(c, src))?;
        Some(vec![import(n, ImportForm::Module, name)])
    }

    fn heritage_of(&self, n: Node, src: &[u8]) -> Vec<(RefKind, Vec<String>)> {
        kids(n)
            .into_iter()
            .filter(|c| c.kind() == "inheritance_specifier")
            .filter_map(|c| {
                c.child_by_field_name("inherits_from").or_else(|| kids(c).into_iter().next())
            })
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
