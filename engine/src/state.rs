//! Shared state: which definitions read and which write a variable, a field
//! or a constant.
//!
//! Two functions that never call each other still affect each other when one
//! writes what the other reads. The walk records, for every name and member
//! access in an expression, whether it is read (`RefKind::Read`, or `Attr` for
//! a member read on its own) or written (`RefKind::Write`): assigned, changed
//! in place (`x += 1`, `x[k] = v`, `del x`), or the receiver of a call whose
//! method name says it changes its receiver (`x.append(v)`). Resolution then
//! keeps the ones that name a variable, property or constant node.
//!
//! A name a function declares itself (a parameter, a local, in Python any name
//! it assigns without `global`) is its own and is left out here, where the
//! file's declarations are known.

use std::collections::{HashMap, HashSet};

use tree_sitter::Node;

use crate::facts::{Def, ImportFact, RefFact, RefKind};
use crate::generic::{kids, line, text};
use crate::refs::{bound_name, is_call_kind, path_of, MEMBER_FIELDS, UNKNOWN};

/// Member-access shapes: `a.b`, `a::b`, `a->b`.
const MEMBER_KINDS: &[&str] = &[
    "attribute",
    "member_expression",
    "field_expression",
    "member_access_expression",
    "navigation_expression",
    "selector_expression",
    "field_access",
    "scoped_identifier",
    "qualified_identifier",
    "scoped_property_access_expression",
    "class_constant_access_expression",
    "scope_resolution",
];
/// Bare names that can be a variable, a constant or a field.
const BARE_KINDS: &[&str] = &[
    "identifier",
    "simple_identifier",
    "constant",
    "class_variable",
    "global_variable",
    "instance_variable",
];
const ASSIGNMENTS: &[&str] = &[
    "assignment",
    "augmented_assignment",
    "assignment_expression",
    "augmented_assignment_expression",
    "operator_assignment",
    "assignment_statement",
    "compound_assignment_expr",
    "reference_assignment_expression",
];
const UPDATES: &[&str] = &[
    "update_expression",
    "inc_statement",
    "dec_statement",
    "postfix_unary_expression",
    "prefix_unary_expression",
    "postfix_expression",
    "prefix_expression",
];
/// Several targets written side by side: `a, b = …`, `[a, b] = …`.
const TARGET_LISTS: &[&str] = &[
    "pattern_list",
    "tuple_pattern",
    "list_pattern",
    "expression_list",
    "left_assignment_list",
    "array_pattern",
    "tuple",
    "list",
    "tuple_expression",
    "parenthesized_expression",
];
/// Nodes that declare the names they bind.
const DECLARATIONS: &[&str] = &[
    "variable_declarator",
    "let_declaration",
    "init_declarator",
    "var_spec",
    "const_spec",
    "short_var_declaration",
    "range_clause",
    "for_in_clause",
    "for_statement",
    "for_in_statement",
    "for_expression",
    "foreach_statement",
    "catch_clause",
    "catch_declaration",
    "catch_formal_parameter",
    "as_pattern",
    "named_expression",
    "let_condition",
    "property_declaration",
    "local_variable_declaration",
    "variable_declaration",
    "declaration",
    "resource",
    "lambda_parameters",
    "closure_parameters",
    "val_definition",
    "var_definition",
    "enhanced_for_statement",
    "for_each_statement",
    "receive_statement",
    "type_switch_statement",
];
/// Method names that change the receiver they are called on. A call of one on
/// a variable is a write of it (`INFERRED`: the name is the only evidence).
pub const MUTATORS: &[&str] = &[
    // Python
    "append",
    "extend",
    "insert",
    "add",
    "update",
    "pop",
    "popitem",
    "remove",
    "discard",
    "clear",
    "setdefault",
    "sort",
    "reverse",
    "appendleft",
    "popleft",
    // JavaScript
    "push",
    "unshift",
    "shift",
    "splice",
    "set",
    "delete",
    "fill",
    "copyWithin",
    // JVM and Kotlin
    "put",
    "putAll",
    "putIfAbsent",
    "addAll",
    "removeAll",
    "removeIf",
    "retainAll",
    "offer",
    "poll",
    "computeIfAbsent",
    "compute",
    "merge",
    "incrementAndGet",
    "decrementAndGet",
    "getAndIncrement",
    "getAndSet",
    "addFirst",
    "addLast",
    "removeFirst",
    "removeLast",
    // C++ and Rust
    "push_back",
    "push_front",
    "pop_back",
    "pop_front",
    "emplace",
    "emplace_back",
    "erase",
    "resize",
    "truncate",
    "drain",
    "retain",
    "entry",
    "store",
    "swap",
    "fetch_add",
    "fetch_sub",
    "push_str",
    "get_or_insert_with",
    "get_mut",
    "borrow_mut",
    // C#
    "Add",
    "AddRange",
    "Remove",
    "RemoveAt",
    "RemoveAll",
    "Clear",
    "Insert",
    "Enqueue",
    "Dequeue",
    "Push",
    "Pop",
    "TryAdd",
    "TryRemove",
    "AddOrUpdate",
    "Set",
    "Store",
    "Delete",
    // Ruby and PHP
    "concat",
    "delete_if",
    "replace",
    "store",
    "merge!",
    "push!",
    "map!",
    "reject!",
    "select!",
    "increment",
    "decrement",
    // Swift
    "removeAll",
    "removeValue",
    "updateValue",
    "formUnion",
];
const MAX_CLIMB: usize = 8;
/// Calls a mutating call can go through and still change the variable it
/// started from: `CACHE.lock().unwrap().insert(k, v)`, `map.entry(k).or_default()`.
const PASS_THROUGH: &[&str] = &[
    "lock",
    "unwrap",
    "expect",
    "borrow_mut",
    "write",
    "get_mut",
    "as_mut",
    "entry",
    "or_insert",
    "or_default",
    "or_insert_with",
    "values_mut",
    "iter_mut",
    "get_or_init",
];

