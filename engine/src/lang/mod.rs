//! The language registry and the interface a language module implements.
//!
//! Every language goes through the same generic walk (`generic.rs`, a port of
//! graphlore's `spans.py`): a node is a definition when its type carries a hint
//! word and it names itself. A module overrides only what its grammar does
//! differently. Adding a language is: a grammar crate in `Cargo.toml`, a variant
//! of [`Lang`], a row in [`SPECS`], and a module here when the defaults are not
//! enough.

use serde::Serialize;
use tree_sitter::{Language, Node};

use crate::facts::DefKind;
use crate::generic;

mod c_family;
mod csharp;
mod ecma;
mod go;
mod java;
mod kotlin;
mod php;
mod python;
mod ruby;
mod rust;
mod scala;
mod swift;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Lang {
    Python,
    JavaScript,
    TypeScript,
    Tsx,
    Go,
    Java,
    Kotlin,
    Scala,
    CSharp,
    Rust,
    Swift,
    C,
    Cpp,
    Ruby,
    Php,
}

pub struct LangSpec {
    pub lang: Lang,
    /// The name graphlore and tree-sitter-language-pack use for it.
    pub name: &'static str,
    pub extensions: &'static [&'static str],
    pub grammar: fn() -> Language,
}

macro_rules! grammar {
    ($e:expr) => {{
        fn load() -> Language {
            $e.into()
        }
        load
    }};
}

pub static SPECS: &[LangSpec] = &[
    LangSpec {
        lang: Lang::Python,
        name: "python",
        extensions: &["py", "pyi"],
        grammar: grammar!(tree_sitter_python::LANGUAGE),
    },
    LangSpec {
        lang: Lang::JavaScript,
        name: "javascript",
        extensions: &["js", "mjs", "cjs", "jsx"],
        grammar: grammar!(tree_sitter_javascript::LANGUAGE),
    },
    LangSpec {
        lang: Lang::TypeScript,
        name: "typescript",
        extensions: &["ts", "mts", "cts"],
        grammar: grammar!(tree_sitter_typescript::LANGUAGE_TYPESCRIPT),
    },
    LangSpec {
        lang: Lang::Tsx,
        name: "tsx",
        extensions: &["tsx"],
        grammar: grammar!(tree_sitter_typescript::LANGUAGE_TSX),
    },
    LangSpec {
        lang: Lang::Go,
        name: "go",
        extensions: &["go"],
        grammar: grammar!(tree_sitter_go::LANGUAGE),
    },
    LangSpec {
        lang: Lang::Java,
        name: "java",
        extensions: &["java"],
        grammar: grammar!(tree_sitter_java::LANGUAGE),
    },
    LangSpec {
        lang: Lang::Kotlin,
        name: "kotlin",
        extensions: &["kt", "kts"],
        grammar: grammar!(tree_sitter_kotlin_sg::LANGUAGE),
    },
    LangSpec {
        lang: Lang::Scala,
        name: "scala",
        extensions: &["scala", "sc"],
        grammar: grammar!(tree_sitter_scala::LANGUAGE),
    },
    LangSpec {
        lang: Lang::CSharp,
        name: "csharp",
        extensions: &["cs"],
        grammar: grammar!(tree_sitter_c_sharp::LANGUAGE),
    },
    LangSpec {
        lang: Lang::Rust,
        name: "rust",
        extensions: &["rs"],
        grammar: grammar!(tree_sitter_rust::LANGUAGE),
    },
    LangSpec {
        lang: Lang::Swift,
        name: "swift",
        extensions: &["swift"],
        grammar: grammar!(tree_sitter_swift::LANGUAGE),
    },
    LangSpec {
        lang: Lang::C,
        name: "c",
        extensions: &["c", "h"],
        grammar: grammar!(tree_sitter_c::LANGUAGE),
    },
    LangSpec {
        lang: Lang::Cpp,
        name: "cpp",
        extensions: &["cc", "cpp", "cxx", "c++", "hpp", "hxx", "hh", "h++", "ipp", "tpp"],
        grammar: grammar!(tree_sitter_cpp::LANGUAGE),
    },
    LangSpec {
        lang: Lang::Ruby,
        name: "ruby",
        extensions: &["rb", "rake", "gemspec"],
        grammar: grammar!(tree_sitter_ruby::LANGUAGE),
    },
    LangSpec {
        lang: Lang::Php,
        name: "php",
        extensions: &["php"],
        grammar: grammar!(tree_sitter_php::LANGUAGE_PHP),
    },
];

