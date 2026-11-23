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

use crate::facts::{
    Def, FileFacts, Implicit, ImportFact, ImportForm, Local, RefFact, RefKind, Runs,
};
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
const SELF_KINDS: &[&str] = &["this", "self", "self_expression", "this_expression", "crate"];
/// (`base` is C#'s: `base.Run()` is the base class's `Run`)
const SUPER_KINDS: &[&str] = &["super", "super_expression", "base_expression", "base"];
/// `(object, member)` field pairs of member-access shapes.
pub const MEMBER_FIELDS: &[(&str, &str)] = &[
    ("object", "property"),
    ("object", "attribute"),
    ("object", "name"),
    ("object", "field"),
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

pub const UNKNOWN: &str = "()";
/// The head of a C# member binding (`?.b`) until the access around it puts
/// the object in its place.
const BOUND: &str = "[?.]";

/// The dotted path a node names, as far as the walk can tell.
/// Whether a plain name is the language's word for the object a method is
/// called on (`self`) or for its base (`super`): `this`, `self`, `cls`,
/// `Self`, `super`, `base`, `parent`, each only where the language means it.
/// Elsewhere it is a name like any other (Go `parent.addChild(c)`, a Python
/// parameter `base`, a Java parameter `cls`).
fn receiver_word(t: &str) -> Option<&'static str> {
    use crate::lang::Lang as L;
    if !matches!(t, "this" | "self" | "cls" | "Self" | "super" | "base" | "parent") {
        return None;
    }
    let word = if matches!(t, "super" | "base" | "parent") { "super" } else { "self" };
    // a tree parsed outside the build (no language on record) keeps every word
    let Some(lang) = crate::parse::current() else { return Some(word) };
    let means = match t {
        "this" => !matches!(lang, L::Python | L::Go | L::Rust | L::Ruby | L::Swift | L::C),
        // (`self` stays the object everywhere: where it is no keyword it is
        // the alias of one, JavaScript `var self = this`)
        "self" | "Self" => true,
        "cls" => lang == L::Python,
        "super" => !matches!(lang, L::Go | L::C | L::Cpp | L::CSharp | L::Php),
        "base" => lang == L::CSharp,
        _ => lang == L::Php, // `parent`
    };
    means.then_some(word)
}

pub fn path_of(n: Node, src: &[u8]) -> Option<Vec<String>> {
    let k = n.kind();
    if SELF_KINDS.contains(&k) {
        return Some(vec![if k == "crate" { "crate".into() } else { "self".into() }]);
    }
    if SUPER_KINDS.contains(&k) {
        // Kotlin `super<A>`: the supertype named (overload-resolution.md,
        // 'Call with an explicit super-form receiver')
        if k == "super_expression" {
            if let Some(t) = kids(n).into_iter().find(|c| c.kind() == "user_type") {
                if let Some(p) = path_of(t, src) {
                    return Some(p);
                }
            }
        }
        return Some(vec!["super".into()]);
    }
    if k == "variable_name" {
        // PHP `$this`, and `$x` kept with its `$`: a variable, which only a
        // local of that name can type (never a function or class named `x`)
        let t = text(n, src);
        return Some(vec![if t == "$this" || t == "this" { "self".into() } else { t }]);
    }
    // Scala `opt[URI]`, Rust `parse::<T>`: the function, its type arguments
    // aside
    if k == "generic_function" {
        return path_of(n.child_by_field_name("function")?, src);
    }
    if k == "instance_variable" {
        // Ruby `@repo`: a field of the object
        return Some(vec!["self".into(), text(n, src).trim_start_matches('@').to_string()]);
    }
    if k == "relative_scope" {
        // PHP `self::`, `static::`, `parent::`
        let t = text(n, src);
        return Some(vec![if t == "parent" { "super".into() } else { "self".into() }]);
    }
    if NAME_KINDS.contains(&k) {
        let t = text(n, src);
        // PHP `new static()`, `static::make()`: the class around
        if t == "static" && crate::parse::current() == Some(crate::lang::Lang::Php) {
            return Some(vec!["self".into()]);
        }
        return match receiver_word(&t) {
            Some(word) => Some(vec![word.into()]),
            // (`$` is a sigil in PHP and Ruby, and Swift's `$0` / `$wrapped`
            // name `0` / `wrapped`: JS / TS `$ZodType`, `$scope` are names
            // like any other)
            None if matches!(
                crate::parse::current(),
                Some(crate::lang::Lang::Php | crate::lang::Lang::Ruby | crate::lang::Lang::Swift)
            ) =>
            {
                Some(vec![t.trim_start_matches('$').to_string()])
            }
            None => Some(vec![t]),
        };
    }
    // `x!`, `x!!`: the value itself
    if k == "postfix_expression" {
        let mut cursor = n.walk();
        if n.children(&mut cursor).any(|c| c.kind() == "bang" || c.kind() == "!!") {
            return path_of(
                n.child_by_field_name("target").or_else(|| kids(n).into_iter().next())?,
                src,
            );
        }
    }
    // `xs[0]`: an element of `xs`
    if matches!(
        k,
        "subscript_expression"
            | "index_expression"
            | "element_access_expression"
            | "indexing_expression"
            | "subscript"
            | "element_reference"
    ) {
        let object = ["object", "operand", "value", "expression", "array"]
            .iter()
            .find_map(|f| n.child_by_field_name(f))
            .or_else(|| kids(n).into_iter().next())?;
        let mut p = path_of(object, src).filter(|p| p[0] != UNKNOWN)?;
        p.push(ELEMENT.into());
        return Some(p);
    }
    // `[x.owner for x in xs]`: a collection of what each element gives
    if matches!(k, "list_comprehension" | "set_comprehension" | "generator_expression") {
        let clause = kids(n).into_iter().find(|c| c.kind() == "for_in_clause")?;
        let var = bound_name(clause.child_by_field_name("left")?, src)?;
        let of = path_of(clause.child_by_field_name("right")?, src)?;
        let each = path_of(n.child_by_field_name("body")?, src)?;
        return mapped(&of, &var, each);
    }
    if is_call_kind(k) || matches!(k, "new_expression" | "object_creation_expression") {
        // the callee is read once: a chain of calls is as deep as it is long
        let called = callee(n, src);
        // `xs.map { it.owner }`: a collection of what the lambda gives
        if let Some(p) = called.as_ref().and_then(|(_, p)| mapped_call(n, src, p)) {
            return Some(p);
        }
        // `f().m`: the result of a call, marked `()` after the callee (before
        // the member shapes: Java / Ruby / PHP calls carry `object` and `name`
        // fields)
        return match called {
            Some((_, mut p)) if p.last().is_some_and(|s| s != UNKNOWN) => {
                p.push(UNKNOWN.into());
                Some(p)
            }
            _ => Some(vec![UNKNOWN.into()]),
        };
    }
    for (obj, member) in MEMBER_FIELDS {
        if let (Some(o), Some(m)) = (n.child_by_field_name(obj), n.child_by_field_name(member)) {
            // Rust `self::f`: `f` of the module the path is written in
            if k == "scoped_identifier" && o.kind() == "self" {
                return member_path(m, src);
            }
            let mut p = path_of(o, src).unwrap_or_else(|| vec![UNKNOWN.into()]);
            p.extend(member_path(m, src)?);
            return Some(p);
        }
    }
    // a cast used where it is written (`((Foo) x).m()`, `(x as Foo).m()`, Go
    // `x.(*Foo).m()`): a value of the type it names
    if is_cast(n) {
        let mut p = value_type(n, src).filter(|p| !p.is_empty())?;
        p.push(UNKNOWN.into());
        return Some(p);
    }
    match k {
        // C# `a?.b`: `b` of `a` (the member is a `member_binding_expression`,
        // and what follows it hangs off that)
        "conditional_access_expression" => {
            let ks = kids(n);
            let mut p = path_of(*ks.first()?, src)?;
            let rest = path_of(*ks.get(1)?, src)?;
            if rest.first().is_some_and(|s| s == BOUND) {
                p.extend(rest.into_iter().skip(1));
                Some(p)
            } else {
                None
            }
        }
        "member_binding_expression" => {
            let mut p = vec![BOUND.to_string()];
            p.extend(path_of(n.child_by_field_name("name")?, src)?);
            Some(p)
        }
        // Swift `(x as! Foo)`: a tuple of one value is the value
        "tuple_expression" if kids(n).len() == 1 => {
            let only = kids(n)[0];
            path_of(only.child_by_field_name("value").unwrap_or(only), src)
        }
        // Kotlin / Swift: `a.b` with no fields, or Swift `target`/`suffix`
        "navigation_expression" => {
            let ks = kids(n);
            let mut p =
                ks.first().and_then(|f| path_of(*f, src)).unwrap_or_else(|| vec![UNKNOWN.into()]);
            let suffix = ks.iter().find(|c| c.kind() == "navigation_suffix")?;
            let name = kids(*suffix).into_iter().last()?;
            p.extend(member_path(name, src)?);
            Some(p)
        }
        "navigation_suffix" => kids(n).into_iter().last().and_then(|c| path_of(c, src)),
        // `a.b.C` written as one node: Kotlin `identifier`, Java/C# qualified
        // names, PHP `qualified_name` / `namespace_name`, Scala `stable_identifier`
        "user_type"
        | "namespace_name"
        | "stable_identifier"
        | "dotted_name"
        | "nested_identifier"
        | "nested_type_identifier"
        | "named_type"
        | "class_constant_access_expression" => {
            let mut p = Vec::new();
            for c in kids(n) {
                if matches!(c.kind(), "type_arguments" | "type_projection") {
                    continue;
                }
                p.extend(path_of(c, src)?);
            }
            // Swift `Foo.Type`, `Foo.Protocol`: the metatype of `Foo`
            if k == "user_type"
                && p.len() > 1
                && p.last().is_some_and(|s| s == "Type" || s == "Protocol")
                && crate::parse::current() == Some(crate::lang::Lang::Swift)
            {
                p.pop();
            }
            (!p.is_empty()).then_some(p)
        }
        "qualified_name" => {
            // PHP: prefix namespace_name + name; C# has qualifier/name fields
            let mut p = Vec::new();
            // (PHP `\Name`: the global namespace, written `\`)
            if crate::parse::current() == Some(crate::lang::Lang::Php)
                && text(n, src).trim_start().starts_with('\\')
            {
                p.push("\\".to_string());
            }
            for c in kids(n) {
                p.extend(path_of(c, src)?);
            }
            (p.len() > usize::from(p.first().is_some_and(|h| h == "\\"))).then_some(p)
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
        | "reference_expression"
        | "unary_expression" => kids(n).into_iter().find_map(|c| path_of(c, src)),
        // `await x`, Rust `x?`: what the wrapper holds
        "await_expression" | "try_expression" => {
            let mut p = kids(n).into_iter().find_map(|c| path_of(c, src))?;
            if p.last().is_some_and(|s| s != OPENED) {
                p.push(OPENED.into());
            }
            Some(p)
        }
        // Java `a.b.C` written as names with no fields
        "scoped_type_identifier" | "scoped_identifier" | "qualified_identifier" => {
            let mut p = Vec::new();
            // C++ `::Helper()`: the global namespace, written `::`
            if n.kind() == "qualified_identifier"
                && n.child_by_field_name("scope").is_none()
                && text(n, src).trim_start().starts_with("::")
            {
                p.push("::".to_string());
            }
            for c in kids(n) {
                if matches!(c.kind(), "type_arguments" | "annotation" | "marker_annotation") {
                    continue;
                }
                p.extend(path_of(c, src)?);
            }
            (!p.is_empty()).then_some(p)
        }
        _ => None,
    }
}

pub fn is_call_kind(k: &str) -> bool {
    (k.contains("call") || k.contains("invocation"))
        && !k.contains("signature")
        && !k.contains("suffix")
        && !k.contains("macro")
        && k != "call_signature"
        && k != "constructor_invocation"
        // (Kotlin `::handle`: a function named, not called)
        && k != "callable_reference"
        // (Java `super(…)` / `this(…)`: a construction, read where it is)
        && k != "explicit_constructor_invocation"
}

/// The callee of a call node, and whether it constructs (`new`).
pub fn callee(n: Node, src: &[u8]) -> Option<(RefKind, Vec<String>)> {
    let k = n.kind();
    if matches!(
        k,
        "new_expression"
            | "object_creation_expression"
            | "instance_expression"
            | "struct_expression"
            | "composite_literal"
            | "implicit_object_creation_expression"
            // (Swift `Res<Cred>(value: 1)`: a construction written with its
            // type arguments)
            | "constructor_expression"
            // (C++ `Pool{}`, C `(item){ 0 }`)
            | "compound_literal_expression"
    ) {
        // Scala `new Outer.Inner(…)`, `new scopt.OptionParser[Config](…)`:
        // the type named through what holds it
        let stable = kids(n).into_iter().find_map(|c| match c.kind() {
            "stable_type_identifier" => Some(c),
            "generic_type" => {
                c.child_by_field_name("type").filter(|t| t.kind() == "stable_type_identifier")
            }
            _ => None,
        });
        if let Some(st) = stable {
            return Some((RefKind::New, stable_path(st, src)));
        }
        let t = n
            .child_by_field_name("constructor")
            .or_else(|| n.child_by_field_name("constructed_type"))
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
    // Swift `Self()`: a construction of the type around
    if text(f, src) == "Self" && crate::parse::current() == Some(crate::lang::Lang::Swift) {
        return Some((RefKind::New, vec!["self".into()]));
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
            // (Scala `a.b.C[Int]`: the type is written with its package)
            let stable = node
                .child_by_field_name("type")
                .filter(|t| t.kind() == "stable_type_identifier")
                .map(|t| stable_path(t, src));
            if let Some(p) = stable.or_else(|| path_of(node, src)) {
                out.push((p, ctx));
            }
            for c in kids(node) {
                if matches!(
                    c.kind(),
                    "type_arguments"
                        | "type_parameter"
                        | "template_argument_list"
                        | "type_argument_list"
                ) {
                    stack.extend(kids(c).into_iter().map(|a| (a, "generic_arg")));
                }
            }
            continue;
        }
        // Scala `scopt.RenderingMode`: one type, named through its package
        if k == "stable_type_identifier" {
            out.push((stable_path(node, src), ctx));
            continue;
        }
        // C++ `Outer::Inner`, `ns::Type` (one written with arguments,
        // `std::vector<Foo>`, is read through its name below)
        let qualified_type = k == "qualified_identifier"
            && node.child_by_field_name("name").is_some_and(|c| c.kind() == "type_identifier")
            && node
                .child_by_field_name("scope")
                .is_some_and(|c| matches!(c.kind(), "namespace_identifier" | "type_identifier"));
        if TYPE_KINDS.contains(&k) || k == "identifier" || qualified_type {
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
        let sub = if matches!(k, "type_arguments" | "type_parameter" | "type_argument_list") {
            "generic_arg"
        } else {
            ctx
        };
        // a parameter of a function type (`(ctx: RefinementCtx) => void`):
        // its type is a type, its name is not
        if matches!(k, "required_parameter" | "optional_parameter" | "rest_parameter") {
            if let Some(t) = node.child_by_field_name("type") {
                stack.push((t, "parameter_type"));
            }
            continue;
        }
        stack.extend(kids(node).into_iter().map(|c| (c, sub)));
    }
}

/// The names of a Scala `stable_type_identifier`, in the order written.
fn stable_path(n: Node, src: &[u8]) -> Vec<String> {
    let mut out = Vec::new();
    let mut stack = vec![n];
    while let Some(x) = stack.pop() {
        let ks = kids(x);
        if ks.is_empty() {
            out.push(text(x, src));
        }
        stack.extend(ks.into_iter().rev());
    }
    out
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
            // Go `(*Response, Directive, error)`: every result names its type
            Some("result") if child.kind() == "parameter_list" => {
                for one in kids(child) {
                    types_in(one, src, "return_type", out);
                }
            }
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
                    if let Some(ps) = params_field(d) {
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
        // Kotlin `@Deprecated("old")`: the annotation's type, before its arguments
        if t.kind() == "constructor_invocation" {
            if let Some(ty) = kids(t).into_iter().next() {
                t = ty;
            }
        }
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
pub fn bound_name(n: Node, src: &[u8]) -> Option<String> {
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

/// Generic types that hold the value they are about: the field's type is
/// what they wrap (`Box<dyn Transport>`, `Optional<Foo>`, `Arc<Mutex<Pool>>`).
const WRAPPERS: &[&str] = &[
    "Box",
    "Arc",
    "Rc",
    "Weak",
    "Option",
    "Cell",
    "RefCell",
    "Mutex",
    "RwLock",
    "Pin",
    "Cow",
    "Optional",
    "Lazy",
    "Provider",
    "Supplier",
    "AtomicReference",
    "Ref",
    "Mono",
    "Future",
    "Promise",
    "Readonly",
    "Partial",
    "Required",
    "unique_ptr",
    "shared_ptr",
    "weak_ptr",
    "optional",
    "CompletableFuture",
    "ListenableFuture",
    "Deferred",
];

/// Wrappers that hold their value closed: what is called on one is the
/// wrapper's own method until it is opened (`unwrap()`, `get()`, `await`,
/// `?`, `.Result`). The others (`Box`, `Arc`, `Rc`, `Readonly`) answer
/// for what they hold.
const SEALED: &[&str] = &[
    "Option",
    "Optional",
    "optional",
    "Mutex",
    "RwLock",
    "RefCell",
    "Cell",
    "Weak",
    "weak_ptr",
    "AtomicReference",
    "Provider",
    "Supplier",
    "Mono",
    "Future",
    "Promise",
    "CompletableFuture",
    "ListenableFuture",
    "Deferred",
    "Result",
    "Task",
    "ValueTask",
];

/// A wrapper only in one language's library, where a project of another is
/// free to define a type of that name (retrofit's `Result<T>`): Rust
/// `Result`, C# `Task` / `ValueTask`.
fn language_wrapper(name: &str, n: Node) -> bool {
    match name {
        "Result" => n.language().name() == Some("rust"),
        "Task" | "ValueTask" => n.language().name() == Some("c_sharp"),
        _ => false,
    }
}

/// Wrappers of Rust's standard library: elsewhere the name is a type like
/// any other (a Java project's own `Box<T>`).
const RUST_WRAPPERS: &[&str] = &["Box", "Arc", "Rc", "Cell", "RefCell", "Pin", "Cow"];

/// Whether `name` is in one of the wrapper tables, whatever the language.
pub fn wrapper_name(name: &str) -> bool {
    WRAPPERS.contains(&name)
        || SEALED.contains(&name)
        || matches!(name, "Result" | "Task" | "ValueTask")
}

fn is_wrapper(name: &str, n: Node) -> bool {
    // (the project's own `Optional<T>`, `Future<T>`, in a file that sees it)
    if crate::parse::shadowed(name) {
        return false;
    }
    if RUST_WRAPPERS.contains(&name) {
        return n.language().name() == Some("rust");
    }
    WRAPPERS.contains(&name) || language_wrapper(name, n)
}

/// Whether the type a position writes is a wrapper's (`Option<Foo>`,
/// `Promise<Foo>`), not a tuple or a union with one in it.
fn wrapper_named(t: Node, src: &[u8]) -> bool {
    path_of(t, src).and_then(|p| p.last().cloned()).is_some_and(|name| is_wrapper(&name, t))
}

/// Whether a type position is a wrapper that holds its value closed, or one
/// around such a wrapper (`Arc<Mutex<Pool>>`), around a type it names.
fn sealed_type(t: Node, src: &[u8]) -> bool {
    // the type written is itself the wrapper (a tuple of one is not)
    if !wrapper_named(t, src) {
        return false;
    }
    let mut v = Vec::new();
    types_in(t, src, "", &mut v);
    let last = |p: &Vec<String>| p.last().cloned().unwrap_or_default();
    if v.len() < 2 {
        return false;
    }
    let sealed = |name: &str| SEALED.contains(&name) && is_wrapper(name, t);
    v[..v.len() - 1].iter().any(|(p, _)| sealed(&last(p)))
}

/// The first type a type position names, through wrappers.
fn first_type(n: Node, src: &[u8]) -> Option<Vec<String>> {
    if matches!(n.kind(), "implicit_type" | "placeholder_type_specifier")
        || (n.child_count() == 0 && text(n, src) == "var")
    {
        return None; // `var`, `auto`: the type is the value's
    }
    let mut v = Vec::new();
    types_in(n, src, "", &mut v);
    // types_in yields the outer type, then its arguments (in reverse order)
    let mut it = v.into_iter().map(|(p, _)| p);
    let first = it.next()?;
    let wrapper = first.last().is_some_and(|l| is_wrapper(l, n));
    if wrapper {
        // what it holds is its first type argument (`Option<Space<M>>` holds
        // a `Space`, not the `M` written deepest)
        // (only where that argument is written with arguments of its own:
        // any other is the last the walk gives, as before)
        let held = first_type_argument(n).filter(|a| first_type_argument(*a).is_some());
        if let Some(inner) = held.and_then(|a| first_type(a, src)) {
            return Some(inner);
        }
        let rest: Vec<Vec<String>> = it.collect();
        if let Some(inner) = rest.into_iter().last() {
            return Some(inner);
        }
    }
    Some(first)
}

/// The first type argument written in `n`, of the outermost type that has any.
fn first_type_argument(n: Node) -> Option<Node> {
    let mut level = vec![n];
    for _ in 0..6 {
        let mut next = Vec::new();
        for x in level {
            if matches!(
                x.kind(),
                "type_arguments" | "type_argument_list" | "template_argument_list"
            ) {
                return kids(x).into_iter().find(|c| c.kind() != "lifetime" && !is_comment(c));
            }
            next.extend(kids(x));
        }
        level = next;
    }
    None
}

/// What a value constructs: `Foo()`, `new Foo()`, `Foo::new()`, `Foo{}`,
/// `&Foo{}`, `Foo.new`. The path is the callee's; the resolver keeps it only
/// when it names a type.
/// A cast: what the value is, written at the site.
fn is_cast(n: Node) -> bool {
    matches!(
        n.kind(),
        "cast_expression" | "as_expression" | "type_assertion" | "type_assertion_expression"
    )
}

fn value_type(v: Node, src: &[u8]) -> Option<Vec<String>> {
    match v.kind() {
        // a cast says what the value is: C# / Java `(Foo)x`, C# `x as Foo`
        // (`right`), TS `x as Foo` and Kotlin `x as Foo` / `as?` (the type
        // child), TS `<Foo>x`
        "cast_expression" => v.child_by_field_name("type").and_then(|t| first_type(t, src)),
        "as_expression" => v
            .child_by_field_name("right")
            .or_else(|| {
                kids(v).into_iter().rev().find(|c| {
                    matches!(
                        c.kind(),
                        "type_identifier" | "user_type" | "generic_type" | "nullable_type"
                    ) || c.kind().ends_with("_type")
                })
            })
            .and_then(|t| first_type(t, src)),
        // Go `x.(*Foo)`
        "type_assertion_expression" => {
            v.child_by_field_name("type").and_then(|t| first_type(t, src))
        }
        "type_assertion" => kids(v)
            .into_iter()
            .find(|c| c.kind() == "type_arguments")
            .and_then(|t| kids(t).into_iter().next())
            .and_then(|t| first_type(t, src)),
        "parenthesized_expression" | "reference_expression" | "unary_expression" => {
            kids(v).into_iter().find_map(|c| value_type(c, src))
        }
        // `let x = load()?`, `val u = await find()`: what the wrapper holds
        "await_expression" | "try_expression" => {
            let mut p = kids(v).into_iter().find_map(|c| value_type(c, src))?;
            p.push(OPENED.into());
            Some(p)
        }
        // a chain (`builder(x).tag(y)`) is kept: the resolver follows what
        // each call returns; one that starts at something unnameable is not
        // (a mapping of a collection, `xs.map { it.owner }`, is the
        // collection of what each gives)
        _ => {
            if matches!(
                v.kind(),
                "list_comprehension" | "set_comprehension" | "generator_expression"
            ) {
                return path_of(v, src).filter(|p| p.first().is_some_and(|s| s != UNKNOWN));
            }
            // (a member read with no call is what the member holds: `req :=
            // dl.Request`)
            let read = || {
                matches!(
                    v.kind(),
                    "selector_expression"
                        | "member_expression"
                        | "field_expression"
                        | "navigation_expression"
                        | "member_access_expression"
                        | "field_access"
                        | "attribute"
                )
                .then(|| path_of(v, src))
                .flatten()
                .filter(|p| p.len() >= 2 && !p.iter().any(|s| s == "()" || s.starts_with('[')))
            };
            // (`const request = first`: what the name holds)
            if matches!(v.kind(), "identifier" | "simple_identifier") {
                return path_of(v, src)
                    .filter(|p| p.len() == 1 && p[0] != "self" && p[0] != "super");
            }
            let Some(called) = callee(v, src).map(|(_, p)| p) else {
                return read().filter(|p| p.first().is_some_and(|s| s != UNKNOWN));
            };
            let mapped = if is_call_kind(v.kind()) { mapped_call(v, src, &called) } else { None };
            Some(mapped.unwrap_or(called)).filter(|p| p.first().is_some_and(|s| s != UNKNOWN))
        }
    }
}

/// C, C++: the functions a node puts in slots of a struct. An initializer
/// of a variable (`static funcs byName = { .close = netClose, netRead }`:
/// by name, or by position among its elements) or an assignment to a member
/// (`c->f.read = netRead`). Only a bare name is a function put there; the
/// resolver keeps it when it names one.
fn slot_facts(n: Node, src: &[u8]) -> Vec<crate::facts::SlotFact> {
    let mut out = Vec::new();
    let mut fact = |holder: Vec<String>, field: Option<String>, position: Option<u32>, v: Node| {
        out.push(crate::facts::SlotFact {
            holder,
            field,
            position,
            value: text(v, src),
            line: line(v),
            from_def: None,
            byte: v.start_byte(),
        });
    };
    match n.kind() {
        "init_declarator" => {
            let Some(list) =
                n.child_by_field_name("value").filter(|v| v.kind() == "initializer_list")
            else {
                return out;
            };
            let Some(name) = n.child_by_field_name("declarator").and_then(|d| bound_name(d, src))
            else {
                return out;
            };
            for (i, item) in kids(list).into_iter().filter(|c| !is_comment(c)).enumerate() {
                match item.kind() {
                    "identifier" => fact(vec![name.clone()], None, u32::try_from(i).ok(), item),
                    "initializer_pair" => {
                        let designators: Vec<Node> = kids(item)
                            .into_iter()
                            .filter(|c| c.kind() == "field_designator")
                            .collect();
                        let value =
                            item.child_by_field_name("value").filter(|v| v.kind() == "identifier");
                        if let ([one], Some(v)) = (designators.as_slice(), value) {
                            let field = kids(*one).into_iter().next().map(|f| text(f, src));
                            fact(vec![name.clone()], field, None, v);
                        }
                    }
                    _ => {}
                }
            }
        }
        "assignment_expression" => {
            let left = n.child_by_field_name("left").filter(|l| l.kind() == "field_expression");
            let right = n.child_by_field_name("right").filter(|r| r.kind() == "identifier");
            let op = n.child_by_field_name("operator").map(|o| text(o, src));
            if let (Some(l), Some(r), Some("=")) = (left, right, op.as_deref()) {
                if let Some(mut path) = path_of(l, src) {
                    if path.len() >= 2 {
                        let field = path.pop();
                        fact(path, field, None, r);
                    }
                }
            }
        }
        _ => {}
    }
    out
}

/// The name a C++ deduction guide is for (`Wrapper(T&&, bool) ->
/// Wrapper<T, true>;`): a declaration with no type before its declarator, a
/// trailing result, and the result's template the declarator's own name.
fn deduction_guide(n: Node, src: &[u8]) -> Option<String> {
    if n.kind() != "declaration" || n.child_by_field_name("type").is_some() {
        return None;
    }
    let decl = n.child_by_field_name("declarator").filter(|d| d.kind() == "function_declarator")?;
    let name =
        text(decl.child_by_field_name("declarator").filter(|d| d.kind() == "identifier")?, src);
    let trailing = kids(decl).into_iter().find(|c| c.kind() == "trailing_return_type")?;
    let mut t = kids(trailing).into_iter().find(|c| c.kind() == "type_descriptor")?;
    t = t.child_by_field_name("type")?;
    let of = if t.kind() == "template_type" { t.child_by_field_name("name")? } else { t };
    (text(of, src) == name).then_some(name)
}

/// The arguments a type writes for its template, as written: `Response`,
/// `true` of `AsyncWrapper<Response, true>` (through a `type_descriptor`).
fn template_args(t: Node, src: &[u8]) -> Vec<String> {
    let mut t = t;
    for _ in 0..3 {
        match t.kind() {
            "type_descriptor" => match t.child_by_field_name("type") {
                Some(x) => t = x,
                None => return Vec::new(),
            },
            "qualified_identifier" => match t.child_by_field_name("name") {
                Some(x) => t = x,
                None => return Vec::new(),
            },
            _ => break,
        }
    }
    if t.kind() != "template_type" {
        return Vec::new();
    }
    t.child_by_field_name("arguments")
        .map(|a| kids(a).into_iter().filter(|c| !is_comment(c)).map(|c| text(c, src)).collect())
        .unwrap_or_default()
}

/// A C++ variable the grammar took for a function's declaration: its name,
/// its type's path, the names passed and the calls passed.
/// A misread variable: its name, its type, the bare names and calls passed
/// to it, the qualified names passed beside a `this` (`std::thread
/// t(Env::Entry, this);`), and whether a `this` was passed.
type Misread<'t> = (String, Vec<String>, Vec<Node<'t>>, Vec<Node<'t>>, Vec<Node<'t>>, bool);

/// C++ `Pool pool(context);` where every "parameter type" is a name the
/// function has as a local or parameter, or `Random rnd(seed());` with a
/// call where a type would be: a variable constructed from them, which the
/// grammar reads as the declaration of a function.
fn misread_variable<'t>(
    n: Node<'t>,
    src: &[u8],
    anc: &[Node],
    locals: &[Local],
) -> Option<Misread<'t>> {
    if n.kind() != "declaration" || anc.last()?.kind() != "compound_statement" {
        return None;
    }
    let decl = n.child_by_field_name("declarator").filter(|d| d.kind() == "function_declarator")?;
    let name = decl.child_by_field_name("declarator").filter(|d| d.kind() == "identifier")?;
    let params = params_field(decl)?;
    let mut args = Vec::new();
    // `Random rnd(test::RandomSeed());`: a call where a parameter's type
    // would be written, which only a variable's arguments have
    let mut calls = Vec::new();
    // `std::thread t(PosixEnv::Entry, this);`: `this` is no type, so what it
    // is passed with are arguments, a qualified name among them
    let mut this_arg = false;
    let mut qualified = Vec::new();
    for p in kids(params).into_iter().filter(|c| !is_comment(c)) {
        if p.kind() != "parameter_declaration" {
            return None;
        }
        let ty = p.child_by_field_name("type");
        if p.named_child_count() == 1 {
            match ty {
                Some(t) if t.kind() == "type_identifier" && text(t, src) == "this" => {
                    this_arg = true;
                    continue;
                }
                Some(t) if t.kind() == "qualified_identifier" => {
                    qualified.push(t);
                    continue;
                }
                _ => {}
            }
        }
        match p.child_by_field_name("declarator") {
            Some(d)
                if d.kind() == "abstract_function_declarator"
                    && d.child_by_field_name("declarator").is_none()
                    && params_field(d).is_some_and(|ps| kids(ps).is_empty()) =>
            {
                calls.push(p.child_by_field_name("type")?);
                continue;
            }
            // a bare name and nothing else: no declarator, no qualifier
            Some(_) => return None,
            None => {}
        }
        let ty = p.child_by_field_name("type").filter(|t| t.kind() == "type_identifier")?;
        if p.named_child_count() != 1 {
            return None;
        }
        args.push(ty);
    }
    if !this_arg && !qualified.is_empty() {
        return None; // `Pool pool(std::string);` declares a function
    }
    if args.is_empty() && calls.is_empty() && !this_arg {
        return None; // `Pool pool();` declares a function
    }
    let func = anc
        .iter()
        .rev()
        .find(|a| matches!(a.kind(), "function_definition" | "lambda_expression"))?;
    let (lo, at) = (func.start_byte(), n.start_byte());
    let params_of = func
        .child_by_field_name("declarator")
        .and_then(|d| {
            let mut cur = d;
            for _ in 0..4 {
                if let Some(p) = params_field(cur) {
                    return Some(p);
                }
                cur = cur.child_by_field_name("declarator")?;
            }
            None
        })
        .map(|ps| {
            kids(ps)
                .into_iter()
                .filter_map(|p| {
                    p.child_by_field_name("declarator").and_then(|d| bound_name(d, src))
                })
                .collect::<Vec<String>>()
        })
        .unwrap_or_default();
    let known = |a: &Node| {
        let t = text(*a, src);
        params_of.contains(&t) || locals.iter().any(|l| l.name == t && lo <= l.byte && l.byte < at)
    };
    if !this_arg && !args.iter().all(known) {
        return None;
    }
    // (as a declared local's type is written: `first_type`)
    let ty = first_type(n.child_by_field_name("type")?, src)?;
    Some((text(name, src), ty, args, calls, qualified, this_arg))
}

/// A construction whose type is written on the declaration and not at the
/// call: C# `Box b = new(1, 2)` (a local, a field, a property), C++ `Buf b(3)`
/// and `Buf b{3}`. The type's path and how many arguments are passed.
fn declared_construction(
    n: Node,
    src: &[u8],
    anc: &[Node],
    lang: Option<crate::lang::Lang>,
) -> Option<(Vec<String>, Option<u8>)> {
    let count =
        |list: Node| u8::try_from(kids(list).iter().filter(|c| !is_comment(c)).count()).ok();
    match n.kind() {
        "implicit_object_creation_expression" => {
            let decl =
                anc.iter().rev().take(3).find(|a| {
                    matches!(a.kind(), "variable_declaration" | "property_declaration")
                })?;
            let path = path_of(decl.child_by_field_name("type")?, src)?;
            let args = kids(n).into_iter().find(|c| c.kind() == "argument_list").and_then(count);
            Some((path, args))
        }
        "init_declarator" if lang == Some(crate::lang::Lang::Cpp) => {
            let value = n.child_by_field_name("value")?;
            if !matches!(value.kind(), "argument_list" | "initializer_list") {
                return None;
            }
            // the object itself, not a pointer or a reference to one
            if n.child_by_field_name("declarator")?.kind() != "identifier" {
                return None;
            }
            let decl = anc.last().filter(|a| a.kind() == "declaration")?;
            let path = path_of(decl.child_by_field_name("type")?, src)?;
            Some((path, count(value)))
        }
        _ => None,
    }
}

/// The receiver type of a callable's last function-typed parameter that has
/// one: Kotlin `fun withIndent(block: Builder.() -> Unit)`.
fn lambda_receiver(n: Node, src: &[u8]) -> Option<Vec<String>> {
    let params = kids(n).into_iter().find(|c| c.kind() == "function_value_parameters")?;
    kids(params).into_iter().rev().filter(|p| p.kind() == "parameter").find_map(|p| {
        let mut stack = kids(p);
        while let Some(c) = stack.pop() {
            match c.kind() {
                "function_type" => {
                    let recv = kids(c).into_iter().find(|x| x.kind() == "receiver_type")?;
                    return first_type(recv, src);
                }
                "nullable_type" | "parenthesized_type" | "type_modifiers" => stack.extend(kids(c)),
                _ => {}
            }
        }
        None
    })
}

/// A Kotlin call with a trailing lambda: the lambda's byte range and where
/// its receiver, if it has one, comes from.
fn receiver_lambda(n: Node, src: &[u8]) -> Option<(usize, usize, crate::facts::ReceiverOf)> {
    use crate::facts::ReceiverOf;
    if n.kind() != "call_expression" {
        return None;
    }
    let suffix = kids(n).into_iter().find(|c| c.kind() == "call_suffix")?;
    let ks = kids(suffix);
    let lambda = ks.iter().find(|c| c.kind() == "annotated_lambda")?;
    let (_, path) = callee(n, src)?;
    let last = path.last()?.as_str();
    let of = match last {
        // the scope functions whose lambda has the value as its receiver
        "apply" | "run" if path.len() > 1 => ReceiverOf::Value(path[..path.len() - 1].to_vec()),
        "with" if path.len() == 1 => {
            let args = ks.iter().find(|c| c.kind() == "value_arguments")?;
            let arg = kids(*args).into_iter().next()?;
            let value = kids(arg).into_iter().last()?;
            ReceiverOf::Value(path_of(value, src).filter(|p| p[0] != UNKNOWN)?)
        }
        // `it`, not a receiver
        "also" | "let" | "takeIf" | "takeUnless" => return None,
        _ if path[0] == UNKNOWN => return None,
        _ => ReceiverOf::Callee(path),
    };
    Some((lambda.start_byte(), lambda.end_byte(), of))
}

/// Give each use the innermost receiver lambda around it, and each lambda the
/// one around it.
fn bind_receivers(
    found: &[(usize, usize, crate::facts::ReceiverOf)],
    refs: &mut [RefFact],
) -> Vec<crate::facts::ReceiverScope> {
    if found.is_empty() || found.len() > usize::from(u16::MAX) {
        return Vec::new();
    }
    // smallest range first: the first that holds a position is the innermost
    let mut order: Vec<usize> = (0..found.len()).collect();
    order.sort_by_key(|&i| found[i].1 - found[i].0);
    let innermost = |byte: usize, inside: Option<usize>| {
        order.iter().copied().find(|&i| {
            Some(i) != inside
                && found[i].0 <= byte
                && byte < found[i].1
                && inside.is_none_or(|x| found[i].1 - found[i].0 > found[x].1 - found[x].0)
        })
    };
    for r in refs.iter_mut() {
        r.recv = innermost(r.byte, None).and_then(|i| u16::try_from(i).ok());
    }
    found
        .iter()
        .enumerate()
        .map(|(i, (start, _, of))| crate::facts::ReceiverScope {
            of: of.clone(),
            outer: innermost(*start, Some(i)).and_then(|o| u16::try_from(o).ok()),
        })
        .collect()
}

/// A function written where a value goes: its `return` is its own.
fn is_closure(kind: &str) -> bool {
    matches!(
        kind,
        "arrow_function"
            | "function_expression"
            | "lambda_expression"
            | "lambda_literal"
            | "anonymous_function"
            | "anonymous_function_creation_expression"
            | "func_literal"
            | "closure_expression"
            | "lambda"
            | "do_block"
    )
}

/// The value of a `return` statement.
fn returned_value<'t>(n: Node<'t>, src: &[u8]) -> Option<Node<'t>> {
    let v = match n.kind() {
        "return_statement" | "return_expression" | "return" => {
            kids(n).into_iter().find(|c| !is_comment(c))?
        }
        // Kotlin `return x`, `return@label x` (also `throw`, `break`)
        "jump_expression" if text(n, src).starts_with("return") => {
            kids(n).into_iter().rfind(|c| c.kind() != "label" && !is_comment(c))?
        }
        "control_transfer_statement" => n.child_by_field_name("result")?,
        _ => return None,
    };
    // Ruby `return Foo.new`: the one argument
    if v.kind() == "argument_list" {
        let ks = kids(v);
        return (ks.len() == 1).then(|| ks[0]);
    }
    Some(v)
}