/// Whether a declaration holds a collection or a builder, by its declared type
/// or its value.
pub fn holds_container(node: Node, src: &[u8]) -> bool {
    use std::sync::OnceLock;
    static RE: OnceLock<regex::Regex> = OnceLock::new();
    let re = RE.get_or_init(|| {
        regex::Regex::new(concat!(
            r"(?i)(^\s*[\[{]|\b(dict|list|set|map|array|vec|vector|deque|queue|stack|cache|buffer|",
            r"builder|collection|registry|table|bag|counter|defaultdict|ordereddict|weakmap|weakset|",
            r"hashmap|hashset|btreemap|btreeset|arraylist|linkedlist|concurrenthashmap|dictionary|",
            r"mutablelistof|mutablemapof|mutablesetof|arrayof|listof|mapof|setof|sortedmap|",
            r"mutablelist|mutablemap|mutableset|stringbuilder|bytearray|bytes\.buffer|sync\.map)\b|",
            r"\bmake\(|\[\]\w|\bnew\s+\w*(list|map|set|array|dictionary|queue|stack|collection)\w*)"
        ))
        .unwrap()
    });
    let mut parts = Vec::new();
    for field in ["type", "value", "right", "default_value", "initializer"] {
        if let Some(c) = node.child_by_field_name(field) {
            parts.push(c);
        }
    }
    if parts.is_empty() {
        // no fields to go by (Kotlin, Swift, C# declarators): the declaration
        // past its name
        parts.extend(kids(node).into_iter().skip(1));
    }
    parts.iter().any(|p| {
        let t = text(*p, src);
        re.is_match(&t[..t.len().min(200)])
    })
}

/// The state uses of one file, gathered node by node.
#[derive(Default)]
pub struct State {
    uses: Vec<RefFact>,
    /// `(byte, name)` of every name a declaration binds.
    declared: Vec<(usize, String)>,
    /// Names declared in a block, a loop or a closure: `(the scope's first
    /// byte, its end, the name)`. Outside every function nothing else says
    /// that a use in there is the block's own.
    blocked: Vec<(usize, usize, String)>,
    /// Python `global x` / `nonlocal x`: assignments of `x` are not locals.
    outer: Vec<(usize, String)>,
    /// An assignment to a bare name declares a local (Python).
    pub assignment_declares: bool,
}

