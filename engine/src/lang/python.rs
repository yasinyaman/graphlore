//! Python through tree-sitter-python. graphlore's own span engine reads Python
//! with the stdlib `ast`; this module keeps to what `ast` reports: a region
//! starts at the first decorator (never at a comment above the `def`), it ends on its last line of
//! code (a block's trailing comments are not part of it), and a lambda bound to
//! a name is not a definition.
//!
//! Imports: `import a.b [as c]`, `from [..]m import x [as y]`, `from m import *`;
//! one under `if TYPE_CHECKING:` is type-checking only.

use crate::parse::Fields;
use crate::parse::Kinds;
use tree_sitter::Node;

use super::LanguageModule;
use crate::facts::{DefKind, ImportFact, ImportForm, RefKind, Runs};
use crate::generic::{all_kids, end_line, kids, line, text};
use crate::refs::{import, path_of};

pub struct Python;

/// The end of the last token that is not a comment, as `ast`'s `end_lineno`.
fn code_end(node: Node) -> u32 {
    let mut n = node;
    loop {
        let last = all_kids(n).into_iter().rev().find(|c| c.kind_str() != "comment");
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
        if p.kind_str() == "if_statement" {
            let cond = p.by_field("condition").map(|c| text(c, src)).unwrap_or_default();
            let in_body = p.by_field("consequence").is_some_and(|c| c.id() == child.id());
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

    fn assignment_declares(&self) -> bool {
        true
    }

    fn needs_import(&self) -> bool {
        true
    }

    /// `CACHE = {}` at module level (a variable; `MAX = 3` a constant) and
    /// `count = 0` / `name: str` in a class body (a class attribute).
    fn state_defs<'t>(&self, node: Node<'t>, _src: &[u8]) -> Vec<(Node<'t>, Node<'t>, DefKind)> {
        if node.kind_str() != "assignment" {
            return Vec::new();
        }
        let Some(stmt) = node.parent().filter(|p| p.kind_str() == "expression_statement") else {
            return Vec::new();
        };
        // `self.x = …` in a method: a field of the class
        if let Some(attr) =
            node.by_field("left").filter(|l| l.kind_str() == "attribute").filter(|l| {
                l.by_field("object").is_some_and(|o| {
                    o.kind_str() == "identifier" && &_src[o.byte_range()] == b"self"
                })
            })
        {
            let in_method = {
                let mut p = stmt.parent();
                let mut found = false;
                while let Some(x) = p {
                    if x.kind_str() == "function_definition" {
                        found = x.parent().and_then(|b| b.parent()).is_some_and(|c| {
                            c.kind_str() == "class_definition"
                                || c.parent().is_some_and(|d| d.kind_str() == "class_definition")
                        });
                        break;
                    }
                    p = x.parent();
                }
                found
            };
            return match attr.by_field("attribute") {
                Some(name) if in_method => vec![(node, name, DefKind::Property)],
                _ => Vec::new(),
            };
        }
        let Some(scope) = stmt.parent() else { return Vec::new() };
        let in_class = scope.kind_str() == "block"
            && scope.parent().is_some_and(|c| c.kind_str() == "class_definition");
        if scope.kind_str() != "module" && !in_class {
            return Vec::new();
        }
        let mut out = Vec::new();
        let Some(left) = node.by_field("left") else { return out };
        let names: Vec<Node> = if left.kind_str() == "identifier" {
            vec![left]
        } else if matches!(left.kind_str(), "pattern_list" | "tuple_pattern") {
            kids(left).into_iter().filter(|c| c.kind_str() == "identifier").collect()
        } else {
            Vec::new()
        };
        // the class attributes of an `enum.Enum` subclass are its members
        let enum_class = in_class
            && scope
                .parent()
                .and_then(|c| c.by_field("superclasses"))
                .is_some_and(|bases| is_enum_base_list(bases, _src));
        // `TimeoutTypes = Union[float, Timeout]`, `Scope = MutableMapping[str,
        // Any]`, `T = TypeVar("T")`, `X: TypeAlias = …`: a module-level name
        // for a type, which annotations write
        let alias = !in_class
            && names.len() == 1
            && (node.by_field("type").is_some_and(|t| {
                let text = String::from_utf8_lossy(&_src[t.byte_range()]);
                text.rsplit('.').next() == Some("TypeAlias")
            }) || node.by_field("right").is_some_and(|v| is_type_expression(v, _src)));
        for name in names {
            let kind = if alias {
                DefKind::TypeAlias
            } else if enum_class {
                DefKind::EnumMember
            } else if in_class {
                DefKind::Property
            } else if super::capitals(&String::from_utf8_lossy(&_src[name.byte_range()]))
                && node.by_field("right").is_some_and(|v| {
                    // `CACHE = {}` is written in capitals and changed all the same
                    !matches!(
                        v.kind_str(),
                        "dictionary"
                            | "list"
                            | "set"
                            | "call"
                            | "dictionary_comprehension"
                            | "list_comprehension"
                            | "set_comprehension"
                    )
                })
            {
                DefKind::Constant
            } else {
                DefKind::Variable
            };
            out.push((node, name, kind));
        }
        out
    }

    fn end_line(&self, node: Node) -> u32 {
        code_end(node)
    }

    fn region_start(&self, node: Node, def_line: u32) -> u32 {
        match node.parent() {
            Some(p) if p.kind_str() == "decorated_definition" => line(p).min(def_line),
            _ => def_line,
        }
    }

    fn imports_of(&self, n: Node, src: &[u8]) -> Option<Vec<ImportFact>> {
        let mut out = Vec::new();
        match n.kind_str() {
            "import_statement" => {
                let mut cursor = n.walk();
                for (i, c) in n.children(&mut cursor).enumerate() {
                    if n.field_name_for_child(i as u32) != Some("name") {
                        continue;
                    }
                    let (module, alias) = if c.kind_str() == "aliased_import" {
                        (
                            c.by_field("name").map(|x| dotted(x, src)).unwrap_or_default(),
                            c.by_field("alias").map(|x| text(x, src)),
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
                let module = n.by_field("module_name").map(|m| dotted(m, src)).unwrap_or_default();
                if kids(n).iter().any(|c| c.kind_str() == "wildcard_import") {
                    out.push(import(n, ImportForm::Wildcard, module));
                } else {
                    let mut f = import(n, ImportForm::From, module);
                    let mut cursor = n.walk();
                    for (i, c) in n.children(&mut cursor).enumerate() {
                        if n.field_name_for_child(i as u32) != Some("name") {
                            continue;
                        }
                        if c.kind_str() == "aliased_import" {
                            let name = c.by_field("name").map(|x| dotted(x, src));
                            let alias = c.by_field("alias").map(|x| text(x, src));
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
        if n.kind_str() != "class_definition" {
            return Vec::new();
        }
        let Some(list) = n.by_field("superclasses") else { return Vec::new() };
        kids(list)
            .into_iter()
            .filter(|c| c.kind_str() != "keyword_argument")
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

/// The bases `enum` defines (`Enum`, `enum.IntEnum`, …): a class with one of
/// them is an enumeration (also through a project's own `class Base(Enum)`,
/// which this does not follow).
const ENUM_BASES: &[&str] = &["Enum", "IntEnum", "StrEnum", "Flag", "IntFlag", "ReprEnum"];

fn is_enum_base_list(bases: Node, src: &[u8]) -> bool {
    kids(bases).into_iter().any(|b| {
        let name = match b.kind_str() {
            "identifier" => b,
            "attribute" => match b.by_field("attribute") {
                Some(a) => a,
                None => return false,
            },
            _ => return false,
        };
        ENUM_BASES.contains(&String::from_utf8_lossy(&src[name.byte_range()]).as_ref())
    })
}

/// The generics of `typing` / `collections.abc` whose subscript is a type.
const TYPING_GENERICS: &[&str] = &[
    "Union",
    "Optional",
    "Callable",
    "Dict",
    "List",
    "Tuple",
    "Type",
    "Literal",
    "Mapping",
    "MutableMapping",
    "Sequence",
    "MutableSequence",
    "Iterable",
    "Iterator",
    "AsyncIterable",
    "AsyncIterator",
    "Awaitable",
    "Coroutine",
    "Generator",
    "AsyncGenerator",
    "Set",
    "FrozenSet",
    "Annotated",
    "ClassVar",
    "Final",
];
/// The calls that make a type variable or a distinct type.
const TYPE_MAKERS: &[&str] = &["TypeVar", "NewType", "ParamSpec", "TypeVarTuple"];

/// Whether an assigned value is a type: a `typing` generic subscripted
/// (`Union[…]`, `typing.Callable[…]`), `A | B` of names, subscripts and `None`
/// with a capitalized name or a subscript among them, or a `TypeVar(…)`.
fn is_type_expression(v: Node, src: &[u8]) -> bool {
    let last = |n: Node| -> String {
        let t = String::from_utf8_lossy(&src[n.byte_range()]).to_string();
        t.rsplit('.').next().unwrap_or("").to_string()
    };
    match v.kind_str() {
        "subscript" => v.by_field("value").is_some_and(|x| {
            matches!(x.kind_str(), "identifier" | "attribute")
                && TYPING_GENERICS.contains(&last(x).as_str())
        }),
        "call" => v.by_field("function").is_some_and(|x| {
            matches!(x.kind_str(), "identifier" | "attribute")
                && TYPE_MAKERS.contains(&last(x).as_str())
        }),
        "binary_operator" => {
            let op = v.by_field("operator").map(|o| o.kind_str()) == Some("|");
            let mut typed = false;
            let mut sides = true;
            for side in [v.by_field("left"), v.by_field("right")].into_iter().flatten() {
                match side.kind_str() {
                    "none" => {}
                    "identifier" | "attribute" => {
                        typed |= last(side).chars().next().is_some_and(char::is_uppercase);
                    }
                    "subscript" | "binary_operator" => typed |= is_type_expression(side, src),
                    _ => sides = false,
                }
            }
            op && sides && typed
        }
        _ => false,
    }
}
