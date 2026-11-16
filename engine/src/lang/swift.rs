//! Swift. One `class_declaration` covers class, struct, enum, actor and
//! extension; an extension adds members to a type defined elsewhere, so it is
//! no node of its own and its members belong to that type.

use tree_sitter::Node;

use super::LanguageModule;
use crate::facts::DefKind;
use crate::generic::{self, all_kids, text};

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
}
