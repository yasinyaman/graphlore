//! References and imports, read in one walk after the definitions.
//!
//! The generic part — what a call, a construction, a type in a signature or a
//! decorator looks like — works across grammars the way the definition rule
//! does: by node types that carry a hint word and by the field names grammars
//! share (`function`, `object`/`property`, `operand`/`field`, `scope`/`name`,
//! …). Import statements, base lists and package declarations differ too much
//! between languages for that, so each language module reads its own
//! (`LanguageModule::imports_of`, `heritage_of`, `package_of`).

use std::collections::HashMap;

use tree_sitter::Node;

use crate::facts::{Def, FileFacts, ImportFact, ImportForm, Local, RefFact, RefKind, Runs};
use crate::generic::{kids, line, text};
use crate::lang::LanguageModule;

/// Node types that name something in a path.
const NAME_KINDS: &[&str] = &[
    "identifier",
    "simple_identifier",
    "type_identifier",
    "field_identifier",
    "property_identifier",
    "private_property_identifier",
    "shorthand_property_identifier",
    "constant",
    "name",
    "namespace_identifier",
    "package_identifier",
    "statement_identifier",
    "primitive_type",
    "predefined_type",
    "bound_identifier",
];
/// Receivers that mean the enclosing type.
const SELF_KINDS: &[&str] =
    &["this", "self", "self_expression", "this_expression", "crate", "base"];
const SUPER_KINDS: &[&str] = &["super", "super_expression", "base_expression"];
/// `(object, member)` field pairs of member-access shapes.
const MEMBER_FIELDS: &[(&str, &str)] = &[
    ("object", "property"),
    ("object", "attribute"),
    ("object", "name"),
    ("operand", "field"),
    ("value", "field"),
    ("argument", "field"),
    ("expression", "name"),
    ("receiver", "method"),
    ("scope", "name"),
    ("path", "name"),
    ("qualifier", "name"),
    ("package", "name"),
    ("target", "suffix"),
    ("namespace", "name"),
];
/// Type nodes whose name is the type referred to.
const TYPE_KINDS: &[&str] = &[
    "type_identifier",
    "scoped_type_identifier",
    "qualified_type",
    "generic_type",
    "user_type",
    "named_type",
    "nested_type_identifier",
    "generic_name",
    "qualified_name",
    "scoped_identifier",
    "class_type",
];
/// Fields whose subtree is a value, not a type.
const VALUE_FIELDS: &[&str] = &["value", "default_value", "default", "body", "initializer"];
/// Decorators, annotations, attributes.
const DECORATOR_KINDS: &[&str] =
    &["decorator", "marker_annotation", "annotation", "attribute", "attribute_item"];

const UNKNOWN: &str = "()";

