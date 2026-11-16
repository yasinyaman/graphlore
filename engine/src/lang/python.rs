//! Python through tree-sitter-python. graphlore's own span engine reads Python
//! with the stdlib `ast`; this module keeps to what `ast` reports: a region
//! starts at the first decorator (never at a comment above the `def`), it ends on its last line of
//! code (a block's trailing comments are not part of it), and a lambda bound to
//! a name is not a definition.
//!
//! Imports: `import a.b [as c]`, `from [..]m import x [as y]`, `from m import *`;
//! one under `if TYPE_CHECKING:` is type-checking only.

use tree_sitter::Node;

use super::LanguageModule;
use crate::facts::{ImportFact, ImportForm, RefKind, Runs};
use crate::generic::{all_kids, end_line, kids, line, text};
use crate::refs::{import, path_of};

pub struct Python;

/// The end of the last token that is not a comment, as `ast`'s `end_lineno`.
fn code_end(node: Node) -> u32 {
    let mut n = node;
    loop {
        let last = all_kids(n).into_iter().rev().find(|c| c.kind() != "comment");
        match last {
            Some(c) => n = c,
            None => return end_line(n),
        }
    }
}

/// Inside the `if` branch of `if TYPE_CHECKING:` (or `if typing.TYPE_CHECKING:`).
fn under_type_checking(n: Node, src: &[u8]) -> bool {
    let mut child = n;
    while let Some(p) = child.parent() {
        if p.kind() == "if_statement" {
            let cond = p.child_by_field_name("condition").map(|c| text(c, src)).unwrap_or_default();
            let in_body =
                p.child_by_field_name("consequence").is_some_and(|c| c.id() == child.id());
            if cond.trim().ends_with("TYPE_CHECKING") && in_body {
                return true;
            }
        }
        child = p;
    }
    false
}

fn dotted(n: Node, src: &[u8]) -> String {
    text(n, src).split_whitespace().collect()
}

const BUILTINS: &[&str] = &[
    "abs",
    "all",
    "any",
    "ascii",
    "bin",
    "bool",
    "breakpoint",
    "bytearray",
    "bytes",
    "callable",
    "chr",
    "classmethod",
    "compile",
    "complex",
    "delattr",
    "dict",
    "dir",
    "divmod",
    "enumerate",
    "eval",
    "exec",
    "filter",
    "float",
    "format",
    "frozenset",
    "getattr",
    "globals",
    "hasattr",
    "hash",
    "help",
    "hex",
    "id",
    "input",
    "int",
    "isinstance",
    "issubclass",
    "iter",
    "len",
    "list",
    "locals",
    "map",
    "max",
    "memoryview",
    "min",
    "next",
    "object",
    "oct",
    "open",
    "ord",
    "pow",
    "print",
    "property",
    "range",
    "repr",
    "reversed",
    "round",
    "set",
    "setattr",
    "slice",
    "sorted",
    "staticmethod",
    "str",
    "sum",
    "super",
    "tuple",
    "type",
    "vars",
    "zip",
    "__import__",
];

impl LanguageModule for Python {
    fn bound_functions(&self) -> bool {
        false
    }

    fn end_line(&self, node: Node) -> u32 {
        code_end(node)
    }

    fn region_start(&self, node: Node, def_line: u32) -> u32 {
        match node.parent() {
            Some(p) if p.kind() == "decorated_definition" => line(p).min(def_line),
            _ => def_line,
        }
    }

    fn imports_of(&self, n: Node, src: &[u8]) -> Option<Vec<ImportFact>> {
        let mut out = Vec::new();
        match n.kind() {
            "import_statement" => {
                let mut cursor = n.walk();
                for (i, c) in n.children(&mut cursor).enumerate() {
                    if n.field_name_for_child(i as u32) != Some("name") {
                        continue;
                    }
                    let (module, alias) = if c.kind() == "aliased_import" {
                        (
                            c.child_by_field_name("name")
                                .map(|x| dotted(x, src))
                                .unwrap_or_default(),
                            c.child_by_field_name("alias").map(|x| text(x, src)),
                        )
                    } else {
                        (dotted(c, src), None)
                    };
                    let mut f = import(n, ImportForm::Module, module);
                    f.alias = alias;
                    out.push(f);
                }
            }
            "import_from_statement" => {
                let module = n
                    .child_by_field_name("module_name")
                    .map(|m| dotted(m, src))
                    .unwrap_or_default();
                if kids(n).iter().any(|c| c.kind() == "wildcard_import") {
                    out.push(import(n, ImportForm::Wildcard, module));
                } else {
                    let mut f = import(n, ImportForm::From, module);
                    let mut cursor = n.walk();
                    for (i, c) in n.children(&mut cursor).enumerate() {
                        if n.field_name_for_child(i as u32) != Some("name") {
                            continue;
                        }
                        if c.kind() == "aliased_import" {
                            let name = c.child_by_field_name("name").map(|x| dotted(x, src));
                            let alias = c.child_by_field_name("alias").map(|x| text(x, src));
                            if let Some(name) = name {
                                f.names.push((name, alias));
                            }
                        } else {
                            f.names.push((dotted(c, src), None));
                        }
                    }
                    out.push(f);
                }
            }
            "future_import_statement" => return Some(Vec::new()),
            _ => return None,
        }
        if under_type_checking(n, src) {
            for f in out.iter_mut() {
                f.runs = Runs::TypeChecking;
            }
        }
        Some(out)
    }

    fn heritage_of(&self, n: Node, src: &[u8]) -> Vec<(RefKind, Vec<String>)> {
        if n.kind() != "class_definition" {
            return Vec::new();
        }
        let Some(list) = n.child_by_field_name("superclasses") else { return Vec::new() };
        kids(list)
            .into_iter()
            .filter(|c| c.kind() != "keyword_argument")
            .filter_map(|c| path_of(c, src))
            .map(|p| (RefKind::Inherits, p))
            .collect()
    }

    fn is_field(&self, _node: Node) -> bool {
        false
    }

    fn builtins(&self) -> &'static [&'static str] {
        BUILTINS
    }
}