impl State {
    pub fn new(assignment_declares: bool) -> Self {
        State { assignment_declares, ..Default::default() }
    }

    /// Record what `n` reads, writes and declares. Called for every node.
    pub fn visit<'t>(&mut self, n: Node<'t>, src: &[u8], anc: &[Node<'t>]) {
        let k = n.kind();
        if matches!(k, "global_statement" | "nonlocal_statement") {
            for c in kids(n) {
                self.outer.push((n.start_byte(), text(c, src)));
            }
            return;
        }
        let had = self.declared.len();
        self.declarations(n, k, src);
        if self.declared.len() > had {
            use crate::lang::Lang;
            let lang = crate::parse::current();
            let scoped = !matches!(lang, Some(Lang::Python | Lang::Ruby | Lang::Php) | None);
            // (JS `var` is the function's, whatever block it is written in)
            let hoisted = matches!(lang, Some(Lang::JavaScript | Lang::TypeScript | Lang::Tsx))
                && (k == "variable_declaration"
                    || up(anc, 0).is_some_and(|p| p.kind() == "variable_declaration"));
            // (the body of a type is no block: what it declares are members)
            let of_type = |kind: &str| {
                [
                    "class",
                    "interface",
                    "enum",
                    "struct",
                    "object",
                    "trait",
                    "impl",
                    "protocol",
                    "template",
                    "extension",
                    "declaration_list",
                    "record",
                    "union",
                    "namespace",
                    "module",
                ]
                .iter()
                .any(|w| kind.contains(w))
            };
            let scope = std::iter::once(&n)
                .chain(anc.iter().rev())
                .find(|a| crate::refs::is_scope(a.kind()) || of_type(a.kind()))
                .filter(|a| scoped && !hoisted && !of_type(a.kind()));
            if let Some(scope) = scope {
                let (lo, hi) = (scope.start_byte(), scope.end_byte());
                let names: Vec<String> =
                    self.declared[had..].iter().map(|(_, name)| name.clone()).collect();
                self.blocked.extend(names.into_iter().map(|name| (lo, hi, name)));
                // (its range says where it hides: no local of the whole function)
                self.declared.truncate(had);
            }
        }
        if k == "infix_expression" && scala_compound(n, src) {
            // Scala `hits += 1`
            let mut out = Vec::new();
            if let Some(t) = n.child_by_field_name("left") {
                targets(t, src, "augmented", &mut out, 0);
            }
            for (node, path, ctx) in out {
                self.push(node, RefKind::Write, path.clone(), ctx);
                self.push(node, RefKind::Read, path, "read");
            }
        } else if k == "assignment_expression"
            && up(anc, 0).is_some_and(|p| p.kind() == "arguments")
            && crate::parse::current() == Some(crate::lang::Lang::Scala)
        {
            // Scala `Local(a = 1)`: an argument given by name writes nothing
        } else if ASSIGNMENTS.contains(&k) {
            let target = n
                .child_by_field_name("left")
                .or_else(|| n.child_by_field_name("target"))
                .or_else(|| kids(n).into_iter().next());
            let has_value = n.child_by_field_name("right").is_some()
                || n.child_by_field_name("result").is_some()
                || n.named_child_count() >= 2;
            if let (Some(t), true) = (target, has_value) {
                let augmented = is_augmented(n, k, src);
                let mut out = Vec::new();
                targets(t, src, if augmented { "augmented" } else { "assign" }, &mut out, 0);
                for (node, path, ctx) in out {
                    // `x = 1` declares a local `x`; `$x = 1`, `@@x = 1` do not
                    if self.assignment_declares
                        && !augmented
                        && path.len() == 1
                        && ctx == "assign"
                        && node.kind() == "identifier"
                    {
                        self.declared.push((node.start_byte(), path[0].clone()));
                    }
                    self.push(node, RefKind::Write, path.clone(), ctx);
                    if augmented {
                        self.push(node, RefKind::Read, path, "read");
                    }
                }
            }
        } else if UPDATES.contains(&k) {
            let t = text(n, src);
            if t.starts_with("++") || t.starts_with("--") || t.ends_with("++") || t.ends_with("--")
            {
                let target = n
                    .child_by_field_name("argument")
                    .or_else(|| n.child_by_field_name("operand"))
                    .or_else(|| kids(n).into_iter().next());
                let mut out = Vec::new();
                if let Some(t) = target {
                    targets(t, src, "augmented", &mut out, 0);
                }
                for (node, path, ctx) in out {
                    self.push(node, RefKind::Write, path.clone(), ctx);
                    self.push(node, RefKind::Read, path, "read");
                }
            }
        } else if k == "delete_statement" {
            let mut out = Vec::new();
            for c in kids(n) {
                targets(c, src, "delete", &mut out, 0);
            }
            for (node, path, ctx) in out {
                self.push(node, RefKind::Write, path, ctx);
            }
        }
        if BARE_KINDS.contains(&k) {
            self.bare(n, src, anc);
        } else if MEMBER_KINDS.contains(&k) {
            self.member(n, src, anc);
        }
    }

