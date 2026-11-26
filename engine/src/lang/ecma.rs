//! JavaScript, TypeScript and TSX. The generic rule already covers classes,
//! methods, functions and anonymous functions bound to a name (`const f = () =>`,
//! object keys, class fields, `exports.send = function`); TypeScript namespaces
//! and ambient `declare module` blocks only prefix names.
//!
//! Imports: ES `import`, `export … from` (re-exports), `require(...)` (bound by
//! the declaration around it) and `import()` (dynamic). `import type` is
//! type-checking only.

use crate::parse::Fields;
use crate::parse::Kinds;
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
    n.by_field("source").map(|s| string_value(s, src))
}

fn specifiers(list: Node, src: &[u8]) -> Vec<(String, Option<String>)> {
    kids(list)
        .into_iter()
        .filter(|c| c.kind_str().ends_with("specifier"))
        .filter_map(|c| {
            let name = c.by_field("name").map(|x| text(x, src))?;
            let alias = c.by_field("alias").map(|x| text(x, src));
            Some((name, alias))
        })
        .collect()
}

/// `require('express')`, `require('./utils').parse`, `await import('m')`: the
/// value of a declaration that binds a module or one of its members. Such a
/// name is an import, not state: a call through it is a call of what it names.
fn binds_import(v: Node, src: &[u8]) -> bool {
    let mut v = v;
    for _ in 0..4 {
        match v.kind_str() {
            "await_expression" | "parenthesized_expression" => match v.named_child(0) {
                Some(inner) => v = inner,
                None => return false,
            },
            "member_expression" => match v.by_field("object") {
                Some(o) => v = o,
                None => return false,
            },
            "call_expression" => {
                return v
                    .by_field("function")
                    .is_some_and(|f| f.kind_str() == "import" || text(f, src) == "require");
            }
            _ => return false,
        }
    }
    false
}

