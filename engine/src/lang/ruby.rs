//! Ruby. `module` is a definition (graphify makes a node of it too), and a
//! constant assigned a block-taking call (`Line = Struct.new(:a) do … end`) is a
//! class named after the constant. `require` / `require_relative` / `load` /
//! `autoload` are imports; `class A < B` extends `B`; `include`, `extend` and
//! `prepend` in a class or module body mix in.

use tree_sitter::Node;

use super::LanguageModule;
use crate::facts::{DefKind, ImportFact, ImportForm, RefKind};
use crate::generic::{kids, text};
use crate::refs::{import, path_of, string_value};

pub struct Ruby;

fn method_name(n: Node, src: &[u8]) -> Option<String> {
    (n.kind() == "call" && n.child_by_field_name("receiver").is_none())
        .then(|| n.child_by_field_name("method").map(|m| text(m, src)))
        .flatten()
}

impl LanguageModule for Ruby {
    fn assignment_declares(&self) -> bool {
        true
    }

    /// RSpec `let(:conn) { … }`, `let!`, `subject(:request) { … }` and a bare
    /// `subject { … }`: a method the spec defines, named by the symbol.
    fn extra_def<'t>(&self, n: Node<'t>, src: &[u8]) -> Option<(Node<'t>, DefKind)> {
        let m = method_name(n, src)?;
        if !matches!(m.as_str(), "let" | "let!" | "subject" | "subject!") {
            return None;
        }
        n.child_by_field_name("block")?;
        let named = n
            .child_by_field_name("arguments")
            .and_then(|a| kids(a).into_iter().next())
            .filter(|a| a.kind() == "simple_symbol");
        match named {
            Some(sym) => Some((sym, DefKind::Function)),
            None if m.starts_with("subject") => {
                n.child_by_field_name("method").map(|x| (x, DefKind::Function))
            }
            None => None,
        }
    }

    /// `config.include Faraday::IntegrationHelpers` in `RSpec.configure`.
    fn spec_includes(&self, n: Node, src: &[u8]) -> Vec<Vec<String>> {
        if n.kind() != "call"
            || n.child_by_field_name("method").map(|x| text(x, src)).as_deref() != Some("include")
            || n.child_by_field_name("receiver").map(|x| text(x, src)).as_deref() != Some("config")
        {
            return Vec::new();
        }
        let Some(args) = n.child_by_field_name("arguments") else { return Vec::new() };
        kids(args)
            .into_iter()
            .filter(|a| matches!(a.kind(), "constant" | "scope_resolution"))
            .filter_map(|a| path_of(a, src))
            .collect()
    }

    /// `$hits = 0` and `MAX = 3` at the top level, and `@@total = 0` / `LIMIT
    /// = 2` in a class or module body.
    fn state_defs<'t>(&self, n: Node<'t>, _src: &[u8]) -> Vec<(Node<'t>, Node<'t>, DefKind)> {
        if n.kind() != "assignment" {
            return Vec::new();
        }
        let Some(left) = n.child_by_field_name("left") else { return Vec::new() };
        let scope = n.parent().map(|p| (p.kind(), p.parent().map(|q| q.kind())));
        let top = matches!(scope, Some(("program", _)));
        let in_type = matches!(scope, Some(("body_statement", Some("class" | "module"))));
        let in_method = {
            let mut p = n.parent();
            let mut found = false;
            while let Some(x) = p {
                if x.kind() == "method" {
                    found = true;
                    break;
                }
                p = x.parent();
            }
            found
        };
        let kind = match left.kind() {
            "instance_variable" if in_method => DefKind::Property,
            "global_variable" if top => DefKind::Variable,
            "constant" if top || in_type => DefKind::Constant,
            "class_variable" if in_type => DefKind::Property,
            _ => return Vec::new(),
        };
        vec![(n, left, kind)]
    }

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
        // `Request = Struct.new(:path) do extend Registry … end`: the class
        // named after the constant mixes in what its block says
        let struct_block = (n.kind() == "assignment"
            && n.child_by_field_name("left").is_some_and(|l| l.kind() == "constant"))
        .then(|| n.child_by_field_name("right"))
        .flatten()
        .filter(|r| r.kind() == "call")
        .and_then(|c| c.child_by_field_name("block"))
        .filter(|b| b.kind() == "do_block");
        if struct_block.is_none() && !matches!(n.kind(), "class" | "module" | "singleton_class") {
            return Vec::new();
        }
        let mut out = Vec::new();
        if let Some(sup) = n.child_by_field_name("superclass") {
            if let Some(p) = kids(sup).into_iter().next().and_then(|c| path_of(c, src)) {
                out.push((RefKind::Inherits, p));
            }
        }
        if let Some(body) = struct_block.unwrap_or(n).child_by_field_name("body") {
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

    /// A bare identifier that is no local: a method of `self` called without
    /// parentheses or arguments (`current_user`, `raise_if_locked`).
    fn refs_of(&self, n: Node, src: &[u8]) -> Option<Vec<(RefKind, Vec<String>, &'static str)>> {
        if n.kind() != "identifier" {
            return None;
        }
        let p = n.parent()?;
        // `items.each { … }`: the receiver is a method of `self` called first
        if p.kind() == "call" && p.child_by_field_name("receiver").is_some_and(|r| r.id() == n.id())
        {
            return Some(vec![(RefKind::Call, vec![text(n, src)], "implicit_call")]);
        }
        let is_target = p.child_by_field_name("left").is_some_and(|l| l.id() == n.id())
            || matches!(
                p.kind(),
                "method_parameters"
                    | "block_parameters"
                    | "lambda_parameters"
                    | "optional_parameter"
                    | "keyword_parameter"
                    | "splat_parameter"
                    | "hash_splat_parameter"
                    | "block_parameter"
                    | "method"
                    | "singleton_method"
                    | "setter"
                    | "call"
                    | "argument_list"
                    | "pair"
                    | "exception_variable"
                    | "for"
                    | "alias"
                    | "undef"
            )
            || p.kind().ends_with("_parameters");
        if is_target {
            // `call` children are its receiver / method name (read by the
            // generic rule); arguments are values, not calls
            return Some(Vec::new());
        }
        Some(vec![(RefKind::Call, vec![text(n, src)], "implicit_call")])
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
