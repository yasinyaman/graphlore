//! Go. Methods are named `Receiver.Method` (the generic walk reads the
//! `receiver` field), and a `type_spec` is a struct, an interface or an alias
//! depending on the type it declares.

use tree_sitter::Node;

use super::LanguageModule;
use crate::facts::DefKind;
use crate::generic;

pub struct Go;

impl LanguageModule for Go {
    fn kind_of(&self, node: Node, _src: &[u8]) -> DefKind {
        match node.kind() {
            "type_spec" => match node.child_by_field_name("type").map(|t| t.kind()) {
                Some("struct_type") => DefKind::Struct,
                Some("interface_type") => DefKind::Interface,
                _ => DefKind::TypeAlias,
            },
            "type_alias" => DefKind::TypeAlias,
            k => generic::kind_of(k),
        }
    }
}