/// The dotted path a node names, as far as the walk can tell.
pub fn path_of(n: Node, src: &[u8]) -> Option<Vec<String>> {
    let k = n.kind();
    if SELF_KINDS.contains(&k) {
        return Some(vec![if k == "crate" { "crate".into() } else { "self".into() }]);
    }
    if SUPER_KINDS.contains(&k) {
        return Some(vec!["super".into()]);
    }
    if k == "variable_name" {
        // PHP `$this`, `$x`
        let t = text(n, src);
        return Some(vec![if t == "$this" || t == "this" {
            "self".into()
        } else {
            UNKNOWN.into()
        }]);
    }
    if k == "relative_scope" {
        // PHP `self::`, `static::`, `parent::`
        let t = text(n, src);
        return Some(vec![if t == "parent" { "super".into() } else { "self".into() }]);
    }
    if NAME_KINDS.contains(&k) {
        let t = text(n, src);
        return match t.as_str() {
            "this" | "self" | "cls" | "Self" => Some(vec!["self".into()]),
            "super" | "base" | "parent" => Some(vec!["super".into()]),
            _ => Some(vec![t.trim_start_matches('$').to_string()]),
        };
    }
    for (obj, member) in MEMBER_FIELDS {
        if let (Some(o), Some(m)) = (n.child_by_field_name(obj), n.child_by_field_name(member)) {
            let mut p = path_of(o, src).unwrap_or_else(|| vec![UNKNOWN.into()]);
            p.extend(path_of(m, src)?);
            return Some(p);
        }
    }
    match k {
        // Kotlin / Swift: `a.b` with no fields, or Swift `target`/`suffix`
        "navigation_expression" => {
            let ks = kids(n);
            let mut p =
                ks.first().and_then(|f| path_of(*f, src)).unwrap_or_else(|| vec![UNKNOWN.into()]);
            let suffix = ks.iter().find(|c| c.kind() == "navigation_suffix")?;
            let name = kids(*suffix).into_iter().last()?;
            p.extend(path_of(name, src)?);
            Some(p)
        }
        "navigation_suffix" => kids(n).into_iter().last().and_then(|c| path_of(c, src)),
        // `a.b.C` written as one node: Kotlin `identifier`, Java/C# qualified
        // names, PHP `qualified_name` / `namespace_name`, Scala `stable_identifier`
        "user_type" | "namespace_name" | "stable_identifier" | "dotted_name"
        | "nested_identifier" => {
            let mut p = Vec::new();
            for c in kids(n) {
                if matches!(c.kind(), "type_arguments" | "type_projection") {
                    continue;
                }
                p.extend(path_of(c, src)?);
            }
            (!p.is_empty()).then_some(p)
        }
        "qualified_name" => {
            // PHP: prefix namespace_name + name; C# has qualifier/name fields
            let mut p = Vec::new();
            for c in kids(n) {
                p.extend(path_of(c, src)?);
            }
            (!p.is_empty()).then_some(p)
        }
        "generic_type" | "generic_name" | "parameterized_type" | "template_type" => n
            .child_by_field_name("name")
            .or_else(|| n.child_by_field_name("type"))
            .or_else(|| kids(n).into_iter().next())
            .and_then(|c| path_of(c, src)),
        "attribute" if n.child_by_field_name("object").is_none() => {
            n.child_by_field_name("name").and_then(|c| path_of(c, src))
        }
        "pointer_type"
        | "reference_type"
        | "nullable_type"
        | "optional_type"
        | "type"
        | "type_annotation"
        | "parenthesized_expression"
        | "non_null_expression"
        | "await_expression"
        | "unary_expression" => kids(n).into_iter().find_map(|c| path_of(c, src)),
        // `f().m`: the result of a call, marked `()` after the callee
        _ if is_call_kind(k) || matches!(k, "new_expression" | "object_creation_expression") => {
            match callee(n, src) {
                Some((_, mut p)) if p.last().is_some_and(|s| s != UNKNOWN) => {
                    p.push(UNKNOWN.into());
                    Some(p)
                }
                _ => Some(vec![UNKNOWN.into()]),
            }
        }
        _ => None,
    }
}

fn is_call_kind(k: &str) -> bool {
    (k.contains("call") || k.contains("invocation"))
        && !k.contains("signature")
        && !k.contains("suffix")
        && !k.contains("macro")
        && k != "call_signature"
        && k != "constructor_invocation"
}

/// The callee of a call node, and whether it constructs (`new`).
fn callee(n: Node, src: &[u8]) -> Option<(RefKind, Vec<String>)> {
    let k = n.kind();
    if matches!(
        k,
        "new_expression"
            | "object_creation_expression"
            | "instance_expression"
            | "struct_expression"
            | "composite_literal"
            | "implicit_object_creation_expression"
    ) {
        let t = n
            .child_by_field_name("constructor")
            .or_else(|| n.child_by_field_name("type"))
            .or_else(|| n.child_by_field_name("name"))
            .or_else(|| {
                kids(n)
                    .into_iter()
                    .find(|c| TYPE_KINDS.contains(&c.kind()) || NAME_KINDS.contains(&c.kind()))
            })?;
        let p = path_of(t, src)?;
        return Some((RefKind::New, p));
    }
    if !is_call_kind(k) {
        return None;
    }
    // Java `method_invocation`, Ruby `call`, PHP member/scoped calls: object
    // and name as fields of the call itself
    if let Some(name) = n.child_by_field_name("method").or_else(|| n.child_by_field_name("name")) {
        if n.child_by_field_name("function").is_none() {
            let recv = n
                .child_by_field_name("object")
                .or_else(|| n.child_by_field_name("receiver"))
                .or_else(|| n.child_by_field_name("scope"));
            let mut p = match recv {
                Some(r) => path_of(r, src).unwrap_or_else(|| vec![UNKNOWN.into()]),
                None => Vec::new(),
            };
            p.extend(path_of(name, src)?);
            return Some((RefKind::Call, p));
        }
    }
    let f = n
        .child_by_field_name("function")
        .or_else(|| kids(n).into_iter().find(|c| !c.kind().contains("argument")))?;
    if f.kind() == "import" {
        return None; // JS `import()`: an import, read by the module
    }
    Some((RefKind::Call, path_of(f, src)?))
}