    fn push(&mut self, n: Node, kind: RefKind, path: Vec<String>, context: &'static str) {
        if path.is_empty() || path.iter().any(|s| s == UNKNOWN || s.is_empty()) {
            return;
        }
        if path.len() == 1 && matches!(path[0].as_str(), "self" | "super" | "crate" | "_") {
            return;
        }
        self.uses.push(RefFact {
            line: line(n),
            kind,
            path,
            context,
            from_def: None,
            from_owner: None,
            args: None,
            self_arg: false,
            hints: Vec::new(),
            implicit: None,
            targs: 0,
            arity: None,
            this_of: None,
            recv: None,
            byte: n.start_byte(),
        });
    }

    fn declarations(&mut self, n: Node, k: &str, src: &[u8]) {
        let parameter = k.contains("parameter") && !k.contains("type_parameter");
        if !parameter && !DECLARATIONS.contains(&k) {
            return;
        }
        // JS `for ([a, b] of rows)` with no `let` / `const` / `var` assigns
        // names declared elsewhere
        if k == "for_in_statement"
            && n.child_by_field_name("kind").is_none()
            && matches!(
                crate::parse::current(),
                Some(
                    crate::lang::Lang::JavaScript
                        | crate::lang::Lang::TypeScript
                        | crate::lang::Lang::Tsx
                )
            )
        {
            return;
        }
        if let Some(name) = bound_name(n, src) {
            self.declared.push((n.start_byte(), name));
        }
        // names bound by a pattern or listed on the left: `a, b := f()`,
        // `for k, v in d`, `const { a, b } = x`, `let (a, b) = t`
        for field in ["left", "name", "pattern", "declarator", "alias", "key", "value_name"] {
            // (every one of them: Go `var body, expected string`)
            let mut cursor = n.walk();
            let sides: Vec<Node> = n.children_by_field_name(field, &mut cursor).collect();
            for side in sides {
                let mut stack = vec![(side, 0)];
                while let Some((c, depth)) = stack.pop() {
                    if BARE_KINDS.contains(&c.kind())
                        || c.kind() == "shorthand_property_identifier_pattern"
                    {
                        self.declared.push((n.start_byte(), text(c, src)));
                    } else if depth < 4 && !MEMBER_KINDS.contains(&c.kind()) {
                        stack.extend(kids(c).into_iter().map(|x| (x, depth + 1)));
                    }
                }
            }
        }
    }