/// The value a definition's body is, where it is an expression: Kotlin `fun
/// f() = x`, Scala `def f = x`, `() => x`, the last expression of a Ruby method.
fn body_value(n: Node) -> Option<Node> {
    if let Some(fb) = kids(n).into_iter().find(|c| c.kind() == "function_body") {
        return fb.child(0).filter(|c| c.kind() == "=").and_then(|_| kids(fb).into_iter().last());
    }
    let body = n.child_by_field_name("body")?;
    match body.kind() {
        "body_statement" => kids(body).into_iter().rfind(|c| !is_comment(c)),
        k if k.contains("block") || k.contains("statement") || k == "function_body" => None,
        _ if matches!(n.kind(), "arrow_function" | "function_definition" | "lambda_expression") => {
            Some(body)
        }
        _ => None,
    }
}

/// What a returned value constructs or calls; `self` for the object itself.
fn returned_path(v: Node, src: &[u8]) -> Option<Vec<String>> {
    if path_of(v, src).is_some_and(|p| p == ["self"]) {
        return Some(vec!["self".into()]);
    }
    value_type(v, src)
}

/// `return [copiedText, copy]`: the names a callable returns as a tuple or
/// an array literal, the first time it does.
/// `return { show, hide: close }`: the members of an object literal a
/// function returns, each with the name its value is.
fn note_members(def: &mut Def, v: Node, src: &[u8]) {
    if !def.returned_members.is_empty() || v.kind() != "object" {
        return;
    }
    for c in kids(v) {
        match c.kind() {
            "shorthand_property_identifier" => {
                let t = text(c, src);
                def.returned_members.push((t.clone(), t));
            }
            "pair" => {
                let key =
                    c.child_by_field_name("key").filter(|k| k.kind() == "property_identifier");
                let value = c.child_by_field_name("value").filter(|x| x.kind() == "identifier");
                if let (Some(k), Some(x)) = (key, value) {
                    def.returned_members.push((text(k, src), text(x, src)));
                }
            }
            _ => {}
        }
    }
}

fn note_parts(def: &mut Def, v: Node, src: &[u8]) {
    if !def.returned_parts.is_empty()
        || !matches!(v.kind(), "array" | "tuple_expression" | "tuple" | "list" | "array_literal")
    {
        return;
    }
    // (an item that is no name keeps its place, empty)
    let names: Vec<String> = kids(v)
        .into_iter()
        .filter(|c| !is_comment(c))
        .map(|c| {
            // (C#'s and Swift's items sit in an argument / a `value`)
            let c =
                if c.kind() == "argument" { kids(c).into_iter().last().unwrap_or(c) } else { c };
            if matches!(c.kind(), "identifier" | "simple_identifier") {
                text(c, src)
            } else {
                String::new()
            }
        })
        .collect();
    if names.len() >= 2 && names.iter().any(|n| !n.is_empty()) {
        def.returned_parts = names;
    }
}

fn note_return(def: &mut Def, path: Vec<String>) {
    if def.returns.len() < 4 && !def.returns.contains(&path) {
        def.returns.push(path);
    }
}

fn is_comment(n: &Node) -> bool {
    n.kind().contains("comment")
}

/// How many arguments a call passes: the entries of its argument list, and a
/// trailing lambda (Kotlin, Swift) as one more.
fn arg_count(n: Node) -> Option<u8> {
    // C# `new Options { Flag = true }`: no list, no argument
    if n.kind() == "object_creation_expression"
        && n.child_by_field_name("arguments").is_none()
        && n.child_by_field_name("initializer").is_some()
    {
        return Some(0);
    }
    let (list, lambdas) = argument_list(n)?;
    let passed = list.map_or(0, |l| kids(l).iter().filter(|c| !is_comment(c)).count());
    u8::try_from(passed + lambdas).ok()
}

/// Whether a call passes `this` / `self` as one of its arguments.
fn passes_self(n: Node, src: &[u8]) -> bool {
    let list = n.child_by_field_name("arguments").or_else(|| {
        let sfx = kids(n).into_iter().find(|c| c.kind() == "call_suffix")?;
        kids(sfx).into_iter().find(|c| c.kind() == "value_arguments")
    });
    list.is_some_and(|l| {
        kids(l).into_iter().any(|a| {
            // the argument itself, or the value of `name = this`
            let v =
                if a.kind().contains("argument") { kids(a).into_iter().last() } else { Some(a) };
            v.and_then(|v| path_of(v, src)).is_some_and(|p| p == ["self"])
        })
    })
}

/// RSpec `shared_examples 'name' do … end` (true) or `it_behaves_like 'name'`
/// / `include_examples 'name'` (false), with the name.
fn shared_examples_call(n: Node, src: &[u8]) -> Option<(String, bool)> {
    if n.kind() != "call" || n.child_by_field_name("receiver").is_some() {
        return None;
    }
    let method = text(n.child_by_field_name("method")?, src);
    let declares = match method.as_str() {
        "shared_examples" | "shared_examples_for" | "shared_context" => true,
        "it_behaves_like" | "it_should_behave_like" | "include_examples" | "include_context" => {
            false
        }
        _ => return None,
    };
    let first = kids(n.child_by_field_name("arguments")?).into_iter().next()?;
    let name = match first.kind() {
        "string" => string_value(first, src),
        "simple_symbol" => text(first, src).trim_start_matches(':').to_string(),
        _ => return None,
    };
    Some((name, declares))
}

/// The names a Ruby call declares on a type without a `def`: `attr_accessor
/// :a`, `def_delegators :@x, :a, :b`, `Struct.new(:a, :b)` (and any `X.new`
/// given only symbols, such as a struct-like base class).
fn ruby_attrs(n: Node, src: &[u8]) -> Vec<String> {
    if n.kind() != "call" {
        return Vec::new();
    }
    let Some(method) = n.child_by_field_name("method").map(|m| text(m, src)) else {
        return Vec::new();
    };
    let Some(args) = n.child_by_field_name("arguments") else { return Vec::new() };
    let ks = kids(args);
    let symbols: Vec<String> = ks
        .iter()
        .filter(|a| a.kind() == "simple_symbol")
        .map(|a| text(*a, src).trim_start_matches(':').to_string())
        .filter(|s| !s.starts_with('@'))
        .collect();
    let declares = n.child_by_field_name("receiver").is_none()
        && matches!(
            method.as_str(),
            "attr_accessor"
                | "attr_reader"
                | "attr_writer"
                | "def_delegators"
                | "def_delegator"
                | "delegate"
                | "alias_method"
                | "memoized"
        );
    let structure = method == "new" && !ks.is_empty() && symbols.len() == ks.len();
    if declares || structure {
        symbols
    } else {
        Vec::new()
    }
}

/// The names of the type parameters a declaration writes: `<T, R>`.
fn type_params(n: Node, src: &[u8]) -> Vec<String> {
    // C++ writes them on the `template <typename T>` around the declaration
    let template = n.parent().filter(|p| p.kind() == "template_declaration");
    let lists: Vec<Node> = match template {
        Some(t) => kids(t).into_iter().filter(|c| c.kind() == "template_parameter_list").collect(),
        None => Vec::new(),
    };
    if !lists.is_empty() {
        return lists
            .into_iter()
            .flat_map(kids)
            .filter(|tp| tp.kind().contains("type_parameter"))
            .filter_map(|tp| {
                tp.child_by_field_name("name")
                    .or_else(|| kids(tp).into_iter().find(|c| c.kind() == "type_identifier"))
                    .map(|c| text(c, src))
            })
            .collect();
    }
    type_params_bounds(n, src).0
}

/// The type parameters a declaration writes, and the first type each is
/// bound by (`<T extends Foo>`, `<S: Sink>`, `where T : Foo`, `[A <: Base]`).
fn type_params_bounds(n: Node, src: &[u8]) -> (Vec<String>, Vec<Option<Vec<String>>>) {
    let mut names: Vec<String> = Vec::new();
    let mut bounds: Vec<Option<Vec<String>>> = Vec::new();
    // Python 3.12 `def f[T: Base](…)`, `class C[U]` (PEP 695): one
    // `type_parameter` whose `type`s are the parameters, a bound written
    // `T: Base` (a `constrained_type`)
    if let Some(list) =
        n.child_by_field_name("type_parameters").filter(|l| l.kind() == "type_parameter")
    {
        for t in kids(list).into_iter().filter(|c| c.kind() == "type") {
            let inner = kids(t);
            match inner.first() {
                Some(c) if c.kind() == "identifier" => {
                    names.push(text(*c, src));
                    bounds.push(None);
                }
                Some(c) if c.kind() == "constrained_type" => {
                    let parts: Vec<Node> =
                        kids(*c).into_iter().filter(|x| x.kind() == "type").collect();
                    if let Some(name) = parts
                        .first()
                        .and_then(|p| kids(*p).into_iter().find(|x| x.kind() == "identifier"))
                    {
                        names.push(text(name, src));
                        bounds.push(parts.get(1).and_then(|b| bound_path(&text(*b, src))));
                    }
                }
                _ => {}
            }
        }
        return (names, bounds);
    }
    let lists = kids(n)
        .into_iter()
        .filter(|c| matches!(c.kind(), "type_parameters" | "type_parameter_list"));
    for tp in lists.flat_map(kids) {
        // (Scala writes the names and their bounds side by side)
        if tp.kind().contains("identifier") {
            names.push(text(tp, src));
            bounds.push(None);
        } else if tp.kind() == "upper_bound" {
            // (`[A: Read]` asks for a `Read[A]`: it says nothing of `A`)
            if let Some(last) = bounds.last_mut().filter(|b| b.is_none()) {
                *last = bound_path(&text(tp, src));
            }
        } else if tp.kind().ends_with("bound") {
        } else if let Some(id) = kids(tp).into_iter().find(|c| c.kind().contains("identifier")) {
            names.push(text(id, src));
            let written = &src[id.end_byte().min(tp.end_byte())..tp.end_byte()];
            bounds.push(bound_path(&String::from_utf8_lossy(written)));
        }
    }
    // Scala 3 `extension [A, B](x: F[A]) def m(…)`: the extension's are the
    // method's too
    let around = n.parent().into_iter().chain(n.parent().and_then(|p| p.parent()));
    if let Some(ext) = around.into_iter().find(|a| a.kind() == "extension_definition") {
        let (more, their) = type_params_bounds(ext, src);
        names.extend(more);
        bounds.extend(their);
    }
    // `where T: Foo`, `where T : Foo`
    let clauses = kids(n).into_iter().filter(|c| {
        matches!(
            c.kind(),
            "where_clause" | "type_constraints" | "type_parameter_constraints_clause"
        )
    });
    for clause in clauses {
        let rows = if clause.kind() == "type_parameter_constraints_clause" {
            vec![clause]
        } else {
            kids(clause)
        };
        for row in rows {
            let row = match kids(row).as_slice() {
                [only] if only.kind().ends_with("constraint") => *only,
                _ => row,
            };
            let ks = kids(row);
            let Some((left, rest)) = ks.split_first() else { continue };
            let name = text(*left, src);
            let Some(i) = names.iter().position(|x| *x == name) else { continue };
            if let (None, Some(last)) = (&bounds[i], rest.last()) {
                let written = &src[left.end_byte().min(last.end_byte())..last.end_byte()];
                bounds[i] = bound_path(&String::from_utf8_lossy(written));
            }
        }
    }
    // (a bound that is the parameter itself, or another parameter, says nothing)
    for b in bounds.iter_mut() {
        if b.as_ref().is_some_and(|p| p.len() == 1 && names.contains(&p[0])) {
            *b = None;
        }
    }
    (names, bounds)
}

/// The type a bound names, from its text: `: Sink + Send`, `extends Foo<T>`,
/// `<: Base`. One named type, alone or the first of several; anything else
/// (`FnMut(Match) -> bool`, `keyof T`, `[A, B]`, `~int | ~string`, a default
/// `= Foo`) is no type whose members the parameter has.
fn bound_path(written: &str) -> Option<Vec<String>> {
    let mut t = written.trim();
    for lead in ["<:", ":", "extends ", "any ", "some "] {
        if let Some(rest) = t.strip_prefix(lead) {
            t = rest.trim_start();
        }
    }
    let name_end = t
        .char_indices()
        .find(|(_, c)| !(c.is_alphanumeric() || matches!(c, '_' | '.' | ':')))
        .map_or(t.len(), |(i, _)| i);
    let (name, mut rest) = (&t[..name_end], t[name_end..].trim_start());
    if rest.starts_with('<') {
        let mut depth = 0usize;
        let close = rest.char_indices().find(|(_, c)| {
            match c {
                '<' => depth += 1,
                '>' => depth -= 1,
                _ => {}
            }
            depth == 0
        })?;
        rest = rest[close.0 + 1..].trim_start();
    }
    if !(rest.is_empty() || rest.starts_with(['+', '&', ','])) {
        return None;
    }
    let path: Vec<String> =
        name.split(['.', ':']).filter(|p| !p.is_empty()).map(str::to_string).collect();
    let named = !path.is_empty()
        && path.iter().all(|p| p.chars().next().is_some_and(|c| c.is_alphabetic() || c == '_'));
    named.then_some(path)
}

/// The type parameters of a type declaration whose bound names the type
/// itself: `T` in `Builder<T : Builder<T>>`, `B<T extends B<T>>`, `where T :
/// B<T>`.
fn self_params(n: Node, src: &[u8], name: &str) -> Vec<String> {
    let word = |hay: &str| {
        hay.match_indices(name).any(|(i, _)| {
            let before = hay[..i].chars().next_back();
            let after = hay[i + name.len()..].chars().next();
            !before.is_some_and(|c| c.is_alphanumeric() || c == '_')
                && !after.is_some_and(|c| c.is_alphanumeric() || c == '_')
        })
    };
    let mut out = Vec::new();
    for list in kids(n) {
        match list.kind() {
            "type_parameters" | "type_parameter_list" => {
                for tp in kids(list) {
                    let ks = kids(tp);
                    let Some(id) = ks.iter().find(|c| c.kind().contains("identifier")) else {
                        continue;
                    };
                    let bound: String = ks
                        .iter()
                        .filter(|c| c.id() != id.id())
                        .map(|c| text(*c, src))
                        .collect::<Vec<_>>()
                        .join(" ");
                    if word(&bound) {
                        out.push(text(*id, src));
                    }
                }
            }
            // C# `where T : B<T>`
            "type_parameter_constraints_clause" => {
                let ks = kids(list);
                if let Some(id) = ks.first() {
                    let rest: String =
                        ks[1..].iter().map(|c| text(*c, src)).collect::<Vec<_>>().join(" ");
                    if word(&rest) {
                        out.push(text(*id, src));
                    }
                }
            }
            _ => {}
        }
    }
    out
}

/// The call a declaration's value is (`val x = f(…)`, `var x = a.b<T>()`).
fn declared_call(n: Node) -> Option<Node> {
    fn call(c: Node) -> Option<Node> {
        is_call_kind(c.kind()).then_some(c)
    }
    value_node(n).and_then(call).or_else(|| {
        // Kotlin keeps the value beside the declaration; C# in the declarator
        kids(n).into_iter().find_map(|c| call(c).or_else(|| kids(c).into_iter().find_map(call)))
    })
}

/// The type a call is handed explicitly: its one type argument (`mock<Foo>()`,
/// `sp.GetService<IFoo>()`) or the one class literal among its arguments
/// (`create(Service::class.java)`, `create(Service.class)`, `typeof(Bar)`).
fn given_type(call: Node, src: &[u8]) -> Option<Vec<String>> {
    // the call's own type arguments: not those of a call it is chained on,
    // nor of its arguments
    let mut stack = kids(call);
    let mut hops = 0;
    while let Some(c) = stack.pop() {
        hops += 1;
        if hops > 40 {
            break;
        }
        let k = c.kind();
        if matches!(k, "type_arguments" | "type_argument_list") {
            let ks: Vec<Node> = kids(c).into_iter().filter(|x| !is_comment(x)).collect();
            return match ks.as_slice() {
                [one] => first_type(*one, src).or_else(|| path_of(*one, src)),
                _ => None,
            };
        }
        if is_call_kind(k) || matches!(k, "value_arguments" | "argument_list" | "arguments") {
            continue;
        }
        if matches!(
            k,
            "call_suffix" | "generic_name" | "member_access_expression" | "navigation_suffix"
        ) || c.child_count() <= 3
        {
            stack.extend(kids(c));
        }
    }
    let (list, _) = argument_list(call)?;
    let literals: Vec<Node> = kids(list?)
        .into_iter()
        .map(|a| {
            if a.kind().contains("argument") {
                kids(a).into_iter().last().unwrap_or(a)
            } else {
                a
            }
        })
        .filter(|v| {
            let t = text(*v, src);
            matches!(v.kind(), "class_literal" | "typeof_expression")
                || t.ends_with("::class")
                || t.ends_with("::class.java")
                || t.ends_with("::class.javaObjectType")
        })
        .collect();
    let [one] = literals.as_slice() else { return None };
    // the type named first in it
    let mut stack = vec![*one];
    while let Some(c) = stack.pop() {
        if matches!(c.kind(), "type_identifier" | "identifier" | "user_type" | "simple_identifier")
        {
            return first_type(c, src).or_else(|| path_of(c, src));
        }
        stack.extend(kids(c).into_iter().rev());
    }
    None
}

/// The argument list of a call, and how many trailing lambdas follow it.
fn argument_list(n: Node) -> Option<(Option<Node>, usize)> {
    if let Some(a) = n.child_by_field_name("arguments") {
        return Some((Some(a), 0));
    }
    if let Some(sfx) = kids(n).into_iter().find(|c| c.kind() == "call_suffix") {
        let ks = kids(sfx);
        let lambdas = ks.iter().filter(|c| c.kind().contains("lambda")).count();
        return Some((ks.into_iter().find(|c| c.kind() == "value_arguments"), lambdas));
    }
    kids(n).into_iter().find(|c| c.kind().contains("argument")).map(|a| (Some(a), 0))
}

/// What a value is, as an argument: a literal's kind, or the path the
/// resolver can type.
fn arg_kind(v: Node, src: &[u8]) -> crate::facts::Arg {
    use crate::facts::Arg;
    let k = v.kind();
    // Swift `.post`, `.status(301)`
    let implicit = implicit_member_call(v, src).or_else(|| {
        (k == "prefix_expression" && src.get(v.start_byte()) == Some(&b'.'))
            .then(|| v.child_by_field_name("target"))
            .flatten()
            .filter(|t| t.kind() == "simple_identifier")
            .map(|t| text(t, src))
    });
    if let Some(name) = implicit {
        return Arg::Implicit(name);
    }
    if k.contains("string") || k.contains("char_literal") || k == "character_literal" {
        Arg::Str
    } else if k.contains("integer")
        || k.contains("number")
        || k.contains("float")
        || k.contains("decimal")
        || matches!(k, "real_literal" | "long_literal" | "hex_literal" | "int_literal")
    {
        let t = text(v, src);
        let hex = t.starts_with("0x") || t.starts_with("0X");
        let int = !t.is_empty()
            && !t.contains('.')
            && (hex || !t.contains(['e', 'E', 'f', 'F', 'd', 'D']))
            && !t.ends_with(['l', 'L'])
            && t.trim_start_matches('-').chars().next().is_some_and(|c| c.is_ascii_digit());
        if int {
            Arg::Int
        } else {
            Arg::Num
        }
    } else if matches!(k, "true" | "false" | "boolean_literal" | "boolean" | "bool_literal") {
        Arg::Bool
    } else if matches!(k, "null" | "nil" | "none" | "null_literal" | "nullptr" | "undefined") {
        Arg::Null
    } else if is_closure(k) || k == "annotated_lambda" || k == "block" {
        Arg::Func
    } else if matches!(k, "class_literal" | "typeof_expression")
        || (k == "callable_reference" && text(v, src).ends_with("::class"))
    {
        Arg::Class
    } else if matches!(
        k,
        "comparison_expression"
            | "equality_expression"
            | "conjunction_expression"
            | "disjunction_expression"
            | "check_expression"
            | "instanceof_expression"
            | "is_pattern_expression"
    ) {
        Arg::Bool
    } else if matches!(k, "binary_expression" | "additive_expression" | "multiplicative_expression")
    {
        // `"a" + b` is a string; a comparison a flag; arithmetic on a number
        // a number
        let ks = kids(v);
        let mut cursor = v.walk();
        let op = v
            .children(&mut cursor)
            .find(|c| !c.is_named())
            .map(|c| c.kind().to_string())
            .unwrap_or_default();
        let has = |want: Arg| ks.iter().any(|c| arg_kind(*c, src) == want);
        if matches!(
            op.as_str(),
            "==" | "!=" | "<" | ">" | "<=" | ">=" | "&&" | "||" | "===" | "!=="
        ) {
            Arg::Bool
        } else if has(Arg::Str) {
            Arg::Str
        } else if matches!(op.as_str(), "+" | "-")
            && matches!(
                crate::parse::current(),
                Some(crate::lang::Lang::C | crate::lang::Lang::Cpp)
            )
        {
            // (C / C++ `sep + 1` may be a pointer moved along, no number)
            Arg::Unknown
        } else if has(Arg::Num) || has(Arg::Int) {
            Arg::Num
        } else {
            Arg::Unknown
        }
    } else if matches!(k, "cast_expression" | "as_expression") {
        // `(Foo) x`, `x as Foo`: the type it is cast to
        v.child_by_field_name("type")
            .or_else(|| kids(v).into_iter().last())
            .and_then(|t| first_type(t, src))
            .map_or(Arg::Unknown, Arg::Path)
    } else if callee(v, src).is_some_and(|(_, p)| {
        p.last().is_some_and(|m| matches!(m.as_str(), "toString" | "ToString" | "to_string"))
    }) {
        Arg::Str
    } else {
        value_type(v, src)
            .or_else(|| path_of(v, src))
            .filter(|p| p.first().is_some_and(|h| h != UNKNOWN))
            .map_or(Arg::Unknown, Arg::Path)
    }
}

