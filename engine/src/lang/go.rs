//! Go. Methods are named `Receiver.Method` (the generic walk reads the
//! `receiver` field), and a `type_spec` is a struct, an interface or an alias
//! depending on the type it declares. Imports are packages (`import x "path"`;
//! `.` imports every name, `_` none); an anonymous struct field or interface
//! element embeds its type.

use crate::parse::Fields;
use crate::parse::Kinds;
use tree_sitter::Node;

use super::LanguageModule;
use crate::facts::{DefKind, ImportFact, ImportForm, RefKind};
use crate::generic::{self, kids, text};
use crate::refs::{import, path_of, string_value};

pub struct Go;

const BUILTINS: &[&str] = &[
    "append", "cap", "clear", "close", "complex", "copy", "delete", "imag", "len", "make", "max",
    "min", "new", "panic", "print", "println", "real", "recover",
];

fn spec(n: Node, src: &[u8]) -> Option<ImportFact> {
    let path = string_value(n.by_field("path")?, src);
    let mut f = import(n, ImportForm::Module, path);
    if let Some(name) = n.by_field("name") {
        let alias = text(name, src);
        if alias == "." {
            f.form = ImportForm::Wildcard;
        } else {
            f.alias = Some(alias);
        }
    }
    Some(f)
}

impl LanguageModule for Go {
    /// Package-level `var` and `const`: every function of the package sees them.
    fn state_defs<'t>(&self, node: Node<'t>, _src: &[u8]) -> Vec<(Node<'t>, Node<'t>, DefKind)> {
        // a struct's fields
        if node.kind_str() == "field_declaration"
            && node.parent().and_then(|l| l.parent()).is_some_and(|s| s.kind_str() == "struct_type")
        {
            let mut cursor = node.walk();
            return node
                .children_by_field_name("name", &mut cursor)
                .map(|n| (node, n, DefKind::Property))
                .collect();
        }
        let kind = match node.kind_str() {
            "var_spec" => DefKind::Variable,
            "const_spec" => DefKind::Constant,
            _ => return Vec::new(),
        };
        let mut decl = node.parent();
        if decl.is_some_and(|d| d.kind_str() == "var_spec_list") {
            decl = decl.and_then(|d| d.parent());
        }
        if !decl.and_then(|d| d.parent()).is_some_and(|f| f.kind_str() == "source_file") {
            return Vec::new();
        }
        let mut cursor = node.walk();
        node.children_by_field_name("name", &mut cursor).map(|n| (node, n, kind)).collect()
    }

    fn kind_of(&self, node: Node, _src: &[u8]) -> DefKind {
        match node.kind_str() {
            "type_spec" => match node.by_field("type").map(|t| t.kind_str()) {
                Some("struct_type") => DefKind::Struct,
                Some("interface_type") => DefKind::Interface,
                _ => DefKind::TypeAlias,
            },
            "type_alias" => DefKind::TypeAlias,
            k => generic::kind_of(k),
        }
    }

    fn imports_of(&self, n: Node, src: &[u8]) -> Option<Vec<ImportFact>> {
        if n.kind_str() != "import_declaration" {
            return None;
        }
        let mut out = Vec::new();
        for c in kids(n) {
            match c.kind_str() {
                "import_spec" => out.extend(spec(c, src)),
                "import_spec_list" => out.extend(
                    kids(c)
                        .into_iter()
                        .filter(|s| s.kind_str() == "import_spec")
                        .filter_map(|s| spec(s, src)),
                ),
                _ => {}
            }
        }
        Some(out)
    }

    fn package_of(&self, n: Node, src: &[u8]) -> Option<String> {
        (n.kind_str() == "package_clause")
            .then(|| kids(n).into_iter().next().map(|c| text(c, src)))
            .flatten()
    }

    fn heritage_of(&self, n: Node, src: &[u8]) -> Vec<(RefKind, Vec<String>)> {
        if n.kind_str() != "type_spec" {
            return Vec::new();
        }
        let Some(t) = n.by_field("type") else { return Vec::new() };
        let mut out = Vec::new();
        match t.kind_str() {
            "struct_type" => {
                for list in kids(t) {
                    for field in kids(list) {
                        if field.kind_str() == "field_declaration"
                            && field.by_field("name").is_none()
                        {
                            if let Some(p) = field.by_field("type").and_then(|x| path_of(x, src)) {
                                out.push((RefKind::Embeds, p));
                            }
                        }
                    }
                }
            }
            "interface_type" => {
                for e in kids(t) {
                    if matches!(e.kind_str(), "type_elem" | "constraint_elem") {
                        for x in kids(e) {
                            if let Some(p) = path_of(x, src) {
                                out.push((RefKind::Embeds, p));
                            }
                        }
                    }
                }
            }
            _ => {}
        }
        out
    }

    fn is_field(&self, node: Node) -> bool {
        // an anonymous field is an embedding, read by `heritage_of`
        node.kind_str() == "field_declaration" && node.by_field("name").is_some()
    }

    fn builtins(&self) -> &'static [&'static str] {
        BUILTINS
    }
}