    /// A bare name in an expression.
    fn bare<'t>(&mut self, n: Node<'t>, src: &[u8], anc: &[Node<'t>]) {
        // Scala `Local(a = 1)`: the name of an argument, no variable
        if crate::parse::current() == Some(crate::lang::Lang::Scala)
            && up(anc, 1).is_some_and(|g| g.kind() == "arguments")
            && up(anc, 0).is_some_and(|p| {
                p.kind() == "assignment_expression"
                    && p.child_by_field_name("left").is_some_and(|l| l.id() == n.id())
            })
        {
            return;
        }
        let Some(p) = up(anc, 0) else { return };
        let pk = p.kind();
        // Kotlin `Command(name = "x")`: the name of an argument, no variable
        if pk == "value_argument"
            && p.child(0).is_some_and(|c| c.id() == n.id())
            && p.child(1).is_some_and(|c| c.kind() == "=")
        {
            return;
        }
        // (a parameter's name; its default value is read like any other)
        let default_value = ["value", "default_value"]
            .iter()
            .any(|f| p.child_by_field_name(f).is_some_and(|v| v.id() == n.id()));
        if pk.contains("parameter") && !default_value {
            self.declared.push((n.start_byte(), text(n, src)));
            return;
        }
        // the key of a Go composite literal (`S{n: 1}`) names a field of the
        // new value, it reads nothing
        if pk == "literal_element"
            && up(anc, 1)
                .is_some_and(|k| k.kind() == "keyed_element" && first_named(k) == Some(p.id()))
        {
            return;
        }
        // (a base named where a type is declared is its base, no value read)
        if pk.contains("heritage")
            || pk.contains("extends")
            || pk.contains("implements")
            || pk.contains("superclass")
            || pk.contains("delegation_specifier")
            || matches!(pk, "base_list" | "base_class_clause" | "super_interfaces")
            || (pk == "argument_list" && up(anc, 1).is_some_and(|c| c.kind() == "class_definition"))
        {
            return;
        }
        // C# `Maybe.Nothing<T>()`, `List<string>`: the name inside a
        // `generic_name` is a generic method's or a type's, no value read
        if pk == "generic_name" {
            return;
        }
        if pk.contains("import")
            || pk.contains("type")
            || pk.contains("label")
            || pk.contains("decorator")
            || pk.contains("annotation")
            || matches!(pk, "global_statement" | "nonlocal_statement" | "keyed_element")
        {
            return;
        }
        // the member part of `a.b`, a declared name, a callee, a key
        for field in [
            "name",
            "property",
            "attribute",
            "field",
            "function",
            "key",
            "label",
            "alias",
            "method",
            "declarator",
            "pattern",
            "suffix",
            // (`new Foo { … }`: the type built, no value read; a JS `new`
            // takes a value, which is read)
            "type",
        ] {
            if p.child_by_field_name(field).is_some_and(|x| x.id() == n.id()) {
                return;
            }
        }
        if pk == "navigation_suffix" || (is_call_kind(pk) && first_named(p) == Some(n.id())) {
            return;
        }
        if MEMBER_KINDS.contains(&pk) && first_named(p) != Some(n.id()) {
            return; // the member part of a shape with no fields
        }
        if is_write_target(n, anc) {
            return;
        }
        let path = vec![bare_name(n, src)];
        if calls_mutator(n, src, anc) {
            self.push(n, RefKind::Write, path.clone(), "mutate");
        }
        // (the object of `a.b` is read on the way to `b`: no value of its own)
        let ctx = if MEMBER_KINDS.contains(&pk) { "object" } else { "read" };
        self.push(n, RefKind::Read, path, ctx);
    }

    /// `a.b` in an expression: read on its own (`Attr`, as a method taken as a
    /// value is), read on the way to something else, or changed in place.
    fn member<'t>(&mut self, n: Node<'t>, src: &[u8], anc: &[Node<'t>]) {
        let Some(path) = member_path(n, src) else { return };
        if path.len() < 2 || path.iter().any(|s| s == UNKNOWN) {
            return;
        }
        let Some(p) = up(anc, 0) else { return };
        if is_callee(p, n) || is_write_target(n, anc) {
            return;
        }
        if p.kind().contains("import") || p.kind().contains("type") {
            return;
        }
        if is_object(p, n) {
            if calls_mutator(n, src, anc) {
                self.push(n, RefKind::Write, path.clone(), "mutate");
            }
            self.push(n, RefKind::Read, path, "read");
        } else {
            self.push(n, RefKind::Attr, path, "property_read");
        }
    }

