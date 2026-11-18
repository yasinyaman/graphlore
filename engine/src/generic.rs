//! The generic definition rule, ported from graphlore's `spans.py`
//! (`_spans_treesitter` and its helpers). It walks any tree-sitter grammar: a
//! node is a definition when its type carries a hint word and it names itself —
//! a `name` field, a C/C++ declarator chain, or an identifier child in a grammar
//! without name fields. A type merely written in a signature is not a definition.
//!
//! Keep this in step with `spans.py`: the qualnames and line ranges it produces
//! are compared against that module by `tests/test_engine.py`.

use tree_sitter::Node;

use crate::facts::{Def, DefKind};
use crate::lang::{LanguageModule, Step};

/// Node-type hints of a definition (substring match).
const HINTS: &[&str] = &[
    "function",
    "method",
    "constructor",
    "class",
    "struct",
    "interface",
    "enum",
    "trait",
    "module",
    "object",
    "type",
    "subroutine",
    "procedure",
    "package",
    "namespace",
    "record",
    "protocol",
];
/// Hints that must be a whole `_`-separated word: as substrings "impl" sits in
/// PHP's `simple_parameter` and "mod" in many names.
const WORD_HINTS: &[&str] = &["impl", "mod"];
/// Types that match a hint but name a use of a symbol, never its definition.
const NON_SYMBOL_SUFFIXES: &[&str] =
    &["_type", "_body", "_parameters", "_clause", "_identifier", "_annotation", "_parameter"];
/// Types that match a hint and carry a `name` field yet define nothing.
const CALL_TYPES: &[&str] = &[
    "template_function",
    "template_method",
    "call_expression",
    "function_declarator",
    "type_binding",
    "type_parameter",
    "constrained_type_parameter",
    "optional_type_parameter",
    "type_argument",
    "type_arguments",
    "type_parameter_declaration",
    "type_predicate",
    "tuple_type_item",
    "struct_expression",
    "macro_type_specifier",
];
/// Definitions whose grammar gives them a keyword, not a name.
const KEYWORD_NAMES: &[(&str, &str)] = &[
    ("init_declaration", "init"),
    ("deinit_declaration", "deinit"),
    ("secondary_constructor", "constructor"),
];
/// The child that names a declaration in a grammar without a `name` field.
const NAME_CHILD_TYPES: &[&str] = &["simple_identifier", "type_identifier", "identifier"];
/// C/C++ declarations with no body: spans only when they declare a function.
const PROTOTYPE_TYPES: &[&str] = &["declaration", "field_declaration"];
/// Callees that take a function to run later, not to wrap it.
const SCHEDULERS: &[&str] = &["setTimeout", "setInterval", "setImmediate", "queueMicrotask"];
const FUNCTION_VALUES: &[&str] =
    &["arrow_function", "function_expression", "function", "generator_function"];
/// Nesting deeper than this is not walked (generated code); the walk uses an
/// explicit stack, so this bounds work, not the call stack.
const MAX_DEPTH: usize = 2000;

pub fn text(n: Node, src: &[u8]) -> String {
    String::from_utf8_lossy(&src[n.byte_range()]).into_owned()
}

pub fn line(n: Node) -> u32 {
    n.start_position().row as u32 + 1
}

pub fn end_line(n: Node) -> u32 {
    n.end_position().row as u32 + 1
}

pub fn kids<'t>(n: Node<'t>) -> Vec<Node<'t>> {
    let mut cursor = n.walk();
    n.named_children(&mut cursor).collect()
}

/// All children, anonymous tokens included (keywords such as `interface`).
pub fn all_kids<'t>(n: Node<'t>) -> Vec<Node<'t>> {
    let mut cursor = n.walk();
    n.children(&mut cursor).collect()
}

fn keyword_name(kind: &str) -> Option<&'static str> {
    KEYWORD_NAMES.iter().find(|(k, _)| *k == kind).map(|(_, v)| *v)
}

/// `_is_ts_symbol`: whether a node type can be a definition.
pub fn is_symbol(kind: &str) -> bool {
    let t = kind.to_ascii_lowercase();
    if CALL_TYPES.contains(&t.as_str()) || NON_SYMBOL_SUFFIXES.iter().any(|s| t.ends_with(s)) {
        return false;
    }
    // calls expose the callee under `name` but define nothing
    if t.contains("invocation") || t.contains("call") {
        return false;
    }
    if HINTS.iter().any(|h| t.contains(h)) {
        return true;
    }
    t.split('_').any(|w| WORD_HINTS.contains(&w))
}