/// The arguments of a call: each one's label, where it is passed by name
/// (Kotlin `name = x`, Swift `with: x`, C# `name: x`, Python `name=x`), and
/// what it is.
fn arg_facts(n: Node, src: &[u8]) -> Vec<crate::facts::ArgFact> {
    use crate::facts::{Arg, ArgFact};
    let Some((list, lambdas)) = argument_list(n) else { return Vec::new() };
    let mut out = Vec::new();
    for a in list.into_iter().flat_map(kids).filter(|c| !is_comment(c)) {
        let ks = kids(a);
        let wrapped = a.kind().contains("argument");
        let label = a
            .child_by_field_name("name")
            .filter(|_| wrapped)
            .or_else(|| {
                // Kotlin `name = x`; C# `name: x` (`name_colon`)
                let mut cursor = a.walk();
                let named = a.children(&mut cursor).any(|c| !c.is_named() && c.kind() == "=");
                (wrapped && named && ks.len() > 1).then(|| ks[0])
            })
            .or_else(|| ks.iter().copied().find(|c| c.kind() == "name_colon"))
            .map(|l| text(l, src).trim_end_matches(':').trim().to_string());
        let value = if wrapped {
            a.child_by_field_name("value").or_else(|| ks.last().copied())
        } else {
            Some(a)
        };
        out.push(ArgFact { label, kind: value.map_or(Arg::Unknown, |v| arg_kind(v, src)) });
    }
    out.extend((0..lambdas).map(|_| ArgFact { label: None, kind: Arg::Func }));
    if out.len() > 8 {
        return Vec::new();
    }
    out
}

/// C, C++: the parameter list of a function written down its declarator
/// chain (`void *create(int n)` is a `pointer_declarator` around the
/// `function_declarator`, `Foo& get(int i)` a `reference_declarator`).
fn declarator_params(n: Node) -> Option<Node> {
    let mut d = n.child_by_field_name("declarator")?;
    for _ in 0..6 {
        if let Some(ps) = params_field(d) {
            return Some(ps);
        }
        d = d.child_by_field_name("declarator").or_else(|| {
            // (`reference_declarator` names no field for what it wraps)
            kids(d).into_iter().find(|c| c.kind().ends_with("declarator"))
        })?;
    }
    None
}

/// The parameters a callable declares (`self` / `cls` apart).
fn param_list(n: Node, src: &[u8]) -> Vec<crate::facts::ParamFact> {
    let list = params_field(n).or_else(|| declarator_params(n)).or_else(|| {
        kids(n).into_iter().find(|c| {
            matches!(c.kind(), "function_value_parameters" | "parameter_list" | "formal_parameters")
        })
    });
    // Swift writes the parameters as the declaration's own children
    match list {
        Some(l) => params_in(l, false, src),
        None => params_in(n, true, src),
    }
}

/// The parameters written in `holder`; `only_parameters` when it holds other
/// things beside them.
fn params_in(holder: Node, only_parameters: bool, src: &[u8]) -> Vec<crate::facts::ParamFact> {
    use crate::facts::ParamFact;
    let mut out: Vec<ParamFact> = Vec::new();
    let (mut after_eq, mut vararg) = (false, false);
    let mut cursor = holder.walk();
    for c in holder.children(&mut cursor) {
        if !c.is_named() {
            // Kotlin and Swift write a default beside the parameter
            after_eq = c.kind() == "=";
            continue;
        }
        if is_comment(&c) {
            continue;
        }
        if after_eq {
            after_eq = false;
            if let Some(last) = out.last_mut() {
                last.optional = true;
            }
            continue;
        }
        if c.kind() == "parameter_modifiers" {
            vararg = text(c, src).contains("vararg");
            continue;
        }
        // `internal constructor(…)`: the constructor's own modifiers
        if c.kind() == "modifiers" {
            continue;
        }
        if only_parameters && c.kind() != "parameter" {
            continue;
        }
        let t = text(c, src);
        if c.kind() == "receiver_parameter"
            || (matches!(c.kind(), "identifier" | "self_parameter")
                && matches!(t.as_str(), "self" | "cls" | "&self" | "&mut self" | "mut self"))
        {
            continue;
        }
        let typed = locals_of(c, src, true).into_iter().next();
        let name = c
            .child_by_field_name("external_name")
            .map(|e| text(e, src))
            .or_else(|| typed.as_ref().map(|(name, ..)| name.clone()))
            .or_else(|| bound_name(c, src))
            .unwrap_or_default();
        let k = c.kind();
        let mut tokens = c.walk();
        let (mut eq, mut dots) = (false, false);
        for x in c.children(&mut tokens) {
            eq |= !x.is_named() && x.kind() == "=";
            dots |= x.kind() == "..." || x.kind() == "variadic_parameter";
        }
        // a function type names no type of its own: `Function` stands for it
        let mut ty = typed.map(|(_, ty, ..)| ty).unwrap_or_default();
        if ty.is_empty() {
            let mut stack = kids(c);
            let mut hops = 0;
            while let Some(x) = stack.pop() {
                hops += 1;
                if x.kind().contains("function_type") || x.kind() == "lambda_type" {
                    ty = vec!["Function".into()];
                    break;
                }
                if hops < 12 && (x.kind().contains("type") || x.kind().contains("annotation")) {
                    stack.extend(kids(x));
                }
            }
        }
        out.push(ParamFact {
            name,
            ty,
            elem: written_elem(c, src),
            keyed: written_keyed(c, src),
            key: written_key(c, src),
            optional: eq
                || k.contains("default")
                || k == "optional_parameter"
                || ["value", "default_value", "default"]
                    .iter()
                    .any(|f| c.child_by_field_name(f).is_some()),
            variadic: std::mem::take(&mut vararg)
                || dots
                || ["splat", "variadic", "spread", "rest"].iter().any(|w| k.contains(w))
                || t.starts_with("params "),
            written: c
                .child_by_field_name("type")
                .map(|ty| text(ty, src).split_whitespace().collect())
                .unwrap_or_default(),
        });
    }
    out
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
/// A closure's byte range and the names it binds (name, type path, declared).
type ClosureScope = (usize, usize, Vec<(String, Vec<String>, bool)>);

/// Give each closure parameter a name of its own (`r@1042`) in the uses
/// inside the closure, and a local of that name: `r` in `func(w
/// http.ResponseWriter, r *http.Request) { … }` is the request there and
/// whatever `r` was outside, and an untyped block parameter is no outer name.
fn bind_closure_params(closures: &[ClosureScope], refs: &mut [RefFact], locals: &mut Vec<Local>) {
    if closures.is_empty() {
        return;
    }
    // innermost first: the smallest range that holds a use binds its name
    let mut order: Vec<usize> = (0..closures.len()).collect();
    order.sort_by_key(|&i| closures[i].1 - closures[i].0);
    let mut used = vec![false; closures.len()];
    for r in refs.iter_mut() {
        let Some(head) = r.path.first() else { continue };
        let bare = head.trim_start_matches('$');
        let found = order.iter().copied().find(|&i| {
            let (start, end, params) = &closures[i];
            *start <= r.byte && r.byte < *end && params.iter().any(|(n, _, _)| n == bare)
        });
        if let Some(i) = found {
            let sigil = if head.starts_with('$') { "$" } else { "" };
            r.path[0] = format!("{sigil}{bare}@{}", closures[i].0);
            used[i] = true;
        }
    }
    rename_closure_heads(closures, &order, locals, &mut used);
    for (i, (start, _, params)) in closures.iter().enumerate() {
        if !used[i] {
            continue;
        }
        for (name, path, declared) in params {
            locals.push(Local {
                name: format!("{name}@{start}"),
                path: path.clone(),
                declared: *declared,
                field: false,
                assigned: false,
                given: None,
                elem: None,
                value_args: Vec::new(),
                sealed: false,
                keyed: false,
                targs: Vec::new(),
                targ_elems: Vec::new(),
                key: None,
                written_head: None,
                from_def: None,
                byte: *start,
                until: 0,
                after: 0,
            });
        }
    }
    // and the parameters of a closure inside another, typed from its
    // parameters (`chunk -> chunk.forEach { it.d() }`)
    rename_closure_heads(closures, &order, locals, &mut used);
}

/// Renames the head of a local's path to the closure parameter it names
/// (`name@byte`, as `bind_closure_params` does for uses): a local assigned
/// inside a closure from one of its parameters, or a parameter of a closure
/// inside it typed from one.
fn rename_closure_heads(
    closures: &[ClosureScope],
    order: &[usize],
    locals: &mut [Local],
    used: &mut [bool],
) {
    for l in locals.iter_mut() {
        let Some(head) = l.path.first() else { continue };
        let bare = head.trim_start_matches('$').to_string();
        let found = order.iter().copied().find(|&i| {
            let (start, end, params) = &closures[i];
            *start <= l.byte && l.byte < *end && params.iter().any(|(n, _, _)| *n == bare)
        });
        if let Some(i) = found {
            let sigil = if head.starts_with('$') { "$" } else { "" };
            l.path[0] = format!("{sigil}{bare}@{}", closures[i].0);
            used[i] = true;
        }
    }
}

/// A closure's parameter: its name, the path of its type, whether declared.
type Param = (String, Vec<String>, bool);

/// The names a closure binds: `|headers|` (Ruby, Rust), `x -> …` (Java),
/// `{ property -> … }` (Kotlin), `item => …` (JS, C#), `lambda x: …`,
/// `rescue => e`. Typed where the source writes a type, else untyped: either
/// way the name is not an outer one.
fn closure_params(n: Node, src: &[u8]) -> Vec<Param> {
    let mut stack: Vec<(Node, usize)> = match n.kind() {
        "block_parameters"
        | "lambda_parameters"
        | "closure_parameters"
        | "inferred_parameters"
        | "exception_variable" => vec![(n, 0)],
        "arrow_function"
        | "function_expression"
        | "lambda_expression"
        | "anonymous_function"
        | "anonymous_function_creation_expression"
        | "func_literal"
        | "lambda" => ["parameter", "parameters"]
            .iter()
            .filter_map(|f| n.child_by_field_name(f))
            .map(|p| (p, 0))
            .collect(),
        // Swift `{ p in … }`, `{ (p: Plane) -> Pilot in … }`
        "lambda_literal" => n
            .child_by_field_name("type")
            .into_iter()
            .flat_map(kids)
            .filter(|c| c.kind() == "lambda_function_type_parameters")
            .flat_map(kids)
            .map(|p| (p, 0))
            .collect(),
        _ => return Vec::new(),
    };
    // in the order written: which parameter is the first matters (an
    // element, an index); `_` keeps its place
    let mut out: Vec<(usize, Param)> = Vec::new();
    while let Some((p, depth)) = stack.pop() {
        let k = p.kind();
        // (C# `d => …`: the parameter is the node itself)
        if matches!(k, "identifier" | "simple_identifier" | "variable_name" | "implicit_parameter")
        {
            let name =
                if k == "implicit_parameter" { Some(text(p, src)) } else { bound_name(p, src) };
            if let Some(name) = name {
                if !matches!(name.as_str(), "self" | "this") {
                    out.push((p.start_byte(), (name, Vec::new(), false)));
                }
            }
            continue;
        }
        if k.contains("type") || depth > 5 {
            continue;
        }
        // JS / TS `([key, value], index) => …`, `({ a, b: c }) => …`: every
        // name the pattern binds is the closure's
        if k == "shorthand_property_identifier_pattern" {
            out.push((p.start_byte(), (text(p, src), Vec::new(), false)));
            continue;
        }
        if k == "pair_pattern" {
            stack.extend(p.child_by_field_name("value").map(|v| (v, depth + 1)));
            continue;
        }
        if matches!(k, "array_pattern" | "object_pattern" | "rest_pattern" | "assignment_pattern") {
            let inner = if k == "assignment_pattern" {
                p.child_by_field_name("left").into_iter().collect()
            } else {
                kids(p)
            };
            stack.extend(inner.into_iter().map(|c| (c, depth + 1)));
            continue;
        }
        if let Some(pattern) = p
            .child_by_field_name("pattern")
            .filter(|x| matches!(x.kind(), "array_pattern" | "object_pattern"))
        {
            stack.push((pattern, depth + 1));
            continue;
        }
        let typed = locals_of(p, src, true);
        if !typed.is_empty() {
            out.extend(
                typed
                    .into_iter()
                    .map(|(name, path, declared, _)| (p.start_byte(), (name, path, declared))),
            );
            continue;
        }
        // a parameter with a default or a pattern: its name, not its value
        match p.child_by_field_name("name").or_else(|| p.child_by_field_name("pattern")) {
            Some(name) => stack.push((name, depth + 1)),
            None => stack.extend(kids(p).into_iter().map(|c| (c, depth + 1))),
        }
    }
    out.sort_by_key(|(byte, _)| *byte);
    out.into_iter().map(|(_, param)| param).collect()
}

/// The parameter of a parameter list (or the parameter itself) that declares
/// `name`.
fn param_node<'t>(ps: Node<'t>, name: &str, src: &[u8]) -> Option<Node<'t>> {
    let mut stack = vec![(ps, 0)];
    while let Some((n, depth)) = stack.pop() {
        let k = n.kind();
        if k.contains("parameter") && !k.ends_with("parameters") && !k.ends_with("_list") {
            if locals_of(n, src, true).iter().any(|(x, ..)| x == name) {
                return Some(n);
            }
            continue;
        }
        if depth < 3 {
            stack.extend(kids(n).into_iter().map(|c| (c, depth + 1)));
        }
    }
    None
}

/// How many type arguments a call writes for what it calls: C#
/// `client.Get<User>(id)` (the called name is a `generic_name`), Java
/// `this.<User>get(id)`.
fn call_targs(n: Node) -> u8 {
    let called = n.child_by_field_name("function").map(|f| {
        if f.kind() == "member_access_expression" {
            f.child_by_field_name("name").unwrap_or(f)
        } else {
            f
        }
    });
    let list = called
        .filter(|x| x.kind() == "generic_name")
        .and_then(|x| kids(x).into_iter().find(|c| c.kind() == "type_argument_list"))
        .or_else(|| n.child_by_field_name("type_arguments"));
    list.map(|l| kids(l).len().min(255) as u8).unwrap_or(0)
}

/// A name a statement binds: name, path, declared, the range it holds in.
type Bound = (String, Vec<String>, bool, Option<(usize, usize)>);

/// Names a statement binds whose type the source states at the binding:
/// `(name, path, declared, range)`. A loop variable written with its type (Java
/// `for (Pet pet : pets)`, C# `foreach (Pet pet in pets)`), a type pattern
/// (Java `o instanceof Pet p`, C# `o is Pet p`, `case Pet p:`), and a name
/// bound to what a wrapper holds: Rust `if let Some(p) = e`, `while let`,
/// `let Some(p) = e else { … }`, a `match` arm's `Some(p)` / `Ok(p)` or the
/// fields of an enum variant, Swift `if let p = e` / `guard let p = e`. A
/// pattern that writes no name (`x is Foo`) binds none. The range is where
/// the name holds when that is narrower than the block around: a `match`
/// arm's bindings are the arm's (r[names.scopes.pattern-bindings.match-arm]).
fn bound_by_pattern(n: Node, src: &[u8]) -> Vec<Bound> {
    let mut out: Vec<Bound> = Vec::new();
    let typed = |ty: Option<Node>, name: Option<Node>, out: &mut Vec<_>| {
        let ty = ty.and_then(|t| first_type(t, src));
        let name = name.filter(|x| x.kind() == "identifier").map(|x| text(x, src));
        if let (Some(ty), Some(name)) = (ty, name) {
            out.push((name, ty, true, None));
        }
    };
    match n.kind() {
        // Scala `case r: Repo =>`, Java `try (Repo r = open())`
        "typed_pattern" => {
            typed(n.child_by_field_name("type"), n.child_by_field_name("pattern"), &mut out)
        }
        "resource" => typed(n.child_by_field_name("type"), n.child_by_field_name("name"), &mut out),
        "enhanced_for_statement" | "foreach_statement" => {
            let name = n.child_by_field_name("name").or_else(|| n.child_by_field_name("left"));
            typed(n.child_by_field_name("type"), name, &mut out);
        }
        "instanceof_expression" => {
            typed(n.child_by_field_name("right"), n.child_by_field_name("name"), &mut out)
        }
        "declaration_pattern" => {
            typed(n.child_by_field_name("type"), n.child_by_field_name("name"), &mut out)
        }
        "let_condition" | "let_declaration" => {
            let pattern = n.child_by_field_name("pattern");
            let value = n.child_by_field_name("value").and_then(|v| held_path(v, src));
            if let Some(p) = pattern {
                out.extend(
                    held_by_pattern(p, value.as_deref(), src)
                        .into_iter()
                        .map(|(a, b, c)| (a, b, c, None)),
                );
            }
        }
        "match_expression" => {
            let value = n.child_by_field_name("value").and_then(|v| held_path(v, src));
            let arms = n.child_by_field_name("body").map(kids).unwrap_or_default();
            for arm in arms.into_iter().filter(|a| a.kind() == "match_arm") {
                let pattern =
                    arm.child_by_field_name("pattern").and_then(|p| kids(p).into_iter().next());
                if let Some(p) = pattern {
                    let arm_range = Some((arm.start_byte(), arm.end_byte()));
                    out.extend(
                        held_by_pattern(p, value.as_deref(), src)
                            .into_iter()
                            .map(|(a, b, c)| (a, b, c, arm_range)),
                    );
                }
            }
        }
        // Swift: `bound_identifier`, `=`, then the value, once per binding
        "if_statement" | "guard_statement" => {
            let mut cursor = n.walk();
            let all: Vec<(Option<&str>, Node)> = n
                .children(&mut cursor)
                .enumerate()
                .map(|(i, c)| (n.field_name_for_child(i as u32), c))
                .collect();
            for (i, (field, c)) in all.iter().enumerate() {
                if *field != Some("bound_identifier") {
                    continue;
                }
                let name = text(*c, src);
                let eq = all.get(i + 1).is_some_and(|(_, x)| x.kind() == "=");
                let value = all.get(i + 2).filter(|_| eq).and_then(|(_, v)| held_path(*v, src));
                // `if let pet {`, `if let pet = pet`, `guard let self`: the
                // name goes on meaning what it meant
                let Some(mut path) = value else { continue };
                if path[0] == UNKNOWN || path == [name.clone()] || name == "self" {
                    continue;
                }
                path.push(OPENED.into());
                out.push((name, path, false, None));
            }
        }
        _ => {}
    }
    out
}

/// The path of a value a pattern takes apart: what it constructs or calls
/// (`load()`), else the name or field it is (`pet`, `&self.pet`).
fn held_path(v: Node, src: &[u8]) -> Option<Vec<String>> {
    value_type(v, src).or_else(|| {
        let mut v = v;
        while matches!(v.kind(), "reference_expression" | "parenthesized_expression") {
            v = kids(v).into_iter().last()?;
        }
        path_of(v, src)
    })
}

/// What a Rust pattern binds out of a value: `Some(p)` / `Ok(p)` is what the
/// value holds once opened, `Slot::Full(a, b)` the fields of that variant.
/// `Err(e)` and a pattern that names nothing bind nothing the source types.
fn held_by_pattern(
    p: Node,
    value: Option<&[String]>,
    src: &[u8],
) -> Vec<(String, Vec<String>, bool)> {
    let mut p = p;
    while matches!(p.kind(), "reference_pattern" | "ref_pattern" | "mut_pattern") {
        match kids(p).into_iter().last() {
            Some(inner) => p = inner,
            None => return Vec::new(),
        }
    }
    if p.kind() != "tuple_struct_pattern" {
        return Vec::new();
    }
    let Some(ty) = p.child_by_field_name("type") else { return Vec::new() };
    let binds: Vec<Node> = kids(p).into_iter().filter(|c| c.id() != ty.id()).collect();
    let name_of = |c: Node| {
        let mut c = c;
        while matches!(c.kind(), "reference_pattern" | "ref_pattern" | "mut_pattern") {
            c = kids(c).into_iter().last()?;
        }
        (c.kind() == "identifier").then(|| text(c, src)).filter(|n| n != "_")
    };
    let variant = text(ty, src);
    // a name the pattern binds whose type the walk cannot tell (`Err(e)`,
    // `Some(x)` of something unnamed) is still a local: it hides the outer
    // names, and a distinctive method name may say what it is
    let untyped = |binds: &[Node]| -> Vec<(String, Vec<String>, bool)> {
        binds.iter().filter_map(|c| name_of(*c)).map(|n| (n, Vec::new(), false)).collect()
    };
    match variant.as_str() {
        "Some" | "Ok" => {
            let (Some(value), [one]) = (value, binds.as_slice()) else { return untyped(&binds) };
            if value.first().is_some_and(|h| h == UNKNOWN) {
                return untyped(&binds);
            }
            let mut path = value.to_vec();
            path.push(OPENED.into());
            // (`if let Some(x) = x`: the name goes on meaning what it meant)
            name_of(*one)
                .filter(|n| value != [n.clone()])
                .map(|n| vec![(n, path, false)])
                .unwrap_or_default()
        }
        "Err" | "None" => untyped(&binds),
        _ => {
            let Some(of) = path_of(ty, src) else { return Vec::new() };
            binds
                .iter()
                .enumerate()
                .filter_map(|(i, c)| {
                    let mut path = of.clone();
                    path.push(tuple_part(i));
                    Some((name_of(*c)?, path, false))
                })
                .collect()
        }
    }
}