    /// The uses that are not of a name the enclosing function declared; in a
    /// language where a name from another file has to be imported
    /// (`needs_import`), only the file's own state names and its imports.
    pub fn finish(
        mut self,
        defs: &[Def],
        imports: &[ImportFact],
        needs_import: bool,
        innermost: impl Fn(&[usize]) -> Vec<Option<usize>>,
    ) -> Vec<RefFact> {
        let at = |pairs: &[(usize, String)]| -> Vec<(Option<usize>, String)> {
            let bytes: Vec<usize> = pairs.iter().map(|(b, _)| *b).collect();
            innermost(&bytes).into_iter().zip(pairs.iter().map(|(_, n)| n.clone())).collect()
        };
        // the callable that owns a position, through properties and constants
        let owner = |mut d: Option<usize>| -> Option<usize> {
            while let Some(x) = d {
                if !defs[x].extra {
                    return defs[x].kind.is_callable().then_some(x);
                }
                d = defs[x].parent;
            }
            None
        };
        let outer: HashSet<(usize, String)> =
            at(&self.outer).into_iter().filter_map(|(d, n)| Some((owner(d)?, n))).collect();
        let mut locals: HashMap<usize, HashSet<String>> = HashMap::new();
        for (d, name) in at(&self.declared) {
            if let Some(f) = owner(d) {
                if !outer.contains(&(f, name.clone())) {
                    locals.entry(f).or_default().insert(name);
                }
            }
        }
        let shadowed = |mut d: Option<usize>, name: &str| -> bool {
            while let Some(x) = d {
                if locals.get(&x).is_some_and(|s| s.contains(name)) {
                    return true;
                }
                d = defs[x].parent;
            }
            false
        };
        let mut known: HashSet<&str> = HashSet::new();
        if needs_import {
            // the file's own names (a variable, or a class whose field is written)
            known.extend(defs.iter().map(|d| d.name.as_str()));
            for imp in imports {
                for (name, alias) in &imp.names {
                    known.insert(alias.as_deref().unwrap_or(name.as_str()));
                }
                if let Some(a) = &imp.alias {
                    known.insert(a.as_str());
                }
                known.extend(imp.module.rsplit(['.', '/', ':']).next());
            }
        }
        let bytes: Vec<usize> = self.uses.iter().map(|u| u.byte).collect();
        let homes = innermost(&bytes);
        let mut seen: HashSet<(Option<usize>, RefKind, Vec<String>)> = HashSet::new();
        let mut out = Vec::new();
        for (mut u, home) in std::mem::take(&mut self.uses).into_iter().zip(homes) {
            let head = u.path[0].as_str();
            // the declaration of the variable itself is not a use of it
            if let Some(h) = home {
                if defs[h].extra && defs[h].name == *u.path.last().expect("a path") {
                    continue;
                }
            }
            // a local of the same name hides a variable; `local.field` is the
            // field of the object the local holds, which resolution types
            if u.path.len() == 1 && shadowed(home, head) {
                continue;
            }
            // (or the name of a block the use is written in, unless what the
            // block declares by it is a function: `const f = () => …`)
            if u.path.len() == 1
                && self.blocked.iter().any(|(lo, hi, n)| {
                    n == head
                        && *lo <= u.byte
                        && u.byte < *hi
                        && !defs.iter().any(|d| {
                            d.name == *n
                                && d.kind.is_callable()
                                && *lo <= d.start_byte
                                && d.start_byte < *hi
                        })
                })
            {
                continue;
            }
            if needs_import && !matches!(head, "self" | "super" | "crate") && !known.contains(head)
            {
                continue;
            }
            if !seen.insert((home, u.kind, u.path.clone())) {
                continue;
            }
            u.from_def = None; // assigned with the other references
            out.push(u);
        }
        out
    }
}

/// A bare name as the definitions spell it (`$hits` is `hits`, as
/// `add_extras` strips the sigil).
fn bare_name(n: Node, src: &[u8]) -> String {
    let written = text(n, src);
    // (the sigil is PHP's: a JS / TS `$name` is spelled with it)
    if matches!(
        crate::parse::current(),
        Some(crate::lang::Lang::Php | crate::lang::Lang::Ruby | crate::lang::Lang::Swift)
    ) {
        written.trim_start_matches('$').to_string()
    } else {
        written
    }
}