/// `_ts_region_start`: extend upward over adjacent doc comments, decorators,
/// annotations and attributes; a blank line detaches them.
pub fn region_start(node: Node, def_line: u32) -> u32 {
    let mut start = def_line;
    let mut prev = node.prev_named_sibling();
    while let Some(p) = prev {
        let pt = p.kind().to_ascii_lowercase();
        if !["comment", "decorator", "annotation", "attribute"].iter().any(|h| pt.contains(h)) {
            break;
        }
        if end_line(p) + 1 < start {
            break;
        }
        start = line(p);
        prev = p.prev_named_sibling();
    }
    start
}

/// `_ts_receiver_type`: Go `(c *Client)` -> `Client`.
fn receiver_type(recv: Node, src: &[u8]) -> Option<String> {
    let mut queue: std::collections::VecDeque<Node> = kids(recv).into();
    while let Some(n) = queue.pop_front() {
        if n.kind() == "type_identifier" {
            return Some(text(n, src));
        }
        queue.extend(kids(n));
    }
    None
}

/// `_ts_wrapped_function`: the function in `wrap(async (req, res) => {…})`.
fn wrapped_function<'t>(call: Node<'t>, src: &[u8]) -> Option<Node<'t>> {
    let f = call.child_by_field_name("function")?;
    let args = call.child_by_field_name("arguments")?;
    if f.kind() != "identifier" || SCHEDULERS.contains(&text(f, src).as_str()) {
        return None;
    }
    let found: Vec<Node> =
        kids(args).into_iter().filter(|a| FUNCTION_VALUES.contains(&a.kind())).collect();
    if found.len() != 1 || found[0].child_by_field_name("name").is_some() {
        return None;
    }
    Some(found[0])
}

/// `_ts_bound_function`: an anonymous function bound to a name.
pub fn bound_function<'t>(node: Node<'t>, src: &[u8]) -> Option<(String, Node<'t>)> {
    let mut name = node
        .child_by_field_name("name")
        .or_else(|| node.child_by_field_name("key"))
        .or_else(|| node.child_by_field_name("property"));
    let mut value = node.child_by_field_name("value");
    if name.is_none() && value.is_none() {
        let mut left = node.child_by_field_name("left");
        if let Some(l) = left {
            if l.kind() == "member_expression" {
                left = l.child_by_field_name("property");
            }
        }
        name = left;
        value = node.child_by_field_name("right");
    }
    let name = name?;
    let mut value = value?;
    if !name.kind().ends_with("identifier") {
        return None;
    }
    if value.kind() == "call_expression" {
        value = wrapped_function(value, src)?;
    }
    let vt = value.kind().to_ascii_lowercase();
    if !["function", "arrow", "lambda", "closure", "func"].iter().any(|h| vt.contains(h)) {
        return None;
    }
    if vt.ends_with("_type") {
        // a type written out (Go `map[string]func() int`), not a function
        return None;
    }
    if value.child_by_field_name("name").is_some() {
        return None;
    }
    Some((text(name, src), value))
}

/// `_ts_anonymous_class`: Java `new Callback() { … }`, Kotlin
/// `object : Callback<T> { … }`, Ruby `Request = Struct.new(:a) do … end`.
pub fn anonymous_class(node: Node, src: &[u8]) -> Option<String> {
    match node.kind() {
        "object_creation_expression" => {
            if !kids(node).iter().any(|c| c.kind() == "class_body") {
                return None;
            }
            node.child_by_field_name("type").map(|t| text(t, src))
        }
        "object_literal" => {
            let ks = kids(node);
            let spec = ks.iter().find(|c| c.kind() == "delegation_specifier")?;
            let user = kids(*spec).into_iter().find(|c| c.kind() == "user_type")?;
            if !ks.iter().any(|c| c.kind() == "class_body") {
                return None;
            }
            Some(text(user, src).split('<').next().unwrap_or_default().to_string())
        }
        "assignment" => {
            let left = node.child_by_field_name("left")?;
            let right = node.child_by_field_name("right")?;
            if left.kind() == "constant"
                && right.kind() == "call"
                && right.child_by_field_name("block").is_some()
            {
                Some(text(left, src))
            } else {
                None
            }
        }
        _ => None,
    }
}

fn next_declarator<'t>(d: Node<'t>, also_qualified: bool) -> Option<Node<'t>> {
    d.child_by_field_name("declarator").or_else(|| {
        kids(d).into_iter().find(|c| {
            c.kind().ends_with("declarator")
                || (also_qualified && c.kind() == "qualified_identifier")
        })
    })
}

