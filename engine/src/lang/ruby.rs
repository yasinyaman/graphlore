//! Ruby. `module` is a definition (graphify makes a node of it too), and a
//! constant assigned a block-taking call (`Line = Struct.new(:a) do … end`) is a
//! class named after the constant. `require` / `require_relative` / `load` /
//! `autoload` are imports; `class A < B` extends `B`; `include`, `extend` and
//! `prepend` in a class or module body mix in.

use tree_sitter::Node;

use super::LanguageModule;
use crate::facts::{ImportFact, ImportForm, RefKind};
use crate::generic::{kids, text};
use crate::refs::{import, path_of, string_value};

pub struct Ruby;

fn method_name(n: Node, src: &[u8]) -> Option<String> {
    (n.kind() == "call" && n.child_by_field_name("receiver").is_none())
        .then(|| n.child_by_field_name("method").map(|m| text(m, src)))
        .flatten()
}

impl LanguageModule for Ruby {
    fn imports_of(&self, n: Node, src: &[u8]) -> Option<Vec<ImportFact>> {
        let m = method_name(n, src)?;
        if !matches!(m.as_str(), "require" | "require_relative" | "load" | "autoload") {
            return None;
        }
        let args = n.child_by_field_name("arguments")?;
        let lit = kids(args).into_iter().rfind(|a| a.kind() == "string")?;
        let mut f = import(n, ImportForm::Module, string_value(lit, src));
        f.system = m != "require_relative";
        Some(vec![f])
    }

    fn heritage_of(&self, n: Node, src: &[u8]) -> Vec<(RefKind, Vec<String>)> {
        if !matches!(n.kind(), "class" | "module" | "singleton_class") {
            return Vec::new();
        }
        let mut out = Vec::new();
        if let Some(sup) = n.child_by_field_name("superclass") {
            if let Some(p) = kids(sup).into_iter().next().and_then(|c| path_of(c, src)) {
                out.push((RefKind::Inherits, p));
            }
        }
        if let Some(body) = n.child_by_field_name("body") {
            for stmt in kids(body) {
                let Some(m) = method_name(stmt, src) else { continue };
                if !matches!(m.as_str(), "include" | "extend" | "prepend") {
                    continue;
                }
                if let Some(args) = stmt.child_by_field_name("arguments") {
                    for a in kids(args) {
                        if let Some(p) = path_of(a, src) {
                            out.push((RefKind::MixesIn, p));
                        }
                    }
                }
            }
        }
        out
    }

    fn builtins(&self) -> &'static [&'static str] {
        &[
            "puts",
            "print",
            "p",
            "pp",
            "raise",
            "require",
            "require_relative",
            "load",
            "include",
            "extend",
            "prepend",
            "attr_accessor",
            "attr_reader",
            "attr_writer",
            "private",
            "protected",
            "public",
            "lambda",
            "proc",
            "loop",
            "format",
            "sleep",
            "rand",
            "catch",
            "throw",
            "binding",
            "block_given?",
            "yield",
            "fail",
            "warn",
            "freeze",
            "new",
        ]
    }
}
