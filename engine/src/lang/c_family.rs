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
    /// C++ `using AsyncResponse = AsyncWrapper<Response>;`: a type alias (no
    /// hint word in `alias_declaration`, so no span of `spans.py`'s rule).
    fn extra_def<'t>(&self, n: Node<'t>, _src: &[u8]) -> Option<(Node<'t>, DefKind)> {
        (n.kind() == "alias_declaration")
            .then(|| n.child_by_field_name("name"))
            .flatten()
            .map(|name| (name, DefKind::TypeAlias))
    }

    /// Variables at file or namespace scope (not `extern` ones, which name a
    /// variable defined elsewhere), and `static` data members of a class.
    fn state_defs<'t>(&self, n: Node<'t>, src: &[u8]) -> Vec<(Node<'t>, Node<'t>, DefKind)> {
        let words = |n: Node| -> Vec<String> {
            kids(n)
                .into_iter()
                .filter(|c| matches!(c.kind(), "storage_class_specifier" | "type_qualifier"))
                .map(|c| generic::text(c, src))
                .collect()
        };
        let in_class = n.kind() == "field_declaration"
            && n.parent().is_some_and(|p| p.kind() == "field_declaration_list");
        let at_file = n.kind() == "declaration"
            && n.parent().is_some_and(|p| {
                p.kind() == "translation_unit"
                    || (p.kind() == "declaration_list"
                        && p.parent().is_some_and(|q| q.kind() == "namespace_definition"))
            });
        if !(in_class || at_file) {
            return Vec::new();
        }
        let w = words(n);
        if w.iter().any(|x| x == "extern") {
            return Vec::new();
        }
        let constant = w.iter().any(|x| x == "const" || x == "constexpr");
        let kind = match (in_class, constant) {
            (_, true) => DefKind::Constant,
            (true, false) => DefKind::Property,
            (false, false) => DefKind::Variable,
        };
        let mut out = Vec::new();
        let mut cursor = n.walk();
        for d in n.children_by_field_name("declarator", &mut cursor) {
            // `int x`, `int x = 1`, `int *p`, `int a[3]`; never a function
            let mut cur = d;
            for _ in 0..4 {
                match cur.kind() {
                    "identifier" | "field_identifier" => {
                        out.push((n, cur, kind));
                        break;
                    }
                    "init_declarator"
                    | "pointer_declarator"
                    | "array_declarator"
                    | "reference_declarator" => {
                        match cur.child_by_field_name("declarator").or_else(|| cur.named_child(0)) {
                            Some(next) => cur = next,
                            None => break,
                        }
                    }
                    _ => break,
                }
            }
        }
        out
    }

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
