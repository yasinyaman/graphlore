//! JavaScript, TypeScript and TSX. The generic rule already covers classes,
//! methods, functions and anonymous functions bound to a name (`const f = () =>`,
//! object keys, class fields, `exports.send = function`); TypeScript namespaces
//! and ambient `declare module` blocks only prefix names.

use tree_sitter::Node;

use super::LanguageModule;
use crate::facts::DefKind;
use crate::generic::{self, text};

pub struct Ecma;

impl LanguageModule for Ecma {
    fn kind_of(&self, node: Node, src: &[u8]) -> DefKind {
        match node.kind() {
            "internal_module" | "module" => DefKind::Namespace,
            "method_definition"
                if node.child_by_field_name("name").map(|n| text(n, src)).as_deref()
                    == Some("constructor") =>
            {
                DefKind::Constructor
            }
            k => generic::kind_of(k),
        }
    }
}