fn locals_of(n: Node, src: &[u8], in_params: bool) -> Vec<(String, Vec<String>, bool, bool)> {
    let k = n.kind();
    let mut out = Vec::new();
    // parameters: `x: Foo`, `Foo x`, `x Foo`, `$x: Foo`
    if in_params && k.contains("parameter") && !k.ends_with("parameters") && !k.ends_with("_list") {
        let ty = n.child_by_field_name("type").and_then(|t| first_type(t, src)).or_else(|| {
            kids(n)
                .into_iter()
                .find(|c| {
                    matches!(
                        c.kind(),
                        "user_type" | "nullable_type" | "type_annotation" | "named_type" | "type"
                    )
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
            // (C# `new Article { Author = author }`: a member of the object being
            // made is set, no name is declared)
            if n.parent().is_some_and(|p| {
                matches!(
                    p.kind(),
                    "initializer_expression"
                        | "anonymous_object_creation_expression"
                        | "with_initializer"
                        | "object_creation_expression"
                )
            }) {
                return out;
            }
            let (Some(l), Some(r)) =
                (n.child_by_field_name("left"), n.child_by_field_name("right"))
            else {
                return out;
            };
            let Some(ty) = value_type(r, src) else {
                // assigned something the walk cannot type: still a local that
                // shadows outer names
                if matches!(l.kind(), "identifier" | "variable_name") {
                    if let Some(name) = bound_name(l, src) {
                        out.push((name, Vec::new(), false, false));
                    }
                }
                return out;
            };
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
        // (Scala `val r = new Repo` in a method: one of a class or an object
        // is a definition, `Scala::state_defs`)
        "val_definition" | "var_definition"
            if n.parent()
                .is_some_and(|p| matches!(p.kind(), "compilation_unit" | "template_body")) => {}
        "variable_declarator"
        | "let_declaration"
        | "init_declarator"
        | "val_definition"
        | "var_definition" => {
            // `const { show, hide: close } = useToast()`: each name is that
            // member of what the value is
            let pattern = n.child_by_field_name("name").filter(|x| x.kind() == "object_pattern");
            if let Some(pattern) = pattern {
                let value = n.child_by_field_name("value");
                let Some(mut of) = value.and_then(|v| value_type(v, src)) else { return out };
                if value.is_some_and(|v| v.kind() == "call_expression") {
                    of.push(UNKNOWN.into());
                }
                for c in kids(pattern) {
                    let (key, name) = match c.kind() {
                        "shorthand_property_identifier_pattern" => (text(c, src), text(c, src)),
                        "pair_pattern" => {
                            let k = c.child_by_field_name("key").map(|x| text(x, src));
                            let v = c
                                .child_by_field_name("value")
                                .filter(|x| x.kind() == "identifier")
                                .map(|x| text(x, src));
                            match (k, v) {
                                (Some(k), Some(v)) => (k, v),
                                _ => continue,
                            }
                        }
                        _ => continue,
                    };
                    let mut path = of.clone();
                    path.push(key);
                    out.push((name, path, false, false));
                }
                return out;
            }
            let name = n
                .child_by_field_name("name")
                .or_else(|| n.child_by_field_name("pattern"))
                .or_else(|| n.child_by_field_name("declarator"))
                .and_then(|x| bound_name(x, src));
            let declared = n.child_by_field_name("type").and_then(|t| first_type(t, src));
            let value = n.child_by_field_name("value");
            let valued = value.and_then(|v| value_type(v, src));
            // (a cast writes the type as surely as a declaration does)
            let cast = value.is_some_and(is_cast);
            if let Some(name) = name {
                match (declared, valued) {
                    (Some(t), _) => out.push((name, t, true, false)),
                    (None, Some(t)) => out.push((name, t, cast, false)),
                    // (a local all the same: `let hostname = 3;` hides a
                    // function `hostname` where the value is passed on)
                    // (not one a module is bound to: `const m = await
                    // import('./m.js')`, `const x = require('m')`)
                    _ => {
                        let bound = value.is_some_and(|v| {
                            let t = text(v, src);
                            t.contains("import(") || t.contains("require(")
                        });
                        if !bound {
                            out.push((name, Vec::new(), false, false));
                        }
                    }
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
            // a field only when declared in a type's body (Swift / Kotlin use
            // property_declaration for locals too)
            let field = matches!(k, "field_declaration" | "public_field_definition")
                || (k == "property_declaration"
                    && n.parent().is_some_and(|p| {
                        matches!(
                            p.kind(),
                            "class_body"
                                | "declaration_list"
                                | "protocol_body"
                                | "enum_class_body"
                                | "field_declaration_list"
                        )
                    }));
            let declared =
                n.child_by_field_name("type").and_then(|t| first_type(t, src)).or_else(|| {
                    kids(n)
                        .into_iter()
                        .find(|c| matches!(c.kind(), "user_type" | "type_annotation"))
                        .and_then(|t| first_type(t, src))
                });
            // (C, C++ `Foo x;`: the declarator is the bare name)
            let c_like = k == "declaration"
                && matches!(
                    crate::parse::current(),
                    Some(crate::lang::Lang::C | crate::lang::Lang::Cpp)
                );
            let mut declarators: Vec<Node> = kids(n)
                .into_iter()
                .filter(|c| {
                    c.kind().ends_with("declarator")
                        || c.kind() == "variable_declaration"
                        || (c_like && c.kind() == "identifier")
                })
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
                    // (`var (a, b) = F()`: taken apart where it is visited)
                    if destructured(x, src).is_some() {
                        continue;
                    }
                    let name = if x.id() == n.id() {
                        n.child_by_field_name("name").and_then(|nm| bound_name(nm, src))
                    } else {
                        bound_name(x, src)
                    };
                    let Some(name) = name else { continue };
                    // Kotlin `val c = Plain()`: the value sits beside the
                    // `variable_declaration`, in the property declaration
                    let beside = || {
                        (d.kind() == "variable_declaration" && k == "property_declaration")
                            .then(|| {
                                kids(n)
                                    .into_iter()
                                    .filter(|c| c.id() != d.id())
                                    .find(|c| callee(*c, src).is_some() || is_cast(*c))
                            })
                            .flatten()
                    };
                    let value = x
                        .child_by_field_name("value")
                        .or_else(|| {
                            // (C# `var x = await f()`: no field names the value)
                            kids(x).into_iter().find(|c| {
                                callee(*c, src).is_some()
                                    || is_cast(*c)
                                    || matches!(
                                        c.kind(),
                                        "await_expression"
                                            | "try_expression"
                                            // (`var sut = Parser.Default`: what
                                            // the member holds)
                                            | "member_access_expression"
                                    )
                            })
                        })
                        .or_else(beside);
                    let cast = value.is_some_and(is_cast);
                    let valued = value.and_then(|v| value_type(v, src));
                    match (&d_type, valued) {
                        (Some(t), _) => out.push((name, t.clone(), true, field)),
                        (None, Some(t)) => out.push((name, t, cast, field)),
                        // `val n = 5` in a function: a local all the same,
                        // which hides the outer name
                        _ if k == "property_declaration" && !field => {
                            out.push((name, Vec::new(), false, false));
                        }
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
        // Go `var buf bytes.Buffer`, `var a, b = f(), g()`: every name
        "var_spec" => {
            let ty = n.child_by_field_name("type").and_then(|t| first_type(t, src));
            let values = n.child_by_field_name("value").map(kids).unwrap_or_default();
            let mut cursor = n.walk();
            let names: Vec<Node> = n.children_by_field_name("name", &mut cursor).collect();
            for (i, name) in names.into_iter().enumerate() {
                if text(name, src) == "_" {
                    continue;
                }
                match &ty {
                    Some(t) => out.push((text(name, src), t.clone(), true, false)),
                    None => {
                        let t = values.get(i).and_then(|v| value_type(*v, src)).unwrap_or_default();
                        out.push((text(name, src), t, false, false));
                    }
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
            // (every name it declares is a local, typed or not: `endpoint :=
            // "/secure"` hides a package's `endpoint`)
            let values = kids(r);
            for (i, name) in kids(l).into_iter().enumerate() {
                if name.kind() != "identifier" || text(name, src) == "_" {
                    continue;
                }
                let t = values.get(i).and_then(|v| value_type(*v, src)).unwrap_or_default();
                out.push((text(name, src), t, false, false));
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
        public: false,
        byte: n.start_byte(),
        scope: None,
        within: Vec::new(),
    }
}

/// Read the references, imports and package of `facts`' file; `facts.defs`
/// must already be filled.
pub fn read(m: &dyn LanguageModule, root: Node, src: &[u8], facts: &mut FileFacts) {
    read_at(m, root, src, facts, 0);
}

/// `read`, `depth` macros deep (a Rust macro's arguments are read again as
/// expressions: `macro_refs`).
fn read_at(m: &dyn LanguageModule, root: Node, src: &[u8], facts: &mut FileFacts, depth: usize) {
    let rust = facts.lang == Some(crate::lang::Lang::Rust);
    // Rust macro invocations, for the calls in their arguments
    let mut macros: Vec<Node> = Vec::new();
    let macro_depth = depth;
    let by_node: HashMap<usize, usize> =
        facts.defs.iter().enumerate().map(|(i, d)| (d.node_id, i)).collect();
    let mut refs: Vec<RefFact> = Vec::new();
    let mut imports: Vec<ImportFact> = Vec::new();
    let mut locals: Vec<Local> = Vec::new();
    // byte ranges of declarations the grammar misread (`misread_variable`)
    let mut misread: Vec<(usize, usize)> = Vec::new();
    // JS: what a `this` at each byte belongs to (`RefFact::this_of`)
    let mut this_at: HashMap<usize, Vec<String>> = HashMap::new();
    let csharp = facts.lang == Some(crate::lang::Lang::CSharp);
    let push = |refs: &mut Vec<RefFact>, n: Node, kind, path: Vec<String>, context, def| {
        if path.is_empty() || path.last().is_some_and(|s| s == UNKNOWN || s.is_empty()) {
            return;
        }
        let arity = (csharp && matches!(kind, RefKind::Type | RefKind::New))
            .then(|| path.last().and_then(|name| written_arity(n, name, src)))
            .flatten();
        refs.push(RefFact {
            line: line(n),
            kind,
            path,
            context,
            from_def: def,
            from_owner: None,
            args: None,
            self_arg: false,
            hints: Vec::new(),
            implicit: None,
            targs: 0,
            arity,
            this_of: None,
            recv: None,
            byte: n.start_byte(),
        });
    };
    // `(span node, name node, kind, from state_defs)`
    let mut extras: Vec<(Node, Node, crate::facts::DefKind, bool)> = Vec::new();
    let mut state = crate::state::State::new(m.assignment_declares());
    let mut resources: Vec<crate::resources::ResourceUse> = Vec::new();
    // `(node, depth)`; `anc` holds the ancestors of the node being visited
    let mut stack = vec![(root, 0usize)];
    let mut anc: Vec<Node> = Vec::new();
    // closures that are no definitions: (start, end, the names they bind)
    let mut closures: Vec<ClosureScope> = Vec::new();
    // a closure called where it is written as a declaration's value: its
    // first byte -> (the declared name, the declaration's first byte)
    let mut invoked: HashMap<usize, (String, usize)> = HashMap::new();
    // where a declaration whose value is a function starts
    let mut function_decls: std::collections::HashSet<usize> = std::collections::HashSet::new();
    let mut outer_names: Vec<(usize, String)> = Vec::new();
    // every closure: (start, end, first line, last line, node id)
    let mut blocks: Vec<(usize, usize, u32, u32, usize)> = Vec::new();
    // lambdas with a receiver: (start, end, where the receiver comes from)
    let mut receivers: Vec<(usize, usize, crate::facts::ReceiverOf)> = Vec::new();
    let mut steps = 0usize;
    // block scopes: a local declared while a node was read lives until the
    // block around that node ends
    let scoped = !matches!(
        facts.lang,
        Some(crate::lang::Lang::Python | crate::lang::Lang::Ruby | crate::lang::Lang::Php) | None
    );
    let ecma = matches!(
        facts.lang,
        Some(
            crate::lang::Lang::JavaScript | crate::lang::Lang::TypeScript | crate::lang::Lang::Tsx
        )
    );
    let mut stamped = 0usize;
    let mut until = 0usize;
    let mut declared: Option<(usize, usize)> = None;
    while let Some((n, depth)) = stack.pop() {
        for l in locals[stamped..].iter_mut().filter(|l| !l.field && l.until == 0) {
            l.until = until;
            if let Some((_, end)) = declared.filter(|(value, _)| l.byte < *value && scoped) {
                l.after = end;
            }
        }
        stamped = locals.len();
        anc.truncate(depth);
        until = 0;
        // (the declaration the node is, or is the name of)
        // (never a definition's own node: a parameter's default value is
        // no value its function is declared with)
        declared = std::iter::once(&n)
            .chain(anc.iter().rev().take(2))
            .filter(|a| !by_node.contains_key(&a.id()))
            .find_map(|a| value_node(*a).map(|v| (v.start_byte(), a.end_byte())));
        // (JS `var` is the function's, whatever block it is written in)
        let hoisted = ecma
            && (n.kind() == "variable_declaration"
                || anc.last().is_some_and(|a| a.kind() == "variable_declaration"));
        if scoped && !hoisted {
            // (what a definition's own node declares, its parameters, is its)
            let around = std::iter::once(&n).chain(anc.iter().rev());
            until = around
                .into_iter()
                .find(|a| is_scope(a.kind()) || by_node.contains_key(&a.id()))
                .map_or(0, |a| a.end_byte());
        }
        steps += 1;
        if steps > 2_000_000 {
            break;
        }
        if rust && n.kind() == "macro_invocation" {
            macros.push(n);
            // (outside any definition, its arguments are a scope: `rgtest!(name,
            // |dir: Dir, mut cmd: TestCommand| { … })`)
            if let Some(tt) = kids(n).into_iter().find(|c| c.kind() == "token_tree") {
                blocks.push((
                    tt.start_byte(),
                    tt.end_byte(),
                    tt.start_position().row as u32 + 1,
                    tt.end_position().row as u32 + 1,
                    tt.id(),
                ));
            }
        }
        // a declaration whose value is a function: `const f: T = () => …`
        if n.child_by_field_name("value").is_some_and(|v| is_closure(v.kind())) {
            function_decls.insert(n.start_byte());
        }
        // `var Dataset = (function () { function Dataset() {} … return
        // Dataset })()`: the function called where it is written, as the
        // value of a declaration; the declared name is what it returns
        if is_closure(n.kind()) {
            let mut up = anc.iter().rev().filter(|a| a.kind() != "parenthesized_expression");
            let call = up.next().filter(|c| {
                c.kind() == "call_expression"
                    && c.child_by_field_name("function").is_some_and(|f| {
                        f.start_byte() <= n.start_byte() && n.end_byte() <= f.end_byte()
                    })
            });
            let decl = call.and(up.next()).filter(|d| d.kind() == "variable_declarator");
            if let Some(d) = decl {
                if let Some(name) =
                    d.child_by_field_name("name").filter(|x| x.kind() == "identifier")
                {
                    invoked.insert(n.start_byte(), (text(name, src), d.start_byte()));
                }
            }
        }
        // (Ruby's `{ … }` block is a `block` under its call)
        if is_closure(n.kind())
            || (n.kind() == "block" && anc.last().is_some_and(|a| a.kind() == "call"))
        {
            blocks.push((
                n.start_byte(),
                n.end_byte(),
                n.start_position().row as u32 + 1,
                n.end_position().row as u32 + 1,
                n.id(),
            ));
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
        facts.spec_includes.extend(m.spec_includes(n, src));
        if !by_node.contains_key(&n.id()) {
            if let Some((name, kind)) = m.extra_def(n, src) {
                extras.push((n, name, kind, false));
            }
            extras.extend(m.state_defs(n, src).into_iter().map(|(a, b, k)| (a, b, k, true)));
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
            if let Some(o) = m.object_of(n, src) {
                facts.defs[di].object = Some(o);
            }
            if facts.defs[di].kind.is_callable() {
                facts.defs[di].receiver = m.receiver_of(n, src);
            }
            if matches!(n.kind(), "object_creation_expression" | "object_literal") {
                facts.defs[di].anonymous = true;
            }
            if facts.defs[di].kind.is_class_like() {
                let name = facts.defs[di].name.clone();
                facts.defs[di].self_params = self_params(n, src, &name);
                // `class Response<T>`: what a declaration's type arguments bind
                facts.defs[di].type_params = type_params(n, src);
                facts.defs[di].type_bounds = type_params_bounds(n, src).1;
                // `class UserRepo : Repo<User>`: what it binds its bases' to
                facts.defs[di].base_targs = base_targs(n, src);
                // Rust `struct Script(Arc<Mutex<State>>)`: its numbered fields
                if let Some(list) =
                    kids(n).into_iter().find(|c| c.kind() == "ordered_field_declaration_list")
                {
                    let types: Vec<Node> = kids(list)
                        .into_iter()
                        .filter(|c| !is_comment(c) && !c.kind().contains("visibility"))
                        .filter(|c| c.kind() != "attribute_item")
                        .collect();
                    facts.defs[di].rets =
                        types.iter().map(|t| first_type(*t, src).unwrap_or_default()).collect();
                    facts.defs[di].part_sealed =
                        types.iter().map(|t| sealed_type(*t, src)).collect();
                }
                // Kotlin `class Name(val a: A, b: B = …)`: the primary
                // constructor's parameters are the class's
                if let Some(pc) = kids(n).into_iter().find(|c| c.kind() == "primary_constructor") {
                    facts.defs[di].plist = params_in(pc, false, src);
                    facts.defs[di].params = u8::try_from(facts.defs[di].plist.len()).ok();
                }
            }
            // `type Either<L, A> = …`: its parameters are names of its own
            if facts.defs[di].kind == crate::facts::DefKind::TypeAlias {
                facts.defs[di].type_params = type_params(n, src);
            }
            if facts.defs[di].kind.is_callable() {
                facts.defs[di].lambda_receiver = lambda_receiver(n, src);
                facts.defs[di].type_params = type_params(n, src);
                facts.defs[di].type_bounds = type_params_bounds(n, src).1;
                facts.defs[di].elem = return_elem(n, src);
                facts.defs[di].keyed = return_keyed(n, src);
                facts.defs[di].key = return_key(n, src);
                facts.defs[di].targs = return_targs(n, src);
                facts.defs[di].targ_elems = return_targ_elems(n, src);
                facts.defs[di].rets = return_tuple(n, src);
                if let Some((inner, sealed)) = return_wraps(n, src) {
                    facts.defs[di].wraps = Some(inner);
                    facts.defs[di].sealed = sealed;
                }
                facts.defs[di].plist = param_list(n, src);
                facts.defs[di].params = u8::try_from(facts.defs[di].plist.len()).ok();
                if let Some(v) = body_value(n) {
                    if let Some(path) = returned_path(v, src) {
                        note_return(&mut facts.defs[di], path);
                    }
                    note_parts(&mut facts.defs[di], v, src);
                    note_members(&mut facts.defs[di], v, src);
                }
            }
            if facts.defs[di].kind.is_class_like() {
                let name = facts.defs[di].name.clone();
                facts.defs[di].table = crate::resources::declared_table(n, src, facts.lang, &name);
            }
            let def: &Def = &facts.defs[di];
            for (kind, path) in m.heritage_of(n, src) {
                push(&mut refs, n, kind, path, "", Some(di));
            }
            let mut decos = Vec::new();
            decorators(n, src, &mut decos);
            for p in decos {
                push(&mut refs, n, RefKind::Decorator, p, "decorator", Some(di));
            }
            // (a scope with parameters is one for them too: C# 14
            // `extension(RestRequest request) { … }`, whose members use `request`)
            if is_callable(def.kind) || def.block {
                let mut types = Vec::new();
                signature_types(n, src, &mut types);
                for (p, ctx) in types {
                    push(&mut refs, n, RefKind::Type, p, ctx, Some(di));
                }
                let mut pstack: Vec<Node> = Vec::new();
                pstack.extend(params_field(n));
                pstack.extend(n.child_by_field_name("receiver"));
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
                if let Some(ps) = declarator_params(n) {
                    pstack.push(ps);
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
                                        assigned: false,
                                        given: None,
                                        elem: None,
                                        value_args: Vec::new(),
                                        sealed: false,
                                        keyed: false,
                                        targs: Vec::new(),
                                        targ_elems: Vec::new(),
                                        key: None,
                                        written_head: None,
                                        from_def: Some(di),
                                        byte: c.start_byte(),
                                        until: 0,
                                        after: 0,
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
                            ) && !(c.kind() == "optional_parameter"
                                && c.child_by_field_name("type").is_some())
                            {
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
                                        assigned: false,
                                        given: None,
                                        elem: None,
                                        value_args: Vec::new(),
                                        sealed: false,
                                        keyed: false,
                                        targs: Vec::new(),
                                        targ_elems: Vec::new(),
                                        key: None,
                                        written_head: None,
                                        from_def: Some(di),
                                        byte: c.start_byte(),
                                        until: 0,
                                        after: 0,
                                    });
                                }
                            } else {
                                pstack.push(c);
                            }
                        }
                    }
                    for (name, path, declared, field) in found {
                        // what its own declaration writes (`Optional<User> u`,
                        // `Map<K, V> m`, `Call<Contact> call`)
                        let own = declared.then(|| param_node(ps, &name, src)).flatten();
                        locals.push(Local {
                            sealed: own.is_some_and(|c| written_sealed(c, src)),
                            keyed: own.is_some_and(|c| written_keyed(c, src)),
                            targs: own.map(|c| written_targs(c, src)).unwrap_or_default(),
                            name,
                            path,
                            declared,
                            field,
                            assigned: false,
                            given: None,
                            elem: None,
                            value_args: Vec::new(),
                            targ_elems: own.map(|c| written_targ_elems(c, src)).unwrap_or_default(),
                            key: own.and_then(|c| written_key(c, src)),
                            written_head: None,
                            from_def: Some(di),
                            byte: ps.start_byte(),
                            until: 0,
                            after: 0,
                        });
                    }
                }
            }
            // a class's constructor parameters (C# primary constructors, Kotlin,
            // Scala `class A(val x: B)`): their types are the class's
            if def.kind.is_class_like() {
                let mut types = Vec::new();
                for c in kids(n) {
                    if matches!(
                        c.kind(),
                        "parameter_list" | "primary_constructor" | "class_parameters"
                    ) {
                        param_types(c, src, &mut types);
                    }
                }
                for (p, ctx) in types {
                    push(&mut refs, n, RefKind::Type, p, ctx, Some(di));
                }
                // (C#'s primary constructor is a `parameter_list` on the class)
                let csharp = facts.lang == Some(crate::lang::Lang::CSharp);
                for c in kids(n) {
                    if matches!(c.kind(), "primary_constructor" | "class_parameters")
                        || (csharp && c.kind() == "parameter_list")
                    {
                        let mut st = vec![c];
                        while let Some(x) = st.pop() {
                            let found = locals_of(x, src, true);
                            if found.is_empty() {
                                st.extend(kids(x));
                            }
                            for (name, path, declared, _) in found {
                                let own = declared.then(|| param_node(x, &name, src)).flatten();
                                locals.push(Local {
                                    sealed: own.is_some_and(|c| written_sealed(c, src)),
                                    keyed: own.is_some_and(|c| written_keyed(c, src)),
                                    targs: own.map(|c| written_targs(c, src)).unwrap_or_default(),
                                    name,
                                    path,
                                    declared,
                                    field: true,
                                    assigned: false,
                                    given: None,
                                    elem: None,
                                    value_args: Vec::new(),
                                    targ_elems: own
                                        .map(|c| written_targ_elems(c, src))
                                        .unwrap_or_default(),
                                    key: own.and_then(|c| written_key(c, src)),
                                    written_head: None,
                                    from_def: Some(di),
                                    byte: x.start_byte(),
                                    until: 0,
                                    after: 0,
                                });
                            }
                        }
                    }
                }
            }
        } else if m.is_field(n) {
            let declared = n
                .child_by_field_name("type")
                .or_else(|| {
                    kids(n)
                        .into_iter()
                        .find(|c| c.kind() == "variable_declaration")
                        .and_then(|v| v.child_by_field_name("type"))
                })
                // (Swift `var fav: ColorOptions`: the annotation is a child
                // with no field name)
                .or_else(|| kids(n).into_iter().find(|c| c.kind() == "type_annotation"));
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
                // (Rust `const FLAGS: &[&dyn Flag] = …`, `static N: Foo = …`)
                | "const_item"
                | "static_item"
                // (Go `const RequestIDKey ctxKey = 0`)
                | "const_spec"
                // (Java `for (El e : xs)`, an interface's `Filter ALL = …`;
                // Scala `val c: Compress = …`)
                | "enhanced_for_statement"
                | "constant_declaration"
                | "val_definition"
                | "var_definition"
                | "val_declaration"
                | "var_declaration"
        ) {
            if let Some(t) = n.child_by_field_name("type") {
                let mut types = Vec::new();
                types_in(t, src, "variable_type", &mut types);
                for (p, ctx) in types {
                    push(&mut refs, n, RefKind::Type, p, ctx, None);
                }
            }
        }
        // Rust `struct Wrapper(pub Inner);`, `enum E { Loaded(Ignore) }`: the
        // types of a tuple struct's or variant's fields
        if n.kind() == "ordered_field_declaration_list" {
            let mut cursor = n.walk();
            let written: Vec<Node> = n.children_by_field_name("type", &mut cursor).collect();
            for t in written {
                let mut types = Vec::new();
                types_in(t, src, "field", &mut types);
                for (p, ctx) in types {
                    push(&mut refs, t, RefKind::Type, p, ctx, None);
                }
            }
        }
        // TypeScript: the types a type alias is written with, and those of an
        // interface's property, call, construct and index signatures
        if ecma
            && matches!(
                n.kind(),
                "type_alias_declaration"
                    | "property_signature"
                    | "call_signature"
                    | "construct_signature"
                    | "index_signature"
            )
        {
            let mut types = Vec::new();
            match n.kind() {
                "type_alias_declaration" => {
                    if let Some(t) = n.child_by_field_name("value") {
                        types_in(t, src, "alias", &mut types);
                        // (`infer A` declares `A` for the type it is written in)
                        let mut inferred = Vec::new();
                        let mut stack = vec![t];
                        while let Some(x) = stack.pop() {
                            if x.kind() == "infer_type" {
                                inferred.extend(kids(x).into_iter().next().map(|c| text(c, src)));
                            }
                            stack.extend(kids(x));
                        }
                        types.retain(|(p, _)| !(p.len() == 1 && inferred.contains(&p[0])));
                    }
                }
                // (a parameter written without a type is a name, not a type)
                "call_signature" | "construct_signature" => signature_types(n, src, &mut types),
                _ => {
                    for f in ["type", "index_type"] {
                        if let Some(t) = n.child_by_field_name(f) {
                            types_in(t, src, "field", &mut types);
                        }
                    }
                }
            }
            for (p, ctx) in types {
                push(&mut refs, n, RefKind::Type, p, ctx, None);
            }
        }
        // the types a type parameter is bound by: `[T <: Txn[T]]`, `[T: Show]`,
        // `<T extends Foo>`, `<S: Sink>`
        if matches!(n.kind(), "type_parameters" | "type_parameter_list") {
            let mut types = Vec::new();
            for tp in kids(n) {
                if tp.kind().ends_with("bound") {
                    types_in(tp, src, "bound", &mut types);
                } else if !tp.kind().contains("identifier") {
                    for c in kids(tp).into_iter().skip(1) {
                        types_in(c, src, "bound", &mut types);
                    }
                }
            }
            for (p, ctx) in types {
                push(&mut refs, n, RefKind::Type, p, ctx, None);
            }
        }
        // Scala patterns that name a type: `case Succ(p)`, `case o: Obj[In]`
        if matches!(n.kind(), "case_class_pattern" | "typed_pattern") {
            if let Some(t) = n.child_by_field_name("type") {
                let mut types = Vec::new();
                types_in(t, src, "pattern", &mut types);
                for (p, ctx) in types {
                    push(&mut refs, n, RefKind::Type, p, ctx, None);
                }
            }
        }
        // a type named to test or to convert a value, or to catch one: `s is
        // Foo`, `s as Foo`, `(Foo) s`, `s instanceof Foo`, `s.(Foo)`, `catch
        // (e: Foo)`; and Kotlin's `val x: Foo`, whose type has no field name
        if matches!(
            n.kind(),
            "instanceof_expression"
                | "cast_expression"
                | "is_expression"
                | "is_pattern_expression"
                | "as_expression"
                | "check_expression"
                | "type_test"
                | "type_assertion_expression"
                | "type_assertion"
                | "satisfies_expression"
                | "type_cast_expression"
                | "catch_block"
                | "catch_declaration"
                | "catch_formal_parameter"
                | "catch_clause"
                | "type_case"
                | "declaration_pattern"
                | "type_pattern"
                | "typeof_expression"
                | "variable_declaration"
        ) {
            let kotlin = facts.lang == Some(crate::lang::Lang::Kotlin);
            let ks = kids(n);
            let written: Vec<Node> = if n.kind() == "variable_declaration" {
                // (C# gives a declaration's type a field: `Foo<T> x = …`)
                if kotlin {
                    ks.into_iter().skip(1).collect()
                } else {
                    n.child_by_field_name("type").into_iter().collect()
                }
            } else if let Some(t) =
                n.child_by_field_name("type").or_else(|| n.child_by_field_name("right"))
            {
                vec![t]
            } else if n.kind() == "catch_clause" {
                Vec::new() // (JS: it names no type)
            } else {
                // (after the value tested: what is left that writes a type)
                let tested = usize::from(
                    !(n.kind().starts_with("catch")
                        || matches!(n.kind(), "type_test" | "typeof_expression")),
                );
                ks.into_iter()
                    .skip(tested)
                    .filter(|c| c.kind().contains("type") || c.kind().contains("pattern"))
                    .collect()
            };
            let mut types = Vec::new();
            for t in written {
                if !matches!(t.kind(), "declaration_pattern" | "type_pattern") {
                    types_in(t, src, "cast", &mut types);
                }
            }
            for (p, ctx) in types {
                push(&mut refs, n, RefKind::Type, p, ctx, None);
            }
        }
        // what a type alias is written with (TypeScript's has its own rule
        // above), and what a `where` clause bounds a type parameter by
        if matches!(
            n.kind(),
            "typealias_declaration" | "type_alias" | "type_item" | "alias_declaration"
        ) {
            let named = n.child_by_field_name("type").map(|t| vec![t]).unwrap_or_else(|| {
                kids(n)
                    .into_iter()
                    .skip(1)
                    .filter(|c| !c.kind().contains("parameter") && !c.kind().contains("modifier"))
                    .collect()
            });
            let mut types = Vec::new();
            for t in named {
                types_in(t, src, "alias", &mut types);
            }
            for (p, ctx) in types {
                push(&mut refs, n, RefKind::Type, p, ctx, None);
            }
        }
        // Positions `benchmarks/positions.py` listed as never read.
        // A closure's own signature: `func(next Handler) Handler { … }`,
        // `|a: Arg| -> Ret { … }`, `function (Foo $x): Bar { … }`
        if is_closure(n.kind()) && !by_node.contains_key(&n.id()) {
            let mut types = Vec::new();
            signature_types(n, src, &mut types);
            for (p, ctx) in types {
                push(&mut refs, n, RefKind::Type, p, ctx, None);
            }
        }
        {
            let mut types = Vec::new();
            match n.kind() {
                // Java `throws Err`, Swift `throws(ParserError)`
                "throws" | "throws_clause" => {
                    for t in kids(n) {
                        types_in(t, src, "throws", &mut types);
                    }
                }
                // Java `El... many`
                "spread_parameter" => {
                    for t in kids(n).into_iter().filter(|c| c.kind() != "variable_declarator") {
                        types_in(t, src, "parameter_type", &mut types);
                    }
                }
                // Java `El.class`
                "class_literal" => {
                    if let Some(t) = kids(n).into_iter().next() {
                        types_in(t, src, "class_literal", &mut types);
                    }
                }
                // Swift `var handler: (any Handler)? { get }` in a protocol
                "protocol_property_declaration" => {
                    for t in kids(n).into_iter().filter(|c| c.kind() == "type_annotation") {
                        types_in(t, src, "field", &mut types);
                    }
                }
                // Rust `Mode::L(Limited { n }) => …`: the struct a pattern
                // takes apart
                "struct_pattern" => {
                    if let Some(t) = n.child_by_field_name("type") {
                        types_in(t, src, "pattern", &mut types);
                    }
                }
                // C# `new Err[2]`, Java `new El[] { … }`: the element type
                "array_creation_expression" => {
                    if let Some(t) = n.child_by_field_name("type") {
                        types_in(t, src, "constructor", &mut types);
                    }
                }
                // Swift `(any Handler).self`: a type named as a value
                "navigation_expression" => {
                    let named =
                        n.child_by_field_name("element").filter(|t| t.kind().ends_with("_type"));
                    if let Some(t) = named {
                        types_in(t, src, "value", &mut types);
                    }
                }
                _ => {}
            }
            for (p, ctx) in types {
                push(&mut refs, n, RefKind::Type, p, ctx, None);
            }
        }
        if facts.lang == Some(crate::lang::Lang::Go) {
            let mut types = Vec::new();
            match n.kind() {
                // `type Key Opt`, `type Mid []func(Handler) Handler`: what a
                // defined type is defined as (a struct's fields and an
                // interface's methods are read where they are written)
                "type_spec" => {
                    let written = n
                        .child_by_field_name("type")
                        .filter(|t| !matches!(t.kind(), "struct_type" | "interface_type"));
                    if let Some(t) = written {
                        types_in(t, src, "alias", &mut types);
                    }
                }
                // `[]*Opt{…}`, `map[string]Handler{…}`: the types a literal
                // of a slice, an array or a map is made of
                "composite_literal" => {
                    let written = n.child_by_field_name("type").filter(|t| {
                        matches!(
                            t.kind(),
                            "slice_type" | "array_type" | "map_type" | "implicit_length_array_type"
                        )
                    });
                    if let Some(t) = written {
                        types_in(t, src, "constructor", &mut types);
                    }
                }
                // `make(map[string]Enc)`, `make(chan Handler, 1)`: a type
                // written as an argument
                "argument_list" => {
                    for a in kids(n) {
                        if a.kind().ends_with("_type") || a.kind() == "type_identifier" {
                            types_in(a, src, "type_argument", &mut types);
                        }
                    }
                }
                _ => {}
            }
            for (p, ctx) in types {
                push(&mut refs, n, RefKind::Type, p, ctx, None);
            }
        }
        if matches!(facts.lang, Some(crate::lang::Lang::C | crate::lang::Lang::Cpp)) {
            let mut types = Vec::new();
            match n.kind() {
                // `printbuffer p = { 0 };`, `item *q;`, `int f(item x);`: the
                // type a declaration is written with
                "declaration" => {
                    if let Some(t) = n.child_by_field_name("type") {
                        types_in(t, src, "variable_type", &mut types);
                    }
                }
                // a member function declared in its class: `static Pool*
                // Get(Slice& s);`, `virtual Handle* Lookup(…) = 0;`
                "field_declaration" => {
                    if let Some(t) = n.child_by_field_name("type") {
                        types_in(t, src, "field", &mut types);
                    }
                }
                // `friend class Wrap<Pool, true>;`, `auto make() -> Pool`,
                // `setup<Slice>(p)`: a type named as a friend, as a trailing
                // result, as an explicit template argument of a call
                "friend_declaration" | "trailing_return_type" => {
                    for t in kids(n) {
                        types_in(t, src, "type", &mut types);
                    }
                }
                "template_function" | "template_method" => {
                    if let Some(a) = n.child_by_field_name("arguments") {
                        for t in kids(a) {
                            types_in(t, src, "generic_arg", &mut types);
                        }
                    }
                }
                // `typedef item *item_ptr;`: what the name stands for (a
                // struct written there with its body is a definition)
                "type_definition" => {
                    let written = n
                        .child_by_field_name("type")
                        .filter(|t| t.child_by_field_name("body").is_none());
                    if let Some(t) = written {
                        types_in(t, src, "alias", &mut types);
                    }
                }
                _ => {}
            }
            for (p, ctx) in types {
                push(&mut refs, n, RefKind::Type, p, ctx, None);
            }
        }
        // (a C# `where_clause` is a LINQ query's: only Rust's bounds types)
        if matches!(n.kind(), "type_constraints" | "type_parameter_constraints_clause")
            || (n.kind() == "where_clause" && facts.lang == Some(crate::lang::Lang::Rust))
        {
            let mut types = Vec::new();
            types_in(n, src, "bound", &mut types);
            for (p, ctx) in types {
                push(&mut refs, n, RefKind::Type, p, ctx, None);
            }
        }
        // JS / TS / PHP `s instanceof Foo`
        if n.kind() == "binary_expression"
            && n.child_by_field_name("operator").is_some_and(|o| o.kind() == "instanceof")
        {
            if let Some(p) = n.child_by_field_name("right").and_then(|r| path_of(r, src)) {
                push(&mut refs, n, RefKind::Type, p, "cast", None);
            }
        }
        // `typeof Backbone.Model`: the value whose type is meant
        if n.kind() == "type_query" {
            if let Some(p) = kids(n).into_iter().next().and_then(|v| path_of(v, src)) {
                push(&mut refs, n, RefKind::Type, p, "type_query", None);
            }
        }
        if !by_node.contains_key(&n.id()) {
            // the parameters of a closure, a block or a handler that is no
            // definition of its own: names of its body only
            let mut params = closure_params(n, src);
            // `xs.forEach { x -> … }`, `xs.map(x => …)`, `xs.each do |x|`:
            // the first parameter is an element of `xs` (`x.let { … }`: `x`
            // itself); a Kotlin lambda that names none has `it`
            // (Swift's has `$0`)
            let lambda = implicit_parameter(n);
            if lambda.is_some() || params.first().is_some_and(|(_, path, _)| path.is_empty()) {
                if let Some((at, of, each)) = iterated_on(&anc, n, src) {
                    let mut one = of.clone();
                    if each {
                        one.push(ITERATED.into());
                    }
                    let mut first = of.clone();
                    first.push(KEY.into());
                    let mut second = of;
                    second.push(VALUE.into());
                    // `{ (k, v) -> … }`, `([k, v]) => …`, `|(k, v)|`: an entry
                    // taken apart, its value second
                    let destructured = each && at == 0 && destructures_pair(n);
                    let two = params.len() == 2;
                    if let (Some(it), 0) = (lambda, at) {
                        params = vec![(it.to_string(), one, false)];
                    } else if destructured && two {
                        if params[0].1.is_empty() {
                            params[0].1 = first;
                        }
                        if params[1].1.is_empty() {
                            params[1].1 = second;
                        }
                    } else if lambda.is_none() && params.get(at).is_some_and(|p| p.1.is_empty()) {
                        params[at].1 = one;
                        // `m.forEach((k, v) -> …)`: a map's key and value (a
                        // list's element and index)
                        if each && at == 0 && two && params[1].1.is_empty() {
                            params[0].1 = first;
                            params[1].1 = second;
                        }
                        // `xs.fold(Acc()) { acc, x -> … }`: the accumulator
                        if at == 1 && params[0].1.is_empty() {
                            if let Some(init) = fold_init(&anc, n, src) {
                                params[0].1 = init;
                            }
                        }
                    }
                }
            }
            params.retain(|(name, ..)| name != "_");
            if !params.is_empty() {
                // a parameter list stands for the block around it
                let body = if n.kind().ends_with("_parameters") || n.kind() == "exception_variable"
                {
                    anc.last().copied().unwrap_or(n)
                } else {
                    n
                };
                // JS: a function passed to a call, with its parameters in order
                if matches!(n.kind(), "function_expression" | "arrow_function" | "function")
                    && anc.last().is_some_and(|a| a.kind() == "arguments")
                {
                    let call = anc.len().checked_sub(2).and_then(|i| anc.get(i));
                    if let Some(callee) = call
                        .filter(|c| c.kind() == "call_expression")
                        .and_then(|c| c.child_by_field_name("function"))
                        .and_then(|c| path_of(c, src))
                    {
                        let names = params.iter().map(|(name, ..)| name.clone()).collect();
                        facts.callbacks.push((body.start_byte(), callee, names));
                    }
                }
                closures.push((body.start_byte(), body.end_byte(), params));
            }
            // `for (x in xs)`, `for x in xs`, `for _, x := range xs`
            for (name, of) in loop_variable(n, src) {
                locals.push(Local {
                    name,
                    path: of,
                    declared: false,
                    field: false,
                    assigned: false,
                    given: None,
                    elem: None,
                    value_args: Vec::new(),
                    sealed: false,
                    keyed: false,
                    targs: Vec::new(),
                    targ_elems: Vec::new(),
                    key: None,
                    written_head: None,
                    from_def: None,
                    byte: n.start_byte(),
                    until: 0,
                    after: 0,
                });
            }
        }
        if !by_node.contains_key(&n.id()) {
            // an untyped `x = …` declares a local only where assigning declares
            // (Python, Ruby); elsewhere it writes a variable declared before
            let plain_assignment = matches!(n.kind(), "assignment" | "assignment_expression")
                && !m.assignment_declares();
            // `let (a, b) = f()`: each name is a part of what `f` returns
            let parts = destructured(n, src);
            if let Some((names, value)) = &parts {
                if let Some(of) = value_type(*value, src) {
                    for (i, name) in names.iter().enumerate().filter(|(_, n)| *n != "_") {
                        let mut path = of.clone();
                        path.push(tuple_part(i));
                        locals.push(Local {
                            name: name.clone(),
                            path,
                            declared: false,
                            field: false,
                            assigned: plain_assignment,
                            given: None,
                            elem: None,
                            value_args: Vec::new(),
                            sealed: false,
                            keyed: false,
                            targs: Vec::new(),
                            targ_elems: Vec::new(),
                            key: None,
                            written_head: None,
                            from_def: None,
                            byte: n.start_byte(),
                            until: 0,
                            after: 0,
                        });
                    }
                }
            }
            // `for (Pet pet : pets)`, `o is Pet p`, `if let Some(p) = e`
            for (name, path, declared, range) in bound_by_pattern(n, src) {
                locals.push(Local {
                    name,
                    path,
                    declared,
                    field: false,
                    assigned: false,
                    given: None,
                    elem: None,
                    value_args: Vec::new(),
                    sealed: false,
                    keyed: false,
                    targs: Vec::new(),
                    targ_elems: Vec::new(),
                    key: None,
                    written_head: None,
                    from_def: None,
                    byte: range.map_or(n.start_byte(), |(lo, _)| lo),
                    until: range.map_or(0, |(_, hi)| hi),
                    after: range.map_or(0, |(lo, _)| lo),
                });
            }
            let taken_apart = parts.is_some();
            // (a Rust `let` is in force once it has ended: its own value
            // still sees the declaration before it, `let r = r.clone();`)
            let declared_at =
                if n.kind() == "let_declaration" { n.end_byte() } else { n.start_byte() };
            for (name, mut path, declared, field) in
                locals_of(n, src, false).into_iter().filter(|_| !taken_apart)
            {
                // Ruby `s = stubs`: a bare name on the right is a call (or
                // another local), and the local is what that gives
                if path.is_empty() && facts.lang == Some(crate::lang::Lang::Ruby) {
                    if let Some(r) =
                        n.child_by_field_name("right").filter(|r| r.kind() == "identifier")
                    {
                        path = vec![text(r, src)];
                    }
                }
                if plain_assignment && path.is_empty() {
                    continue;
                }
                let call = (!declared && !path.is_empty()).then(|| declared_call(n)).flatten();
                let given = call.and_then(|c| given_type(c, src));
                let value_args = call
                    .map(|c| arg_facts(c, src))
                    .filter(|a| !a.is_empty() && a.len() <= 4)
                    .unwrap_or_default();
                // `val y = xs[0]`: an element of `xs`
                if path.is_empty() && !declared {
                    if let Some(of) = indexed(n, src) {
                        path = of;
                    }
                }
                locals.push(Local {
                    name,
                    path,
                    declared,
                    field,
                    assigned: plain_assignment,
                    given,
                    elem: if declared { written_elem(n, src) } else { None },
                    value_args,
                    sealed: declared && written_sealed(n, src),
                    keyed: declared && written_keyed(n, src),
                    targs: if declared { written_targs(n, src) } else { Vec::new() },
                    targ_elems: if declared { written_targ_elems(n, src) } else { Vec::new() },
                    key: if declared { written_key(n, src) } else { None },
                    written_head: (declared
                        && matches!(
                            facts.lang,
                            Some(crate::lang::Lang::C | crate::lang::Lang::Cpp)
                        ))
                    .then(|| written_head(n, src))
                    .flatten(),
                    from_def: None,
                    byte: declared_at,
                    until: 0,
                    after: 0,
                });
            }
        }
        // type arguments anywhere: `Deserialize<Person>()`, `IRequest<Envelope>`,
        // `List<Foo>` in a body (signatures repeat them; edges are unique)
        if matches!(n.kind(), "type_arguments" | "type_argument_list") {
            let mut types = Vec::new();
            for c in kids(n) {
                types_in(c, src, "generic_arg", &mut types);
            }
            for (p, ctx) in types {
                push(&mut refs, n, RefKind::Type, p, ctx, None);
            }
        }
        // Kotlin operator conventions (kotlin.core/operators.md): `a + b` is
        // `a.plus(b)`, `a[i]` is `a.get(i)`, `a upTo b` is `a.upTo(b)` (an
        // `infix fun`); context `operator`, resolved only through a's type
        // (C++ `a + b`: `a.operator+(b)` when a's class declares it,
        // [over.match.oper])
        if facts.lang == Some(crate::lang::Lang::Cpp) && n.kind() == "binary_expression" {
            let op = n.child_by_field_name("operator").map(|o| text(o, src));
            let lhs = n.child_by_field_name("left").and_then(|l| path_of(l, src));
            if let (Some(op), Some(mut path)) = (op, lhs) {
                if matches!(op.as_str(), "+" | "-" | "*" | "/" | "%" | "==" | "!=" | "<" | ">")
                    && !path.iter().any(|s| s == UNKNOWN)
                {
                    path.push(format!("operator{op}"));
                    push(&mut refs, n, RefKind::Call, path, "operator", None);
                }
            }
        }
        if facts.lang == Some(crate::lang::Lang::Kotlin) {
            let named = kids(n);
            let method = match n.kind() {
                "additive_expression" | "multiplicative_expression" => {
                    let mut cursor = n.walk();
                    let op = n
                        .children(&mut cursor)
                        .find(|c| !c.is_named())
                        .map(|c| c.kind().to_string())
                        .unwrap_or_default();
                    match op.as_str() {
                        "+" => Some("plus".to_string()),
                        "-" => Some("minus".to_string()),
                        "*" => Some("times".to_string()),
                        "/" => Some("div".to_string()),
                        "%" => Some("rem".to_string()),
                        _ => None,
                    }
                }
                "indexing_expression" => Some("get".to_string()),
                "infix_expression" => {
                    named.get(1).filter(|x| x.kind() == "simple_identifier").map(|x| text(*x, src))
                }
                _ => None,
            };
            if let Some(m) = method {
                let lhs = named.first().and_then(|l| path_of(*l, src));
                if let Some(mut path) = lhs.filter(|p| !p.iter().any(|s| s == UNKNOWN)) {
                    path.push(m);
                    push(&mut refs, n, RefKind::Call, path, "operator", None);
                }
            }
        }
        // JSX: `<FormMessage />`, `<Table.Cell>` render a component
        if matches!(n.kind(), "jsx_opening_element" | "jsx_self_closing_element") {
            if let Some(path) = n.child_by_field_name("name").and_then(|x| path_of(x, src)) {
                if path.first().is_some_and(|h| h.chars().next().is_some_and(|c| c.is_uppercase()))
                {
                    push(&mut refs, n, RefKind::Call, path, "jsx", None);
                }
            }
        }
        if let Some(found) = receiver_lambda(n, src) {
            receivers.push(found);
        }
        if facts.lang == Some(crate::lang::Lang::Ruby) {
            if let Some((name, declares)) = shared_examples_call(n, src) {
                let line = n.start_position().row as u32 + 1;
                if declares {
                    facts.shared_examples.push((name, line, n.end_position().row as u32 + 1));
                } else {
                    facts.example_includes.push((name, line));
                }
            }
            let found = ruby_attrs(n, src);
            if !found.is_empty() {
                // the class or module the declaration is written in
                let owner = anc.iter().rev().find_map(|a| {
                    by_node.get(&a.id()).copied().filter(|&di| facts.defs[di].kind.is_class_like())
                });
                for name in found {
                    if let Some(di) = owner {
                        if !facts.defs[di].attrs.contains(&name) {
                            facts.defs[di].attrs.push(name.clone());
                        }
                    }
                    if !facts.attrs.contains(&name) {
                        facts.attrs.push(name);
                    }
                }
            }
        }
        // `return Foo()` in a definition (not in a closure inside it)
        if let Some(v) = returned_value(n, src) {
            let home = anc.iter().rev().find_map(|a| match by_node.get(&a.id()) {
                Some(&di) if facts.defs[di].kind.is_callable() => Some(Some(di)),
                _ if is_closure(a.kind()) => Some(None),
                _ => None,
            });
            if let Some(Some(di)) = home {
                if let Some(path) = returned_path(v, src) {
                    note_return(&mut facts.defs[di], path);
                }
                note_parts(&mut facts.defs[di], v, src);
                note_members(&mut facts.defs[di], v, src);
            }
        }
        // reads and writes of variables, fields and constants, and of what
        // lies outside the program (environment, files, tables, events)
        state.visit(n, src, &anc);
        crate::resources::visit(n, src, facts.lang, &anc, &mut resources);
        if let Some(extra) = m.refs_of(n, src) {
            for (kind, path, context) in extra {
                push(&mut refs, n, kind, path, context, None);
            }
        } else if n.parent().is_some_and(|p| p.kind() == "decorator") {
            // the decorator expression itself is the definition's decorator
            // reference, not a call of the enclosing scope
        } else if let Some((name, of)) = implicit_member_value(n, src, &anc) {
            // Swift `.body` not called: a case or static member of the type the
            // place asks for
            let at = refs.len();
            push(&mut refs, n, RefKind::Attr, vec![IMPLICIT.into(), name], "implicit", None);
            if let Some(r) = refs.get_mut(at) {
                r.implicit = Some(of);
            }
        } else if let Some(name) = implicit_member_call(n, src) {
            // Swift `.status(301)`: a static member of the type the place asks
            // for (an argument's parameter, a declared type, a result)
            if let Some(of) = implicit_place(&anc, src) {
                let at = refs.len();
                push(&mut refs, n, RefKind::Call, vec![IMPLICIT.into(), name], "call", None);
                if let Some(r) = refs.get_mut(at) {
                    r.args = arg_count(n);
                    r.hints = arg_facts(n, src);
                    r.implicit = Some(of);
                }
            }
        } else if let Some((kind, path)) = callee(n, src) {
            let context = if kind == RefKind::New { "constructor" } else { "call" };
            // `new Callback<T>() { … }` is a definition itself: the class has
            // that type as its base, and the method around it constructs it
            let anon = by_node.get(&n.id()).copied().filter(|_| {
                kind == RefKind::New && crate::generic::anonymous_class(n, src).is_some()
            });
            let at = match anon {
                Some(di) => {
                    push(&mut refs, n, RefKind::Base, path.clone(), "", Some(di));
                    let at = refs.len();
                    let outer = facts.defs[di].parent;
                    if outer.is_some() {
                        push(&mut refs, n, kind, path, context, outer);
                    }
                    at
                }
                None => {
                    let at = refs.len();
                    push(&mut refs, n, kind, path, context, None);
                    at
                }
            };
            if let Some(r) = refs.get_mut(at) {
                r.args = arg_count(n);
                r.self_arg = passes_self(n, src);
                r.hints = arg_facts(n, src);
                r.targs = call_targs(n);
            }
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
                        // Go `http.HandlerFunc(mx.routeHTTP)`, `time.AfterFunc(d,
                        // cb.onTimeout)`: a method value handed over
                        "selector_expression"
                            if crate::parse::current() == Some(crate::lang::Lang::Go) =>
                        {
                            if let Some(p) = path_of(a, src) {
                                push(&mut refs, a, RefKind::Indirect, p, "argument", None);
                            }
                        }
                        // PHP `[$this, 'handle']`, `[Foo::class, 'handle']`: a
                        // method named by the object and its name
                        "array_creation_expression" => {
                            if let Some(p) = php_callable(a, src) {
                                push(&mut refs, a, RefKind::Indirect, p, "argument", None);
                            }
                        }
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
        } else if let Some((path, args)) = declared_construction(n, src, &anc, facts.lang) {
            let at = refs.len();
            push(&mut refs, n, RefKind::New, path, "constructor", None);
            if let Some(r) = refs.get_mut(at) {
                r.args = args;
            }
        }
        // `this::helper`, `Util::twice`, `::handle`: the function the
        // reference names; Kotlin `Foo::class`: the class
        if matches!(n.kind(), "callable_reference" | "method_reference") {
            let ks: Vec<Node> = kids(n).into_iter().filter(|c| !is_comment(c)).collect();
            // (the grammar keeps `class` out of the tree)
            if text(n, src).ends_with("::class") {
                if let Some(p) = ks.first().and_then(|c| path_of(*c, src)) {
                    push(&mut refs, n, RefKind::Type, p, "class_literal", None);
                }
            } else if let Some(p) = referenced(n, src) {
                push(&mut refs, n, RefKind::Indirect, p, "argument", None);
            }
        }
        // Swift `case option(ParsedArgument)`: the types a case carries
        if n.kind() == "enum_type_parameters" {
            let mut types = Vec::new();
            types_in(n, src, "field", &mut types);
            for (p, ctx) in types {
                push(&mut refs, n, RefKind::Type, p, ctx, None);
            }
        }
        // Python `except Exit as e:`, `except (A, m.B):`: the types caught
        if n.kind() == "except_clause" {
            let mut caught = n
                .child_by_field_name("value")
                .or_else(|| kids(n).into_iter().find(|c| c.kind() != "block" && !is_comment(c)));
            if let Some(c) = caught.filter(|c| c.kind() == "as_pattern") {
                caught = kids(c).into_iter().next();
            }
            let each: Vec<Node> = match caught {
                Some(c) if matches!(c.kind(), "tuple" | "parenthesized_expression") => kids(c),
                Some(c) => vec![c],
                None => Vec::new(),
            };
            for c in each {
                if matches!(c.kind(), "identifier" | "attribute") {
                    if let Some(p) = path_of(c, src) {
                        push(&mut refs, c, RefKind::Type, p, "except", None);
                    }
                }
            }
        }
        // (Kotlin's grammar reads `Repo::class` outside an argument list as a
        // navigation with nothing after the `::class`)
        if n.kind() == "navigation_expression" && facts.lang == Some(crate::lang::Lang::Kotlin) {
            let ks = kids(n);
            let bare = ks.len() == 2
                && ks[1].kind() == "navigation_suffix"
                && kids(ks[1]).is_empty()
                && text(ks[1], src) == "::class";
            if bare {
                if let Some(p) = path_of(ks[0], src) {
                    push(&mut refs, n, RefKind::Type, p, "class_literal", None);
                }
            }
        }
        // C# `: base(1, text)` / `: this("x")`: the constructor of the base
        // class, or another of the class itself
        // (Java `super(1, text);` / `this("x");` in a constructor)
        if matches!(n.kind(), "constructor_initializer" | "explicit_constructor_invocation") {
            let written = text(n, src);
            let word = written.trim_start_matches(':').trim_start();
            let head = if word.starts_with("base") || word.starts_with("super") {
                "super"
            } else {
                "self"
            };
            let passed = kids(n)
                .into_iter()
                .find(|c| c.kind() == "argument_list")
                .map(|l| kids(l).iter().filter(|c| !is_comment(c)).count());
            let at = refs.len();
            push(&mut refs, n, RefKind::New, vec![head.to_string()], "constructor", None);
            if let Some(r) = refs.get_mut(at) {
                r.args = passed.and_then(|n| u8::try_from(n).ok());
            }
        }
        // PHP `Repo::class`: the class named
        if n.kind() == "class_constant_access_expression" {
            let ks = kids(n);
            if ks.len() == 2 && text(ks[1], src) == "class" {
                if let Some(p) = path_of(ks[0], src).filter(|p| p[0] != "self" && p[0] != "super") {
                    push(&mut refs, n, RefKind::Type, p, "class_literal", None);
                }
            }
        }
        // JS `this` in a function expression no definition holds
        if n.kind() == "this"
            && matches!(
                facts.lang,
                Some(
                    crate::lang::Lang::JavaScript
                        | crate::lang::Lang::TypeScript
                        | crate::lang::Lang::Tsx
                )
            )
        {
            if let Some(of) = this_attached(&anc, src) {
                this_at.insert(n.start_byte(), of);
            }
        }
        // JS `Object.defineProperty(this, 'router', …)`, `Object.create(p, {
        // app: … })`: properties defined at run time
        if n.kind() == "call_expression"
            && matches!(
                facts.lang,
                Some(crate::lang::Lang::JavaScript | crate::lang::Lang::TypeScript)
            )
        {
            let callee = n.child_by_field_name("function").and_then(|c| path_of(c, src));
            let args: Vec<Node> = n
                .child_by_field_name("arguments")
                .map(|a| kids(a).into_iter().filter(|c| !is_comment(c)).collect())
                .unwrap_or_default();
            match (callee.as_deref(), args.as_slice()) {
                (Some([o, d]), [_, name, ..]) if o == "Object" && d == "defineProperty" => {
                    facts.runtime_props.extend(string_arg(*name, src));
                }
                (Some([o, c]), [_, props, ..])
                    if o == "Object" && c == "create" && props.kind() == "object" =>
                {
                    for pair in kids(*props).into_iter().filter(|p| p.kind() == "pair") {
                        if let Some(k) = pair.child_by_field_name("key") {
                            facts
                                .runtime_props
                                .push(text(k, src).trim_matches(['\'', '"']).to_string());
                        }
                    }
                }
                _ => {}
            }
        }
        // JS `const article = req.article`: a name given a request's property
        if n.kind() == "variable_declarator"
            && matches!(
                facts.lang,
                Some(crate::lang::Lang::JavaScript | crate::lang::Lang::TypeScript)
            )
        {
            let name = n.child_by_field_name("name").filter(|x| x.kind() == "identifier");
            let value = n.child_by_field_name("value").filter(|v| v.kind() == "member_expression");
            if let (Some(name), Some(v)) = (name, value) {
                let object = v.child_by_field_name("object").filter(|o| o.kind() == "identifier");
                if let (Some(o), Some(p)) = (object, v.child_by_field_name("property")) {
                    if matches!(text(o, src).as_str(), "req" | "request") {
                        facts.request_aliases.push((text(name, src), text(p, src)));
                    }
                }
            }
        }
        // JS `req.article = …`: a property put on a request
        if n.kind() == "assignment_expression"
            && matches!(
                facts.lang,
                Some(crate::lang::Lang::JavaScript | crate::lang::Lang::TypeScript)
            )
        {
            if let Some(left) =
                n.child_by_field_name("left").filter(|l| l.kind() == "member_expression")
            {
                let object =
                    left.child_by_field_name("object").filter(|o| o.kind() == "identifier");
                let prop = left.child_by_field_name("property");
                if let (Some(o), Some(p)) = (object, prop) {
                    if matches!(text(o, src).as_str(), "req" | "request") {
                        facts.request_props.push(text(p, src));
                    }
                }
            }
        }
        // JS `mongoose.model('Article', ArticleSchema)`: a model registered
        if n.kind() == "call_expression"
            && matches!(
                facts.lang,
                Some(crate::lang::Lang::JavaScript | crate::lang::Lang::TypeScript)
            )
        {
            let callee = n.child_by_field_name("function").and_then(|c| path_of(c, src));
            if callee.is_some_and(|c| c == ["mongoose", "model"] || c == ["model"]) {
                let args: Vec<Node> = n
                    .child_by_field_name("arguments")
                    .map(|a| kids(a).into_iter().filter(|c| !is_comment(c)).collect())
                    .unwrap_or_default();
                if let [name, schema, ..] = args.as_slice() {
                    if let (Some(name), "identifier") = (string_arg(*name, src), schema.kind()) {
                        facts.models.push((name, text(*schema, src)));
                    }
                }
            }
        }
        // C, C++: a function put in a slot of a struct
        if matches!(facts.lang, Some(crate::lang::Lang::C | crate::lang::Lang::Cpp)) {
            facts.slots.extend(slot_facts(n, src));
        }
        // C++ `Wrapper(T&&, bool) -> Wrapper<T, true>;`: a deduction guide,
        // which declares no function (the template's own name, a trailing
        // result naming it, no type before it)
        if facts.lang == Some(crate::lang::Lang::Cpp) {
            if let Some(name) = deduction_guide(n, src) {
                let (lo, hi) = (n.start_byte(), n.end_byte());
                for d in facts.defs.iter_mut() {
                    if d.name == name && lo <= d.start_byte && d.end_byte <= hi {
                        d.misread = true;
                    }
                }
                misread.push((lo, hi));
            }
        }
        // C, C++ `typedef struct conn conn_t;`, C++ `using Header = …;`: what
        // the alias names
        // Kotlin / Swift `typealias Store = Repo`: another name for one type
        // (a function type, a tuple or a union names no type of its own)
        if matches!(n.kind(), "type_alias" | "typealias_declaration")
            && matches!(facts.lang, Some(crate::lang::Lang::Kotlin | crate::lang::Lang::Swift))
        {
            let named = n
                .child_by_field_name("value")
                .or_else(|| kids(n).into_iter().rev().find(|c| !is_comment(c)))
                .filter(|t| matches!(t.kind(), "user_type" | "type_identifier"));
            if let Some(ty) = named.and_then(|t| path_of(t, src)) {
                let (lo, hi) = (n.start_byte(), n.end_byte());
                for d in facts.defs.iter_mut() {
                    if d.kind == crate::facts::DefKind::TypeAlias
                        && lo <= d.start_byte
                        && d.end_byte <= hi
                        && d.declared_type.is_none()
                    {
                        d.declared_type = Some(ty.clone());
                    }
                }
            }
        }
        // Python `global x` / `nonlocal x`: an assignment to x in the function
        // binds the module's (or the enclosing function's) name, no local
        if matches!(n.kind(), "global_statement" | "nonlocal_statement") {
            for c in kids(n).into_iter().filter(|c| c.kind() == "identifier") {
                outer_names.push((n.start_byte(), text(c, src)));
            }
        }
        // Scala `export p.print` / `export q.{a, b}`: members forwarded
        if n.kind() == "export_declaration" {
            let mut cursor = n.walk();
            let path: Vec<String> = n
                .children_by_field_name("path", &mut cursor)
                .filter(|c| c.is_named())
                .map(|c| text(c, src))
                .collect();
            let picked: Vec<String> = kids(n)
                .into_iter()
                .filter(|c| c.kind() == "namespace_selectors")
                .flat_map(kids)
                .filter(|c| c.kind() == "identifier")
                .map(|c| text(c, src))
                .collect();
            if picked.is_empty() {
                if path.len() >= 2 {
                    facts.forwards.push((n.start_byte(), path));
                }
            } else {
                for m in picked {
                    let mut p = path.clone();
                    p.push(m);
                    facts.forwards.push((n.start_byte(), p));
                }
            }
        }
        // C++ `inline namespace v2 { … }`
        if n.kind() == "namespace_definition" && kids(n).iter().any(|c| c.kind() == "inline")
            || (n.kind() == "namespace_definition"
                && text(n, src).trim_start().starts_with("inline"))
        {
            facts.inline_namespaces.push(n.start_byte());
        }
        // TS `import Internal = My.Internal;`: a name for what the path names
        if n.kind() == "import_alias" {
            let ks = kids(n);
            let name = ks.first().filter(|c| c.kind() == "identifier").map(|c| text(*c, src));
            let of = ks.get(1).and_then(|c| path_of(*c, src));
            if let (Some(name), Some(of)) = (name, of) {
                if of.first() != Some(&name) {
                    facts.ns_aliases.push((name, of));
                }
            }
        }
        // C++ `namespace u = util;`
        if n.kind() == "namespace_alias_definition" {
            let name = n.child_by_field_name("name").map(|c| text(c, src));
            let of =
                kids(n).into_iter().rev().find(|c| !is_comment(c)).and_then(|c| path_of(c, src));
            if let (Some(name), Some(of)) = (name, of) {
                if of != [name.clone()] {
                    facts.ns_aliases.push((name, of));
                }
            }
        }
        if matches!(facts.lang, Some(crate::lang::Lang::C | crate::lang::Lang::Cpp))
            && matches!(n.kind(), "type_definition" | "alias_declaration")
        {
            if let Some(t) = n.child_by_field_name("type") {
                let (lo, hi) = (n.start_byte(), n.end_byte());
                for d in facts.defs.iter_mut() {
                    if d.kind == crate::facts::DefKind::TypeAlias
                        && lo <= d.start_byte
                        && d.end_byte <= hi
                        && d.declared_type.is_none()
                    {
                        // `typedef struct conn { … } conn_t;`: the struct's own
                        // name (what its body holds is no target), none for an
                        // anonymous one
                        d.declared_type = if matches!(
                            t.kind(),
                            "struct_specifier"
                                | "union_specifier"
                                | "enum_specifier"
                                | "class_specifier"
                        ) {
                            t.child_by_field_name("name").map(|nm| vec![text(nm, src)])
                        } else {
                            first_type(t, src)
                        };
                        d.alias_args = template_args(t, src);
                    }
                }
            }
        }
        // C++ `Pool pool(context);` beside a local `context`: the grammar
        // reads a declaration of a function `pool`, the language a variable
        // (a local hides a type of its name)
        if facts.lang == Some(crate::lang::Lang::Cpp) {
            if let Some((name, ty, args, calls, qualified, this_arg)) =
                misread_variable(n, src, &anc, &locals)
            {
                let (lo, hi) = (n.start_byte(), n.end_byte());
                for d in facts.defs.iter_mut() {
                    if d.name == name && lo <= d.start_byte && d.end_byte <= hi {
                        d.misread = true;
                        // (`spans.py`'s grammar reads a call or a `this`
                        // among the arguments as the variable it is)
                        d.unspanned = !calls.is_empty() || this_arg;
                    }
                }
                misread.push((lo, hi));
                let at = refs.len();
                push(&mut refs, n, RefKind::New, ty.clone(), "constructor", None);
                if let Some(r) = refs.get_mut(at) {
                    r.args = u8::try_from(
                        args.len() + calls.len() + qualified.len() + usize::from(this_arg),
                    )
                    .ok();
                }
                for a in args {
                    push(&mut refs, a, RefKind::Indirect, vec![text(a, src)], "argument", None);
                }
                for q in qualified {
                    let path = text(q, src).split("::").map(str::to_string).collect();
                    push(&mut refs, q, RefKind::Indirect, path, "argument", None);
                }
                for c in calls {
                    if let Some(path) = path_of(c, src) {
                        push(&mut refs, c, RefKind::Call, path, "call", None);
                    }
                }
                locals.push(Local {
                    name,
                    path: ty,
                    declared: true,
                    field: false,
                    assigned: false,
                    given: None,
                    elem: None,
                    value_args: Vec::new(),
                    sealed: false,
                    keyed: false,
                    targs: Vec::new(),
                    targ_elems: Vec::new(),
                    key: None,
                    written_head: written_head(n, src),
                    from_def: None,
                    byte: n.start_byte(),
                    until: 0,
                    after: 0,
                });
            }
        }
        let mut cursor = n.walk();
        let children: Vec<Node> = n.children(&mut cursor).filter(|c| c.is_named()).collect();
        anc.push(n);
        stack.extend(children.into_iter().rev().map(|c| (c, depth + 1)));
    }
    for l in locals[stamped..].iter_mut().filter(|l| !l.field && l.until == 0) {
        l.until = until;
        if let Some((_, end)) = declared.filter(|(value, _)| l.byte < *value && scoped) {
            l.after = end;
        }
    }
    // (what the signature of a misread declaration wrote is no use: its
    // "parameter types" are the arguments, read above)
    if !misread.is_empty() {
        refs.retain(|r| {
            !(matches!(r.context, "parameter_type" | "return_type")
                && misread.iter().any(|&(lo, hi)| lo <= r.byte && r.byte < hi))
        });
    }
    if !this_at.is_empty() {
        for r in refs.iter_mut().filter(|r| r.path.first().is_some_and(|h| h == "self")) {
            r.this_of = this_at.get(&r.byte).cloned();
        }
    }
    add_extras(&mut facts.defs, &extras, m, src);
    let hoisted = add_blocks(&mut facts.defs, blocks, &invoked);
    locals.retain(|l| !hoisted.iter().any(|(name, at)| *at == l.byte && *name == l.name));
    let defs = &facts.defs;
    // `subject { conn }`, `let(:conn) { … }`: the call that declares a
    // callable is no use of a name
    let declaring: std::collections::HashSet<usize> = defs
        .iter()
        .filter(|d| d.extra && d.kind.is_callable() && d.params == Some(0))
        .map(|d| d.start_byte)
        .collect();
    if !declaring.is_empty() {
        refs.retain(|r| !(r.path.len() == 1 && declaring.contains(&r.byte)));
    }
    // what a Rust macro's arguments read and write (`assert_eq!(c.n, 1)`):
    // the same walk on the arguments read as an expression
    for mac in &macros {
        state.add_uses(macro_state_uses(*mac, src));
    }
    refs.extend(state.finish(defs, &imports, m.needs_import(), |bytes| {
        innermost_where(defs, bytes, |d| !d.misread)
    }));
    // `assert_eq!(handle_count(&pool), 2)`: the grammar gives a macro's
    // arguments as tokens; read as expressions they are calls like any other
    for mac in macros {
        let (found, bound) = macro_refs(m, mac, src, macro_depth);
        refs.extend(found);
        locals.extend(bound);
    }
    bind_closure_params(&closures, &mut refs, &mut locals);
    facts.receivers = bind_receivers(&receivers, &mut refs);
    // the innermost definition around each use (a sweep: defs are in source
    // pre-order, so a stack of open ranges is the chain of ancestors)
    assign_defs(&facts.defs, &mut refs);
    assign_local_defs(&facts.defs, &mut locals);
    let bytes: Vec<usize> = resources.iter().map(|r| r.byte).collect();
    for (r, d) in resources.iter_mut().zip(homes(&facts.defs, &bytes)) {
        r.from_def = d;
    }
    facts.resources = resources;
    let bytes: Vec<usize> = facts.slots.iter().map(|s| s.byte).collect();
    for (s, d) in facts.slots.iter_mut().zip(homes(&facts.defs, &bytes)) {
        s.from_def = d;
    }
    // `const pick: Picker = () => …`: the name is the function it declares,
    // a definition of the scope; the type written beside it makes no local
    // that would hide it where it is passed as a value
    {
        let defs = &facts.defs;
        locals.retain(|l| {
            l.field
                || !function_decls.contains(&l.byte)
                || !defs.iter().any(|d| {
                    d.kind.is_callable()
                        && !d.block
                        && !d.misread
                        && d.name == l.name
                        && d.parent == l.from_def
                })
        });
    }
    // (`global counter` in a function: `counter = …` there is the module's)
    if !outer_names.is_empty() {
        let bytes: Vec<usize> = outer_names.iter().map(|(b, _)| *b).collect();
        let named: Vec<(Option<usize>, String)> = homes(&facts.defs, &bytes)
            .into_iter()
            .zip(outer_names.iter().map(|(_, n)| n.clone()))
            .collect();
        locals.retain(|l| l.field || !named.iter().any(|(d, n)| *d == l.from_def && *n == l.name));
    }
    // one per (definition, name, field): a declared type over a guessed one
    locals.retain(|l| !matches!(l.path.as_slice(), [t] if t == "var" || t == "auto" || t == "let"));
    locals.sort_by(|a, b| {
        (a.from_def, &a.name, !a.field, a.assigned, a.until, !a.declared).cmp(&(
            b.from_def,
            &b.name,
            !b.field,
            b.assigned,
            b.until,
            !b.declared,
        ))
    });
    // (Rust declares a name again in the scope it is in, `let r = a(); let r
    // = b();`, and in each arm of a `match`: every one is kept, and a use
    // has the one declared last before it, `Project::local_in_force`)
    // (and a name declared in two blocks is two locals: `if (a) { const x =
    // new A() } else { const x = new B() }`; an assignment's hint is never
    // one beside a declaration)
    let redeclares = facts.lang == Some(crate::lang::Lang::Rust);
    locals.dedup_by(|b, a| {
        a.from_def == b.from_def
            && a.name == b.name
            && !(redeclares && !a.field && !b.field)
            && (a.field || b.field || b.assigned || a.until == b.until)
    });
    for imp in imports.iter_mut() {
        if imp.runs == Runs::ModuleLevel && inside_callable(&facts.defs, imp.byte) {
            imp.runs = Runs::Deferred;
        }
    }
    // an implicit member in an argument: the call it is an argument of, by
    // its place among the references (the node's byte until here)
    let at: HashMap<usize, u32> = refs
        .iter()
        .enumerate()
        .filter(|(_, r)| matches!(r.kind, RefKind::Call | RefKind::New))
        .map(|(i, r)| (r.byte, i as u32))
        .collect();
    for r in refs.iter_mut() {
        if let Some(Implicit::Arg { call, .. }) = &mut r.implicit {
            match at.get(&(*call as usize)) {
                Some(i) => *call = *i,
                None => r.implicit = None,
            }
        }
    }
    refs.retain(|r| r.path.first().is_none_or(|h| h != IMPLICIT) || r.implicit.is_some());
    facts.refs = refs;
    facts.imports = imports;
    facts.locals = locals;
}

/// The head of the path of a Swift implicit member expression (`.name`).
pub const IMPLICIT: &str = "[.]";

/// `.status(301)`: the name of an implicit member that is called.
fn implicit_member_call(n: Node, src: &[u8]) -> Option<String> {
    if n.kind() != "call_expression" {
        return None;
    }
    let head = kids(n).into_iter().next().filter(|c| c.kind() == "prefix_expression")?;
    if src.get(head.start_byte()) != Some(&b'.') {
        return None;
    }
    let target = head.child_by_field_name("target")?;
    (target.kind() == "simple_identifier").then(|| text(target, src))
}

/// `.body` written where a value is and not called, with the place that types
/// it: what `implicit_place` reads, the other side of `==` / `!=`, the target of
/// an assignment, the value `??` falls back from, or the subject of the
/// `switch` whose `case .body:` it is.
fn implicit_member_value(n: Node, src: &[u8], anc: &[Node]) -> Option<(String, Implicit)> {
    if crate::parse::current() != Some(crate::lang::Lang::Swift) {
        return None;
    }
    let parent = anc.last()?;
    // `case .body:`: the grammar keeps the name, and drops the dot, in a pattern
    if n.kind() == "simple_identifier" && parent.kind() == "pattern" {
        let entry = anc.iter().rev().nth(2).filter(|e| e.kind() == "switch_entry")?;
        let switch = anc.iter().rev().nth(3).filter(|s| s.kind() == "switch_statement")?;
        let _ = entry;
        let dot = src[..n.start_byte()].iter().rev().find(|b| !b.is_ascii_whitespace());
        if dot != Some(&b'.') || kids(*parent).len() != 1 {
            return None;
        }
        let subject = switch.child_by_field_name("expr")?;
        return Some((text(n, src), Implicit::Value(path_of(subject, src)?)));
    }
    if n.kind() != "prefix_expression" || src.get(n.start_byte()) != Some(&b'.') {
        return None;
    }
    let target = n.child_by_field_name("target").filter(|t| t.kind() == "simple_identifier")?;
    if parent.kind() == "call_expression" {
        return None; // `.status(301)`: `implicit_member_call`'s
    }
    let name = text(target, src);
    let other = |field: &str| parent.child_by_field_name(field).filter(|o| o.id() != n.id());
    let place = match parent.kind() {
        "equality_expression" => {
            let side = other("lhs").or_else(|| other("rhs"))?;
            Implicit::Value(path_of(side, src)?)
        }
        "assignment" => {
            let t = parent.child_by_field_name("target")?;
            let t = kids(t).into_iter().next().unwrap_or(t);
            Implicit::Value(path_of(t, src)?)
        }
        "nil_coalescing_expression" => Implicit::Value(path_of(other("value")?, src)?),
        _ => implicit_place(anc, src)?,
    };
    Some((name, place))
}

/// The place an implicit member stands in, which is what types it: an
/// argument of a call (`anc` ends `call_expression`, `call_suffix`,
/// `value_arguments`, `value_argument`), the value of a declaration with a
/// written type, or what a `return` gives.
fn implicit_place(anc: &[Node], src: &[u8]) -> Option<Implicit> {
    let parent = anc.last()?;
    match parent.kind() {
        "value_argument" => {
            let list = anc.get(anc.len().checked_sub(2)?)?;
            let call = anc.iter().rev().find(|a| a.kind() == "call_expression")?;
            let index = kids(*list)
                .into_iter()
                .filter(|c| c.kind() == "value_argument")
                .position(|c| c.id() == parent.id())?;
            let label = parent
                .child_by_field_name("name")
                .map(|l| text(l, src).trim_end_matches(':').trim().to_string())
                .filter(|l| !l.is_empty());
            Some(Implicit::Arg {
                call: u32::try_from(call.start_byte()).ok()?,
                index: u8::try_from(index).ok()?,
                label,
            })
        }
        "property_declaration" => written_type(*parent, src).map(Implicit::Typed),
        "control_transfer_statement" => Some(Implicit::Return),
        // `{ .status(1) }`: the one expression of a body is what it returns
        "statements" => {
            let body = anc.get(anc.len().checked_sub(2)?)?;
            (body.kind() == "function_body" && kids(*parent).len() == 1).then_some(Implicit::Return)
        }
        _ => None,
    }
}

/// The reads and writes in the arguments of a Rust macro invocation (see
/// `macro_refs`), at their bytes and lines in the file.
fn macro_state_uses(mac: Node, src: &[u8]) -> Vec<RefFact> {
    const PREFIX: &[u8] = b"fn __m(){";
    let Some(tt) = kids(mac).into_iter().find(|c| c.kind() == "token_tree") else {
        return Vec::new();
    };
    let tokens = &src[tt.start_byte()..tt.end_byte()];
    if !matches!(tokens.first(), Some(b'(' | b'[')) || tokens.len() > 20_000 {
        return Vec::new();
    }
    let wrapped = [PREFIX, tokens, b";}".as_slice()].concat();
    let Ok(tree) = crate::parse::parse(crate::lang::Lang::Rust, &wrapped, None) else {
        return Vec::new();
    };
    if tree.root_node().has_error() {
        return Vec::new();
    }
    let mut st = crate::state::State::new(false);
    let mut stack: Vec<(Node, usize)> = vec![(tree.root_node(), 0)];
    let mut anc: Vec<Node> = Vec::new();
    while let Some((n, depth)) = stack.pop() {
        anc.truncate(depth);
        st.visit(n, &wrapped, &anc);
        anc.push(n);
        for c in kids(n).into_iter().rev() {
            stack.push((c, depth + 1));
        }
    }
    let row = tt.start_position().row as u32;
    st.take_uses()
        .into_iter()
        .filter(|u| u.byte >= PREFIX.len())
        .map(|mut u| {
            u.byte = tt.start_byte() + u.byte - PREFIX.len();
            u.line += row;
            u
        })
        .collect()
}

/// The calls and constructions in the arguments of a Rust macro invocation
/// (`assert!(f(x))`, `vec![a(), b()]`, `format!("{}", c.name())`): the token
/// tree is parsed again as an expression, on its own, and what it holds is
/// placed where it stands in the file. Arguments that are no expressions (a
/// `{ … }` body, a pattern, `a => b` arms) parse with an error and are left
/// out, and so is the pattern after the first argument of `matches!`.
fn macro_refs(
    m: &dyn LanguageModule,
    mac: Node,
    src: &[u8],
    depth: usize,
) -> (Vec<RefFact>, Vec<Local>) {
    const PREFIX: &[u8] = b"fn __m(){";
    let none = || (Vec::new(), Vec::new());
    if depth >= 3 {
        return none();
    }
    let Some(tt) = kids(mac).into_iter().find(|c| c.kind() == "token_tree") else {
        return none();
    };
    let tokens = &src[tt.start_byte()..tt.end_byte()];
    if !matches!(tokens.first(), Some(b'(' | b'[')) || tokens.len() > 20_000 {
        return none();
    }
    let name = mac
        .child_by_field_name("macro")
        .map(|n| text(n, src))
        .unwrap_or_default()
        .rsplit("::")
        .next()
        .unwrap_or_default()
        .to_string();
    // `matches!(value, Pattern)`: only the value is an expression
    let tokens: Vec<u8> = if matches!(name.as_str(), "matches" | "assert_matches") {
        let (mut level, mut cut) = (0usize, None);
        for (i, b) in tokens.iter().enumerate() {
            match b {
                b'(' | b'[' | b'{' => level += 1,
                b')' | b']' | b'}' => level = level.saturating_sub(1),
                b',' if level == 1 => {
                    cut = Some(i);
                    break;
                }
                _ => {}
            }
        }
        match cut {
            Some(i) => [&tokens[..i], b")".as_slice()].concat(),
            None => tokens.to_vec(),
        }
    } else {
        tokens.to_vec()
    };
    let wrapped = [PREFIX, tokens.as_slice(), b";}".as_slice()].concat();
    let Ok(tree) = crate::parse::parse(crate::lang::Lang::Rust, &wrapped, None) else {
        return none();
    };
    if tree.root_node().has_error() {
        return none();
    }
    let mut scratch = FileFacts::new(String::new(), Some(crate::lang::Lang::Rust), false, &wrapped);
    read_at(m, tree.root_node(), &wrapped, &mut scratch, depth + 1);
    let row = tt.start_position().row as u32;
    let moved = |byte: usize| tt.start_byte() + byte.saturating_sub(PREFIX.len());
    // a closure parameter's `name@byte` names the closure where it is in the
    // file, as one read outside a macro does
    let renamed = |seg: &str| -> String {
        match seg.rsplit_once('@') {
            Some((name, at)) => match at.parse::<usize>() {
                Ok(b) => format!("{name}@{}", moved(b)),
                Err(_) => seg.to_string(),
            },
            None => seg.to_string(),
        }
    };
    // the names the arguments bind — a closure's typed parameters, a `let`
    // in its body — are locals of the definition (or the block) around
    let locals: Vec<Local> = scratch
        .locals
        .into_iter()
        .filter(|l| !l.field && l.byte >= PREFIX.len())
        .map(|mut l| {
            l.name = renamed(&l.name);
            l.path = l.path.iter().map(|s| renamed(s)).collect();
            l.byte = moved(l.byte);
            l.from_def = None;
            l
        })
        .collect();
    let refs = scratch
        .refs
        .into_iter()
        .filter(|r| matches!(r.kind, RefKind::Call | RefKind::New))
        .filter(|r| r.byte >= PREFIX.len())
        .filter(|r| r.implicit.is_none())
        .map(|mut r| {
            r.path = r.path.iter().map(|s| renamed(s)).collect();
            r.byte = moved(r.byte);
            r.line += row;
            r.from_def = None;
            r.recv = None;
            r
        })
        .collect();
    (refs, locals)
}

/// The value node of a declaration: `= …` of an assignment, a declarator, a
/// field or a property.
fn value_node<'t>(node: Node<'t>) -> Option<Node<'t>> {
    ["value", "right", "default_value", "initializer"]
        .iter()
        .find_map(|f| node.child_by_field_name(f))
        .or_else(|| {
            // C/C++, Go and Kotlin keep it one level down
            kids(node).into_iter().find_map(|c| {
                c.child_by_field_name("value")
                    .or_else(|| (c.kind() == "expression_list").then(|| c.named_child(0)).flatten())
            })
        })
        .or_else(|| {
            // Kotlin `val store = Repo()`: what follows the `=`, with no field
            if node.kind() != "property_declaration" {
                return None;
            }
            let mut cursor = node.walk();
            let all: Vec<Node> = node.children(&mut cursor).collect();
            let eq = all.iter().position(|c| c.kind() == "=")?;
            all.get(eq + 1).copied().filter(|c| c.is_named())
        })
        // Go `var store = &Repo{}`: the first of the values
        .map(|v| match v.kind() {
            "expression_list" => v.named_child(0).unwrap_or(v),
            _ => v,
        })
}

/// A short string literal a declaration holds.
fn state_value(node: Node, src: &[u8]) -> Option<String> {
    let v = value_node(node)?;
    if !(v.kind().contains("string") || v.kind() == "encapsed_string") {
        return None;
    }
    let s = string_value(v, src);
    (s.len() <= 200 && !s.contains('\n')).then_some(s)
}

/// The marker that ends a path naming an element of what comes before it.
pub const ELEMENT: &str = "[]";

/// The marker after a path that names a collection of what comes before it
/// (`xs.map { it.owner }` is `xs`, `[]`, `owner`, `[+]`).
pub const MANY: &str = "[+]";

/// The marker that ends a path naming what an iteration over what comes
/// before it gives: an element of a list, nothing known of a map (its
/// entries, or in Python its keys).
pub const ITERATED: &str = "[*]";

/// The marker that ends a path naming the second of the pair an iteration
/// gives: the value of a map's entry, the element beside an index
/// (`withIndex()`, `enumerate()`).
pub const VALUE: &str = "[v]";

/// The marker that ends a path naming the first of the pair an iteration
/// gives: a map's key, an element (`forEach((x, i) => …)`), nothing beside an
/// index.
pub const KEY: &str = "[k]";

/// The marker after a path that names a map from the elements of what
/// precedes it to what each gives (`xs.associateWith { it.owner }`).
pub const MANY_KEYED: &str = "[+=]";

/// A path segment: the wrapper the path named so far is opened (`await x`,
/// Rust `x?`). Nothing to a value that is no wrapper.
pub const OPENED: &str = "[?]";

/// Methods that give a collection of what their lambda gives for each element.
const MAPPING_METHODS: &[&str] = &[
    "map",
    "mapNotNull",
    "flatMap",
    "Select",
    "SelectMany",
    "collect",
    "flat_map",
    "filter_map",
    "compactMap",
    "mapTo",
    "mapIndexedNotNull",
];

/// The path of a collection made of what `each` gives per element of `of`,
/// `var` being the name the element goes by in `each`.
fn mapped(of: &[String], var: &str, each: Vec<String>) -> Option<Vec<String>> {
    if each.first().is_none_or(|h| h == UNKNOWN) || of.first().is_none_or(|h| h == UNKNOWN) {
        return None;
    }
    let mut out = if each[0] == var {
        // a member of the element
        let mut p = of.to_vec();
        p.push(ITERATED.into());
        p.extend(each.into_iter().skip(1));
        p
    } else {
        // something built or called per element: `Bar(it)`
        each
    };
    out.push(MANY.into());
    Some(out)
}

/// PHP's callable written as an array: `[$this, 'handle']` is the method
/// `handle` of the object, `[Foo::class, 'handle']` of `Foo`.
fn php_callable(a: Node, src: &[u8]) -> Option<Vec<String>> {
    let items: Vec<Node> =
        kids(a).into_iter().filter(|c| c.kind() == "array_element_initializer").collect();
    let [of, name] = items.as_slice() else { return None };
    let (of, name) = (kids(*of).into_iter().next()?, kids(*name).into_iter().next()?);
    if !matches!(name.kind(), "string" | "encapsed_string") {
        return None;
    }
    let name = string_value(name, src);
    let mut p = match of.kind() {
        "variable_name" if text(of, src) == "$this" => vec!["self".to_string()],
        "class_constant_access_expression" => {
            let ks = kids(of);
            (ks.len() == 2 && text(ks[1], src) == "class").then(|| path_of(ks[0], src)).flatten()?
        }
        _ => return None,
    };
    let plain = !name.is_empty() && name.chars().all(|c| c.is_alphanumeric() || c == '_');
    plain.then(|| {
        p.push(name);
        p
    })
}

/// A function given by reference: `Item::owner` (Kotlin, Java, Rust) and
/// `this::convert` name what it is; `::Item`, `Item::new` a construction;
/// Swift's `\.owner` a member of each element (an empty head, as Ruby's
/// `&:owner`).
fn referenced(f: Node, src: &[u8]) -> Option<Vec<String>> {
    match f.kind() {
        "callable_reference" | "method_reference" => {
            let ks: Vec<Node> = kids(f).into_iter().filter(|c| !is_comment(c)).collect();
            let (last, of) = ks.split_last()?;
            let name = text(*last, src);
            let mut p = Vec::new();
            for c in of {
                p.extend(path_of(*c, src)?);
            }
            if name != "new" {
                p.push(name);
            }
            (!p.is_empty()).then_some(p)
        }
        "key_path_expression" => {
            let names: Vec<String> = kids(f)
                .into_iter()
                .filter(|c| c.kind() == "navigation_suffix" || c.kind() == "simple_identifier")
                .filter_map(|c| path_of(c, src))
                .flatten()
                .collect();
            match names.as_slice() {
                [one] => Some(vec![String::new(), one.clone()]),
                _ => None,
            }
        }
        // Rust `map(Part::head)`
        "scoped_identifier" => path_of(f, src).filter(|p| p.len() >= 2),
        // Swift `\.owner`: the key path is the target of a navigation
        "navigation_expression"
            if f.child_by_field_name("target").is_some_and(|t| {
                t.kind() == "key_path_expression" && t.named_child_count() == 0
            }) =>
        {
            let suffix = f.child_by_field_name("suffix")?;
            let name = path_of(suffix, src)?;
            match name.as_slice() {
                [one] => Some(vec![String::new(), one.clone()]),
                _ => None,
            }
        }
        _ => None,
    }
}

/// What a lambda's body gives: its one expression, its last one where the
/// language makes that the value (Kotlin, Ruby, Rust, Scala), or what its
/// `return`s give when they agree.
fn lambda_result(body: Node, src: &[u8]) -> Option<Vec<String>> {
    if let Some(e) = single_expression(body) {
        return path_of(e, src);
    }
    let statements: Vec<Node> = kids(body).into_iter().filter(|c| !is_comment(c)).collect();
    let tail = statements.last().copied().filter(|t| {
        !t.kind().ends_with("declaration")
            && !t.kind().contains("return")
            && !matches!(
                t.kind(),
                "let_declaration" | "expression_statement" | "local_variable_declaration"
            )
            || (t.kind() == "expression_statement"
                && matches!(body.kind(), "statements" | "block_body" | "body_statement"))
    });
    // the `return`s of the body, not of a lambda inside it
    let mut returned: Vec<Vec<String>> = Vec::new();
    let mut stack: Vec<(Node, usize)> = statements.iter().map(|c| (*c, 0)).collect();
    while let Some((n, depth)) = stack.pop() {
        if is_closure(n.kind()) || depth > 6 {
            continue;
        }
        if let Some(v) = returned_value(n, src) {
            returned.push(path_of(v, src)?);
            continue;
        }
        stack.extend(kids(n).into_iter().map(|c| (c, depth + 1)));
    }
    match (returned.first(), tail) {
        (Some(first), _) => returned.iter().all(|r| r == first).then(|| first.clone()),
        (None, Some(t)) => {
            let t =
                if t.kind() == "expression_statement" { kids(t).into_iter().last()? } else { t };
            path_of(t, src)
        }
        _ => None,
    }
}

/// Whether `name` is a local a lambda's body declares, and what it is
/// assigned: `Some(Some(path))`, `Some(None)` for a local of no path, `None`
/// for a name the body does not declare.
fn lambda_local(body: Node, name: &str, src: &[u8]) -> Option<Option<Vec<String>>> {
    let mut stack: Vec<(Node, usize)> = kids(body).into_iter().map(|c| (c, 0)).collect();
    while let Some((n, depth)) = stack.pop() {
        if is_closure(n.kind()) || depth > 3 {
            continue;
        }
        let declares = matches!(
            n.kind(),
            "property_declaration"
                | "let_declaration"
                | "local_variable_declaration"
                | "variable_declaration"
                | "lexical_declaration"
                | "variable_declarator"
                | "short_var_declaration"
                | "assignment"
                | "init_declarator"
        );
        if declares {
            let named = ["name", "pattern", "left", "declarator"]
                .iter()
                .find_map(|f| n.child_by_field_name(f))
                .or_else(|| {
                    kids(n).into_iter().find(|c| {
                        matches!(c.kind(), "variable_declaration" | "variable_declarator")
                    })
                })
                .and_then(|x| bound_name(x, src));
            if named.as_deref() == Some(name) {
                let value = value_node(n).or_else(|| {
                    // Kotlin `val o = x.owner`: the value beside the declaration
                    kids(n).into_iter().rev().find(|c| {
                        !matches!(c.kind(), "variable_declaration" | "binding_pattern_kind")
                            && !c.kind().contains("modifier")
                    })
                });
                return Some(value.and_then(|v| path_of(v, src)));
            }
        }
        stack.extend(kids(n).into_iter().map(|c| (c, depth + 1)));
    }
    None
}

/// The one expression a lambda's body is, through the block that holds it.
fn single_expression(body: Node) -> Option<Node> {
    let mut cur = body;
    for _ in 0..6 {
        match cur.kind() {
            "statements" | "block" | "statement_block" | "block_body" | "body_statement"
            | "compound_statement" => {
                let ks: Vec<Node> = kids(cur).into_iter().filter(|c| !is_comment(c)).collect();
                match ks.as_slice() {
                    [one] => cur = *one,
                    _ => return None,
                }
            }
            "expression_statement" | "return_statement" | "return" => {
                cur = kids(cur).into_iter().last()?;
            }
            _ => return Some(cur),
        }
    }
    None
}

/// `xs.map { it.owner }`, `xs.map(x => x.owner)`, `xs.map(&:owner)`,
/// `xs.stream().map(x -> x.owner())`: the path of the collection it gives.
fn mapped_call(n: Node, src: &[u8], path: &[String]) -> Option<Vec<String>> {
    let (method, of) = path.split_last()?;
    if !of.is_empty() && matches!(method.as_str(), "fold" | "Aggregate" | "inject" | "reduce") {
        if let Some(init) = fold_value(n, src) {
            return Some(init);
        }
    }
    // `xs.associateWith { it.owner }`: a map from the elements to them
    if method == "associateWith" && !of.is_empty() {
        let mut p = match mapped_by(n, src, of) {
            Mapped::To(p) => p,
            _ => return None,
        };
        if p.last().is_some_and(|s| s == MANY) {
            p.pop();
            p.push(MANY_KEYED.into());
            return Some(p);
        }
        return None;
    }
    if of.is_empty() || !MAPPING_METHODS.contains(&method.as_str()) {
        return None;
    }
    match mapped_by(n, src, of) {
        // a function is given and what it gives has no name: the result is
        // a collection of something unknown, not of the same elements
        Mapped::Unnamed => Some(vec![UNKNOWN.into()]),
        Mapped::To(p) => Some(p),
        Mapped::NoFunction => None,
    }
}

enum Mapped {
    To(Vec<String>),
    Unnamed,
    NoFunction,
}

fn mapped_by(n: Node, src: &[u8], of: &[String]) -> Mapped {
    match mapped_path(n, src, of) {
        Ok(Some(p)) => Mapped::To(p),
        Ok(None) => Mapped::Unnamed,
        Err(()) => Mapped::NoFunction,
    }
}

/// `Err` when the call is given no function to map with (Java
/// `.collect(toList())`), `Ok(None)` when it is and its result has no path.
fn mapped_path(n: Node, src: &[u8], of: &[String]) -> Result<Option<Vec<String>>, ()> {
    // the one function given to it: an argument, a trailing lambda, a block
    let mut given: Vec<Node> = Vec::new();
    if let Some((list, _)) = argument_list(n) {
        for a in list.into_iter().flat_map(kids).filter(|c| !is_comment(c)) {
            given.push(if a.kind().contains("argument") && a.kind() != "block_argument" {
                kids(a).into_iter().last().unwrap_or(a)
            } else {
                a
            });
        }
    }
    if let Some(sfx) = kids(n).into_iter().find(|c| c.kind() == "call_suffix") {
        given.extend(kids(sfx).into_iter().filter_map(|c| match c.kind() {
            "annotated_lambda" => kids(c).into_iter().find(|c| c.kind() == "lambda_literal"),
            // Swift's trailing closure
            "lambda_literal" => Some(c),
            _ => None,
        }));
    }
    given.extend(n.child_by_field_name("block"));
    let [f] = given.as_slice() else { return Err(()) };
    // Ruby `&:owner`: the method of that name on each element
    if f.kind() == "block_argument" {
        let Some(sym) = kids(*f).into_iter().find(|c| c.kind() == "simple_symbol") else {
            return Ok(None);
        };
        let name = text(sym, src).trim_start_matches(':').to_string();
        return Ok(mapped(of, "", vec![String::new(), name]));
    }
    // `map(Item::owner)`, `map(::Item)`, `map(Item::new)`, Swift `map(\.owner)`
    if let Some(each) = referenced(*f, src) {
        return Ok(match each.first().map(String::as_str) {
            Some("") => mapped(of, "", each),
            _ => {
                let mut each = each;
                each.push(MANY.into());
                Some(each)
            }
        });
    }
    if !(is_closure(f.kind()) || f.kind() == "block") {
        return Err(());
    }
    let params = params_field(*f).or_else(|| f.child_by_field_name("parameter")).or_else(|| {
        kids(*f).into_iter().find(|c| {
            matches!(c.kind(), "lambda_parameters" | "block_parameters" | "closure_parameters")
        })
    });
    let names = match params.map(|ps| closure_params(ps, src)) {
        Some(names) if !names.is_empty() => names,
        _ => closure_params(*f, src),
    };
    // the first parameter is the element (`xs.map((x, i) => …)`)
    let var = match names.first() {
        Some((one, ..)) => one.clone(),
        // a lambda that names no parameter has `it` or `$0`
        None => match implicit_parameter(*f) {
            Some(it) => it.to_string(),
            None => return Ok(None),
        },
    };
    let body = f.child_by_field_name("body").or_else(|| {
        kids(*f).into_iter().rfind(|c| !c.kind().contains("parameter") && !is_comment(c))
    });
    let each = body
        .and_then(|b| lambda_result(b, src))
        // a local of the lambda: what it is assigned (`val o = it.owner; o`)
        .and_then(|each| match body.and_then(|b| lambda_local(b, &each[0], src)) {
            Some(Some(value)) => {
                let mut p = value;
                p.extend(each.into_iter().skip(1));
                Some(p)
            }
            Some(None) => None,
            None => Some(each),
        })
        // what another parameter gives has no name outside the lambda
        .filter(|each| !names.iter().skip(1).any(|(other, ..)| *other == each[0]));
    Ok(each.and_then(|each| mapped(of, &var, each)))
}

/// Names of collection types whose one type argument is their element's.
const COLLECTIONS: &[&str] = &[
    "List",
    "MutableList",
    "ArrayList",
    "LinkedList",
    "Set",
    "MutableSet",
    "HashSet",
    "LinkedHashSet",
    "TreeSet",
    "Collection",
    "MutableCollection",
    "Iterable",
    "Sequence",
    "Array",
    "IEnumerable",
    "IList",
    "ICollection",
    "IReadOnlyList",
    "IReadOnlyCollection",
    "Vec",
    "VecDeque",
    "Seq",
    "Queue",
    "Deque",
    "ArrayDeque",
    "Stack",
    "list",
    "set",
    "frozenset",
    "Iterator",
    "Flow",
    "Stream",
    "ReadonlyArray",
    "ImmutableList",
    "IAsyncEnumerable",
    "AsyncIterable",
    "Flux",
    "Slice",
    "ArraySlice",
    "NonEmptyList",
    "PersistentList",
    // C++'s standard sequences (`std::vector<Foo>`)
    "vector",
    "deque",
    "forward_list",
    "unordered_set",
    "multiset",
    "span",
    "queue",
    "priority_queue",
];

/// The element type a type position names, when it names a collection:
/// `List<Foo>`, `Foo[]`, `[]Foo`, `[Foo]`, `list[Foo]`.
fn elem_type(t: Node, src: &[u8]) -> Option<Vec<String>> {
    // an array or slice type, through a pointer, a reference or an annotation
    let mut stack = vec![(t, 0)];
    while let Some((n, depth)) = stack.pop() {
        if matches!(n.kind(), "array_type" | "slice_type" | "variadic_parameter") {
            let inner = n.child_by_field_name("element").or_else(|| kids(n).into_iter().next())?;
            return first_type(inner, src);
        }
        if depth < 3
            && matches!(
                n.kind(),
                "type_annotation"
                    | "pointer_type"
                    | "reference_type"
                    | "nullable_type"
                    | "optional_type"
                    | "type"
                    | "parenthesized_type"
            )
        {
            stack.extend(kids(n).into_iter().map(|c| (c, depth + 1)));
        }
    }
    let mut v = Vec::new();
    types_in(t, src, "", &mut v);
    // the outer type and its arguments, in whichever order the grammar
    // gives them
    let at = v.iter().position(|(_, ctx)| *ctx != "generic_arg").unwrap_or(0);
    if v.is_empty() {
        return None;
    }
    let (outer, _) = v.remove(at);
    let args: Vec<Vec<String>> = v.into_iter().map(|(p, _)| p).collect();
    if COLLECTIONS.contains(&outer.last()?.as_str()) && args.len() == 1 {
        return Some(args[0].clone());
    }
    map_value(t, src)
}

/// Names of map types whose second type argument is their values' type.
const MAPS: &[&str] = &[
    "Map",
    "MutableMap",
    "HashMap",
    "LinkedHashMap",
    "TreeMap",
    "SortedMap",
    "NavigableMap",
    "ConcurrentHashMap",
    "ConcurrentMap",
    "WeakHashMap",
    "EnumMap",
    "ImmutableMap",
    "PersistentMap",
    "Dictionary",
    "IDictionary",
    "IReadOnlyDictionary",
    "ConcurrentDictionary",
    "SortedDictionary",
    "ImmutableDictionary",
    "BTreeMap",
    "IndexMap",
    "dict",
    "Dict",
    "Mapping",
    "MutableMapping",
    "OrderedDict",
    "DefaultDict",
    "defaultdict",
    "Record",
    "ReadonlyMap",
    // C++'s (`std::map<K, V>`)
    "map",
    "unordered_map",
    "multimap",
];

/// The generic type a type position names, through annotations and
/// pointers: its name and the nodes of its type arguments, in order.
fn generic_args<'t>(t: Node<'t>, src: &[u8]) -> Option<(String, Vec<Node<'t>>)> {
    let mut stack = vec![(t, 0)];
    while let Some((n, depth)) = stack.pop() {
        let args = kids(n).into_iter().find(|c| {
            matches!(
                c.kind(),
                "type_arguments"
                    | "type_argument_list"
                    | "template_argument_list"
                    | "type_parameter"
            )
        });
        if let Some(args) = args {
            let name = path_of(n, src).and_then(|p| p.last().cloned())?;
            let given = kids(args).into_iter().filter(|c| !is_comment(c)).collect();
            return Some((name, given));
        }
        if depth < 3
            && matches!(
                n.kind(),
                "type_annotation"
                    | "nullable_type"
                    | "optional_type"
                    | "type"
                    | "pointer_type"
                    | "reference_type"
                    | "parenthesized_type"
                    | "user_type"
            )
        {
            stack.extend(kids(n).into_iter().map(|c| (c, depth + 1)));
        }
    }
    None
}

/// The type arguments a type position writes (`Response<User>`: `[["User"]]`),
/// unless it is a wrapper's, whose argument is the type itself.
fn targs_of(t: Node, src: &[u8]) -> Vec<Vec<String>> {
    match generic_args(t, src) {
        Some((name, given)) if !is_wrapper(&name, t) => {
            let args: Option<Vec<Vec<String>>> =
                given.into_iter().map(|a| first_type(a, src)).collect();
            args.unwrap_or_default()
        }
        _ => Vec::new(),
    }
}

/// The element type of each type argument a type position writes that is a
/// collection (`Call<List<Contributor>>`: `[Some(["Contributor"])]`).
fn targ_elems_of(t: Node, src: &[u8]) -> Vec<Option<Vec<String>>> {
    match generic_args(t, src) {
        Some((name, given)) if !is_wrapper(&name, t) => {
            given.into_iter().map(|a| elem_type(a, src)).collect()
        }
        _ => Vec::new(),
    }
}

/// `targ_elems_of` for what a declaration declares (see `written_targs`).
fn written_targ_elems(node: Node, src: &[u8]) -> Vec<Option<Vec<String>>> {
    let mut places = vec![node];
    places.extend(
        kids(node)
            .into_iter()
            .filter(|c| c.kind().ends_with("declaration") || c.kind().ends_with("declarator")),
    );
    places
        .into_iter()
        .map(|n| match n.child_by_field_name("type") {
            Some(t) => targ_elems_of(t, src),
            None => kids(n)
                .into_iter()
                .find(|c| matches!(c.kind(), "user_type" | "type_annotation" | "nullable_type"))
                .map(|t| targ_elems_of(t, src))
                .unwrap_or_default(),
        })
        .find(|a| !a.is_empty())
        .unwrap_or_default()
}

/// `targ_elems_of` for the type a callable declares it returns.
fn return_targ_elems(def: Node, src: &[u8]) -> Vec<Option<Vec<String>>> {
    let mut cursor = def.walk();
    for (i, child) in def.children(&mut cursor).enumerate() {
        let result = match def.field_name_for_child(i as u32) {
            Some("return_type") | Some("returns") => true,
            Some("result") => child.kind() != "parameter_list",
            Some("type") => !def.kind().contains("field") && !def.kind().contains("property"),
            Some(_) => false,
            None => {
                matches!(child.kind(), "user_type" | "nullable_type")
                    && def.kind() == "function_declaration"
            }
        };
        if result {
            return targ_elems_of(child, src);
        }
    }
    Vec::new()
}

/// A class's bases written with type arguments, from its heritage clauses:
/// Java `extends` / `implements`, Kotlin's delegation specifiers, C#'s base
/// list, TS `extends` / `implements`, Swift's inheritance specifiers.
fn base_targs(class: Node, src: &[u8]) -> Vec<(Vec<String>, Vec<Vec<String>>)> {
    let heritage = |k: &str| {
        matches!(
            k,
            "superclass"
                | "super_interfaces"
                | "extends_interfaces"
                | "delegation_specifier"
                | "delegation_specifiers"
                | "base_list"
                | "class_heritage"
                | "extends_clause"
                | "implements_clause"
                | "inheritance_specifier"
                | "type_list"
                | "constructor_invocation"
                | "interface_type_list"
        )
    };
    let mut out = Vec::new();
    let mut stack: Vec<(Node, usize)> =
        kids(class).into_iter().filter(|c| heritage(c.kind())).map(|c| (c, 0)).collect();
    while let Some((n, depth)) = stack.pop() {
        // TS `extends Repo<User>`: the name and its arguments side by side
        if n.kind() == "extends_clause" {
            let args = kids(n).into_iter().find(|c| c.kind() == "type_arguments");
            let value = n.child_by_field_name("value").and_then(|v| path_of(v, src));
            if let (Some(args), Some(name)) = (args, value) {
                let args: Option<Vec<Vec<String>>> = kids(args)
                    .into_iter()
                    .filter(|c| !is_comment(c))
                    .map(|a| first_type(a, src))
                    .collect();
                if let Some(args) = args.filter(|a| !a.is_empty()) {
                    out.push((name, args));
                }
                continue;
            }
        }
        let generic = matches!(n.kind(), "generic_type" | "generic_name")
            || (n.kind() == "user_type" && kids(n).iter().any(|c| c.kind() == "type_arguments"));
        if generic {
            let name = match n.kind() {
                "user_type" => path_of(n, src),
                _ => n
                    .child_by_field_name("name")
                    .or_else(|| n.child_by_field_name("type"))
                    .or_else(|| kids(n).into_iter().next())
                    .and_then(|c| path_of(c, src)),
            };
            let args = targs_of(n, src);
            if let (Some(name), false) = (name, args.is_empty()) {
                out.push((name, args));
            }
            continue;
        }
        if depth < 4 {
            stack.extend(kids(n).into_iter().map(|c| (c, depth + 1)));
        }
    }
    out
}

/// The type arguments a declaration writes on what it declares.
fn written_targs(node: Node, src: &[u8]) -> Vec<Vec<String>> {
    let mut places = vec![node];
    places.extend(
        kids(node)
            .into_iter()
            .filter(|c| c.kind().ends_with("declaration") || c.kind().ends_with("declarator")),
    );
    places
        .into_iter()
        .map(|n| match n.child_by_field_name("type") {
            Some(t) => targs_of(t, src),
            None => kids(n)
                .into_iter()
                .find(|c| matches!(c.kind(), "user_type" | "type_annotation" | "nullable_type"))
                .map(|t| targs_of(t, src))
                .unwrap_or_default(),
        })
        .find(|a| !a.is_empty())
        .unwrap_or_default()
}

/// The name of a member as a path segment: a tuple's numbered field (Rust
/// `self.0`, Swift `pair.1`) is its part (`[#0]`).
fn member_path(m: Node, src: &[u8]) -> Option<Vec<String>> {
    // (Swift's member sits in a `navigation_suffix`)
    let m = match m.kind() {
        "navigation_suffix" => kids(m).into_iter().last()?,
        _ => m,
    };
    if m.kind() == "integer_literal" {
        return text(m, src).parse().ok().map(|i| vec![tuple_part(i)]);
    }
    path_of(m, src)
}

/// The marker of the `i`th part of a tuple (`[#0]`, `[#1]`).
pub fn tuple_part(i: usize) -> String {
    format!("[#{i}]")
}

/// Which part of a tuple a path segment names, if it is a tuple part.
pub fn tuple_index(seg: &str) -> Option<usize> {
    seg.strip_prefix("[#")?.strip_suffix(']')?.parse().ok()
}

/// The parts of the tuple a type position names: `(A, B)` (Rust, Swift, C#,
/// TS `[A, B]`), `Pair<A, B>`, `Triple`, `tuple[A, B]`, `Tuple<A, B>`.
fn tuple_parts(t: Node, src: &[u8]) -> Vec<Vec<String>> {
    let mut stack = vec![(t, 0)];
    while let Some((n, depth)) = stack.pop() {
        if n.kind() == "tuple_type" {
            return kids(n)
                .into_iter()
                .filter(|c| !is_comment(c))
                .map(|c| {
                    let ty = c.child_by_field_name("type").unwrap_or(c);
                    first_type(ty, src).unwrap_or_default()
                })
                .collect();
        }
        if let Some((name, given)) = generic_args(n, src) {
            if matches!(name.as_str(), "Pair" | "Triple" | "tuple" | "Tuple" | "ValueTuple") {
                return given.into_iter().map(|a| first_type(a, src).unwrap_or_default()).collect();
            }
            return Vec::new();
        }
        if depth < 3 && matches!(n.kind(), "type_annotation" | "type" | "nullable_type") {
            stack.extend(kids(n).into_iter().map(|c| (c, depth + 1)));
        }
    }
    Vec::new()
}

/// The parts of the tuple a callable declares it returns (Go: its results).
fn return_tuple(def: Node, src: &[u8]) -> Vec<Vec<String>> {
    let mut cursor = def.walk();
    for (i, child) in def.children(&mut cursor).enumerate() {
        let result = match def.field_name_for_child(i as u32) {
            Some("return_type") | Some("returns") => true,
            Some("result") if child.kind() == "parameter_list" => {
                let parts: Vec<Vec<String>> = kids(child)
                    .into_iter()
                    .filter(|c| c.kind() == "parameter_declaration")
                    .map(|c| {
                        c.child_by_field_name("type")
                            .and_then(|t| first_type(t, src))
                            .unwrap_or_default()
                    })
                    .collect();
                return if parts.len() >= 2 { parts } else { Vec::new() };
            }
            Some("result") => true,
            Some("type") => !def.kind().contains("field") && !def.kind().contains("property"),
            // (Swift's grammar files the result type under `name`)
            Some("name") => child.kind() == "tuple_type",
            Some(_) => false,
            None => {
                matches!(child.kind(), "user_type" | "nullable_type" | "tuple_type")
                    && def.kind() == "function_declaration"
            }
        };
        if result {
            return tuple_parts(child, src);
        }
    }
    Vec::new()
}

/// A declaration that takes a tuple apart (`let (a, b) = f()`, `val (a, b) =
/// f()`, `var (a, b) = F()`, `a, b = f()`, `const [a, b] = f()`, Go `a, b :=
/// f()`): its names in order (`_` where none is bound) and its value.
fn destructured<'t>(n: Node<'t>, src: &[u8]) -> Option<(Vec<String>, Node<'t>)> {
    let one = |c: Node| -> String {
        let mut cur = c;
        for _ in 0..3 {
            if matches!(cur.kind(), "identifier" | "simple_identifier") {
                return text(cur, src);
            }
            let ks: Vec<Node> = kids(cur).into_iter().filter(|x| !is_comment(x)).collect();
            match ks.last() {
                Some(x)
                    if matches!(cur.kind(), "mut_pattern" | "variable_declaration" | "pattern") =>
                {
                    cur = *x
                }
                _ => break,
            }
        }
        "_".to_string()
    };
    let names_of = |p: Node| -> Option<Vec<String>> {
        if !matches!(
            p.kind(),
            "tuple_pattern"
                | "array_pattern"
                | "pattern_list"
                | "multi_variable_declaration"
                | "pattern"
                | "expression_list"
                | "parenthesized_variable_designation"
        ) {
            return None;
        }
        let names: Vec<String> = kids(p).into_iter().filter(|c| !is_comment(c)).map(one).collect();
        (names.len() >= 2).then_some(names)
    };
    let (pattern, value) = match n.kind() {
        "let_declaration" => (n.child_by_field_name("pattern")?, n.child_by_field_name("value")?),
        "assignment" | "short_var_declaration" => {
            let right = n.child_by_field_name("right")?;
            let right = if right.kind() == "expression_list" {
                match kids(right).as_slice() {
                    [one] => *one,
                    _ => return None,
                }
            } else {
                right
            };
            (n.child_by_field_name("left")?, right)
        }
        "variable_declarator" => {
            let p = n
                .child_by_field_name("name")
                .or_else(|| kids(n).into_iter().find(|c| c.kind() == "tuple_pattern"))?;
            let v = n.child_by_field_name("value").or_else(|| {
                kids(n).into_iter().find(|c| {
                    callee(*c, src).is_some()
                        || matches!(c.kind(), "await_expression" | "try_expression")
                })
            })?;
            (p, v)
        }
        "property_declaration" => {
            let p = n.child_by_field_name("name").or_else(|| {
                kids(n).into_iter().find(|c| c.kind() == "multi_variable_declaration")
            })?;
            let v = n
                .child_by_field_name("value")
                .or_else(|| kids(n).into_iter().rev().find(|c| callee(*c, src).is_some()))?;
            (p, v)
        }
        _ => return None,
    };
    Some((names_of(pattern)?, value))
}

/// The type arguments of the type a callable declares it returns.
fn return_targs(def: Node, src: &[u8]) -> Vec<Vec<String>> {
    let mut cursor = def.walk();
    for (i, child) in def.children(&mut cursor).enumerate() {
        let result = match def.field_name_for_child(i as u32) {
            Some("return_type") | Some("returns") => true,
            Some("result") => child.kind() != "parameter_list",
            Some("type") => !def.kind().contains("field") && !def.kind().contains("property"),
            Some(_) => false,
            None => {
                matches!(child.kind(), "user_type" | "nullable_type")
                    && def.kind() == "function_declaration"
            }
        };
        if result {
            return targs_of(child, src);
        }
    }
    Vec::new()
}

/// The type of the values of the map a type position names: `Map<K, V>`,
/// `dict[K, V]`, Go `map[K]V`, Swift `[K: V]`.
fn map_value(t: Node, src: &[u8]) -> Option<Vec<String>> {
    let mut stack = vec![(t, 0)];
    while let Some((n, depth)) = stack.pop() {
        match n.kind() {
            "map_type" => return n.child_by_field_name("value").and_then(|v| first_type(v, src)),
            "dictionary_type" => {
                let v = n.child_by_field_name("value").or_else(|| kids(n).get(1).copied())?;
                return first_type(v, src);
            }
            _ => {}
        }
        let args = kids(n).into_iter().find(|c| {
            matches!(
                c.kind(),
                "type_arguments"
                    | "type_argument_list"
                    | "template_argument_list"
                    | "type_parameter"
            )
        });
        if let Some(args) = args {
            let named = path_of(n, src).and_then(|p| p.last().cloned());
            if !named.is_some_and(|x| MAPS.contains(&x.as_str())) {
                return None;
            }
            let given: Vec<Node> = kids(args).into_iter().filter(|c| !is_comment(c)).collect();
            return match given.as_slice() {
                [_, v] => first_type(*v, src),
                _ => None,
            };
        }
        if depth < 3
            && matches!(
                n.kind(),
                "type_annotation"
                    | "nullable_type"
                    | "optional_type"
                    | "type"
                    | "pointer_type"
                    | "reference_type"
                    | "parenthesized_type"
                    | "generic_type"
                    | "user_type"
                    | "generic_name"
            )
        {
            stack.extend(kids(n).into_iter().map(|c| (c, depth + 1)));
        }
    }
    None
}

/// The element type of the collection a declaration or a parameter is
/// written as.
fn written_elem(node: Node, src: &[u8]) -> Option<Vec<String>> {
    let mut places = vec![node];
    places.extend(
        kids(node)
            .into_iter()
            .filter(|c| c.kind().ends_with("declaration") || c.kind().ends_with("declarator")),
    );
    places.into_iter().find_map(|n| {
        n.child_by_field_name("type").and_then(|t| elem_type(t, src)).or_else(|| {
            kids(n)
                .into_iter()
                .find(|c| {
                    matches!(
                        c.kind(),
                        "user_type" | "type_annotation" | "nullable_type" | "array_type"
                    )
                })
                .and_then(|t| elem_type(t, src))
        })
    })
}

/// The type of the keys of the map a type position names (see `map_value`).
fn map_key(t: Node, src: &[u8]) -> Option<Vec<String>> {
    let mut stack = vec![(t, 0)];
    while let Some((n, depth)) = stack.pop() {
        match n.kind() {
            "map_type" => return n.child_by_field_name("key").and_then(|k| first_type(k, src)),
            "dictionary_type" => {
                let k = n.child_by_field_name("key").or_else(|| kids(n).first().copied())?;
                return first_type(k, src);
            }
            _ => {}
        }
        if let Some((name, given)) = generic_args(n, src) {
            if !MAPS.contains(&name.as_str()) {
                return None;
            }
            return match given.as_slice() {
                [k, _] => first_type(*k, src),
                _ => None,
            };
        }
        if depth < 3
            && matches!(n.kind(), "type_annotation" | "nullable_type" | "optional_type" | "type")
        {
            stack.extend(kids(n).into_iter().map(|c| (c, depth + 1)));
        }
    }
    None
}

/// The key type of the map a declaration writes for what it declares.
fn written_key(node: Node, src: &[u8]) -> Option<Vec<String>> {
    let mut places = vec![node];
    places.extend(
        kids(node)
            .into_iter()
            .filter(|c| c.kind().ends_with("declaration") || c.kind().ends_with("declarator")),
    );
    places.into_iter().find_map(|n| match n.child_by_field_name("type") {
        Some(t) => map_key(t, src),
        None => kids(n)
            .into_iter()
            .find(|c| {
                matches!(
                    c.kind(),
                    "user_type" | "type_annotation" | "nullable_type" | "dictionary_type"
                )
            })
            .and_then(|t| map_key(t, src)),
    })
}

/// The key type of the map a callable declares it returns.
fn return_key(def: Node, src: &[u8]) -> Option<Vec<String>> {
    let mut cursor = def.walk();
    for (i, child) in def.children(&mut cursor).enumerate() {
        let result = match def.field_name_for_child(i as u32) {
            Some("return_type") | Some("returns") => true,
            Some("result") => child.kind() != "parameter_list",
            Some("type") => !def.kind().contains("field") && !def.kind().contains("property"),
            Some(_) => false,
            None => {
                matches!(child.kind(), "user_type" | "nullable_type")
                    && def.kind() == "function_declaration"
            }
        };
        if result {
            return map_key(child, src);
        }
    }
    None
}

/// Whether a declaration writes a map's type for what it declares.
fn written_keyed(node: Node, src: &[u8]) -> bool {
    let mut places = vec![node];
    places.extend(
        kids(node)
            .into_iter()
            .filter(|c| c.kind().ends_with("declaration") || c.kind().ends_with("declarator")),
    );
    places.into_iter().any(|n| match n.child_by_field_name("type") {
        Some(t) => map_value(t, src).is_some(),
        None => kids(n)
            .into_iter()
            .find(|c| {
                matches!(
                    c.kind(),
                    "user_type" | "type_annotation" | "nullable_type" | "dictionary_type"
                )
            })
            .is_some_and(|t| map_value(t, src).is_some()),
    })
}

/// Whether a callable declares it returns a map.
fn return_keyed(def: Node, src: &[u8]) -> bool {
    let mut cursor = def.walk();
    for (i, child) in def.children(&mut cursor).enumerate() {
        let result = match def.field_name_for_child(i as u32) {
            Some("return_type") | Some("returns") => true,
            Some("result") => {
                // Go `(map[string]*Foo, error)`: the first result
                if child.kind() == "parameter_list" {
                    return kids(child).into_iter().next().is_some_and(|f| written_keyed(f, src));
                }
                true
            }
            Some("type") => !def.kind().contains("field") && !def.kind().contains("property"),
            Some(_) => false,
            None => {
                matches!(child.kind(), "user_type" | "nullable_type")
                    && def.kind() == "function_declaration"
            }
        };
        if result {
            return map_value(child, src).is_some();
        }
    }
    false
}

/// Whether a declaration writes a sealed wrapper for what it declares
/// (`opt: Optional<Foo>`, `Arc<Mutex<Pool>> pool`): the places
/// `written_type` reads.
fn written_sealed(node: Node, src: &[u8]) -> bool {
    let mut places = vec![node];
    places.extend(
        kids(node)
            .into_iter()
            .filter(|c| c.kind().ends_with("declaration") || c.kind().ends_with("declarator")),
    );
    places.into_iter().any(|n| match n.child_by_field_name("type") {
        Some(t) => sealed_type(t, src),
        None => kids(n)
            .into_iter()
            .find(|c| matches!(c.kind(), "user_type" | "type_annotation" | "nullable_type"))
            .is_some_and(|t| sealed_type(t, src)),
    })
}

/// What a callable declares it returns, when that is a wrapper around a
/// type (`-> Option<Foo>`, `Promise<Foo>`, `Task<Foo>`, `Box<dyn Foo>`): the
/// type, and whether the wrapper holds it closed.
fn return_wraps(def: Node, src: &[u8]) -> Option<(Vec<String>, bool)> {
    let mut cursor = def.walk();
    for (i, child) in def.children(&mut cursor).enumerate() {
        let result = match def.field_name_for_child(i as u32) {
            Some("return_type") | Some("returns") => true,
            // (Go has no wrappers, and its results are a list)
            Some("result") => child.kind() != "parameter_list",
            Some("type") => !def.kind().contains("field") && !def.kind().contains("property"),
            Some(_) => false,
            None => {
                matches!(child.kind(), "user_type" | "nullable_type")
                    && def.kind() == "function_declaration"
            }
        };
        if result {
            if !wrapper_named(child, src) {
                return None;
            }
            return first_type(child, src).map(|inner| (inner, sealed_type(child, src)));
        }
    }
    None
}

/// The element type of the collection a callable declares it returns.
fn return_elem(def: Node, src: &[u8]) -> Option<Vec<String>> {
    let mut cursor = def.walk();
    for (i, child) in def.children(&mut cursor).enumerate() {
        let result = match def.field_name_for_child(i as u32) {
            Some("return_type") | Some("returns") => true,
            Some("result") => {
                // Go `([]Foo, error)`: the first result
                if child.kind() == "parameter_list" {
                    return kids(child).into_iter().next().and_then(|f| written_elem(f, src));
                }
                true
            }
            Some("type") => !def.kind().contains("field") && !def.kind().contains("property"),
            Some(_) => false,
            None => {
                matches!(child.kind(), "user_type" | "nullable_type")
                    && def.kind() == "function_declaration"
            }
        };
        if result {
            return elem_type(child, src);
        }
    }
    None
}

/// `xs[0]` as the value a declaration holds: an element of `xs`.
fn indexed(decl: Node, src: &[u8]) -> Option<Vec<String>> {
    fn subscript(v: Node) -> bool {
        matches!(
            v.kind(),
            "subscript_expression"
                | "index_expression"
                | "element_access_expression"
                | "indexing_expression"
                | "subscript"
                | "element_reference"
        )
    }
    let v = value_node(decl).filter(|v| subscript(*v)).or_else(|| {
        kids(decl).into_iter().find_map(|c| {
            subscript(c).then_some(c).or_else(|| kids(c).into_iter().find(|x| subscript(*x)))
        })
    })?;
    let object = ["object", "operand", "value", "expression", "array"]
        .iter()
        .find_map(|f| v.child_by_field_name(f))
        .or_else(|| kids(v).into_iter().next())?;
    let mut path = path_of(object, src).filter(|p| p[0] != UNKNOWN)?;
    path.push(ELEMENT.into());
    Some(path)
}

/// Methods whose lambda takes an element of the receiver first.
const ITERATION_METHODS: &[&str] = &[
    "forEach",
    "map",
    "filter",
    "any",
    "all",
    "none",
    "find",
    "first",
    "last",
    "firstOrNull",
    "lastOrNull",
    "single",
    "singleOrNull",
    "count",
    "sumOf",
    "maxBy",
    "minBy",
    "maxByOrNull",
    "minByOrNull",
    "sortedBy",
    "sortedByDescending",
    "groupBy",
    "associateBy",
    "associate",
    "associateWith",
    "flatMap",
    "mapNotNull",
    "filterNot",
    "takeWhile",
    "dropWhile",
    "partition",
    "onEach",
    "each",
    "select",
    "reject",
    "collect",
    "detect",
    "find_all",
    "filter_map",
    "each_with_object",
    "for_each",
    "Where",
    "Select",
    "Any",
    "All",
    "First",
    "FirstOrDefault",
    "Single",
    "SingleOrDefault",
    "OrderBy",
    "OrderByDescending",
    "ForEach",
    "some",
    "every",
    "findIndex",
    "findLast",
    "compactMap",
    "allSatisfy",
    "contains",
    "removeAll",
    "joinToString",
    "distinctBy",
    "indexOfFirst",
    "sort_by",
    "group_by",
    "flat_map",
    "min_by",
    "max_by",
    "for_each",
    "ForEach",
    "each",
    "each_with_index",
    "onEach",
    "mapNotNull",
    "flatMap",
    "filterNot",
    "sortedBy",
    "sortedByDescending",
    "groupBy",
    "associateBy",
    "associateWith",
    "associate",
    "partition",
    "takeWhile",
    "dropWhile",
    "indexOfFirst",
    "firstNotNullOfOrNull",
    "some",
    "every",
    "findLast",
    "findIndex",
    "Where",
    "Select",
    "SelectMany",
    "Any",
    "All",
    "First",
    "FirstOrDefault",
    "Single",
    "SingleOrDefault",
    "Last",
    "LastOrDefault",
    "Count",
    "OrderBy",
    "OrderByDescending",
    "GroupBy",
    "ToDictionary",
    "RemoveAll",
    "Find",
    "FindAll",
    "Exists",
    "select",
    "reject",
    "detect",
    "find_all",
    "collect",
    "flat_map",
    "filter_map",
    "sort_by",
    "group_by",
    "min_by_key",
    "max_by_key",
    "sort_by_key",
    "position",
    "take_while",
    "skip_while",
    "inspect",
    "retain",
    "compactMap",
    "contains",
    "allSatisfy",
    "anyMatch",
    "allMatch",
    "noneMatch",
    "removeIf",
    "peek",
    "ifPresent",
    "sumBy",
    "maxOf",
    "minOf",
    "maxByOrNull",
    "minByOrNull",
];

/// Methods whose lambda takes an index, then an element of the receiver.
const INDEXED_METHODS: &[&str] =
    &["forEachIndexed", "mapIndexed", "mapIndexedNotNull", "filterIndexed", "onEachIndexed"];

/// Methods whose lambda takes an accumulator, then an element.
const FOLDING_METHODS: &[&str] = &["fold", "Aggregate", "inject", "reduce", "reduceRight"];

/// `xs.fold(Acc()) { … }`, C# `Aggregate(seed, …)`, Ruby `inject(init)`, JS
/// `reduce(f, init)`: the path of the initial value, which the result and
/// the accumulator are.
fn fold_init(anc: &[Node], _lambda: Node, src: &[u8]) -> Option<Vec<String>> {
    let call = anc.iter().rev().take(5).find(|a| is_call_kind(a.kind()))?;
    fold_value(*call, src)
}

fn fold_value(call: Node, src: &[u8]) -> Option<Vec<String>> {
    let (_, path) = callee(call, src)?;
    let method = path.last()?;
    let (list, _) = argument_list(call)?;
    let args: Vec<Node> = kids(list?)
        .into_iter()
        .filter(|c| !is_comment(c))
        .map(|a| {
            if a.kind().contains("argument") {
                kids(a).into_iter().last().unwrap_or(a)
            } else {
                a
            }
        })
        .collect();
    let init = match (method.as_str(), args.as_slice()) {
        ("fold" | "Aggregate" | "inject", [init, ..]) if !is_closure(init.kind()) => *init,
        // JS `xs.reduce((acc, x) => …, init)`
        ("reduce", [f, init]) if is_closure(f.kind()) => *init,
        _ => return None,
    };
    value_type(init, src).or_else(|| path_of(init, src)).filter(|p| p[0] != UNKNOWN)
}

/// Methods whose lambda takes the receiver itself.
const SELF_METHODS: &[&str] = &["let", "also", "takeIf", "takeUnless", "tap", "then"];

/// The name of the one parameter a lambda that declares none has: Kotlin
/// `it`, Swift `$0` (read as `0`).
fn implicit_parameter(n: Node) -> Option<&'static str> {
    if n.kind() != "lambda_literal"
        || n.child_by_field_name("type").is_some()
        || kids(n).iter().any(|c| c.kind() == "lambda_parameters")
    {
        return None;
    }
    // the two grammars share the node's kind
    let swift = n.language().name().is_some_and(|l| l == "swift");
    Some(if swift { "0" } else { "it" })
}

/// The call a lambda is given to: which of its parameters is the element,
/// the path of the receiver, and whether that parameter is an element of
/// it (else the receiver itself, `x.let { … }`).
fn iterated_on(anc: &[Node], n: Node, src: &[u8]) -> Option<(usize, Vec<String>, bool)> {
    let lambda_start = n.start_byte();
    let call = anc.iter().rev().take(5).find(|a| is_call_kind(a.kind()))?;
    let (_, path) = callee(*call, src)?;
    // given to the call, not the receiver the call is made on
    let receiver_end = ["function", "object", "receiver"]
        .iter()
        .find_map(|f| call.child_by_field_name(f))
        .or_else(|| kids(*call).into_iter().next())
        .map_or(0, |r| r.end_byte());
    if path.len() < 2 || path[0] == UNKNOWN || lambda_start < receiver_end {
        return None;
    }
    let (method, receiver) = path.split_last()?;
    let mut of = receiver.to_vec();
    let of = std::mem::take(&mut of);
    if ITERATION_METHODS.contains(&method.as_str()) {
        Some((0, of, true))
    } else if INDEXED_METHODS.contains(&method.as_str())
        || FOLDING_METHODS.contains(&method.as_str())
    {
        Some((1, of, true))
    } else if SELF_METHODS.contains(&method.as_str()) {
        Some((0, of, false))
    } else {
        None
    }
}

/// `xs.zip(ys)` (Kotlin, Rust, Swift) or Python `zip(xs, ys)`: the paths of
/// the two collections zipped.
fn zipped(n: Node, src: &[u8]) -> Option<(Vec<String>, Vec<String>)> {
    if !is_call_kind(n.kind()) {
        return None;
    }
    let (_, path) = callee(n, src)?;
    let (method, of) = path.split_last()?;
    if method != "zip" {
        return None;
    }
    let (list, _) = argument_list(n)?;
    let args: Vec<Node> = kids(list?)
        .into_iter()
        .filter(|c| !is_comment(c))
        .map(|a| {
            if a.kind().contains("argument") {
                kids(a).into_iter().last().unwrap_or(a)
            } else {
                a
            }
        })
        .collect();
    let named = |a: &Node| path_of(*a, src).filter(|p| p[0] != UNKNOWN);
    match (of.is_empty(), args.as_slice()) {
        (false, [other]) => Some((of.to_vec(), named(other)?)),
        (true, [a, b]) => Some((named(a)?, named(b)?)),
        _ => None,
    }
}

/// Whether a lambda's one parameter is a pair taken apart: Kotlin `{ (k, v)
/// -> … }`, JS `([k, v]) => …`, Rust `|(k, v)|`.
fn destructures_pair(n: Node) -> bool {
    let first = match n.kind() {
        "lambda_literal" => kids(n)
            .into_iter()
            .find(|c| c.kind() == "lambda_parameters")
            .and_then(|ps| kids(ps).into_iter().next()),
        _ => ["parameters", "parameter"].iter().find_map(|f| n.child_by_field_name(f)).and_then(
            |ps| {
                if matches!(ps.kind(), "array_pattern" | "tuple_pattern") {
                    Some(ps)
                } else {
                    kids(ps).into_iter().next()
                }
            },
        ),
    };
    first.is_some_and(|p| {
        matches!(p.kind(), "multi_variable_declaration" | "array_pattern" | "tuple_pattern")
            && kids(p).len() == 2
    })
}

/// The two names of a pair taken apart (`(k, v)`, `[k, v]`, `k, v`), when
/// each is one name.
fn pair_names(var: Node, src: &[u8]) -> Option<(String, String)> {
    if !matches!(
        var.kind(),
        "multi_variable_declaration"
            | "tuple_pattern"
            | "pattern_list"
            | "array_pattern"
            | "pattern"
            | "parenthesized_variable_designation"
    ) {
        return None;
    }
    let one = |c: Node| -> Option<String> {
        match c.kind() {
            "identifier" | "simple_identifier" => bound_name(c, src),
            "variable_declaration" | "pattern" => match kids(c).as_slice() {
                [x] if matches!(x.kind(), "identifier" | "simple_identifier") => {
                    bound_name(*x, src)
                }
                _ => None,
            },
            _ => None,
        }
    };
    match kids(var).into_iter().filter(|c| !is_comment(c)).collect::<Vec<_>>().as_slice() {
        [a, b] => Some((one(*a)?, one(*b)?)),
        _ => None,
    }
}

/// The variables of a loop over a collection, and the paths of what they
/// are: one name is what the iteration gives (`[*]`), two taken apart are a
/// key or index and the value beside it (`[v]`), Go's `range` its own pair.
fn loop_variable(n: Node, src: &[u8]) -> Vec<(String, Vec<String>)> {
    loop_vars(n, src).unwrap_or_default()
}

fn loop_vars(n: Node, src: &[u8]) -> Option<Vec<(String, Vec<String>)>> {
    let k = n.kind();
    let (var, iterable) = match k {
        // Go `for _, x := range xs`: the second name is the element
        "range_clause" => {
            let left = kids(n.child_by_field_name("left")?);
            (*left.get(1)?, n.child_by_field_name("right")?)
        }
        "for_statement"
        | "enhanced_for_statement"
        | "foreach_statement"
        | "for_in_statement"
        | "for_expression"
        | "for_in_clause"
        | "for_range_loop"
        | "for" => {
            // a type written for the variable types it already
            if n.child_by_field_name("type").is_some_and(|t| first_type(t, src).is_some()) {
                return None;
            }
            let var = ["left", "name", "pattern", "item", "declarator"]
                .iter()
                .find_map(|f| n.child_by_field_name(f))
                .or_else(|| {
                    kids(n).into_iter().find(|c| {
                        matches!(c.kind(), "variable_declaration" | "multi_variable_declaration")
                    })
                })?;
            let iterable = ["right", "value", "collection"]
                .iter()
                .find_map(|f| n.child_by_field_name(f))
                .or_else(|| {
                    // Kotlin: the expression after the variable
                    let ks = kids(n);
                    let at = ks.iter().position(|c| c.id() == var.id())?;
                    ks.get(at + 1).copied()
                })?;
            // JS `for (k in obj)` names keys, not elements
            let mut cursor = n.walk();
            if k == "for_in_statement" && !n.children(&mut cursor).any(|c| c.kind() == "of") {
                return None;
            }
            (var, iterable)
        }
        _ => return None,
    };
    // Go's `range` gives the pair itself: the second name is the element (of
    // a slice) or the value (of a map)
    if k == "range_clause" {
        let name = bound_name(var, src).filter(|n| n != "_")?;
        let mut path = path_of(iterable, src).filter(|p| p[0] != UNKNOWN)?;
        path.push(ELEMENT.into());
        return Some(vec![(name, path)]);
    }
    if let Some((key, value)) = pair_names(var, src) {
        // `for ((a, b) in xs.zip(ys))`, Python `zip(xs, ys)`: one of each
        if let Some((left, right)) = zipped(iterable, src) {
            let mut out = Vec::new();
            for (name, mut of) in [(key, left), (value, right)] {
                of.push(ITERATED.into());
                out.push((name, of));
            }
            out.retain(|(n, _)| n != "_");
            return Some(out);
        }
        let of = path_of(iterable, src).filter(|p| p[0] != UNKNOWN)?;
        let mut first = of.clone();
        first.push(KEY.into());
        let mut path = of;
        path.push(VALUE.into());
        let out = vec![(key, first), (value, path)];
        return Some(out.into_iter().filter(|(n, _)| n != "_").collect());
    }
    // one name, not a pattern of several
    let name = match var.kind() {
        "identifier" | "simple_identifier" | "variable_name" => bound_name(var, src)?,
        // C++ `auto& r`, `auto* r`
        "reference_declarator" | "pointer_declarator" => {
            let inner = kids(var).into_iter().find(|c| c.kind() == "identifier")?;
            bound_name(inner, src)?
        }
        "variable_declaration" | "pattern" | "value_binding_pattern" => {
            let ks = kids(var);
            let ids: Vec<&Node> = ks
                .iter()
                .filter(|c| matches!(c.kind(), "identifier" | "simple_identifier"))
                .collect();
            match ids.as_slice() {
                [one] if ks.len() == 1 || var.kind() == "variable_declaration" => {
                    bound_name(**one, src)?
                }
                _ => return None,
            }
        }
        _ => return None,
    };
    if name == "_" {
        return None;
    }
    let mut path = path_of(iterable, src).filter(|p| p[0] != UNKNOWN)?;
    path.push(ITERATED.into());
    Some(vec![(name, path)])
}

/// The type a declaration writes for what it declares (`val x: Foo`, `Foo x`,
/// `x: Foo = …`): on the declaration or on the declarator one level down.
fn written_type(node: Node, src: &[u8]) -> Option<Vec<String>> {
    let mut places = vec![node];
    places.extend(
        kids(node)
            .into_iter()
            .filter(|c| c.kind().ends_with("declaration") || c.kind().ends_with("declarator")),
    );
    places.into_iter().find_map(|n| {
        let field = n.child_by_field_name("type");
        field.and_then(|t| first_type(t, src).or_else(|| predefined(t, src))).or_else(|| {
            kids(n)
                .into_iter()
                .find(|c| matches!(c.kind(), "user_type" | "type_annotation" | "nullable_type"))
                .and_then(|t| first_type(t, src))
        })
    })
}

/// The first name of the outer type a declaration writes (see
/// `Def::written_head`).
fn written_head(node: Node, src: &[u8]) -> Option<String> {
    let t = std::iter::once(node)
        .chain(
            kids(node)
                .into_iter()
                .filter(|c| c.kind().ends_with("declaration") || c.kind().ends_with("declarator")),
        )
        .find_map(|n| n.child_by_field_name("type"))?;
    // (as written: the readers of types drop a C++ namespace)
    let written = text(t, src);
    let head =
        written.split(|c: char| !(c.is_alphanumeric() || c == '_')).find(|s| !s.is_empty())?;
    Some(head.to_string())
}

/// A type of the language itself, as written (C# `string`, `string?`): no
/// type of the project, but what an extension can be written for.
fn predefined(t: Node, src: &[u8]) -> Option<Vec<String>> {
    let t = if t.kind() == "nullable_type" { kids(t).into_iter().next()? } else { t };
    (t.kind() == "predefined_type").then(|| vec![text(t, src)])
}

/// What constructs the value a declaration holds (`Settings()`).
/// The call a declaration's value is, and its arguments: `mongoose.model`,
/// `['Article']` of `const Article = mongoose.model('Article')`.
fn value_call<'t>(node: Node<'t>) -> Option<(Node<'t>, Vec<Node<'t>>)> {
    let v = value_node(node)?;
    if v.kind() != "call_expression" {
        return None;
    }
    let args = v.child_by_field_name("arguments")?;
    Some((
        v.child_by_field_name("function")?,
        kids(args).into_iter().filter(|c| !is_comment(c)).collect(),
    ))
}

/// JS `var req = Object.create(http.IncomingMessage.prototype)`: the path of
/// what the object is created from (see `Def::created_from`).
fn created_from(node: Node, src: &[u8]) -> Option<Vec<String>> {
    let (callee, args) = value_call(node)?;
    (path_of(callee, src)? == ["Object", "create"]).then_some(())?;
    path_of(*args.first()?, src)
}

/// JS `const Article = mongoose.model('Article')`: the model's name (see
/// `Def::model_name`).
fn model_named(node: Node, src: &[u8]) -> Option<String> {
    let (callee, args) = value_call(node)?;
    let path = path_of(callee, src)?;
    if !(path == ["mongoose", "model"] || path == ["model"]) || args.len() != 1 {
        return None;
    }
    string_arg(args[0], src)
}

/// The text of a string literal argument, without its quotes.
fn string_arg(n: Node, src: &[u8]) -> Option<String> {
    if !matches!(n.kind(), "string" | "template_string") {
        return None;
    }
    let t = text(n, src);
    let t = t.trim_matches(|c| c == '\'' || c == '"' || c == '`');
    (!t.contains("${")).then(|| t.to_string())
}

/// JS: what a function expression `this` belongs to is attached to (see
/// `RefFact::this_of`), from the ancestors of the `this` node; `None` inside
/// a definition (whose object `Def::object` says) or an arrow function's
/// own `this` (it has none: the one around).
fn this_attached(anc: &[Node], src: &[u8]) -> Option<Vec<String>> {
    for (i, a) in anc.iter().enumerate().rev() {
        match a.kind() {
            "arrow_function" => continue,
            "function_expression" | "function" | "generator_function" => {
                let parent = anc.get(i.checked_sub(1)?)?;
                return match parent.kind() {
                    // `app[method] = function () {…}`
                    "assignment_expression" => {
                        let left = parent.child_by_field_name("left")?;
                        (left.kind() == "subscript_expression")
                            .then(|| path_of(left.child_by_field_name("object")?, src))
                            .flatten()
                    }
                    // `UserSchema.path('name').validate(function () {…})`
                    "arguments" => {
                        let call = anc.get(i.checked_sub(2)?)?;
                        (call.kind() == "call_expression")
                            .then(|| path_of(call.child_by_field_name("function")?, src))
                            .flatten()
                    }
                    _ => None,
                };
            }
            "function_declaration"
            | "method_definition"
            | "class_body"
            | "generator_function_declaration" => return None,
            _ => {}
        }
    }
    None
}

fn state_value_path(node: Node, src: &[u8]) -> Option<Vec<String>> {
    let mut v = value_node(node)?;
    // `var app = module.exports = express()`: what the last one assigns
    while v.kind() == "assignment_expression" {
        v = v.child_by_field_name("right")?;
    }
    // Ruby `Store = Repo`, `Store = Shop::Repo`: another name for the class
    if matches!(v.kind(), "constant" | "scope_resolution") {
        return path_of(v, src);
    }
    value_type(v, src)
}

/// C#: how many type arguments the type named `name` is written with in
/// `n` (the first place it is written there): `Some(0)` for the bare name.
fn written_arity(n: Node, name: &str, src: &[u8]) -> Option<u8> {
    let mut stack = vec![n];
    let mut seen = 0usize;
    while let Some(x) = stack.pop() {
        seen += 1;
        if seen > 600 {
            return None;
        }
        match x.kind() {
            "generic_name" => {
                let ks = kids(x);
                if ks.first().is_some_and(|c| text(*c, src) == name) {
                    let args = ks.iter().find(|c| c.kind() == "type_argument_list")?;
                    return u8::try_from(kids(*args).len()).ok();
                }
            }
            "identifier" if text(x, src) == name => return Some(0),
            _ => {}
        }
        stack.extend(kids(x).into_iter().rev());
    }
    None
}

/// The parameter list of a declaration. (Scala gives a generic method's type
/// parameters the same field name: `def opt[B <: Base](b: B)`.)
fn params_field<'t>(n: Node<'t>) -> Option<Node<'t>> {
    let mut c = n.walk();
    if !c.goto_first_child() {
        return None;
    }
    loop {
        if c.field_name() == Some("parameters") && !c.node().kind().starts_with("type_parameter") {
            return Some(c.node());
        }
        if !c.goto_next_sibling() {
            return None;
        }
    }
}