impl Lang {
    pub fn spec(self) -> &'static LangSpec {
        SPECS.iter().find(|s| s.lang == self).expect("every Lang has a spec")
    }

    pub fn name(self) -> &'static str {
        self.spec().name
    }

    pub fn module(self) -> &'static dyn LanguageModule {
        match self {
            Lang::Python => &python::Python,
            Lang::JavaScript | Lang::TypeScript | Lang::Tsx => &ecma::Ecma,
            Lang::Go => &go::Go,
            Lang::Java => &java::Java,
            Lang::Kotlin => &kotlin::Kotlin,
            Lang::Scala => &scala::Scala,
            Lang::CSharp => &csharp::CSharp,
            Lang::Rust => &rust::Rust,
            Lang::Swift => &swift::Swift,
            Lang::C | Lang::Cpp => &c_family::CFamily,
            Lang::Ruby => &ruby::Ruby,
            Lang::Php => &php::Php,
        }
    }

    pub fn from_name(name: &str) -> Option<Lang> {
        SPECS.iter().find(|s| s.name == name).map(|s| s.lang)
    }
}

fn extension(rel: &str) -> Option<String> {
    let base = rel.rsplit('/').next().unwrap_or(rel);
    let dot = base.rfind('.')?;
    if dot == 0 {
        return None; // `.bashrc`
    }
    Some(base[dot + 1..].to_ascii_lowercase())
}

/// The language of a file, from its extension; `src` refines it (a `.h` that
/// reads as C++). `None` for anything no module reads.
pub fn detect(rel: &str, src: Option<&[u8]>) -> Option<Lang> {
    let ext = extension(rel)?;
    let lang = SPECS.iter().find(|s| s.extensions.contains(&ext.as_str()))?.lang;
    if let Some(src) = src {
        if let Some(refined) = lang.module().refine(lang, rel, src) {
            return Some(refined);
        }
    }
    Some(lang)
}

/// What the generic walk makes of one node.
pub enum Step<'t> {
    /// A definition. Its children are walked under `qualname.`.
    Def {
        /// Possibly qualified (`Session.Get`, `Foo::Bar`): split into owner and
        /// bare name by the walk.
        name: String,
        /// The node the span and kind come from.
        node: Node<'t>,
        /// The node whose children are walked next (a bound function's value).
        descend: Node<'t>,
        /// A region start other than the generic one (bound functions use their
        /// own line).
        region_start: Option<u32>,
        /// A non-lexical owner found by the classifier (Go receiver).
        owner: Option<String>,
        /// A kind the classifier already knows (an anonymous class, a bound
        /// function, a C prototype); otherwise [`LanguageModule::kind_of`] decides.
        kind: Option<DefKind>,
    },
    /// No definition of its own, but a prefix for the names inside (Rust `impl`).
    Scope { prefix: String, owner: Option<String>, descend: Node<'t> },
    /// Neither a definition nor walked into (Rust `mod foo;`).
    Skip,
    /// Not a definition: walk its children under the same prefix.
    Descend,
}

/// What a language does differently from the generic walk. Every method has a
/// default; a module overrides what its grammar needs.
pub trait LanguageModule: Send + Sync {
    /// A better language for this file than its extension says (`.h` as C++).
    fn refine(&self, _lang: Lang, _rel: &str, _src: &[u8]) -> Option<Lang> {
        None
    }

    /// Classify one node. The default is the `spans.py` rule.
    fn classify<'t>(&self, node: Node<'t>, src: &[u8]) -> Step<'t> {
        generic::classify(self, node, src)
    }

    /// Whether a node type can be a definition at all.
    fn is_symbol(&self, kind: &str) -> bool {
        generic::is_symbol(kind)
    }

    /// Whether an anonymous function bound to a name (`const f = () => …`) is a
    /// definition.
    fn bound_functions(&self) -> bool {
        true
    }

    /// The first line of a definition's region (doc comments, decorators).
    fn region_start(&self, node: Node, def_line: u32) -> u32 {
        generic::region_start(node, def_line)
    }

    /// The last line of a definition.
    fn end_line(&self, node: Node) -> u32 {
        generic::end_line(node)
    }

    /// What a definition is.
    fn kind_of(&self, node: Node, _src: &[u8]) -> DefKind {
        generic::kind_of(node.kind())
    }

    /// Whether a definition becomes a graph node.
    fn emits_node(&self, kind: DefKind) -> bool {
        !matches!(kind, DefKind::Namespace | DefKind::Extension)
    }

    /// Line ranges that are test code although the file is not a test file.
    fn test_regions(&self, _root: Node, _src: &[u8]) -> Vec<(u32, u32)> {
        Vec::new()
    }
}
