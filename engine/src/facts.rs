//! What one file yields: definitions with their spans, document headings, and
//! (from phase 2 on) imports and references. The graph is assembled from these.

use serde::Serialize;

use crate::lang::Lang;

/// What a definition is. Decides its label (`name()`, `.name()`, bare), the
/// relation that links it to its parent, and whether it becomes a graph node.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum DefKind {
    Class,
    Struct,
    Interface,
    Enum,
    EnumMember,
    Function,
    Method,
    Constructor,
    /// A module that is a definition in its own right (Ruby `module`).
    Module,
    /// A scope that only prefixes names: C++/C#/PHP/TS namespaces, Rust inline
    /// `mod`, packages. No graph node.
    Namespace,
    /// Adds members to a type defined elsewhere (Swift `extension`). No graph
    /// node; its members belong to the type it extends.
    Extension,
    TypeAlias,
    Other,
}

impl DefKind {
    pub fn is_callable(self) -> bool {
        matches!(self, DefKind::Function | DefKind::Method | DefKind::Constructor)
    }

    /// Kinds whose callables are methods and that `_callable_class` marks.
    pub fn is_class_like(self) -> bool {
        matches!(
            self,
            DefKind::Class | DefKind::Struct | DefKind::Interface | DefKind::Enum | DefKind::Module
        )
    }

    pub fn as_str(self) -> &'static str {
        match self {
            DefKind::Class => "class",
            DefKind::Struct => "struct",
            DefKind::Interface => "interface",
            DefKind::Enum => "enum",
            DefKind::EnumMember => "enum_member",
            DefKind::Function => "function",
            DefKind::Method => "method",
            DefKind::Constructor => "constructor",
            DefKind::Module => "module",
            DefKind::Namespace => "namespace",
            DefKind::Extension => "extension",
            DefKind::TypeAlias => "type_alias",
            DefKind::Other => "other",
        }
    }
}

/// One definition. Lines are 1-based and inclusive.
#[derive(Clone, Debug, Serialize)]
pub struct Def {
    /// The bare name (`acquire`, `Get`), never qualified.
    pub name: String,
    /// The dotted chain of enclosing names, exactly as graphlore's `spans.py`
    /// builds it (`Fixed.acquire`, `Client.Get`, `tests.runs`).
    pub qualname: String,
    pub kind: DefKind,
    /// The first line of the definition's region: its leading doc comments,
    /// decorators and attributes included.
    pub region_start: u32,
    /// The line the definition itself starts on (what a node's line points at).
    pub def_line: u32,
    pub end_line: u32,
    /// Index of the lexically enclosing definition in the same `defs` list.
    pub parent: Option<usize>,
    /// The type this definition belongs to when that is not its lexical parent:
    /// a Go receiver, a Rust `impl` target, C++ `Session::Get`, a Swift extension.
    pub owner: Option<String>,
    /// Inside a test region of a source file (Rust `#[cfg(test)]`, `#[test]`).
    pub is_test: bool,
}

/// A document heading (Markdown, reStructuredText).
#[derive(Clone, Debug, Serialize)]
pub struct Heading {
    pub level: u32,
    pub text: String,
    pub line: u32,
    pub end_line: u32,
}

/// An import statement. Filled from phase 2 on.
#[derive(Clone, Debug, Serialize)]
pub struct ImportFact {
    pub line: u32,
    pub module: String,
    pub names: Vec<String>,
    /// `module-level`, `deferred` or `type-checking`.
    pub kind: String,
}

/// A use of a name (call, type reference). Filled from phase 2 on.
#[derive(Clone, Debug, Serialize)]
pub struct RefFact {
    pub line: u32,
    pub name: String,
    pub relation: String,
    /// Index of the enclosing definition, if any.
    pub from_def: Option<usize>,
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct ParseStatus {
    /// The tree has ERROR or MISSING nodes; the definitions found are partial.
    pub has_error: bool,
    /// The parse ran past its deadline and was abandoned.
    pub timed_out: bool,
}

#[derive(Clone, Debug, Serialize)]
pub struct FileFacts {
    /// Root-relative, `/`-separated.
    pub path: String,
    pub lang: Option<Lang>,
    pub is_document: bool,
    pub lines: u32,
    pub defs: Vec<Def>,
    pub headings: Vec<Heading>,
    pub test_regions: Vec<(u32, u32)>,
    pub imports: Vec<ImportFact>,
    pub refs: Vec<RefFact>,
    pub parse: ParseStatus,
}

impl FileFacts {
    pub fn new(path: String, lang: Option<Lang>, is_document: bool, src: &[u8]) -> Self {
        FileFacts {
            path,
            lang,
            is_document,
            lines: count_lines(src),
            defs: Vec::new(),
            headings: Vec::new(),
            test_regions: Vec::new(),
            imports: Vec::new(),
            refs: Vec::new(),
            parse: ParseStatus::default(),
        }
    }
}

/// Lines as tree-sitter and Python's `splitlines` see a file ending in a newline:
/// a trailing newline does not open a new line.
pub fn count_lines(src: &[u8]) -> u32 {
    if src.is_empty() {
        return 0;
    }
    let n = src.iter().filter(|&&b| b == b'\n').count() as u32;
    if src.ends_with(b"\n") {
        n
    } else {
        n + 1
    }
}