/// `require('m')` and `import('m')`, bound by the declaration they sit in.
fn call_import(n: Node, src: &[u8]) -> Option<Vec<ImportFact>> {
    let f = n.by_field("function")?;
    let args = n.by_field("arguments")?;
    let first = kids(args).into_iter().next()?;
    if !matches!(first.kind_str(), "string" | "template_string") {
        return None;
    }
    let module = string_value(first, src);
    if f.kind_str() == "import" {
        // `const m = await import('./m.js')`, `const { f } = await import(…)`:
        // the names the declaration binds to the module
        let mut fact = import(n, ImportForm::Dynamic, module);
        let mut holder = n.parent();
        while holder.is_some_and(|h| {
            matches!(h.kind_str(), "await_expression" | "parenthesized_expression")
        }) {
            holder = holder.and_then(|h| h.parent());
        }
        let target = holder
            .filter(|h| h.kind_str() == "variable_declarator")
            .and_then(|h| h.by_field("name"));
        match target {
            Some(t) if t.kind_str() == "identifier" => fact.alias = Some(text(t, src)),
            Some(t) if t.kind_str() == "object_pattern" => {
                for p in kids(t) {
                    match p.kind_str() {
                        "shorthand_property_identifier_pattern" => {
                            fact.names.push((text(p, src), None))
                        }
                        "pair_pattern" => {
                            let k = p.by_field("key").map(|x| text(x, src));
                            let v = p.by_field("value").map(|x| text(x, src));
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
        return Some(vec![fact]);
    }
    if f.kind_str() != "identifier" || text(f, src) != "require" {
        return None;
    }
    // `const x = require('m')`, `const {a, b: c} = require('m')`,
    // `const y = require('m').y`
    let mut holder = n.parent();
    let mut member = None;
    if let Some(p) = holder {
        if p.kind_str() == "member_expression" {
            member = p.by_field("property").map(|x| text(x, src));
            holder = p.parent();
        }
    }
    let target = holder
        .filter(|h| {
            h.kind_str() == "variable_declarator" || h.kind_str() == "assignment_expression"
        })
        .and_then(|h| h.by_field("name").or_else(|| h.by_field("left")));
    let mut fact = import(n, ImportForm::Module, module);
    // `module.exports = require('./lib/app')`: the module is that module
    if member.is_none()
        && target.is_some_and(|t| {
            t.kind_str() == "member_expression" && text(t, src) == "module.exports"
        })
    {
        fact.form = ImportForm::ReExport;
        return Some(vec![fact]);
    }
    match (target, member) {
        (Some(t), Some(m)) if t.kind_str() == "identifier" => {
            fact.form = ImportForm::From;
            fact.names.push((m, Some(text(t, src))));
        }
        (Some(t), None) if t.kind_str() == "identifier" => fact.alias = Some(text(t, src)),
        (Some(t), None) if t.kind_str() == "object_pattern" => {
            fact.form = ImportForm::From;
            for p in kids(t) {
                match p.kind_str() {
                    "shorthand_property_identifier_pattern" => {
                        fact.names.push((text(p, src), None))
                    }
                    "pair_pattern" => {
                        let k = p.by_field("key").map(|x| text(x, src));
                        let v = p.by_field("value").map(|x| text(x, src));
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
        if n.kind_str() == "assignment_expression" {
            let field = n
                .by_field("left")
                .filter(|l| l.kind_str() == "member_expression")
                .filter(|l| l.by_field("object").is_some_and(|o| o.kind_str() == "this"))
                .and_then(|l| l.by_field("property"))
                .filter(|p| p.kind_str() == "property_identifier");
            let mut p = n.parent();
            let mut in_class = false;
            while let Some(x) = p {
                if x.kind_str() == "method_definition" {
                    in_class = x.parent().is_some_and(|b| b.kind_str() == "class_body");
                    break;
                }
                p = x.parent();
            }
            return match field {
                Some(name) if in_class => vec![(n, name, DefKind::Property)],
                _ => Vec::new(),
            };
        }
        // `constructor(public greeting: string)`: a parameter that declares
        // a property of the class
        if matches!(n.kind_str(), "required_parameter" | "optional_parameter") {
            let property = all_kids(n).iter().any(|c| {
                matches!(c.kind_str(), "accessibility_modifier" | "readonly" | "override_modifier")
            });
            let of_class = n
                .parent()
                .and_then(|ps| ps.parent())
                .filter(|m| m.kind_str() == "method_definition")
                .is_some_and(|m| m.parent().is_some_and(|b| b.kind_str() == "class_body"));
            return match n.by_field("pattern").filter(|x| x.kind_str() == "identifier") {
                Some(name) if property && of_class => vec![(n, name, DefKind::Property)],
                _ => Vec::new(),
            };
        }
        if n.kind_str() != "variable_declarator" {
            return Vec::new();
        }
        let Some(decl) = n
            .parent()
            .filter(|p| matches!(p.kind_str(), "lexical_declaration" | "variable_declaration"))
        else {
            return Vec::new();
        };
        let top = decl.parent().is_some_and(|p| {
            module_level(p)
                || (p.kind_str() == "export_statement" && p.parent().is_some_and(module_level))
        });
        let is_const = all_kids(decl).iter().any(|c| c.kind_str() == "const");
        let value = n.by_field("value");
        let function = value.is_some_and(|v| {
            matches!(
                v.kind_str(),
                "arrow_function"
                    | "function_expression"
                    | "function"
                    | "generator_function"
                    | "class"
            )
        });
        let module = value.is_some_and(|v| binds_import(v, src));
        match n.by_field("name").filter(|x| x.kind_str() == "identifier") {
            Some(name) if top && !is_const && !function && !module => {
                vec![(n, name, DefKind::Variable)]
            }
            _ => Vec::new(),
        }
    }

    fn kind_of(&self, node: Node, src: &[u8]) -> DefKind {
        match node.kind_str() {
            "internal_module" | "module" => DefKind::Namespace,
            "method_definition"
                if node.by_field("name").map(|n| text(n, src)).as_deref()
                    == Some("constructor") =>
            {
                DefKind::Constructor
            }
            k => generic::kind_of(k),
        }
    }

    fn imports_of(&self, n: Node, src: &[u8]) -> Option<Vec<ImportFact>> {
        match n.kind_str() {
            "import_statement" => {
                // `import fs = require("./fs")`: the module, under that name
                if let Some(cl) =
                    kids(n).into_iter().find(|c| c.kind_str() == "import_require_clause")
                {
                    let mut f = import(n, ImportForm::Module, source(cl, src)?);
                    f.alias = kids(cl)
                        .into_iter()
                        .find(|c| c.kind_str() == "identifier")
                        .map(|x| text(x, src));
                    return Some(vec![f]);
                }
                let module = source(n, src)?;
                let type_only = all_kids(n).iter().any(|c| c.kind_str() == "type");
                let mut out = Vec::new();
                let mut from = import(n, ImportForm::From, module.clone());
                let clause = kids(n).into_iter().find(|c| c.kind_str() == "import_clause");
                match clause {
                    None => out.push(import(n, ImportForm::Module, module.clone())),
                    Some(cl) => {
                        for c in kids(cl) {
                            match c.kind_str() {
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
                    match c.kind_str() {
                        "export_clause" => f.names.extend(specifiers(c, src)),
                        "namespace_export" => {
                            f.alias = kids(c).into_iter().next().map(|x| text(x, src))
                        }
                        _ => {}
                    }
                }
                if all_kids(n).iter().any(|c| c.kind_str() == "type") {
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
        if p.kind_str() == "assignment_expression" {
            let left = p.by_field("left")?;
            if left.kind_str() != "member_expression" {
                return None;
            }
            let obj = path_of(left.by_field("object")?, src)?;
            return obj.into_iter().rfind(|s| s != "prototype" && s != "()");
        }
        // `const obj = { name() {}, other: function () {} }`
        let pair_or_method = if p.kind_str() == "object" {
            n
        } else if p.kind_str() == "pair" {
            p
        } else {
            return None;
        };
        let object = pair_or_method.parent().filter(|o| o.kind_str() == "object")?;
        let holder = object.parent()?;
        // `UserSchema.methods = { encryptPassword: function () {} }`: the
        // member it is assigned to, as `obj.name = function` names its object
        if holder.kind_str() == "assignment_expression" {
            let left = holder.by_field("left").filter(|l| l.kind_str() == "member_expression")?;
            return path_of(left, src)?.into_iter().rfind(|s| s != "prototype" && s != "()");
        }
        let decl = Some(holder).filter(|d| d.kind_str() == "variable_declarator")?;
        decl.by_field("name").filter(|x| x.kind_str() == "identifier").map(|x| text(x, src))
    }

    fn extra_def<'t>(&self, n: Node<'t>, src: &[u8]) -> Option<(Node<'t>, DefKind)> {
        let is_function = |v: Option<Node>| {
            v.is_some_and(|v| {
                matches!(
                    v.kind_str(),
                    "arrow_function"
                        | "function_expression"
                        | "function"
                        | "generator_function"
                        | "class"
                )
            })
        };
        match n.kind_str() {
            // an enum's members: `enum Color { Red, Green = 2 }`
            "enum_assignment" => n.by_field("name").map(|name| (name, DefKind::EnumMember)),
            "property_identifier" if n.parent().is_some_and(|p| p.kind_str() == "enum_body") => {
                Some((n, DefKind::EnumMember))
            }
            // a class field that holds a value, not a function
            "public_field_definition" | "field_definition" => {
                let name = n.by_field("name").or_else(|| n.by_field("property"))?;
                (!is_function(n.by_field("value"))).then_some((name, DefKind::Property))
            }
            // a module-level `const X = …` that is not a function
            "variable_declarator" => {
                let decl = n.parent().filter(|p| p.kind_str() == "lexical_declaration")?;
                let top = decl.parent().is_some_and(|p| {
                    module_level(p)
                        || (p.kind_str() == "export_statement"
                            && p.parent().is_some_and(module_level))
                });
                let is_const = all_kids(decl).iter().any(|c| c.kind_str() == "const");
                let name = n.by_field("name").filter(|x| x.kind_str() == "identifier")?;
                let value = n.by_field("value");
                let import = value.is_some_and(|v| binds_import(v, src));
                (top && is_const && !is_function(value) && !import)
                    .then_some((name, DefKind::Constant))
            }
            _ => None,
        }
    }

    fn exports_of(&self, n: Node, src: &[u8]) -> Vec<(String, String)> {
        let mut out = Vec::new();
        match n.kind_str() {
            "export_statement" if n.by_field("source").is_none() => {
                // (`export = E;` is the module itself, as a default is)
                let default = all_kids(n).iter().any(|c| matches!(c.kind_str(), "default" | "="));
                for c in kids(n) {
                    match c.kind_str() {
                        "export_clause" => {
                            for (name, alias) in specifiers(c, src) {
                                out.push((name.clone(), alias.unwrap_or(name)));
                            }
                        }
                        "identifier" if default => out.push((text(c, src), "default".into())),
                        _ if default => {
                            if let Some(name) = c.by_field("name") {
                                out.push((text(name, src), "default".into()));
                            }
                        }
                        _ => {}
                    }
                }
            }
            // CommonJS: `module.exports = X`, `module.exports = { a, b: c }`
            "assignment_expression" => {
                let left = n.by_field("left").map(|l| text(l, src));
                let Some(right) = n.by_field("right") else { return out };
                // `exports.name = bound`, `module.exports.name = bound`: a
                // name the module passes on
                let named = left.as_deref().and_then(|l| {
                    l.strip_prefix("module.exports.").or_else(|| l.strip_prefix("exports."))
                });
                if let Some(name) = named {
                    if right.kind_str() == "identifier" && !name.contains('.') {
                        out.push((text(right, src), name.to_string()));
                    }
                    return out;
                }
                if left.as_deref() != Some("module.exports") {
                    return out;
                }
                match right.kind_str() {
                    "identifier" => out.push((text(right, src), "default".into())),
                    "object" => {
                        for prop in kids(right) {
                            match prop.kind_str() {
                                "shorthand_property_identifier" => {
                                    let t = text(prop, src);
                                    out.push((t.clone(), t));
                                }
                                "pair" => {
                                    let k = prop.by_field("key").map(|x| text(x, src));
                                    let v = prop.by_field("value");
                                    if let (Some(k), Some(v)) = (k, v) {
                                        if v.kind_str() == "identifier" {
                                            out.push((text(v, src), k));
                                        }
                                    }
                                }
                                _ => {}
                            }
                        }
                    }
                    _ => {
                        if let Some(name) = right.by_field("name") {
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
            match c.kind_str() {
                "class_heritage" => {
                    for h in kids(c) {
                        match h.kind_str() {
                            "extends_clause" => {
                                let v = h.by_field("value");
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

/// Whether a statement directly in `p` is at the level of a module: the
/// program, or the body of a TypeScript `namespace` (whose `const`s and
/// variables are the namespace's, as a module's are the module's).
fn module_level(p: Node) -> bool {
    p.kind_str() == "program"
        || (p.kind_str() == "statement_block"
            && p.parent().is_some_and(|q| matches!(q.kind_str(), "internal_module" | "module")))
}
