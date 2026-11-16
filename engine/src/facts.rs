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
    /// The object a function is assigned to as a property (JS `res.send =
    /// function`, `View.prototype.render = …`, `{ get() {} }` in `const app =
    /// {…}`): what `this` means inside it when no class encloses it.
    pub object: Option<String>,
    /// A C/C++ prototype or a method declared in its class: a declaration
    /// whose body is elsewhere.
    pub decl_only: bool,
    /// The tree-sitter node the definition came from, and its byte range:
    /// for attaching references, never serialised.
    #[serde(skip)]
    pub node_id: usize,
    #[serde(skip)]
    pub start_byte: usize,
    #[serde(skip)]
    pub end_byte: usize,
}

/// A document heading (Markdown, reStructuredText).
#[derive(Clone, Debug, Serialize)]
pub struct Heading {
    pub level: u32,
    pub text: String,
    pub line: u32,
    pub end_line: u32,
}

/// When an import runs: what graphlore's `ImportChecker` calls its kind.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize)]
pub enum Runs {
    #[serde(rename = "module-level")]
    ModuleLevel,
    #[serde(rename = "deferred")]
    Deferred,
    #[serde(rename = "type-checking")]
    TypeChecking,
}

impl Runs {
    pub fn as_str(self) -> &'static str {
        match self {
            Runs::ModuleLevel => "module-level",
            Runs::Deferred => "deferred",
            Runs::TypeChecking => "type-checking",
        }
    }
}

/// The shape of an import statement.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ImportForm {
    /// The whole module, bound to `alias` (or to its own first name when
    /// `alias` is `None`): Python `import a.b`, JS `import * as ns`, Go, Swift,
    /// a C `#include`, a Ruby `require`.
    Module,
    /// Names out of `module`: Python `from m import a`, JS named imports, Java
    /// `import a.b.C` (module `a.b`, name `C`), Rust `use a::b::C`, PHP `use`.
    From,
    /// Every public name of `module`: `from m import *`, Java `a.b.*`, Rust
    /// `use m::*`, Kotlin `x.*`, Scala `x._`, C# `using N;`.
    Wildcard,
    /// `export … from` in JS/TS: `names`, or everything when `names` is empty.
    ReExport,
    /// `import()` in JS/TS.
    Dynamic,
}

/// An import statement, as written. One statement can yield several facts
/// (Python `import a, b`; Rust `use a::{b, c}`).
#[derive(Clone, Debug, Serialize)]
pub struct ImportFact {
    pub line: u32,
    pub form: ImportForm,
    /// The module as written, separators kept (`..pkg.mod`, `./m`,
    /// `github.com/x/y`, `crate::a`, `A\B`, `x/y.h`).
    pub module: String,
    /// `(name, alias)` pairs for `From` and `ReExport`; JS default imports are
    /// `("default", local)`.
    pub names: Vec<(String, Option<String>)>,
    pub alias: Option<String>,
    pub runs: Runs,
    /// Java/Kotlin `import static`, C# `using static`.
    pub is_static: bool,
    /// A C `<system>` include, a Ruby `require` (not `require_relative`).
    pub system: bool,
    #[serde(skip)]
    pub byte: usize,
}

/// What a reference is.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RefKind {
    /// A call; a call of a class is a construction.
    Call,
    /// `new X()`, `X { … }`, `X{}`: a construction of the type named.
    New,
    Inherits,
    Implements,
    /// A base in a list that does not say which (C#, Kotlin, Swift): an
    /// interface target makes it `implements`, anything else `inherits`.
    Base,
    MixesIn,
    Embeds,
    /// A type written in a signature or a field.
    Type,
    Decorator,
    /// A function passed as a value: an argument, an element of a collection.
    Indirect,
    /// `obj.name` read without a call: a property, or a method taken as a value.
    Attr,
}

/// A name with a known type: a typed parameter, a typed field, a variable
/// assigned a construction (`x = Foo()`, `let x = Foo::new()`).
#[derive(Clone, Debug, Serialize)]
pub struct Local {
    pub name: String,
    /// The type's path, or the constructor call's (`["Foo", "new"]`).
    pub path: Vec<String>,
    /// Written as the variable's type (parameter, field, `Foo x`), not
    /// guessed from what it is assigned.
    pub declared: bool,
    /// A field of a type (`this.x` / `self.x`), not a local variable.
    pub field: bool,
    pub from_def: Option<usize>,
    #[serde(skip)]
    pub byte: usize,
}

/// A use of a name.
#[derive(Clone, Debug, Serialize)]
pub struct RefFact {
    pub line: u32,
    pub kind: RefKind,
    /// The name as a path: `["g"]`, `["mod", "f"]`, `["self", "n"]`,
    /// `["a", "b", "k"]`. `self` stands for `this`/`self`/`$this`, `super`
    /// for `super`/`base`/`parent`; `()` marks a receiver that is the result
    /// of a call or another expression the walk cannot name.
    pub path: Vec<String>,
    /// `call`, `parameter_type`, `return_type`, `field`, `generic_arg`,
    /// `decorator`, `constructor`, `type`.
    pub context: &'static str,
    /// The innermost definition the use sits in.
    pub from_def: Option<usize>,
    /// The type a use belongs to when no definition encloses it: a Rust
    /// `impl Trait for Type` (the use is `Type`'s).
    pub from_owner: Option<String>,
    #[serde(skip)]
    pub byte: usize,
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
    /// The package or namespace the file declares for all of its contents:
    /// Go `package`, Java/Kotlin/Scala `package`, a C# file-scoped namespace,
    /// a PHP `namespace X;`. Block namespaces are definitions instead.
    pub package: Option<String>,
    pub defs: Vec<Def>,
    pub headings: Vec<Heading>,
    pub test_regions: Vec<(u32, u32)>,
    pub imports: Vec<ImportFact>,
    /// `(local name, exported name)` for names a module exports under another
    /// name or as its default (JS/TS `export { a as b }`, `export default X`,
    /// `module.exports = X`).
    pub exports: Vec<(String, String)>,
    pub refs: Vec<RefFact>,
    pub locals: Vec<Local>,
    pub parse: ParseStatus,
}

impl FileFacts {
    pub fn new(path: String, lang: Option<Lang>, is_document: bool, src: &[u8]) -> Self {
        FileFacts {
            path,
            lang,
            is_document,
            lines: count_lines(src),
            package: None,
            defs: Vec::new(),
            headings: Vec::new(),
            test_regions: Vec::new(),
            imports: Vec::new(),
            exports: Vec::new(),
            refs: Vec::new(),
            locals: Vec::new(),
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