/// The types a type position names: `Foo`, `pkg.Foo`, the arguments of
/// `List<Foo>` (as `generic_arg`).
fn types_in(
    n: Node,
    src: &[u8],
    context: &'static str,
    out: &mut Vec<(Vec<String>, &'static str)>,
) {
    let mut stack = vec![(n, context)];
    while let Some((node, ctx)) = stack.pop() {
        let k = node.kind();
        if matches!(k, "generic_type" | "generic_name" | "parameterized_type" | "template_type")
            || (k == "user_type" && kids(node).iter().any(|c| c.kind() == "type_arguments"))
        {
            if let Some(p) = path_of(node, src) {
                out.push((p, ctx));
            }
            for c in kids(node) {
                if matches!(
                    c.kind(),
                    "type_arguments" | "type_parameter" | "template_argument_list"
                ) {
                    stack.extend(kids(c).into_iter().map(|a| (a, "generic_arg")));
                }
            }
            continue;
        }
        if TYPE_KINDS.contains(&k) || k == "identifier" {
            if let Some(p) = path_of(node, src) {
                out.push((p, ctx));
            }
            continue;
        }
        if k == "attribute" {
            if let Some(p) = path_of(node, src) {
                out.push((p, ctx));
            }
            continue;
        }
        let sub = if k == "type_arguments" || k == "type_parameter" { "generic_arg" } else { ctx };
        stack.extend(kids(node).into_iter().map(|c| (c, sub)));
    }
}

/// Type references in the signature of a callable or a field: parameters,
/// result, the declared type.
fn signature_types(def: Node, src: &[u8], out: &mut Vec<(Vec<String>, &'static str)>) {
    let mut cursor = def.walk();
    for (i, child) in def.children(&mut cursor).enumerate() {
        let field = def.field_name_for_child(i as u32);
        match field {
            Some("receiver") => {}
            Some("parameters") => param_types(child, src, out),
            Some("return_type") | Some("result") | Some("returns") => {
                types_in(child, src, "return_type", out)
            }
            // Java / C / C++ method and function: `type` is the result
            Some("type") if !def.kind().contains("field") && !def.kind().contains("property") => {
                types_in(child, src, "return_type", out)
            }
            // C / C++: the parameters sit inside the declarator chain
            Some("declarator") => {
                let mut stack = vec![child];
                while let Some(d) = stack.pop() {
                    if let Some(ps) = d.child_by_field_name("parameters") {
                        param_types(ps, src, out);
                        break;
                    }
                    stack.extend(kids(d));
                }
            }
            _ => match child.kind() {
                "function_value_parameters"
                | "parameter_list"
                | "formal_parameters"
                | "parameter" => param_types(child, src, out),
                // Kotlin: the result type is a bare child of the function
                "user_type" | "nullable_type" if def.kind() == "function_declaration" => {
                    types_in(child, src, "return_type", out)
                }
                _ => {}
            },
        }
    }
}

fn param_types(params: Node, src: &[u8], out: &mut Vec<(Vec<String>, &'static str)>) {
    let mut stack = vec![params];
    while let Some(n) = stack.pop() {
        let mut cursor = n.walk();
        for (i, child) in n.children(&mut cursor).enumerate() {
            if !child.is_named() {
                continue;
            }
            let field = n.field_name_for_child(i as u32);
            if field.is_some_and(|f| VALUE_FIELDS.contains(&f) || f == "pattern") {
                continue;
            }
            if field == Some("type")
                || matches!(
                    child.kind(),
                    "type_annotation" | "user_type" | "nullable_type" | "named_type"
                )
            {
                types_in(child, src, "parameter_type", out);
            } else if !NAME_KINDS.contains(&child.kind()) {
                stack.push(child);
            }
        }
    }
}

/// The decorators, annotations and attributes on a definition.
fn decorators(def: Node, src: &[u8], out: &mut Vec<Vec<String>>) {
    let mut found: Vec<Node> = Vec::new();
    // Python: the decorated_definition around it
    if let Some(p) = def.parent() {
        if p.kind() == "decorated_definition" {
            found.extend(kids(p).into_iter().filter(|c| c.kind() == "decorator"));
        }
    }
    // TS / JS: decorators stacked before it
    let mut prev = if found.is_empty() { def.prev_named_sibling() } else { None };
    while let Some(p) = prev {
        if p.kind() != "decorator" {
            break;
        }
        found.push(p);
        prev = p.prev_named_sibling();
    }
    // Java / Kotlin modifiers, C# attribute lists, PHP attributes
    for c in kids(def) {
        if matches!(c.kind(), "modifiers" | "attribute_list" | "decorator") {
            let mut stack = vec![c];
            while let Some(n) = stack.pop() {
                if DECORATOR_KINDS.contains(&n.kind()) && n.kind() != "attribute_item" {
                    found.push(n);
                } else {
                    stack.extend(kids(n));
                }
            }
        }
    }
    for d in found {
        let target = d
            .child_by_field_name("name")
            .or_else(|| kids(d).into_iter().find(|c| c.kind() != "arguments"));
        let Some(mut t) = target else { continue };
        if is_call_kind(t.kind()) {
            if let Some(f) =
                t.child_by_field_name("function").or_else(|| kids(t).into_iter().next())
            {
                t = f;
            }
        }
        if let Some(p) = path_of(t, src) {
            if !p.iter().any(|s| s == UNKNOWN) {
                out.push(p);
            }
        }
    }
}

fn is_callable(kind: crate::facts::DefKind) -> bool {
    kind.is_callable()
}

/// The name a declarator or pattern binds: `x`, `*x`, `&x`, `$x`, `mut x`.
fn bound_name(n: Node, src: &[u8]) -> Option<String> {
    let mut cur = n;
    for _ in 0..6 {
        match cur.kind() {
            "identifier"
            | "simple_identifier"
            | "field_identifier"
            | "property_identifier"
            | "shorthand_property_identifier_pattern"
            | "bound_identifier"
            | "name" => {
                return Some(text(cur, src));
            }
            "variable_name" => return Some(text(cur, src).trim_start_matches('$').to_string()),
            "instance_variable" => return Some(text(cur, src).trim_start_matches('@').to_string()),
            _ => {}
        }
        cur = cur
            .child_by_field_name("declarator")
            .or_else(|| cur.child_by_field_name("name"))
            .or_else(|| cur.child_by_field_name("pattern"))
            .or_else(|| {
                kids(cur).into_iter().find(|c| {
                    matches!(
                        c.kind(),
                        "identifier"
                            | "simple_identifier"
                            | "variable_name"
                            | "bound_identifier"
                            | "name"
                            | "field_identifier"
                    ) || c.kind().ends_with("declarator")
                        || c.kind().ends_with("pattern")
                })
            })?;
    }
    None
}

/// The first type a type position names.
fn first_type(n: Node, src: &[u8]) -> Option<Vec<String>> {
    if matches!(n.kind(), "implicit_type" | "placeholder_type_specifier") {
        return None; // `var`, `auto`
    }
    let mut v = Vec::new();
    types_in(n, src, "", &mut v);
    v.into_iter().next().map(|(p, _)| p)
}

/// What a value constructs: `Foo()`, `new Foo()`, `Foo::new()`, `Foo{}`,
/// `&Foo{}`, `Foo.new`. The path is the callee's; the resolver keeps it only
/// when it names a type.
fn value_type(v: Node, src: &[u8]) -> Option<Vec<String>> {
    match v.kind() {
        "unary_expression"
        | "await_expression"
        | "parenthesized_expression"
        | "reference_expression"
        | "try_expression" => kids(v).into_iter().find_map(|c| value_type(c, src)),
        _ => callee(v, src).map(|(_, p)| p).filter(|p| !p.iter().any(|s| s == UNKNOWN)),
    }
}

fn is_self_member(n: Node, src: &[u8]) -> Option<String> {
    for (obj, member) in MEMBER_FIELDS {
        if let (Some(o), Some(m)) = (n.child_by_field_name(obj), n.child_by_field_name(member)) {
            let head = path_of(o, src)?;
            if head == ["self"] {
                return Some(text(m, src));
            }
        }
    }
    None
}

/// Typed names a node declares: `(name, type path, declared, field)`.
fn locals_of(n: Node, src: &[u8], in_params: bool) -> Vec<(String, Vec<String>, bool, bool)> {
    let k = n.kind();
    let mut out = Vec::new();
    // parameters: `x: Foo`, `Foo x`, `x Foo`, `$x: Foo`
    if in_params && k.contains("parameter") && !k.ends_with("parameters") && !k.ends_with("_list") {
        let ty = n.child_by_field_name("type").and_then(|t| first_type(t, src)).or_else(|| {
            kids(n)
                .into_iter()
                .find(|c| {
                    matches!(c.kind(), "user_type" | "type_annotation" | "named_type" | "type")
                })
                .and_then(|t| first_type(t, src))
        });
        // an untyped parameter still shadows outer names (path left empty)
        let ty = ty.or_else(|| Some(Vec::new()));
        if let Some(ty) = ty {
            // Go: several names share one type
            let mut names: Vec<String> = Vec::new();
            let mut cursor = n.walk();
            for (i, c) in n.children(&mut cursor).enumerate() {
                if n.field_name_for_child(i as u32) == Some("name") && c.kind() == "identifier" {
                    names.push(text(c, src));
                }
            }
            if names.is_empty() {
                let named = n
                    .child_by_field_name("name")
                    .or_else(|| n.child_by_field_name("pattern"))
                    .or_else(|| n.child_by_field_name("declarator"))
                    .or_else(|| {
                        kids(n).into_iter().find(|c| {
                            matches!(c.kind(), "identifier" | "simple_identifier" | "variable_name")
                        })
                    });
                names.extend(named.and_then(|x| bound_name(x, src)));
            }
            // a parameter that is also a field: TS `private svc: Svc`, PHP
            // promotion, Kotlin / Scala `val x: Foo` in a constructor
            let field = kids(n).iter().any(|c| {
                matches!(c.kind(), "accessibility_modifier" | "visibility_modifier" | "readonly")
                    || (c.kind() == "binding_pattern_kind")
            }) || matches!(k, "property_promotion_parameter" | "class_parameter");
            for name in names {
                if name != "self" && name != "this" {
                    out.push((name, ty.clone(), true, field));
                }
            }
        }
        return out;
    }
    match k {
        // `x = Foo()`, `self.x = Foo()`, `$x = new Foo`, `@x = Foo.new`
        "assignment" | "assignment_expression" => {
            let (Some(l), Some(r)) =
                (n.child_by_field_name("left"), n.child_by_field_name("right"))
            else {
                return out;
            };
            let Some(ty) = value_type(r, src) else { return out };
            if let Some(f) = is_self_member(l, src) {
                out.push((f, ty, false, true));
            } else if l.kind() == "instance_variable" {
                out.push((text(l, src).trim_start_matches('@').to_string(), ty, false, true));
            } else if let Some(name) = bound_name(l, src) {
                if l.child_count() == 0 || l.kind() == "variable_name" {
                    out.push((name, ty, false, false));
                }
            }
        }
        // `const x = new Foo()`, `let x = Foo::new()`, `let x: Foo = …`
        "variable_declarator" | "let_declaration" | "init_declarator" => {
            let name = n
                .child_by_field_name("name")
                .or_else(|| n.child_by_field_name("pattern"))
                .or_else(|| n.child_by_field_name("declarator"))
                .and_then(|x| bound_name(x, src));
            let declared = n.child_by_field_name("type").and_then(|t| first_type(t, src));
            let valued = n.child_by_field_name("value").and_then(|v| value_type(v, src));
            if let Some(name) = name {
                match (declared, valued) {
                    (Some(t), _) => out.push((name, t, true, false)),
                    (None, Some(t)) => out.push((name, t, false, false)),
                    _ => {}
                }
            }
        }
        // `Foo x = …;` (Java, C#, C++): the declared type, else the value's
        "local_variable_declaration"
        | "field_declaration"
        | "variable_declaration"
        | "declaration"
        | "property_declaration"
        | "public_field_definition" => {
            let field = matches!(
                k,
                "field_declaration" | "property_declaration" | "public_field_definition"
            );
            let declared =
                n.child_by_field_name("type").and_then(|t| first_type(t, src)).or_else(|| {
                    kids(n)
                        .into_iter()
                        .find(|c| matches!(c.kind(), "user_type" | "type_annotation"))
                        .and_then(|t| first_type(t, src))
                });
            let mut declarators: Vec<Node> = kids(n)
                .into_iter()
                .filter(|c| c.kind().ends_with("declarator") || c.kind() == "variable_declaration")
                .collect();
            if declarators.is_empty() {
                declarators.push(n);
            }
            for d in declarators {
                let inner: Vec<Node> = if d.kind() == "variable_declaration" {
                    kids(d)
                        .into_iter()
                        .filter(|c| {
                            c.kind().ends_with("declarator") || c.kind() == "simple_identifier"
                        })
                        .collect()
                } else {
                    vec![d]
                };
                let d_type = if d.kind() == "variable_declaration" {
                    d.child_by_field_name("type")
                        .and_then(|t| first_type(t, src))
                        .or_else(|| declared.clone())
                } else {
                    declared.clone()
                };
                for x in inner {
                    let name = if x.id() == n.id() {
                        n.child_by_field_name("name").and_then(|nm| bound_name(nm, src))
                    } else {
                        bound_name(x, src)
                    };
                    let Some(name) = name else { continue };
                    let valued = x
                        .child_by_field_name("value")
                        .or_else(|| kids(x).into_iter().find(|c| callee(*c, src).is_some()))
                        .and_then(|v| value_type(v, src));
                    match (&d_type, valued) {
                        (Some(t), _) => out.push((name, t.clone(), true, field)),
                        (None, Some(t)) => out.push((name, t, false, field)),
                        _ => {}
                    }
                }
            }
        }
        // Python `with Foo() as x:`
        "as_pattern" => {
            let value = kids(n).into_iter().next();
            let alias = n.child_by_field_name("alias").and_then(|a| bound_name(a, src));
            if let (Some(v), Some(name)) = (value, alias) {
                if let Some(t) = value_type(v, src) {
                    out.push((name, t, false, false));
                }
            }
        }
        // Go `x := Foo{}`, `a, b := …`
        "short_var_declaration" => {
            let (Some(l), Some(r)) =
                (n.child_by_field_name("left"), n.child_by_field_name("right"))
            else {
                return out;
            };
            for (name, value) in kids(l).into_iter().zip(kids(r)) {
                if let Some(t) = value_type(value, src) {
                    out.push((text(name, src), t, false, false));
                }
            }
        }
        _ => {}
    }
    out
}

/// The text of a string literal without its quotes.
pub fn string_value(n: Node, src: &[u8]) -> String {
    let mut stack = vec![n];
    while let Some(c) = stack.pop() {
        if c.kind().contains("content") || c.kind() == "string_fragment" {
            return text(c, src);
        }
        stack.extend(kids(c));
    }
    let t = text(n, src);
    t.trim_matches(|c| c == '"' || c == '\'' || c == '`' || c == '<' || c == '>').to_string()
}

/// A new import fact at `n`, module-level until the walk says otherwise.
pub fn import(n: Node, form: ImportForm, module: impl Into<String>) -> ImportFact {
    ImportFact {
        line: line(n),
        form,
        module: module.into(),
        names: Vec::new(),
        alias: None,
        runs: Runs::ModuleLevel,
        is_static: false,
        system: false,
        byte: n.start_byte(),
    }
}

/// Read the references, imports and package of `facts`' file; `facts.defs`
/// must already be filled.
pub fn read(m: &dyn LanguageModule, root: Node, src: &[u8], facts: &mut FileFacts) {
    let by_node: HashMap<usize, usize> =
        facts.defs.iter().enumerate().map(|(i, d)| (d.node_id, i)).collect();
    let mut refs: Vec<RefFact> = Vec::new();
    let mut imports: Vec<ImportFact> = Vec::new();
    let mut locals: Vec<Local> = Vec::new();
    let push = |refs: &mut Vec<RefFact>, n: Node, kind, path: Vec<String>, context, def| {
        if path.is_empty() || path.last().is_some_and(|s| s == UNKNOWN || s.is_empty()) {
            return;
        }
        refs.push(RefFact {
            line: line(n),
            kind,
            path,
            context,
            from_def: def,
            from_owner: None,
            byte: n.start_byte(),
        });
    };
    let mut stack = vec![root];
    let mut steps = 0usize;
    while let Some(n) = stack.pop() {
        steps += 1;
        if steps > 2_000_000 {
            break;
        }
        if facts.package.is_none() {
            if let Some(p) = m.package_of(n, src) {
                facts.package = Some(p);
            }
        }
        facts.exports.extend(m.exports_of(n, src));
        if let Some(found) = m.imports_of(n, src) {
            imports.extend(found);
            continue;
        }
        if !by_node.contains_key(&n.id()) {
            let found = m.heritage_of(n, src);
            if !found.is_empty() {
                let owner = m.heritage_owner(n, src);
                for (kind, path) in found {
                    let at = refs.len();
                    push(&mut refs, n, kind, path, "", None);
                    if let Some(r) = refs.get_mut(at) {
                        r.from_owner = owner.clone();
                    }
                }
            }
        }
        if let Some(&di) = by_node.get(&n.id()) {
            let def: &Def = &facts.defs[di];
            for (kind, path) in m.heritage_of(n, src) {
                push(&mut refs, n, kind, path, "", Some(di));
            }
            let mut decos = Vec::new();
            decorators(n, src, &mut decos);
            for p in decos {
                push(&mut refs, n, RefKind::Decorator, p, "decorator", Some(di));
            }
            if is_callable(def.kind) {
                let mut types = Vec::new();
                signature_types(n, src, &mut types);
                for (p, ctx) in types {
                    push(&mut refs, n, RefKind::Type, p, ctx, Some(di));
                }
                let mut pstack: Vec<Node> = Vec::new();
                for f in ["parameters", "receiver"] {
                    if let Some(ps) = n.child_by_field_name(f) {
                        pstack.push(ps);
                    }
                }
                pstack.extend(kids(n).into_iter().filter(|c| {
                    matches!(
                        c.kind(),
                        "parameter"
                            | "function_value_parameters"
                            | "parameter_list"
                            | "formal_parameters"
                            | "class_parameters"
                            | "primary_constructor"
                    )
                }));
                if let Some(decl) = n.child_by_field_name("declarator") {
                    if let Some(ps) = decl.child_by_field_name("parameters") {
                        pstack.push(ps);
                    }
                }
                while let Some(ps) = pstack.pop() {
                    let found = locals_of(ps, src, true);
                    if found.is_empty() {
                        for c in kids(ps) {
                            // an untyped parameter written as a bare name
                            // (Python, JS, Ruby): it shadows outer names
                            if matches!(c.kind(), "identifier" | "simple_identifier") {
                                let name = text(c, src);
                                if name != "self" && name != "cls" {
                                    locals.push(Local {
                                        name,
                                        path: Vec::new(),
                                        declared: false,
                                        field: false,
                                        from_def: Some(di),
                                        byte: c.start_byte(),
                                    });
                                }
                            } else if matches!(
                                c.kind(),
                                "default_parameter"
                                    | "list_splat_pattern"
                                    | "dictionary_splat_pattern"
                                    | "assignment_pattern"
                                    | "rest_pattern"
                                    | "optional_parameter"
                                    | "splat_parameter"
                                    | "hash_splat_parameter"
                            ) {
                                let name = c
                                    .child_by_field_name("name")
                                    .or_else(|| c.child_by_field_name("left"))
                                    .or_else(|| {
                                        kids(c).into_iter().find(|x| x.kind() == "identifier")
                                    })
                                    .map(|x| text(x, src));
                                if let Some(name) = name {
                                    locals.push(Local {
                                        name,
                                        path: Vec::new(),
                                        declared: false,
                                        field: false,
                                        from_def: Some(di),
                                        byte: c.start_byte(),
                                    });
                                }
                            } else {
                                pstack.push(c);
                            }
                        }
                    }
                    for (name, path, declared, field) in found {
                        locals.push(Local {
                            name,
                            path,
                            declared,
                            field,
                            from_def: Some(di),
                            byte: ps.start_byte(),
                        });
                    }
                }
            }
            // a class's constructor parameters (Kotlin, Scala `class A(val x: B)`)
            if def.kind.is_class_like() {
                for c in kids(n) {
                    if matches!(c.kind(), "primary_constructor" | "class_parameters") {
                        let mut st = vec![c];
                        while let Some(x) = st.pop() {
                            let found = locals_of(x, src, true);
                            if found.is_empty() {
                                st.extend(kids(x));
                            }
                            for (name, path, declared, _) in found {
                                locals.push(Local {
                                    name,
                                    path,
                                    declared,
                                    field: true,
                                    from_def: Some(di),
                                    byte: x.start_byte(),
                                });
                            }
                        }
                    }
                }
            }
        } else if m.is_field(n) {
            let declared = n.child_by_field_name("type").or_else(|| {
                kids(n)
                    .into_iter()
                    .find(|c| c.kind() == "variable_declaration")
                    .and_then(|v| v.child_by_field_name("type"))
            });
            if let Some(t) = declared {
                let mut types = Vec::new();
                types_in(t, src, "field", &mut types);
                for (p, ctx) in types {
                    push(&mut refs, n, RefKind::Type, p, ctx, None);
                }
            }
        }
        // a variable's declared type: `x: Foo = …`, `let x: Foo`, `Foo x = …`
        if matches!(
            n.kind(),
            "assignment"
                | "variable_declarator"
                | "let_declaration"
                | "local_variable_declaration"
                | "short_var_declaration"
                | "var_spec"
        ) {
            if let Some(t) = n.child_by_field_name("type") {
                let mut types = Vec::new();
                types_in(t, src, "variable_type", &mut types);
                for (p, ctx) in types {
                    push(&mut refs, n, RefKind::Type, p, ctx, None);
                }
            }
        }
        if !by_node.contains_key(&n.id()) {
            for (name, path, declared, field) in locals_of(n, src, false) {
                locals.push(Local {
                    name,
                    path,
                    declared,
                    field,
                    from_def: None,
                    byte: n.start_byte(),
                });
            }
        }
        // `self.prop`, `obj.method` read, not called and not assigned to
        if matches!(
            n.kind(),
            "attribute"
                | "member_expression"
                | "field_expression"
                | "member_access_expression"
                | "navigation_expression"
                | "selector_expression"
        ) {
            let called = n.parent().is_some_and(|p| {
                callee(p, src).is_some()
                    && p.child_by_field_name("function").is_some_and(|f| f.id() == n.id())
                    || p.kind().contains("call")
                        && kids(p).first().is_some_and(|f| f.id() == n.id())
            });
            let assigned = n
                .parent()
                .is_some_and(|p| p.child_by_field_name("left").is_some_and(|l| l.id() == n.id()));
            let chained = n.parent().is_some_and(|p| {
                MEMBER_FIELDS
                    .iter()
                    .any(|(o, _)| p.child_by_field_name(o).is_some_and(|x| x.id() == n.id()))
            });
            if !called && !assigned && !chained {
                if let Some(path) = path_of(n, src) {
                    if path.len() >= 2 && !path.iter().any(|s| s == UNKNOWN) {
                        push(&mut refs, n, RefKind::Attr, path, "property_read", None);
                    }
                }
            }
        }
        if let Some(extra) = m.refs_of(n, src) {
            for (kind, path, context) in extra {
                push(&mut refs, n, kind, path, context, None);
            }
        } else if n.parent().is_some_and(|p| p.kind() == "decorator") {
            // the decorator expression itself is the definition's decorator
            // reference, not a call of the enclosing scope
        } else if let Some((kind, path)) = callee(n, src) {
            let context = if kind == RefKind::New { "constructor" } else { "call" };
            push(&mut refs, n, kind, path, context, None);
            // functions handed over as values: `MockTransport(app)`,
            // `map(str.strip, xs)`, `hooks = [on_request]` inside an argument
            if let Some(args) = n.child_by_field_name("arguments").or_else(|| {
                kids(n)
                    .into_iter()
                    .find(|c| c.kind().contains("argument") || c.kind() == "call_suffix")
            }) {
                let mut st = kids(args);
                while let Some(a) = st.pop() {
                    match a.kind() {
                        "identifier" | "simple_identifier" => push(
                            &mut refs,
                            a,
                            RefKind::Indirect,
                            vec![text(a, src)],
                            "argument",
                            None,
                        ),
                        "argument" | "value_argument" | "value_arguments" | "list" | "array"
                        | "tuple" | "keyword_argument" | "pair" | "dictionary" | "object"
                        | "array_literal" | "collection_literal" => {
                            let ctx_kids = kids(a);
                            st.extend(
                                ctx_kids
                                    .into_iter()
                                    .filter(|c| !matches!(c.kind(), "lambda" | "arrow_function")),
                            );
                        }
                        _ => {}
                    }
                }
            }
        }
        let mut cursor = n.walk();
        let children: Vec<Node> = n.children(&mut cursor).filter(|c| c.is_named()).collect();
        stack.extend(children.into_iter().rev());
    }
    // the innermost definition around each use (a sweep: defs are in source
    // pre-order, so a stack of open ranges is the chain of ancestors)
    assign_defs(&facts.defs, &mut refs);
    assign_local_defs(&facts.defs, &mut locals);
    // one per (definition, name, field): a declared type over a guessed one
    locals.retain(|l| !matches!(l.path.as_slice(), [t] if t == "var" || t == "auto" || t == "let"));
    locals.sort_by(|a, b| {
        (a.from_def, &a.name, !a.field, !a.declared).cmp(&(
            b.from_def,
            &b.name,
            !b.field,
            !b.declared,
        ))
    });
    locals.dedup_by(|b, a| a.from_def == b.from_def && a.name == b.name);
    for imp in imports.iter_mut() {
        if imp.runs == Runs::ModuleLevel && inside_callable(&facts.defs, imp.byte) {
            imp.runs = Runs::Deferred;
        }
    }
    facts.refs = refs;
    facts.imports = imports;
    facts.locals = locals;
}

fn inside_callable(defs: &[Def], byte: usize) -> bool {
    defs.iter().any(|d| d.kind.is_callable() && d.start_byte <= byte && byte < d.end_byte)
}

fn assign_defs(defs: &[Def], refs: &mut [RefFact]) {
    let mut order: Vec<usize> = (0..refs.len())
        .filter(|&i| refs[i].from_def.is_none() && refs[i].from_owner.is_none())
        .collect();
    order.sort_by_key(|&i| refs[i].byte);
    let mut open: Vec<usize> = Vec::new();
    let mut next = 0;
    for i in order {
        let b = refs[i].byte;
        while next < defs.len() && defs[next].start_byte <= b {
            while open.last().is_some_and(|&o| defs[o].end_byte <= defs[next].start_byte) {
                open.pop();
            }
            open.push(next);
            next += 1;
        }
        while open.last().is_some_and(|&o| defs[o].end_byte <= b) {
            open.pop();
        }
        refs[i].from_def = open.last().copied();
    }
}

fn assign_local_defs(defs: &[Def], locals: &mut [Local]) {
    let mut order: Vec<usize> =
        (0..locals.len()).filter(|&i| locals[i].from_def.is_none()).collect();
    order.sort_by_key(|&i| locals[i].byte);
    let mut open: Vec<usize> = Vec::new();
    let mut next = 0;
    for i in order {
        let b = locals[i].byte;
        while next < defs.len() && defs[next].start_byte <= b {
            while open.last().is_some_and(|&o| defs[o].end_byte <= defs[next].start_byte) {
                open.pop();
            }
            open.push(next);
            next += 1;
        }
        while open.last().is_some_and(|&o| defs[o].end_byte <= b) {
            open.pop();
        }
        locals[i].from_def = open.last().copied();
    }
}
