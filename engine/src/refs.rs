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
        // PHP `$this`, and `$x` kept with its `$`: a variable, which only a
        // local of that name can type (never a function or class named `x`)
        let t = text(n, src);
        return Some(vec![if t == "$this" || t == "this" { "self".into() } else { t }]);
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
        "user_type"
        | "namespace_name"
        | "stable_identifier"
        | "dotted_name"
        | "nested_identifier"
        | "named_type"
        | "class_constant_access_expression" => {
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
        let sub = if matches!(k, "type_arguments" | "type_parameter" | "type_argument_list") {
            "generic_arg"
        } else {
            ctx
        };
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
            // Go `(*Response, error)`: the first result is what a caller
            // takes first
            Some("result") if child.kind() == "parameter_list" => {
                if let Some(first) = kids(child).into_iter().next() {
                    types_in(first, src, "return_type", out);
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

fn is_wrapper(name: &str, n: Node) -> bool {
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
    let sealed = |name: &str| {
        SEALED.contains(&name) && (WRAPPERS.contains(&name) || language_wrapper(name, t))
    };
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
        let rest: Vec<Vec<String>> = it.collect();
        if let Some(inner) = rest.into_iter().last() {
            return Some(inner);
        }
    }
    Some(first)
}

/// What a value constructs: `Foo()`, `new Foo()`, `Foo::new()`, `Foo{}`,
/// `&Foo{}`, `Foo.new`. The path is the callee's; the resolver keeps it only
/// when it names a type.
fn value_type(v: Node, src: &[u8]) -> Option<Vec<String>> {
    match v.kind() {
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
            let called = callee(v, src).map(|(_, p)| p)?;
            let mapped = if is_call_kind(v.kind()) { mapped_call(v, src, &called) } else { None };
            Some(mapped.unwrap_or(called)).filter(|p| p.first().is_some_and(|s| s != UNKNOWN))
        }
    }
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
    kids(n)
        .into_iter()
        .filter(|c| matches!(c.kind(), "type_parameters" | "type_parameter_list"))
        .flat_map(kids)
        .filter_map(|tp| {
            if tp.kind().contains("identifier") {
                return Some(text(tp, src));
            }
            kids(tp).into_iter().find(|c| c.kind().contains("identifier")).map(|c| text(c, src))
        })
        .collect()
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
    if k.contains("string") || k.contains("char_literal") || k == "character_literal" {
        Arg::Str
    } else if k.contains("integer")
        || k.contains("number")
        || k.contains("float")
        || k.contains("decimal")
        || matches!(k, "real_literal" | "long_literal" | "hex_literal" | "int_literal")
    {
        Arg::Num
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
        } else if has(Arg::Num) {
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

/// The parameters a callable declares (`self` / `cls` apart).
fn param_list(n: Node, src: &[u8]) -> Vec<crate::facts::ParamFact> {
    let list = n
        .child_by_field_name("parameters")
        .or_else(|| n.child_by_field_name("declarator")?.child_by_field_name("parameters"))
        .or_else(|| {
            kids(n).into_iter().find(|c| {
                matches!(
                    c.kind(),
                    "function_value_parameters" | "parameter_list" | "formal_parameters"
                )
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
                from_def: None,
                byte: *start,
            });
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
        if k.contains("type") || depth > 3 {
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
                    // Kotlin `val c = Plain()`: the value sits beside the
                    // `variable_declaration`, in the property declaration
                    let beside = || {
                        (d.kind() == "variable_declaration" && k == "property_declaration")
                            .then(|| {
                                kids(n)
                                    .into_iter()
                                    .filter(|c| c.id() != d.id())
                                    .find(|c| callee(*c, src).is_some())
                            })
                            .flatten()
                    };
                    let valued = x
                        .child_by_field_name("value")
                        .or_else(|| {
                            // (C# `var x = await f()`: no field names the value)
                            kids(x).into_iter().find(|c| {
                                callee(*c, src).is_some()
                                    || matches!(c.kind(), "await_expression" | "try_expression")
                            })
                        })
                        .or_else(beside)
                        .and_then(|v| value_type(v, src));
                    match (&d_type, valued) {
                        (Some(t), _) => out.push((name, t.clone(), true, field)),
                        (None, Some(t)) => out.push((name, t, false, field)),
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
            args: None,
            self_arg: false,
            hints: Vec::new(),
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
    // lambdas with a receiver: (start, end, where the receiver comes from)
    let mut receivers: Vec<(usize, usize, crate::facts::ReceiverOf)> = Vec::new();
    let mut steps = 0usize;
    while let Some((n, depth)) = stack.pop() {
        anc.truncate(depth);
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
                // Kotlin `class Name(val a: A, b: B = …)`: the primary
                // constructor's parameters are the class's
                if let Some(pc) = kids(n).into_iter().find(|c| c.kind() == "primary_constructor") {
                    facts.defs[di].plist = params_in(pc, false, src);
                    facts.defs[di].params = u8::try_from(facts.defs[di].plist.len()).ok();
                }
            }
            if facts.defs[di].kind.is_callable() {
                facts.defs[di].lambda_receiver = lambda_receiver(n, src);
                facts.defs[di].type_params = type_params(n, src);
                facts.defs[di].elem = return_elem(n, src);
                facts.defs[di].keyed = return_keyed(n, src);
                facts.defs[di].targs = return_targs(n, src);
                if let Some((inner, sealed)) = return_wraps(n, src) {
                    facts.defs[di].wraps = Some(inner);
                    facts.defs[di].sealed = sealed;
                }
                facts.defs[di].plist = param_list(n, src);
                facts.defs[di].params = u8::try_from(facts.defs[di].plist.len()).ok();
                if let Some(path) = body_value(n).and_then(|v| returned_path(v, src)) {
                    note_return(&mut facts.defs[di], path);
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
                                        assigned: false,
                                        given: None,
                                        elem: None,
                                        value_args: Vec::new(),
                                        sealed: false,
                                        keyed: false,
                                        targs: Vec::new(),
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
                                        assigned: false,
                                        given: None,
                                        elem: None,
                                        value_args: Vec::new(),
                                        sealed: false,
                                        keyed: false,
                                        targs: Vec::new(),
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
                            from_def: Some(di),
                            byte: ps.start_byte(),
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
                for c in kids(n) {
                    if matches!(c.kind(), "primary_constructor" | "class_parameters") {
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
                    let mut second = of;
                    second.push(VALUE.into());
                    // `{ (k, v) -> … }`, `([k, v]) => …`, `|(k, v)|`: an entry
                    // taken apart, its value second
                    let destructured = each && at == 0 && destructures_pair(n);
                    let two = params.len() == 2;
                    if let (Some(it), 0) = (lambda, at) {
                        params = vec![(it.to_string(), one, false)];
                    } else if destructured && two {
                        if params[1].1.is_empty() {
                            params[1].1 = second;
                        }
                    } else if lambda.is_none() && params.get(at).is_some_and(|p| p.1.is_empty()) {
                        params[at].1 = one;
                        // `m.forEach((k, v) -> …)`: the value of a map's
                        // entry beside its key (nothing for a list's)
                        if each && at == 0 && two && params[1].1.is_empty() {
                            params[1].1 = second;
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
                    from_def: None,
                    byte: n.start_byte(),
                });
            }
        }
        if !by_node.contains_key(&n.id()) {
            // an untyped `x = …` declares a local only where assigning declares
            // (Python, Ruby); elsewhere it writes a variable declared before
            let plain_assignment = matches!(n.kind(), "assignment" | "assignment_expression")
                && !m.assignment_declares();
            for (name, mut path, declared, field) in locals_of(n, src, false) {
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
                    from_def: None,
                    byte: n.start_byte(),
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
        if let Some(path) = returned_value(n, src).and_then(|v| returned_path(v, src)) {
            let home = anc.iter().rev().find_map(|a| match by_node.get(&a.id()) {
                Some(&di) if facts.defs[di].kind.is_callable() => Some(Some(di)),
                _ if is_closure(a.kind()) => Some(None),
                _ => None,
            });
            if let Some(Some(di)) = home {
                note_return(&mut facts.defs[di], path);
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
        let mut cursor = n.walk();
        let children: Vec<Node> = n.children(&mut cursor).filter(|c| c.is_named()).collect();
        anc.push(n);
        stack.extend(children.into_iter().rev().map(|c| (c, depth + 1)));
    }
    add_extras(&mut facts.defs, &extras, m, src);
    let defs = &facts.defs;
    refs.extend(state.finish(defs, &imports, m.needs_import(), |bytes| innermost(defs, bytes)));
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
    if !(is_closure(f.kind()) || f.kind() == "block") {
        return Err(());
    }
    let params = f
        .child_by_field_name("parameters")
        .or_else(|| f.child_by_field_name("parameter"))
        .or_else(|| {
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
        .and_then(single_expression)
        .and_then(|e| path_of(e, src))
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
    } else if INDEXED_METHODS.contains(&method.as_str()) {
        Some((1, of, true))
    } else if SELF_METHODS.contains(&method.as_str()) {
        Some((0, of, false))
    } else {
        None
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
        | "for" => {
            // a type written for the variable types it already
            if n.child_by_field_name("type").is_some_and(|t| first_type(t, src).is_some()) {
                return None;
            }
            let var = ["left", "name", "pattern", "item"]
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
        let of = path_of(iterable, src).filter(|p| p[0] != UNKNOWN)?;
        let mut path = of;
        path.push(VALUE.into());
        let mut out = vec![(key, Vec::new())];
        if value != "_" {
            out.push((value, path));
        }
        out.retain(|(n, _)| n != "_");
        return Some(out);
    }
    // one name, not a pattern of several
    let name = match var.kind() {
        "identifier" | "simple_identifier" | "variable_name" => bound_name(var, src)?,
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
        n.child_by_field_name("type").and_then(|t| first_type(t, src)).or_else(|| {
            kids(n)
                .into_iter()
                .find(|c| matches!(c.kind(), "user_type" | "type_annotation" | "nullable_type"))
                .and_then(|t| first_type(t, src))
        })
    })
}

/// What constructs the value a declaration holds (`Settings()`).
fn state_value_path(node: Node, src: &[u8]) -> Option<Vec<String>> {
    value_node(node).and_then(|v| value_type(v, src))
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
    if !defs.iter().any(|d| d.extra) {
        return innermost(defs, bytes);
    }
    let all = innermost(defs, bytes);
    let plain = innermost_where(defs, bytes, |d| !d.extra);
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
        let name = text(*name_node, src).trim_start_matches('$').to_string();
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
        let def_line = line(*node);
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
            decl_only: false,
            container: crate::state::holds_container(*node, src),
            table: crate::resources::declared_table(*node, src, None, ""),
            value: state_value(*node, src),
            value_path: state_value_path(*node, src),
            declared_type: written_type(*node, src),
            wraps: written_sealed(*node, src).then(|| written_type(*node, src)).flatten(),
            sealed: written_sealed(*node, src),
            elem: written_elem(*node, src),
            keyed: written_keyed(*node, src),
            targs: written_targs(*node, src),
            receiver: None,
            anonymous: false,
            params: None,
            plist: Vec::new(),
            returns: Vec::new(),
            lambda_receiver: None,
            self_params: Vec::new(),
            attrs: Vec::new(),
            type_params: Vec::new(),
            node_id: node.id(),
            start_byte: node.start_byte(),
            end_byte: node.end_byte(),
        });
    }
}