/// `_ts_declarator_name`: C/C++ `Session::Get` -> `Session.Get`.
fn declarator_name(node: Node, src: &[u8]) -> Option<String> {
    let mut decl = node.child_by_field_name("declarator");
    for _ in 0..8 {
        let d = decl?;
        match d.kind() {
            "identifier" | "field_identifier" | "destructor_name" | "type_identifier"
            | "operator_name" => return Some(text(d, src)),
            "qualified_identifier" => return Some(text(d, src).replace("::", ".")),
            _ => {}
        }
        decl = next_declarator(d, true);
    }
    None
}

/// `_ts_declares_function`: `int f(void);`, not a variable or a field.
fn declares_function(node: Node) -> bool {
    let mut decl = node.child_by_field_name("declarator");
    for _ in 0..8 {
        let Some(d) = decl else { return false };
        if d.kind() == "function_declarator" {
            return true;
        }
        decl = next_declarator(d, false);
    }
    false
}

/// `_ts_child_name`: a keyword (Swift `init`) or the first identifier child
/// (Kotlin).
fn child_name(node: Node, src: &[u8]) -> Option<String> {
    if let Some(k) = keyword_name(node.kind()) {
        return Some(k.to_string());
    }
    let t = node.kind().to_ascii_lowercase();
    if !(t.ends_with("_declaration") || t.ends_with("_entry") || t.ends_with("type_alias"))
        || t.contains("package")
    {
        return None;
    }
    kids(node).into_iter().find(|c| NAME_CHILD_TYPES.contains(&c.kind())).map(|c| text(c, src))
}

/// The generic classification of one node, in `spans.py`'s order.
pub fn classify<'t, M: LanguageModule + ?Sized>(m: &M, node: Node<'t>, src: &[u8]) -> Step<'t> {
    let kind = node.kind();
    let is_sym = m.is_symbol(kind) || keyword_name(kind).is_some();
    let def = |name: String| Step::Def {
        name,
        node,
        descend: node,
        region_start: None,
        owner: None,
        kind: None,
    };
    if is_sym {
        if let Some(name_node) = node.child_by_field_name("name") {
            if kind.ends_with("_specifier") && node.child_by_field_name("body").is_none() {
                // C `struct config cfg` in a signature, or a forward declaration
                return Step::Descend;
            }
            if kind == "mod_item" && node.child_by_field_name("body").is_none() {
                return Step::Skip; // Rust `mod foo;` names a file
            }
            let owner = node.child_by_field_name("receiver").and_then(|r| receiver_type(r, src));
            return Step::Def {
                name: text(name_node, src),
                node,
                descend: node,
                region_start: None,
                owner,
                kind: None,
            };
        }
        if kind.to_ascii_lowercase().contains("impl") {
            if let Some(type_node) = node.child_by_field_name("type") {
                // Rust `impl Pool for Fixed`: a prefix, no span; generics stripped
                let base = type_node.child_by_field_name("type").unwrap_or(type_node);
                let t = text(base, src);
                return Step::Scope { prefix: t.clone(), owner: Some(t), descend: node };
            }
        }
    }
    let prototype = PROTOTYPE_TYPES.contains(&kind) && declares_function(node);
    if is_sym || prototype {
        if let Some(name) = declarator_name(node, src) {
            let kind = if prototype || kind == "function_definition" {
                Some(DefKind::Function)
            } else {
                None
            };
            return Step::Def { name, node, descend: node, region_start: None, owner: None, kind };
        }
    }
    if is_sym {
        if let Some(name) = child_name(node, src) {
            return def(name);
        }
    }
    if let Some(name) = anonymous_class(node, src) {
        return Step::Def {
            name,
            node,
            descend: node,
            region_start: None,
            owner: None,
            kind: Some(DefKind::Class),
        };
    }
    if m.bound_functions() {
        if let Some((name, f)) = bound_function(node, src) {
            return Step::Def {
                name,
                node: f,
                descend: f,
                region_start: Some(line(f)),
                owner: None,
                kind: Some(DefKind::Function),
            };
        }
    }
    Step::Descend
}

