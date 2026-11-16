//! JavaScript, TypeScript and TSX. The generic rule already covers classes,
//! methods, functions and anonymous functions bound to a name (`const f = () =>`,
//! object keys, class fields, `exports.send = function`); TypeScript namespaces
//! and ambient `declare module` blocks only prefix names.
//!
//! Imports: ES `import`, `export … from` (re-exports), `require(...)` (bound by
//! the declaration around it) and `import()` (dynamic). `import type` is
//! type-checking only.

use tree_sitter::Node;

use super::{paths_in, LanguageModule};
use crate::facts::{DefKind, ImportFact, ImportForm, RefKind, Runs};
use crate::generic::{self, all_kids, kids, text};
use crate::refs::{import, path_of, string_value};

pub struct Ecma;

const BUILTINS: &[&str] = &[
    "require",
    "setTimeout",
    "setInterval",
    "clearTimeout",
    "clearInterval",
    "setImmediate",
    "parseInt",
    "parseFloat",
    "isNaN",
    "isFinite",
    "String",
    "Number",
    "Boolean",
    "Object",
    "Array",
    "Symbol",
    "Promise",
    "Error",
    "TypeError",
    "RangeError",
    "Date",
    "RegExp",
    "Map",
    "Set",
    "WeakMap",
    "WeakSet",
    "BigInt",
    "encodeURIComponent",
    "decodeURIComponent",
    "encodeURI",
    "decodeURI",
    "structuredClone",
    "fetch",
    "queueMicrotask",
    "describe",
    "it",
    "test",
    "expect",
    "beforeEach",
    "afterEach",
    "beforeAll",
    "afterAll",
    "jest",
    "vi",
];

fn source(n: Node, src: &[u8]) -> Option<String> {
    n.child_by_field_name("source").map(|s| string_value(s, src))
}

fn specifiers(list: Node, src: &[u8]) -> Vec<(String, Option<String>)> {
    kids(list)
        .into_iter()
        .filter(|c| c.kind().ends_with("specifier"))
        .filter_map(|c| {
            let name = c.child_by_field_name("name").map(|x| text(x, src))?;
            let alias = c.child_by_field_name("alias").map(|x| text(x, src));
            Some((name, alias))
        })
        .collect()
}

/// `require('m')` and `import('m')`, bound by the declaration they sit in.
fn call_import(n: Node, src: &[u8]) -> Option<Vec<ImportFact>> {
    let f = n.child_by_field_name("function")?;
    let args = n.child_by_field_name("arguments")?;
    let first = kids(args).into_iter().next()?;
    if !matches!(first.kind(), "string" | "template_string") {
        return None;
    }
    let module = string_value(first, src);
    if f.kind() == "import" {
        return Some(vec![import(n, ImportForm::Dynamic, module)]);
    }
    if f.kind() != "identifier" || text(f, src) != "require" {
        return None;
    }
    // `const x = require('m')`, `const {a, b: c} = require('m')`,
    // `const y = require('m').y`
    let mut holder = n.parent();
    let mut member = None;
    if let Some(p) = holder {
        if p.kind() == "member_expression" {
            member = p.child_by_field_name("property").map(|x| text(x, src));
            holder = p.parent();
        }
    }
    let target = holder
        .filter(|h| h.kind() == "variable_declarator" || h.kind() == "assignment_expression")
        .and_then(|h| h.child_by_field_name("name").or_else(|| h.child_by_field_name("left")));
    let mut fact = import(n, ImportForm::Module, module);
    match (target, member) {
        (Some(t), Some(m)) if t.kind() == "identifier" => {
            fact.form = ImportForm::From;
            fact.names.push((m, Some(text(t, src))));
        }
        (Some(t), None) if t.kind() == "identifier" => fact.alias = Some(text(t, src)),
        (Some(t), None) if t.kind() == "object_pattern" => {
            fact.form = ImportForm::From;
            for p in kids(t) {
                match p.kind() {
                    "shorthand_property_identifier_pattern" => {
                        fact.names.push((text(p, src), None))
                    }
                    "pair_pattern" => {
                        let k = p.child_by_field_name("key").map(|x| text(x, src));
                        let v = p.child_by_field_name("value").map(|x| text(x, src));
                        if let Some(k) = k {
                            fact.names.push((k, v));
                        }
                    }
                    _ => {}
                }
            }
        }
        _ => {}
    }
    Some(vec![fact])
}

impl LanguageModule for Ecma {
    fn needs_import(&self) -> bool {
        true
    }