/// `path_of`, and PHP `self::$total` (whose `$total` `path_of` cannot name).
fn member_path(n: Node, src: &[u8]) -> Option<Vec<String>> {
    if n.kind() == "scoped_property_access_expression" {
        let mut p = path_of(n.child_by_field_name("scope")?, src)?;
        let name = n.child_by_field_name("name")?;
        let inner = kids(name).into_iter().find(|c| c.kind() == "name").unwrap_or(name);
        p.push(text(inner, src).trim_start_matches('$').to_string());
        return Some(p);
    }
    path_of(n, src)
}

/// Scala writes `x += 1` as an infix expression.
fn scala_compound(n: Node, src: &[u8]) -> bool {
    n.child_by_field_name("operator").is_some_and(|o| {
        let t = text(o, src);
        t.len() >= 2 && t.ends_with('=') && !matches!(t.as_str(), "==" | "!=" | "<=" | ">=" | "===")
    })
}

fn first_named(n: Node) -> Option<usize> {
    n.named_child(0).map(|c| c.id())
}

/// `a op= b`, as opposed to `a = b`.
fn is_augmented(n: Node, k: &str, src: &[u8]) -> bool {
    if k.contains("augmented") || k.contains("compound") || k == "operator_assignment" {
        return true;
    }
    let mut cursor = n.walk();
    let op = n.child_by_field_name("operator").or_else(|| {
        n.children(&mut cursor).find(|c| !c.is_named() && text(*c, src).ends_with('='))
    });
    op.is_some_and(|o| {
        let t = text(o, src);
        t != "="
            && t != ":="
            && t.ends_with('=')
            && !matches!(t.as_str(), "==" | "!=" | "<=" | ">=")
    })
}

/// The names and members an assignment target writes.
fn targets<'t>(
    t: Node<'t>,
    src: &[u8],
    ctx: &'static str,
    out: &mut Vec<(Node<'t>, Vec<String>, &'static str)>,
    depth: usize,
) {
    if depth > MAX_CLIMB {
        return;
    }
    let k = t.kind();
    if BARE_KINDS.contains(&k) {
        out.push((t, vec![bare_name(t, src)], ctx));
    } else if MEMBER_KINDS.contains(&k) {
        if let Some(p) = member_path(t, src) {
            out.push((t, p, ctx));
        }
    } else if is_item(k) {
        let base = ["value", "object", "operand", "argument", "expression", "array"]
            .iter()
            .find_map(|f| t.child_by_field_name(f))
            .or_else(|| kids(t).into_iter().next());
        if let Some(b) = base {
            targets(b, src, if ctx == "delete" { ctx } else { "item" }, out, depth + 1);
        }
    } else if k == "directly_assignable_expression" {
        // Kotlin / Swift: a name, `a.b` or `a[i]` spelled as base and suffix
        let ks = kids(t);
        match ks.as_slice() {
            [one] => targets(*one, src, ctx, out, depth + 1),
            [base, .., last] if last.kind() == "navigation_suffix" => {
                if let (Some(mut p), Some(name)) =
                    (path_of(*base, src), kids(*last).into_iter().last())
                {
                    p.push(text(name, src));
                    out.push((t, p, ctx));
                }
            }
            [base, ..] => targets(*base, src, "item", out, depth + 1),
            [] => {}
        }
    } else if TARGET_LISTS.contains(&k) {
        for c in kids(t) {
            targets(c, src, ctx, out, depth + 1);
        }
    }
}

fn is_item(k: &str) -> bool {
    k.contains("subscript")
        || k == "index_expression"
        || k.contains("element_access")
        || k == "element_reference"
        || k == "indexing_expression"
}

/// The `i`-th ancestor (0: the parent) from the walk's stack of ancestors:
/// tree-sitter finds a parent by walking down from the root again.
fn up<'t>(anc: &[Node<'t>], i: usize) -> Option<Node<'t>> {
    anc.len().checked_sub(i + 1).map(|j| anc[j])
}

/// Whether `n` is written: assigned to, updated or deleted.
pub fn writes_to<'t>(n: Node<'t>, anc: &[Node<'t>]) -> bool {
    is_write_target(n, anc)
}