/// What a definition is, from its node type alone.
pub fn kind_of(kind: &str) -> DefKind {
    let t = kind.to_ascii_lowercase();
    let has = |h: &str| t.contains(h);
    let word = |w: &str| t.split('_').any(|p| p == w);
    if has("enum")
        && ["variant", "member", "constant", "entry", "case", "assignment"].iter().any(|h| has(h))
    {
        DefKind::EnumMember
    } else if has("namespace") || has("package") || word("mod") {
        DefKind::Namespace
    } else if has("constructor") || t == "init_declaration" || t == "deinit_declaration" {
        DefKind::Constructor
    } else if has("method") {
        DefKind::Method
    } else if has("func")
        || has("subroutine")
        || has("procedure")
        || has("lambda")
        || has("arrow")
        || has("closure")
    {
        DefKind::Function
    } else if has("interface") || has("trait") || has("protocol") {
        DefKind::Interface
    } else if has("enum") {
        DefKind::Enum
    } else if has("struct") {
        DefKind::Struct
    } else if has("class") || has("object") || has("record") {
        DefKind::Class
    } else if has("module") {
        DefKind::Module
    } else if has("type") {
        DefKind::TypeAlias
    } else {
        DefKind::Other
    }
}

/// Split a possibly qualified name into (owner, bare name): `Session.Get`,
/// `Foo::Bar`, `Shop\Stack`.
fn split_qualified(name: &str) -> (Option<String>, String) {
    let normalized = name.replace("::", ".").replace('\\', ".");
    let trimmed = normalized.trim_matches('.');
    match trimmed.rsplit_once('.') {
        Some((owner, bare)) if !owner.is_empty() && !bare.is_empty() => {
            (Some(owner.to_string()), bare.to_string())
        }
        _ => (None, trimmed.to_string()),
    }
}

struct Item<'t> {
    node: Node<'t>,
    prefix: String,
    parent: Option<usize>,
    owner: Option<String>,
    depth: usize,
}

fn push_children<'t>(
    stack: &mut Vec<Item<'t>>,
    of: Node<'t>,
    prefix: &str,
    parent: Option<usize>,
    owner: &Option<String>,
    depth: usize,
) {
    if depth >= MAX_DEPTH {
        return;
    }
    let children = kids(of);
    for child in children.into_iter().rev() {
        stack.push(Item {
            node: child,
            prefix: prefix.to_string(),
            parent,
            owner: owner.clone(),
            depth: depth + 1,
        });
    }
}

/// Every definition under `root`, in source (pre-)order — the order and the
/// `(region_start, end_line, def_line, qualname)` values of `spans.py`.
pub fn walk_defs(m: &dyn LanguageModule, root: Node, src: &[u8]) -> Vec<Def> {
    let mut defs: Vec<Def> = Vec::new();
    let mut stack: Vec<Item> = Vec::new();
    push_children(&mut stack, root, "", None, &None, 0);
    while let Some(item) = stack.pop() {
        match m.classify(item.node, src) {
            Step::Def { name, node, descend, region_start, owner, kind } => {
                let qual_base = match &owner {
                    Some(o) => format!("{o}.{name}"),
                    None => name.clone(),
                };
                let qualname = format!("{}{}", item.prefix, qual_base);
                let (name_owner, bare) = split_qualified(&name);
                let owner = owner.or(name_owner).or_else(|| item.owner.clone());
                let def_line = line(node);
                let mut kind = kind.unwrap_or_else(|| m.kind_of(node, src));
                let parent_class =
                    item.parent.map(|p| defs[p].kind.is_class_like()).unwrap_or(false);
                if kind == DefKind::Function && (parent_class || owner.is_some()) {
                    kind = DefKind::Method;
                }
                let children_owner =
                    if kind == DefKind::Extension { Some(bare.clone()) } else { None };
                defs.push(Def {
                    name: bare,
                    qualname: qualname.clone(),
                    kind,
                    region_start: region_start.unwrap_or_else(|| m.region_start(node, def_line)),
                    def_line,
                    end_line: m.end_line(node),
                    parent: item.parent,
                    owner,
                    is_test: false,
                    object: None,
                    extra: false,
                    decl_only: PROTOTYPE_TYPES.contains(&node.kind()),
                    container: false,
                    table: None,
                    value: None,
                    value_path: None,
                    declared_type: None,
                    wraps: None,
                    sealed: false,
                    elem: None,
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
                let idx = defs.len() - 1;
                push_children(
                    &mut stack,
                    descend,
                    &format!("{qualname}."),
                    Some(idx),
                    &children_owner,
                    item.depth,
                );
            }
            Step::Scope { prefix, owner, descend } => {
                push_children(
                    &mut stack,
                    descend,
                    &format!("{}{}.", item.prefix, prefix),
                    item.parent,
                    &owner,
                    item.depth,
                );
            }
            Step::Skip => {}
            Step::Descend => {
                push_children(
                    &mut stack,
                    item.node,
                    &item.prefix,
                    item.parent,
                    &item.owner,
                    item.depth,
                );
            }
        }
    }
    defs
}