    /// Module-level `let` / `var` (a `const` is `extra_def`'s constant).
    fn state_defs<'t>(&self, n: Node<'t>, src: &[u8]) -> Vec<(Node<'t>, Node<'t>, DefKind)> {
        // `this.x = …` in a method of a class: a field of the class
        if n.kind() == "assignment_expression" {
            let field = n
                .child_by_field_name("left")
                .filter(|l| l.kind() == "member_expression")
                .filter(|l| l.child_by_field_name("object").is_some_and(|o| o.kind() == "this"))
                .and_then(|l| l.child_by_field_name("property"))
                .filter(|p| p.kind() == "property_identifier");
            let mut p = n.parent();
            let mut in_class = false;
            while let Some(x) = p {
                if x.kind() == "method_definition" {
                    in_class = x.parent().is_some_and(|b| b.kind() == "class_body");
                    break;
                }
                p = x.parent();
            }
            return match field {
                Some(name) if in_class => vec![(n, name, DefKind::Property)],
                _ => Vec::new(),
            };
        }
        if n.kind() != "variable_declarator" {
            return Vec::new();
        }
        let Some(decl) = n
            .parent()
            .filter(|p| matches!(p.kind(), "lexical_declaration" | "variable_declaration"))
        else {
            return Vec::new();
        };
        let top = decl.parent().is_some_and(|p| {
            p.kind() == "program"
                || (p.kind() == "export_statement"
                    && p.parent().is_some_and(|q| q.kind() == "program"))
        });
        let is_const = all_kids(decl).iter().any(|c| c.kind() == "const");
        let value = n.child_by_field_name("value");
        let function = value.is_some_and(|v| {
            matches!(
                v.kind(),
                "arrow_function"
                    | "function_expression"
                    | "function"
                    | "generator_function"
                    | "class"
            )
        });
        // `var express = require('express')` binds a module: an import, not state
        let module = value.is_some_and(|v| {
            let v = if v.kind() == "await_expression" { v.named_child(0).unwrap_or(v) } else { v };
            v.kind() == "call_expression"
                && v.child_by_field_name("function")
                    .is_some_and(|f| f.kind() == "import" || text(f, src) == "require")
        });
        match n.child_by_field_name("name").filter(|x| x.kind() == "identifier") {
            Some(name) if top && !is_const && !function && !module => {
                vec![(n, name, DefKind::Variable)]
            }
            _ => Vec::new(),
        }
    }

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

    fn imports_of(&self, n: Node, src: &[u8]) -> Option<Vec<ImportFact>> {
        match n.kind() {
            "import_statement" => {
                let module = source(n, src)?;
                let type_only = all_kids(n).iter().any(|c| c.kind() == "type");
                let mut out = Vec::new();
                let mut from = import(n, ImportForm::From, module.clone());
                let clause = kids(n).into_iter().find(|c| c.kind() == "import_clause");
                match clause {
                    None => out.push(import(n, ImportForm::Module, module.clone())),
                    Some(cl) => {
                        for c in kids(cl) {
                            match c.kind() {
                                "identifier" => {
                                    from.names.push(("default".into(), Some(text(c, src))))
                                }
                                "namespace_import" => {
                                    let mut f = import(n, ImportForm::Module, module.clone());
                                    f.alias = kids(c).into_iter().next().map(|x| text(x, src));
                                    out.push(f);
                                }
                                "named_imports" => from.names.extend(specifiers(c, src)),
                                _ => {}
                            }
                        }
                    }
                }
                if !from.names.is_empty() {
                    out.push(from);
                }
                if type_only {
                    for f in out.iter_mut() {
                        f.runs = Runs::TypeChecking;
                    }
                }
                Some(out)
            }
            "export_statement" => {
                let module = source(n, src)?;
                let mut f = import(n, ImportForm::ReExport, module);
                for c in kids(n) {
                    match c.kind() {
                        "export_clause" => f.names.extend(specifiers(c, src)),
                        "namespace_export" => {
                            f.alias = kids(c).into_iter().next().map(|x| text(x, src))
                        }
                        _ => {}
                    }
                }
                if all_kids(n).iter().any(|c| c.kind() == "type") {
                    f.runs = Runs::TypeChecking;
                }
                Some(vec![f])
            }
            "call_expression" => call_import(n, src),
            _ => None,
        }
    }

    fn object_of(&self, n: Node, src: &[u8]) -> Option<String> {
        let p = n.parent()?;
        // `obj.name = function`, `Klass.prototype.name = function`
        if p.kind() == "assignment_expression" {
            let left = p.child_by_field_name("left")?;
            if left.kind() != "member_expression" {
                return None;
            }
            let obj = path_of(left.child_by_field_name("object")?, src)?;
            return obj.into_iter().rfind(|s| s != "prototype" && s != "()");
        }
        // `const obj = { name() {}, other: function () {} }`
        let pair_or_method = if p.kind() == "object" {
            n
        } else if p.kind() == "pair" {
            p
        } else {
            return None;
        };
        let object = pair_or_method.parent().filter(|o| o.kind() == "object")?;
        let decl = object.parent().filter(|d| d.kind() == "variable_declarator")?;
        decl.child_by_field_name("name").filter(|x| x.kind() == "identifier").map(|x| text(x, src))
    }

    fn extra_def<'t>(&self, n: Node<'t>, _src: &[u8]) -> Option<(Node<'t>, DefKind)> {
        let is_function = |v: Option<Node>| {
            v.is_some_and(|v| {
                matches!(
                    v.kind(),
                    "arrow_function"
                        | "function_expression"
                        | "function"
                        | "generator_function"
                        | "class"
                )
            })
        };
        match n.kind() {
            // a class field that holds a value, not a function
            "public_field_definition" | "field_definition" => {
                let name =
                    n.child_by_field_name("name").or_else(|| n.child_by_field_name("property"))?;
                (!is_function(n.child_by_field_name("value"))).then_some((name, DefKind::Property))
            }
            // a module-level `const X = …` that is not a function
            "variable_declarator" => {
                let decl = n.parent().filter(|p| p.kind() == "lexical_declaration")?;
                let top = decl.parent().is_some_and(|p| {
                    p.kind() == "program"
                        || (p.kind() == "export_statement"
                            && p.parent().is_some_and(|q| q.kind() == "program"))
                });
                let is_const = all_kids(decl).iter().any(|c| c.kind() == "const");
                let name = n.child_by_field_name("name").filter(|x| x.kind() == "identifier")?;
                let value = n.child_by_field_name("value");
                (top && is_const && !is_function(value)).then_some((name, DefKind::Constant))
            }
            _ => None,
        }
    }

    fn exports_of(&self, n: Node, src: &[u8]) -> Vec<(String, String)> {
        let mut out = Vec::new();
        match n.kind() {
            "export_statement" if n.child_by_field_name("source").is_none() => {
                let default = all_kids(n).iter().any(|c| c.kind() == "default");
                for c in kids(n) {
                    match c.kind() {
                        "export_clause" => {
                            for (name, alias) in specifiers(c, src) {
                                out.push((name.clone(), alias.unwrap_or(name)));
                            }
                        }
                        "identifier" if default => out.push((text(c, src), "default".into())),
                        _ if default => {
                            if let Some(name) = c.child_by_field_name("name") {
                                out.push((text(name, src), "default".into()));
                            }
                        }
                        _ => {}
                    }
                }
            }
            // CommonJS: `module.exports = X`, `module.exports = { a, b: c }`
            "assignment_expression" => {
                let left = n.child_by_field_name("left").map(|l| text(l, src));
                if left.as_deref() != Some("module.exports") {
                    return out;
                }
                let Some(right) = n.child_by_field_name("right") else { return out };
                match right.kind() {
                    "identifier" => out.push((text(right, src), "default".into())),
                    "object" => {
                        for prop in kids(right) {
                            match prop.kind() {
                                "shorthand_property_identifier" => {
                                    let t = text(prop, src);
                                    out.push((t.clone(), t));
                                }
                                "pair" => {
                                    let k = prop.child_by_field_name("key").map(|x| text(x, src));
                                    let v = prop.child_by_field_name("value");
                                    if let (Some(k), Some(v)) = (k, v) {
                                        if v.kind() == "identifier" {
                                            out.push((text(v, src), k));
                                        }
                                    }
                                }
                                _ => {}
                            }
                        }
                    }
                    _ => {
                        if let Some(name) = right.child_by_field_name("name") {
                            out.push((text(name, src), "default".into()));
                        }
                    }
                }
            }
            _ => {}
        }
        out
    }

    fn heritage_of(&self, n: Node, src: &[u8]) -> Vec<(RefKind, Vec<String>)> {
        let mut out = Vec::new();
        for c in kids(n) {
            match c.kind() {
                "class_heritage" => {
                    for h in kids(c) {
                        match h.kind() {
                            "extends_clause" => {
                                let v = h.child_by_field_name("value");
                                let items = match v {
                                    Some(v) => path_of(v, src).into_iter().collect(),
                                    None => paths_in(h, src),
                                };
                                out.extend(items.into_iter().map(|p| (RefKind::Inherits, p)));
                            }
                            "implements_clause" => out.extend(
                                paths_in(h, src).into_iter().map(|p| (RefKind::Implements, p)),
                            ),
                            // JavaScript: `extends Expr` straight under the heritage
                            _ => {
                                if let Some(p) = path_of(h, src) {
                                    out.push((RefKind::Inherits, p));
                                }
                            }
                        }
                    }
                }
                "extends_type_clause" => {
                    out.extend(paths_in(c, src).into_iter().map(|p| (RefKind::Inherits, p)))
                }
                _ => {}
            }
        }
        out
    }

    fn builtins(&self) -> &'static [&'static str] {
        BUILTINS
    }
}