/// Whether `n` is what an assignment, an update or a `del` writes (through any
/// `[…]` around it).
fn is_write_target<'t>(n: Node<'t>, anc: &[Node<'t>]) -> bool {
    let mut cur = n;
    for i in 0..MAX_CLIMB {
        let Some(p) = up(anc, i) else { return false };
        let pk = p.kind();
        if ASSIGNMENTS.contains(&pk) {
            let target = p
                .child_by_field_name("left")
                .or_else(|| p.child_by_field_name("target"))
                .or_else(|| p.named_child(0));
            return target.is_some_and(|t| t.id() == cur.id());
        }
        if UPDATES.contains(&pk) || pk == "delete_statement" {
            return true;
        }
        let through = TARGET_LISTS.contains(&pk)
            || pk == "directly_assignable_expression"
            || (is_item(pk) && p.named_child(0).is_some_and(|c| c.id() == cur.id()));
        if !through {
            return false;
        }
        cur = p;
    }
    false
}

/// `(object, member name)` of a member access or of a call written with its
/// receiver as a field (Java `a.m()`, Ruby `a.m`).
fn member_parts<'t>(p: Node<'t>, src: &[u8]) -> Option<(Node<'t>, String)> {
    let k = p.kind();
    if !(MEMBER_KINDS.contains(&k) || is_call_kind(k)) {
        return None;
    }
    for (obj, member) in MEMBER_FIELDS {
        if let (Some(o), Some(m)) = (p.child_by_field_name(obj), p.child_by_field_name(member)) {
            return Some((o, text(m, src)));
        }
    }
    if k == "navigation_expression" {
        let ks = kids(p);
        let suffix = ks.iter().find(|c| c.kind() == "navigation_suffix")?;
        let name = kids(*suffix).into_iter().last()?;
        return Some((*ks.first()?, text(name, src)));
    }
    if MEMBER_KINDS.contains(&k) {
        let ks = kids(p);
        if ks.len() >= 2 {
            return Some((ks[0], text(*ks.last()?, src)));
        }
    }
    None
}

fn is_object(p: Node, n: Node) -> bool {
    let k = p.kind();
    if !(MEMBER_KINDS.contains(&k) || is_call_kind(k)) {
        return false;
    }
    MEMBER_FIELDS.iter().any(|(obj, member)| {
        p.child_by_field_name(member).is_some()
            && p.child_by_field_name(obj).is_some_and(|o| o.id() == n.id())
    }) || (MEMBER_KINDS.contains(&k) && first_named(p) == Some(n.id()))
}

/// Whether `n` is what call `p` calls.
fn is_callee(p: Node, n: Node) -> bool {
    is_call_kind(p.kind())
        && (p.child_by_field_name("function").is_some_and(|f| f.id() == n.id())
            || (p.child_by_field_name("function").is_none() && first_named(p) == Some(n.id())))
}

/// Whether a method that changes its receiver is called on `n`, directly or
/// further along the chain (`CACHE.lock().unwrap().insert(k, v)`).
fn calls_mutator<'t>(n: Node<'t>, src: &[u8], anc: &[Node<'t>]) -> bool {
    let mut cur = n;
    let mut i = 0;
    for _ in 0..MAX_CLIMB {
        let Some(p) = up(anc, i) else { return false };
        if !is_object(p, cur) {
            return false;
        }
        let Some((_, name)) = member_parts(p, src) else { return false };
        if is_call_kind(p.kind()) {
            // the call node itself carries receiver and method name
            if MUTATORS.contains(&name.as_str()) {
                return true;
            }
            if !PASS_THROUGH.contains(&name.as_str()) {
                return false;
            }
            cur = p;
            i += 1;
            continue;
        }
        let Some(call) = up(anc, i + 1).filter(|pp| is_callee(*pp, p)) else { return false };
        if MUTATORS.contains(&name.as_str()) {
            return true;
        }
        // `request.getHeader(…).replace(…)` changes a string, not `request`
        if !PASS_THROUGH.contains(&name.as_str()) {
            return false;
        }
        cur = call;
        i += 2;
    }
    false
}
