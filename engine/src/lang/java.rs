//! Java. Anonymous classes (`new Callback() { … }`) are named after the type
//! they implement by the generic walk; `@interface` declarations are interfaces.

use tree_sitter::Node;

use super::LanguageModule;
use crate::facts::DefKind;
use crate::generic;

pub struct Java;

impl LanguageModule for Java {
    fn kind_of(&self, node: Node, _src: &[u8]) -> DefKind {
        match node.kind() {
            "annotation_type_declaration" => DefKind::Interface,
            k => generic::kind_of(k),
        }
    }
}
