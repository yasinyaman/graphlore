//! C and C++. Function names live in a declarator chain (handled by the generic
//! walk, prototypes included); `Session::Get` defined outside its class belongs
//! to `Session`. A `.h` file is C unless it reads as C++.

use std::sync::LazyLock;

use regex::bytes::Regex;
use tree_sitter::Node;

use super::{Lang, LanguageModule};
use crate::facts::DefKind;
use crate::generic;

pub struct CFamily;

/// `spans._CPP_HEADER_MARK`.
static CPP_HEADER_MARK: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?m)^[ \t]*(?:class\s+\w|namespace\b|template\s*<|using\s+namespace\b|(?:public|private|protected)\s*:)",
    )
    .expect("valid regex")
});

impl LanguageModule for CFamily {
    fn refine(&self, lang: Lang, rel: &str, src: &[u8]) -> Option<Lang> {
        if lang == Lang::C
            && rel.to_ascii_lowercase().ends_with(".h")
            && CPP_HEADER_MARK.is_match(src)
        {
            Some(Lang::Cpp)
        } else {
            None
        }
    }

    fn kind_of(&self, node: Node, _src: &[u8]) -> DefKind {
        match node.kind() {
            "class_specifier" => DefKind::Class,
            "union_specifier" => DefKind::Struct,
            k => generic::kind_of(k),
        }
    }
}