/// Whether a node of this kind is a scope of its own for what is declared
/// in it: a block, a loop, a `catch`, a closure, one arm of a match.
pub(crate) fn is_scope(kind: &str) -> bool {
    kind.ends_with("block")
        || kind.ends_with("_body")
        // (`for_statement`, `for_in_statement`, `foreach_statement`,
        // `enhanced_for_statement`: not `formal_parameters`)
        || kind.starts_with("for_")
        || kind.starts_with("foreach")
        || kind.contains("_for_")
        || kind.contains("catch")
        || is_closure(kind)
        || matches!(
            kind,
            "compound_statement"
                | "statements"
                | "while_statement"
                | "using_statement"
                | "match_arm"
                | "case_clause"
                // (Go: each clause of a `switch` or `select` is a block)
                | "expression_case"
                | "default_case"
                | "type_case"
                | "communication_case"
                | "switch_entry"
                | "when_entry"
        )
}

fn inside_callable(defs: &[Def], byte: usize) -> bool {
    defs.iter().any(|d| d.kind.is_callable() && d.start_byte <= byte && byte < d.end_byte)
}

/// For each byte offset, the innermost definition whose range holds it.
pub(crate) fn innermost(defs: &[Def], bytes: &[usize]) -> Vec<Option<usize>> {
    innermost_where(defs, bytes, |_| true)
}

