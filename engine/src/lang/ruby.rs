//! Ruby. `module` is a definition (graphify makes a node of it too), and a
//! constant assigned a block-taking call (`Line = Struct.new(:a) do … end`) is a
//! class named after the constant. `require` / `require_relative` / `load` /
//! `autoload` are imports; `class A < B` extends `B`; `include`, `extend` and
//! `prepend` in a class or module body mix in.

use crate::parse::Fields;
use crate::parse::Kinds;
use tree_sitter::Node;

use super::LanguageModule;
use crate::facts::{DefKind, ImportFact, ImportForm, RefKind};
use crate::generic::{kids, text};
use crate::refs::{import, path_of, string_value};

pub struct Ruby;

fn method_name(n: Node, src: &[u8]) -> Option<String> {
    (n.kind_str() == "call" && n.by_field("receiver").is_none())
        .then(|| n.by_field("method").map(|m| text(m, src)))
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
        n.by_field("block")?;
        let named = n
            .by_field("arguments")
            .and_then(|a| kids(a).into_iter().next())
            .filter(|a| a.kind_str() == "simple_symbol");
        match named {
            Some(sym) => Some((sym, DefKind::Function)),
            None if m.starts_with("subject") => {
                n.by_field("method").map(|x| (x, DefKind::Function))
            }
            None => None,
        }
    }

    /// `config.include Faraday::IntegrationHelpers` in `RSpec.configure`.
    fn spec_includes(&self, n: Node, src: &[u8]) -> Vec<Vec<String>> {
        if n.kind_str() != "call"
            || n.by_field("method").map(|x| text(x, src)).as_deref() != Some("include")
            || n.by_field("receiver").map(|x| text(x, src)).as_deref() != Some("config")
        {
            return Vec::new();
        }
        let Some(args) = n.by_field("arguments") else { return Vec::new() };
        kids(args)
            .into_iter()
            .filter(|a| matches!(a.kind_str(), "constant" | "scope_resolution"))
            .filter_map(|a| path_of(a, src))
            .collect()
    }

    /// `$hits = 0` and `MAX = 3` at the top level, and `@@total = 0` / `LIMIT
    /// = 2` in a class or module body.
    fn state_defs<'t>(&self, n: Node<'t>, _src: &[u8]) -> Vec<(Node<'t>, Node<'t>, DefKind)> {
        if n.kind_str() != "assignment" {
            return Vec::new();
        }
        let Some(left) = n.by_field("left") else { return Vec::new() };
        let scope = n.parent().map(|p| (p.kind_str(), p.parent().map(|q| q.kind_str())));
        let top = matches!(scope, Some(("program", _)));
        let in_type = matches!(scope, Some(("body_statement", Some("class" | "module"))));
        let in_method = {
            let mut p = n.parent();
            let mut found = false;
            while let Some(x) = p {
                if x.kind_str() == "method" {
                    found = true;
                    break;
                }
                p = x.parent();
            }
            found
        };
        let kind = match left.kind_str() {
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
        let args = n.by_field("arguments")?;
        let lit = kids(args).into_iter().rfind(|a| a.kind_str() == "string")?;
        let mut f = import(n, ImportForm::Module, string_value(lit, src));
        f.system = m != "require_relative";
        Some(vec![f])
    }

    fn heritage_of(&self, n: Node, src: &[u8]) -> Vec<(RefKind, Vec<String>)> {
        // `Request = Struct.new(:path) do extend Registry … end`: the class
        // named after the constant mixes in what its block says
        let struct_block = (n.kind_str() == "assignment"
            && n.by_field("left").is_some_and(|l| l.kind_str() == "constant"))
        .then(|| n.by_field("right"))
        .flatten()
        .filter(|r| r.kind_str() == "call")
        .and_then(|c| c.by_field("block"))
        .filter(|b| b.kind_str() == "do_block");
        if struct_block.is_none() && !matches!(n.kind_str(), "class" | "module" | "singleton_class")
        {
            return Vec::new();
        }
        let mut out = Vec::new();
        if let Some(sup) = n.by_field("superclass") {
            if let Some(p) = kids(sup).into_iter().next().and_then(|c| path_of(c, src)) {
                out.push((RefKind::Inherits, p));
            }
        }
        if let Some(body) = struct_block.unwrap_or(n).by_field("body") {
            for stmt in kids(body) {
                let Some(m) = method_name(stmt, src) else { continue };
                if !matches!(m.as_str(), "include" | "extend" | "prepend") {
                    continue;
                }
                if let Some(args) = stmt.by_field("arguments") {
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
        // a bare `super` calls the method of the same name further up (with
        // arguments it is a `call`, read by the generic rule)
        if n.kind_str() == "super" && n.parent().is_some_and(|p| p.kind_str() != "call") {
            return Some(vec![(RefKind::Call, vec!["super".into()], "implicit_call")]);
        }
        if n.kind_str() != "identifier" {
            return None;
        }
        let p = n.parent()?;
        // `items.each { … }`: the receiver is a method of `self` called first
        if p.kind_str() == "call" && p.by_field("receiver").is_some_and(|r| r.id() == n.id()) {
            return Some(vec![(RefKind::Call, vec![text(n, src)], "implicit_call")]);
        }
        let is_target = p.by_field("left").is_some_and(|l| l.id() == n.id())
            || matches!(
                p.kind_str(),
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
                    | "exception_variable"
                    | "for"
                    | "alias"
                    | "undef"
            )
            || p.kind_str().ends_with("_parameters")
            // (a hash's key names nothing; its value is a value like any other:
            // `{ status: status }` calls `status`)
            || (p.kind_str() == "pair" && p.by_field("value").is_none_or(|v| v.id() != n.id()));
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
