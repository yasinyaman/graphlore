//! C and C++. Function names live in a declarator chain (handled by the generic
//! walk, prototypes included); `Session::Get` defined outside its class belongs
//! to `Session`. A `.h` file is C unless it reads as C++. `#include "x"` names
//! a file, `#include <x>` a system header (or a project one on the include
//! path); `class A : public B` makes `A` extend `B`.

use std::sync::LazyLock;

use regex::bytes::Regex;
use tree_sitter::Node;

use super::{Lang, LanguageModule};
use crate::facts::{DefKind, ImportFact, ImportForm, RefKind};
use crate::generic::{self, kids};
use crate::refs::{import, path_of, string_value};

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

    fn imports_of(&self, n: Node, src: &[u8]) -> Option<Vec<ImportFact>> {
        if n.kind() != "preproc_include" {
            return None;
        }
        let p = n.child_by_field_name("path")?;
        let mut f = import(n, ImportForm::Module, string_value(p, src));
        f.system = p.kind() == "system_lib_string";
        Some(vec![f])
    }

    fn heritage_of(&self, n: Node, src: &[u8]) -> Vec<(RefKind, Vec<String>)> {
        kids(n)
            .into_iter()
            .filter(|c| c.kind() == "base_class_clause")
            .flat_map(kids)
            .filter(|c| c.kind() != "access_specifier")
            .filter_map(|c| path_of(c, src))
            .map(|p| (RefKind::Inherits, p))
            .collect()
    }

    fn builtins(&self) -> &'static [&'static str] {
        &[
            "printf", "fprintf", "sprintf", "snprintf", "malloc", "calloc", "realloc", "free",
            "memcpy", "memmove", "memset", "memcmp", "strlen", "strcmp", "strncmp", "strcpy",
            "strncpy", "strcat", "assert", "sizeof", "exit", "abort", "abs", "puts", "fopen",
            "fclose", "fread", "fwrite",
        ]
    }
}