/// The definition a use at each byte belongs to: the innermost one, but a
/// field first assigned in a method (`self.x = make()`, an extra definition
/// whose parent is the class) gives way to that method, which is what calls
/// `make()`. A field or constant declared outside any callable keeps its uses.
pub(crate) fn homes(defs: &[Def], bytes: &[usize]) -> Vec<Option<usize>> {
    // (a variable the grammar took for a declaration is home to nothing)
    if !defs.iter().any(|d| d.extra) {
        return innermost_where(defs, bytes, |d| !d.misread);
    }
    let all = innermost_where(defs, bytes, |d| !d.misread);
    let plain = innermost_where(defs, bytes, |d| !d.extra && !d.misread);
    all.into_iter()
        .zip(plain)
        .map(|(a, p)| match (a, p) {
            (Some(a), Some(p)) if defs[a].extra && defs[p].kind.is_callable() => Some(p),
            (a, _) => a,
        })
        .collect()
}

fn innermost_where(
    defs: &[Def],
    bytes: &[usize],
    keep: impl Fn(&Def) -> bool,
) -> Vec<Option<usize>> {
    let mut by_start: Vec<usize> = (0..defs.len()).filter(|&i| keep(&defs[i])).collect();
    by_start.sort_by_key(|&i| (defs[i].start_byte, std::cmp::Reverse(defs[i].end_byte)));
    let mut order: Vec<usize> = (0..bytes.len()).collect();
    order.sort_by_key(|&i| bytes[i]);
    let mut out = vec![None; bytes.len()];
    let mut open: Vec<usize> = Vec::new();
    let mut next = 0;
    for i in order {
        let b = bytes[i];
        while next < by_start.len() && defs[by_start[next]].start_byte <= b {
            let d = by_start[next];
            while open.last().is_some_and(|&o| defs[o].end_byte <= defs[d].start_byte) {
                open.pop();
            }
            open.push(d);
            next += 1;
        }
        while open.last().is_some_and(|&o| defs[o].end_byte <= b) {
            open.pop();
        }
        out[i] = open.last().copied();
    }
    out
}

