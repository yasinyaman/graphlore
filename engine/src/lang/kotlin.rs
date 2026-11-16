//! Kotlin (the fwcd grammar, as `tree-sitter-kotlin-sg`). It has no `name`
//! fields: the generic walk names a declaration by its identifier child. One
//! `class_declaration` covers classes, interfaces and enum classes.

use tree_sitter::Node;

use super::LanguageModule;
use crate::facts::DefKind;
use crate::generic::{self, all_kids, text};

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
}