/// A C# property written with a body: an accessor with a block or an
/// expression, or `=> expr` for the property itself. (`{ get; set; }` has
/// none: what initializes it is the type's use.)
fn computed_property(node: Node) -> bool {
    if node.kind() != "property_declaration"
        || crate::parse::current() != Some(crate::lang::Lang::CSharp)
    {
        return false;
    }
    kids(node).into_iter().any(|c| match c.kind() {
        "arrow_expression_clause" => true,
        "accessor_list" => kids(c).into_iter().any(|a| {
            kids(a).into_iter().any(|b| matches!(b.kind(), "block" | "arrow_expression_clause"))
        }),
        _ => false,
    })
}

/// The closures no definition holds, as definitions of their own (see
/// `Def::block`): a test file's `it('…', () => { … })` has locals like a
/// function's, and each such callback its own. One inside another has it as
/// its parent, so the inner one sees the outer one's locals; so has a
/// definition written in one (`const helper = () => …` in a `describe`).
///
/// `invoked`: the closures called where they are written as the value of a
/// declaration (`var Dataset = (function () { function Dataset() {} return
/// Dataset })()`). The definition of the declared name written in one is
/// what the declaration names (`Def::returned_out`: the scope of the
/// declaration knows it too), and the (name, first byte) of each such
/// declaration is returned, which is then no local.
fn add_blocks(
    defs: &mut Vec<Def>,
    mut blocks: Vec<(usize, usize, u32, u32, usize)>,
    invoked: &HashMap<usize, (String, usize)>,
) -> Vec<(String, usize)> {
    if blocks.is_empty() {
        return Vec::new();
    }
    blocks.sort_by_key(|b| (b.0, std::cmp::Reverse(b.1)));
    let starts: Vec<usize> = blocks.iter().map(|b| b.0).collect();
    let inside = innermost_where(defs, &starts, |d| !d.misread);
    // the blocks open around the one being added: (index in `defs`, end)
    let mut open: Vec<(usize, usize)> = Vec::new();
    for (b, held) in blocks.into_iter().zip(inside) {
        if held.is_some() {
            continue;
        }
        let (start, end, first, last, node_id) = b;
        while open.last().is_some_and(|&(_, e)| e <= start) {
            open.pop();
        }
        let parent = open.last().map(|&(i, _)| i);
        defs.push(Def {
            name: String::new(),
            qualname: String::new(),
            kind: crate::facts::DefKind::Other,
            region_start: first,
            def_line: first,
            end_line: last,
            parent,
            owner: None,
            is_test: false,
            object: None,
            extra: false,
            decl_only: false,
            container: false,
            table: None,
            value: None,
            value_path: None,
            declared_type: None,
            written_head: None,
            alias_args: Vec::new(),
            wraps: None,
            keyed: false,
            key: None,
            returned_parts: Vec::new(),
            returned_members: Vec::new(),
            scope_lines: None,
            targs: Vec::new(),
            targ_elems: Vec::new(),
            base_targs: Vec::new(),
            rets: Vec::new(),
            part_sealed: Vec::new(),
            sealed: false,
            elem: None,
            receiver: None,
            anonymous: false,
            misread: false,
            block: true,
            returned_out: false,
            computed: false,
            unspanned: false,
            inline_body: None,
            created_from: None,
            model_name: None,
            params: None,
            plist: Vec::new(),
            returns: Vec::new(),
            lambda_receiver: None,
            self_params: Vec::new(),
            attrs: Vec::new(),
            type_params: Vec::new(),
            type_bounds: Vec::new(),
            node_id,
            start_byte: start,
            end_byte: end,
        });
        open.push((defs.len() - 1, end));
    }
    // a definition written in such a closure is a name of it, as one
    // written in a function is the function's: not the file's
    // (an RSpec `let` keeps its own scope, `Def::scope_lines`)
    let orphans: Vec<usize> = (0..defs.len())
        .filter(|&i| !defs[i].block && !defs[i].extra && defs[i].parent.is_none())
        .collect();
    let starts: Vec<usize> = orphans.iter().map(|&i| defs[i].start_byte).collect();
    let around = innermost_where(defs, &starts, |d| d.block);
    let mut hoisted = Vec::new();
    for (i, block) in orphans.into_iter().zip(around) {
        defs[i].parent = block;
        let Some(b) = block else { continue };
        if let Some((name, at)) = invoked.get(&defs[b].start_byte) {
            if defs[i].name == *name && (defs[i].kind.is_callable() || defs[i].kind.is_class_like())
            {
                defs[i].returned_out = true;
                hoisted.push((name.clone(), *at));
            }
        }
    }
    hoisted
}

fn assign_defs(defs: &[Def], refs: &mut [RefFact]) {
    let idx: Vec<usize> = (0..refs.len())
        .filter(|&i| refs[i].from_def.is_none() && refs[i].from_owner.is_none())
        .collect();
    let bytes: Vec<usize> = idx.iter().map(|&i| refs[i].byte).collect();
    for (k, d) in homes(defs, &bytes).into_iter().enumerate() {
        refs[idx[k]].from_def = d;
    }
}

fn assign_local_defs(defs: &[Def], locals: &mut [Local]) {
    let idx: Vec<usize> = (0..locals.len()).filter(|&i| locals[i].from_def.is_none()).collect();
    let bytes: Vec<usize> = idx.iter().map(|&i| locals[i].byte).collect();
    for (k, d) in homes(defs, &bytes).into_iter().enumerate() {
        locals[idx[k]].from_def = d;
    }
}

/// Properties and constants as definitions, after the `spans.py` ones (so
/// every index into `defs` stays valid), each under the definition around it.
/// C, C++ `extern redisAllocFuncs hiredisAllocFns;`: a declaration of a
/// variable defined elsewhere.
fn is_extern(n: Node, src: &[u8]) -> bool {
    kids(n).iter().any(|c| c.kind() == "storage_class_specifier" && text(*c, src) == "extern")
}

fn add_extras(
    defs: &mut Vec<Def>,
    extras: &[(Node, Node, crate::facts::DefKind, bool)],
    m: &dyn LanguageModule,
    src: &[u8],
) {
    let base = defs.len();
    // a name declared twice in one scope (`X = 1` … `X = 2`) is one variable:
    // the first declaration
    let mut seen: std::collections::HashSet<(Option<usize>, String)> =
        std::collections::HashSet::new();
    for (node, name_node, kind, state) in extras {
        let b = node.start_byte();
        let parent = defs[..base]
            .iter()
            .enumerate()
            .filter(|(_, d)| d.start_byte <= b && b < d.end_byte)
            .max_by_key(|(_, d)| d.start_byte)
            .map(|(i, _)| i);
        // a field first assigned in a method (`self.x = …` in `__init__`) is the
        // type's, not the method's
        let mut parent = parent;
        if *kind == crate::facts::DefKind::Property {
            while let Some(p) = parent.filter(|&p| defs[p].kind.is_callable()) {
                parent = defs[p].parent;
            }
        }
        // (`$x`, PHP; `:conn`, the symbol that names an RSpec `let`)
        // (PHP `$hits`, Ruby `$stdout` and the symbol `:conn` that names an
        // RSpec `let`; a JS / TS `$name` is spelled with its `$`)
        let written = text(*name_node, src);
        let sigils = matches!(
            crate::parse::current(),
            Some(crate::lang::Lang::Php | crate::lang::Lang::Ruby | crate::lang::Lang::Swift)
        );
        let name = if sigils {
            written.trim_start_matches(['$', ':']).to_string()
        } else {
            written.trim_start_matches(':').to_string()
        };
        if name == "_" {
            continue; // Go `var _ I = (*T)(nil)`: a compile-time check, no variable
        }
        // (properties and constants found before keep their own rule)
        if *state {
            let taken = defs[..base].iter().any(|d| d.parent == parent && d.name == name);
            if taken || !seen.insert((parent, name.clone())) {
                continue;
            }
        }
        let qualname = match parent {
            Some(p) => format!("{}.{name}", defs[p].qualname),
            None => name.clone(),
        };
        // C, C++ `struct { … } ev;`: a field of an anonymous struct type
        let inline = node
            .child_by_field_name("type")
            .filter(|t| matches!(t.kind(), "struct_specifier" | "union_specifier"))
            .filter(|t| t.child_by_field_name("name").is_none())
            .and_then(|t| t.child_by_field_name("body"))
            .map(|b| (b.start_byte(), b.end_byte()))
            .filter(|_| *state);
        let def_line = line(*node);
        // a callable written as a call with a block (RSpec `let(:conn) {
        // Client.new }`): what the block's last expression gives is its
        // result, and it is a name of the block it is written in
        let block = kind.is_callable().then(|| node.child_by_field_name("block")).flatten();
        let returns: Vec<Vec<String>> = block
            .and_then(|b| b.child_by_field_name("body"))
            .and_then(|body| kids(body).into_iter().rfind(|c| !is_comment(c)))
            .and_then(|v| returned_path(v, src))
            .into_iter()
            .collect();
        let scope_lines = block.and_then(|_| {
            let around = node.parent()?.parent()?;
            matches!(around.kind(), "do_block" | "block")
                .then(|| (line(around), m.end_line(around)))
        });
        defs.push(Def {
            name,
            qualname,
            kind: *kind,
            region_start: m.region_start(*node, def_line),
            def_line,
            end_line: m.end_line(*node),
            parent,
            owner: None,
            is_test: false,
            object: None,
            extra: true,
            decl_only: *state && is_extern(*node, src),
            container: crate::state::holds_container(*node, src),
            table: crate::resources::declared_table(*node, src, None, ""),
            value: state_value(*node, src),
            value_path: state_value_path(*node, src),
            declared_type: if inline.is_some() { None } else { written_type(*node, src) },
            written_head: written_head(*node, src),
            alias_args: Vec::new(),
            wraps: written_sealed(*node, src).then(|| written_type(*node, src)).flatten(),
            sealed: written_sealed(*node, src),
            elem: written_elem(*node, src),
            keyed: written_keyed(*node, src),
            key: written_key(*node, src),
            returned_parts: Vec::new(),
            returned_members: Vec::new(),
            scope_lines,
            targs: written_targs(*node, src),
            targ_elems: written_targ_elems(*node, src),
            base_targs: Vec::new(),
            rets: Vec::new(),
            part_sealed: Vec::new(),
            receiver: None,
            anonymous: false,
            misread: false,
            block: false,
            returned_out: false,
            computed: !*state && computed_property(*node),
            unspanned: false,
            inline_body: inline,
            created_from: kind.is_state().then(|| created_from(*node, src)).flatten(),
            model_name: kind.is_state().then(|| model_named(*node, src)).flatten(),
            params: block.map(|_| 0),
            plist: Vec::new(),
            returns,
            lambda_receiver: None,
            self_params: Vec::new(),
            attrs: Vec::new(),
            type_params: Vec::new(),
            type_bounds: Vec::new(),
            node_id: node.id(),
            start_byte: node.start_byte(),
            end_byte: node.end_byte(),
        });
    }
}
