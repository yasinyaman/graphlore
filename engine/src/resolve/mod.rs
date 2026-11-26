//! From the facts of every file to the edges between them: imports, calls,
//! constructions, bases, type references and decorators.
//!
//! A name is looked up the way the language looks it up, in order: the
//! enclosing definitions (and, where methods see their class's members without
//! `this`, the class and its bases), the file's own top level, the names its
//! imports bind, the package or namespace it shares with other files, its
//! wildcard imports. What that finds is `EXTRACTED`. Only when it finds nothing
//! is the name looked up across the project — one candidate, or one left after
//! preferring the files the caller imports, the same directory and the longest
//! common path — and that is `INFERRED`. A name with no candidate, or with
//! several, gets no edge: the build reports how many there were.
//!
//! How a module path names files is the one thing each language does its own
//! way; that lives in `modules.rs`.

use std::cell::RefCell;
use std::collections::{BTreeMap, HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::rc::Rc;

use serde_json::{json, Value};

use crate::facts::{Def, DefKind, FileFacts, ImportForm, ReceiverOf, RefFact, RefKind, Runs};
use crate::graph::assemble::Assembled;
use crate::graph::ids::slug;
use crate::lang::Lang;
use crate::resources::ResourceKind;

mod http;
mod modules;
pub mod reuse;
pub mod trace;

use trace::{FileKeyed, Named, NamedSet, Trace, Traced};

pub type DefRef = (usize, usize);

/// Language families: files of one family can name each other's definitions.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum Family {
    Python,
    Ecma,
    Go,
    Jvm,
    CSharp,
    Rust,
    Php,
    Ruby,
    Swift,
    C,
}

/// Every family, for a question asked of each.
const FAMILIES: [Family; 10] = [
    Family::Python,
    Family::Ecma,
    Family::Go,
    Family::Jvm,
    Family::CSharp,
    Family::Rust,
    Family::Php,
    Family::Ruby,
    Family::Swift,
    Family::C,
];

impl Family {
    pub fn of(lang: Lang) -> Family {
        match lang {
            Lang::Python => Family::Python,
            Lang::JavaScript | Lang::TypeScript | Lang::Tsx => Family::Ecma,
            Lang::Go => Family::Go,
            Lang::Java | Lang::Kotlin | Lang::Scala => Family::Jvm,
            Lang::CSharp => Family::CSharp,
            Lang::Rust => Family::Rust,
            Lang::Php => Family::Php,
            Lang::Ruby => Family::Ruby,
            Lang::Swift => Family::Swift,
            Lang::C | Lang::Cpp => Family::C,
        }
    }

    /// The ecosystem an external name belongs to (part of its node id).
    pub fn ecosystem(self) -> &'static str {
        match self {
            Family::Python => "py",
            Family::Ecma => "npm",
            Family::Go => "go",
            Family::Jvm => "jvm",
            Family::CSharp => "dotnet",
            Family::Rust => "crate",
            Family::Php => "php",
            Family::Ruby => "gem",
            Family::Swift => "swift",
            Family::C => "c",
        }
    }

    /// Whether a method sees its class's members by their bare names.
    /// Whether a name can have several definitions told apart by their
    /// parameters.
    fn overloads(self) -> bool {
        matches!(self, Family::Jvm | Family::CSharp | Family::Swift | Family::C)
    }

    fn implicit_this(self) -> bool {
        matches!(self, Family::Jvm | Family::CSharp | Family::C | Family::Swift | Family::Ruby)
    }

    pub fn name(self) -> &'static str {
        match self {
            Family::Python => "python",
            Family::Ecma => "javascript/typescript",
            Family::Go => "go",
            Family::Jvm => "jvm",
            Family::CSharp => "csharp",
            Family::Rust => "rust",
            Family::Php => "php",
            Family::Ruby => "ruby",
            Family::Swift => "swift",
            Family::C => "c/c++",
        }
    }
}

/// What a name resolves to.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Target {
    Def(DefRef),
    /// A module that is a set of files (a Python or JS module, a Go package,
    /// a header, a Swift module).
    Module(Vec<usize>),
    /// A Python package directory with no `__init__.py`.
    Dir(String),
    /// A JVM package, a C# or PHP namespace (`.`-separated).
    Ns(String),
    /// An external name: the external module's index, then the names reached
    /// through it (`from abc import ABC` is `abc` + `[ABC]`).
    Ext(usize, Vec<String>),
}

/// What an import statement's module names.
#[derive(Clone, Debug)]
pub enum ModRes {
    Target(Target),
    /// The module is external: its label.
    Ext(String),
    /// A relative or project path that names no file, or several.
    Missing,
}

#[derive(Clone, Debug)]
enum Bind {
    T(Target),
    /// A name taken out of a module: looked up when used, so that re-exports
    /// are followed.
    Member(Target, String),
}

/// What the type parameters of a generic type stand for where a path names
/// one of it: `Response<Contact> r` makes `Response`'s `T` `Contact` for
/// `r.body()`. Each argument is a path with the file and scope it is
/// written in.
#[derive(Clone, Debug)]
struct Binds {
    owner: DefRef,
    args: Vec<BoundArg>,
}

/// What a call reference calls, if anything (`Project::call_target`).
type Called = Option<(DefRef, Confidence)>;

/// The walk's per-reference state (`Project::suspend`).
type Suspended = (bool, bool, Option<(usize, u16)>, Option<(usize, usize)>, Option<u8>, bool);

/// A local's written type arguments, their element types, and the
/// definition it is a local of.
type LocalTargs<'a> = (&'a [Vec<String>], &'a [Option<Vec<String>>], usize);

/// What one type parameter stands for: the path written, the element type
/// when that is a collection (`List<Contributor>`), and where it is written.
#[derive(Clone, Debug)]
struct BoundArg {
    path: Vec<String>,
    elem: Option<Vec<String>>,
    file: usize,
    scope: Option<usize>,
}

#[derive(Default)]
struct Scope {
    /// Local name -> what it is bound to, and the import that bound it.
    bindings: HashMap<String, (Bind, usize)>,
    /// Rust: names a `use` in a block binds there only: the block's range,
    /// the name, what it is bound to, the import.
    scoped: Vec<(usize, usize, String, Bind, usize)>,
    /// Wildcard imports, with the import index.
    stars: Vec<(Target, usize)>,
    /// What each import's module resolved to.
    modules: Vec<ModRes>,
    /// Files the imports name (for preferring candidates).
    imported: HashSet<usize>,
}

#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Debug)]
pub enum Confidence {
    Extracted,
    Inferred,
}

impl Confidence {
    fn label(self) -> &'static str {
        match self {
            Confidence::Extracted => "EXTRACTED",
            Confidence::Inferred => "INFERRED",
        }
    }
    fn score(self) -> f64 {
        match self {
            Confidence::Extracted => 1.0,
            Confidence::Inferred => 0.55,
        }
    }
}

/// Members of the strings, collections and promises every language has: a
/// use of one says nothing of what type a value is.
const CORE_MEMBERS: &[&str] = &[
    "append",
    "extend",
    "items",
    "keys",
    "values",
    "get",
    "set",
    "pop",
    "push",
    "length",
    "map",
    "filter",
    "forEach",
    "reduce",
    "then",
    "catch",
    "finally",
    "toString",
    "split",
    "join",
    "strip",
    "lower",
    "upper",
    "format",
    "update",
    "add",
    "remove",
    "find",
    "indexOf",
    "includes",
    "slice",
    "concat",
    "sort",
    "reverse",
    "size",
    "count",
    "isEmpty",
    "contains",
    "equals",
    "hashCode",
    "iterator",
    "next",
    "to_string",
    "clone",
    "unwrap",
    "len",
    "iter",
    "into",
    "as_ref",
    "copy",
    "clear",
    "insert",
    "delete",
    "has",
    "entries",
    "trim",
    "replace",
    "startsWith",
    "endsWith",
    "substring",
    "charAt",
    "toLowerCase",
    "toUpperCase",
    "encode",
    "decode",
    "first",
    "last",
    "isNotEmpty",
    "also",
    "let",
    "apply",
    "run",
    "hash",
    "compareTo",
    "getClass",
    "wait",
    "notify",
    "ToString",
    "Equals",
    "GetHashCode",
    "GetType",
    "Count",
    "Length",
    "Add",
    "Remove",
    "Contains",
    "Select",
    "Where",
    "Any",
    "All",
    "First",
    "Last",
    "startswith",
    "endswith",
    "isdigit",
    "splitlines",
    "setdefault",
    "index",
    "call",
    "bind",
];

/// Whether a member name is one the language's own values answer to.
fn is_core_member(fam: Family, name: &str) -> bool {
    CORE_MEMBERS.contains(&name)
        || match fam {
            Family::Ruby => RUBY_CORE_METHODS.contains(&name),
            Family::Swift => SWIFT_CORE_METHODS.contains(&name),
            _ => false,
        }
}

/// Methods of Ruby's core objects (String, Array, Hash, Object): a project
/// method of one of these names is not what an untyped receiver is asked.
const RUBY_CORE_METHODS: &[&str] = &[
    "dup",
    "clone",
    "shift",
    "unshift",
    "gsub",
    "gsub!",
    "sub",
    "strip",
    "split",
    "join",
    "select",
    "reject",
    "merge",
    "merge!",
    "fetch",
    "freeze",
    "inspect",
    "pretty_inspect",
    "to_s",
    "to_h",
    "to_a",
    "to_sym",
    "to_i",
    "to_f",
    "to_str",
    "to_proc",
    "to_json",
    "keys",
    "values",
    "first",
    "last",
    "empty?",
    "nil?",
    "include?",
    "key?",
    "has_key?",
    "respond_to?",
    "send",
    "public_send",
    "size",
    "length",
    "count",
    "clear",
    "replace",
    "concat",
    "reverse",
    "sort",
    "sort_by",
    "uniq",
    "compact",
    "flatten",
    "detect",
    "any?",
    "all?",
    "none?",
    "start_with?",
    "end_with?",
    "match",
    "match?",
    "downcase",
    "upcase",
    "capitalize",
    "encode",
    "force_encoding",
    "bytes",
    "chars",
    "lines",
    "then",
    "instance_of?",
    "is_a?",
    "kind_of?",
    "equal?",
    "eql?",
    "hash",
    "format",
    "group_by",
    "partition",
    "each_with_object",
    "each_with_index",
    "inject",
    "reduce",
    "slice",
    "take",
    "drop",
    "index",
    "insert",
    "sample",
    "rotate",
    "transform_values",
    "transform_keys",
    "each_key",
    "each_value",
    "each_pair",
    "each_line",
    "store",
    "dig",
    "values_at",
    "collect",
    "find_all",
    "flat_map",
    "zip",
    "tally",
    "succ",
    "round",
    "floor",
    "ceil",
    "times",
    "upto",
    "unpack",
    "pack",
    "scan",
    "squeeze",
    "center",
    "ljust",
    "rjust",
    "prepend",
    "append",
    "to_set",
    "entries",
    "member?",
    "cycle",
    "lazy",
    "instance_variable_get",
    "instance_variable_set",
    "define_method",
    "method",
    "methods",
    "class",
    "frozen?",
    "tap",
];

/// Instance methods of .NET's own strings, collections and types: on a
/// receiver that is not a project type they are the library's before an
/// extension of the same name (`List<T>.ForEach`, `string.Split`).
const CSHARP_CORE_METHODS: &[&str] = &[
    "Split",
    "ForEach",
    "GetProperties",
    "Contains",
    "Find",
    "FindAll",
    "Add",
    "AddRange",
    "Remove",
    "Clear",
    "IndexOf",
    "Insert",
    "CopyTo",
    "ToString",
    "Equals",
    "GetHashCode",
    "GetType",
    "Trim",
    "Replace",
    "Substring",
    "StartsWith",
    "EndsWith",
    "ToLower",
    "ToUpper",
    "CompareTo",
    "Join",
    "Format",
    "GetEnumerator",
    "Dispose",
    "Close",
    "Read",
    "Write",
    "GetBytes",
    "GetString",
];

/// Methods of Rust's standard types (`Mutex::lock`, `Clone::clone`,
/// `io::Error::kind`): what the diagnostics count as a library's name.
const RUST_CORE_METHODS: &[&str] = &[
    "lock",
    "clone",
    "kind",
    "iter",
    "iter_mut",
    "into_iter",
    "get",
    "get_mut",
    "insert",
    "remove",
    "push",
    "pop",
    "len",
    "is_empty",
    "resize",
    "clear",
    "extend",
    "parse",
    "unwrap",
    "expect",
    "map",
    "and_then",
    "as_ref",
    "as_mut",
    "to_string",
    "to_owned",
    "into_owned",
    "read",
    "write",
    "flush",
    "take",
    "replace",
    "contains",
    "split",
    "trim",
    "next",
    // `str` / `String`, `Path`, `Peekable`, `fs::FileType` (rust-analyzer on
    // ripgrep: a guess by these names went to a project method of the name
    // where the receiver was the standard library's)
    "as_str",
    "as_bytes",
    "parent",
    "is_file",
    "is_dir",
    "is_symlink",
    "file_name",
    "peek",
];

/// Rust's primitive types: a path that starts at one is the language's.
const RUST_PRIMITIVES: &[&str] = &[
    "bool", "char", "str", "u8", "u16", "u32", "u64", "u128", "usize", "i8", "i16", "i32", "i64",
    "i128", "isize", "f32", "f64",
];

/// Methods of C++'s standard library (`std::string::c_str`).
const CPP_CORE_METHODS: &[&str] = &[
    "c_str",
    "size",
    "length",
    "empty",
    "begin",
    "end",
    "find",
    "substr",
    "push_back",
    "emplace_back",
    "reserve",
    "data",
    "at",
    "front",
    "back",
    "erase",
    "insert",
    "clear",
    "reset",
    "get",
    "lock",
    "unlock",
    "load",
    "store",
    "count",
    "str",
];

/// Names JavaScript's own arrays, strings, promises and maps answer to, and
/// Node's and the DOM's event emitters and streams: on a receiver the walk
/// cannot type they say nothing about a project method of the same name
/// (`socket.once('close', …)` beside a project `once`).
const ECMA_CORE_METHODS: &[&str] = &[
    "concat",
    "indexOf",
    "lastIndexOf",
    "includes",
    "slice",
    "splice",
    "forEach",
    "filter",
    "map",
    "reduce",
    "reduceRight",
    "some",
    "every",
    "find",
    "findIndex",
    "findLast",
    "findLastIndex",
    "flat",
    "flatMap",
    "join",
    "reverse",
    "sort",
    "shift",
    "unshift",
    "push",
    "pop",
    "fill",
    "keys",
    "values",
    "entries",
    "copyWithin",
    "toSorted",
    "toReversed",
    "toSpliced",
    "charAt",
    "charCodeAt",
    "codePointAt",
    "endsWith",
    "startsWith",
    "localeCompare",
    "match",
    "matchAll",
    "normalize",
    "padEnd",
    "padStart",
    "repeat",
    "replace",
    "replaceAll",
    "search",
    "split",
    "substring",
    "substr",
    "toLowerCase",
    "toUpperCase",
    "trim",
    "trimStart",
    "trimEnd",
    "toString",
    "valueOf",
    "toJSON",
    "toLocaleString",
    "toFixed",
    "hasOwnProperty",
    "isPrototypeOf",
    "apply",
    "call",
    "bind",
    "then",
    "catch",
    "finally",
    "get",
    "set",
    "has",
    "delete",
    "clear",
    "add",
    "next",
    "exec",
    "test",
    "on",
    "once",
    "off",
    "emit",
    "addListener",
    "removeListener",
    "removeAllListeners",
    "listeners",
    "listenerCount",
    "prependListener",
    "prependOnceListener",
    "setMaxListeners",
    "eventNames",
    "pipe",
    "unpipe",
    "write",
    "end",
    "read",
    "destroy",
    "pause",
    "resume",
    "cork",
    "uncork",
    "setEncoding",
    "abort",
    "addEventListener",
    "removeEventListener",
    "dispatchEvent",
    "appendChild",
    "removeChild",
    "querySelector",
    "querySelectorAll",
    "getElementById",
    "getElementsByClassName",
    "getElementsByTagName",
    "setAttribute",
    "getAttribute",
    "removeAttribute",
    "preventDefault",
    "stopPropagation",
    "getContext",
    "getComputedStyle",
    "getBoundingClientRect",
    "focus",
    "blur",
    "click",
    "json",
    "text",
    "arrayBuffer",
    "blob",
    "getTime",
    "toISOString",
];

/// The same for Swift's collections and strings.
const SWIFT_CORE_METHODS: &[&str] = &[
    "sorted",
    "filter",
    "reduce",
    "forEach",
    "contains",
    "append",
    "joined",
    "split",
    "compactMap",
    "flatMap",
    "prefix",
    "suffix",
    "dropFirst",
    "dropLast",
    "reversed",
    "enumerated",
    "firstIndex",
    "lastIndex",
    "lowercased",
    "uppercased",
    "hasPrefix",
    "hasSuffix",
    "replacingOccurrences",
    "trimmingCharacters",
    "components",
    "encode",
    "decode",
    "removeAll",
    "removeFirst",
    "removeLast",
    "allSatisfy",
    "shuffled",
    "randomElement",
    "merging",
    "mapValues",
    "compactMapValues",
    "withUnsafeBytes",
    "resume",
    "cancel",
    "wait",
    "signal",
    "lock",
    "unlock",
    "async",
    "sync",
    "store",
    "value",
    "elementsEqual",
    "starts",
    "index",
    "distance",
    "formIndex",
    "popLast",
    "popFirst",
    "union",
    "intersection",
    "subtracting",
    "isSubset",
    "isSuperset",
    "addingPercentEncoding",
    "data",
    "write",
];

/// Whether a path segment is something of collections: an element, a method
/// that keeps the collection or one that gives an element.
/// What a collection says of itself: its size, whether it is empty. After a
/// name or call declared as a collection (`fn matches(&self) -> &[Match]`),
/// `matches().is_empty()` asks the collection, never an element's method of
/// the name (rust-analyzer on ripgrep: `[T]::is_empty`, not `Match::is_empty`).
const SIZE_METHODS: &[&str] = &["len", "is_empty", "isEmpty", "size", "length", "Count", "Length"];

fn is_collection_step(seg: &str) -> bool {
    seg == "partition"
        || SIZE_METHODS.contains(&seg)
        || NESTING_METHODS.contains(&seg)
        || GROUPING_METHODS.contains(&seg)
        || ASSOCIATING_METHODS.contains(&seg)
        || FLATTENING_METHODS.contains(&seg)
        || REDUCING_METHODS.contains(&seg)
        || is_element_marker(seg)
        || KEEPING_METHODS.contains(&seg)
        || ELEMENT_ACCESSORS.contains(&seg)
        || KEY_LOOKUPS.contains(&seg)
        || PAIRING_METHODS.contains(&seg)
        || MAP_VIEWS.contains(&seg)
}

/// Puts back the file a walk was asked from when the walk ends.
struct Asking<'a>(&'a RefCell<Option<usize>>, Option<usize>);

impl Drop for Asking<'_> {
    fn drop(&mut self) {
        *self.0.borrow_mut() = self.1;
    }
}

/// What a JavaScript function has of its own: a method read with one of
/// these after it is the function, not a getter's value.
const FUNCTION_MEMBERS: &[&str] = &["bind", "call", "apply", "name", "length", "prototype"];

/// `[]`, `[*]`, `[v]`: one of what precedes (see `refs::ELEMENT`).
fn is_element_marker(seg: &str) -> bool {
    seg == crate::refs::ELEMENT
        || seg == crate::refs::ITERATED
        || seg == crate::refs::VALUE
        || seg == crate::refs::KEY
}

/// Methods of a map that take a key and give its value.
const KEY_LOOKUPS: &[&str] = &[
    "get",
    "getValue",
    "getOrDefault",
    "getOrPut",
    "getOrElse",
    "getOrNull",
    "computeIfAbsent",
    "remove",
    "GetValueOrDefault",
    "fetch",
    "get_mut",
];

/// Methods that give each element with its index (a pair).
const PAIRING_METHODS: &[&str] =
    &["enumerate", "withIndex", "enumerated", "each_with_index", "with_index", "Index"];

/// Methods that give lists of consecutive elements.
const NESTING_METHODS: &[&str] = &[
    "chunked",
    "windowed",
    "chunks",
    "windows",
    "chunks_exact",
    "each_slice",
    "each_cons",
    "Chunk",
];

/// Methods that group the elements under a key: a map of lists of them.
const GROUPING_METHODS: &[&str] = &["groupBy", "GroupBy", "group_by", "ToLookup"];

/// Methods that key each element: a map whose values are the elements.
const ASSOCIATING_METHODS: &[&str] =
    &["associateBy", "ToDictionary", "keyBy", "index_by", "indexBy", "uniqueIndex"];

/// Methods that give the elements of a collection of collections.
const FLATTENING_METHODS: &[&str] = &["flatten", "Flatten", "flat", "SelectMany"];

/// Methods that fold the elements into one of them.
const REDUCING_METHODS: &[&str] =
    &["reduce", "reduceOrNull", "Aggregate", "inject", "maxWith", "minWith", "maxWithOrNull"];

/// A map's views: its values (a collection of them), its entries (a map
/// still), its keys (nothing known).
const MAP_VIEWS: &[&str] =
    &["values", "Values", "entries", "entrySet", "items", "keys", "Keys", "keySet", "keys_mut"];

/// Methods of a collection that give a collection of the same elements.
const KEEPING_METHODS: &[&str] = &[
    "filter",
    "filterNot",
    "filterNotNull",
    "sorted",
    "sortedBy",
    "sortedByDescending",
    "sortedWith",
    "sortedDescending",
    "reversed",
    "distinct",
    "distinctBy",
    "take",
    "takeWhile",
    "takeLast",
    "drop",
    "dropWhile",
    "dropLast",
    "toList",
    "toMutableList",
    "toSet",
    "toMutableSet",
    "toTypedArray",
    "asSequence",
    "asIterable",
    "asReversed",
    "onEach",
    "shuffled",
    "subList",
    "slice",
    "stream",
    "parallelStream",
    "collect",
    "toArray",
    "iterator",
    "iter",
    "into_iter",
    "iter_mut",
    "rev",
    "skip",
    "step_by",
    "peekable",
    "cloned",
    "copied",
    "to_vec",
    "Where",
    "OrderBy",
    "OrderByDescending",
    "ThenBy",
    "Take",
    "Skip",
    "Distinct",
    "Reverse",
    "ToList",
    "ToArray",
    "AsEnumerable",
    "AsQueryable",
    "select",
    "reject",
    "sort",
    "sort_by",
    "uniq",
    "compact",
    "reverse",
    "take_while",
    "drop_while",
    "to_a",
    "each",
    "toSorted",
    "toReversed",
    "prefix",
    "suffix",
    "dropFirst",
    "limit",
    "peek",
    "lazy",
    "unmodifiable",
    "asList",
    "toCollection",
    "filterIsInstance",
    "sort_by_key",
    "dedup",
    "freeze",
    "dup",
    "clone",
    "copy",
];

/// Methods of an optional that give what it holds.
const UNWRAPPERS: &[&str] = &[
    "get",
    "unwrap",
    "expect",
    "unwrap_or_default",
    "orElseThrow",
    "orElse",
    "orElseGet",
    "getOrThrow",
    "as_ref",
    "as_mut",
    "cloned",
    "clone",
    "await",
    "Value",
    "Result",
    "lock",
    "try_lock",
    "borrow",
    "borrow_mut",
    "read",
    "write",
    "upgrade",
    "unwrap_or",
    "unwrap_or_else",
    "join",
    "block",
    "getOrNull",
    "value",
    "GetAwaiter",
    "GetResult",
];

/// Methods of a wrapper that give a wrapper of the same value
/// (`res.map_err(…)?`, `opt.filter { … }`, `task.ConfigureAwait(false)`).
const SEALED_KEEPING: &[&str] = &[
    "map_err",
    "ok_or",
    "ok_or_else",
    "ok",
    "or",
    "or_else",
    "filter",
    "as_deref",
    "as_deref_mut",
    "context",
    "with_context",
    "inspect",
    "take",
    "ConfigureAwait",
];

/// Methods of a collection that give one of its elements.
const ELEMENT_ACCESSORS: &[&str] = &[
    "first",
    "last",
    "get",
    "single",
    "next",
    "pop",
    "peek",
    "poll",
    "firstOrNull",
    "lastOrNull",
    "singleOrNull",
    "elementAt",
    "random",
    "getOrNull",
    "removeFirst",
    "removeLast",
    "First",
    "Last",
    "Single",
    "FirstOrDefault",
    "LastOrDefault",
    "SingleOrDefault",
    "ElementAt",
    "shift",
    "sample",
    "front",
    "back",
    "at",
    "removeAt",
    "min",
    "max",
    "find",
    "find_by",
    "detect",
    "Find",
    "findLast",
    "minBy",
    "maxBy",
    "minByOrNull",
    "maxByOrNull",
    "min_by",
    "max_by",
    "min_by_key",
    "max_by_key",
    "nth",
    "findFirst",
    "findAny",
    "MinBy",
    "MaxBy",
];

/// Words in the name of a generic function that returns something built
/// around the type it is given, not a value of it.
const WRAPPERS_OF_WHAT_THEY_ARE_GIVEN: &[&str] = &[
    "list",
    "set",
    "map",
    "array",
    "seq",
    "flow",
    "collection",
    "optional",
    "option",
    "task",
    "future",
    "channel",
    "lazy",
    "provider",
    "empty",
    "result",
    "async",
    "enumerable",
    "vec",
    "iterator",
    "stream",
    "observable",
    "deferred",
    "state",
    "single",
    "some",
    "many",
    "all",
];

/// What a built-in type name takes.
#[derive(Clone, Copy, PartialEq, Eq)]
enum Prim {
    Str,
    Num,
    Bool,
    /// A class handed over as a value: `Class<T>`, `KClass<T>`, `Type`.
    Class,
    /// Something to call: a function type, `Runnable`, `Action`, `Func`.
    Func,
    /// Anything: `Any`, `Object`, a type parameter.
    Any,
}

/// The kind of value a parameter type named `name` takes, when the name is
/// one every language's library has; `None` for a name the project may define.
fn primitive(name: &str) -> Option<Prim> {
    Some(match name {
        "String" | "CharSequence" | "str" | "string" | "Substring" | "NSString"
        | "StringBuilder" | "Char" | "char" | "Character" => Prim::Str,
        "Int" | "Long" | "Short" | "Byte" | "Double" | "Float" | "int" | "long" | "short"
        | "byte" | "double" | "float" | "Integer" | "Number" | "UInt" | "ULong" | "Int32"
        | "Int64" | "UInt32" | "UInt64" | "decimal" | "uint" | "ulong" | "number" | "usize"
        | "isize" | "i32" | "i64" | "u32" | "u64" | "u8" | "f32" | "f64" | "CGFloat"
        | "TimeInterval" | "BigDecimal" | "BigInteger" | "size_t" => Prim::Num,
        "Boolean" | "boolean" | "bool" | "Bool" => Prim::Bool,
        "Class" | "KClass" | "Type" | "KType" => Prim::Class,
        "Function" | "Runnable" | "Callable" | "Supplier" | "Consumer" | "Predicate" | "Action"
        | "Func" | "BiFunction" | "BiConsumer" | "Closure" | "Function0" | "Function1"
        | "Function2" => Prim::Func,
        "Any" | "Object" | "object" | "dynamic" | "any" | "unknown" | "AnyObject" | "id"
        | "Comparable" | "Serializable" | "var" | "auto" => Prim::Any,
        // a type parameter by convention: `T`, `R`, `E`, `TResult`
        n if n.len() == 1 && n.chars().all(|c| c.is_ascii_uppercase()) => Prim::Any,
        _ => return None,
    })
}

/// `(definition, name)` -> the member names used on that name there.
type UsedOn = HashMap<(usize, String), Vec<(String, bool)>>;

/// A type worked out for something, and how surely.
type Typed = Option<(DefRef, Confidence)>;

struct ExtNode {
    label: String,
    eco: &'static str,
}

/// Counts per family for the build report.
#[derive(Default, Clone, serde::Serialize)]
pub struct ResolveStats {
    pub edges: BTreeMap<String, usize>,
    pub inferred: usize,
    pub unresolved: usize,
    pub unresolved_imports: usize,
}

const COMMON_METHODS: &[&str] = &[
    "new",
    "get",
    "set",
    "run",
    "init",
    "close",
    "open",
    "read",
    "write",
    "add",
    "remove",
    "delete",
    "update",
    "find",
    "call",
    "apply",
    "map",
    "filter",
    "push",
    "pop",
    "len",
    "size",
    "length",
    "start",
    "stop",
    "send",
    "handle",
    "process",
    "execute",
    "build",
    "create",
    "parse",
    "load",
    "save",
    "reset",
    "clear",
    "contains",
    "equals",
    "hash",
    "to_s",
    "toString",
    "String",
    "value",
    "values",
    "keys",
    "items",
    "next",
    "append",
    "insert",
    "list",
    "join",
    "split",
    "format",
    "copy",
    "clone",
    "flush",
    "wait",
    "then",
    "on",
    "emit",
    "log",
    "debug",
    "info",
    "warn",
    "error",
    "fetch",
    "put",
    "post",
    "patch",
    "head",
    "options",
    "use",
    "render",
    "toJSON",
    "json",
    "text",
    "data",
    "name",
    "id",
    "type",
    "is_empty",
    "isEmpty",
    "iter",
    "into",
    "from",
    "as_ref",
    "unwrap",
    "expect",
    "describe",
    "validate",
    "configure",
    "setup",
    "teardown",
    "main",
    "test",
    "check",
    "dispose",
    "compare",
    "compareTo",
    "hashCode",
    "Equals",
    "GetHashCode",
    "ToString",
    "Dispose",
    "Add",
    "Remove",
    "Get",
    "Set",
    "Contains",
    "Clear",
    "__init__",
    "__call__",
    "__str__",
    "__repr__",
    "__eq__",
    "__hash__",
    "__enter__",
    "__exit__",
    "invoke",
    "resolve",
    "reject",
    "catch",
    "finally",
    "match",
    "replace",
    "trim",
    "lower",
    "upper",
    "encode",
    "decode",
    "count",
    "sum",
    "max",
    "min",
    "sort",
    "reverse",
    "first",
    "last",
    "each",
];

/// A cache of one file's resolution, emptied at each file (`forget`): each
/// value with where it was worked out (`Trace::valid`).
type Memo<K, V> = RefCell<HashMap<K, (V, u32)>>;
/// A cache kept across files: each value with what working it out read.
type KeptMemo<K, V> = RefCell<HashMap<K, (V, Rc<trace::Frame>)>>;
/// Per file: the lexical children of each definition by bare name.
type Children = Vec<HashMap<usize, HashMap<String, Vec<usize>>>>;

pub struct Project<'a> {
    pub root: PathBuf,
    pub files: Traced<&'a [FileFacts]>,
    pub by_path: Named<&'a str, usize>,
    pub family: Vec<Option<Family>>,
    pub manifests: Vec<(String, PathBuf)>,
    /// Per file: the workspace repository it is in (`""`: none, or the
    /// project is one repository).
    pub repo: Vec<String>,
    repos: Vec<String>,
    hubs: Traced<&'a [String]>,
    def_ids: Traced<&'a [Vec<Option<String>>]>,
    /// Per file: top-level definitions (no definition encloses them, or only
    /// namespaces) by bare name.
    top: Traced<Vec<HashMap<String, Vec<usize>>>>,
    /// Per file: lexical children of each definition by bare name.
    children: Traced<Children>,
    /// Every definition by bare name.
    by_name: Named<String, Vec<DefRef>>,
    /// Extension functions and methods (`Def::receiver`), by name.
    extensions: Named<String, Vec<DefRef>>,
    /// Every definition by qualname.
    by_qual: Named<String, Vec<DefRef>>,
    /// Package-qualified names (`a.b.C`, `Shop.Models.Item`) of the families
    /// that have packages or namespaces.
    fqn: Named<String, Vec<DefRef>>,
    /// Package / namespace names that exist, per family.
    namespaces: NamedSet<(Family, String)>,
    /// Package / namespace / module -> files.
    pkg_files: Named<(Family, String), Vec<usize>>,
    /// `(family, owner type name)` -> methods attached by owner (Go receivers,
    /// Rust impls, C++ `A::m`, Swift extensions).
    owner_methods: Named<(Family, String), Vec<DefRef>>,
    modsys: modules::ModuleSystems,
    scopes: Traced<Vec<Scope>>,
    exts: RefCell<Vec<ExtNode>>,
    ext_index: RefCell<HashMap<(&'static str, String), usize>>,
    bases_memo: KeptMemo<DefRef, Vec<DefRef>>,
    /// `(file, definition, name)` -> typed local / parameter.
    locals: FileKeyed<(usize, usize, String), usize>,
    /// The locals of one name in one definition, in the order they are
    /// declared, where there are several (Rust `let r = a(); let r = b();`).
    shadows: FileKeyed<(usize, usize, String), Vec<usize>>,
    /// type -> field name -> the field's local (its declared or assigned type).
    fields: FileKeyed<DefRef, HashMap<String, (usize, usize)>>,
    depth: RefCell<usize>,
    /// Set while a type reference is resolved: names then mean types only
    /// (`Item Item { get; set; }`: the property's type, not the property).
    types_only: RefCell<bool>,
    /// The path being resolved is the callee of a call.
    calling: RefCell<bool>,
    /// The receiver lambda the use being resolved sits in: `(file, index)`.
    recv: RefCell<Option<(usize, u16)>>,
    /// Names some type answers to without defining them (`FileFacts::attrs`).
    attrs: NamedSet<(Family, String)>,
    /// The types that declare a name without defining it (`Def::attrs`).
    attr_owners: Named<(Family, String), Vec<DefRef>>,
    /// Per file: the member names used on each name, per definition.
    used_on: RefCell<HashMap<usize, Rc<UsedOn>>>,
    /// What `structural_type` found for a local: `(file, definition, name)`.
    structural: Memo<(usize, usize, String), Option<DefRef>>,
    /// The types of locals already worked out: `(file, index of the local)`.
    local_types: Memo<(usize, usize), Typed>,
    /// Whether a local assigned a value holds it in a sealed wrapper
    /// (`let r = load();` with `fn load() -> Result<Foo, E>`), per local.
    sealed_locals: Memo<(usize, usize), bool>,
    /// How many parts the tuple a local's value is has (0: no tuple), per
    /// local.
    tuple_locals: Memo<(usize, usize), usize>,
    /// What a call reference calls, with its overload picked, per reference
    /// (`call_target`): what an implicit member in its arguments is typed by.
    call_targets: Memo<(usize, usize), Called>,
    /// Whether the reference being resolved was answered by a name alone
    /// (`fallback_method`, a single extension in sight): no scope said so.
    guessed: std::cell::Cell<bool>,
    /// The Go type an embedded field's name was taken for (`member`): a
    /// read that ends there is of the field, which has no node
    embedded_hit: std::cell::Cell<Option<DefRef>>,
    /// The bindings a local's value gives, per local (`local_value_binds`).
    value_binds: Memo<(usize, usize), Option<Binds>>,
    /// The bindings the last path walked ended with.
    end_binds: RefCell<Option<Binds>>,
    /// A local's value is being typed: the method its path ends with is what
    /// gives the type (`let p = self.pool.lock().unwrap()`), not a call to
    /// link, so a library's method at the end still types it.
    typing_value: RefCell<bool>,
    /// The locals whose type is being worked out: `(file, index of the local)`.
    typing: RefCell<HashSet<(usize, usize)>>,
    /// The use being resolved, when it is a call: `(file, index of the use)`.
    cur: RefCell<Option<(usize, usize)>>,
    /// Where the use being resolved is written, whatever its kind: `(file,
    /// byte)`. What a name means depends on it (a block's local, a
    /// redeclaration).
    site: RefCell<Option<(usize, usize)>>,
    /// What the reference being resolved is (a type written, a call, …).
    ref_kind: RefCell<Option<RefKind>>,
    /// The file the path being walked is written in: of several blocks of
    /// one namespace, its own and those it includes come first.
    asking: RefCell<Option<usize>>,
    /// Go: each type's aliases (`go_aliases`), worked out on first use.
    go_alias_map: RefCell<Option<HashMap<DefRef, Vec<DefRef>>>>,
    /// How many arguments the call being resolved passes.
    args: RefCell<Option<u8>>,
    /// One of them is `this`.
    self_arg: RefCell<bool>,
    /// The receiver type of each such lambda, once worked out.
    recv_types: Memo<(usize, u16), Typed>,
    /// Import -> files a resolved use went through it to.
    used: RefCell<HashMap<(usize, usize), HashSet<usize>>>,
    /// What resolution reads, frame by frame (`trace.rs`).
    pub trace: Rc<Trace>,
    /// Tables of the whole project that a lookup would otherwise scan every
    /// file for (`index_scans`), each asked by key: the definitions with
    /// `Def::object` by (object, name); what `Object.create(X.prototype)`
    /// builds, by X (file, object); the files registering a Mongoose model;
    /// the request properties project code assigns; the files including RSpec
    /// shared examples, by name (file, line); the modules `config.include`
    /// names; the PHP classes by the last name of a written base.
    object_members: Named<(String, String), Vec<DefRef>>,
    created_from: Named<String, Vec<(usize, String)>>,
    model_files: Named<String, Vec<usize>>,
    request_props: NamedSet<String>,
    example_includes: Named<String, Vec<(usize, u32)>>,
    spec_includes: Named<String, Vec<Vec<String>>>,
    php_bases: Named<String, Vec<DefRef>>,
    /// The files with a top-level definition of a name, in file order
    /// (`top_in` reads only those), and the Go files of each (directory,
    /// package).
    top_files: Named<String, Vec<usize>>,
    go_pkg: Named<(String, String), Vec<usize>>,
}

impl trace::KeyText for Family {
    fn feed(&self, h: &mut trace::Fnv) {
        h.write(self.name().as_bytes());
    }
}

/// The key of a qualified name in `by_qual`: Ruby writes one name both ways
/// (`class Far::Compact`, and `class Compact` inside `module Far`).
fn qual_key(fam: Family, qualname: &str) -> String {
    if fam == Family::Ruby {
        qualname.replace("::", ".")
    } else {
        qualname.to_string()
    }
}

/// The definition around definition `x` of file `f`: where what its
/// signature writes is looked up.
fn defs_parent(files: &Traced<&[FileFacts]>, f: usize, x: usize) -> Option<usize> {
    files[f].defs[x].parent
}

fn is_top(defs: &[Def], d: usize) -> bool {
    let mut p = defs[d].parent;
    while let Some(x) = p {
        if defs[x].kind != DefKind::Namespace {
            return false;
        }
        p = defs[x].parent;
    }
    true
}

pub fn dir_of(rel: &str) -> &str {
    rel.rfind('/').map(|i| &rel[..i]).unwrap_or("")
}

/// `a/./b/../c` -> `a/c`; `None` when it climbs above the root.
pub fn normalize(path: &str) -> Option<String> {
    let mut parts: Vec<&str> = Vec::new();
    for p in path.split('/') {
        match p {
            "" | "." => {}
            ".." => {
                parts.pop()?;
            }
            x => parts.push(x),
        }
    }
    Some(parts.join("/"))
}

impl<'a> Project<'a> {
    /// Forget what was worked out for the file resolved before: what a
    /// file's references resolve to is then worked out from what it reads
    /// alone, and a cached answer cannot hide another file's reads from it
    /// (`trace.rs`). Kept across files: the bases of each type, which save
    /// most of the time a cache across files saves (their frames are
    /// flattened, so they hold no other file's work), Go's aliases (one key)
    /// and the member names used in each file (a read of that file only).
    fn forget(&self) {
        self.structural.borrow_mut().clear();
        self.local_types.borrow_mut().clear();
        self.sealed_locals.borrow_mut().clear();
        self.tuple_locals.borrow_mut().clear();
        self.call_targets.borrow_mut().clear();
        self.value_binds.borrow_mut().clear();
        self.recv_types.borrow_mut().clear();
    }

    pub fn new(
        root: &Path,
        files: &'a [FileFacts],
        asm: &'a Assembled,
        manifests: Vec<(String, PathBuf)>,
        repos: &[String],
    ) -> Project<'a> {
        let trace = Rc::new(Trace::default());
        let mut by_path = Named::new("by_path", trace.clone());
        by_path.raw_mut().extend(files.iter().enumerate().map(|(i, f)| (f.path.as_str(), i)));
        let family: Vec<Option<Family>> = files.iter().map(|f| f.lang.map(Family::of)).collect();
        let tr = || trace.clone();
        let mut p = Project {
            root: root.to_path_buf(),
            files: Traced::new(files, tr()),
            by_path,
            family,
            manifests,
            repo: files
                .iter()
                .map(|f| {
                    repos
                        .iter()
                        .filter(|r| f.path.starts_with(&format!("{r}/")))
                        .max_by_key(|r| r.len())
                        .cloned()
                        .unwrap_or_default()
                })
                .collect(),
            repos: repos.to_vec(),
            hubs: Traced::new(&asm.hubs, tr()),
            def_ids: Traced::new(&asm.def_ids, tr()),
            top: Traced::new(Vec::new(), tr()),
            children: Traced::new(Vec::new(), tr()),
            by_name: Named::new("by_name", tr()),
            extensions: Named::new("extensions", tr()),
            by_qual: Named::new("by_qual", tr()),
            fqn: Named::new("fqn", tr()),
            namespaces: NamedSet::new("namespaces", tr()),
            pkg_files: Named::new("pkg_files", tr()),
            owner_methods: Named::new("owner_methods", tr()),
            modsys: modules::ModuleSystems::default(),
            scopes: Traced::new(Vec::new(), tr()),
            exts: RefCell::new(Vec::new()),
            ext_index: RefCell::new(HashMap::new()),
            bases_memo: RefCell::new(HashMap::new()),
            locals: FileKeyed::new(tr()),
            shadows: FileKeyed::new(tr()),
            fields: FileKeyed::new(tr()),
            depth: RefCell::new(0),
            types_only: RefCell::new(false),
            calling: RefCell::new(false),
            recv: RefCell::new(None),
            attrs: NamedSet::new("attrs", tr()),
            attr_owners: Named::new("attr_owners", tr()),
            used_on: RefCell::new(HashMap::new()),
            structural: RefCell::new(HashMap::new()),
            local_types: RefCell::new(HashMap::new()),
            sealed_locals: RefCell::new(HashMap::new()),
            tuple_locals: RefCell::new(HashMap::new()),
            call_targets: RefCell::new(HashMap::new()),
            guessed: std::cell::Cell::new(false),
            embedded_hit: std::cell::Cell::new(None),
            value_binds: RefCell::new(HashMap::new()),
            end_binds: RefCell::new(None),
            typing_value: RefCell::new(false),
            typing: RefCell::new(HashSet::new()),
            cur: RefCell::new(None),
            site: RefCell::new(None),
            ref_kind: RefCell::new(None),
            asking: RefCell::new(None),
            go_alias_map: RefCell::new(None),
            args: RefCell::new(None),
            self_arg: RefCell::new(false),
            recv_types: RefCell::new(HashMap::new()),
            used: RefCell::new(HashMap::new()),
            trace: trace.clone(),
            object_members: Named::new("object_members", tr()),
            created_from: Named::new("created_from", tr()),
            model_files: Named::new("model_files", tr()),
            request_props: NamedSet::new("request_props", tr()),
            example_includes: Named::new("example_includes", tr()),
            spec_includes: Named::new("spec_includes", tr()),
            php_bases: Named::new("php_bases", tr()),
            top_files: Named::new("top_files", tr()),
            go_pkg: Named::new("go_pkg", tr()),
        };
        p.index();
        p.index_locals();
        p.index_scans();
        p.modsys = modules::ModuleSystems::build(&p);
        *p.scopes.raw_mut() = (0..files.len()).map(|f| p.scope_of(f)).collect();
        // the files a file's imported names live in (`use A\B\C`, `import a.b.C`)
        for f in 0..files.len() {
            let mut more = Vec::new();
            let scoped = p.scopes[f].scoped.iter().map(|(_, _, _, b, _)| b);
            for b in p.scopes[f].bindings.values().map(|(b, _)| b).chain(scoped) {
                if let Some(Target::Def((g, _))) = p.eval(b, 2) {
                    more.push(g);
                }
            }
            p.scopes.raw_mut()[f].imported.extend(more);
        }
        // Rust: what a module star-imported (`use crate::prelude::*;`)
        // passes on with `pub use crate::error::Error;`. Only a star import:
        // a crate root re-exports most of the crate, and a file that names
        // one thing out of it does not see the rest
        // C# `global using N;`: a namespace every file under the directory
        // of the file that writes it has open (the project it is compiled in)
        let mut global: Vec<(String, Target)> = Vec::new();
        for (g, file) in files.iter().enumerate() {
            if p.family[g] != Some(Family::CSharp) {
                continue;
            }
            let dir = match file.path.rfind('/') {
                Some(i) => file.path[..=i].to_string(),
                None => String::new(),
            };
            for (star, i) in &p.scopes[g].stars {
                let written = file.imports.get(*i).is_some_and(|imp| imp.public);
                if written && !matches!(star, Target::Ext(..)) {
                    global.push((dir.clone(), star.clone()));
                }
            }
        }
        if !global.is_empty() {
            for (f, file) in files.iter().enumerate() {
                if p.family[f] != Some(Family::CSharp) {
                    continue;
                }
                for (dir, star) in &global {
                    if file.path.starts_with(dir.as_str()) {
                        if let Target::Module(fs) = star {
                            p.scopes.raw_mut()[f].imported.extend(fs.iter().copied());
                        }
                        // (no import of this file's to count the use on)
                        p.scopes.raw_mut()[f].stars.push((star.clone(), usize::MAX));
                    }
                }
            }
        }
        let public: Vec<Vec<usize>> = (0..files.len()).map(|g| p.rust_public(g)).collect();
        for f in 0..files.len() {
            if p.family[f] != Some(Family::Rust) {
                continue;
            }
            let mut more = Vec::new();
            for (star, _) in &p.scopes[f].stars {
                if let Target::Module(fs) = star {
                    for &g in fs {
                        more.extend(public[g].iter().copied().filter(|&h| h != f));
                    }
                }
            }
            p.scopes.raw_mut()[f].imported.extend(more);
        }
        // … and the files a module it imports passes on (`module.exports =
        // require('./lib/app')`, `exports.response = res`, `export * from`):
        // a package's entry stands for the files it is made of
        let passes: Vec<Vec<usize>> = (0..files.len()).map(|g| p.passes_on(g)).collect();
        if passes.iter().any(|x| !x.is_empty()) {
            for (f, scope) in p.scopes.raw_mut().iter_mut().enumerate() {
                let mut seen: HashSet<usize> = HashSet::new();
                let mut frontier: Vec<usize> = scope.imported.iter().copied().collect();
                for _ in 0..3 {
                    let mut next = Vec::new();
                    for g in frontier {
                        for &h in &passes[g] {
                            if h != f && seen.insert(h) {
                                next.push(h);
                            }
                        }
                    }
                    frontier = next;
                    if frontier.is_empty() {
                        break;
                    }
                }
                scope.imported.extend(seen);
            }
        }
        // Go's aliases, worked out once before any file's frame is open
        if p.family.contains(&Some(Family::Go)) {
            p.go_aliases((0, 0));
        }
        p
    }

    /// The files a Rust module's `pub use` names are in.
    fn rust_public(&self, g: usize) -> Vec<usize> {
        let mut out = Vec::new();
        let s = &self.scopes[g];
        for (i, imp) in self.files[g].imports.iter().enumerate() {
            if !imp.public {
                continue;
            }
            match s.modules.get(i) {
                Some(ModRes::Target(Target::Module(fs))) => out.extend(fs.iter().copied()),
                Some(ModRes::Target(Target::Def((h, _)))) => out.push(*h),
                _ => {}
            }
            for (name, alias) in &imp.names {
                let local = alias.as_deref().unwrap_or(name);
                if let Some((b, _)) = s.bindings.get(local) {
                    if let Some(Target::Def((h, _))) = self.eval(b, 2) {
                        out.push(h);
                    }
                }
            }
        }
        out.retain(|&h| h != g);
        out.sort_unstable();
        out.dedup();
        out
    }

    /// The files module `g` passes on to whoever imports it: the ones it
    /// re-exports whole, and the ones a name it exports was imported from.
    fn passes_on(&self, g: usize) -> Vec<usize> {
        let mut out = Vec::new();
        let s = &self.scopes[g];
        for (i, imp) in self.files[g].imports.iter().enumerate() {
            if imp.form == ImportForm::ReExport {
                if let Some(ModRes::Target(Target::Module(fs))) = s.modules.get(i) {
                    out.extend(fs.iter().copied());
                }
            }
        }
        for (local, _) in &self.files[g].exports {
            if let Some((b, _)) = s.bindings.get(local) {
                match self.eval(b, 2) {
                    Some(Target::Module(fs)) => out.extend(fs),
                    Some(Target::Def((h, _))) => out.push(h),
                    _ => {}
                }
            }
        }
        out.retain(|&h| h != g);
        out.sort_unstable();
        out.dedup();
        out
    }

    fn index(&mut self) {
        for (fi, f) in self.files.iter().enumerate() {
            let mut top: HashMap<String, Vec<usize>> = HashMap::new();
            let mut children: HashMap<usize, HashMap<String, Vec<usize>>> = HashMap::new();
            let fam = self.family[fi];
            if let Some(fam) = fam {
                self.attrs.raw_mut().extend(f.attrs.iter().map(|a| (fam, a.clone())));
            }
            for (di, d) in f.defs.iter().enumerate() {
                if let Some(fam) = fam {
                    for a in &d.attrs {
                        self.attr_owners
                            .raw_mut()
                            .entry((fam, a.clone()))
                            .or_default()
                            .push((fi, di));
                    }
                }
                // a class with no name of its own answers to none, nor a
                // variable the grammar took for a declaration
                if d.anonymous || d.misread || d.block {
                    continue;
                }
                // a method declared for a type (Go receiver, Rust impl, C++
                // `A::m`) is no module-level name
                // C++ `class AsyncWrapper<T, true>`: a specialization answers
                // to the template's name too
                let bare = (fam == Some(Family::C) && d.kind.is_class_like())
                    .then(|| specialized(&d.name))
                    .flatten();
                // (nor is a type an `impl` gives its trait: `type Output = …`)
                let of_owner =
                    d.owner.is_some() && (d.kind.is_callable() || d.kind == DefKind::TypeAlias);
                // (nor a Go struct's field, whatever holds the struct: a field
                // of the anonymous struct a test table is made of)
                let go_field = fam == Some(Family::Go) && d.kind == DefKind::Property;
                if is_top(&f.defs, di) && !of_owner && !go_field {
                    top.entry(d.name.clone()).or_default().push(di);
                    if let Some(b) = bare {
                        top.entry(b.to_string()).or_default().push(di);
                    }
                }
                // (what a module pattern's function returns is a name of
                // the scope its declaration is written in)
                if d.returned_out {
                    match d.parent.and_then(|b| f.defs[b].parent) {
                        Some(outer) => children
                            .entry(outer)
                            .or_default()
                            .entry(d.name.clone())
                            .or_default()
                            .push(di),
                        None => top.entry(d.name.clone()).or_default().push(di),
                    }
                }
                if let Some(par) = d.parent {
                    children.entry(par).or_default().entry(d.name.clone()).or_default().push(di);
                    if let Some(b) = bare {
                        children.entry(par).or_default().entry(b.to_string()).or_default().push(di);
                    }
                }
                let Some(fam) = fam else { continue };
                if let Some(b) = bare {
                    let q = specialized(&d.qualname).unwrap_or(b);
                    self.by_qual.raw_mut().entry(qual_key(fam, q)).or_default().push((fi, di));
                }
                if self.def_ids[fi].get(di).is_some_and(|x| x.is_some()) {
                    self.by_name.raw_mut().entry(d.name.clone()).or_default().push((fi, di));
                }
                // (an RSpec `let` is a name of its block only: the scoped
                // lookup in `resolve_name_any` is its one way in)
                if d.scope_lines.is_none() {
                    self.by_qual
                        .raw_mut()
                        .entry(qual_key(fam, &d.qualname))
                        .or_default()
                        .push((fi, di));
                }
                if d.receiver.is_some() && !d.decl_only {
                    self.extensions.raw_mut().entry(d.name.clone()).or_default().push((fi, di));
                }
                if d.kind == DefKind::Namespace {
                    let ns = d.qualname.replace("::", ".");
                    self.namespaces.raw_mut().insert((fam, ns.clone()));
                    self.pkg_files.raw_mut().entry((fam, ns)).or_default().push(fi);
                }
                if let Some(fq) = self.fqn_of(fi, di) {
                    self.fqn.raw_mut().entry(fq).or_default().push((fi, di));
                }
                if let Some(owner) = &d.owner {
                    if d.kind.is_callable() {
                        let bare = owner.rsplit(['.', ':']).next().unwrap_or(owner).to_string();
                        self.owner_methods.raw_mut().entry((fam, bare)).or_default().push((fi, di));
                    }
                }
            }
            if let (Some(fam), Some(pkg)) = (fam, f.package.as_ref()) {
                let pkg = pkg.replace('\\', ".");
                let mut parts: Vec<&str> = pkg.split('.').collect();
                while !parts.is_empty() {
                    self.namespaces.raw_mut().insert((fam, parts.join(".")));
                    parts.pop();
                }
                self.pkg_files.raw_mut().entry((fam, pkg)).or_default().push(fi);
            }
            self.top.raw_mut().push(top);
            self.children.raw_mut().push(children);
        }
    }

    /// The prints of every key of the project-wide indexes, for
    /// incremental resolution (`reuse.rs`).
    fn key_prints(&self) -> HashMap<u64, u64> {
        let mut out = HashMap::new();
        self.by_path.prints(&mut out);
        self.by_name.prints(&mut out);
        self.extensions.prints(&mut out);
        self.by_qual.prints(&mut out);
        self.fqn.prints(&mut out);
        self.namespaces.prints(&mut out);
        self.pkg_files.prints(&mut out);
        self.owner_methods.prints(&mut out);
        self.attrs.prints(&mut out);
        self.attr_owners.prints(&mut out);
        self.object_members.prints(&mut out);
        self.created_from.prints(&mut out);
        self.model_files.prints(&mut out);
        self.request_props.prints(&mut out);
        self.example_includes.prints(&mut out);
        self.spec_includes.prints(&mut out);
        self.php_bases.prints(&mut out);
        self.top_files.prints(&mut out);
        self.go_pkg.prints(&mut out);
        // the Go aliases of the whole project, one key
        if self.family.contains(&Some(Family::Go)) {
            self.go_aliases((0, 0));
            let mut all: Vec<(DefRef, Vec<DefRef>)> =
                self.go_alias_map.borrow().iter().flatten().map(|(k, v)| (*k, v.clone())).collect();
            all.sort();
            let mut h = trace::Fnv::new();
            for (k, v) in &all {
                use trace::Print;
                k.print(&mut h);
                v.print(&mut h);
            }
            out.insert(trace::key_hash("go_aliases", ""), h.0 | 1);
        }
        out
    }

    /// What other files read of file `f`: its content (`content`, the
    /// facts cache's hash), its scope and its node ids.
    fn file_print(&self, f: usize, content: u64) -> u64 {
        let mut h = trace::Fnv::new();
        h.write(&content.to_le_bytes());
        let target = |t: &Target| match t {
            Target::Ext(e, rest) => {
                let ext = &self.exts.borrow()[*e];
                format!("Ext({}:{} {rest:?})", ext.eco, ext.label)
            }
            t => format!("{t:?}"),
        };
        let bind = |b: &Bind| match b {
            Bind::T(t) => format!("T({})", target(t)),
            Bind::Member(t, n) => format!("M({},{n})", target(t)),
        };
        let scope = &self.scopes.raw()[f];
        let mut names: Vec<(&String, &(Bind, usize))> = scope.bindings.iter().collect();
        names.sort_by(|a, b| a.0.cmp(b.0));
        for (n, (b, i)) in names {
            h.write(format!("b{n}={}#{i}", bind(b)).as_bytes());
            h.sep();
        }
        for (lo, hi, n, b, i) in &scope.scoped {
            h.write(format!("s{lo}-{hi}:{n}={}#{i}", bind(b)).as_bytes());
            h.sep();
        }
        for (t, i) in &scope.stars {
            h.write(format!("*{}#{i}", target(t)).as_bytes());
            h.sep();
        }
        for m in &scope.modules {
            let text = match m {
                ModRes::Target(t) => target(t),
                ModRes::Ext(l) => format!("ext:{l}"),
                ModRes::Missing => "missing".to_string(),
            };
            h.write(text.as_bytes());
            h.sep();
        }
        let mut imported: Vec<usize> = scope.imported.iter().copied().collect();
        imported.sort_unstable();
        h.write(format!("{imported:?}").as_bytes());
        h.write(format!("{:?}", self.def_ids.raw()[f]).as_bytes());
        h.write(self.hubs.raw()[f].as_bytes());
        h.0
    }

    /// No reference being resolved: the per-reference state of the walk as
    /// it is before the first one.
    fn neutral(&self) {
        self.restore((false, false, None, None, None, false));
        *self.site.borrow_mut() = None;
        *self.ref_kind.borrow_mut() = None;
        *self.asking.borrow_mut() = None;
        *self.depth.borrow_mut() = 0;
        self.typing.borrow_mut().clear();
        *self.typing_value.borrow_mut() = false;
        *self.end_binds.borrow_mut() = None;
        self.guessed.set(false);
        self.embedded_hit.set(None);
    }

    /// The Go files of a (directory, package).
    pub(super) fn go_pkg_files(&self, key: &(String, String)) -> &[usize] {
        self.go_pkg.get(key).map(Vec::as_slice).unwrap_or(&[])
    }

    /// The tables `object_members` … `php_bases`, in file order (so that a
    /// lookup lists its candidates in the order a scan of the files did).
    fn index_scans(&mut self) {
        let files = *self.files.raw();
        for (g, top) in self.top.raw().iter().enumerate() {
            for name in top.keys() {
                self.top_files.raw_mut().entry(name.clone()).or_default().push(g);
            }
        }
        for (g, facts) in files.iter().enumerate() {
            if facts.lang == Some(crate::lang::Lang::Go) {
                let dir = facts.path.rsplit_once('/').map(|x| x.0).unwrap_or("").to_string();
                let pkg = facts.package.clone().unwrap_or_default();
                self.go_pkg.raw_mut().entry((dir, pkg)).or_default().push(g);
            }
        }
        for (g, facts) in files.iter().enumerate() {
            for (i, x) in facts.defs.iter().enumerate() {
                if let Some(o) = &x.object {
                    let key = (o.clone(), x.name.clone());
                    self.object_members.raw_mut().entry(key).or_default().push((g, i));
                }
                if let Some(p) = x.created_from.as_deref() {
                    if p.len() >= 2 && p[p.len() - 1] == "prototype" && x.kind.is_state() {
                        let entry = self.created_from.raw_mut().entry(p[p.len() - 2].clone());
                        entry.or_default().push((g, x.name.clone()));
                    }
                }
            }
            for (m, _) in &facts.models {
                let at = self.model_files.raw_mut().entry(m.clone()).or_default();
                if at.last() != Some(&g) {
                    at.push(g);
                }
            }
            self.request_props.raw_mut().extend(facts.request_props.iter().cloned());
            for (n, at) in &facts.example_includes {
                self.example_includes.raw_mut().entry(n.clone()).or_default().push((g, *at));
            }
            if !facts.spec_includes.is_empty() {
                let all = self.spec_includes.raw_mut().entry(String::new()).or_default();
                all.extend(facts.spec_includes.iter().cloned());
            }
            if self.family.get(g).copied().flatten() == Some(Family::Php) {
                for r in &facts.refs {
                    if matches!(r.kind, RefKind::Inherits | RefKind::Base) {
                        if let (Some(last), Some(d)) = (r.path.last(), r.from_def) {
                            self.php_bases.raw_mut().entry(last.clone()).or_default().push((g, d));
                        }
                    }
                }
            }
        }
    }

    fn index_locals(&mut self) {
        let mut locals = HashMap::new();
        let mut shadows: HashMap<(usize, usize, String), Vec<usize>> = HashMap::new();
        let mut fields: HashMap<DefRef, HashMap<String, (usize, usize)>> = HashMap::new();
        for (fi, f) in self.files.iter().enumerate() {
            for (li, l) in f.locals.iter().enumerate() {
                let Some(d) = l.from_def else { continue };
                if l.field {
                    if let Some(c) = self.class_of(fi, Some(d)) {
                        fields.entry(c).or_default().entry(l.name.clone()).or_insert((fi, li));
                    }
                } else {
                    // (of several, the first is the one the index names)
                    locals.entry((fi, d, l.name.clone())).or_insert(li);
                    shadows.entry((fi, d, l.name.clone())).or_default().push(li);
                }
            }
        }
        shadows.retain(|_, v| v.len() > 1);
        for (key, v) in shadows.iter_mut() {
            v.sort_by_key(|&li| (self.files[key.0].locals[li].byte, li));
            // (the one the index names is the first declared)
            locals.insert(key.clone(), v[0]);
        }
        *self.shadows.raw_mut() = shadows;
        *self.locals.raw_mut() = locals;
        *self.fields.raw_mut() = fields;
    }

    /// The type a local path names: a type, or the type a constructor or
    /// factory method belongs to (`Foo::new()`, `Foo.create()`).
    fn type_of_path(
        &self,
        f: usize,
        d: Option<usize>,
        path: &[String],
        declared: bool,
    ) -> Option<DefRef> {
        {
            let mut depth = self.depth.borrow_mut();
            if *depth > 3 {
                return None;
            }
            *depth += 1;
        }
        let before = self.types_only.replace(declared);
        // the path was written somewhere else (a declaration, a signature):
        // the receiver lambda around the use being resolved says nothing of it
        let recv = self.recv.replace(None);
        let out = self.type_of_path_inner(f, d, path).map(|(t, _)| t);
        *self.recv.borrow_mut() = recv;
        *self.types_only.borrow_mut() = before;
        *self.depth.borrow_mut() -= 1;
        out
    }

    fn type_of_path_inner(
        &self,
        f: usize,
        d: Option<usize>,
        path: &[String],
    ) -> Option<(DefRef, Confidence)> {
        // an element of a collection: `xs[]`
        if let Some((last, of)) = path.split_last() {
            if is_element_marker(last) {
                return self.elem_of_as(f, d, of, last);
            }
        }
        // Rust `Self` written in a struct's, enum's or trait's own body is
        // that type (r[names.scopes.self.def-scope])
        // Rust `Self` written in a struct's or enum's own fields is that type
        // (r[names.scopes.self.def-scope])
        if let (Some(x), [one]) = (d, path) {
            // (the reader writes the `Self` type as the word for the object)
            if (one == "Self" || one == "self")
                && self.family[f] == Some(Family::Rust)
                && self.files[f].defs[x].kind.is_class_like()
                && self.files[f].defs[x].owner.is_none()
            {
                return Some(((f, x), Confidence::Extracted));
            }
        }
        let found = self.resolve_path(f, d, path, true);
        match found? {
            (Target::Def(r), conf) => {
                let def = self.def(r);
                let k = def.kind;
                // C, C++ `using AsyncResponseC = AsyncWrapper<Response, true>;`,
                // `typedef struct conn conn_t;`: the type the alias names, and of
                // a template's specializations the one its arguments fit
                // (and Kotlin / Swift `typealias Store = Repo`)
                // (and Go `type A = T`, the one alias form it has)
                let c_alias = k == DefKind::TypeAlias
                    && (matches!(self.family[r.0], Some(Family::C | Family::Jvm | Family::Swift))
                        || (self.family[r.0] == Some(Family::Go) && def.declared_type.is_some()));
                // `typedef struct reader { … } reader;`: the struct written
                // inside the alias, which holds the members
                let inside = c_alias
                    .then(|| self.children[r.0].get(&r.1))
                    .flatten()
                    .map(|m| {
                        m.values()
                            .flatten()
                            .map(|&i| (r.0, i))
                            .filter(|c| self.def(*c).kind.is_class_like())
                            .collect::<Vec<DefRef>>()
                    })
                    .and_then(|v| (v.len() == 1).then(|| v[0]));
                let aliased = inside.or_else(|| {
                    c_alias
                        .then_some(def.declared_type.as_ref())
                        .flatten()
                        .and_then(|ty| self.type_of_path(r.0, def.parent, ty, true))
                        .filter(|t| *t != r && self.is_type_like(*t))
                });
                if let Some(t) = aliased {
                    Some((self.specialization_for(t, &def.alias_args), conf))
                } else if k.is_class_like() || k == DefKind::TypeAlias {
                    Some((r, conf))
                } else if k.is_callable()
                    && self.family[r.0] == Some(Family::Ecma)
                    && self.has_prototype_members(r)
                {
                    // `function Widget() {}` with `Widget.prototype.render =
                    // function …`: what `new Widget()` makes has those
                    Some((r, conf))
                } else if let Some((t, declared)) =
                    k.is_callable().then(|| self.return_type(r)).flatten()
                {
                    // a call of a function whose result is a project type
                    Some((t, if declared { conf } else { Confidence::Inferred }))
                } else if k.is_callable() && path.len() >= 2 && self.returns_self_param(r) {
                    // `builder(x).tag(y)`: `tag` returns the builder it is called on
                    self.type_of_path_inner(f, d, &path[..path.len() - 1])
                } else if k.is_callable() && path.len() == 2 && is_factory(&def.name) {
                    // `Foo::new()`, `Foo.create()`: a type's own constructor
                    let owner = self.class_of(r.0, Some(r.1))?;
                    (self.def(owner).name == path[0]).then_some((owner, conf))
                } else if k == DefKind::Constructor {
                    self.class_of(r.0, Some(r.1)).map(|t| (t, conf))
                } else if k == DefKind::EnumMember && !*self.types_only.borrow() {
                    // `Path.method(.get)`: a case makes a value of its enum
                    def.parent
                        .map(|p| (r.0, p))
                        .filter(|e| self.def(*e).kind.is_class_like())
                        .map(|e| (e, conf))
                } else if k.is_state() && !*self.types_only.borrow() {
                    // a value that is a field or a variable (`guard let q =
                    // cat`): the type that holds, as surely as it is written
                    let written = def.declared_type.is_some();
                    self.held_type(r)
                        .map(|t| (t, if written { conf } else { Confidence::Inferred }))
                } else {
                    None
                }
            }
            _ => None,
        }
    }

    /// Whether a call's written type arguments are read for `c`'s language
    /// (C#, Java), so that a count of them says which callable is meant.
    fn writes_type_arguments(&self, c: DefRef) -> bool {
        use crate::lang::Lang;
        matches!(self.files[c.0].lang, Some(Lang::CSharp | Lang::Java))
    }

    /// Whether two callables take the same parameters as written: they
    /// differ in their type parameters alone (`Get(Request r)` beside
    /// `Get<T>(Request r)`).
    fn same_parameters(&self, a: DefRef, b: DefRef) -> bool {
        let (x, y) = (&self.def(a).plist, &self.def(b).plist);
        // (`IRequest<TResponse>` is not `IRequest`: the type as written,
        // type arguments and all)
        x.len() == y.len()
            && x.iter().zip(y).all(|(p, q)| {
                !p.written.is_empty() && p.written == q.written && p.variadic == q.variadic
            })
    }

    /// Whether `c` is a member of a C# 14 `extension(T x) { … }` block: the
    /// grammar reads the block as a constructor named `extension`, which the
    /// engine keeps as a scope only (`LanguageModule::scope_only`).
    fn in_extension_block(&self, c: DefRef) -> bool {
        self.def(c).parent.is_some_and(|p| {
            let block = &self.files[c.0].defs[p];
            block.block && block.name == "extension"
        })
    }

    /// Whether callable `c` can take the call `r` makes, and how well: `None`
    /// when the count of arguments, a name one is passed under or the type of
    /// one rules it out; else a score, higher for each argument whose type is
    /// the parameter's. What the facts do not say counts for nothing either
    /// way.
    fn fit(&self, f: usize, d: Option<usize>, c: DefRef, r: &RefFact) -> Option<i32> {
        let def = self.def(c);
        if def.params.is_none() {
            return Some(0);
        }
        // a C# extension's first parameter is the receiver (a member of an
        // `extension(T x) { … }` block has it on the block)
        let plist = match (&def.receiver, self.family[c.0]) {
            (Some(_), Some(Family::CSharp))
                if !def.plist.is_empty() && !self.in_extension_block(c) =>
            {
                &def.plist[1..]
            }
            _ => &def.plist[..],
        };
        // type arguments written at the call (`client.Get<User>(id)`) are
        // for a callable that declares as many type parameters; a call that
        // writes none says nothing (they may be inferred)
        if r.targs > 0
            && self.writes_type_arguments(c)
            && def.type_params.len() != usize::from(r.targs)
        {
            return None;
        }
        let variadic = plist.iter().any(|x| x.variadic);
        if let Some(a) = r.args.map(usize::from) {
            let required = plist.iter().filter(|x| !x.optional && !x.variadic).count();
            if (a > plist.len() && !variadic) || a < required {
                return None;
            }
        }
        // Swift passes labelled arguments in order; elsewhere a name ends
        // the positional ones
        let swift = self.family[c.0] == Some(Family::Swift);
        let (mut score, mut positional) = (0, true);
        for (i, a) in r.hints.iter().enumerate() {
            let param = match &a.label {
                Some(l) => {
                    positional = swift;
                    let at = if swift { plist.get(i).filter(|x| x.name == *l) } else { None };
                    at.or_else(|| plist.iter().find(|x| x.name == *l))?
                }
                None if positional => match plist.get(i).or(plist.last().filter(|x| x.variadic)) {
                    // an unlabelled argument is not one Swift wants a label for
                    Some(x) if swift && x.name != "_" && !x.name.is_empty() => {
                        if !matches!(a.kind, crate::facts::Arg::Func) {
                            return None;
                        }
                        x
                    }
                    // (a closure for the parameter that asks for no label,
                    // before one a trailing closure could also stand for)
                    Some(x) if swift && matches!(a.kind, crate::facts::Arg::Func) => {
                        score += 1;
                        x
                    }
                    Some(x) => x,
                    None => continue,
                },
                None => continue,
            };
            // (C# `int`, `string`, `object` and Java's primitives are types
            // the path of a written type leaves empty: the written text is
            // the type)
            let written: Vec<String>;
            let w = param.written.trim().trim_end_matches('?');
            // (only for a literal: a C# type of the project can convert to a
            // predefined one through an `implicit operator`)
            let literal = !matches!(a.kind, crate::facts::Arg::Path(_));
            let ty = if literal
                && (matches!(self.family[c.0], Some(Family::CSharp | Family::C))
                    || self.files[c.0].lang == Some(crate::lang::Lang::Java))
                && param.ty.is_empty()
                && !w.is_empty()
                && w.chars().all(char::is_alphanumeric)
            {
                written = vec![w.to_string()];
                &written
            } else {
                &param.ty
            };
            // C# `r.Apply(x => x + 1)`: a lambda fits only a delegate of as
            // many parameters (the standard's 10.7.1)
            if let crate::facts::Arg::Lambda(k) = &a.kind {
                if let Some(n) = crate::refs::csharp_delegate_arity(&param.written) {
                    if n != usize::from(*k) {
                        return None;
                    }
                    score += 1;
                }
            }
            score += self.arg_fits(f, d, c, &a.kind, ty)?;
            // an argument that is a parameter of the caller, declared with the
            // very type this parameter is written with (type arguments and
            // all), is an identity conversion: the better one (C# §12.6.4.5;
            // `GetPopulateArray(getFormattables, …)` with `Func<object,
            // IEnumerable<IFormattable>> getFormattables` beside an overload
            // taking `Func<object, IEnumerable<IConvertible>>`)
            if let crate::facts::Arg::Path(path) = &a.kind {
                if let ([name], Some(di)) = (path.as_slice(), d) {
                    let norm = |t: &str| -> String {
                        t.chars().filter(|ch| !ch.is_whitespace()).collect::<String>()
                    };
                    let theirs = norm(param.written.trim_end_matches('?'));
                    let given = self.files[f].defs[di]
                        .plist
                        .iter()
                        .find(|x| x.name == *name)
                        .map(|x| norm(x.written.trim_end_matches('?')));
                    if !theirs.is_empty() && given.as_deref() == Some(theirs.as_str()) {
                        score += 2;
                    }
                }
            }
        }
        // a `params` member applicable only in its expanded form is worse than
        // one applicable in its normal form (C# §12.6.4.3)
        if variadic && r.args.is_some_and(|a| usize::from(a) != plist.len()) {
            score -= 1;
        }
        Some(score)
    }

    /// One argument against one parameter's written type.
    fn arg_fits(
        &self,
        f: usize,
        d: Option<usize>,
        c: DefRef,
        arg: &crate::facts::Arg,
        ty: &[String],
    ) -> Option<i32> {
        use crate::facts::Arg;
        let Some(name) = ty.last() else { return Some(0) };
        let prim = primitive(name);
        // a literal is no instance of a class of the project where nothing
        // converts one implicitly
        let strict = matches!(self.family[c.0], Some(Family::Jvm | Family::CSharp));
        let literal = |want: Prim| match prim {
            Some(p) if p == want => Some(1),
            Some(Prim::Any) => Some(0),
            Some(_) => None,
            None if strict && self.param_type(c, ty).is_some() => None,
            None => Some(0),
        };
        // Java: a number or a flag fits a primitive parameter without boxing,
        // which phase 1 of overload resolution takes first (JLS 15.12.2.2);
        // of the primitives an integer literal widens to, the narrowest is
        // the most specific (15.12.2.5: int <: long <: float <: double)
        let java = self.files[c.0].lang == Some(crate::lang::Lang::Java);
        let unboxed = java
            && matches!(
                name.as_str(),
                "int" | "long" | "short" | "byte" | "double" | "float" | "char" | "boolean"
            );
        let widening = |n: &str| match n {
            "int" => 4,
            "long" => 3,
            "float" => 2,
            "double" => 1,
            _ => 0,
        };
        match arg {
            // (C++: a string literal is a `const char*` exactly, a
            // `std::string` through a user-defined conversion)
            Arg::Str if self.family[c.0] == Some(Family::C) => {
                literal(Prim::Str).map(|x| x + i32::from(name == "char"))
            }
            Arg::Str => literal(Prim::Str),
            Arg::Num => literal(Prim::Num).map(|x| x + i32::from(unboxed)),
            Arg::Int if java && matches!(name.as_str(), "short" | "byte" | "char") => None,
            // (C++: an `int` parameter is an exact match, any other numeric
            // type a conversion, [over.ics.rank])
            Arg::Int if self.family[c.0] == Some(Family::C) => {
                literal(Prim::Num).map(|x| x + if name == "int" { 2 } else { 0 })
            }
            Arg::Int => {
                literal(Prim::Num).map(|x| x + if unboxed { 1 + widening(name) } else { 0 })
            }
            Arg::Bool => literal(Prim::Bool).map(|x| x + i32::from(unboxed)),
            Arg::Class => literal(Prim::Class),
            // a lambda is something to call: no string, number, flag or
            // class (it may be an interface of one method, which a class of
            // the project can be)
            Arg::Func | Arg::Lambda(_) => match prim {
                Some(Prim::Func) => Some(1),
                // a name the project gives a type (`typealias Validation =
                // (…) -> Result`): what a lambda is passed for
                None if self
                    .type_of_path(c.0, self.def(c).parent, ty, true)
                    .is_some_and(|t| self.def(t).kind == DefKind::TypeAlias) =>
                {
                    Some(1)
                }
                Some(Prim::Any) | None => Some(0),
                Some(_) => None,
            },
            Arg::Unknown | Arg::Null => Some(0),
            // `.status(301)`: a static member of the parameter's type. A
            // type that has it is the one meant; one that has none is not,
            // unless it is a protocol (its conforming types add their own)
            Arg::Implicit(member) => match self.param_type(c, ty) {
                Some(pt) if self.class_member(pt, member, 4).is_some() => Some(2),
                Some(pt) if self.def(pt).kind == DefKind::Interface => Some(0),
                Some(_) => None,
                None => Some(0),
            },
            Arg::Path(path) => {
                let at = if path.len() == 1 && path[0] == "self" {
                    // `this` in a lambda with a receiver is the receiver
                    let recv = *self.recv.borrow();
                    recv.and_then(|(_, i)| self.receiver_type(f, d, i))
                        .map(|(t, _)| t)
                        .or_else(|| self.class_of(f, d))
                } else {
                    self.value_type_of(f, d, path)
                };
                // a field, a property or a constant: the type it is declared
                // with, or its value's
                let held = if at.is_none() { self.state_of(f, d, path) } else { None };
                let at = at.or_else(|| held.and_then(|p| self.held_type(p)));
                let Some(at) = at else {
                    // no type of the project: the type the source writes for
                    // the name still says a string from a number, and whether
                    // it is the parameter's own
                    let written = (path.len() == 1)
                        .then(|| self.local_written_type(f, d, &path[0]))
                        .flatten()
                        .or_else(|| held.and_then(|p| self.written_field_type(p)));
                    return match written {
                        Some(w) if w.eq_ignore_ascii_case(name) => Some(1),
                        Some(w) => match (primitive(&w), prim) {
                            (Some(Prim::Any), _) | (_, Some(Prim::Any)) | (None, _) => Some(0),
                            (Some(a), Some(b)) if a == b => Some(1),
                            (Some(_), Some(_)) => None,
                            (Some(_), None) if strict && self.param_type(c, ty).is_some() => None,
                            (Some(_), None) => Some(0),
                        },
                        None => Some(0),
                    };
                };
                // (a parameter whose written type the project defines is that
                // type, whatever its name: an `enum Type` is no `Class`)
                let prim = prim.filter(|_| self.param_type(c, ty).is_none());
                match prim {
                    Some(Prim::Any) => Some(0),
                    // an object of the project is no string, number or flag
                    Some(_) => None,
                    None => match self.param_type(c, ty) {
                        // (the parameter's very type over one of its bases:
                        // `request(_: Endpoint)` over `request(_: any
                        // URLConvertible)` for an `Endpoint`)
                        Some(pt) if pt == at => Some(3),
                        Some(pt) if self.is_a(at, pt, 6) => Some(2),
                        // not one of its bases: no fit, where a type's
                        // bases are all written on it. A Swift protocol can
                        // be conformed to in an extension elsewhere, and a
                        // C++ class converts through a constructor
                        Some(pt) if self.bases_are_written(pt) => None,
                        Some(_) | None => Some(0),
                    },
                }
            }
        }
    }

    /// The field, property, variable or constant a path names, if it names one.
    fn state_of(&self, f: usize, d: Option<usize>, path: &[String]) -> Option<DefRef> {
        if path.iter().any(|s| s == "()") {
            return None;
        }
        let recv = self.recv.replace(None);
        let calling = self.calling.replace(false);
        let found = self.resolve_path(f, d, path, true);
        *self.calling.borrow_mut() = calling;
        *self.recv.borrow_mut() = recv;
        match found {
            Some((Target::Def(p), _)) if self.def(p).kind.is_state() => Some(p),
            _ => None,
        }
    }

    /// The project type a field, property or constant holds.
    fn held_type(&self, p: DefRef) -> Option<DefRef> {
        let def = self.def(p);
        let declared = def
            .declared_type
            .as_ref()
            .and_then(|ty| self.type_of_path(p.0, def.parent, ty, true))
            .filter(|t| self.is_type_like(*t));
        declared.or_else(|| {
            self.class_of(p.0, def.parent)
                .and_then(|owner| self.field_type(owner, &def.name, 3))
                .or_else(|| self.state_type(p))
                .map(|(t, _)| t)
        })
    }

    /// The type a field is declared with, as written (`String`, `Int`).
    fn written_field_type(&self, p: DefRef) -> Option<String> {
        self.def(p).declared_type.as_ref()?.last().cloned()
    }

    /// The project type a parameter of `c` is written with.
    fn param_type(&self, c: DefRef, ty: &[String]) -> Option<DefRef> {
        self.type_of_path(c.0, self.def(c).parent, ty, true).filter(|t| self.is_type_like(*t))
    }

    /// Whether every type that is a `base` says so where it is declared, so
    /// that one that does not is not one.
    fn bases_are_written(&self, base: DefRef) -> bool {
        match self.family[base.0] {
            Some(Family::Jvm | Family::CSharp) => true,
            Some(Family::Swift) => self.def(base).kind != DefKind::Interface,
            _ => false,
        }
    }

    /// Whether type `t` has `base` among its bases.
    fn is_a(&self, t: DefRef, base: DefRef, depth: usize) -> bool {
        depth > 0 && self.bases(t).into_iter().any(|b| b == base || self.is_a(b, base, depth - 1))
    }

    /// Whether no type of the family answers to `name` but by one method:
    /// no property or field of that name (Swift `var status`, Ruby
    /// `@status`), and no attribute declared without a `def` (Ruby
    /// `attr_reader :status`, `Struct.new(:status)`).
    fn sole_holder(&self, fam: Family, name: &str) -> bool {
        // nor a name the language's own strings, arrays and hashes answer to
        let core = match fam {
            Family::Ruby => RUBY_CORE_METHODS,
            Family::Swift => SWIFT_CORE_METHODS,
            _ => &[],
        };
        if core.contains(&name) || self.attrs.contains(&(fam, name.to_string())) {
            return false;
        }
        !self
            .by_name
            .get(name)
            .into_iter()
            .flatten()
            .any(|r| self.family[r.0] == Some(fam) && self.def(*r).kind.is_state())
    }

    /// The candidates that fit best; of those, the ones not marked deprecated
    /// when some are not (a deprecated overload is kept for old callers).
    fn top_fits(&self, fits: &[(DefRef, i32)]) -> Vec<DefRef> {
        let top = fits.iter().map(|(_, s)| *s).max();
        let best: Vec<DefRef> =
            fits.iter().filter(|(_, s)| Some(*s) == top).map(|(c, _)| *c).collect();
        let current: Vec<DefRef> = best.iter().copied().filter(|c| !self.deprecated(*c)).collect();
        if current.is_empty() {
            best
        } else {
            current
        }
    }

    /// Of candidates that fit alike, those that take exactly the arguments
    /// passed, before those that need a default or a vararg to.
    fn exact_first(&self, best: Vec<DefRef>, r: &RefFact) -> Vec<DefRef> {
        let Some(a) = r.args.map(usize::from) else { return best };
        let exact: Vec<DefRef> = best
            .iter()
            .copied()
            .filter(|c| {
                let pl = &self.def(*c).plist;
                self.def(*c).params.is_some() && pl.len() == a && !pl.iter().any(|x| x.variadic)
            })
            .collect();
        if exact.is_empty() {
            best
        } else {
            exact
        }
    }

    /// Whether functions are assigned to the function `c` as its members in
    /// its file (an ES5 constructor's `prototype`, see `assigned_member`).
    fn has_prototype_members(&self, c: DefRef) -> bool {
        let name = self.def(c).name.as_str();
        self.def(c).object.is_none()
            && self.files[c.0]
                .defs
                .iter()
                .any(|x| x.kind.is_callable() && x.object.as_deref() == Some(name))
    }

    /// Whether a Python method is a `@property` (or `@cached_property`).
    fn is_property(&self, c: DefRef) -> bool {
        self.files[c.0].refs.iter().any(|r| {
            r.from_def == Some(c.1)
                && r.kind == RefKind::Decorator
                && r.path
                    .last()
                    .is_some_and(|n| matches!(n.as_str(), "property" | "cached_property"))
        })
    }

    /// Whether a definition carries `@Deprecated` / `[Obsolete]` / `#[deprecated]`.
    fn deprecated(&self, c: DefRef) -> bool {
        self.files[c.0].refs.iter().any(|r| {
            r.from_def == Some(c.1)
                && r.kind == RefKind::Decorator
                && r.path
                    .iter()
                    .any(|n| matches!(n.as_str(), "Deprecated" | "Obsolete" | "deprecated"))
        })
    }

    /// The callables written beside `m` under its name: its overloads.
    fn overloads(&self, m: DefRef) -> Vec<DefRef> {
        let d = self.def(m);
        let named = match d.parent {
            Some(par) => self.children[m.0].get(&par).and_then(|x| x.get(&d.name)),
            None => self.top[m.0].get(&d.name),
        };
        let mut out: Vec<DefRef> = named
            .into_iter()
            .flatten()
            .map(|&i| (m.0, i))
            .filter(|r| {
                let x = self.def(*r);
                // (of one scope: `::Helper` and `app::Helper` are no overloads
                // of each other)
                x.kind.is_callable() && !x.decl_only && x.owner == d.owner && x.parent == d.parent
            })
            .collect();
        out.sort();
        // Swift: those an extension of the type adds under the name
        // (`extension Session { func request(_ endpoint: Endpoint) }`)
        // (never for a function declared in a function: its overloads are
        // its own scope's)
        let local = d.parent.is_some_and(|p| self.files[m.0].defs[p].kind.is_callable());
        if self.family[m.0] == Some(Family::Swift) && !local {
            if let Some(c) = self.class_of(m.0, Some(m.1)) {
                for x in self.members(c, &d.name) {
                    let def = self.def(x);
                    if def.kind.is_callable() && !def.decl_only && !out.contains(&x) {
                        out.push(x);
                    }
                }
            }
        }
        // and those a base adds under the name: with other parameters (one
        // with the same is overridden, not another overload)
        let shape = |r: DefRef| -> Vec<String> {
            self.def(r).plist.iter().map(|x| x.ty.last().cloned().unwrap_or_default()).collect()
        };
        if let Some(c) =
            d.parent.map(|par| (m.0, par)).filter(|c| self.def(*c).kind.is_class_like())
        {
            let mut seen: Vec<DefRef> = vec![c];
            let mut queue = self.bases(c);
            while let Some(b) = queue.pop() {
                if seen.contains(&b) || seen.len() > 8 {
                    continue;
                }
                seen.push(b);
                for x in self.members(b, &d.name) {
                    let def = self.def(x);
                    if def.kind.is_callable()
                        && !def.decl_only
                        && !out.iter().any(|o| shape(*o) == shape(x))
                    {
                        out.push(x);
                    }
                }
                queue.extend(self.bases(b));
            }
        }
        out
    }

    /// Of the overloads of `m`, the one the call `r` can be of: the only one
    /// that takes its arguments, else the one whose parameters are their
    /// types. `m` when that does not decide.
    ///
    /// When several fit alike, all of them (at most four, the one found
    /// first): the call is of one of them, and the facts do not say which.
    fn pick_overload(&self, f: usize, d: Option<usize>, m: DefRef, r: &RefFact) -> Vec<DefRef> {
        // where one name can have several definitions at once; elsewhere a
        // second definition replaces the first
        if !self.family[m.0].is_some_and(Family::overloads) {
            return vec![m];
        }
        // C#: no method of the type takes as many type arguments as the call
        // writes; an extension of the name that does is what is called
        // (`parser.ParseArguments<A, B>(args)` beside `ParseArguments<T>`)
        let wider = || -> Vec<DefRef> {
            if r.targs == 0 || self.family[m.0] != Some(Family::CSharp) {
                return Vec::new();
            }
            let found: Vec<DefRef> = self
                .extensions
                .get(&self.def(m).name)
                .into_iter()
                .flatten()
                .copied()
                .filter(|e| {
                    self.family[e.0] == Some(Family::CSharp)
                        && self.def(*e).type_params.len() == usize::from(r.targs)
                })
                .collect();
            if found.len() <= 4 {
                found
            } else {
                Vec::new()
            }
        };
        // C#: no method of the type takes these arguments; an extension of
        // the name for the type that does is what is called (the standard's
        // 12.8.10.3: extensions are looked for when no instance method is
        // applicable)
        let applicable_extensions = || -> Vec<DefRef> {
            if self.family[m.0] != Some(Family::CSharp) || r.args.is_none() {
                return Vec::new();
            }
            let Some(owner) = self.def(m).parent.map(|o| (m.0, o)) else { return Vec::new() };
            if !self.def(owner).kind.is_class_like() {
                return Vec::new();
            }
            let mut names = vec![self.def(owner).name.clone()];
            names.extend(self.bases(owner).into_iter().map(|b| self.def(b).name.clone()));
            let found: Vec<DefRef> = self
                .extensions
                .get(&self.def(m).name)
                .into_iter()
                .flatten()
                .copied()
                .filter(|e| {
                    self.family[e.0] == Some(Family::CSharp)
                        && self.def(*e).receiver.as_ref().is_some_and(|t| {
                            let bare = t.split('<').next().unwrap_or(t);
                            names.iter().any(|n| bare.rsplit('.').next() == Some(n.as_str()))
                        })
                        && self.fit(f, d, *e, r).is_some()
                })
                .collect();
            if found.len() <= 4 {
                found
            } else {
                Vec::new()
            }
        };
        let all = self.overloads(m);
        if all.len() < 2 {
            if self.fit(f, d, m, r).is_none() {
                let ext = applicable_extensions();
                if !ext.is_empty() {
                    return ext;
                }
            }
            if self.def(m).type_params.len() != usize::from(r.targs) {
                let wider = wider();
                if !wider.is_empty() {
                    return wider;
                }
            }
            return vec![m];
        }
        let fits: Vec<(DefRef, i32)> =
            all.iter().filter_map(|c| self.fit(f, d, *c, r).map(|s| (*c, s))).collect();
        let mut best = self.exact_first(self.top_fits(&fits), r);
        if best.is_empty() {
            let ext = applicable_extensions();
            if !ext.is_empty() {
                return ext;
            }
            let wider = wider();
            if !wider.is_empty() {
                return wider;
            }
            return vec![m];
        }
        // a call that writes no type arguments, between a callable and its
        // generic twin of the same parameters: the plain one (C# and Java
        // prefer it; the generic one is called with its argument written)
        if r.targs == 0 && best.len() > 1 && self.writes_type_arguments(m) {
            let plain: Vec<DefRef> =
                best.iter().copied().filter(|c| self.def(*c).type_params.is_empty()).collect();
            let twins = !plain.is_empty()
                && best.iter().all(|c| {
                    self.def(*c).type_params.is_empty()
                        || plain.iter().any(|p| self.same_parameters(*p, *c))
                });
            if twins && plain.len() < best.len() {
                best = plain;
            }
        }
        // C++ and Swift: of candidates that fit alike, one that is no template
        // (no generic parameters) is better ([over.match.best]; Swift's
        // isDeclAsSpecializedAs opens the generic one's parameters)
        // Only a generic one whose parameters are its type parameters
        // themselves (`Show<T>(T x)`) is known to fit alike: one taking a
        // closure or a `Box<T>` may be the only one that fits at all.
        // (and only where every argument is a literal of a known kind: an
        // array literal or a value the walk cannot type may be the very
        // `Sequence` the generic one takes)
        let known_args = r.args.is_some_and(|a| usize::from(a) == r.hints.len())
            && r.hints.iter().all(|h| {
                use crate::facts::Arg;
                matches!(h.kind, Arg::Str | Arg::Num | Arg::Int | Arg::Bool)
            });
        if best.len() > 1
            && r.targs == 0
            && known_args
            && matches!(self.family[m.0], Some(Family::C | Family::Swift))
        {
            let opened = |c: DefRef| {
                let d = self.def(c);
                !d.type_params.is_empty()
                    && d.plist.iter().all(|p| {
                        let w = p.written.trim();
                        d.type_params.iter().any(|t| t == w)
                    })
            };
            let plain: Vec<DefRef> =
                best.iter().copied().filter(|c| self.def(*c).type_params.is_empty()).collect();
            if !plain.is_empty() && best.iter().all(|c| plain.contains(c) || opened(*c)) {
                best = plain;
            }
        }
        if let Some(at) = best.iter().position(|c| *c == m) {
            best.swap(0, at);
        }
        best.truncate(4);
        best
    }

    /// The receiver type of a lambda with one (`FileFacts::receivers`): the
    /// value of `x.apply { … }` / `with(x) { … }`, or what the function called
    /// declares for its lambda (`block: Builder.() -> Unit`).
    fn receiver_type(&self, f: usize, d: Option<usize>, i: u16) -> Option<(DefRef, Confidence)> {
        if let Some((known, _)) =
            self.recv_types.borrow().get(&(f, i)).filter(|(_, at)| self.trace.valid(*at))
        {
            return *known;
        }
        let at = self.trace.epoch();
        let out = self.receiver_type_uncached(f, d, i);
        self.recv_types.borrow_mut().insert((f, i), (out, at));
        out
    }

    fn receiver_type_uncached(
        &self,
        f: usize,
        d: Option<usize>,
        i: u16,
    ) -> Option<(DefRef, Confidence)> {
        let scope = self.files[f].receivers.get(usize::from(i))?;
        // what names the receiver is written outside the lambda
        let before = self.recv.replace(scope.outer.map(|o| (f, o)));
        let types = self.types_only.replace(false);
        let calling = self.calling.replace(false);
        let out = match &scope.of {
            ReceiverOf::Value(path) => self.type_of_path_inner(f, d, path),
            ReceiverOf::Callee(path) => match self.resolve_path(f, d, path, true) {
                Some((Target::Def(c), conf)) => self.def(c).lambda_receiver.clone().and_then(|p| {
                    self.type_of_path(c.0, self.def(c).parent, &p, true).map(|t| (t, conf))
                }),
                _ => None,
            },
        }
        .filter(|(t, _)| self.def(*t).kind.is_class_like());
        *self.calling.borrow_mut() = calling;
        *self.types_only.borrow_mut() = types;
        *self.recv.borrow_mut() = before;
        out
    }

    /// A bare name inside lambdas with a receiver: a member of the innermost
    /// receiver that has one of that name (Kotlin looks there before the
    /// class around).
    fn receiver_member(
        &self,
        f: usize,
        d: Option<usize>,
        name: &str,
    ) -> Option<(DefRef, Confidence)> {
        let (file, mut at) = (*self.recv.borrow())?;
        if file != f {
            return None;
        }
        for _ in 0..8 {
            if let Some((t, conf)) = self.receiver_type(f, d, at) {
                if let Some(m) = self.class_member(t, name, 4) {
                    return Some((m, conf));
                }
                // an extension written for the receiver's type
                let on = self.def(t).name.clone();
                if let Some((Target::Def(m), Confidence::Extracted)) =
                    self.extension_method(f, name, Some(&on))
                {
                    return Some((m, conf));
                }
            }
            at = self.files[f].receivers.get(usize::from(at))?.outer?;
        }
        None
    }

    /// The project type a callable returns, and whether it declares it. One
    /// that declares nothing returns what its `return`s and expression body
    /// construct (`fun builder(n) = Builder(n)`, `return self`), when they
    /// agree: read from the body, so a guess.
    fn return_type(&self, r: DefRef) -> Option<(DefRef, bool)> {
        // a tuple is no one type (Go's first result is what its callers
        // take, `v, err := f()`)
        if !self.def(r).rets.is_empty() && self.family[r.0] != Some(Family::Go) {
            return None;
        }
        // `-> Option<Foo>`, `Promise<Foo>`: what the wrapper holds (whether
        // it has to be opened first is the walk's `sealed`)
        if let Some(inner) = self.def(r).wraps.as_ref().filter(|_| self.def(r).kind.is_callable()) {
            // (`-> Result<Self, Error>`: the type the method is declared for)
            let own = matches!(inner.as_slice(), [one] if one == "self" || one == "Self");
            let t = if own {
                self.class_of(r.0, Some(r.1))?
            } else {
                self.type_of_path(r.0, self.def(r).parent, inner, true)?
            };
            return self.is_type_like(t).then_some((t, true));
        }
        let declared = self.files[r.0].refs.iter().find(|x| {
            x.from_def == Some(r.1) && x.kind == RefKind::Type && x.context == "return_type"
        });
        let Some(rt) = declared else {
            let mut found: Option<DefRef> = None;
            for path in &self.def(r).returns {
                let t = if path.len() == 1 && path[0] == "self" {
                    self.class_of(r.0, Some(r.1))
                } else {
                    self.type_of_path(r.0, Some(r.1), path, false)
                };
                match (t, found) {
                    (Some(t), None) => found = Some(t),
                    (Some(t), Some(f)) if t != f => return None,
                    _ => {}
                }
            }
            return found.filter(|t| self.is_type_like(*t)).map(|t| (t, false));
        };
        let path = rt.path.clone();
        if path == ["self"] {
            // `-> Self`, `: this`: the method's own type (builders)
            return self.class_of(r.0, Some(r.1)).map(|t| (t, true));
        }
        let t = self.type_of_path(r.0, self.def(r).parent, &path, true)?;
        self.is_type_like(t).then_some((t, true))
    }

    /// A type a value can have: a class-like, or a Go defined type that is
    /// no struct or interface (`type Middlewares []func(…)`, `type nodes
    /// []*node`), which has methods of its own.
    fn is_type_like(&self, t: DefRef) -> bool {
        let k = self.def(t).kind;
        k.is_class_like() || (k == DefKind::TypeAlias && self.family[t.0] == Some(Family::Go))
    }

    /// The type of a local name or parameter seen from `d`.
    fn local_type(&self, f: usize, d: Option<usize>, name: &str) -> Option<(DefRef, Confidence)> {
        // Go, C#: a local of a block the use is not written in (`if x :=
        // f(); …` ended with the `if`, `{ var y = … }`) types nothing there
        if matches!(self.family[f], Some(Family::Go | Family::CSharp))
            && (*self.site.borrow()).is_some_and(|(sf, _)| sf == f)
            && !self.is_local_name(f, d, name)
        {
            return None;
        }
        let defs = &self.files[f].defs;
        let mut cur = d;
        while let Some(x) = cur {
            if let Some(&li) = self.locals.get(&(f, x, name.to_string())) {
                let mut li = self.local_in_force(f, x, name, li);
                // a redeclaration's own value names the declaration before
                // it (`let agent = agent.clone();`)
                while self.typing.borrow().contains(&(f, li)) {
                    match self.declared_before(f, x, name, li) {
                        Some(prev) => li = prev,
                        None => break,
                    }
                }
                let out = self.local_type_at(f, x, li, name);
                // … and one the walk cannot type gives way to the declaration
                // before it that it can: most often its value is made from
                // that one (`let timings = mem::take(timings);`), and the
                // walk has no blocks to say the earlier one is out of scope
                if out.is_none() {
                    let mut at = li;
                    while let Some(prev) = self.declared_before(f, x, name, at) {
                        if let Some((t, _)) = self.local_type_at(f, x, prev, name) {
                            return Some((t, Confidence::Inferred));
                        }
                        at = prev;
                    }
                }
                return out;
            }
            cur = defs[x].parent;
        }
        None
    }

    /// The declaration of the name before local `li` in the definition.
    fn declared_before(&self, f: usize, x: usize, name: &str, li: usize) -> Option<usize> {
        let all = self.shadows.get(&(f, x, name.to_string()))?;
        let at = all.iter().position(|&i| i == li)?;
        at.checked_sub(1).map(|i| all[i])
    }

    /// The type of local `li` of definition `x`, worked out once.
    fn local_type_at(
        &self,
        f: usize,
        x: usize,
        li: usize,
        name: &str,
    ) -> Option<(DefRef, Confidence)> {
        // typing a local can come back to it (`let session =
        // session(using: x)`: the value names the local): the second
        // time round it has no type
        if let Some((known, _)) =
            self.local_types.borrow().get(&(f, li)).filter(|(_, at)| self.trace.valid(*at))
        {
            return *known;
        }
        // worked out from the top, the answer is the local's own and
        // is kept; inside another typing it may be cut short
        let whole = self.typing.borrow().is_empty() && *self.depth.borrow() == 0;
        if !self.typing.borrow_mut().insert((f, li)) {
            return None;
        }
        let at = self.trace.epoch();
        // a local's type is the same whatever use asks for it: its
        // value's path is what is called there (`x = f()` -> `f`),
        // a written type is not
        let value = !self.files[f].locals[li].declared;
        // (`if let Some(r) = opt` beside the parameter `opt`: the
        // value is that parameter, which nothing calls)
        let held = value && self.holds_declared_local(f, x, li);
        let calling = self.calling.replace(value && !held);
        let typing_value = self.typing_value.replace(value);
        let recv = self.recv.replace(None);
        let out = self.local_type_of(f, x, li, name);
        *self.recv.borrow_mut() = recv;
        *self.typing_value.borrow_mut() = typing_value;
        *self.calling.borrow_mut() = calling;
        self.typing.borrow_mut().remove(&(f, li));
        if whole {
            self.local_types.borrow_mut().insert((f, li), (out, at));
        }
        out
    }

    /// Of several locals of one name in a definition, the one in force where
    /// the reference being resolved is written: the last declared before it
    /// (the first, for a use above them all). `li` is the one the index holds.
    fn local_in_force(&self, f: usize, x: usize, name: &str, li: usize) -> usize {
        if self.shadows.is_empty() {
            return li;
        }
        let Some(all) = self.shadows.get(&(f, x, name.to_string())) else { return li };
        let Some((_, at)) = (*self.site.borrow()).filter(|(sf, _)| *sf == f) else {
            return li;
        };
        let locals = &self.files[f].locals;
        // the one whose block the use is written in, the innermost of them
        // (declared last); else the last declared before the use
        // (the arms of one `match` are declared at the same place: the first)
        // (and not in its own value: `let convertible = Wrap(convertible)`)
        let inside = |i: &usize| {
            (locals[*i].until == 0 || at < locals[*i].until)
                && (locals[*i].after == 0 || locals[*i].after <= at || at < locals[*i].byte)
        };
        let before = || all.iter().rev().copied().filter(|&i| locals[i].byte <= at);
        let Some(last) = before().find(inside).or_else(|| before().next()) else {
            return all.iter().copied().find(inside).unwrap_or(all[0]);
        };
        all.iter().copied().find(|&i| locals[i].byte == locals[last].byte).unwrap_or(last)
    }

    /// Where the first local of the name is declared in the definition.
    /// Ruby: whether every local `name` of the definitions around is first
    /// assigned after `byte` (a use there is no use of the local).
    fn ruby_local_starts_after(&self, f: usize, d: Option<usize>, name: &str, byte: usize) -> bool {
        if self.files[f].lang != Some(crate::lang::Lang::Ruby) {
            return false;
        }
        let defs = &self.files[f].defs;
        let mut cur = d;
        while let Some(x) = cur {
            if let Some(&li) = self.locals.get(&(f, x, name.to_string())) {
                return self.first_declared(f, x, name, li) > byte;
            }
            cur = defs[x].parent;
        }
        false
    }

    fn first_declared(&self, f: usize, x: usize, name: &str, li: usize) -> usize {
        match self.shadows.get(&(f, x, name.to_string())) {
            Some(all) => self.files[f].locals[all[0]].byte,
            None => self.files[f].locals[li].byte,
        }
    }

    /// Whether local `li` is what a binding opens out of another local that
    /// is declared with its type (`[opt, "[?]"]`, `opt: Option<Repo>`).
    fn holds_declared_local(&self, f: usize, x: usize, li: usize) -> bool {
        let l = &self.files[f].locals[li];
        // (or is that local under another name: the `it` of `r.let { … }`)
        let head = match l.path.as_slice() {
            [head] => head,
            [head, mark] if mark == crate::refs::OPENED => head,
            _ => return false,
        };
        if *head == l.name {
            return false;
        }
        let defs = &self.files[f].defs;
        let mut cur = Some(x);
        while let Some(y) = cur {
            if let Some(&other) = self.locals.get(&(f, y, head.clone())) {
                return other != li && self.files[f].locals[other].declared;
            }
            cur = defs[y].parent;
        }
        false
    }

    /// The type of local `li` of definition `x` (see `local_type`).
    fn local_type_of(
        &self,
        f: usize,
        x: usize,
        li: usize,
        name: &str,
    ) -> Option<(DefRef, Confidence)> {
        let l = &self.files[f].locals[li];
        if l.path.is_empty() {
            // pytest hands a test the result of the fixture its parameter
            // names (`def test_x(runner):` beside `@pytest.fixture def runner()`)
            if let Some(t) = self.fixture_type(f, x, name) {
                return Some((t, Confidence::Inferred));
            }
            // no type written, nothing typeable assigned: the one
            // type that answers everything asked of it here
            return self.structural_type(f, x, name).map(|t| (t, Confidence::Inferred));
        }
        let t = self
            .type_of_path(f, Some(x), &l.path, l.declared)
            .or_else(|| self.given_result(f, x, l))
            .or_else(|| self.bound_result(f, x, l))
            .or_else(|| self.accessed(f, x, l));
        let Some(t) = t else {
            // a value the walk cannot type, unless it is of a
            // library's type: the same
            let external = l.declared
                || matches!(
                    l.path.first().and_then(|h| self.resolve_name(f, Some(x), h)),
                    Some(Target::Ext(_, _))
                );
            if external {
                return None;
            }
            return self.structural_type(f, x, name).map(|t| (t, Confidence::Inferred));
        };
        let conf = if l.declared { Confidence::Extracted } else { Confidence::Inferred };
        Some((t, conf))
    }

    /// What a pytest fixture named `name` returns, for a parameter of that
    /// name of a function in test code: the fixture of the file itself, else
    /// of the nearest `conftest.py` in its directory or one above. A fixture
    /// is a top-level function under `@pytest.fixture` / `@fixture`.
    fn fixture_type(&self, f: usize, x: usize, name: &str) -> Option<DefRef> {
        if self.family[f] != Some(Family::Python) || !self.files[f].defs[x].kind.is_callable() {
            return None;
        }
        let is_fixture = |c: DefRef| {
            let d = self.def(c);
            d.kind.is_callable()
                && d.parent.is_none()
                && self.files[c.0].refs.iter().any(|r| {
                    r.from_def == Some(c.1)
                        && r.kind == RefKind::Decorator
                        && r.path.last().is_some_and(|n| n == "fixture")
                })
        };
        let dir = |p: &str| p.rfind('/').map(|i| p[..=i].to_string()).unwrap_or_default();
        let here = dir(&self.files[f].path);
        let mut best: Option<(usize, DefRef)> = None;
        for &c in self.by_name.get(name).into_iter().flatten() {
            if self.family[c.0] != Some(Family::Python) || !is_fixture(c) {
                continue;
            }
            let path = &self.files[c.0].path;
            // the file's own fixture wins over any conftest's
            let rank = if c.0 == f {
                usize::MAX
            } else if path.ends_with("conftest.py") && here.starts_with(dir(path).as_str()) {
                dir(path).len()
            } else {
                continue;
            };
            if best.is_none_or(|(r, _)| rank > r) {
                best = Some((rank, c));
            }
        }
        let (_, fixture) = best?;
        self.return_type(fixture).map(|(t, _)| t)
    }

    /// The type of a local assigned a generic call that is handed a type
    /// (`val s = retrofit.create(Service::class.java)`, `mock<Foo>()`,
    /// `sp.GetRequiredService<IFoo>()`): that type, when the function called
    /// returns its own type parameter — or is not the project's, and is not
    /// named like something that wraps what it is given (`listOf<Foo>()`).
    fn given_result(&self, f: usize, d: usize, l: &crate::facts::Local) -> Option<DefRef> {
        let given = l.given.as_ref()?;
        let name = l.path.last()?;
        let before = self.recv.replace(None);
        let callee = self.resolve_path(f, Some(d), &l.path, true);
        *self.recv.borrow_mut() = before;
        match callee {
            Some((Target::Def(c), _)) if self.def(c).kind.is_callable() => {
                let ret = self.files[c.0].refs.iter().find(|x| {
                    x.from_def == Some(c.1) && x.kind == RefKind::Type && x.context == "return_type"
                })?;
                // it returns its own type parameter, not something of it
                if ret.path.len() != 1 || !self.def(c).type_params.contains(&ret.path[0]) {
                    return None;
                }
            }
            Some(_) => return None,
            None => {
                let lower = name.to_lowercase();
                let csharp = self.family[f] == Some(Family::CSharp);
                // a type's name called (Kotlin `ArrayList<Foo>()`) builds
                // that type; C# names its methods like types
                if (!csharp && name.chars().next().is_some_and(char::is_uppercase))
                    || WRAPPERS_OF_WHAT_THEY_ARE_GIVEN.iter().any(|w| lower.contains(w))
                    || lower.ends_with("of")
                {
                    return None;
                }
            }
        }
        self.type_of_path(f, Some(d), given, true).filter(|t| self.def(*t).kind.is_class_like())
    }

    /// The type of a value written as an argument: a path typed as a value,
    /// whatever call is being resolved around it.
    fn value_type_of(&self, f: usize, d: Option<usize>, path: &[String]) -> Option<DefRef> {
        let calling = self.calling.replace(false);
        let out = self.type_of_path(f, d, path, false);
        *self.calling.borrow_mut() = calling;
        out
    }

    /// The type of the elements of what `path` names: a local, parameter or
    /// field declared as a collection (`xs: List<Foo>`, `Foo[] xs`), or the
    /// result of a call that declares it returns one.
    fn elem_of(&self, f: usize, d: Option<usize>, path: &[String]) -> Option<(DefRef, Confidence)> {
        self.elem_of_as(f, d, path, crate::refs::ELEMENT)
    }

    /// `elem_of` for one marker (`refs::ELEMENT`, `ITERATED`, `VALUE`): what an
    /// index, an iteration or the second of a pair gives.
    fn elem_of_as(
        &self,
        f: usize,
        d: Option<usize>,
        path: &[String],
        how: &str,
    ) -> Option<(DefRef, Confidence)> {
        let typed = |file: usize, scope: Option<usize>, elem: &[String]| {
            self.type_of_path(file, scope, elem, true)
                .filter(|t| self.is_type_like(*t))
                .map(|t| (t, Confidence::Extracted))
        };
        // Go `for i := range n` with `n Count` (`type Count int`): i is a Count
        if how == crate::refs::KEY && self.family[f] == Some(Family::Go) {
            if let Some((t, conf)) = self.type_of_path_inner(f, d, path) {
                let td = self.def(t);
                let integer = td.value_path.as_ref().and_then(|v| v.last()).is_some_and(|u| {
                    matches!(
                        u.as_str(),
                        "int"
                            | "int8"
                            | "int16"
                            | "int32"
                            | "int64"
                            | "uint"
                            | "uint8"
                            | "uint16"
                            | "uint32"
                            | "uint64"
                            | "uintptr"
                    )
                });
                if td.kind == DefKind::TypeAlias && td.elem.is_none() && !td.keyed && integer {
                    return Some((t, conf));
                }
            }
        }
        if let [name] = path {
            let defs = &self.files[f].defs;
            let mut cur = d;
            while let Some(x) = cur {
                if let Some(&li) = self.locals.get(&(f, x, name.clone())) {
                    let l = &self.files[f].locals[li];
                    // a parameter's is on the definition's parameter list
                    let param = defs[x].plist.iter().find(|p| p.name == *name);
                    let elem = l.elem.clone().or_else(|| param.and_then(|p| p.elem.clone()));
                    let keyed = l.keyed || param.is_some_and(|p| p.keyed);
                    // Go `c byName` with `type byName []*Command`: the defined
                    // type holds the element, in the file it is written in
                    if elem.is_none() && l.declared && !l.path.is_empty() {
                        if let Some(mut t) = self.type_of_path(f, Some(x), &l.path, true) {
                            // (`type More Items`, `type Items []Item`: through
                            // the chain to the one written as a collection)
                            for _ in 0..4 {
                                let td = self.def(t);
                                if td.kind != DefKind::TypeAlias || td.elem.is_some() {
                                    break;
                                }
                                let Some(under) = td.value_path.clone() else { break };
                                match self.type_of_path(t.0, td.parent, &under, true) {
                                    Some(u) if u != t => t = u,
                                    _ => break,
                                }
                            }
                            let td = self.def(t);
                            if td.kind == DefKind::TypeAlias && td.elem.is_some() {
                                let e = td.elem.clone()?;
                                let fits = match how {
                                    crate::refs::ITERATED | crate::refs::KEY => !td.keyed,
                                    crate::refs::VALUE => td.keyed,
                                    _ => true,
                                };
                                if how == crate::refs::KEY && td.keyed {
                                    let k = td.key.clone()?;
                                    return typed(t.0, td.parent, &k);
                                }
                                return if fits { typed(t.0, td.parent, &e) } else { None };
                            }
                        }
                    }
                    if let Some(e) = elem {
                        // (the first of a map's pair is its key)
                        if how == crate::refs::KEY && keyed {
                            let key = l.key.clone().or_else(|| param.and_then(|p| p.key.clone()));
                            return key.and_then(|k| typed(f, Some(x), &k));
                        }
                        let fits = match how {
                            crate::refs::ITERATED | crate::refs::KEY => !keyed,
                            crate::refs::VALUE => keyed,
                            _ => true,
                        };
                        return if fits { typed(f, Some(x), &e) } else { None };
                    }
                    // an optional is a collection of at most one
                    // (`opt.ifPresent { it.run() }`)
                    if l.sealed && how != crate::refs::VALUE {
                        return typed(f, Some(x), &l.path);
                    }
                    // another name for a collection: `val ys = xs`
                    let alias = !l.declared
                        && !l.path.is_empty()
                        && l.path[0] != *name
                        && !l.path.is_empty();
                    // (`if let Some(r) = opt`: what an optional gives once
                    // opened is the value, no collection of it)
                    let opened_optional = matches!(l.path.as_slice(),
                        [of, mark] if mark == crate::refs::OPENED
                            && self.local_sealed(f, Some(x), of));
                    if alias && !opened_optional {
                        return self
                            .elem_of_as(f, Some(x), &l.path, how)
                            .map(|(t, _)| (t, Confidence::Inferred));
                    }
                    // C#: a value of a project type with an enumerator or an
                    // indexer of its own, which the walk below asks
                    if !(l.declared && !l.path.is_empty() && self.family[f] == Some(Family::CSharp))
                    {
                        return None;
                    }
                    break;
                }
                cur = defs[x].parent;
            }
        }
        // a field, a call's result, or a chain of collection methods on
        // one (`xs.filter { … }.sortedBy { … }`, `xs.map { it.owner }`)
        if *self.depth.borrow() > 3 {
            return None;
        }
        *self.depth.borrow_mut() += 1;
        let recv = self.recv.replace(None);
        let calling = self.calling.replace(false);
        let types = self.types_only.replace(false);
        let mut marked = path.to_vec();
        marked.push(how.to_string());
        let found = self.resolve_path_inner(f, d, &marked, true, false);
        *self.types_only.borrow_mut() = types;
        *self.calling.borrow_mut() = calling;
        *self.recv.borrow_mut() = recv;
        *self.depth.borrow_mut() -= 1;
        match found {
            Some((Target::Def(t), conf)) if self.def(t).kind.is_class_like() => Some((t, conf)),
            _ => None,
        }
    }

    /// The element type of what definition `p` is declared as or returns.
    fn elem_type_of(&self, p: DefRef) -> Option<DefRef> {
        let def = self.def(p);
        self.type_of_path(p.0, def.parent, def.elem.as_ref()?, true)
            .filter(|t| self.is_type_like(*t))
    }

    /// `val x = xs.first()`: an element of the collection `xs`.
    fn accessed(&self, f: usize, d: usize, l: &crate::facts::Local) -> Option<DefRef> {
        let (method, of) = l.path.split_last()?;
        let lookup = KEY_LOOKUPS.contains(&method.as_str());
        if of.is_empty() || !(lookup || ELEMENT_ACCESSORS.contains(&method.as_str())) {
            return None;
        }
        // (a map's `first()` is an entry; `get(k)` its value)
        let how = if lookup { crate::refs::ELEMENT } else { crate::refs::ITERATED };
        self.elem_of_as(f, Some(d), of, how).map(|(t, _)| t)
    }

    /// The type of a local assigned a call of a function that returns its
    /// own type parameter, bound by an argument: `fun <T> pick(x: T): T`
    /// gives the argument's type, `fun <T> head(xs: List<T>): T` its element's.
    fn bound_result(&self, f: usize, d: usize, l: &crate::facts::Local) -> Option<DefRef> {
        if l.value_args.is_empty() {
            return None;
        }
        let recv = self.recv.replace(None);
        let callee = self.resolve_path(f, Some(d), &l.path, true);
        *self.recv.borrow_mut() = recv;
        let Some((Target::Def(c), _)) = callee else { return None };
        let def = self.def(c);
        if !def.kind.is_callable() {
            return None;
        }
        let ret = self.files[c.0].refs.iter().find(|x| {
            x.from_def == Some(c.1) && x.kind == RefKind::Type && x.context == "return_type"
        })?;
        let [t] = ret.path.as_slice() else { return None };
        if !def.type_params.contains(t) {
            return None;
        }
        for (p, a) in def.plist.iter().zip(&l.value_args) {
            let crate::facts::Arg::Path(q) = &a.kind else { continue };
            if a.label.is_some() {
                break;
            }
            if p.ty.len() == 1 && p.ty[0] == *t {
                return self.value_type_of(f, Some(d), q);
            }
            if p.elem.as_ref().is_some_and(|e| e.len() == 1 && e[0] == *t) {
                return self.elem_of(f, Some(d), q).map(|(x, _)| x);
            }
        }
        None
    }

    /// Whether callable `c` declares that it returns a type parameter its
    /// type binds to itself (`fun tag(x: Any): T` in `Builder<T : Builder<T>>`).
    fn returns_self_param(&self, c: DefRef) -> bool {
        let ret = self.files[c.0].refs.iter().find(|x| {
            x.from_def == Some(c.1) && x.kind == RefKind::Type && x.context == "return_type"
        });
        let Some(ret) = ret.filter(|r| r.path.len() == 1) else { return false };
        self.class_of(c.0, Some(c.1))
            .is_some_and(|o| self.def(o).self_params.contains(&ret.path[0]))
    }

    /// The member names used on each name in each definition of file `f`.
    fn used_on(&self, f: usize) -> Rc<UsedOn> {
        // (what file `f`'s references are: a read of that file)
        self.trace.file(f);
        if let Some(known) = self.used_on.borrow().get(&f) {
            return known.clone();
        }
        let mut out: UsedOn = HashMap::new();
        for r in &self.files[f].refs {
            let (Some(d), [head, member, ..]) = (r.from_def, r.path.as_slice()) else { continue };
            if member.starts_with(['(', '[']) || matches!(head.as_str(), "self" | "super" | "()") {
                continue;
            }
            // (an operator written `a + b` or `a upTo b` says nothing of what
            // a is: a library type answers to it as well)
            if r.context == "operator" {
                continue;
            }
            // called there, or only read
            let called = r.path.len() == 2 && matches!(r.kind, RefKind::Call);
            let names = out.entry((d, head.clone())).or_default();
            match names.iter_mut().find(|(n, _)| n == member) {
                Some(known) => known.1 |= called,
                None => names.push((member.clone(), called)),
            }
        }
        let out = Rc::new(out);
        self.used_on.borrow_mut().insert(f, out.clone());
        out
    }

    /// Whether type `c` answers to `name`: a member of its own or of a base,
    /// or a name it declares without defining (`Def::attrs`).
    ///
    /// `called`: the name is called there, which a field does not answer
    /// (but in Ruby, where reading an attribute is a call).
    fn answers(&self, c: DefRef, name: &str, called: bool, depth: usize) -> bool {
        let strict = called && self.family[c.0] != Some(Family::Ruby);
        self.members(c, name).iter().any(|m| !strict || self.def(*m).kind.is_callable())
            || self.def(c).attrs.iter().any(|a| a == name)
            || (depth > 0
                && self.bases(c).into_iter().any(|b| self.answers(b, name, called, depth - 1)))
    }

    /// The type of a local nothing in the source types, from what is asked of
    /// it: the one type of the project that answers every member name used on
    /// it in its definition. Names the language's own strings and collections
    /// have say nothing, and two names at least must say something. A guess:
    /// duck typing writes no type, and a library's type could answer too.
    fn structural_type(&self, f: usize, d: usize, name: &str) -> Option<DefRef> {
        let key = (f, d, name.to_string());
        if let Some((known, _)) =
            self.structural.borrow().get(&key).filter(|(_, at)| self.trace.valid(*at))
        {
            return *known;
        }
        // a cycle ends here
        self.structural.borrow_mut().insert(key.clone(), (None, trace::ALWAYS));
        let at = self.trace.epoch();
        let found = self.structural_type_uncached(f, d, name);
        self.structural.borrow_mut().insert(key, (found, at));
        found
    }

    fn structural_type_uncached(&self, f: usize, d: usize, name: &str) -> Option<DefRef> {
        let fam = self.family[f]?;
        let used = self.used_on(f);
        let names: Vec<&(String, bool)> = used
            .get(&(d, name.to_string()))
            .into_iter()
            .flatten()
            .filter(|(n, _)| !is_core_member(fam, n))
            .collect();
        if names.len() < 2 {
            return None;
        }
        // every type that has one of the names, then those that answer all
        let mut pool: Vec<DefRef> = Vec::new();
        for (n, _) in &names {
            let defined =
                self.by_name.get(n.as_str()).into_iter().flatten().copied().filter_map(|r| {
                    let x = self.def(r);
                    // the type it is a member of (a Go receiver's, an impl's)
                    (self.family[r.0] == Some(fam) && (x.kind.is_callable() || x.kind.is_state()))
                        .then(|| self.class_of(r.0, Some(r.1)))
                        .flatten()
                });
            let declared = self.attr_owners.get(&(fam, n.clone())).into_iter().flatten().copied();
            pool.extend(defined.chain(declared));
        }
        pool.sort();
        pool.dedup();
        let fits: Vec<DefRef> = pool
            .into_iter()
            .filter(|c| {
                !self.def(*c).anonymous
                    && names.iter().all(|(n, called)| self.answers(*c, n, *called, 4))
            })
            .collect();
        // one type; or one that the others all derive from
        let found = match fits.as_slice() {
            [one] => Some(*one),
            [] => None,
            many => {
                many.iter().copied().find(|b| many.iter().all(|c| c == b || self.is_a(*c, *b, 6)))
            }
        };
        found
    }

    /// A parameter or local whose type is not known: it shadows every outer
    /// name, so nothing is resolved through it.
    fn is_untyped_local(&self, f: usize, d: Option<usize>, name: &str) -> bool {
        let defs = &self.files[f].defs;
        let mut cur = d;
        while let Some(x) = cur {
            if let Some(&li) = self.locals.get(&(f, x, name.to_string())) {
                return self.files[f].locals[li].path.is_empty();
            }
            cur = defs[x].parent;
        }
        false
    }

    /// `this.name` inside a function assigned to an object: another function
    /// of the same file assigned to that object.
    fn object_member(&self, f: usize, d: Option<usize>, name: &str) -> Option<DefRef> {
        let defs = &self.files[f].defs;
        let mut cur = d;
        let obj = loop {
            let x = cur?;
            if let Some(o) = &defs[x].object {
                break o.clone();
            }
            cur = defs[x].parent;
        };
        let cands: Vec<DefRef> = defs
            .iter()
            .enumerate()
            .filter(|(_, x)| x.name == name && x.object.as_deref() == Some(obj.as_str()))
            .map(|(i, _)| (f, i))
            .collect();
        self.best(cands)
    }

    /// A local assigned something external (`ts := httptest.NewServer()`):
    /// its methods are the external type's, never a project namesake's.
    /// The members named `name` of a namespace: its block's, and in C, C++
    /// and C# those of every block that opens the same namespace
    /// (`namespace cpr { … }` in each header), its own first.
    fn namespace_members(&self, c: DefRef, name: &str) -> Vec<DefRef> {
        let own = self.children[c.0].get(&c.1).and_then(|m| m.get(name));
        let mut out: Vec<DefRef> = own.into_iter().flatten().map(|&k| (c.0, k)).collect();
        // (C# `namespace Shop.Text { … }` is opened by every file that puts a
        // type in it, as a C++ namespace is by every header)
        let Some(fam @ (Family::C | Family::CSharp)) = self.family[c.0] else {
            return out;
        };
        let key = qual_key(fam, &self.def(c).qualname);
        for &other in self.by_qual.get(&key).into_iter().flatten() {
            if other == c || self.def(other).kind != DefKind::Namespace {
                continue;
            }
            let kids = self.children[other.0].get(&other.1).and_then(|m| m.get(name));
            out.extend(kids.into_iter().flatten().map(|&k| (other.0, k)));
        }
        // C++ `inline namespace v2` inside the namespace: its members are the
        // namespace's ([namespace.def])
        if fam == Family::C {
            let blocks: Vec<DefRef> = std::iter::once(c)
                .chain(self.by_qual.get(&key).into_iter().flatten().copied())
                .collect();
            for b in blocks {
                let inline = &self.files[b.0].inline_namespaces;
                if inline.is_empty() {
                    continue;
                }
                for kids in self.children[b.0].get(&b.1).into_iter().flat_map(|m| m.values()) {
                    for &k in kids {
                        let d = &self.files[b.0].defs[k];
                        if d.kind == DefKind::Namespace && inline.contains(&d.start_byte) {
                            let inner = self.children[b.0].get(&k).and_then(|m| m.get(name));
                            out.extend(inner.into_iter().flatten().map(|&x| (b.0, x)));
                        }
                    }
                }
            }
            out.sort();
            out.dedup();
        }
        // a method defined for a type in the block (`Response Session::Get()
        // { … }` inside `namespace cpr { }`) is the type's, no name of the
        // namespace
        out.retain(|m| {
            let d = self.def(*m);
            !(d.owner.is_some() && d.kind.is_callable())
        });
        out
    }

    /// The other specializations of the C++ template `c` is one of (and the
    /// template itself): `AsyncWrapper<T, false>` beside `AsyncWrapper<T, true>`.
    fn specializations(&self, c: DefRef) -> Vec<DefRef> {
        let d = self.def(c);
        if self.family[c.0] != Some(Family::C) || !d.kind.is_class_like() {
            return Vec::new();
        }
        let q = specialized(&d.qualname).unwrap_or(&d.qualname);
        let mut out: Vec<DefRef> = self
            .by_qual
            .get(&qual_key(Family::C, q))
            .into_iter()
            .flatten()
            .copied()
            .filter(|o| *o != c && self.def(*o).kind.is_class_like())
            .collect();
        out.sort();
        out.dedup();
        out
    }

    /// Of `t` and the other specializations of its template, the one the
    /// written arguments fit (`<Response, true>` fits `<T, true>`, not `<T,
    /// false>`): a value argument must be the same, a type one fits any. `t`
    /// when none or several fit.
    fn specialization_for(&self, t: DefRef, args: &[String]) -> DefRef {
        if args.is_empty() {
            return t;
        }
        let mut all = vec![t];
        all.extend(self.specializations(t));
        let fits: Vec<DefRef> = all
            .into_iter()
            .filter(|c| {
                let spec = specialization_args(&self.def(*c).name);
                !spec.is_empty()
                    && spec
                        .iter()
                        .enumerate()
                        .all(|(i, s)| !literal_arg(s) || args.get(i).is_some_and(|a| a == s))
            })
            .collect();
        match fits.as_slice() {
            [one] => *one,
            _ => t,
        }
    }

    /// The slot of a struct a `SlotFact` fills: the field of the holder's
    /// type it names, or the one at its position among the struct's fields
    /// (data fields count) when the initializer gives none. A slot is a
    /// function pointer, so a callable definition of its struct.
    fn slot_of(&self, f: usize, s: &crate::facts::SlotFact) -> Option<DefRef> {
        // the holder's struct, or a field of an anonymous struct type (`ac->ev`
        // of `struct { … } ev;`), which answers for its members
        let t = match self.type_of_path(f, s.from_def, &s.holder, false) {
            Some(t) if self.def(t).kind.is_class_like() => t,
            _ => match self.resolve_path(f, s.from_def, &s.holder, true) {
                Some((Target::Def(x), _)) if self.def(x).inline_body.is_some() => x,
                _ => return None,
            },
        };
        let inline = self.def(t).inline_body;
        // (an anonymous struct's members are the outer struct's, in its body)
        let (owner, range) = match inline {
            Some(r) => (self.def(t).parent.map(|p| (t.0, p))?, Some(r)),
            None => (t, None),
        };
        let slot = match (&s.field, s.position) {
            (Some(name), _) if inline.is_some() => self.best(self.members(t, name))?,
            (Some(name), _) => self.class_member(t, name, 2)?,
            (None, Some(i)) => {
                let mut fields: Vec<DefRef> = self.children[owner.0]
                    .get(&owner.1)
                    .into_iter()
                    .flat_map(|m| m.values().flatten())
                    .map(|&d| (owner.0, d))
                    .filter(|c| {
                        let x = self.def(*c);
                        let inside =
                            range.is_none_or(|(lo, hi)| lo <= x.start_byte && x.end_byte <= hi);
                        inside
                            && (x.kind.is_state()
                                || (x.kind.is_callable() && x.kind != DefKind::Constructor))
                    })
                    .collect();
                fields.sort_by_key(|c| self.def(*c).start_byte);
                fields.dedup();
                *fields.get(usize::try_from(i).ok()?)?
            }
            _ => return None,
        };
        self.def(slot).kind.is_callable().then_some(slot)
    }

    /// JS `this.lookup()` in an ES5 constructor `function View() {…}`: the
    /// function assigned to `View.prototype.lookup` (`Def::object` `View`).
    fn constructor_member(&self, f: usize, d: Option<usize>, name: &str) -> Option<DefRef> {
        let defs = &self.files[f].defs;
        let mut cur = d;
        while let Some(x) = cur {
            if defs[x].kind.is_callable() && defs[x].object.is_none() {
                let ctor = defs[x].name.as_str();
                let cands: Vec<DefRef> = defs
                    .iter()
                    .enumerate()
                    .filter(|(_, y)| y.name == name && y.object.as_deref() == Some(ctor))
                    .map(|(i, _)| (f, i))
                    .collect();
                if !cands.is_empty() {
                    return self.best(cands);
                }
            }
            cur = defs[x].parent;
        }
        None
    }

    /// JS `this.x` in a function expression no definition holds, by what the
    /// function is attached to (`RefFact::this_of`): `app[method] = function
    /// () { this.route() }` is `app.route`; a callback a Mongoose schema is
    /// handed (`UserSchema.path('name').validate(function () {
    /// this.skipValidation() })`) runs on a document, whose methods are the
    /// file's `UserSchema.methods`, `INFERRED`.
    fn this_of_member(&self, f: usize, name: &str) -> Option<(DefRef, Confidence)> {
        let of = (*self.cur.borrow())
            .filter(|(cf, _)| *cf == f)
            .and_then(|(cf, ri)| self.files[cf].refs[ri].this_of.clone())?;
        let defs = &self.files[f].defs;
        let named = |object: &str| -> Vec<DefRef> {
            defs.iter()
                .enumerate()
                .filter(|(_, y)| y.name == name && y.object.as_deref() == Some(object))
                .map(|(i, _)| (f, i))
                .collect()
        };
        let head = of.first()?;
        if of.len() == 1 {
            return self.best(named(head)).map(|m| (m, Confidence::Extracted));
        }
        let schema = defs.iter().any(|y| {
            y.name == *head
                && y.kind.is_state()
                && y.value_path.as_deref().is_some_and(|v| v.last().is_some_and(|s| s == "Schema"))
        });
        schema.then(|| self.best(named("methods"))).flatten().map(|m| (m, Confidence::Inferred))
    }

    /// JS: a member of what a framework hands a function. A parameter named
    /// `req` / `request` (`res` / `response`) of no written type, in a
    /// project that builds the framework's request (response) object
    /// itself, express's `Object.create(http.IncomingMessage.prototype)`:
    /// the functions assigned to that object, or to an `app.request`
    /// (`app.response`). A local constructing a Mongoose model (`new
    /// Article(…)`, `Article` a `mongoose.model('Article')`): a function of
    /// the `methods` of the schema the model is registered with.
    fn handed_member(&self, f: usize, d: Option<usize>, head: &str, name: &str) -> Option<DefRef> {
        if self.local_type(f, d, head).is_some() {
            return None;
        }
        let bare = head.split('@').next().unwrap_or(head);
        let untyped = head.contains('@') || self.is_untyped_local(f, d, head);
        let proto = match bare {
            "req" | "request" if untyped => Some(("IncomingMessage", "request")),
            "res" | "response" if untyped => Some(("ServerResponse", "response")),
            _ => None,
        };
        if let Some((created, alias)) = proto {
            let mut cands: Vec<DefRef> = Vec::new();
            let mut built = false;
            let objects = self.created_from.get(created).into_iter().flatten();
            for (g, obj) in objects.filter(|(g, _)| self.family[*g] == Some(Family::Ecma)) {
                built = true;
                let members = self.object_members.get(&(obj.clone(), name.to_string()));
                cands.extend(members.into_iter().flatten().filter(|m| m.0 == *g).copied());
            }
            // (only a project that builds the object itself)
            if !built {
                return None;
            }
            // (`app.response.message = function`: what the app adds to them)
            let added = self.object_members.get(&(alias.to_string(), name.to_string()));
            cands.extend(
                added
                    .into_iter()
                    .flatten()
                    .filter(|m| self.family[m.0] == Some(Family::Ecma))
                    .copied(),
            );
            return self.best(cands);
        }
        // the document a Mongoose model's static hands its callback (`User.
        // load(options, function (err, user) {…})`: the second parameter)
        if let Some((bare, at)) = head.split_once('@') {
            let at: usize = at.parse().ok()?;
            let (_, callee, params) = self.files[f].callbacks.iter().find(|(b, ..)| *b == at)?;
            if params.iter().position(|p| p == bare) != Some(1) || callee.len() != 2 {
                return None;
            }
            let Some(Target::Def(v)) = self.resolve_name(f, d, &callee[0]) else { return None };
            let model = self.def(v).model_name.clone()?;
            return self.model_method(&model, name);
        }
        // a document of a Mongoose model
        let path = self.local_path(f, d, head)?;
        let [model] = path.as_slice() else { return None };
        let Some(Target::Def(v)) = self.resolve_name(f, d, model) else { return None };
        let registered = self.def(v).model_name.clone()?;
        self.model_method(&registered, name)
    }

    /// A function of the `methods` of the schema the Mongoose model
    /// `model` is registered with (`mongoose.model('Article', ArticleSchema)`).
    fn model_method(&self, model: &str, name: &str) -> Option<DefRef> {
        let mut cands: Vec<DefRef> = Vec::new();
        let methods = self.object_members.get(&("methods".to_string(), name.to_string()));
        let methods: Vec<DefRef> = methods.into_iter().flatten().copied().collect();
        for g in self.model_files.get(model).into_iter().flatten() {
            cands.extend(methods.iter().filter(|m| m.0 == *g).copied());
        }
        self.best(cands)
    }

    /// Whether the project builds a framework's request or response object
    /// itself (`Object.create(http.IncomingMessage.prototype)`).
    fn builds_framework_object(&self) -> bool {
        self.created_from.contains_key("IncomingMessage")
            || self.created_from.contains_key("ServerResponse")
    }

    /// JS `req.article.removeComment()`, `const article = req.article`: the
    /// receiver is a property of the request that project code assigns
    /// (`req.article = yield Article.load(id)` in a middleware).
    fn attached_to_request(&self, f: usize, d: Option<usize>, path: &[String]) -> bool {
        let is_req = |h: &str| matches!(h.split('@').next().unwrap_or(h), "req" | "request");
        let prop = match path {
            [h, p, ..] if is_req(h) && path.len() >= 3 => Some(p.clone()),
            [h, ..] => self
                .local_path(f, d, h)
                .and_then(|lp| match lp.as_slice() {
                    [r, p] if is_req(r) => Some(p.clone()),
                    _ => None,
                })
                .or_else(|| {
                    // (`const article = req.article`, which makes no typed
                    // local)
                    self.files[f]
                        .request_aliases
                        .iter()
                        .find(|(n, _)| n == h)
                        .map(|(_, p)| p.clone())
                }),
            _ => None,
        };
        let Some(prop) = prop else { return false };
        self.request_props.contains(&prop)
    }

    /// The path a local's declaration gives it (its type as written).
    fn local_path(&self, f: usize, d: Option<usize>, name: &str) -> Option<&Vec<String>> {
        let defs = &self.files[f].defs;
        let mut cur = d;
        while let Some(x) = cur {
            if let Some(&li) = self.locals.get(&(f, x, name.to_string())) {
                return Some(&self.files[f].locals[li].path);
            }
            cur = defs[x].parent;
        }
        None
    }

    /// The first name of the type a local's declaration writes (C, C++:
    /// `std` of `std::promise<void> release;`).
    fn local_written_head(&self, f: usize, d: Option<usize>, name: &str) -> Option<&str> {
        let defs = &self.files[f].defs;
        let mut cur = d;
        while let Some(x) = cur {
            if let Some(&li) = self.locals.get(&(f, x, name.to_string())) {
                return self.files[f].locals[li].written_head.as_deref();
            }
            cur = defs[x].parent;
        }
        None
    }

    fn is_external_local(&self, f: usize, d: Option<usize>, name: &str) -> bool {
        let defs = &self.files[f].defs;
        let mut cur = d;
        while let Some(x) = cur {
            if let Some(&li) = self.locals.get(&(f, x, name.to_string())) {
                let l = &self.files[f].locals[li];
                let head = l.path.first().map(String::as_str).unwrap_or("");
                if head.is_empty() || matches!(head, "self" | "super" | "()") {
                    return false;
                }
                return matches!(self.resolve_name(f, Some(x), head), Some(Target::Ext(_, _)));
            }
            cur = defs[x].parent;
        }
        false
    }

    /// A local written with a type whose capital head names nothing of the
    /// project (`$p = new \ReflectionProperty(…)`, `let a: SocketAddr`): a
    /// library's type the file imports by no name the engine reads.
    fn is_outside_local(&self, f: usize, d: Option<usize>, name: &str) -> bool {
        let defs = &self.files[f].defs;
        let mut cur = d;
        while let Some(x) = cur {
            if let Some(&li) = self.locals.get(&(f, x, name.to_string())) {
                let l = &self.files[f].locals[li];
                // (PHP `new \\ReflectionProperty(…)`: the global namespace's
                // `\\` is no part of the name)
                let start = usize::from(l.path.first().is_some_and(|h| h == "\\"));
                let head = l.path.get(start).map(String::as_str).unwrap_or("");
                if start == 1 {
                    return head.chars().next().is_some_and(|c| c.is_uppercase())
                        && matches!(
                            self.resolve_path(f, Some(x), &l.path[..2], true),
                            Some((Target::Ext(..), _)) | None
                        );
                }
                return head.chars().next().is_some_and(|c| c.is_uppercase())
                    && !self.by_name.contains_key(head)
                    && !self.is_local_name(f, Some(x), head)
                    && self.resolve_name(f, Some(x), head).is_none();
            }
            cur = defs[x].parent;
        }
        false
    }

    /// Whether `recv` (a path with its last name taken off) is something the
    /// source writes with a library's type: the result of a project function
    /// declared to return one, a field or variable declared as one, or a local
    /// assigned such a thing.
    fn receiver_is_library(&self, f: usize, d: Option<usize>, recv: &[String]) -> bool {
        let held: Vec<String>;
        let mut recv = recv;
        if let [name] = recv {
            let defs = &self.files[f].defs;
            let mut cur = d;
            let mut local = None;
            while let Some(x) = cur {
                if let Some(&li) = self.locals.get(&(f, x, name.clone())) {
                    local = Some(self.local_in_force(f, x, name, li));
                    break;
                }
                cur = defs[x].parent;
            }
            match local {
                Some(li) if !self.files[f].locals[li].declared => {
                    held = self.files[f].locals[li].path.clone();
                    recv = &held;
                }
                // (`var buf bytes.Buffer`: declared with a library's type)
                Some(li) => {
                    let head = self.files[f].locals[li].path.first();
                    // (written through an import, or a name the project has
                    // nothing of: `bytes.Buffer`, `Buffer`)
                    return head.is_some_and(|h| {
                        !self.is_local_name(f, d, h)
                            && match self.resolve_name(f, d, h) {
                                Some(Target::Ext(..)) => true,
                                None => !self.by_name.contains_key(h.as_str()),
                                _ => false,
                            }
                    });
                }
                None => {}
            }
        }
        let callee = match recv.split_last() {
            Some((last, before)) if last == "()" => before,
            _ => recv,
        };
        if callee.is_empty() || callee.iter().any(|s| s.starts_with('[')) {
            return false;
        }
        let guessed = self.guessed.get();
        let calling = self.calling.replace(false);
        let at = self.resolve_path_inner(f, d, callee, false, false);
        *self.calling.borrow_mut() = calling;
        self.guessed.set(guessed);
        matches!(at, Some((Target::Def(x), Confidence::Extracted)) if self.library_typed(x))
    }

    /// Whether what a field or variable holds, or a function returns, is
    /// written as a type the project does not define.
    fn library_typed(&self, x: DefRef) -> bool {
        let def = self.def(x);
        let written = if def.kind.is_callable() {
            self.return_path(x)
        } else if def.kind.is_state() {
            def.declared_type.as_ref()
        } else {
            None
        };
        let Some(head) = written.and_then(|p| p.first()) else { return false };
        matches!(self.resolve_name(x.0, def.parent, head), Some(Target::Ext(..)))
    }

    /// A local that does not hide the outer name: a plain assignment where
    /// assigning declares nothing, or, for a bare call (`called`), a local
    /// with a written type (`baseUrl(…)` beside `String baseUrl` calls the
    /// method, not the string) or one assigned from the outer thing of its own
    /// name (`let agent = agent();`: the call is the function's; a longer
    /// initializer is `own_initializer`'s).
    fn local_keeps_outer(&self, f: usize, d: Option<usize>, name: &str, called: bool) -> bool {
        let defs = &self.files[f].defs;
        let mut cur = d;
        while let Some(x) = cur {
            if let Some(&li) = self.locals.get(&(f, x, name.to_string())) {
                let l = &self.files[f].locals[li];
                // (a use written before the local is declared is no use of
                // it: `stream(…)` above `val stream = response.body()`)
                // (where a declaration is what makes a local: an assignment
                // can be one of several, a comprehension binds after its use;
                // in Ruby the first assignment the parser meets makes the
                // local, so a use above it is a method call)
                let declares = !matches!(self.family[f], Some(Family::Python | Family::Php));
                let first = self.first_declared(f, x, name, li);
                let before =
                    declares && (*self.site.borrow()).is_some_and(|(sf, at)| sf == f && at < first);
                // C, C++: a variable hides a function of its name (they share
                // one scope), unless it is of a project type, where the walk
                // has no call operator to name
                if called
                    && !before
                    && l.declared
                    && self.family[f] == Some(Family::C)
                    && self
                        .local_type(f, d, name)
                        .is_none_or(|(t, _)| !self.def(t).kind.is_class_like())
                {
                    return false;
                }
                // (`baseUrl(…)` beside `String baseUrl`: only where methods
                // and variables are names of two kinds; anywhere else the
                // call is of the local, whatever its type says)
                let two_kinds = matches!(self.files[f].lang, Some(Lang::Java | Lang::Kotlin));
                return l.assigned
                    || before
                    || (called
                        && ((l.declared && two_kinds)
                            || l.path.first().is_some_and(|h| h == name)));
            }
            cur = defs[x].parent;
        }
        false
    }

    /// The definition around `d` (or `d` itself) that declares a type
    /// parameter `name`, and the type that bounds it.
    fn type_param_around(
        &self,
        f: usize,
        d: Option<usize>,
        name: &str,
    ) -> Option<(usize, Option<Vec<String>>)> {
        let defs = &self.files[f].defs;
        let mut cur = d;
        let mut last = None;
        while let Some(x) = cur {
            if let Some(i) = defs[x].type_params.iter().position(|p| p == name) {
                let bound = defs[x].type_bounds.get(i).cloned().flatten();
                // (Go: a receiver's name for the type's parameter has the
                // type's bound)
                let bound = bound.or_else(|| {
                    (self.family[f] == Some(Family::Go) && defs[x].kind.is_callable())
                        .then(|| self.class_of(f, Some(x)))
                        .flatten()
                        .and_then(|c| self.def(c).type_bounds.get(i).cloned().flatten())
                });
                return Some((x, bound));
            }
            last = Some(x);
            cur = defs[x].parent;
        }
        // Rust: a method of `impl<S: Sink> Core<S>` has no parent; the
        // parameters of the type it is written for are in scope in it
        let owner = defs[last?].owner.as_deref()?;
        if self.family[f] != Some(Family::Rust) {
            return None;
        }
        let x = defs.iter().position(|c| {
            c.kind.is_class_like() && c.name == owner && c.type_params.iter().any(|p| p == name)
        })?;
        let i = defs[x].type_params.iter().position(|p| p == name)?;
        Some((x, defs[x].type_bounds.get(i).cloned().flatten()))
    }

    /// Whether `path` is the value a local named like its head is declared
    /// with (`val builder = builder(x).tag(y)`): the declaration does not
    /// see itself, so there the head is the outer name.
    fn own_initializer(&self, f: usize, d: Option<usize>, path: &[String]) -> bool {
        let Some(name) = path.first() else { return false };
        let defs = &self.files[f].defs;
        let mut cur = d;
        while let Some(x) = cur {
            if let Some(&li) = self.locals.get(&(f, x, name.clone())) {
                let l = &self.files[f].locals[li];
                // the whole value, or a part of its chain (`tls::Config::builder()`
                // in `let tls = tls::Config::builder().build()`)
                return l.path.len() > 1
                    && l.path[0] == *name
                    && (path.starts_with(&l.path) || (path.len() > 1 && l.path.starts_with(path)));
            }
            cur = defs[x].parent;
        }
        false
    }

    /// The local `name` of a block outside every definition that the use
    /// being resolved is written in.
    fn file_block_local(&self, f: usize, name: &str) -> Option<&crate::facts::Local> {
        let (_, at) = (*self.site.borrow()).filter(|(sf, _)| *sf == f)?;
        self.files[f]
            .locals
            .iter()
            .filter(|l| l.from_def.is_none() && !l.field && l.until != 0 && l.name == name)
            .filter(|l| l.byte <= at && at < l.until && (l.after == 0 || l.after <= at))
            .max_by_key(|l| l.byte)
    }

    /// Whether the name is a local or parameter declared with its type.
    fn declared_local(&self, f: usize, d: Option<usize>, name: &str) -> bool {
        let defs = &self.files[f].defs;
        let mut cur = d;
        while let Some(x) = cur {
            if let Some(&li) = self.locals.get(&(f, x, name.to_string())) {
                let l = &self.files[f].locals[self.local_in_force(f, x, name, li)];
                return l.declared && !l.assigned;
            }
            cur = defs[x].parent;
        }
        false
    }

    /// A "local" recorded from a plain assignment that declares nothing.
    fn local_is_hint(&self, f: usize, d: Option<usize>, name: &str) -> bool {
        let defs = &self.files[f].defs;
        let mut cur = d;
        while let Some(x) = cur {
            if let Some(&li) = self.locals.get(&(f, x, name.to_string())) {
                return self.files[f].locals[li].assigned;
            }
            cur = defs[x].parent;
        }
        false
    }

    /// Whether a name is a parameter or local of the enclosing definitions.
    fn is_local_name(&self, f: usize, d: Option<usize>, name: &str) -> bool {
        let defs = &self.files[f].defs;
        let mut cur = d;
        // (one declared in a block is a local inside the block: `{ let count
        // = 5 } return count` names the outer `count`)
        let at = (*self.site.borrow()).filter(|(sf, _)| *sf == f).map(|(_, at)| at);
        while let Some(x) = cur {
            let key = (f, x, name.to_string());
            if let Some(&li) = self.locals.get(&key) {
                let all = self.shadows.get(&key);
                let mut each = all.into_iter().flatten().copied().chain(std::iter::once(li));
                let locals = &self.files[f].locals;
                if at.is_none_or(|at| each.any(|i| locals[i].until == 0 || at < locals[i].until)) {
                    return true;
                }
            }
            cur = defs[x].parent;
        }
        false
    }

    /// The other declarations of a C# `partial` type: the definitions of its
    /// fully qualified name with as many type parameters (`RestResponse<T>`
    /// beside `RestResponse` is another type).
    fn partials(&self, c: DefRef) -> Vec<DefRef> {
        let def = self.def(c);
        // TypeScript: the interfaces of one name in one scope are one
        // interface (declaration merging)
        if self.family[c.0] == Some(Family::Ecma) && def.kind == DefKind::Interface {
            return self.files[c.0]
                .defs
                .iter()
                .enumerate()
                .filter(|(i, d)| {
                    *i != c.1
                        && d.kind == DefKind::Interface
                        && d.qualname == def.qualname
                        && d.parent == def.parent
                })
                .map(|(i, _)| (c.0, i))
                .collect();
        }
        if self.family[c.0] != Some(Family::CSharp) || !def.kind.is_class_like() {
            return Vec::new();
        }
        let Some(fq) = self.fqn_of(c.0, c.1) else { return Vec::new() };
        self.fqn
            .get(&fq)
            .into_iter()
            .flatten()
            .copied()
            .filter(|r| {
                let other = self.def(*r);
                *r != c
                    && self.family[r.0] == Some(Family::CSharp)
                    && other.kind == def.kind
                    && other.type_params.len() == def.type_params.len()
            })
            .collect()
    }

    /// C#: a member the type named does not have, of another type of the
    /// same fully qualified name with another number of type parameters
    /// (`Maybe.Nothing<T>()` beside `class Maybe<T>` and `static class Maybe`:
    /// the name written without arguments is either).
    fn namesake_member(&self, c: DefRef, name: &str) -> Option<DefRef> {
        let def = self.def(c);
        if self.family[c.0] != Some(Family::CSharp) || !def.kind.is_class_like() {
            return None;
        }
        let fq = self.fqn_of(c.0, c.1)?;
        self.fqn
            .get(&fq)
            .into_iter()
            .flatten()
            .copied()
            .filter(|r| {
                let other = self.def(*r);
                *r != c
                    && self.family[r.0] == Some(Family::CSharp)
                    && other.kind.is_class_like()
                    && other.type_params.len() != def.type_params.len()
            })
            .find_map(|r| self.class_member(r, name, 4))
    }

    /// The field `name` of type `c`, declared on it or on another declaration
    /// of a partial type.
    fn field_of(&self, c: DefRef, name: &str) -> Option<(usize, usize)> {
        let own = |t: &DefRef| self.fields.get(t).and_then(|m| m.get(name)).copied();
        own(&c).or_else(|| self.partials(c).iter().find_map(own))
    }

    /// The types of the union a field is assigned from: the parameter of the
    /// method that assigns it (`self.stream = stream`, `stream: A | B`).
    fn field_alts(&self, c: DefRef, name: &str) -> Vec<DefRef> {
        let Some((fi, li)) = self.field_of(c, name) else { return Vec::new() };
        let l = &self.files[fi].locals[li];
        let (Some(m), [param]) = (l.from_def, l.path.as_slice()) else { return Vec::new() };
        let Some(p) = self.files[fi].defs[m].plist.iter().find(|p| p.name == *param) else {
            return Vec::new();
        };
        p.alts
            .iter()
            .filter_map(|a| self.type_of_path(fi, Some(m), a, true))
            .filter(|t| self.def(*t).kind.is_class_like())
            .collect()
    }

    /// The type of a field of a type (through its bases).
    fn field_type(&self, c: DefRef, name: &str, depth: usize) -> Option<(DefRef, Confidence)> {
        if let Some((fi, li)) = self.field_of(c, name) {
            let l = &self.files[fi].locals[li];
            let t = self.type_of_path(fi, l.from_def, &l.path, l.declared)?;
            let conf = if l.declared { Confidence::Extracted } else { Confidence::Inferred };
            return Some((t, conf));
        }
        if depth == 0 {
            return None;
        }
        self.bases(c).into_iter().find_map(|b| self.field_type(b, name, depth - 1))
    }

    /// The package-qualified name of a definition, for the families whose
    /// names live in packages or namespaces.
    fn fqn_of(&self, fi: usize, di: usize) -> Option<String> {
        let fam = self.family[fi]?;
        if !matches!(fam, Family::Jvm | Family::CSharp | Family::Php) {
            return None;
        }
        let d = &self.files[fi].defs[di];
        let qual = d.qualname.replace('\\', ".").replace("::", ".");
        match &self.files[fi].package {
            Some(p) => Some(format!("{}.{}", p.replace('\\', "."), qual)),
            None => Some(qual),
        }
    }

    pub fn def(&self, r: DefRef) -> &Def {
        &self.files[r.0].defs[r.1]
    }

    fn node_id(&self, r: DefRef) -> Option<&str> {
        self.def_ids.get(r.0)?.get(r.1)?.as_deref()
    }

    pub fn ext(&self, label: &str, eco: &'static str) -> usize {
        if let Some(&i) = self.ext_index.borrow().get(&(eco, label.to_string())) {
            return i;
        }
        let mut exts = self.exts.borrow_mut();
        exts.push(ExtNode { label: label.to_string(), eco });
        let i = exts.len() - 1;
        self.ext_index.borrow_mut().insert((eco, label.to_string()), i);
        i
    }

    fn scope_of(&self, f: usize) -> Scope {
        let mut s = Scope::default();
        let Some(fam) = self.family[f] else { return s };
        let _asking = Asking(&self.asking, self.asking.replace(Some(f)));
        for (i, imp) in self.files[f].imports.iter().enumerate() {
            let res = modules::resolve_import(self, fam, f, imp);
            let target = match &res {
                ModRes::Target(t) => Some(t.clone()),
                ModRes::Ext(label) => {
                    Some(Target::Ext(self.ext(label, fam.ecosystem()), Vec::new()))
                }
                ModRes::Missing => None,
            };
            if let Some(Target::Module(fs)) = &target {
                s.imported.extend(fs.iter().copied());
            }
            if let Some(t) = target {
                for (name, bind) in modules::bindings(self, fam, f, imp, &t) {
                    // (a `use` in a block binds the name in the block only)
                    if let Some((lo, hi)) = imp.scope {
                        s.scoped.push((lo, hi, name, bind, i));
                        continue;
                    }
                    s.bindings.entry(name).or_insert((bind, i));
                }
                if imp.form == ImportForm::Wildcard || modules::is_star(fam, imp) {
                    s.stars.push((t, i));
                }
            }
            s.modules.push(res);
        }
        s
    }

    // ---- lookups ---------------------------------------------------------

    /// The best of several definitions: one with a body over a prototype.
    /// The workspace repository a root-relative path is in (`""`: none).
    pub fn repo_of_path(&self, path: &str) -> &str {
        self.repos
            .iter()
            .filter(|r| path == r.as_str() || path.starts_with(&format!("{r}/")))
            .max_by_key(|r| r.len())
            .map_or("", |r| r.as_str())
    }

    fn best(&self, cands: impl IntoIterator<Item = DefRef>) -> Option<DefRef> {
        let mut v: Vec<DefRef> = cands.into_iter().collect();
        v.sort_by_key(|r| (self.def(*r).decl_only, *r));
        v.dedup();
        // (two repositories of a workspace each defining the name: the
        // asking file's own, as an import means the importer's module)
        if v.len() > 1 {
            if let Some(f) = *self.asking.borrow() {
                let home = &self.repo[f];
                if !home.is_empty() && v.iter().any(|c| self.repo[c.0] == *home) {
                    v.retain(|c| self.repo[c.0] == *home);
                }
            }
        }
        let first = *v.first()?;
        // A prototype stands for the definition with the same qualname.
        if self.def(first).decl_only {
            if let Some(defn) = self.definition_of(first) {
                return Some(defn);
            }
        }
        Some(first)
    }

    fn definition_of(&self, r: DefRef) -> Option<DefRef> {
        let d = self.def(r);
        let fam = self.family[r.0];
        let cands: Vec<DefRef> = self
            .by_qual
            .get(&fam.map_or_else(|| d.qualname.clone(), |x| qual_key(x, &d.qualname)))
            .into_iter()
            .flatten()
            .copied()
            .filter(|c| !self.def(*c).decl_only && self.family[c.0] == fam)
            .collect();
        (cands.len() == 1).then(|| cands[0])
    }

    /// Top-level `name` in files.
    fn top_in(&self, files: &[usize], name: &str) -> Option<DefRef> {
        // an `extension Session { … }` adds to a type, it does not declare the
        // name: in a module that only extends it, the name is still the type's
        // (only the files that define the name at their top level are read)
        let defining: &[usize] = self.top_files.get(name).map(Vec::as_slice).unwrap_or(&[]);
        let mut found: Vec<DefRef> = files
            .iter()
            .filter(|f| defining.binary_search(f).is_ok())
            .flat_map(|&f| self.top[f].get(name).into_iter().flatten().map(move |&d| (f, d)))
            // (an RSpec `let` is a name of its block only)
            .filter(|r| {
                self.def(*r).kind != DefKind::Extension && self.def(*r).scope_lines.is_none()
            })
            .collect();
        // TS: a name can be a type and a value at once (`export interface
        // $ZodString` beside `export const $ZodString`): where a type is
        // written it is the type, anywhere else the value
        if found.len() > 1 && found.iter().all(|r| self.family[r.0] == Some(Family::Ecma)) {
            let is_type =
                |r: &DefRef| matches!(self.def(*r).kind, DefKind::Interface | DefKind::TypeAlias);
            let wanted = self.naming_type();
            if found.iter().any(|r| is_type(r) == wanted) {
                found.retain(|r| is_type(r) == wanted);
            }
        }
        // TS: what a `namespace` holds is a name inside it (the walk up the
        // definitions around finds it there), not beside one the file declares
        if found.len() > 1 && found.iter().all(|r| self.family[r.0] == Some(Family::Ecma)) {
            let outer = |r: &DefRef| self.def(*r).parent.is_none();
            if found.iter().any(outer) {
                found.retain(outer);
            }
        }
        self.best(found)
    }

    /// The members of a type: its lexical children, and the methods attached
    /// to it by owner.
    fn members(&self, c: DefRef, name: &str) -> Vec<DefRef> {
        // a field of an anonymous struct type: the members its body declares,
        // which the struct around holds
        if let Some((lo, hi)) = self.def(c).inline_body {
            let Some(outer) = self.def(c).parent else { return Vec::new() };
            return self.children[c.0]
                .get(&outer)
                .and_then(|m| m.get(name))
                .into_iter()
                .flatten()
                .filter(|&&d| {
                    let x = self.def((c.0, d));
                    lo <= x.start_byte && x.end_byte <= hi
                })
                .map(|&d| (c.0, d))
                .collect();
        }
        let mut out: Vec<DefRef> = self.children[c.0]
            .get(&c.1)
            .and_then(|m| m.get(name))
            .into_iter()
            .flatten()
            .map(|&d| (c.0, d))
            .collect();
        if let Some(fam) = self.family[c.0] {
            let cname = &self.def(c).name;
            for &m in self.owner_methods.get(&(fam, cname.clone())).into_iter().flatten() {
                if self.def(m).name == name && self.owner_scope_matches(c, m) {
                    out.push(m);
                }
            }
        }
        // a C# partial type: what its other declarations hold
        for p in self.partials(c) {
            let more = self.children[p.0].get(&p.1).and_then(|m| m.get(name));
            out.extend(more.into_iter().flatten().map(|&d| (p.0, d)));
        }
        out
    }

    /// Whether an owner-attached method `m` can belong to type `c`: same Go
    /// package directory, same Rust crate, or anywhere for C++ and Swift.
    fn owner_scope_matches(&self, c: DefRef, m: DefRef) -> bool {
        match self.family[c.0] {
            Some(Family::Go) => dir_of(&self.files[c.0].path) == dir_of(&self.files[m.0].path),
            Some(Family::Rust) => {
                if self.modsys.crate_of(c.0) != self.modsys.crate_of(m.0) {
                    return false;
                }
                // two types of one name in a crate (a private `Config` per
                // module): the `impl` is of the one its own file names
                let name = &self.def(c).name;
                let twins = self.by_name.get(name).into_iter().flatten().any(|r| {
                    *r != c
                        && self.def(*r).kind.is_class_like()
                        && self.modsys.crate_of(r.0) == self.modsys.crate_of(c.0)
                });
                if !twins || m.0 == c.0 {
                    return !twins || self.top[m.0].get(name).is_none_or(|ds| ds.contains(&c.1));
                }
                // (from where the `impl` is written: inside an inline module,
                // that module's names)
                match self.resolve_name(m.0, self.def(m).parent, name) {
                    Some(Target::Def(x)) if self.def(x).kind.is_class_like() => x == c,
                    _ => true,
                }
            }
            _ => true,
        }
    }

    /// The bases of a type that resolve to project types.
    /// The one other type declared beside `c` under its name (a generic and
    /// a plain `RestResponse`, a class and its companion).
    fn namesake(&self, c: DefRef) -> Option<DefRef> {
        let d = self.def(c);
        let named = match d.parent {
            Some(par) => self.children[c.0].get(&par).and_then(|x| x.get(&d.name)),
            None => self.top[c.0].get(&d.name),
        }?;
        let others: Vec<usize> = named
            .iter()
            .copied()
            .filter(|&i| i != c.1 && self.files[c.0].defs[i].kind.is_class_like())
            .collect();
        match others.as_slice() {
            [one] => Some((c.0, *one)),
            _ => None,
        }
    }

    fn bases(&self, c: DefRef) -> Vec<DefRef> {
        if let Some((b, frame)) = self.bases_memo.borrow().get(&c) {
            self.trace.file(c.0);
            self.trace.absorb(frame);
            return b.clone();
        }
        // a cycle ends here
        self.bases_memo.borrow_mut().insert(c, (Vec::new(), Rc::default()));
        // worked out the same whoever asks first: kept across files, the
        // answer must not carry the state of the reference that asked
        let walk = self.suspend();
        let site = self.site.replace(None);
        let kind = self.ref_kind.replace(None);
        let asking = self.asking.replace(None);
        let depth = self.depth.replace(0);
        let typing = self.typing.replace(HashSet::new());
        let typing_value = self.typing_value.replace(false);
        let end_binds = self.end_binds.replace(None);
        let guessed = self.guessed.replace(false);
        let embedded = self.embedded_hit.replace(None);
        self.trace.open_kept();
        let mut out = self.written_bases(c);
        // a C# partial type has the bases any of its declarations writes
        for p in self.partials(c) {
            for b in self.written_bases(p) {
                if b != c && !out.contains(&b) {
                    out.push(b);
                }
            }
        }
        let frame = self.trace.close_kept();
        self.embedded_hit.set(embedded);
        self.guessed.set(guessed);
        *self.end_binds.borrow_mut() = end_binds;
        *self.typing_value.borrow_mut() = typing_value;
        *self.typing.borrow_mut() = typing;
        *self.depth.borrow_mut() = depth;
        *self.asking.borrow_mut() = asking;
        *self.ref_kind.borrow_mut() = kind;
        *self.site.borrow_mut() = site;
        self.restore(walk);
        self.bases_memo.borrow_mut().insert(c, (out.clone(), frame));
        out
    }

    /// Whether a type writes a base the project does not define: its members
    /// are not all the project's.
    fn has_outside_base(&self, c: DefRef) -> bool {
        !self.outside_bases(c).is_empty()
    }

    /// The last names of the bases a type writes that the project does not
    /// define.
    fn outside_bases(&self, c: DefRef) -> Vec<&str> {
        let f = &self.files[c.0];
        let cname = &self.def(c).name;
        let outer = self.def(c).parent;
        f.refs
            .iter()
            .filter(|r| {
                let mine =
                    r.from_def == Some(c.1) || r.from_owner.as_ref().is_some_and(|o| o == cname);
                mine && matches!(
                    r.kind,
                    RefKind::Inherits | RefKind::Implements | RefKind::Base | RefKind::MixesIn
                ) && !matches!(
                    self.resolve_path(c.0, outer, &r.path, true),
                    Some((Target::Def(_), _))
                )
            })
            .filter_map(|r| r.path.last().map(|s| s.trim_start_matches('\\')))
            .collect()
    }

    /// Whether a type implements a library type that declares members a
    /// caller names (`implements RequestInterface`, `extends Writable`): not
    /// a marker (`Sendable`, `JsonSerializable`), not an exception base, and
    /// not in Ruby, where a mix-in or a base class leaves a class's own
    /// methods its own.
    fn implements_library_type(&self, c: DefRef) -> bool {
        self.family[c.0] != Some(Family::Ruby)
            && self.outside_bases(c).iter().any(|b| !MARKER_BASES.contains(b))
    }

    /// The bases one declaration of a type writes.
    fn written_bases(&self, c: DefRef) -> Vec<DefRef> {
        let mut out: Vec<(DefRef, RefKind)> = Vec::new();
        let f = &self.files[c.0];
        let cname = &self.def(c).name;
        for r in &f.refs {
            let mine = r.from_def == Some(c.1) || r.from_owner.as_ref().is_some_and(|o| o == cname);
            if !mine
                || !matches!(
                    r.kind,
                    RefKind::Inherits
                        | RefKind::Implements
                        | RefKind::Base
                        | RefKind::MixesIn
                        | RefKind::Embeds
                )
            {
                continue;
            }
            let outer = self.def(c).parent;
            // the state the main loop gives a base it resolves (`bases` puts
            // the asker's back): whoever asks first, the same answer
            *self.types_only.borrow_mut() = true;
            *self.calling.borrow_mut() = false;
            *self.recv.borrow_mut() = r.recv.map(|i| (c.0, i));
            *self.args.borrow_mut() = r.args;
            *self.self_arg.borrow_mut() = r.self_arg;
            *self.cur.borrow_mut() = None;
            *self.site.borrow_mut() = Some((c.0, r.byte));
            *self.ref_kind.borrow_mut() = Some(r.kind);
            if let Some((Target::Def(b), _)) = self.resolve_path(c.0, outer, &r.path, true) {
                if b != c {
                    out.push((b, r.kind));
                } else if let Some(other) = self.namesake(c) {
                    // C# `RestResponse<T> : RestResponse`: the base is the
                    // other type of the name, not the class itself
                    out.push((other, r.kind));
                }
            }
        }
        // the order members are looked up in: Ruby's ancestors are the class,
        // its modules from the last included, then the superclass
        // (`include_modules_at`); a PHP trait's methods come before the
        // parent class's (spec 16-traits.md)
        match self.files[c.0].lang {
            Some(crate::lang::Lang::Ruby) => {
                let (mixed, rest): (Vec<_>, Vec<_>) =
                    out.into_iter().partition(|(_, k)| *k == RefKind::MixesIn);
                mixed.into_iter().rev().chain(rest).map(|(b, _)| b).collect()
            }
            Some(crate::lang::Lang::Php) => {
                let (mixed, rest): (Vec<_>, Vec<_>) =
                    out.into_iter().partition(|(_, k)| *k == RefKind::MixesIn);
                mixed.into_iter().chain(rest).map(|(b, _)| b).collect()
            }
            _ => out.into_iter().map(|(b, _)| b).collect(),
        }
    }

    /// A member of a type, through its bases.
    fn class_member(&self, c: DefRef, name: &str, depth: usize) -> Option<DefRef> {
        if let Some(m) = self.best(self.members(c, name)) {
            return Some(m);
        }
        if depth == 0 {
            return None;
        }
        // Go: the member at the shallowest depth of embedding, level by
        // level (the spec's Selectors; go/types lookupFieldOrMethodImpl)
        if self.family[c.0] == Some(Family::Go) {
            let mut level = self.bases(c);
            let mut seen: HashSet<DefRef> = HashSet::from([c]);
            for _ in 0..depth {
                level.retain(|b| seen.insert(*b));
                if level.is_empty() {
                    return None;
                }
                if let Some(m) = level.iter().find_map(|b| self.best(self.members(*b, name))) {
                    return Some(m);
                }
                level = level.iter().flat_map(|b| self.bases(*b)).collect();
            }
            return None;
        }
        self.bases(c).into_iter().find_map(|b| self.class_member(b, name, depth - 1))
    }

    /// The constructors a type declares, in source order.
    fn constructors(&self, c: DefRef) -> Vec<DefRef> {
        let full = &self.def(c).name;
        // (a C++ specialization's constructors carry the template's name)
        let cname = &specialized(full).unwrap_or(full).to_string();
        // (a C# partial type's are those of every file that declares it)
        let mut out: Vec<DefRef> = std::iter::once(c)
            .chain(self.partials(c))
            .flat_map(|p| {
                self.children[p.0]
                    .get(&p.1)
                    .into_iter()
                    .flat_map(|m| m.values().flatten())
                    .map(move |&d| (p.0, d))
            })
            .filter(|r| {
                let d = self.def(*r);
                (d.kind == DefKind::Constructor && d.name != "deinit")
                    || (d.kind.is_callable()
                        && (d.name == *cname
                            || matches!(
                                d.name.as_str(),
                                "__init__" | "constructor" | "initialize" | "__construct"
                            )))
            })
            .collect();
        out.sort();
        out
    }

    /// A member of a Ruby module or class that another file adds: `module
    /// Faraday` is opened again by every file that puts something in it.
    fn reopened_member(&self, c: DefRef, name: &str) -> Option<DefRef> {
        if self.family[c.0] != Some(Family::Ruby) {
            return None;
        }
        let key = format!("{}.{name}", qual_key(Family::Ruby, &self.def(c).qualname));
        self.best(
            self.by_qual
                .get(&key)
                .into_iter()
                .flatten()
                .copied()
                .filter(|r| self.family[r.0] == Some(Family::Ruby)),
        )
    }

    /// JS: a function assigned to a function as its member (`Dataset.extractData
    /// = function () { … }` beside `function Dataset() { … }`), in its file.
    fn assigned_member(&self, c: DefRef, name: &str) -> Option<DefRef> {
        if self.family[c.0] != Some(Family::Ecma) || !self.def(c).kind.is_callable() {
            return None;
        }
        let owner = self.def(c).name.as_str();
        let found = self.files[c.0].defs.iter().enumerate().filter(|(_, d)| {
            d.name == name && d.kind.is_callable() && d.object.as_deref() == Some(owner)
        });
        self.best(found.map(|(i, _)| (c.0, i)))
    }

    /// RSpec: a bare name in `shared_examples 'x' do … end` that no block
    /// around it declares is a `let` of the place the examples are included
    /// (`it_behaves_like 'x'`): the innermost one around that line, when
    /// every place that includes them gives the same.
    fn shared_example_let(&self, f: usize, d: Option<usize>, name: &str) -> Option<DefRef> {
        if self.family[f] != Some(Family::Ruby) || self.files[f].shared_examples.is_empty() {
            return None;
        }
        let line = (*self.cur.borrow())
            .filter(|(cf, _)| *cf == f)
            .map(|(cf, ri)| self.files[cf].refs[ri].line)
            .or_else(|| d.map(|x| self.files[f].defs[x].def_line))?;
        let shared = self.files[f]
            .shared_examples
            .iter()
            .filter(|(_, a, b)| *a <= line && line <= *b)
            .min_by_key(|(_, a, b)| b - a)?;
        let mut found: Vec<DefRef> = Vec::new();
        let including = self.example_includes.get(&shared.0).into_iter().flatten();
        for &(g, at) in including.filter(|(g, _)| self.family[*g] == Some(Family::Ruby)) {
            let file = &self.files[g];
            let at = &at;
            {
                let inner = self.top[g]
                    .get(name)
                    .into_iter()
                    .flatten()
                    .copied()
                    .filter(|&i| {
                        file.defs[i].scope_lines.is_some_and(|(a, b)| a <= *at && *at <= b)
                    })
                    .min_by_key(|&i| file.defs[i].scope_lines.map(|(a, b)| b - a));
                match inner {
                    Some(i) if !found.contains(&(g, i)) => found.push((g, i)),
                    _ => {}
                }
            }
        }
        match found.as_slice() {
            [one] => Some(*one),
            _ => None,
        }
    }

    /// A member a Ruby class has through what another opening of it mixes in
    /// or extends: `Faraday::Response.register_middleware` in a file that
    /// opens `class Response` again, beside `extend MiddlewareRegistry` in
    /// the file that declares it.
    fn reopened_base_member(&self, c: DefRef, name: &str) -> Option<DefRef> {
        if self.family[c.0] != Some(Family::Ruby) {
            return None;
        }
        let key = qual_key(Family::Ruby, &self.def(c).qualname);
        let mut openings: Vec<DefRef> = self
            .by_qual
            .get(&key)
            .into_iter()
            .flatten()
            .copied()
            .filter(|r| {
                *r != c
                    && self.family[r.0] == Some(Family::Ruby)
                    && self.def(*r).kind.is_class_like()
            })
            .collect();
        openings.sort();
        openings.into_iter().find_map(|r| self.class_member(r, name, 4))
    }

    /// What a JS/TS module is when it is used as a value (`const Foo =
    /// require('./foo'); new Foo()`): the definition it assigns to
    /// `module.exports` or exports as its default, through modules that pass
    /// another one on (`module.exports = require('./lib/app')`).
    fn module_default(&self, files: &[usize], depth: usize) -> Option<Target> {
        let f = *files.first()?;
        if self.family[f] != Some(Family::Ecma) || depth == 0 {
            return None;
        }
        files
            .iter()
            .find_map(|&g| modules::reexport(self, g, "default", depth - 1))
            .filter(|t| matches!(t, Target::Def(_)))
    }

    /// Go: a member a type has through what it is written as, when it has
    /// none of the name itself. An alias (`type A = T`) is T; a defined type
    /// (`type Copy Base`) has Base's fields and what Base's embedded fields
    /// promote, and, when Base is an interface, its methods, but never the
    /// methods declared for Base; a method declared on an alias of a type is
    /// the type's (`func (a A) M()`).
    fn go_type_member(&self, c: DefRef, name: &str, depth: usize) -> Option<Target> {
        let d = self.def(c);
        if !self.members(c, name).is_empty() {
            return None;
        }
        if d.kind == DefKind::TypeAlias {
            if let Some(ty) = &d.declared_type {
                let t = self.type_of_path(c.0, d.parent, ty, true).filter(|t| *t != c)?;
                return self.member(&Target::Def(t), name, depth - 1);
            }
            let under = d.value_path.as_ref()?;
            let u = self.type_of_path(c.0, d.parent, under, true).filter(|u| *u != c)?;
            let ud = self.def(u);
            if ud.kind == DefKind::Interface || ud.kind == DefKind::TypeAlias {
                return self.member(&Target::Def(u), name, depth - 1);
            }
            let found = self.member(&Target::Def(u), name, depth - 1)?;
            // (Base's own methods are not Copy's)
            if let Target::Def(m) = found {
                if self.def(m).kind.is_callable() && self.members(u, name).contains(&m) {
                    return None;
                }
            }
            return Some(found);
        }
        // a method written on an alias of this type
        self.go_aliases(c)
            .into_iter()
            .find_map(|a| self.members(a, name).first().map(|&m| Target::Def(m)))
    }

    /// C# `[Cache]`: the attribute class `CacheAttribute` before a class
    /// `Cache`; `[@Cache]` is `Cache` alone (the standard's 23.3).
    fn csharp_attribute(
        &self,
        f: usize,
        d: Option<usize>,
        path: &[String],
        strict: bool,
    ) -> Option<(Target, Confidence)> {
        let last = path.last()?;
        if let Some(bare) = last.strip_prefix('@') {
            let mut q = path.to_vec();
            *q.last_mut()? = bare.to_string();
            return self.resolve_path(f, d, &q, strict);
        }
        if !last.ends_with("Attribute") {
            let mut q = path.to_vec();
            q.last_mut()?.push_str("Attribute");
            if let Some(found @ (Target::Def(t), _)) = self.resolve_path(f, d, &q, strict) {
                if self.def(t).kind.is_class_like() {
                    return Some(found);
                }
            }
        }
        self.resolve_path(f, d, path, strict)
    }

    /// Go: the aliases (`type A = T`) whose type is `c`.
    fn go_aliases(&self, c: DefRef) -> Vec<DefRef> {
        // one map of the whole project's aliases: what it holds is the key
        // `go_aliases` (`alias_fingerprint`), not a read of every file
        self.trace.key(trace::key_hash("go_aliases", ""));
        if self.go_alias_map.borrow().is_none() {
            // (worked out from the top, whatever asks first)
            let depth = self.depth.replace(0);
            let saved = self.suspend();
            let typing = self.typing.replace(HashSet::new());
            self.trace.open();
            let mut map: HashMap<DefRef, Vec<DefRef>> = HashMap::new();
            for (f, file) in self.files.iter().enumerate() {
                if self.family[f] != Some(Family::Go) {
                    continue;
                }
                for (i, def) in file.defs.iter().enumerate() {
                    if def.kind != DefKind::TypeAlias {
                        continue;
                    }
                    let Some(ty) = &def.declared_type else { continue };
                    if let Some(t) = self.type_of_path(f, def.parent, ty, true) {
                        if t != (f, i) {
                            map.entry(t).or_default().push((f, i));
                        }
                    }
                }
            }
            self.trace.discard();
            *self.typing.borrow_mut() = typing;
            self.restore(saved);
            *self.depth.borrow_mut() = depth;
            *self.go_alias_map.borrow_mut() = Some(map);
        }
        self.go_alias_map.borrow().as_ref().and_then(|m| m.get(&c).cloned()).unwrap_or_default()
    }

    /// `name` looked up inside a target.
    fn member(&self, t: &Target, name: &str, depth: usize) -> Option<Target> {
        match t {
            Target::Def(c) => {
                let d = self.def(*c);
                // Go: an embedded field is named by its type (`f.basicWriter`
                // of `struct { basicWriter }`), which the struct embeds
                if self.family[c.0] == Some(Family::Go)
                    && d.kind == DefKind::Struct
                    && self.members(*c, name).is_empty()
                {
                    let embedded = self.bases(*c).into_iter().find(|b| self.def(*b).name == name);
                    if let Some(b) = embedded {
                        self.embedded_hit.set(Some(b));
                        return Some(Target::Def(b));
                    }
                }
                if self.family[c.0] == Some(Family::Go) && depth > 0 {
                    if let Some(found) = self.go_type_member(*c, name, depth) {
                        return Some(found);
                    }
                }
                if d.kind == DefKind::Namespace {
                    let all = self.namespace_members(*c, name);
                    // (two files can each define the name in the namespace:
                    // `leveldb::Benchmark` of each benchmark's own file)
                    if let Some(f) = *self.asking.borrow() {
                        let own: Vec<DefRef> = all.iter().copied().filter(|m| m.0 == f).collect();
                        if !own.is_empty() {
                            return self.best(own).map(Target::Def);
                        }
                        let seen: Vec<DefRef> = all
                            .iter()
                            .copied()
                            .filter(|m| {
                                self.scopes.get(f).is_some_and(|s| s.imported.contains(&m.0))
                            })
                            .collect();
                        if !seen.is_empty() {
                            return self.best(seen).map(Target::Def);
                        }
                    }
                    return self.best(all).map(Target::Def);
                }
                // TS: a function, class or enum merged with a `namespace` of
                // its name has what the namespace declares
                if self.family[c.0] == Some(Family::Ecma) && d.kind != DefKind::Namespace {
                    let merged = self
                        .by_qual
                        .get(&qual_key(Family::Ecma, &d.qualname))
                        .into_iter()
                        .flatten()
                        .copied()
                        .filter(|o| o.0 == c.0 && self.def(*o).kind == DefKind::Namespace)
                        .flat_map(|o| self.namespace_members(o, name))
                        .collect::<Vec<_>>();
                    if !merged.is_empty() && self.members(*c, name).is_empty() {
                        return self.best(merged).map(Target::Def);
                    }
                }
                // `Color.RED.label()`: an enum's value has the enum's members
                // (after what the value declares itself: Java `DEFAULT { … }`)
                if d.kind == DefKind::EnumMember {
                    if let Some(own) = self.class_member(*c, name, 4) {
                        return Some(Target::Def(own));
                    }
                    let of =
                        d.parent.map(|p| (c.0, p)).filter(|e| self.def(*e).kind.is_class_like());
                    if let Some(e) = of {
                        return self.member(&Target::Def(e), name, depth);
                    }
                }
                if name == "new" && self.family[c.0] == Some(Family::Ruby) {
                    return Some(Target::Def(*c)); // `Klass.new` constructs Klass
                }
                self.class_member(*c, name, 4)
                    .or_else(|| self.namesake_member(*c, name))
                    .or_else(|| self.reopened_member(*c, name))
                    .or_else(|| self.reopened_base_member(*c, name))
                    .or_else(|| self.assigned_member(*c, name))
                    .or_else(|| self.forwarded_member(*c, name, depth))
                    .map(Target::Def)
            }
            Target::Module(files) => {
                if let Some(d) = self.top_in(files, name) {
                    return Some(Target::Def(d));
                }
                if let Some(sub) = modules::submodule(self, files, name) {
                    return Some(sub);
                }
                if depth == 0 {
                    return None;
                }
                // re-exported: a name the module binds by importing it
                for &f in files {
                    if let Some((b, _)) = self.scopes.get(f).and_then(|s| s.bindings.get(name)) {
                        if let Some(t) = self.eval(b, depth - 1) {
                            return Some(t);
                        }
                    }
                    if let Some(t) = modules::reexport(self, f, name, depth - 1) {
                        return Some(t);
                    }
                    for (star, _) in self.scopes.get(f).map(|s| s.stars.as_slice()).unwrap_or(&[]) {
                        if matches!(star, Target::Ext(..)) {
                            continue;
                        }
                        if let Some(t) = self.member(star, name, depth - 1) {
                            return Some(t);
                        }
                    }
                }
                // CommonJS: what `require` gives is what the module assigns
                // to `module.exports`; a name not the module's own is that
                // thing's (`const Foo = require('./foo'); Foo.create()`)
                if name != "default" {
                    if let Some(d @ Target::Def(_)) = self.module_default(files, depth - 1) {
                        return self.member(&d, name, depth - 1);
                    }
                }
                None
            }
            Target::Dir(d) => modules::dir_member(self, d, name),
            Target::Ns(ns) => {
                let full = format!("{ns}.{name}");
                if let Some(d) = self.best(self.fqn.get(&full).into_iter().flatten().copied()) {
                    return Some(Target::Def(d));
                }
                // (a namespace of that name, in any family)
                FAMILIES
                    .iter()
                    .any(|fam| self.namespaces.contains(&(*fam, full.clone())))
                    .then_some(Target::Ns(full))
            }
            Target::Ext(e, rest) => {
                let mut r = rest.clone();
                r.push(name.to_string());
                Some(Target::Ext(*e, r))
            }
        }
    }

    fn eval(&self, b: &Bind, depth: usize) -> Option<Target> {
        match b {
            Bind::T(t) => Some(t.clone()),
            Bind::Member(m, name) => self.member(m, name, depth),
        }
    }

    /// The type a definition belongs to: its class, or the type it is
    /// declared for (receiver, impl, extension, `A::m`).
    fn class_of(&self, f: usize, d: Option<usize>) -> Option<DefRef> {
        let defs = &self.files[f].defs;
        let mut cur = d;
        while let Some(x) = cur {
            let def = &defs[x];
            if def.kind.is_class_like() {
                return Some((f, x));
            }
            if def.kind == DefKind::Extension {
                return self.type_named(f, &def.name);
            }
            if let Some(owner) = &def.owner {
                // (`impl<T> Holder<T>`: the type is `Holder`)
                let plain = owner.split('<').next().unwrap_or(owner).trim();
                let bare = plain.rsplit(['.', ':']).next().unwrap_or(plain);
                if let Some(t) = self.type_named(f, bare) {
                    return Some(t);
                }
            }
            cur = def.parent;
        }
        None
    }

    /// A type by bare name: in the file, then in its family's scope.
    fn type_named(&self, f: usize, name: &str) -> Option<DefRef> {
        let local: Vec<DefRef> = self.files[f]
            .defs
            .iter()
            .enumerate()
            .filter(|(_, d)| d.kind.is_class_like() && d.name == name && !d.anonymous)
            .map(|(i, _)| (f, i))
            .collect();
        if !local.is_empty() {
            return self.best(local);
        }
        let fam = self.family[f];
        let cands: Vec<DefRef> = self
            .by_name
            .get(name)
            .into_iter()
            .flatten()
            .copied()
            .filter(|r| self.family[r.0] == fam && self.def(*r).kind.is_class_like())
            .filter(|r| self.in_reach_by_name(f, *r))
            .collect();
        self.narrow(f, cands).map(|(r, _)| r)
    }

    /// A bare name as the language sees it from `d` in file `f`; in a type
    /// position, only a type (or a module / namespace on the way to one).
    fn resolve_name(&self, f: usize, d: Option<usize>, name: &str) -> Option<Target> {
        let t = self.resolve_name_any(f, d, name)?;
        if !self.acceptable(&t) {
            // nothing but a member had the name: in a type position, the type
            let ty = self.type_named(f, name)?;
            return Some(Target::Def(ty));
        }
        Some(t)
    }

    /// In a type position (`types_only`), only a type or a module / namespace
    /// can be what a name means; a member of the same name shadows nothing.
    fn acceptable(&self, t: &Target) -> bool {
        if !*self.types_only.borrow() {
            return true;
        }
        match t {
            Target::Def(r) => {
                let k = self.def(*r).kind;
                k.is_class_like() || matches!(k, DefKind::TypeAlias | DefKind::Namespace)
            }
            _ => true,
        }
    }

    fn resolve_name_any(&self, f: usize, d: Option<usize>, name: &str) -> Option<Target> {
        let fam = self.family[f]?;
        let defs = &self.files[f].defs;
        // Rust: a `use` in a block around the use shadows the module's names
        // (r[names.resolution.expansion.imports.shadowing]); the innermost
        // (with no site, the use is somewhere in `d`: a block of `d` is in
        // sight, the innermost first)
        let site = (*self.site.borrow()).filter(|(sf, _)| *sf == f).map(|(_, at)| at);
        let home = d.map(|x| (defs[x].start_byte, defs[x].end_byte));
        if site.is_some() || home.is_some() {
            let inner = self.scopes[f]
                .scoped
                .iter()
                .filter(|(lo, hi, n, _, _)| {
                    n == name
                        && match (site, home) {
                            (Some(at), _) => *lo <= at && at < *hi,
                            (None, Some((a, b))) => a <= *lo && *hi <= b,
                            _ => false,
                        }
                })
                .min_by_key(|(lo, hi, ..)| hi - lo);
            if let Some((_, _, _, b, imp)) = inner {
                if let Some(t) = self.eval(b, 3) {
                    if self.acceptable(&t) {
                        self.note_use(f, *imp, &t);
                        return Some(t);
                    }
                }
            }
        }
        // enclosing definitions: nested functions; class members where
        // methods see them
        let mut cur = d;
        while let Some(x) = cur {
            if let Some(k) = self.children[f].get(&x).and_then(|m| m.get(name)) {
                // (a closure no definition holds is a scope like a function)
                let callable_scope = defs[x].kind.is_callable() || defs[x].block;
                let member_scope = fam.implicit_this() && defs[x].kind.is_class_like();
                if callable_scope || member_scope || defs[x].kind == DefKind::Namespace {
                    if let Some(r) = self.best(k.iter().map(|&i| (f, i))) {
                        if self.acceptable(&Target::Def(r)) {
                            return Some(Target::Def(r));
                        }
                    }
                }
            }
            // (Ruby: a constant is looked up in the lexical nesting first, and
            // only then in the ancestors of the innermost class, the
            // `class_of` lookup below: vm_get_ev_const)
            let ruby_constant =
                fam == Family::Ruby && name.chars().next().is_some_and(char::is_uppercase);
            if fam.implicit_this() && defs[x].kind.is_class_like() && !ruby_constant {
                if let Some(m) = self.class_member((f, x), name, 4) {
                    if self.acceptable(&Target::Def(m)) {
                        return Some(Target::Def(m));
                    }
                }
            }
            cur = defs[x].parent;
        }
        if fam.implicit_this() {
            if let Some(c) = self.class_of(f, d) {
                if let Some(m) = self.class_member(c, name, 4) {
                    if self.acceptable(&Target::Def(m)) {
                        return Some(Target::Def(m));
                    }
                }
            }
        }
        // RSpec `let(:conn)`: a name of the block it is written in; of
        // several, the innermost around the use, and none outside them
        let scoped: Vec<usize> = self.top[f]
            .get(name)
            .into_iter()
            .flatten()
            .copied()
            .filter(|&i| defs[i].scope_lines.is_some())
            .collect();
        if !scoped.is_empty() {
            let at = (*self.cur.borrow())
                .filter(|(cf, _)| *cf == f)
                .map(|(cf, ri)| self.files[cf].refs[ri].line)
                .or_else(|| d.map(|x| defs[x].def_line));
            let inner = at.and_then(|line| {
                scoped
                    .iter()
                    .copied()
                    .filter(|&i| defs[i].scope_lines.is_some_and(|(a, b)| a <= line && line <= b))
                    .min_by_key(|&i| defs[i].scope_lines.map(|(a, b)| b - a))
            });
            if let Some(i) = inner {
                return Some(Target::Def((f, i)));
            }
        }
        if let Some(found) = self.shared_example_let(f, d, name) {
            return Some(Target::Def(found));
        }
        if let Some(r) = self.top_in(&[f], name) {
            if self.acceptable(&Target::Def(r))
                && self.cs_namespace_in_sight(f, d, r)
                && self.rust_module_in_sight(f, d, r)
            {
                return Some(Target::Def(r));
            }
        }
        let scope = &self.scopes[f];
        if let Some((b, imp)) = scope.bindings.get(name) {
            if let Some(t) = self.eval(b, 3) {
                if self.acceptable(&t) {
                    self.note_use(f, *imp, &t);
                    return Some(t);
                }
            }
        }
        // Scala: a wildcard import beats a member of the package another
        // compilation unit defines (Typer's `BindingPrec`: WildImport above
        // PackageClause); Java has it the other way round
        if self.files[f].lang == Some(crate::lang::Lang::Scala) {
            for (star, imp) in &scope.stars {
                if matches!(star, Target::Ext(..)) {
                    continue;
                }
                if let Some(t) = self.member(star, name, 2) {
                    if self.acceptable(&t) {
                        self.note_use(f, *imp, &t);
                        return Some(t);
                    }
                }
            }
        }
        if let Some(t) = modules::scope_lookup(self, fam, f, d, name) {
            if self.acceptable(&t) {
                return Some(t);
            }
        }
        for (star, imp) in &scope.stars {
            // an external module's contents are unknown: it names nothing
            if matches!(star, Target::Ext(..)) {
                continue;
            }
            if let Some(t) = self.member(star, name, 2) {
                if self.acceptable(&t) {
                    self.note_use(f, *imp, &t);
                    return Some(t);
                }
            }
        }
        // a spec: the methods of the modules `RSpec.configure` includes in
        // every example (`config.include Faraday::IntegrationHelpers`). A
        // name it calls that nothing else answers to can only be theirs
        if fam == Family::Ruby && crate::community::is_test_path(&self.files[f].path) {
            if let Some(m) = self.spec_helper_member(name) {
                return Some(Target::Def(m));
            }
        }
        None
    }

    /// A method named `name` of a module some file's `RSpec.configure`
    /// includes (`config.include M`): the one there is.
    fn spec_helper_member(&self, name: &str) -> Option<DefRef> {
        let mut found: Option<DefRef> = None;
        {
            for path in self.spec_includes.get("").into_iter().flatten() {
                let key = qual_key(Family::Ruby, &path.join("::"));
                let modules = self.by_qual.get(&key).into_iter().flatten().copied();
                for module in modules.filter(|m| self.def(*m).kind.is_class_like()) {
                    let hit = self
                        .members(module, name)
                        .into_iter()
                        .chain(self.reopened_member(module, name))
                        .find(|x| self.def(*x).kind.is_callable());
                    if let Some(x) = hit {
                        if found.is_some_and(|y| y != x) {
                            return None;
                        }
                        found = Some(x);
                    }
                }
            }
        }
        found
    }

    fn note_use(&self, f: usize, imp: usize, t: &Target) {
        if let Target::Def((g, _)) = t {
            if *g != f {
                self.trace.used(f, imp, *g);
                self.used.borrow_mut().entry((f, imp)).or_default().insert(*g);
            }
        }
    }

    /// A path (`["mod", "f"]`, `["self", "m"]`) as seen from `d` in `f`.
    /// `bases` is set when resolving a base list (no name-only fallback for
    /// members then).
    fn resolve_path(
        &self,
        f: usize,
        d: Option<usize>,
        path: &[String],
        bases: bool,
    ) -> Option<(Target, Confidence)> {
        let found = self.resolve_path_inner(f, d, path, bases, false);
        // `new Name(text)` inside a class with a method `Name`: `new` names a
        // type, never the method of that name
        let found = match found {
            Some((Target::Def(m), _))
                if *self.ref_kind.borrow() == Some(RefKind::New)
                    && self.def(m).kind.is_callable()
                    && self.def(m).kind != DefKind::Constructor
                    && self.files[f].lang.is_some_and(|l| {
                        matches!(
                            l,
                            Lang::CSharp
                                | Lang::Java
                                | Lang::JavaScript
                                | Lang::TypeScript
                                | Lang::Tsx
                                | Lang::Php
                                | Lang::Cpp
                        )
                    }) =>
            {
                let before = self.types_only.replace(true);
                let typed = self.resolve_path_inner(f, d, path, bases, false);
                *self.types_only.borrow_mut() = before;
                match typed {
                    Some((Target::Def(c), conf)) if self.def(c).kind.is_class_like() => {
                        Some((Target::Def(c), conf))
                    }
                    _ => found,
                }
            }
            other => other,
        };
        // a module called, constructed or named as a type is what it exports
        // as itself (`const express = require('../'); express()`)
        if let Some((Target::Module(fs), conf)) = &found {
            if *self.calling.borrow() || *self.types_only.borrow() || bases {
                if let Some(t) = self.module_default(fs, 3) {
                    return Some((t, *conf));
                }
            }
        }
        // Rust `io::Error::error_message(msg)`: a method a trait of the
        // project gives a library's type, called through the type's path
        let found = match found {
            Some((Target::Ext(..), _))
                if self.family[f] == Some(Family::Rust)
                    && *self.calling.borrow()
                    && !bases
                    && path.len() >= 2
                    && !path.iter().any(|s| s == "()" || s.starts_with('['))
                    && path[path.len() - 2].chars().next().is_some_and(char::is_uppercase)
                    && !RUST_CORE_METHODS.contains(&path[path.len() - 1].as_str()) =>
            {
                let (name, on) = (&path[path.len() - 1], &path[path.len() - 2]);
                let guessed = self.guessed.get();
                match self.extension_method(f, name, Some(on)) {
                    Some((t, Confidence::Extracted)) => Some((t, Confidence::Extracted)),
                    _ => {
                        self.guessed.set(guessed);
                        found
                    }
                }
            }
            other => other,
        };
        let found = found.or_else(|| {
            // C++ `port::CondVar cv;`, `typename SkipList<K, C>::Node*`: a
            // type written with a qualifier the walk could not follow is
            // looked up by its own name, as one written bare
            let as_type = self.naming_type();
            if self.family[f] == Some(Family::C)
                && as_type
                && path.len() >= 2
                && !path.iter().any(|s| s == "()" || s.starts_with('['))
            {
                if let Some(t) =
                    self.resolve_path_inner(f, d, &path[path.len() - 1..], bases, false)
                {
                    return Some(t);
                }
            }
            // Ruby `@pki.issue(…)`: a field the walk cannot type is a
            // receiver it cannot name
            if self.family[f] == Some(Family::Ruby) && !bases && *self.calling.borrow() {
                if let [head, field, method] = path {
                    // (not a method of the class called on the way)
                    let is_field = head == "self"
                        && self
                            .class_of(f, d)
                            .is_none_or(|c| self.class_member(c, field, 4).is_none());
                    if is_field {
                        return self.unknown_receiver(f, method, None);
                    }
                }
            }
            // an element (`xs[0].run()`) or a mapping (`xs.map { … }`) the
            // walk could not type is a receiver it cannot name: what is
            // called on it is found by a distinctive name alone
            // (or an opened wrapper: `query.fetch_one().await?.into_comment()`)
            let marker = |s: &String| {
                s == crate::refs::ELEMENT || s == crate::refs::MANY || s == crate::refs::OPENED
            };
            if let Some(at) = path.iter().rposition(marker) {
                let mut unnamed = vec!["()".to_string()];
                unnamed.extend_from_slice(&path[at + 1..]);
                return self.resolve_path_inner(f, d, &unnamed, bases, false);
            }
            // Laravel `auth()->user()`: the model the project authenticates
            // with, the one class that extends `Authenticatable`
            if self.family[f] == Some(Family::Php)
                && path.len() >= 5
                && path[0] == "auth"
                && path[1] == "()"
                && path[2] == "user"
                && path[3] == "()"
                && self.resolve_name(f, d, "auth").is_none_or(|t| matches!(t, Target::Ext(..)))
            {
                if let Some(model) = self.authenticatable_model() {
                    return self.walk(Target::Def(model), &path[4..], Confidence::Inferred);
                }
            }
            // C# top-level statements (`var builder = …; builder.Services.
            // AddJwt()`): a variable that names nothing in sight, called with
            // the name of an extension the file can see. (A capital head is a
            // library's type, `HttpUtility.UrlEncode(s)`: a static call.)
            if self.family[f] == Some(Family::CSharp)
                && path.len() >= 2
                && path[0].chars().next().is_some_and(|c| c.is_lowercase())
                && !bases
                && *self.calling.borrow()
                && !path.iter().any(|s| s.starts_with('[') || s.contains('@'))
                && !self.is_local_name(f, d, &path[0])
                && self.resolve_name(f, d, &path[0]).is_none()
                && self.qualified(f, d, &path[..1]).is_none()
            {
                if let Some(found) = self.extension_method(f, path.last()?, None) {
                    return Some(found);
                }
            }
            if let Some(found) = self.library_extension_call(f, d, path, bases) {
                return Some(found);
            }
            // `let request = request.with_agent(a)`: the name before the
            // declaration is one the facts no longer hold (a parameter it
            // shadows); only the method's name says what is called
            (path.len() == 2
                && !bases
                && *self.calling.borrow()
                && self.own_initializer(f, d, path))
            .then(|| self.unknown_receiver(f, &path[1], None))
            .flatten()
        });
        // a guess by a method's name on a receiver the source says is a
        // library's (`jar := newJar()` with `func newJar() *cookiejar.Jar`,
        // `c.Flags().HasFlags()`): the method is the library's
        match found {
            // (an extension written for a library's type is what such a
            // receiver can be asked)
            Some((Target::Def(t), Confidence::Inferred))
                if path.len() >= 2
                    && self.def(t).receiver.is_none()
                    // (nor a method a project trait gives a library's type:
                    // `impl UriExt for Uri`)
                    && self
                        .class_of(t.0, Some(t.1))
                        .is_some_and(|c| self.def(c).kind != DefKind::Interface)
                    && self.receiver_is_library(f, d, &path[..path.len() - 1]) =>
            {
                None
            }
            other => other,
        }
    }

    /// A call the walk could not follow, by the name of the one extension in
    /// sight written for a type the project does not define
    /// (`e.Name.LocalName.Squash()` beside `Squash(this string s)`,
    /// `user.email.isEmailValid()` beside `fun String.isEmailValid()`):
    /// `INFERRED`. The receiver is something the walk gives no project type
    /// (a library's member, a chain of them); one it does type has the
    /// extensions written for that type, asked where the walk ends.
    fn library_extension_call(
        &self,
        f: usize,
        d: Option<usize>,
        path: &[String],
        bases: bool,
    ) -> Option<(Target, Confidence)> {
        let (name, of) = path.split_last()?;
        if bases
            || !*self.calling.borrow()
            || of.is_empty()
            || name == "()"
            || name.starts_with('[')
            // (`self.name()` is a member of the type around, or nothing)
            || (of.len() == 1 && matches!(of[0].as_str(), "self" | "super"))
            // (`HttpUtility.UrlEncode(s)`: a static call on a library's type;
            // Rust `usize::from(b)`: on a primitive)
            || (of.len() == 1
                && (of[0].chars().next().is_some_and(|c| c.is_uppercase())
                    || (self.family[f] == Some(Family::Rust)
                        && RUST_PRIMITIVES.contains(&of[0].as_str())))
                && !self.is_local_name(f, d, &of[0]))
        {
            return None;
        }
        let guessed = self.guessed.get();
        let (Target::Def(c), _) = self.extension_method(f, name, None)? else {
            self.guessed.set(guessed);
            return None;
        };
        let written = self.def(c).receiver.clone().or_else(|| self.extended_type(c));
        let outside = written.is_some_and(|w| {
            let plain = w.split('<').next().unwrap_or(&w).trim_end_matches('?').trim();
            let bare = plain.rsplit(['.', ':']).next().unwrap_or(plain);
            !bare.is_empty() && self.type_named(c.0, bare).is_none()
        });
        // a local whose type the source names was asked where the walk
        // stopped, with that type: another library's is not this one
        let named_local = of.len() == 1
            && (self.is_external_local(f, d, &of[0])
                || self.local_written_type(f, d, &of[0]).is_some());
        // a name that is also a member of a type of the project says nothing
        // of an unknown receiver (`self.0.connect()` beside `impl Connector
        // for ()`)
        let fam = self.family[f];
        let only_extensions = self.by_name.get(name.as_str()).into_iter().flatten().all(|r| {
            let x = self.def(*r);
            self.family[r.0] != fam
                || !x.kind.is_callable()
                || x.decl_only
                || x.receiver.is_some()
                || self.extended_type(*r).is_some()
                // (the declaration in the trait such an impl is of:
                // `trait UriExt { fn host_bare(&self); }`)
                || (x.parent.is_some_and(|p| self.def((r.0, p)).kind == DefKind::Interface)
                    && self.by_name.get(name.as_str()).into_iter().flatten().any(|o| {
                        o.0 == r.0 && self.extended_type(*o).is_some()
                    }))
        });
        if !outside
            || named_local
            || !only_extensions
            || self.type_of_path(f, d, of, false).is_some()
        {
            self.guessed.set(guessed);
            return None;
        }
        // (by a name alone: never from the extension to itself, a wrapper
        // calling the library's method of its own name)
        self.guessed.set(true);
        Some((Target::Def(c), Confidence::Inferred))
    }

    /// `want_many`: the path is asked what it is a collection of (`elem_of`):
    /// the answer is the element type, and a path that names one thing is none.
    fn resolve_path_inner(
        &self,
        f: usize,
        d: Option<usize>,
        path: &[String],
        bases: bool,
        want_many: bool,
    ) -> Option<(Target, Confidence)> {
        let fam = self.family[f]?;
        let _asking = Asking(&self.asking, self.asking.replace(Some(f)));
        // `let p = setup(); p.0.go()`: a part of the tuple a local holds is
        // that part of its value
        if let Some(whole) = self.tuple_local_path(f, d, path) {
            return self.resolve_path_inner(f, d, &whole, bases, want_many);
        }
        // `await x`, `x?` (`refs::OPENED`): taken out of the path, and kept as
        // where a wrapper is opened (`opened[i]`: before the `i`th name)
        let stripped: Vec<String>;
        let mut opened = Vec::new();
        let path = if path.iter().any(|s| s == crate::refs::OPENED) {
            let mut clean = Vec::with_capacity(path.len());
            for s in path {
                if s == crate::refs::OPENED {
                    if let Some(last) = opened.last_mut() {
                        *last = true;
                    }
                } else {
                    clean.push(s.clone());
                    opened.push(false);
                }
            }
            // `opened[i]`: right after name `i`, so before name `i + 1`
            stripped = clean;
            &stripped[..]
        } else {
            path
        };
        let opened_after = |i: usize| opened.get(i).copied().unwrap_or(false);
        let (head, rest) = path.split_first()?;
        // a type parameter of a definition around (`interface Box<T> { v: T }`
        // beside a `type T`) is no name of the project; where types and
        // values are names of two kinds, only where a type is written
        // (a member asked of it, or of a value typed by it, is a member of
        // the type that bounds it: `fn run<S: Sink>(s: S) { s.begin() }`)
        if fam != Family::Ecma || self.naming_type() {
            if let Some((x, bound)) =
                self.type_param_around(f, d, head).filter(|_| !self.is_local_name(f, d, head))
            {
                // (a type's parameter bound by the type itself is the type it
                // is used on, which `Binds` and `self_params` know)
                let own = &self.files[f].defs[x];
                let bound = bound.filter(|_| !own.self_params.iter().any(|p| p == head))?;
                let before = self.types_only.replace(true);
                let named = self.resolve_path_inner(
                    f,
                    defs_parent(&self.files, f, x),
                    &bound,
                    false,
                    false,
                );
                *self.types_only.borrow_mut() = before;
                let (t, _) = named?;
                return if rest.is_empty() {
                    Some((t, Confidence::Extracted))
                } else {
                    self.walk(t, rest, Confidence::Extracted)
                };
            }
        }
        // TS: where a type is written, a value of the name (`const Repo = 1`)
        // is no meaning of it; the type around is (types and values are two
        // meanings of a name: the name resolver's `meaning & SymbolFlagsType`)
        if fam == Family::Ecma && *self.types_only.borrow() && self.is_local_name(f, d, head) {
            if let Some(t) = self.resolve_name(f, d, head) {
                return if rest.is_empty() {
                    Some((t, Confidence::Extracted))
                } else {
                    self.walk(t, rest, Confidence::Extracted)
                };
            }
        }
        // a `let` in a block at the top of a file (`for (let x; …) { x }`
        // beside a file-level `var x`): a local of that block, which no
        // definition holds
        if let Some(l) = self.file_block_local(f, head) {
            if rest.is_empty() || l.path.is_empty() {
                return None;
            }
            let t = self.type_of_path(f, d, &l.path, l.declared)?;
            return self.walk(Target::Def(t), rest, Confidence::Extracted);
        }
        // a local declared with a type, called: the call is of what it holds,
        // which is no definition (`cb('hi')` with `cb: (x: string) => number`)
        if rest.is_empty()
            && !bases
            && *self.calling.borrow()
            && !*self.types_only.borrow()
            && self.declared_local(f, d, head)
            && !self.local_keeps_outer(f, d, head, true)
        {
            return None;
        }
        // C++ `log::Writer log(file);`: where a type is written, a name
        // before `::` is a namespace or a type, never the variable of that name
        if fam == Family::C
            && !rest.is_empty()
            && self.naming_type()
            && self.is_local_name(f, d, head)
        {
            let named = self
                .resolve_name(f, d, head)
                .and_then(|t| self.walk(t, rest, Confidence::Extracted));
            if named.is_some() {
                return named;
            }
        }
        // Rust: a module and a function of one name are in two namespaces
        // (`mod log` beside `fn log`): `log::write()` is the module's,
        // `log()` the function (r[names.namespaces.kinds])
        // (C: a struct tag and a function of one name are two name spaces,
        // C 6.2.3; C++ hides the class behind the function, [basic.scope.hiding])
        if matches!(fam, Family::Rust | Family::C) && !self.is_local_name(f, d, head) {
            let called = rest.first().is_none_or(|s| s == "()") && !self.naming_type();
            if let Some(r) = self.rust_namespace_pick(f, d, head, called) {
                return self.walk(Target::Def(r), rest, Confidence::Extracted);
            }
        }
        // C++ `Status::OK()` in a member of Status: the name before `::` is
        // the class, never the constructor of its name the member lookup
        // finds first ([class.qual]: a constructor is not found by a
        // nested-name-specifier)
        if fam == Family::C && rest.first().is_some_and(|s| s != "()") {
            if let Some(Target::Def(c)) = self.resolve_name_any(f, d, head) {
                // (a constructor is a callable of its class's name: the
                // grammar gives it no kind of its own)
                if self.def(c).kind.is_callable() {
                    let class = self.class_of(c.0, Some(c.1));
                    if let Some(class) = class.filter(|k| &self.def(*k).name == head) {
                        return self.walk(Target::Def(class), rest, Confidence::Extracted);
                    }
                }
            }
        }
        // PHP `\\App\\Models\\User`, `\\DateTimeImmutable`: fully qualified, from
        // the global namespace (spec 18-namespaces.md)
        if fam == Family::Php && head == "\\" {
            for k in (1..=rest.len()).rev() {
                let key = rest[..k].join(".");
                let found = self
                    .fqn
                    .get(&key)
                    .into_iter()
                    .flatten()
                    .copied()
                    .filter(|r| self.family[r.0] == Some(Family::Php));
                if let Some(r) = self.best(found) {
                    return self.walk(Target::Def(r), &rest[k..], Confidence::Extracted);
                }
            }
            // (a function or class the project declares in the global
            // namespace: a file with no `namespace`, nothing around it)
            let (name, more) = rest.split_first()?;
            let global: Vec<DefRef> = self
                .by_name
                .get(name)
                .into_iter()
                .flatten()
                .copied()
                .filter(|r| {
                    // (`namespace { … }` is a block of the global namespace)
                    let global_block = |p: usize| {
                        let d = &self.files[r.0].defs[p];
                        d.kind == DefKind::Namespace && d.name.trim().is_empty()
                    };
                    self.family[r.0] == Some(Family::Php)
                        && match self.def(*r).parent {
                            None => self.files[r.0].package.is_none(),
                            Some(p) => global_block(p),
                        }
                })
                .collect();
            // (one the project does not declare is PHP's own or a library's,
            // `\\ReflectionProperty`, `\\rewind`: it names nothing, as the same
            // name written bare does)
            let r = self.best(global)?;
            return self.walk(Target::Def(r), more, Confidence::Extracted);
        }
        // C++ `::Helper()`: a name of the global namespace (no namespace
        // around), in the file or a file it includes ([namespace.qual])
        if fam == Family::C && head == "::" {
            let (name, more) = rest.split_first()?;
            let mut files: Vec<usize> = vec![f];
            files.extend(self.scopes[f].imported.iter().copied().filter(|&g| g != f));
            let found: Vec<DefRef> = files
                .iter()
                .flat_map(|&g| self.top[g].get(name).into_iter().flatten().map(move |&i| (g, i)))
                .filter(|r| self.def(*r).parent.is_none())
                .collect();
            let r = self.best(found)?;
            return self.walk(Target::Def(r), more, Confidence::Extracted);
        }
        // C++ `namespace u = util; u::twice()`: the namespace the alias names
        // (TS `import Internal = My.Internal;`, also named alone: `new C()`)
        if (fam == Family::Ecma || (fam == Family::C && !rest.is_empty()))
            && !self.files[f].ns_aliases.is_empty()
        {
            let named = self.files[f].ns_aliases.iter().find(|(a, _)| a == head);
            if let Some((_, of)) = named.filter(|_| !self.is_local_name(f, d, head)) {
                let mut whole = of.clone();
                whole.extend(rest.iter().cloned());
                return self.resolve_path_inner(f, d, &whole, bases, want_many);
            }
        }
        let mut conf = Confidence::Extracted;
        // Whether `t` is what a sealed wrapper holds, not opened yet: after a
        // name, field or call declared as one (`Optional<Foo>`, `-> Result<Foo,
        // E>`, `Promise<Foo>`); until `await`, `?`, an unwrapper (`unwrap()`,
        // `get()`, `.Result`) or `[]` (the parameter of `opt.map { … }`)
        let mut sealed = false;
        // Whether `t` stands for a collection of itself: after a name
        // declared as one (`xs: List<Foo>`), a call that returns one, a
        // method that keeps one (`filter`, `sorted`) or `[+]`; until an
        // element is taken (`first()`, `[]`).
        let mut many = false;
        // `t` is a map's values (iterating it gives entries or keys), or
        // elements each with an index (`withIndex()`)
        let mut keyed = false;
        let mut indexed = false;
        // the type of the map's keys, when it is known
        let mut keys: Option<DefRef> = None;
        // after `partition`: a pair of collections of the same elements
        let mut partitioned = false;
        // how many collections deep the elements are, beyond the one `many`
        // says (`xs.chunked(2)`: 1)
        let mut nest: u8 = 0;
        // what comes after position `i` is something of collections, or the
        // path ends there and a collection is what is asked for
        let collection_next = |i: usize| match rest.get(i) {
            Some(s) => is_collection_step(s),
            None => want_many,
        };
        // `val builder = builder(x).tag(y)`: inside its own initializer the
        // name is still the outer one
        let own_init = self.own_initializer(f, d, path);
        // a local or parameter declared as a collection
        let head_many =
            (!own_init && collection_next(0)).then(|| self.local_elem(f, d, head)).flatten();
        // JS: what a framework hands a function (express's own `req` / `res`),
        // a document of a Mongoose model
        if fam == Family::Ecma && !rest.is_empty() && !bases && head_many.is_none() {
            if let Some(m) = self.handed_member(f, d, head, &rest[0]) {
                return self.walk(Target::Def(m), &rest[1..], Confidence::Inferred);
            }
        }
        // `self` / `super` where the language has no such word (Go, C, C++,
        // Java's `self`, C#'s `super`): a name like any other when the scope
        // has one. (`this` reaches here as `self` too, so the word is the
        // receiver unless something is named that.)
        let lang = self.files[f].lang;
        let has_word = match head.as_str() {
            "self" => matches!(
                lang,
                Some(Lang::Python | Lang::Rust | Lang::Swift | Lang::Ruby | Lang::Php)
            ),
            "super" => {
                !matches!(lang, Some(Lang::Go | Lang::C | Lang::Cpp | Lang::CSharp | Lang::Php))
            }
            _ => true,
        };
        let plain_word = !has_word
            && (self.is_local_name(f, d, head) || self.resolve_name(f, d, head).is_some());
        let mut t = match head.as_str() {
            _ if head_many.is_some() => {
                let (elem, c) = head_many?;
                conf = c;
                many = true;
                keyed = self.local_keyed(f, d, head);
                keys = keyed.then(|| self.local_key(f, d, head)).flatten();
                Target::Def(elem)
            }
            "self" if !plain_word => {
                if rest.is_empty() {
                    // PHP `new static()` / `new self()`, C# `: this(…)`, Java
                    // `this(…)`: the class around
                    // (and Swift `Self()`)
                    let constructs =
                        matches!(fam, Family::Php | Family::CSharp | Family::Jvm | Family::Swift)
                            && *self.ref_kind.borrow() == Some(RefKind::New);
                    return constructs
                        .then(|| self.class_of(f, d))
                        .flatten()
                        .map(|c| (Target::Def(c), conf));
                }
                // Ruby `self.class.from(x)`: a method of the class itself
                if fam == Family::Ruby && rest.len() > 1 && rest[0] == "class" {
                    let c = self.class_of(f, d)?;
                    let skip = if rest.get(1).is_some_and(|s| s == "()") { 2 } else { 1 };
                    return self.walk(Target::Def(c), &rest[skip..], conf);
                }
                match self.class_of(f, d) {
                    Some(c) => Target::Def(c),
                    // JS `this` inside a function assigned to an object; a
                    // method beside this one in an `impl` for a type the
                    // project does not define (`impl AuthorityExt for Authority`)
                    None => {
                        let found = self
                            .object_member(f, d, &rest[0])
                            .or_else(|| self.owner_sibling(f, d, &rest[0]))
                            .or_else(|| self.constructor_member(f, d, &rest[0]))
                            .map(|m| (m, conf))
                            .or_else(|| self.this_of_member(f, &rest[0]));
                        let (m, conf) = found?;
                        return self.walk(Target::Def(m), &rest[1..], conf);
                    }
                }
            }
            // Rust `super::x`: the parent module
            "super" if fam == Family::Rust => {
                // inside an inline `mod inner { … }` the parent is the module
                // the block is written in: the one around it, else the file's
                let defs = &self.files[f].defs;
                let inline = |from: Option<usize>| {
                    let mut cur = from;
                    while let Some(x) = cur {
                        if defs[x].kind == DefKind::Namespace {
                            return Some(x);
                        }
                        cur = defs[x].parent;
                    }
                    None
                };
                let t = match inline(d) {
                    Some(x) => match inline(defs[x].parent) {
                        Some(outer) => Target::Def((f, outer)),
                        None => Target::Module(vec![f]),
                    },
                    None => modules::rust_parent(self, f)?,
                };
                return self.walk(t, rest, conf);
            }
            "super" if !plain_word => {
                let c = self.class_of(f, d)?;
                // Python `super().m`: the call adds nothing
                let rest = if rest.first().is_some_and(|s| s == "()") { &rest[1..] } else { rest };
                // C# `: base(…)`, Java `super(…)`: the class the constructor's
                // class extends
                if rest.is_empty()
                    && matches!(fam, Family::CSharp | Family::Jvm)
                    && *self.ref_kind.borrow() == Some(RefKind::New)
                {
                    let base = self
                        .bases(c)
                        .into_iter()
                        .find(|b| matches!(self.def(*b).kind, DefKind::Class | DefKind::Struct))?;
                    return Some((Target::Def(base), conf));
                }
                // Ruby `super` / `super(x)`: the method of the same name
                // further up
                if rest.is_empty() && fam == Family::Ruby {
                    let defs = &self.files[f].defs;
                    let mut cur = d;
                    while let Some(x) = cur {
                        if defs[x].kind.is_callable() && !defs[x].block {
                            let m = self
                                .bases(c)
                                .into_iter()
                                .find_map(|b| self.class_member(b, &defs[x].name, 4))?;
                            return Some((Target::Def(m), conf));
                        }
                        cur = defs[x].parent;
                    }
                    return None;
                }
                let name = rest.first()?;
                let m = self.bases(c).into_iter().find_map(|b| self.class_member(b, name, 4))?;
                return self.walk(Target::Def(m), &rest[1..], conf);
            }
            "crate" if fam == Family::Rust => modules::crate_root(self, f)?,
            "()" => {
                // a receiver the walk could not name: a method by name alone
                if rest.len() != 1 || bases {
                    return None;
                }
                return self.unknown_receiver(f, &rest[0], None);
            }
            // PHP `$x`: a variable. Its declared or assigned type, else only
            // a distinctive method name
            v if v.starts_with('$') && fam == Family::Php => match self.local_type(f, d, &v[1..]) {
                Some((t, c)) => {
                    conf = c;
                    Target::Def(t)
                }
                None => {
                    let known = self.is_local_name(f, d, &v[1..]);
                    let external = known && self.is_external_local(f, d, &v[1..]);
                    if !external
                        && !bases
                        && (rest.len() == 1 || (rest.len() == 2 && rest[0] == "()"))
                    {
                        return self
                            .fallback_method(f, rest.last()?)
                            .map(|r| (Target::Def(r), Confidence::Inferred));
                    }
                    return None;
                }
            },
            // a parameter or local of no known type, or of a type the project
            // does not define: only an extension written for it can be meant
            // (where the use is in the local's block, and after its declaration)
            _ if !own_init
                && (self.is_untyped_local(f, d, head) || self.is_external_local(f, d, head))
                && self.is_local_name(f, d, head)
                && !self.local_keeps_outer(
                    f,
                    d,
                    head,
                    rest.is_empty() && *self.calling.borrow(),
                )
                && (*self.types_only.borrow() || self.local_type(f, d, head).is_none()) =>
            {
                if rest.len() == 1 && !bases && *self.calling.borrow() {
                    // (`var message = new HttpRequestMessage()`: the type it
                    // constructs is the one an extension is written for)
                    let on = self
                        .local_written_type(f, d, head)
                        .or_else(|| self.local_built_type(f, d, head));
                    // a type the project does not define has no method of
                    // the project's; a local of no known type is an unknown
                    // receiver: a distinctive method name may say what it is
                    // Where every type of the module is in scope with no
                    // import to say which (Ruby, Swift), only a name no
                    // other type answers to
                    let open_world = matches!(fam, Family::Ruby | Family::Swift);
                    if self.is_external_local(f, d, head)
                        || (open_world && !self.sole_holder(fam, &rest[0]))
                    {
                        return self.extension_method(f, &rest[0], on.as_deref());
                    }
                    // (or the one extension of that name in sight: `{ error
                    // in error.asAFError() }`)
                    return self
                        .unknown_receiver(f, &rest[0], on.as_deref())
                        .or_else(|| self.extension_method(f, &rest[0], on.as_deref()));
                }
                return self.extension_property_hop(f, rest, bases, conf);
            }
            // a local assigned something the walk cannot type (`let pool =
            // self.pool.lock().unwrap()`) still hides every outer name of its
            // own: only a distinctive method name says what is called on it
            _ if !own_init
                && self.is_local_name(f, d, head)
                && !*self.types_only.borrow()
                && !self.local_keeps_outer(
                    f,
                    d,
                    head,
                    rest.is_empty() && *self.calling.borrow(),
                )
                && self.local_type(f, d, head).is_none() =>
            {
                // `const [text, copy] = useCopy(); copy(id)`: the function the
                // part is
                if rest.is_empty() && !bases && *self.calling.borrow() {
                    if let Some(fun) = self.local_function(f, d, head) {
                        return Some((Target::Def(fun), Confidence::Inferred));
                    }
                }
                if !bases && (rest.len() == 1 || (rest.len() == 2 && rest[0] == "()")) {
                    let on = self
                        .local_written_type(f, d, head)
                        .or_else(|| self.local_built_type(f, d, head))
                        .filter(|_| rest.len() == 1);
                    return self.unknown_receiver(f, rest.last()?, on.as_deref());
                }
                return self.extension_property_hop(f, rest, bases, conf);
            }
            _ => match (!(own_init
                || rest.is_empty()
                // the name by itself, where an assignment only hinted at what
                // it holds (`service = make()`), is the variable, not its
                // type; and a bare call beside a local that cannot be what is
                // called (`let agent = agent();`, `baseUrl(…)` beside `String
                // baseUrl`) is a call of the outer name
                && (self.local_is_hint(f, d, head)
                    || (*self.calling.borrow() && self.local_keeps_outer(f, d, head, true)))))
            .then(|| self.local_type(f, d, head))
            .flatten()
            .map(|(t, c)| {
                conf = c;
                Target::Def(t)
            })
            .or_else(|| {
                // inside `x.apply { … }`: the receiver's member, before the
                // names of the class around
                if own_init || *self.types_only.borrow() {
                    return None;
                }
                let (m, c) = self.receiver_member(f, d, head)?;
                conf = conf.max(c);
                // the class around has a callable of the name too: the
                // receiver's is the one meant unless it cannot take the
                // arguments (`buildString { emit(this, flags) }` hands the
                // receiver to the outer `emit`). Types would say; the count
                // of arguments is what the facts have, so this is a guess
                if self.def(m).kind.is_callable() {
                    if let Some(Target::Def(o)) = self.resolve_name(f, d, head) {
                        if o != m && self.def(o).kind.is_callable() {
                            let args = (*self.args.borrow()).filter(|_| rest.is_empty());
                            let owner = self.def(m).parent.map(|p| (m.0, p));
                            let too_few = args.is_some_and(|a| {
                                owner.is_some_and(|c| {
                                    self.members(c, head)
                                        .iter()
                                        .all(|x| self.def(*x).params.is_some_and(|n| n < a))
                                })
                            });
                            // the arguments' types, where the site shows them
                            let cur = (*self.cur.borrow()).filter(|_| rest.is_empty());
                            if let Some((cf, ci)) = cur {
                                let r = &self.files[cf].refs[ci];
                                let best = |x: DefRef| {
                                    self.overloads(x)
                                        .into_iter()
                                        .filter_map(|c| self.fit(f, d, c, r))
                                        .max()
                                };
                                match (best(m), best(o)) {
                                    (None, Some(_)) => return None,
                                    (Some(_), None) => return Some(Target::Def(m)),
                                    (Some(a), Some(b)) if a > b => return Some(Target::Def(m)),
                                    (Some(a), Some(b)) if b > a => return None,
                                    _ => {}
                                }
                            }
                            // `buildString { emit(this) }`: the receiver is
                            // handed to the outer `emit`, not to its own
                            if too_few || (*self.self_arg.borrow() && rest.is_empty()) {
                                return None;
                            }
                            conf = Confidence::Inferred;
                        }
                    }
                }
                Some(Target::Def(m))
            })
            .or_else(|| {
                let found = self.resolve_name(f, d, head)?;
                // a function of the name with a member asked of it, not
                // called: the field of that name is what is meant (Kotlin
                // `constructor(builder: Builder, tags: TagMap =
                // builder.buildTagMap())` beside `fun builder(…)`)
                if let Target::Def(x) = &found {
                    let asked = rest.first().is_some_and(|s| s != "()" && !s.starts_with('['));
                    if asked && self.def(*x).kind.is_callable() && fam.implicit_this() {
                        let held = self.class_of(f, d).and_then(|c| self.field_type(c, head, 3));
                        if let Some((t, cf)) = held {
                            conf = cf;
                            return Some(Target::Def(t));
                        }
                    }
                }
                Some(found)
            })
            .or_else(|| {
                // a field seen without `this` (Java, C#, C++, Swift, Ruby)
                let c = if fam.implicit_this() { self.class_of(f, d)? } else { return None };
                let (t, cf) = self.field_type(c, head, 3)?;
                conf = cf;
                Some(Target::Def(t))
            }) {
                Some(t) => t,
                None => {
                    if let Some(found) = self.qualified(f, d, path) {
                        return Some(found);
                    }
                    // `res.get()` where the file assigns functions to `res`
                    if rest.len() == 1 {
                        let defs = &self.files[f].defs;
                        let cands: Vec<DefRef> = defs
                            .iter()
                            .enumerate()
                            .filter(|(_, x)| {
                                x.name == rest[0] && x.object.as_deref() == Some(head.as_str())
                            })
                            .map(|(i, _)| (f, i))
                            .collect();
                        if let Some(m) = self.best(cands) {
                            return Some((Target::Def(m), Confidence::Extracted));
                        }
                    }
                    if rest.is_empty() {
                        if bases {
                            return None;
                        }
                        return self
                            .fallback_name(f, head)
                            .map(|r| (Target::Def(r), Confidence::Inferred));
                    }
                    let upper = head.chars().next().is_some_and(|c| c.is_uppercase());
                    if upper {
                        conf = Confidence::Inferred;
                        Target::Def(self.type_named(f, head)?)
                    } else if fam == Family::Rust && RUST_PRIMITIVES.contains(&head.as_str()) {
                        // `usize::from(b)`, `str::len(s)`: a primitive's
                        // associated function, the language's (a trait of the
                        // project implemented for it is asked by its path)
                        return None;
                    } else if !bases && (rest.len() == 1 || (rest.len() == 2 && rest[0] == "()")) {
                        return self.unknown_receiver(f, rest.last()?, None);
                    } else {
                        return None;
                    }
                }
            },
        };
        // C++: a template's bare name where several specializations declare
        // it (`AsyncWrapper{…}`, a local `AsyncWrapper w{…}`): which one the
        // arguments make is not read, so `t` is one of them, not picked
        let mut unpicked = false;
        if let Target::Def(c) = t {
            if fam == Family::C && !self.specializations(c).is_empty() {
                let bare = specialized(&self.def(c).name);
                let written = if bare.is_some_and(|b| b == head.as_str()) {
                    true
                } else {
                    // (`Wrapper w{…}`: the local's declared type is the bare name)
                    self.local_path(f, d, head)
                        .and_then(|p| p.last())
                        .is_some_and(|last| bare.is_some_and(|b| b == last))
                };
                if written {
                    unpicked = true;
                    conf = conf.max(Confidence::Inferred);
                }
            }
        }
        // a property named bare (implicit `this`), or a module variable, with
        // more after it: on through its type
        if let Target::Def(p) = t {
            if !many && self.def(p).kind.is_state() && collection_next(0) {
                if let Some(elem) = self.elem_type_of(p) {
                    keyed = self.def(p).keyed;
                    keys = self.def_key(p);
                    t = Target::Def(elem);
                    many = true;
                }
            }
        }
        let head_def = match t {
            Target::Def(p) if self.def(p).kind.is_state() => Some(p),
            _ => None,
        };
        if let Target::Def(p) = t {
            if !many && self.def(p).kind.is_state() && !rest.is_empty() {
                let typed = self
                    .class_of(p.0, self.def(p).parent)
                    .and_then(|owner| self.field_type(owner, &self.def(p).name.clone(), 3))
                    // the type its declaration writes (Go `var rooms
                    // *Manager`, Kotlin `val kind: Kind = builder.kind`)
                    .or_else(|| self.declared_state_type(p))
                    .or_else(|| self.state_type(p));
                match typed {
                    Some((ft, cf)) => {
                        conf = conf.max(cf);
                        t = Target::Def(ft);
                    }
                    // a constant bound to an import (`const users =
                    // require('./users')`): on through the module
                    None => {
                        let top =
                            p.0 == f && self.def(p).parent.is_none() && self.def(p).name == *head;
                        let bound = self.scopes[f].bindings.get(head.as_str()).filter(|_| top);
                        // a module variable holding what the walk cannot type
                        // (`var app = express()`) is a receiver it cannot
                        // name, as a local would be: a distinctive method
                        // name may say what is called. Never one holding a
                        // library's value (`http.createServer()`)
                        if bound.is_none()
                            && self.def(p).parent.is_none()
                            && rest.len() == 1
                            && !bases
                            && *self.calling.borrow()
                            && !*self.types_only.borrow()
                            && self.holds_project_value(p)
                        {
                            return self.unknown_receiver(f, &rest[0], None);
                        }
                        // a field of a type the project does not define
                        // (`WireMockServer _server`): an extension written
                        // for that type
                        if bound.is_none()
                            && rest.len() == 1
                            && !bases
                            && *self.calling.borrow()
                            && !*self.types_only.borrow()
                        {
                            if let Some(found) = self.extension_on_member(f, p, &rest[0]) {
                                return Some(found);
                            }
                        }
                        // `const api = { load() { … } }; api.load()`: a
                        // function written in the object the constant holds
                        if bound.is_none() && fam == Family::Ecma {
                            let name = &self.def(p).name;
                            let own: Vec<DefRef> = self.files[p.0]
                                .defs
                                .iter()
                                .enumerate()
                                .filter(|(_, x)| {
                                    x.name == rest[0] && x.object.as_deref() == Some(name.as_str())
                                })
                                .map(|(i, _)| (p.0, i))
                                .collect();
                            if let Some(m) = self.best(own) {
                                return self.walk(Target::Def(m), &rest[1..], conf);
                            }
                        }
                        let (b, imp) = bound?;
                        let m = self.eval(b, 3)?;
                        self.note_use(f, *imp, &m);
                        t = m;
                    }
                }
            }
        }
        // `Response<Contact> r`: what the type's parameters stand for
        let mut binds: Option<Binds> = None;
        if let (Target::Def(c), false) = (&t, own_init || many || rest.is_empty()) {
            let c = *c;
            binds = match head_def {
                Some(p) => {
                    let def = self.def(p);
                    self.binds_for(c, &def.targs, &def.targ_elems, p.0, def.parent, None)
                }
                None => self
                    .local_targs(f, d, head)
                    .and_then(|(args, elems, x)| self.binds_for(c, args, elems, f, Some(x), None))
                    // `val r = call.execute()`: the bindings its value gives
                    .or_else(|| self.local_value_binds(f, d, head, c)),
            };
        }
        // a local, parameter or field (named bare) declared as, or assigned,
        // a sealed wrapper
        if !many && !own_init && !rest.is_empty() {
            sealed = match head_def {
                Some(p) => self.def(p).sealed,
                None => self.is_local_name(f, d, head) && self.local_sealed(f, d, head),
            };
        }
        if opened_after(0) {
            sealed = false;
        }
        // the type the member being walked through was looked up on
        let mut on: Option<DefRef> = None;
        let mut skip = 0;
        // an element was just taken and may still be in an optional
        let mut one = false;
        // a wrapper was just opened, and what it gave may be another
        // (`lock().unwrap()`): only unwrappers are skipped, it is no optional
        let mut unwrapping = false;
        let csharp = fam == Family::CSharp;
        for (i, seg) in rest.iter().enumerate() {
            // C# `await c` of a project type: what its awaiter gives
            if csharp && opened_after(i) {
                if let Target::Def(c) = t {
                    if let Some(x) = self.csharp_awaited(f, c) {
                        t = Target::Def(x);
                    }
                }
            }
            if skip > 0 {
                skip -= 1;
                if skip == 0 && opened_after(i + 1) {
                    sealed = false;
                }
                continue;
            }
            // Java `Outer.this` (JLS 15.8.4): the object of the class named,
            // and only of a class named: `r.self()` on a `Repo r` is a method
            // called `self`, which is no word of Java's
            if seg == "self"
                && i == 0
                && self.files[f].lang == Some(crate::lang::Lang::Java)
                && matches!(t, Target::Def(c)
                    if self.def(c).kind.is_class_like() && self.def(c).name == *head)
            {
                continue;
            }
            // C# `TryGet(k, out var r)`: `r` has the out parameter's type
            if let Some(arg) = crate::refs::out_param_index(seg) {
                let Target::Def(c) = t else { return None };
                let def = self.def(c);
                if !def.kind.is_callable() {
                    return None;
                }
                let skip = usize::from(def.receiver.is_some());
                let p = def.plist.get(arg + skip).filter(|p| !p.ty.is_empty())?;
                let ty = self
                    .type_of_path(c.0, Some(c.1), &p.ty, true)
                    .filter(|x| self.def(*x).kind.is_class_like())?;
                t = Target::Def(ty);
                binds = None;
                continue;
            }
            // C# `Apply(r => r.Find())`: `r` is the parameter of the delegate
            // the callee's parameter is declared as
            if let Some((arg, j)) = crate::refs::lambda_param_index(seg) {
                let Target::Def(c) = t else { return None };
                t = Target::Def(self.csharp_lambda_param(c, arg, j)?);
                binds = None;
                continue;
            }
            // `let (a, b) = f()`: `a` is `f`'s result's first part
            if let Some(part) = crate::refs::tuple_index(seg) {
                // either half of `partition`: a collection of the same
                if many && std::mem::take(&mut partitioned) && part < 2 {
                    continue;
                }
                let Target::Def(c) = t else { return None };
                let def = self.def(c);
                // C# `var (a, b) = p`: the out parameters of the type's
                // `Deconstruct`
                if def.kind.is_class_like()
                    && def.rets.is_empty()
                    && self.family[c.0] == Some(Family::CSharp)
                {
                    t = Target::Def(self.csharp_deconstructed(f, c, part)?);
                    binds = None;
                    continue;
                }
                // Rust `self.0` on `struct Script(Arc<Mutex<State>>)`
                if def.kind.is_class_like() {
                    let p = def.rets.get(part).filter(|p| !p.is_empty())?;
                    let ft = self
                        .type_of_path(c.0, def.parent, p, true)
                        .filter(|t| self.def(*t).kind.is_class_like())?;
                    sealed =
                        def.part_sealed.get(part).copied().unwrap_or(false) && !opened_after(i + 1);
                    binds = None;
                    t = Target::Def(ft);
                    continue;
                }
                if !def.kind.is_callable() {
                    return None;
                }
                let typed = match def.rets.get(part) {
                    Some(p) if !p.is_empty() => self
                        .type_of_path(c.0, def.parent, p, true)
                        .filter(|t| self.def(*t).kind.is_class_like()),
                    // a function that declares no tuple: its result is the first
                    _ if part == 0 && def.rets.is_empty() => self.return_type(c).map(|(r, _)| r),
                    _ => None,
                };
                // `return [copiedText, copy]`: a part whose type names no class
                // is the definition of that name inside the callable
                let named = || {
                    let name = def.returned_parts.get(part).filter(|n| !n.is_empty())?;
                    let inner = self.children[c.0].get(&c.1)?.get(name)?;
                    inner.first().map(|&i| (c.0, i))
                };
                t = Target::Def(typed.or_else(named)?);
                binds = None;
                continue;
            }
            // `[]`: one of the collection
            // (of an optional, `xs.find { … }?.let { … }`: what it holds)
            if is_element_marker(seg) {
                // `val xs = repo.all(); for (x in xs)`: the value's path ends
                // at the callable, and its result is the collection
                if !many && !one && !sealed {
                    let Target::Def(c) = t else { return None };
                    // C# `foreach (var x in bag)` over a project type: the
                    // `Current` of what its `GetEnumerator` gives, else the
                    // `IEnumerable<T>` it is written with (the standard's
                    // 13.9.5)
                    if self.def(c).kind.is_class_like()
                        && seg == crate::refs::ITERATED
                        && self.family[c.0] == Some(Family::CSharp)
                    {
                        if let Some(x) = self.csharp_iterated(f, c) {
                            t = Target::Def(x);
                            continue;
                        }
                    }
                    // C# `shelf[0]` of a type with an indexer: what the
                    // indexer gives (of several, the first that says)
                    if self.def(c).kind.is_class_like() && seg == crate::refs::ELEMENT {
                        let mut given = None;
                        let mut at = vec![c];
                        for _ in 0..4 {
                            given = at.iter().find_map(|k| {
                                self.members(*k, crate::refs::INDEXER)
                                    .into_iter()
                                    .find_map(|x| self.return_type(x).map(|(r, _)| r))
                            });
                            if given.is_some() {
                                break;
                            }
                            at = at.iter().flat_map(|k| self.bases(*k)).collect();
                        }
                        if let Some(r) = given {
                            t = Target::Def(r);
                            continue;
                        }
                    }
                    if !self.def(c).kind.is_callable() {
                        return None;
                    }
                    let declared = self.elem_type_of(c).or_else(|| {
                        let e = self.def(c).elem.as_ref()?;
                        self.bound_type(binds.as_ref(), e, c, on)
                            .and_then(|(t, nested)| (!nested).then_some(t))
                    });
                    let bound = || {
                        self.return_path(c)
                            .and_then(|p| self.bound_type(binds.as_ref(), p, c, on))
                            .and_then(|(t, nested)| nested.then_some(t))
                    };
                    let elem = declared.or_else(bound)?;
                    keyed = self.def(c).keyed;
                    keys = self.def_key(c);
                    t = Target::Def(elem);
                    many = true;
                }
                // `for ((k, v) in m)`: a map's key; beside an index nothing;
                // the first of what a list gives, its element
                if seg == crate::refs::KEY {
                    if !many || indexed {
                        return None;
                    }
                    if keyed {
                        t = Target::Def(keys?);
                        many = false;
                        keyed = false;
                        nest = 0;
                        continue;
                    }
                }
                // C# `foreach (var (k, v) in m)` over a map: the key and the
                // value of each `KeyValuePair` it gives
                if many
                    && keyed
                    && !indexed
                    && fam == Family::CSharp
                    && seg == crate::refs::ITERATED
                {
                    if let Some(part) = rest.get(i + 1).and_then(|s| crate::refs::tuple_index(s)) {
                        if nest > 0 || part > 1 {
                            return None;
                        }
                        if part == 0 {
                            t = Target::Def(keys?);
                        }
                        many = false;
                        keyed = false;
                        skip = 1;
                        continue;
                    }
                }
                if many {
                    let fits = match seg.as_str() {
                        // an index or a key: the element, a map's value
                        crate::refs::ELEMENT => !indexed,
                        // what iterating gives: an element; a map's entries
                        // or keys, and pairs with an index, are none
                        crate::refs::ITERATED | crate::refs::KEY => !keyed && !indexed,
                        // the second of the pair: a value or an element
                        _ => keyed || indexed,
                    };
                    if !fits {
                        return None;
                    }
                    // one of a collection of collections is a collection
                    if nest > 0 {
                        nest -= 1;
                        keyed = false;
                        indexed = false;
                        continue;
                    }
                } else if seg == crate::refs::VALUE {
                    return None;
                }
                many = false;
                keyed = false;
                indexed = false;
                sealed = false;
                continue;
            }
            if sealed && seg != crate::refs::MANY {
                // a method of the wrapper: what it gives is the wrapper's to
                // say; as the thing called it is the library's
                let called = rest.get(i + 1).is_some_and(|s| s == "()");
                let last = i + 1 + usize::from(called) == rest.len();
                if last && self.linking_call() {
                    return None;
                }
                if UNWRAPPERS.contains(&seg.as_str()) {
                    sealed = false;
                    // (and what it gives may be another: `lock().unwrap()`)
                    unwrapping = true;
                } else if !SEALED_KEEPING.contains(&seg.as_str()) {
                    return None;
                }
                skip = usize::from(called);
                if skip == 0 && opened_after(i + 1) {
                    sealed = false;
                }
                continue;
            }
            // `[+]`: a collection of what the path named so far gives
            // (`[+=]`: a map whose values those are)
            if seg == crate::refs::MANY || seg == crate::refs::MANY_KEYED {
                let Target::Def(x) = t else { return None };
                let def = self.def(x);
                let of = if def.kind.is_class_like() {
                    Some(x)
                } else if def.kind.is_callable() {
                    self.return_type(x).map(|(r, _)| r)
                } else if def.kind.is_state() {
                    self.held_type(x)
                } else {
                    None
                };
                t = Target::Def(of.filter(|_| !many)?);
                many = true;
                keyed = seg == crate::refs::MANY_KEYED;
                keys = None;
                indexed = false;
                nest = 0;
                continue;
            }
            if many {
                // a method of collections: its name says what it gives
                // (called, or read like a property: Swift `xs.first`); as
                // the thing called it is the library's method, nothing here
                if i + 1 == rest.len() && self.linking_call() {
                    return None;
                }
                let s = seg.as_str();
                if keyed && KEY_LOOKUPS.contains(&s) {
                    // `m.get(k)`, `m.getValue(k)`: the value
                    many = false;
                    keyed = false;
                    one = true;
                } else if keyed && MAP_VIEWS.contains(&s) {
                    match s {
                        "values" | "Values" => keyed = false,
                        "entries" | "entrySet" | "items" => {}
                        // the keys: a collection of the key type, when known
                        _ => {
                            t = Target::Def(keys?);
                            keyed = false;
                            nest = 0;
                        }
                    }
                } else if PAIRING_METHODS.contains(&s) && !keyed {
                    indexed = true;
                } else if NESTING_METHODS.contains(&s) && !keyed && !indexed {
                    // `chunked(2)`, `windows(3)`: lists of the elements
                    nest += 1;
                } else if GROUPING_METHODS.contains(&s) && !keyed && !indexed {
                    // `groupBy { … }`: a map whose values are lists of them
                    keyed = true;
                    nest += 1;
                } else if ASSOCIATING_METHODS.contains(&s) && !keyed && !indexed {
                    // `associateBy { it.id }`: a map whose values are them
                    keyed = true;
                } else if FLATTENING_METHODS.contains(&s) && !keyed && !indexed {
                    // `flatten()`: the elements of the lists
                    if nest == 0 {
                        return None;
                    }
                    nest -= 1;
                } else if s == "partition" && !keyed && !indexed {
                    // `val (ok, bad) = xs.partition { … }`: two of them
                    partitioned = true;
                } else if REDUCING_METHODS.contains(&s) && !keyed && !indexed && nest == 0 {
                    // `reduce { a, b -> … }`: one of them
                    many = false;
                    one = true;
                } else if ELEMENT_ACCESSORS.contains(&s) {
                    // (a map's `first()` is an entry, and so is a pair's)
                    if keyed || indexed {
                        return None;
                    }
                    if nest > 0 {
                        nest -= 1;
                    } else {
                        many = false;
                        one = true;
                    }
                } else if !KEEPING_METHODS.contains(&s) {
                    return None;
                }
                skip = usize::from(rest.get(i + 1).is_some_and(|s| s == "()"));
                continue;
            }
            // `xs.first().unwrap()`, `xs.stream().findFirst().get()`: the
            // element out of the optional it came in, unless its type has
            // a member of that name
            let after_element = std::mem::take(&mut one);
            if (after_element || std::mem::take(&mut unwrapping))
                && UNWRAPPERS.contains(&seg.as_str())
            {
                if let Target::Def(c) = t {
                    if self.class_member(c, seg, 4).is_none() {
                        // (as the thing called, it is the library's)
                        if i + 1 == rest.len() && self.linking_call() {
                            return None;
                        }
                        one = after_element;
                        unwrapping = !after_element;
                        skip = usize::from(rest.get(i + 1).is_some_and(|s| s == "()"));
                        continue;
                    }
                }
            }
            // Kotlin `x.apply { … }.build()`, `x.also(f).done()`: the scope
            // functions that give the receiver back
            if matches!(seg.as_str(), "apply" | "also")
                && i + 1 < rest.len()
                && self.files[f].lang == Some(crate::lang::Lang::Kotlin)
            {
                if let Target::Def(c) = t {
                    if self.def(c).kind.is_class_like() && self.class_member(c, seg, 4).is_none() {
                        skip = usize::from(rest.get(i + 1).is_some_and(|s| s == "()"));
                        continue;
                    }
                }
            }
            if seg == "()" {
                // the result of a call: constructing a type gives one of it;
                // anything else returns what the walk cannot know
                if let Target::Def(c) = t {
                    // (Go `Celsius(x)`: a conversion gives a value of the
                    // type, a defined non-struct type as a struct)
                    if self.is_type_like(c) {
                        continue;
                    }
                    // a function that declares it returns a collection, with
                    // something of collections after it
                    // (`flatMap { it.children() }`: each gives a collection,
                    // and the whole is one of their elements)
                    let flat = rest.get(i + 1).is_some_and(|s| s == crate::refs::MANY);
                    if self.def(c).kind.is_callable() && (flat || collection_next(i + 1)) {
                        // (`fun all(): List<T>` on a `Response<Contact>`)
                        let elem = self.elem_type_of(c).or_else(|| {
                            let e = self.def(c).elem.as_ref()?;
                            self.bound_type(binds.as_ref(), e, c, on)
                                .and_then(|(t, nested)| (!nested).then_some(t))
                        });
                        if let Some(elem) = elem {
                            binds = None;
                            keyed = self.def(c).keyed && !flat;
                            keys = self.def_key(c);
                            t = Target::Def(elem);
                            many = true;
                            skip = usize::from(flat);
                            continue;
                        }
                    }
                    // `useToast().show`: a member of the object literal the
                    // function returns, which names a function of its own
                    if let Some(next) = rest.get(i + 1) {
                        let def = self.def(c);
                        let named = def.returned_members.iter().find(|(k, _)| k == next);
                        if let Some((_, local)) = named {
                            let inner = self.children[c.0]
                                .get(&c.1)
                                .and_then(|m| m.get(local))
                                .and_then(|ds| ds.first())
                                .map(|&x| (c.0, x))
                                .filter(|x| self.def(*x).kind.is_callable());
                            if let Some(inner) = inner {
                                t = Target::Def(inner);
                                skip = 1;
                                continue;
                            }
                        }
                    }
                    // several callables of the name (`Result.response()`
                    // beside `response(Response)`): a call in the middle of
                    // a chain writes no arguments the walk keeps, so the one
                    // whose result has what is asked next
                    let c = match self.overload_giving(c, rest.get(i + 1)) {
                        Some(other) => {
                            conf = conf.max(Confidence::Inferred);
                            other
                        }
                        None => c,
                    };
                    // a method that declares its result: `builder().a().b()`
                    if self.def(c).kind.is_callable() {
                        if let Some((r, declared)) = self.return_type(c) {
                            if !declared {
                                conf = conf.max(Confidence::Inferred);
                            }
                            // `fun execute(): Response<T>`: the result's own
                            // bindings, through the ones the call was made under
                            let def = self.def(c);
                            binds = self.binds_for(
                                r,
                                &def.targs,
                                &def.targ_elems,
                                c.0,
                                def.parent,
                                binds.as_ref(),
                            );
                            t = Target::Def(r);
                            sealed = self.def(c).sealed && !opened_after(i + 1);
                            continue;
                        }
                        // `fun body(): T` on a `Response<Contact>`
                        // (a `List<Contributor>` it stands for: a collection)
                        if let Some((r, nested)) = self
                            .return_path(c)
                            .and_then(|p| self.bound_type(binds.as_ref(), p, c, on))
                        {
                            binds = None;
                            many = nested;
                            t = Target::Def(r);
                            continue;
                        }
                        // a result bound to the type itself: the type the
                        // method was called on
                        if let Some(on) = on.filter(|_| self.returns_self_param(c)) {
                            t = Target::Def(on);
                            continue;
                        }
                    }
                }
                if rest.len() == i + 2 && !bases {
                    return self.unknown_receiver(f, &rest[i + 1], None);
                }
                return None;
            }
            // Ruby calls without parentheses: `stubs.match(env)` is what
            // `stubs` returns, then `match`
            if fam == Family::Ruby {
                if let Target::Def(c) = t {
                    if self.def(c).kind.is_callable() {
                        match self.return_type(c) {
                            Some((r, declared)) => {
                                if !declared {
                                    conf = conf.max(Confidence::Inferred);
                                }
                                t = Target::Def(r);
                            }
                            None if rest.len() == i + 1 && !bases => {
                                return self.unknown_receiver(f, seg, None);
                            }
                            None => return None,
                        }
                    }
                }
            }
            if let Target::Def(x) = t {
                if self.def(x).kind.is_class_like() {
                    on = Some(x);
                }
            }
            let mut found = self.member(&t, seg, 3);
            // a field beside a method of its name (Rust `input: ConsumeBuf`
            // and a trait's `fn input()`, Java, PHP): read without a call,
            // with more after it, the name is the field
            if let (Target::Def(c), Some(Target::Def(m))) = (&t, &found) {
                let read = rest.get(i + 1).is_some_and(|s| s != "()");
                if read && fam != Family::Ruby && self.def(*m).kind.is_callable() {
                    let field =
                        self.members(*c, seg).into_iter().find(|x| self.def(*x).kind.is_state());
                    if let Some(field) = field {
                        found = Some(Target::Def(field));
                    }
                }
            }
            // C++ `cpr::async(…)` beside `class async`: the name called is the
            // function, a name with `::` after it the type
            if let Target::Def(ns) = t {
                if fam == Family::C && self.def(ns).kind == DefKind::Namespace {
                    let more = rest.get(i + 1).is_some_and(|s| s != "()" && !s.starts_with('['));
                    let cands = self.namespace_members(ns, seg);
                    let fits: Vec<DefRef> = cands
                        .iter()
                        .copied()
                        .filter(|m| {
                            let k = self.def(*m).kind;
                            if more {
                                k.is_class_like() || k == DefKind::Namespace
                            } else {
                                k.is_callable()
                            }
                        })
                        .collect();
                    if let Some(m) = self.best(fits) {
                        found = Some(Target::Def(m));
                    }
                }
            }
            // C++: the member of a specialization the arguments did not pick
            // (`test_wrapper.Cancel()` on an `AsyncWrapper w{…}`) is the one
            // specialization that answers to it, `INFERRED`; a name several
            // answer to names none of them. On one they did pick, a member it
            // lacks is still the one other specialization's that has it.
            if fam == Family::C && (unpicked || found.is_none()) {
                if let Target::Def(c) = t {
                    let mut all = self.specializations(c);
                    if unpicked {
                        all.push(c);
                    }
                    let mut hits: Vec<DefRef> =
                        all.into_iter().filter_map(|s| self.class_member(s, seg, 3)).collect();
                    hits.sort();
                    hits.dedup();
                    found = match hits.as_slice() {
                        [one] => {
                            conf = conf.max(Confidence::Inferred);
                            Some(Target::Def(*one))
                        }
                        [] if !unpicked => found,
                        _ => None,
                    };
                }
            }
            unpicked = false;
            // Python `@property def cookies(self) -> Cookies`: read without
            // a call, with more after it, it is what it returns
            if let Some(Target::Def(m)) = &found {
                let m = *m;
                let next = rest.get(i + 1);
                // (a JS / TS method read so is a getter: a function has no
                // members of the project's)
                let property = match fam {
                    Family::Python => self.is_property(m),
                    Family::Ecma => next.is_some_and(|s| !FUNCTION_MEMBERS.contains(&s.as_str())),
                    _ => false,
                };
                if property
                    && self.def(m).kind.is_callable()
                    && next.is_some_and(|s| s != "()" && !s.starts_with('['))
                {
                    if let Some((r, declared)) = self.return_type(m) {
                        if !declared {
                            conf = conf.max(Confidence::Inferred);
                        }
                        found = Some(Target::Def(r));
                    }
                }
            }
            // Go `store.Default.Find()`: a variable or constant of another
            // package, with more after it, is what it holds
            if fam == Family::Go && !matches!(t, Target::Def(_)) && i + 1 < rest.len() {
                if let Some(Target::Def(p)) = &found {
                    let p = *p;
                    if self.def(p).kind.is_state() {
                        if let Some((ht, cf)) =
                            self.declared_state_type(p).or_else(|| self.state_type(p))
                        {
                            conf = conf.max(cf);
                            t = Target::Def(ht);
                            continue;
                        }
                    }
                }
            }
            // a property with more after it: go on through its type
            let through_property = matches!(&found, Some(Target::Def(p))
                if matches!(self.def(*p).kind, DefKind::Property | DefKind::Constant))
                && i + 1 < rest.len();
            // a field declared as a collection, with something of
            // collections after it
            if let Some(Target::Def(p)) = &found {
                let flat = rest.get(i + 1).is_some_and(|s| s == crate::refs::MANY);
                if self.def(*p).kind.is_state() && (flat || collection_next(i + 1)) {
                    // (`items: T[]` on a `Page<User>`)
                    let elem = self.elem_type_of(*p).or_else(|| {
                        let e = self.def(*p).elem.as_ref()?;
                        self.bound_type(binds.as_ref(), e, *p, on)
                            .and_then(|(t, nested)| (!nested).then_some(t))
                    });
                    if let Some(elem) = elem {
                        binds = None;
                        keyed = self.def(*p).keyed && !flat;
                        keys = self.def_key(*p);
                        t = Target::Def(elem);
                        many = true;
                        skip = usize::from(flat);
                        continue;
                    }
                }
            }
            if through_property {
                if let Target::Def(c) = t {
                    // (a class attribute with only an annotation, `chief:
                    // Chief`, is typed where it is declared)
                    let declared = || match &found {
                        Some(Target::Def(p)) => {
                            self.held_type(*p).map(|t| (t, Confidence::Extracted))
                        }
                        _ => None,
                    };
                    if let Some((mut ft, cf)) = self.field_type(c, seg, 3).or_else(declared) {
                        conf = conf.max(cf);
                        // a field assigned a union-typed parameter (`stream:
                        // Sync | Async`): a member the first type lacks is of
                        // the one type of the union that has it
                        if let Some(next) =
                            rest.get(i + 1).filter(|s| !s.starts_with('[') && *s != "()")
                        {
                            if self.member(&Target::Def(ft), next, 3).is_none() {
                                let alts: Vec<DefRef> = self
                                    .field_alts(c, seg)
                                    .into_iter()
                                    .filter(|a| self.member(&Target::Def(*a), next, 3).is_some())
                                    .collect();
                                if let [one] = alts.as_slice() {
                                    ft = *one;
                                    conf = Confidence::Inferred;
                                }
                            }
                        }
                        sealed = self.member_sealed(c, seg, found.as_ref()) && !opened_after(i + 1);
                        binds = match &found {
                            Some(Target::Def(p)) => {
                                let def = self.def(*p);
                                self.binds_for(
                                    ft,
                                    &def.targs,
                                    &def.targ_elems,
                                    p.0,
                                    def.parent,
                                    binds.as_ref(),
                                )
                            }
                            _ => None,
                        };
                        t = Target::Def(ft);
                        continue;
                    }
                    // `val body: T` on a `Response<Contact>`
                    if let Some(Target::Def(p)) = &found {
                        let written = self.def(*p).declared_type.as_ref();
                        if let Some((ft, nested)) = written
                            .and_then(|w| self.bound_type(binds.as_ref(), w, *p, on))
                            .or_else(|| self.bound_field(binds.as_ref(), c, seg))
                        {
                            binds = None;
                            many = nested;
                            t = Target::Def(ft);
                            continue;
                        }
                        // C `ac->ev.addRead`: `ev` is of an anonymous struct
                        // type, whose members the field answers for
                        if self.def(*p).inline_body.is_some() {
                            t = Target::Def(*p);
                            continue;
                        }
                        // a property of a type the project does not define
                        // (`request.Name.IsNotEmpty()`, `string Name`): only
                        // an extension written for that type can be meant
                        if i + 2 == rest.len() && !bases && *self.calling.borrow() {
                            return self.extension_on_member(f, *p, &rest[i + 1]);
                        }
                    }
                }
                return None;
            }
            match found {
                Some(next) => t = next,
                None => {
                    // `self.repo.find()`: through the field's type
                    let Target::Def(c) = t else { return None };
                    // Lombok: `getTags()` / `setTags(…)` / `isActive()` of a
                    // class annotated `@Data`, `@Getter`, `@Setter` or `@Value`
                    // is the field it reads or writes
                    if self.def(c).kind.is_class_like() && !bases {
                        if let Some(field) = self.lombok_field(c, seg) {
                            if i + 1 == rest.len() {
                                return Some((Target::Def(field), conf));
                            }
                            if rest[i + 1] == "()" {
                                t = Target::Def(self.held_type(field)?);
                                skip = 1;
                                continue;
                            }
                        }
                    }
                    // the last name is no member of the type: an extension
                    // written for it (`typeSpec.trimmedToString()`)
                    if self.def(c).kind.is_class_like() && i + 1 == rest.len() && !bases {
                        return self.extension_for(f, seg, c);
                    }
                    // … and one called in the middle of a chain, whose result
                    // the walk goes on from (`c.argument().optional()`)
                    if self.def(c).kind.is_class_like()
                        && !bases
                        && i + 2 < rest.len()
                        && rest[i + 1] == "()"
                    {
                        if let Some((Target::Def(x), cf)) = self.extension_for(f, seg, c) {
                            if self.def(x).kind.is_callable() {
                                if let Some((rt, declared)) = self.return_type(x) {
                                    conf = conf.max(cf);
                                    if !declared {
                                        conf = conf.max(Confidence::Inferred);
                                    }
                                    binds = None;
                                    t = Target::Def(rt);
                                    skip = 1;
                                    continue;
                                }
                            }
                        }
                    }
                    if !self.def(c).kind.is_class_like() || i + 1 == rest.len() {
                        return None;
                    }
                    if let Some((ft, nested)) = self.bound_field(binds.as_ref(), c, seg) {
                        binds = None;
                        many = nested;
                        t = Target::Def(ft);
                        continue;
                    }
                    let (ft, cf) = self.field_type(c, seg, 3)?;
                    conf = conf.max(cf);
                    sealed = self.member_sealed(c, seg, None) && !opened_after(i + 1);
                    binds = None;
                    t = Target::Def(ft);
                }
            }
            if opened_after(i + 1) {
                sealed = false;
            }
        }
        if csharp && opened_after(rest.len()) {
            if let Target::Def(c) = t {
                if let Some(x) = self.csharp_awaited(f, c) {
                    t = Target::Def(x);
                }
            }
        }
        // a collection is no definition; one thing is no collection (an
        // optional is a collection of at most one)
        if many != want_many && !(want_many && sealed) {
            return None;
        }
        *self.end_binds.borrow_mut() = binds;
        Some((t, conf))
    }

    /// The bindings a local assigned a call has (`val r = call.execute()` on
    /// a `Call<Contact>`: `Response`'s `T` is `Contact`), when its type is
    /// `ty`.
    fn local_value_binds(
        &self,
        f: usize,
        d: Option<usize>,
        name: &str,
        ty: DefRef,
    ) -> Option<Binds> {
        let defs = &self.files[f].defs;
        let mut cur = d;
        let (x, li) = loop {
            let x = cur?;
            if let Some(&li) = self.locals.get(&(f, x, name.to_string())) {
                break (x, li);
            }
            cur = defs[x].parent;
        };
        let l = &self.files[f].locals[li];
        if l.declared || l.path.is_empty() || l.path.last().is_some_and(|s| s.starts_with('[')) {
            return None;
        }
        if let Some((known, _)) =
            self.value_binds.borrow().get(&(f, li)).filter(|(_, at)| self.trace.valid(*at))
        {
            return known.clone();
        }
        let whole = self.typing.borrow().is_empty() && *self.depth.borrow() == 0;
        if *self.depth.borrow() > 3 || !self.typing.borrow_mut().insert((f, li)) {
            return None;
        }
        let at = self.trace.epoch();
        *self.depth.borrow_mut() += 1;
        let recv = self.recv.replace(None);
        let types = self.types_only.replace(false);
        self.end_binds.replace(None);
        let value = self.resolve_path(f, Some(x), &l.path, true);
        let at_end = self.end_binds.replace(None);
        *self.types_only.borrow_mut() = types;
        *self.recv.borrow_mut() = recv;
        *self.depth.borrow_mut() -= 1;
        self.typing.borrow_mut().remove(&(f, li));
        let out = match value {
            Some((Target::Def(c), _)) if self.def(c).kind.is_callable() => {
                let def = self.def(c);
                self.return_type(c).filter(|(r, _)| *r == ty).and_then(|(r, _)| {
                    self.binds_for(r, &def.targs, &def.targ_elems, c.0, def.parent, at_end.as_ref())
                })
            }
            _ => None,
        };
        if whole {
            self.value_binds.borrow_mut().insert((f, li), (out.clone(), at));
        }
        out
    }

    /// The bindings `args` (written in file `f`, scope `scope`) give `owner`'s
    /// type parameters; an argument that is itself a type parameter `outer`
    /// binds (`fun execute(): Response<T>` in `Call<T>`) takes its binding.
    fn binds_for(
        &self,
        owner: DefRef,
        args: &[Vec<String>],
        elems: &[Option<Vec<String>>],
        f: usize,
        scope: Option<usize>,
        outer: Option<&Binds>,
    ) -> Option<Binds> {
        let def = self.def(owner);
        if args.is_empty() || !def.kind.is_class_like() || def.type_params.len() != args.len() {
            return None;
        }
        let args = args
            .iter()
            .enumerate()
            .map(|(i, a)| {
                let passed = match (a.as_slice(), outer) {
                    ([x], Some(o)) => self.def(o.owner).type_params.iter().position(|p| p == x),
                    _ => None,
                };
                match (passed, outer) {
                    (Some(j), Some(o)) => o.args[j].clone(),
                    _ => BoundArg {
                        path: a.clone(),
                        elem: elems.get(i).cloned().flatten(),
                        file: f,
                        scope,
                    },
                }
            })
            .collect();
        Some(Binds { owner, args })
    }

    /// The bindings of `target`'s type parameters seen from `start` (the
    /// class bindings `b` are for, or the type a member was looked up on)
    /// through the type arguments its bases are written with: `class
    /// UserRepo : Repo<User>` binds `Repo`'s `T` to `User`.
    fn inherited_binds(
        &self,
        start: DefRef,
        b: Option<&Binds>,
        target: DefRef,
        depth: usize,
    ) -> Option<Binds> {
        if depth == 0 {
            return None;
        }
        let def = self.def(start);
        let outer = b.filter(|b| b.owner == start);
        for (base, args) in &def.base_targs {
            let Some(bt) = self
                .type_of_path(start.0, def.parent, base, true)
                .filter(|t| self.def(*t).kind.is_class_like())
            else {
                continue;
            };
            let Some(nb) = self.binds_for(bt, args, &[], start.0, def.parent, outer) else {
                continue;
            };
            if bt == target {
                return Some(nb);
            }
            if let Some(found) = self.inherited_binds(bt, Some(&nb), target, depth - 1) {
                return Some(found);
            }
        }
        None
    }

    /// The type a type parameter `name` of the class around `member` stands
    /// for under `b`: only for a member of the class the bindings are for, and
    /// never for a type parameter of the member itself.
    ///
    /// A member a base declares (`Repo<T>.find()` on a `UserRepo`) takes the
    /// bindings the base is written with. The answer says whether the type
    /// is the element of the collection the parameter stands for
    /// (`Response<List<Contributor>>`'s `body()`).
    fn bound_type(
        &self,
        b: Option<&Binds>,
        name: &[String],
        member: DefRef,
        on: Option<DefRef>,
    ) -> Option<(DefRef, bool)> {
        let [name] = name else { return None };
        let def = self.def(member);
        // (Go `func (s *Stack[U]) Top() U`: a receiver's name for the type's
        // parameter, which the bindings answer by the type's own name)
        let renamed;
        let name = match def.type_params.iter().position(|p| p == name) {
            Some(i) if self.family[member.0] == Some(Family::Go) && def.kind.is_callable() => {
                let owner = self.class_of(member.0, Some(member.1))?;
                renamed = self.def(owner).type_params.get(i)?.clone();
                &renamed
            }
            Some(_) => return None,
            None => name,
        };
        let owner = self.class_of(member.0, Some(member.1))?;
        if let Some(b) = b.filter(|b| b.owner == owner) {
            return self.bound_param(b, name);
        }
        let start = b.map(|b| b.owner).or(on).filter(|s| *s != owner)?;
        let through = self.inherited_binds(start, b, owner, 3)?;
        self.bound_param(&through, name)
    }

    /// The type type parameter `name` of `b.owner` stands for, and whether
    /// it is the element of a collection it stands for.
    fn bound_param(&self, b: &Binds, name: &str) -> Option<(DefRef, bool)> {
        let i = self.def(b.owner).type_params.iter().position(|p| p == name)?;
        let a = &b.args[i];
        let class = |p: &[String]| {
            self.type_of_path(a.file, a.scope, p, true)
                .filter(|t| self.def(*t).kind.is_class_like())
        };
        class(&a.path)
            .map(|t| (t, false))
            .or_else(|| a.elem.as_ref().and_then(|e| class(e)).map(|t| (t, true)))
    }

    /// A field of `c` (assigned in a method, or a constructor's `val x: T`)
    /// whose written type is a type parameter `b` binds.
    fn bound_field(&self, b: Option<&Binds>, c: DefRef, name: &str) -> Option<(DefRef, bool)> {
        let b = b.filter(|b| b.owner == c)?;
        let &(fi, li) = self.fields.get(&c)?.get(name)?;
        let l = &self.files[fi].locals[li];
        match l.path.as_slice() {
            [x] if l.declared => self.bound_param(b, x),
            _ => None,
        }
    }

    /// The type a callable declares it returns, as written.
    fn return_path(&self, c: DefRef) -> Option<&Vec<String>> {
        self.files[c.0]
            .refs
            .iter()
            .find(|x| {
                x.from_def == Some(c.1) && x.kind == RefKind::Type && x.context == "return_type"
            })
            .map(|x| &x.path)
    }

    /// The type arguments a local or parameter is declared with, and the
    /// definition it is a local of.
    fn local_targs(&self, f: usize, d: Option<usize>, name: &str) -> Option<LocalTargs<'_>> {
        let defs = &self.files[f].defs;
        let mut cur = d;
        while let Some(x) = cur {
            if let Some(&li) = self.locals.get(&(f, x, name.to_string())) {
                let l = &self.files[f].locals[li];
                return (!l.targs.is_empty()).then_some((&l.targs[..], &l.targ_elems[..], x));
            }
            cur = defs[x].parent;
        }
        None
    }

    /// `p.0`, `p.Item2`, `p.second` where local `p` holds the tuple a call
    /// returns: the value's path with the part and the rest after it.
    fn tuple_local_path(&self, f: usize, d: Option<usize>, path: &[String]) -> Option<Vec<String>> {
        let (head, rest) = path.split_first()?;
        let part = match rest.first()?.as_str() {
            s if crate::refs::tuple_index(s).is_some() => crate::refs::tuple_index(s)?,
            "first" | "Item1" => 0,
            "second" | "Item2" => 1,
            "third" | "Item3" => 2,
            "Item4" => 3,
            _ => return None,
        };
        let defs = &self.files[f].defs;
        let mut cur = d;
        let (x, li) = loop {
            let x = cur?;
            if let Some(&li) = self.locals.get(&(f, x, head.clone())) {
                break (x, li);
            }
            cur = defs[x].parent;
        };
        let l = &self.files[f].locals[li];
        if l.declared || l.path.is_empty() || l.path.iter().any(|s| s.starts_with("[#")) {
            return None;
        }
        // the value is a call of a function that returns a tuple
        let known = self.tuple_locals.borrow().get(&(f, li)).cloned();
        let parts = match known {
            Some((n, at)) if self.trace.valid(at) => n,
            _ => {
                let whole = self.typing.borrow().is_empty() && *self.depth.borrow() == 0;
                if !self.typing.borrow_mut().insert((f, li)) {
                    return None;
                }
                let at = self.trace.epoch();
                let recv = self.recv.replace(None);
                let value = self.resolve_path(f, Some(x), &l.path, true);
                *self.recv.borrow_mut() = recv;
                self.typing.borrow_mut().remove(&(f, li));
                let n = match value {
                    Some((Target::Def(c), _)) if self.def(c).kind.is_callable() => {
                        self.def(c).rets.len()
                    }
                    _ => 0,
                };
                if whole {
                    self.tuple_locals.borrow_mut().insert((f, li), (n, at));
                }
                n
            }
        };
        if parts <= part {
            return None;
        }
        let mut whole = l.path.clone();
        whole.push(crate::refs::tuple_part(part));
        whole.extend(rest.iter().skip(1).cloned());
        Some(whole)
    }

    /// The per-reference state of the walk, taken out so another reference
    /// can be resolved in the middle of one (`restore` puts it back).
    fn suspend(&self) -> Suspended {
        (
            self.types_only.replace(false),
            self.calling.replace(false),
            self.recv.replace(None),
            self.cur.replace(None),
            self.args.replace(None),
            self.self_arg.replace(false),
        )
    }

    fn restore(&self, s: Suspended) {
        *self.types_only.borrow_mut() = s.0;
        *self.calling.borrow_mut() = s.1;
        *self.recv.borrow_mut() = s.2;
        *self.cur.borrow_mut() = s.3;
        *self.args.borrow_mut() = s.4;
        *self.self_arg.borrow_mut() = s.5;
    }

    /// What call reference `ri` of file `f` calls, with the overload its
    /// arguments are for: a callable, or the one constructor that takes them.
    fn call_target(&self, f: usize, ri: usize) -> Option<(DefRef, Confidence)> {
        if let Some((known, _)) =
            self.call_targets.borrow().get(&(f, ri)).filter(|(_, at)| self.trace.valid(*at))
        {
            return *known;
        }
        // (a call that comes back to itself has no answer)
        self.call_targets.borrow_mut().insert((f, ri), (None, trace::ALWAYS));
        let at = self.trace.epoch();
        let r = &self.files[f].refs[ri];
        let d = r.from_def;
        let saved = self.suspend();
        *self.calling.borrow_mut() = true;
        *self.recv.borrow_mut() = r.recv.map(|i| (f, i));
        *self.cur.borrow_mut() = Some((f, ri));
        *self.args.borrow_mut() = r.args;
        *self.self_arg.borrow_mut() = r.self_arg;
        let found = match &r.implicit {
            Some(place) => self.implicit_member(f, d, r, place),
            None => self.resolve_path(f, d, &r.path, false),
        };
        let out = match found {
            Some((Target::Def(m), conf)) if self.def(m).kind.is_callable() => {
                let picked = self.pick_overload(f, d, m, r);
                let conf = if picked.len() > 1 { Confidence::Inferred } else { conf };
                Some((picked[0], conf))
            }
            // a construction: the one constructor that can take the call
            Some((Target::Def(t), conf)) if self.def(t).kind.is_class_like() => {
                let fitting: Vec<DefRef> = self
                    .constructors(t)
                    .into_iter()
                    .filter(|c| self.fit(f, d, *c, r).is_some())
                    .collect();
                match fitting.as_slice() {
                    [one] => Some((*one, conf)),
                    _ => None,
                }
            }
            _ => None,
        };
        self.restore(saved);
        self.call_targets.borrow_mut().insert((f, ri), (out, at));
        out
    }

    /// A Swift implicit member (`.status(301)`, the path `[IMPLICIT, name]`):
    /// the static member of that name on the type its place asks for — the
    /// parameter it is an argument for, the type its declaration writes, or
    /// the result of the function it is returned from. No type, no edge.
    fn implicit_member(
        &self,
        f: usize,
        d: Option<usize>,
        r: &RefFact,
        place: &crate::facts::Implicit,
    ) -> Option<(Target, Confidence)> {
        use crate::facts::Implicit;
        let name = r.path.get(1)?;
        // the type the place asks for, as a project type, or as the name of a
        // library type it writes
        let (ty, written, conf): (Option<DefRef>, Option<String>, Confidence) = match place {
            Implicit::Arg { call, index, label } => match self.call_target(f, *call as usize) {
                Some((callee, conf)) => {
                    let plist = &self.def(callee).plist;
                    let param = match label {
                        Some(l) => plist.iter().find(|p| p.name == *l)?,
                        None => {
                            let unlabeled = |p: &&crate::facts::ParamFact| p.name == "_";
                            plist.get(usize::from(*index)).filter(unlabeled).or_else(|| {
                                let mut all = plist.iter().filter(unlabeled);
                                match (all.next(), all.next()) {
                                    (Some(one), None) => Some(one),
                                    _ => None,
                                }
                            })?
                        }
                    };
                    (self.param_type(callee, &param.ty), param.ty.last().cloned(), conf)
                }
                None => {
                    // `Endpoint(path: .delay(…))`, a struct that declares no
                    // `init`: its memberwise one takes the stored property
                    // the label names
                    let label = label.as_ref()?;
                    let r2 = &self.files[f].refs[*call as usize];
                    let saved = self.suspend();
                    let t = self.type_of_path(f, r2.from_def, &r2.path, false);
                    self.restore(saved);
                    let t = t.filter(|t| {
                        self.def(*t).kind.is_class_like() && self.constructors(*t).is_empty()
                    })?;
                    let field = self
                        .members(t, label)
                        .into_iter()
                        .find(|m| self.def(*m).kind.is_state())?;
                    (self.held_type(field), self.written_field_type(field), Confidence::Extracted)
                }
            },
            Implicit::Typed(path) => {
                (self.type_of_path(f, d, path, true), path.last().cloned(), Confidence::Extracted)
            }
            Implicit::Value(path) => {
                let saved = self.suspend();
                let t = self.type_of_path(f, d, path, false);
                self.restore(saved);
                (t, None, Confidence::Extracted)
            }
            Implicit::Return => {
                let defs = &self.files[f].defs;
                let mut cur = d;
                loop {
                    let x = cur?;
                    // `static let x: HTTPHeader = { … return .acceptEncoding(…) }()`:
                    // the closure that initializes a property returns its type
                    if defs[x].kind.is_state() {
                        let p = (f, x);
                        break (
                            self.held_type(p),
                            self.written_field_type(p),
                            Confidence::Extracted,
                        );
                    }
                    if defs[x].kind.is_callable() {
                        let (t, declared) = self.return_type((f, x))?;
                        if !declared || defs[x].sealed {
                            return None;
                        }
                        break (Some(t), None, Confidence::Extracted);
                    }
                    cur = defs[x].parent;
                }
            }
        };
        match ty.filter(|t| self.def(*t).kind.is_class_like()) {
            Some(ty) => self.class_member(ty, name, 4).map(|m| (Target::Def(m), conf)),
            // a library type: a static member an extension of it adds
            // (`.streamCompletionQueue(forRequestID:)` for a `DispatchQueue`)
            None => {
                let written = written?;
                let bare = written.split('<').next()?.trim_end_matches('?').to_string();
                let mut cands = self.by_name.get(name.as_str())?.iter().copied().filter(|c| {
                    self.family[c.0] == Some(Family::Swift)
                        && self.extended_type(*c).is_some_and(|t| t == bare)
                });
                match (cands.next(), cands.next()) {
                    (Some(one), None) => Some((Target::Def(one), conf)),
                    _ => None,
                }
            }
        }
    }

    /// Why a call has no edge, when its name is the one callable of that name
    /// in the project (so an edge was plausible): the row
    /// `benchmarks/unresolved.py` counts. `None` for a name the project does
    /// not define, or defines more than once.
    fn explain_unresolved(&self, f: usize, d: Option<usize>, r: &RefFact) -> Option<Value> {
        let name = r.path.last().filter(|s| !s.starts_with('[') && *s != "()")?;
        let fam = self.family[f]?;
        // (an RSpec `let` is a bare name of its block: no member of anything)
        let member_call = r.path.len() >= 2;
        let mut cands = self.by_name.get(name.as_str())?.iter().copied().filter(|c| {
            self.def(*c).kind.is_callable()
                && self.family[c.0] == Some(fam)
                && self.node_id(*c).is_some()
                && !(member_call && self.def(*c).scope_lines.is_some())
        });
        let (c, None) = (cands.next()?, cands.next()) else { return None };
        let marker = r.path.iter().any(|s| s.starts_with('['));
        let head = r.path[0].as_str();
        let bare = head.split('@').next().unwrap_or(head);
        // (a PHP `$x` is a local under its bare name)
        let lhead = head.trim_start_matches('$');
        let lbare = bare.trim_start_matches('$');
        let reason = if r.path.len() == 1 {
            "bare name: not in scope".to_string()
        } else if head == "self" {
            "self: no such member on the type around".to_string()
        } else if head == "super" {
            "super: no such member on a base".to_string()
        } else if head == crate::refs::IMPLICIT {
            "implicit member: its place gives no type".to_string()
        } else if head == "()" {
            "receiver the walk cannot name".to_string()
        } else if self.is_local_name(f, d, lhead) || self.is_local_name(f, d, lbare) {
            if self.is_untyped_local(f, d, lhead) {
                "untyped local or parameter".to_string()
            } else if self.is_external_local(f, d, lhead) {
                "local of a library type".to_string()
            } else {
                match self.local_type(f, d, lhead) {
                    Some((t, _)) if r.path.len() == 2 => {
                        format!("typed local: no such member on its type ({:?})", self.def(t).kind)
                    }
                    Some(_) => "typed local: a step of the path names nothing".to_string(),
                    None => "local the walk cannot type".to_string(),
                }
            }
        } else if head.contains('@') {
            "closure parameter outside any definition".to_string()
        } else {
            match self.resolve_path(f, d, &r.path[..1], true) {
                Some((Target::Def(t), _)) if self.def(t).kind.is_class_like() => {
                    "type: no such member".to_string()
                }
                Some((Target::Def(t), _)) if self.def(t).kind.is_state() => {
                    "field or variable: its type names nothing".to_string()
                }
                Some((Target::Def(_), _)) => "function: its result names nothing".to_string(),
                Some((Target::Ext(..), _)) => "library name".to_string(),
                Some(_) => "module or namespace: no such name".to_string(),
                None => "head name not found".to_string(),
            }
        };
        let visible = c.0 == f
            || self.scopes[f].imported.contains(&c.0)
            || dir_of(&self.files[c.0].path) == dir_of(&self.files[f].path);
        let cd = self.def(c);
        // a row that is most likely a library's name the project happens to
        // have one definition of, and why: not what the engine is missing
        let core = match fam {
            Family::Ecma => ECMA_CORE_METHODS,
            Family::CSharp => CSHARP_CORE_METHODS,
            Family::Ruby => RUBY_CORE_METHODS,
            Family::Swift => SWIFT_CORE_METHODS,
            Family::Rust => RUST_CORE_METHODS,
            Family::C => CPP_CORE_METHODS,
            _ => &[],
        };
        let lower = name.to_lowercase();
        // a capital head that names nothing of the project and no local:
        // a library's type or global (`Date.now()`, `Assert.NotNull(x)`)
        let bare_head = head.split('@').next().unwrap_or(head);
        let outside_head = r.path.len() >= 2
            && reason == "head name not found"
            && bare_head.chars().next().is_some_and(|c| c.is_uppercase())
            && !self.by_name.contains_key(bare_head)
            && !self.is_local_name(f, d, bare_head);
        // C, C++: `std::…` is the library's, even where the project opens
        // `namespace std { }` to add a specialization
        let std_head = fam == Family::C && bare_head == "std" && r.path.len() >= 2;
        // a field or variable declared with a `std::` type
        let std_field = || {
            let held = self.resolve_path(f, d, &r.path[..1], true);
            matches!(held, Some((Target::Def(t), _)) if self.def(t).written_head.as_deref() == Some("std"))
        };
        let std_held = fam == Family::C
            && (self.local_written_head(f, d, bare_head) == Some("std")
                || (reason == "field or variable: its type names nothing" && std_field()));
        let test = |i: usize, def: &Def| {
            def.is_test || crate::community::is_test_path(&self.files[i].path)
        };
        let self_field_assigned = self.files[f].refs.iter().any(|x| {
            x.kind == RefKind::Write
                && x.path.len() == 2
                && x.path[0] == "self"
                && x.path[1] == *name
        });
        let holds_library_value = || match self.resolve_path(f, d, &r.path[..1], true) {
            Some((Target::Def(v), _)) if self.def(v).kind.is_state() => {
                let x = self.def(v);
                x.value_path.as_ref().and_then(|p| p.first()).is_some_and(|h| {
                    matches!(self.resolve_name(v.0, x.parent, h), Some(Target::Ext(..)))
                })
            }
            _ => false,
        };
        // the longest start of the path that names something: a library's
        // name, or a field, variable or function whose written type is one
        // (`$this->request->getBody()`, `RequestInterface $request`). What
        // hangs off it is the library's, unless the one definition is an
        // extension written for such a type.
        // (a C# method with no type around is one a `#if` inside an
        // expression cost its class: it may be an extension all the same)
        let extension = cd.receiver.is_some()
            || self.extended_type(c).is_some()
            || (fam == Family::CSharp && cd.parent.is_none());
        // a name of the project between the head and the name called: the
        // path may come back into the project (`trust.af.evaluate()`)
        let comes_back = r.path.len() > 2
            && r.path[1..r.path.len() - 1].iter().any(|s| self.by_name.contains_key(s.as_str()));
        let library_step = || {
            if r.path.len() < 2 || extension {
                return false;
            }
            if self.is_outside_local(f, d, lhead) && self.local_type(f, d, lhead).is_none() {
                return !comes_back;
            }
            for k in (1..r.path.len()).rev() {
                let last = r.path[k - 1].as_str();
                if last == "()" || last.starts_with('[') {
                    continue;
                }
                match self.resolve_path(f, d, &r.path[..k], true) {
                    Some((Target::Ext(..), _)) => return true,
                    Some((Target::Def(x), _)) => return self.library_typed(x),
                    Some(_) => return false,
                    None => {}
                }
            }
            false
        };
        // where a method is not in scope by its bare name, a bare call is a
        // function's (PHP `\rewind($h)` beside a `rewind()` method, a Go
        // `h := func…` beside a method `h`)
        // (PHP `\\rewind(…)`, fully qualified, is as bare: the global function)
        let bare = r.path.len() == 1 || (r.path.len() == 2 && r.path[0] == "\\");
        let bare_is_no_method = bare
            && !fam.implicit_this()
            && cd.parent.is_some_and(|p| self.def((c.0, p)).kind.is_class_like());
        // a `let` of another block of this file: the name here is a local of
        // the block it is used in
        let let_elsewhere = r.path.len() == 1 && c.0 == f && cd.scope_lines.is_some();
        let library_like = if reason == "library name"
            || reason == "local of a library type"
            || std_held
            || library_step()
        {
            Some("the receiver is a library's")
        } else if r.path.len() >= 2 && self.attrs.contains(&(fam, name.to_string())) {
            // (`env.status` beside `Env = Options.new(:status, …)`, `conn.adapter`
            // beside `def_delegators :builder, :adapter`: the receiver may be
            // the type that declares it with no `def`, typed or not — a
            // typed one that lacks the `def` has the attribute, or nothing)
            Some("a type declares an attribute of the name")
        } else if bare_is_no_method {
            Some("a bare name is no method here")
        } else if let_elsewhere {
            Some("the one definition is a let of another block")
        } else if outside_head || std_head {
            Some("the head is a library's type or global")
        } else if core.contains(&name.as_str())
            || COMMON_METHODS.iter().any(|m| m.to_lowercase() == lower)
            || self.builtin(f, name)
        {
            Some("a name the language's own types answer to")
        } else if c.0 != f && test(c.0, cd) && !crate::community::is_test_path(&self.files[f].path)
        {
            Some("the one definition is test code, the call is not")
        } else if matches!(head, "self" | "super")
            && r.path.len() == 2
            && self.class_of(f, d).is_some_and(|t| self.has_outside_base(t))
        {
            // (`$this->fail()` in a PHPUnit test, `self.assertEqual()`)
            Some("the type around has a base the project does not define")
        } else if r.path.len() >= 2
            && UNTYPED_RECEIVER_REASONS.contains(&reason.as_str())
            && cd.parent.is_some()
            // (a member extension function is the project's own)
            && cd.receiver.is_none()
            && self.class_of(c.0, Some(c.1)).is_some_and(|t| self.implements_library_type(t))
        {
            // (`$request->getUri()` on an untyped parameter: the project's
            // one `getUri` is a `RequestInterface` implementation, and the
            // receiver may be any other)
            Some("the one definition implements a library type's member")
        } else if r.path.len() == 1
            && self.is_local_name(f, d, head)
            // (not the function the local is itself: `const updateTheme =
            // useCallback(…)` declared in the caller is the candidate)
            && !(c.0 == f && cd.parent.is_some() && cd.parent == d)
        {
            // (`done(err)`: what the caller was handed, a callback)
            Some("the name is a local or parameter of the caller")
        } else if vendored_path(&self.files[c.0].path) {
            Some("the one definition is in vendored or minified code")
        } else if fam == Family::Ecma && head == "self" && r.path.len() == 2 && self_field_assigned
        {
            // (`this.engine(…)` after `this.engine = opts.engines[ext]`)
            Some("the member is a field assigned a value, not a method")
        } else if fam == Family::Ecma
            && head == "self"
            && r.path.len() >= 3
            && self.files.iter().any(|g| g.runtime_props.contains(&r.path[1]))
        {
            // (`this.router.route()`, `router` from `Object.defineProperty`)
            Some("the receiver is a property defined at run time")
        } else if fam == Family::Ecma && holds_library_value() {
            // (`const app = express(); app.listen(…)`)
            Some("the receiver holds a library's value")
        } else if fam == Family::Ecma && self.attached_to_request(f, d, &r.path) {
            Some("the receiver is a property middleware attaches to a request")
        } else if fam == Family::Ecma
            && matches!(bare_head, "req" | "request" | "res" | "response")
            && (head.contains('@') || self.is_untyped_local(f, d, head))
            && !self.builds_framework_object()
        {
            // (`req.logout()`: passport's, on the framework's request)
            Some("the receiver is what a framework hands the function")
        } else {
            None
        };
        Some(json!({
            "file": self.files[f].path,
            "line": r.line,
            "language": self.files[f].lang.map(|l| l.name()),
            "path": r.path.join("."),
            "name": name,
            "in_definition": d.is_some_and(|x| !self.files[f].defs[x].block),
            "markers": marker,
            "reason": reason,
            "candidate": {
                "file": self.files[c.0].path,
                "qualname": cd.qualname,
                "extension": cd.receiver.is_some() || self.extended_type(c).is_some(),
                "member": cd.parent.is_some(),
            },
            "candidate_visible": visible,
            "library_like": library_like,
        }))
    }

    /// Whether the path being resolved is written where a type is: a
    /// declaration's type, a construction, a base.
    fn naming_type(&self) -> bool {
        *self.types_only.borrow()
            || matches!(
                *self.ref_kind.borrow(),
                Some(
                    RefKind::Type
                        | RefKind::New
                        | RefKind::Inherits
                        | RefKind::Implements
                        | RefKind::Base
                )
            )
    }

    /// Whether the path being walked is a call to link (not a local's value
    /// being typed).
    fn linking_call(&self) -> bool {
        *self.calling.borrow() && !*self.typing_value.borrow()
    }

    /// Whether member `name` of type `c` (found as `found`, or a field
    /// assigned in a method) holds its value in a sealed wrapper.
    fn member_sealed(&self, c: DefRef, name: &str, found: Option<&Target>) -> bool {
        if let Some(Target::Def(p)) = found {
            return self.def(*p).sealed;
        }
        match self.fields.get(&c).and_then(|m| m.get(name)) {
            Some(&(fi, li)) => self.files[fi].locals[li].sealed,
            None => self.bases(c).into_iter().any(|b| {
                self.fields
                    .get(&b)
                    .and_then(|m| m.get(name))
                    .is_some_and(|&(fi, li)| self.files[fi].locals[li].sealed)
            }),
        }
    }

    /// Whether a local or parameter holds a sealed wrapper: declared as one,
    /// or assigned a call of a function that returns one and not opened
    /// there (`let r = load();`, not `let r = load()?;`).
    fn local_sealed(&self, f: usize, d: Option<usize>, name: &str) -> bool {
        let defs = &self.files[f].defs;
        let mut cur = d;
        while let Some(x) = cur {
            if let Some(&li) = self.locals.get(&(f, x, name.to_string())) {
                let l = &self.files[f].locals[li];
                if l.declared || l.sealed {
                    return l.sealed;
                }
                if l.path.is_empty() || l.path.last().is_some_and(|s| s == crate::refs::OPENED) {
                    return false;
                }
                if let Some((known, _)) = self
                    .sealed_locals
                    .borrow()
                    .get(&(f, li))
                    .filter(|(_, at)| self.trace.valid(*at))
                {
                    return *known;
                }
                // (worked out from the top it is the local's own answer and
                // kept, as `local_type` keeps a type)
                let whole = self.typing.borrow().is_empty() && *self.depth.borrow() == 0;
                if *self.depth.borrow() > 3 || !self.typing.borrow_mut().insert((f, li)) {
                    return false;
                }
                let at = self.trace.epoch();
                *self.depth.borrow_mut() += 1;
                let recv = self.recv.replace(None);
                let types = self.types_only.replace(false);
                let value = self.resolve_path(f, Some(x), &l.path, true);
                *self.types_only.borrow_mut() = types;
                *self.recv.borrow_mut() = recv;
                *self.depth.borrow_mut() -= 1;
                self.typing.borrow_mut().remove(&(f, li));
                let sealed = matches!(value, Some((Target::Def(c), _))
                    if self.def(c).kind.is_callable() && self.def(c).sealed);
                if whole {
                    self.sealed_locals.borrow_mut().insert((f, li), (sealed, at));
                }
                return sealed;
            }
            cur = defs[x].parent;
        }
        false
    }

    /// The type of the keys of the map definition `p` is declared as or
    /// returns.
    fn def_key(&self, p: DefRef) -> Option<DefRef> {
        let def = self.def(p);
        self.type_of_path(p.0, def.parent, def.key.as_ref()?, true)
            .filter(|t| self.def(*t).kind.is_class_like())
    }

    /// The type of the keys of the map a local or parameter is declared as.
    fn local_key(&self, f: usize, d: Option<usize>, name: &str) -> Option<DefRef> {
        let defs = &self.files[f].defs;
        let mut cur = d;
        while let Some(x) = cur {
            if let Some(&li) = self.locals.get(&(f, x, name.to_string())) {
                let l = &self.files[f].locals[li];
                let key = l.key.clone().or_else(|| {
                    defs[x].plist.iter().find(|p| p.name == *name).and_then(|p| p.key.clone())
                })?;
                return self
                    .type_of_path(f, Some(x), &key, true)
                    .filter(|t| self.def(*t).kind.is_class_like());
            }
            cur = defs[x].parent;
        }
        None
    }

    /// The function a local holds when it is a part of a callable's tuple
    /// that names one (`const [text, copy] = useCopy()`).
    fn local_function(&self, f: usize, d: Option<usize>, name: &str) -> Option<DefRef> {
        let defs = &self.files[f].defs;
        let mut cur = d;
        let (x, li) = loop {
            let x = cur?;
            if let Some(&li) = self.locals.get(&(f, x, name.to_string())) {
                break (x, li);
            }
            cur = defs[x].parent;
        };
        let l = &self.files[f].locals[li];
        // (`const [text, copy] = useCopy()`, `const { show } = useToast()`)
        let part = l.path.last().is_some_and(|s| crate::refs::tuple_index(s).is_some());
        let member = l.path.len() >= 3 && l.path[l.path.len() - 2] == "()";
        if l.declared || !(part || member) {
            return None;
        }
        if !self.typing.borrow_mut().insert((f, li)) {
            return None;
        }
        let recv = self.recv.replace(None);
        let calling = self.calling.replace(false);
        let found = self.resolve_path(f, Some(x), &l.path, true);
        *self.calling.borrow_mut() = calling;
        *self.recv.borrow_mut() = recv;
        self.typing.borrow_mut().remove(&(f, li));
        match found {
            Some((Target::Def(fun), _)) if self.def(fun).kind.is_callable() => Some(fun),
            _ => None,
        }
    }

    /// Whether a local or parameter is declared as a map.
    fn local_keyed(&self, f: usize, d: Option<usize>, name: &str) -> bool {
        let defs = &self.files[f].defs;
        let mut cur = d;
        while let Some(x) = cur {
            if let Some(&li) = self.locals.get(&(f, x, name.to_string())) {
                return self.files[f].locals[li].keyed
                    || defs[x].plist.iter().any(|p| p.name == *name && p.keyed);
            }
            cur = defs[x].parent;
        }
        false
    }

    /// The element type of a local or parameter declared as a collection
    /// (or another name for one).
    fn local_elem(&self, f: usize, d: Option<usize>, name: &str) -> Option<(DefRef, Confidence)> {
        if !self.is_local_name(f, d, name) {
            return None;
        }
        self.elem_of(f, d, std::slice::from_ref(&name.to_string()))
    }

    fn walk(&self, t: Target, rest: &[String], conf: Confidence) -> Option<(Target, Confidence)> {
        Some((self.walk_target(t, rest)?, conf))
    }

    fn walk_target(&self, mut t: Target, rest: &[String]) -> Option<Target> {
        for seg in rest {
            t = self.member(&t, seg, 3)?;
        }
        Some(t)
    }

    /// A fully qualified path written out: `a.b.C.m` (JVM, C#), `ns::f` (C++),
    /// `Shop::Cart` (Ruby), `B\\Foo` relative to the current PHP namespace.
    fn qualified(
        &self,
        f: usize,
        d: Option<usize>,
        path: &[String],
    ) -> Option<(Target, Confidence)> {
        let fam = self.family[f]?;
        let joined = |p: &[String]| p.join(".");
        let mut prefixes: Vec<String> = Vec::new();
        if let Some(pkg) = &self.files[f].package {
            let pkg = pkg.replace('\\', ".");
            prefixes.push(format!("{pkg}."));
            // C#: a name is looked up in every namespace around the file's
            // (`Users.UserHelpers` from `App.Features.Articles` is
            // `App.Features.Users.UserHelpers`)
            if fam == Family::CSharp {
                let mut parts: Vec<&str> = pkg.split('.').collect();
                while parts.len() > 1 {
                    parts.pop();
                    prefixes.push(format!("{}.", parts.join(".")));
                }
            }
        }
        // the namespaces / modules lexically around the use (C#, C++, Ruby)
        let defs = &self.files[f].defs;
        let mut cur = d;
        while let Some(x) = cur {
            if matches!(defs[x].kind, DefKind::Namespace | DefKind::Module)
                || (fam == Family::Ruby && defs[x].kind.is_class_like())
            {
                prefixes.push(format!("{}.", defs[x].qualname.replace("::", ".")));
            }
            cur = defs[x].parent;
        }
        prefixes.push(String::new());
        for k in (1..=path.len()).rev() {
            let head = joined(&path[..k]);
            for pre in &prefixes {
                let key = format!("{pre}{head}");
                // (where names live in packages, `fqn` holds them all: a
                // name of another package is not one written without it.
                // Not C#, whose project files import namespaces for every
                // source: `<Using Include="…"/>`)
                let packaged = matches!(fam, Family::Jvm | Family::Php);
                let found = self
                    .fqn
                    .get(&key)
                    .or_else(|| self.by_qual.get(&key).filter(|_| !packaged))
                    .into_iter()
                    .flatten()
                    .copied()
                    .filter(|r| self.family[r.0] == Some(fam))
                    // (Rust: a crate's names are its own)
                    // (… or what the file imports by that name from a crate
                    // the engine cannot follow: `use grep::printer::ColorSpecs`
                    // through a facade crate)
                    .filter(|r| {
                        fam != Family::Rust
                            || self.crate_of(r.0) == self.crate_of(f)
                            || self.imports_name(f, &path[0])
                    })
                    // (Rust: a qualname holds no module path, so a bare one
                    // matches an item of every module of the crate; an item of
                    // another module is in scope only through a `use`, the
                    // file's own or a glob's: `Result` in `lib.rs` is the
                    // prelude's, not `http::Result`)
                    // (Python too: a module's names are its own, its imports'
                    // and the builtins, Reference 4.2.2; a qualname holds no
                    // module path either)
                    .filter(|r| {
                        !matches!(fam, Family::Rust | Family::Python)
                            || !pre.is_empty()
                            || r.0 == f
                            || self.scopes[f].imported.contains(&r.0)
                            || self.imports_name(f, &path[0])
                    })
                    // PHP: a global class is no name of a namespaced file
                    // written bare (only functions and constants fall back)
                    .filter(|r| {
                        !(fam == Family::Php
                            && k == 1
                            && pre.is_empty()
                            && self.files[f].package.is_some()
                            && self.files[r.0].package.is_none()
                            && self.def(*r).kind.is_class_like())
                    })
                    // Ruby: a method at another file's top level is there
                    // only for the files that require it
                    .filter(|r| {
                        fam != Family::Ruby
                            || !pre.is_empty()
                            || r.0 == f
                            || !self.def(*r).kind.is_callable()
                            || self.scopes[f].imported.contains(&r.0)
                    });
                // (C, C++: of the definitions of one qualified name, the
                // file's own, then those of the files it includes)
                let mut found: Vec<DefRef> = found.collect();
                if fam == Family::C && found.len() > 1 {
                    let own: Vec<DefRef> = found.iter().copied().filter(|r| r.0 == f).collect();
                    let seen: Vec<DefRef> = found
                        .iter()
                        .copied()
                        .filter(|r| self.scopes[f].imported.contains(&r.0))
                        .collect();
                    if !own.is_empty() {
                        found = own;
                    } else if !seen.is_empty() {
                        found = seen;
                    }
                }
                if let Some(r) = self.best(found) {
                    let t = self.walk_target(Target::Def(r), &path[k..])?;
                    return Some((t, Confidence::Extracted));
                }
                if k < path.len() && self.namespaces.contains(&(fam, key.clone())) {
                    let t = self.walk_target(Target::Ns(key), &path[k..])?;
                    return Some((t, Confidence::Extracted));
                }
            }
        }
        None
    }

    /// One candidate out of several: those in files `f` imports, then those
    /// in its directory, then the one sharing the longest path prefix.
    fn narrow(&self, f: usize, cands: Vec<DefRef>) -> Option<(DefRef, bool)> {
        let mut c = cands;
        c.sort();
        c.dedup();
        match c.len() {
            0 => return None,
            1 => return Some((c[0], false)),
            _ => {}
        }
        // a prototype and its definition are one candidate
        let defs: Vec<DefRef> = c.iter().copied().filter(|r| !self.def(*r).decl_only).collect();
        if defs.len() == 1 {
            return Some((defs[0], false));
        }
        let via: Vec<DefRef> = match self.scopes.get(f) {
            Some(s) => c.iter().copied().filter(|r| s.imported.contains(&r.0)).collect(),
            None => Vec::new(),
        };
        if via.len() == 1 {
            return Some((via[0], true));
        }
        let here = dir_of(&self.files[f].path);
        let same: Vec<DefRef> =
            c.iter().copied().filter(|r| dir_of(&self.files[r.0].path) == here).collect();
        if same.len() == 1 {
            return Some((same[0], false));
        }
        let common = |r: &DefRef| {
            let a = self.files[f].path.split('/').collect::<Vec<_>>();
            let b = self.files[r.0].path.split('/').collect::<Vec<_>>();
            a.iter().zip(&b).take_while(|(x, y)| x == y).count()
        };
        let best = c.iter().map(common).max()?;
        let top: Vec<DefRef> = c.iter().copied().filter(|r| common(r) == best).collect();
        (top.len() == 1).then(|| (top[0], false))
    }

    fn builtin(&self, f: usize, name: &str) -> bool {
        self.files[f].lang.is_some_and(|l| l.module().builtins().contains(&name))
    }

    /// Whether an import of file `f` binds `name`, resolved or not.
    fn imports_name(&self, f: usize, name: &str) -> bool {
        self.scopes[f].bindings.contains_key(name)
            || self.files[f].imports.iter().any(|imp| {
                imp.alias.as_deref() == Some(name)
                    || imp.names.iter().any(|(n, a)| a.as_deref().unwrap_or(n) == name)
                    || imp.module.rsplit("::").next() == Some(name)
            })
    }

    /// The directory of the Cargo package file `f` belongs to: the deepest
    /// one above it that a manifest names (empty: none, or the root's).
    fn crate_of(&self, f: usize) -> &str {
        let path = self.files[f].path.as_str();
        self.modsys
            .rs_crates_in
            .values()
            .map(|src| src.strip_suffix("src").unwrap_or(src).trim_end_matches('/'))
            .filter(|dir| dir.is_empty() || path.starts_with(&format!("{dir}/")))
            .max_by_key(|dir| dir.len())
            .unwrap_or("")
    }

    /// Whether a guess by name alone may name `r` from file `f`: where names
    /// live in packages (JVM, PHP), only one of the file's own package or of
    /// none, and no type nested in a type of another file.
    fn in_reach_by_name(&self, f: usize, r: DefRef) -> bool {
        // (Rust: a name of another crate is in reach only through a `use`)
        if self.family[f] == Some(Family::Rust) {
            return self.crate_of(f) == self.crate_of(r.0)
                || self.imports_name(f, &self.def(r).name);
        }
        // (Python: a bare name is the module's, an import's or a builtin; a
        // definition of a module the file does not import is none of them)
        if self.family[f] == Some(Family::Python) {
            return r.0 == f
                || self.scopes[f].imported.contains(&r.0)
                || self.imports_name(f, &self.def(r).name);
        }
        if !matches!(self.family[f], Some(Family::Jvm | Family::Php)) {
            return true;
        }
        let theirs = &self.files[r.0].package;
        let nested = self.def(r).parent.is_some_and(|p| self.def((r.0, p)).kind.is_class_like());
        // (PHP: only a function or a constant falls back from a namespace to
        // the global one; a class name does not, spec 18-namespaces.md)
        let global_ok = theirs.is_none()
            && !(self.family[f] == Some(Family::Php)
                && self.files[f].package.is_some()
                && self.def(r).kind.is_class_like());
        (global_ok || *theirs == self.files[f].package) && !(nested && r.0 != f)
    }

    /// A bare name defined nowhere in reach: a project-wide unique function or
    /// type of the same family.
    fn fallback_name(&self, f: usize, name: &str) -> Option<DefRef> {
        if self.builtin(f, name) {
            return None;
        }
        let fam = self.family[f];
        let free = !fam.is_some_and(|x| x.implicit_this());
        let cands: Vec<DefRef> = self
            .by_name
            .get(name)?
            .iter()
            .copied()
            .filter(|r| self.family[r.0] == fam)
            .filter(|r| {
                let d = self.def(*r);
                (d.kind.is_callable() || d.kind.is_class_like())
                    && !(free && d.kind == DefKind::Method)
                    && d.scope_lines.is_none()
            })
            // where names live in packages, a name of another package is
            // not in reach without an import (which the scopes have read),
            // nor is a type nested in a type of another file. (Not C#: a
            // project file can import a namespace for every source, `<Using
            // Include="…"/>`, which nothing here reads.)
            .filter(|r| self.in_reach_by_name(f, *r))
            .collect();
        self.narrow(f, cands).map(|(r, _)| r)
    }

    /// A method called on a receiver the walk cannot type: an extension of
    /// that name the caller can see, else a method only one type has.
    fn unknown_receiver(
        &self,
        f: usize,
        name: &str,
        on: Option<&str>,
    ) -> Option<(Target, Confidence)> {
        self.extension_method(f, name, on).or_else(|| {
            self.fallback_method(f, name).map(|r| (Target::Def(r), Confidence::Inferred))
        })
    }

    /// The one PHP class whose written base is `Authenticatable`
    /// (`Illuminate\Foundation\Auth\User as Authenticatable`): what
    /// Laravel's `auth()->user()` gives.
    fn authenticatable_model(&self) -> Option<DefRef> {
        let mut found = None;
        for &(fi, d) in self.php_bases.get("Authenticatable").into_iter().flatten() {
            if found.is_some_and(|x| x != (fi, d)) {
                return None;
            }
            found = Some((fi, d));
        }
        found.filter(|c| self.def(*c).kind.is_class_like())
    }

    /// The field a Lombok accessor reads or writes: `getTags` / `isActive` /
    /// `setTags` of a type (or a base) annotated `@Data`, `@Value`, `@Getter`
    /// or `@Setter`, or whose field of that name is.
    fn lombok_field(&self, c: DefRef, name: &str) -> Option<DefRef> {
        if self.files[c.0].lang != Some(crate::lang::Lang::Java) {
            return None;
        }
        let (prop, setter) = if let Some(p) = name.strip_prefix("get") {
            (p, false)
        } else if let Some(p) = name.strip_prefix("is") {
            (p, false)
        } else {
            (name.strip_prefix("set")?, true)
        };
        let mut chars = prop.chars();
        let first = chars.next().filter(|c| c.is_uppercase())?;
        let field: String = first.to_lowercase().chain(chars).collect();
        let marked = |d: DefRef, words: &[&str]| {
            self.files[d.0].refs.iter().any(|r| {
                r.from_def == Some(d.1)
                    && r.kind == RefKind::Decorator
                    && r.path.last().is_some_and(|n| words.contains(&n.as_str()))
            })
        };
        let words: &[&str] =
            if setter { &["Data", "Setter"] } else { &["Data", "Value", "Getter"] };
        let mut types = vec![c];
        types.extend(self.bases(c));
        types.into_iter().find_map(|t| {
            let f = self.members(t, &field).into_iter().find(|m| self.def(*m).kind.is_state())?;
            (marked(t, words) || marked(f, words)).then_some(f)
        })
    }

    /// Swift `request.headers.update(…)` on a local of a library type (or
    /// of none the walk knows): `headers` is a property extensions add to
    /// library types; when every such property of the name holds the same
    /// project type, the walk goes on through it (`INFERRED`).
    fn extension_property_hop(
        &self,
        f: usize,
        rest: &[String],
        bases: bool,
        conf: Confidence,
    ) -> Option<(Target, Confidence)> {
        if self.family[f] != Some(Family::Swift) || rest.len() < 2 || bases {
            return None;
        }
        let name = rest.first().filter(|s| !s.starts_with('[') && *s != "()")?;
        let mut held: Option<DefRef> = None;
        for c in self.by_name.get(name.as_str())?.iter().copied() {
            // (a property an extension of a library type adds is the
            // extension's child; a method carries the type as its owner)
            let in_extension = self.def(c).parent.is_some_and(|p| {
                let e = self.def((c.0, p));
                e.kind == DefKind::Extension && self.type_named(c.0, &e.name).is_none()
            });
            if self.family[c.0] != Some(Family::Swift)
                || !self.def(c).kind.is_state()
                || !(in_extension || self.extended_type(c).is_some())
            {
                continue;
            }
            let t = self.held_type(c)?;
            if held.is_some_and(|h| h != t) {
                return None;
            }
            held = Some(t);
        }
        self.walk(Target::Def(held?), &rest[1..], conf.max(Confidence::Inferred))
    }

    /// The type of the `j`th parameter of the delegate the C# callable `c`'s
    /// `arg`th parameter is declared as (`Func<Repo, int> f`: `Repo` for
    /// `j` 0; a delegate the project declares: its own parameter), an
    /// extension's receiver not counted.
    fn csharp_lambda_param(&self, c: DefRef, arg: usize, j: usize) -> Option<DefRef> {
        // (of overloads, the ones whose delegate has such a parameter, when
        // they agree: `Apply(Func<int>)` beside `Apply(Func<Repo, int>)`)
        let mut found: Option<DefRef> = None;
        for o in self.overloads(c) {
            if let Some(t) = self.csharp_lambda_param_of(o, arg, j) {
                if found.is_some_and(|x| x != t) {
                    return None;
                }
                found = Some(t);
            }
        }
        found
    }

    fn csharp_lambda_param_of(&self, c: DefRef, arg: usize, j: usize) -> Option<DefRef> {
        let def = self.def(c);
        if !def.kind.is_callable() {
            return None;
        }
        let skip = usize::from(def.receiver.is_some());
        let param = def.plist.get(arg + skip)?;
        let written = param.written.trim();
        let typed = |path: &[String], file: usize, scope: Option<usize>| {
            self.type_of_path(file, scope, path, true).filter(|t| self.def(*t).kind.is_class_like())
        };
        let (bare, args) = crate::refs::split_generic(written);
        if !args.is_empty() {
            if let Some(params) = crate::refs::csharp_delegate_params(&bare, &args) {
                let p = params.get(j)?;
                return typed(p, c.0, Some(c.1));
            }
        }
        // a delegate the project declares
        if param.ty.is_empty() {
            return None;
        }
        let d = self.type_of_path(c.0, Some(c.1), &param.ty, true)?;
        let dd = self.def(d);
        if dd.kind.is_class_like() {
            return None;
        }
        let p = dd.plist.get(j).filter(|p| !p.ty.is_empty())?;
        typed(&p.ty, d.0, Some(d.1))
    }

    /// What C# `await` of a value of the project type `c` gives: the result
    /// of the `GetResult` of what its `GetAwaiter` (its own, a base's, or an
    /// extension for it) returns (the standard's 12.9.8.2).
    fn csharp_awaited(&self, f: usize, c: DefRef) -> Option<DefRef> {
        if !self.def(c).kind.is_class_like() {
            return None;
        }
        let awaiter = self
            .class_member(c, "GetAwaiter", 4)
            .filter(|m| self.def(*m).kind.is_callable())
            .or_else(|| match self.extension_for(f, "GetAwaiter", c) {
                Some((Target::Def(m), Confidence::Extracted)) => Some(m),
                _ => None,
            })?;
        let w = self.return_type(awaiter).map(|(r, _)| r)?;
        let result = self.class_member(w, "GetResult", 4)?;
        self.return_type(result).map(|(r, _)| r).filter(|r| self.def(*r).kind.is_class_like())
    }

    /// The type of the `part`th out parameter of the `Deconstruct` a C#
    /// deconstruction of a value of type `c` calls: its own or a base's, else
    /// an extension for it; of several, only a type they agree on.
    fn csharp_deconstructed(&self, f: usize, c: DefRef, part: usize) -> Option<DefRef> {
        let typed = |m: DefRef, skip: usize| {
            let d = self.def(m);
            let p = d.plist.get(part + skip).filter(|p| !p.ty.is_empty())?;
            self.type_of_path(m.0, Some(m.1), &p.ty, true)
                .filter(|t| self.def(*t).kind.is_class_like())
        };
        let mut at = vec![c];
        for _ in 0..4 {
            let found: Vec<DefRef> = at
                .iter()
                .flat_map(|k| self.members(*k, "Deconstruct"))
                .filter(|m| self.def(*m).plist.len() > part)
                .collect();
            if !found.is_empty() {
                let types: Vec<Option<DefRef>> = found.iter().map(|m| typed(*m, 0)).collect();
                return types.windows(2).all(|w| w[0] == w[1]).then(|| types[0]).flatten();
            }
            at = at.iter().flat_map(|k| self.bases(*k)).collect();
            if at.is_empty() {
                break;
            }
        }
        match self.extension_for(f, "Deconstruct", c) {
            Some((Target::Def(m), Confidence::Extracted)) => typed(m, 1),
            _ => None,
        }
    }

    /// What a C# `foreach` over a value of the project type `c` gives: the
    /// type of the `Current` of what `c`'s `GetEnumerator` (or
    /// `GetAsyncEnumerator`, or such an extension for `c`) returns, the
    /// method before an interface (`IEnumerable<T>`, whose `T` it is
    /// otherwise).
    fn csharp_iterated(&self, f: usize, c: DefRef) -> Option<DefRef> {
        let value_of = |x: DefRef| {
            let k = self.def(x).kind;
            if k.is_callable() {
                self.return_type(x).map(|(r, _)| r)
            } else if k.is_state() {
                self.held_type(x)
            } else {
                None
            }
        };
        let current = |e: DefRef| {
            let cur = self.class_member(e, "Current", 4)?;
            value_of(cur).filter(|t| self.def(*t).kind.is_class_like())
        };
        for name in ["GetEnumerator", "GetAsyncEnumerator"] {
            let mut at = vec![c];
            for _ in 0..4 {
                let found = at.iter().find_map(|k| {
                    self.members(*k, name).into_iter().find_map(|m| {
                        let e = self.return_type(m).map(|(r, _)| r)?;
                        self.def(e).kind.is_class_like().then(|| current(e)).flatten()
                    })
                });
                if found.is_some() {
                    return found;
                }
                at = at.iter().flat_map(|k| self.bases(*k)).collect();
                if at.is_empty() {
                    break;
                }
            }
            if let Some((Target::Def(m), Confidence::Extracted)) = self.extension_for(f, name, c) {
                if let Some(x) = self.return_type(m).map(|(r, _)| r).and_then(&current) {
                    return Some(x);
                }
            }
        }
        // a source of a query with no enumerator: what its `Select` or
        // `Where` hands its delegate (the standard's 12.20.4)
        for name in ["Select", "Where"] {
            for m in self.members(c, name) {
                if let Some(x) = self.csharp_lambda_param_of(m, 0, 0) {
                    return Some(x);
                }
            }
        }
        // `class Bag : IEnumerable<Item>`
        let def = self.def(c);
        def.base_targs.iter().find_map(|(base, args)| {
            let last = base.last()?;
            if !matches!(last.as_str(), "IEnumerable" | "IAsyncEnumerable") {
                return None;
            }
            let [arg] = args.as_slice() else { return None };
            self.type_of_path(c.0, def.parent, arg, true)
                .filter(|t| self.def(*t).kind.is_class_like())
        })
    }

    /// An extension named `name` written for type `c`, or for one of its
    /// bases (`client.BuildUri(r)` on a `RestClient`, written for
    /// `IRestClient`): that is what the language calls. Else what
    /// `extension_method` makes of the type's own name.
    fn extension_for(&self, f: usize, name: &str, c: DefRef) -> Option<(Target, Confidence)> {
        let own = self.extension_method(f, name, Some(&self.def(c).name));
        if matches!(own, Some((_, Confidence::Extracted))) {
            return own;
        }
        let mut seen = vec![c];
        let mut frontier = vec![c];
        for _ in 0..4 {
            let mut next = Vec::new();
            for t in frontier {
                for b in self.bases(t) {
                    if !seen.contains(&b) {
                        seen.push(b);
                        next.push(b);
                    }
                }
            }
            for b in &next {
                let found = self.extension_method(f, name, Some(&self.def(*b).name));
                if matches!(found, Some((_, Confidence::Extracted))) {
                    return found;
                }
            }
            if next.is_empty() {
                break;
            }
            frontier = next;
        }
        own
    }

    /// `name` called on a field or property `p` whose written type the
    /// project does not define: the extension written for that type, else
    /// the one extension of the name in sight (`extension_method`). Where no
    /// type is written there is nothing to go by.
    fn extension_on_member(&self, f: usize, p: DefRef, name: &str) -> Option<(Target, Confidence)> {
        let def = self.def(p);
        let written = def.declared_type.as_ref()?.last()?;
        if self.type_named(p.0, written).is_some() {
            return None;
        }
        self.extension_method(f, name, Some(written))
    }

    /// An extension function or method named `name` (`Def::receiver`) that
    /// file `f` can see: its own, one in a file it imports, in its directory
    /// or in its package. `on` is the receiver's type when the source writes
    /// it: the extension written for that type is `EXTRACTED`; otherwise a
    /// single candidate (the file's own first) is `INFERRED`.
    /// The namespaces a C# file declares: the one written for the whole file
    /// (`namespace N;`), else each block at its top (`namespace N { … }`).
    fn csharp_namespaces(&self, f: usize) -> Vec<String> {
        if let Some(p) = &self.files[f].package {
            return vec![p.clone()];
        }
        self.files[f]
            .defs
            .iter()
            .filter(|d| d.kind == DefKind::Namespace && d.parent.is_none())
            .map(|d| d.qualname.replace("::", "."))
            .collect()
    }

    /// Scala `export p.print` in `c`: the member it forwards, looked up from
    /// inside `c` ("Export Clauses": an alias of the member selected).
    fn forwarded_member(&self, c: DefRef, name: &str, depth: usize) -> Option<DefRef> {
        let facts = &self.files[c.0];
        if facts.forwards.is_empty() || depth == 0 {
            return None;
        }
        let d = self.def(c);
        let path = facts.forwards.iter().find_map(|(at, p)| {
            (d.start_byte <= *at && *at < d.end_byte && p.last().is_some_and(|l| l == name))
                .then_some(p)
        })?;
        match self.resolve_path(c.0, Some(c.1), path, true)? {
            (Target::Def(m), _) if m != c => Some(m),
            _ => None,
        }
    }

    /// Rust: of a module and a value of one name in the nearest scope that
    /// declares the name, the value where it is called, the module where a
    /// path goes on from it. `None` when the scope has only one kind.
    fn rust_namespace_pick(
        &self,
        f: usize,
        d: Option<usize>,
        name: &str,
        called: bool,
    ) -> Option<DefRef> {
        let defs = &self.files[f].defs;
        let mut cur = d;
        let mut cands: Vec<usize> = Vec::new();
        while let Some(x) = cur {
            if let Some(k) = self.children[f].get(&x).and_then(|m| m.get(name)) {
                cands = k.clone();
                break;
            }
            cur = defs[x].parent;
        }
        if cands.is_empty() {
            cands = self.top[f].get(name).cloned().unwrap_or_default();
        }
        let c_family = self.family[f] == Some(Family::C);
        let module = |i: &usize| {
            matches!(defs[*i].kind, DefKind::Module | DefKind::Namespace)
                || (c_family && defs[*i].kind.is_class_like())
        };
        // (C++: a constructor defined out of its class, `T::T()`, is the
        // class's, not a function beside it)
        let value = |i: &usize| {
            defs[*i].kind.is_callable()
                && !(c_family
                    && (defs[*i].owner.is_some() || defs[*i].kind == DefKind::Constructor))
        };
        if !(cands.iter().any(module) && cands.iter().any(value)) {
            return None;
        }
        let pick: Vec<DefRef> = cands
            .iter()
            .filter(|i| if called { value(i) } else { module(i) })
            .map(|&i| (f, i))
            .collect();
        self.best(pick)
    }

    /// The C# namespace a definition is written in: the innermost
    /// namespace block around it, else the file's.
    fn cs_namespace_of(&self, f: usize, mut i: usize) -> Option<String> {
        loop {
            let d = &self.files[f].defs[i];
            if d.kind == DefKind::Namespace {
                return Some(d.qualname.replace("::", "."));
            }
            match d.parent {
                Some(p) => i = p,
                None => return self.files[f].package.clone(),
            }
        }
    }

    /// C#: a definition of another namespace block of the file is no name
    /// of the use's namespace unless that block is around it (§7.8.1).
    fn cs_namespace_in_sight(&self, f: usize, d: Option<usize>, r: DefRef) -> bool {
        if self.family[f] != Some(Family::CSharp) || r.0 != f {
            return true;
        }
        let (Some(theirs), Some(mine)) =
            (self.cs_namespace_of(r.0, r.1), d.and_then(|x| self.cs_namespace_of(f, x)))
        else {
            return true;
        };
        mine == theirs || mine.strip_prefix(theirs.as_str()).is_some_and(|x| x.starts_with('.'))
    }

    /// Rust: an item of an inline module is no name outside it, only by a
    /// path (r[names.scopes.items.nested-modules]). (An item of the file's
    /// own module stays in sight of its inline modules: `use super::*;` in
    /// `mod tests` is read as the file's scope.)
    fn rust_module_in_sight(&self, f: usize, d: Option<usize>, r: DefRef) -> bool {
        if self.family[f] != Some(Family::Rust) || r.0 != f {
            return true;
        }
        let defs = &self.files[f].defs;
        let module_of = |mut i: usize| -> Option<usize> {
            while let Some(p) = defs[i].parent {
                if matches!(defs[p].kind, DefKind::Module | DefKind::Namespace) {
                    return Some(p);
                }
                i = p;
            }
            None
        };
        let Some(theirs) = module_of(r.1) else { return true };
        // the use is inside that module (or is it)
        let mut cur = d;
        while let Some(x) = cur {
            if x == theirs {
                return true;
            }
            cur = defs[x].parent;
        }
        false
    }

    /// Kotlin: of extensions written for the receiver's type, those of the
    /// first set the specification lists (overload-resolution.md, 'Call with
    /// an explicit receiver'): declared in a function around the use, then
    /// imported by name, then of the file's package, then star-imported.
    fn kotlin_extension_order(&self, f: usize, exact: Vec<DefRef>) -> Vec<DefRef> {
        let home = (*self.cur.borrow())
            .filter(|(cf, _)| *cf == f)
            .and_then(|(cf, ri)| self.files[cf].refs[ri].from_def);
        let around = |r: &DefRef| -> bool {
            let Some(p) = self.def(*r).parent else { return false };
            let mut cur = home;
            while let Some(x) = cur {
                if r.0 == f && x == p {
                    return true;
                }
                cur = self.files[f].defs[x].parent;
            }
            false
        };
        let pkg_of = |g: usize| self.files[g].package.clone().unwrap_or_default();
        let named = |r: &DefRef| {
            let name = &self.def(*r).name;
            self.files[f].imports.iter().any(|imp| {
                imp.form == ImportForm::From
                    && imp.module == pkg_of(r.0)
                    && imp.names.iter().any(|(n, _)| n == name)
            })
        };
        let rank = |r: &DefRef| -> u8 {
            if around(r) {
                0
            } else if named(r) {
                1
            } else if pkg_of(r.0) == pkg_of(f) {
                2
            } else {
                3
            }
        };
        let best = exact.iter().map(rank).min().unwrap_or(3);
        exact.into_iter().filter(|r| rank(r) == best).collect()
    }

    /// Of extensions written for one type, those of the namespace closest
    /// around the use being resolved.
    fn closest_extensions(&self, f: usize, exact: Vec<DefRef>) -> Vec<DefRef> {
        let Some(home) = (*self.cur.borrow())
            .filter(|(cf, _)| *cf == f)
            .and_then(|(cf, ri)| self.files[cf].refs[ri].from_def)
        else {
            return exact;
        };
        let Some(mine) = self.cs_namespace_of(f, home) else { return exact };
        let rank = |r: &DefRef| -> usize {
            match self.cs_namespace_of(r.0, r.1) {
                Some(ns)
                    if mine == ns
                        || mine.strip_prefix(ns.as_str()).is_some_and(|x| x.starts_with('.')) =>
                {
                    ns.split('.').count()
                }
                _ => 0,
            }
        };
        let best = exact.iter().map(rank).max().unwrap_or(0);
        exact.into_iter().filter(|r| rank(r) == best).collect()
    }

    fn extension_method(
        &self,
        f: usize,
        name: &str,
        on: Option<&str>,
    ) -> Option<(Target, Confidence)> {
        let fam = self.family[f];
        // Java has no extensions: a Kotlin one is a static method there,
        // never `latch.await()`
        if self.files[f].lang == Some(crate::lang::Lang::Java) {
            return None;
        }
        let here = dir_of(&self.files[f].path);
        let pkg = self.files[f].package.as_deref();
        let spaces =
            if fam == Some(Family::CSharp) { self.csharp_namespaces(f) } else { Vec::new() };
        // Rust: a method of an `impl Trait for` a type the project does not
        // define (`impl RequestExt for http::Request<()>`) extends that type
        let mut pool: Vec<DefRef> =
            self.extensions.get(name).into_iter().flatten().copied().collect();
        if matches!(fam, Some(Family::Rust | Family::Swift)) {
            pool.extend(self.by_name.get(name).into_iter().flatten().copied().filter(|r| {
                let d = self.def(*r);
                d.kind.is_callable() && !d.decl_only && self.extended_type(*r).is_some()
            }));
        }
        let seen: Vec<DefRef> = pool
            .into_iter()
            .filter(|r| self.family[r.0] == fam && self.node_id(*r).is_some())
            .filter(|r| {
                r.0 == f
                    || self.scopes[f].imported.contains(&r.0)
                    || dir_of(&self.files[r.0].path) == here
                    || (pkg.is_some() && self.files[r.0].package.as_deref() == pkg)
                    // C#: a namespace sees what the ones around it declare
                    // (`RestSharp.Tests` sees `RestSharp`'s extensions),
                    // written as a block or for the whole file
                    || (fam == Some(Family::CSharp) && {
                        let its = self.csharp_namespaces(r.0);
                        spaces.iter().any(|mine| {
                            its.iter().any(|its| {
                                mine == its
                                    || mine.strip_prefix(its.as_str()).is_some_and(|rest| rest.starts_with('.'))
                            })
                        })
                    })
                    || self.uses_namespace_of(f, r.0)
            })
            .collect();
        if let Some(on) = on {
            let exact: Vec<DefRef> = seen
                .iter()
                .copied()
                .filter(|r| {
                    let written = self.def(*r).receiver.clone().or_else(|| self.extended_type(*r));
                    written.is_some_and(|t| t.eq_ignore_ascii_case(on))
                })
                .collect();
            // C#: of several, those of the closest namespace around the use
            // (an imported one ranks below every enclosing one: §12.8.10.3)
            let exact = if fam == Some(Family::CSharp) && exact.len() > 1 {
                self.closest_extensions(f, exact)
            } else if self.files[f].lang == Some(crate::lang::Lang::Kotlin) && exact.len() > 1 {
                self.kotlin_extension_order(f, exact)
            } else {
                exact
            };
            let mine: Vec<DefRef> = exact.iter().copied().filter(|r| r.0 == f).collect();
            match (mine.as_slice(), exact.as_slice()) {
                ([one], _) | (_, [one]) => return Some((Target::Def(*one), Confidence::Extracted)),
                // the overloads of one extension written for the type:
                // `pick_overload` says which
                (_, [first, ..])
                    if fam.is_some_and(Family::overloads)
                        && exact.iter().all(|r| {
                            r.0 == first.0 && self.def(*r).parent == self.def(*first).parent
                        }) =>
                {
                    return Some((Target::Def(*first), Confidence::Extracted));
                }
                _ => {}
            }
        }
        // a receiver of a type the project does not define, or of none the
        // walk knows: a name .NET's own strings, lists and types answer to
        // (`items.ForEach(…)`, `text.Split(' ')`) is theirs before an
        // extension's
        let known = on.is_some_and(|o| self.type_named(f, o).is_some());
        if fam == Some(Family::CSharp) && !known && CSHARP_CORE_METHODS.contains(&name) {
            return None;
        }
        let mine: Vec<DefRef> = seen.iter().copied().filter(|r| r.0 == f).collect();
        match (mine.as_slice(), seen.as_slice()) {
            ([one], _) | (_, [one]) => {
                self.guessed.set(true);
                Some((Target::Def(*one), Confidence::Inferred))
            }
            // the overloads of one extension (`asAFError(or:)`,
            // `asAFError(orFailWith:)`): `pick_overload` says which
            (_, [first, ..])
                if fam.is_some_and(Family::overloads)
                    && seen.iter().all(|r| {
                        r.0 == first.0
                            && self.def(*r).parent == self.def(*first).parent
                            && self.def(*r).receiver == self.def(*first).receiver
                    }) =>
            {
                Some((Target::Def(*first), Confidence::Inferred))
            }
            // one declaration written once per source set (Kotlin `expect` /
            // `actual`): the one nearest the caller
            (_, [first, ..])
                if seen.iter().all(|r| {
                    self.def(*r).receiver == self.def(*first).receiver
                        && self.def(*r).plist.len() == self.def(*first).plist.len()
                        && self.files[r.0].package == self.files[first.0].package
                        && r.0 != first.0
                        || r == first
                }) =>
            {
                self.narrow(f, seen.clone()).map(|(r, _)| (Target::Def(r), Confidence::Inferred))
            }
            _ => None,
        }
    }

    /// The bare name of the type a Rust method is written for, when the
    /// project does not define that type (`http::Request<()>` -> `Request`).
    fn extended_type(&self, r: DefRef) -> Option<String> {
        // (and a method of a Swift `extension Error { … }`)
        if !matches!(self.family[r.0], Some(Family::Rust | Family::Swift)) {
            return None;
        }
        let owner = self.def(r).owner.as_deref()?;
        let bare = owner.split('<').next()?.rsplit(['.', ':']).next()?.trim();
        (!bare.is_empty() && self.type_named(r.0, bare).is_none()).then(|| bare.to_string())
    }

    /// Whether file `f` imports a whole namespace or package that file `g` is
    /// in (`using App.Extensions;`, `import app.specs.*`).
    fn uses_namespace_of(&self, f: usize, g: usize) -> bool {
        let Some(fam) = self.family[f] else { return false };
        self.scopes[f].stars.iter().any(|(star, _)| match star {
            Target::Ns(ns) => {
                self.pkg_files.get(&(fam, ns.clone())).is_some_and(|fs| fs.contains(&g))
            }
            Target::Module(fs) => fs.contains(&g),
            // (a namespace written as a block, `namespace N { … }`: every
            // file that opens it)
            Target::Def(c) if self.def(*c).kind == DefKind::Namespace => {
                let ns = self.def(*c).qualname.replace("::", ".");
                c.0 == g || self.pkg_files.get(&(fam, ns)).is_some_and(|fs| fs.contains(&g))
            }
            _ => false,
        })
    }

    /// The type a local is declared with, bare (`string`, `KmProperty`).
    fn local_written_type(&self, f: usize, d: Option<usize>, name: &str) -> Option<String> {
        let defs = &self.files[f].defs;
        let mut cur = d;
        while let Some(x) = cur {
            if let Some(&li) = self.locals.get(&(f, x, name.to_string())) {
                let l = &self.files[f].locals[li];
                return l.path.last().filter(|_| l.declared).cloned();
            }
            cur = defs[x].parent;
        }
        None
    }

    /// The one name a local's value is built from (`var m = new Message()`),
    /// where no type is written: a hint for an extension written for it.
    fn local_built_type(&self, f: usize, d: Option<usize>, name: &str) -> Option<String> {
        let defs = &self.files[f].defs;
        let mut cur = d;
        while let Some(x) = cur {
            if let Some(&li) = self.locals.get(&(f, x, name.to_string())) {
                return match self.files[f].locals[li].path.as_slice() {
                    [one] => Some(one.clone()),
                    _ => None,
                };
            }
            cur = defs[x].parent;
        }
        None
    }

    /// `self.name()` in a method whose type the project does not define: the
    /// method of that name written for the same type in this file.
    fn owner_sibling(&self, f: usize, d: Option<usize>, name: &str) -> Option<DefRef> {
        let defs = &self.files[f].defs;
        let mut cur = d;
        let owner = loop {
            let x = cur?;
            if let Some(o) = &defs[x].owner {
                break o.clone();
            }
            cur = defs[x].parent;
        };
        let cands: Vec<DefRef> = defs
            .iter()
            .enumerate()
            .filter(|(_, x)| x.name == name && x.owner.as_deref() == Some(owner.as_str()))
            .map(|(i, _)| (f, i))
            // not the caller itself: `(**self).is_open()` in `impl Transport
            // for Box<T>` forwards to another type's method
            .filter(|r| self.node_id(*r).is_some() && Some(r.1) != d)
            .collect();
        match cands.as_slice() {
            [one] => Some(*one),
            _ => None,
        }
    }

    /// `x.name()` on a receiver of unknown type: a method that only one type
    /// of the project has, and not a name every type has.
    fn fallback_method(&self, f: usize, name: &str) -> Option<DefRef> {
        let lower = name.to_lowercase();
        if COMMON_METHODS.iter().any(|c| c.to_lowercase() == lower)
            || self.builtin(f, name)
            || name.len() < 4
        {
            return None;
        }
        let fam = self.family[f];
        // (nor a name the language's own types answer to: Node's `once`,
        // Rust's `Mutex::lock`, C++'s `c_str`)
        let core: &[&str] = match fam {
            Some(Family::Ecma) => ECMA_CORE_METHODS,
            Some(Family::Rust) => RUST_CORE_METHODS,
            Some(Family::C) => CPP_CORE_METHODS,
            _ => &[],
        };
        if core.contains(&name) {
            return None;
        }
        let cands: Vec<DefRef> = self
            .by_name
            .get(name)?
            .iter()
            .copied()
            .filter(|r| self.family[r.0] == fam)
            .filter(|r| {
                let d = self.def(*r);
                // (a function assigned to an object is that object's
                // method: `res.sendFile = function sendFile() {}`; one
                // assigned to `exports` is the module's function)
                d.kind.is_callable()
                    && (d.kind == DefKind::Method
                        || d.owner.is_some()
                        || d.object.as_deref().is_some_and(|o| o != "exports"))
                    && !d.decl_only
                    && d.scope_lines.is_none()
            })
            .collect();
        if cands.len() != 1 {
            return None;
        }
        let c = cands[0];
        // evidence: the caller imports the method's file, or shares its
        // directory (Ruby and Swift see every type of their module)
        let open_world = matches!(fam, Some(Family::Ruby) | Some(Family::Swift));
        let evident = open_world
            || c.0 == f
            || self.scopes[f].imported.contains(&c.0)
            || dir_of(&self.files[c.0].path) == dir_of(&self.files[f].path);
        if evident {
            self.guessed.set(true);
        }
        evident.then_some(c)
    }

    /// The node a use is made from: the innermost enclosing definition that
    /// is a node (an extension's type, an impl's type), else the file.
    /// Whether the callable around `d` is a constructor (`__init__`, `init`,
    /// `constructor`, a Java / C# / C++ constructor, Ruby `initialize`).
    fn constructs(&self, f: usize, d: Option<usize>) -> bool {
        let defs = &self.files[f].defs;
        let mut cur = d;
        while let Some(x) = cur {
            if defs[x].kind.is_callable() {
                return defs[x].kind == DefKind::Constructor
                    || matches!(
                        defs[x].name.as_str(),
                        "__init__" | "init" | "constructor" | "initialize"
                    );
            }
            cur = defs[x].parent;
        }
        false
    }

    /// The table a call reads or writes through an ORM: `User.objects.filter()`,
    /// `Article::where()`, `user.save()`, `db.Items.Add(x)`, when what the
    /// method is called on is a data model (`Def::table`).
    fn orm_use(&self, f: usize, r: &RefFact) -> Option<(String, bool)> {
        if r.kind != RefKind::Call || r.path.len() < 2 {
            return None;
        }
        let method = r.path.last()?.as_str();
        let write = if ORM_WRITES.contains(&method) {
            true
        } else if ORM_READS.contains(&method) {
            false
        } else {
            return None;
        };
        let mut prefix = r.path[..r.path.len() - 1].to_vec();
        // Django `User.objects`, Flask-SQLAlchemy `User.query`
        while prefix.len() > 1
            && matches!(prefix.last().map(String::as_str), Some("objects" | "query" | "()"))
        {
            prefix.pop();
        }
        if prefix.iter().any(|s| s == "()") {
            return None;
        }
        let (target, _) = self.resolve_path(f, r.from_def, &prefix, true)?;
        let Target::Def(t) = target else { return None };
        self.def(t).table.clone().map(|table| (table, write))
    }

    /// Of the overloads of `c`, the one other than `c` whose result has a
    /// member `next`, when `c`'s own has none and exactly one does.
    fn overload_giving(&self, c: DefRef, next: Option<&String>) -> Option<DefRef> {
        let next = next.filter(|s| !s.starts_with('[') && *s != "()")?;
        if !self.def(c).kind.is_callable() {
            return None;
        }
        let all = self.overloads(c);
        if all.len() < 2 {
            return None;
        }
        let gives = |o: DefRef| {
            self.return_type(o).is_some_and(|(r, _)| self.class_member(r, next, 4).is_some())
        };
        if gives(c) {
            return None;
        }
        let mut fit = all.into_iter().filter(|o| *o != c && gives(*o));
        match (fit.next(), fit.next()) {
            (Some(o), None) => Some(o),
            _ => None,
        }
    }

    /// The project type a variable, field or constant is declared with.
    fn declared_state_type(&self, p: DefRef) -> Option<(DefRef, Confidence)> {
        let def = self.def(p);
        let ty = def.declared_type.as_ref()?;
        let t = self.type_of_path(p.0, def.parent, ty, true)?;
        self.is_type_like(t).then_some((t, Confidence::Extracted))
    }

    /// The type of a variable from what constructs its value
    /// (`settings = Settings()`).
    fn state_type(&self, p: DefRef) -> Option<(DefRef, Confidence)> {
        let d = self.def(p);
        let path = d.value_path.as_ref()?;
        let t = self.type_of_path(p.0, d.parent, path, false)?;
        self.is_type_like(t).then_some((t, Confidence::Inferred))
    }

    /// Whether a variable is assigned something of the project's that the
    /// walk could name the start of (`express()`, `makeStore().open()`): not
    /// a literal, not a library's value.
    fn holds_project_value(&self, p: DefRef) -> bool {
        let d = self.def(p);
        let Some(head) = d.value_path.as_ref().and_then(|v| v.first()) else { return false };
        if matches!(head.as_str(), "self" | "super" | "()") || head.starts_with('[') {
            return false;
        }
        matches!(self.resolve_name(p.0, d.parent, head), Some(Target::Def(_) | Target::Module(_)))
    }

    fn source_of(&self, f: usize, r: &RefFact) -> String {
        if let Some(owner) = &r.from_owner {
            if let Some(t) = self.type_named(f, owner) {
                if let Some(id) = self.node_id(t) {
                    return id.to_string();
                }
            }
            return self.hubs[f].clone();
        }
        let defs = &self.files[f].defs;
        let mut cur = r.from_def;
        // a field's type, or what its initializer calls, is its type's use
        // (a Swift computed property's body is its own)
        let swift = self.family[f] == Some(Family::Swift);
        // (and a Go package's `const` / `var`)
        let rust = matches!(self.family[f], Some(Family::Rust | Family::Go));
        let rust_item = |x: usize| {
            rust && matches!(defs[x].kind, DefKind::Constant | DefKind::Variable)
                && defs[x].parent.is_none_or(|p| !defs[p].kind.is_class_like())
        };
        while let Some(x) = cur {
            // (an extra definition that is a callable, RSpec's `let`, is
            // the source of its own block's uses)
            // (and so is a C# property with a body)
            let own = (swift || defs[x].computed) && r.kind != RefKind::Type
                // (a Rust `const` / `static` item of a module: its type and
                // what its value names are its own, there being no type
                // around to have them)
                || (rust_item(x) && defs[x].extra);
            let skip = defs[x].extra && !defs[x].kind.is_callable() && !own;
            if skip {
                cur = defs[x].parent;
                continue;
            }
            if let Some(id) = self.node_id((f, x)) {
                return id.to_string();
            }
            if defs[x].kind == DefKind::Extension {
                if let Some(t) = self.type_named(f, &defs[x].name) {
                    if let Some(id) = self.node_id(t) {
                        return id.to_string();
                    }
                }
            }
            cur = defs[x].parent;
        }
        self.hubs[f].clone()
    }

    /// Whether `m` can be what a call of `base` runs: a method, written for
    /// as many parameters where the language tells methods of one name apart
    /// by them.
    fn overrides(&self, m: DefRef, base: DefRef) -> bool {
        let (x, y) = (self.def(m), self.def(base));
        let method = |d: &Def| d.kind.is_callable() && d.kind != DefKind::Constructor;
        if m == base || !method(x) || !method(y) || x.anonymous || y.anonymous {
            return false;
        }
        // what builds an object is called on the class that is named, never
        // through its base: each language's constructor by its own name
        let builds = match self.files[m.0].lang {
            Some(Lang::Python) => matches!(x.name.as_str(), "__init__" | "__new__"),
            Some(Lang::Ruby) => x.name == "initialize",
            Some(Lang::Php) => x.name == "__construct",
            _ => false,
        };
        if builds {
            return false;
        }
        let counted = self.family[m.0].is_some_and(Family::overloads);
        !counted || x.params.is_none() || y.params.is_none() || x.params == y.params
    }

    /// Whether `m` is written with the parameters of `base`, where a count
    /// that agrees does not say so. In Swift the labels are part of a method's
    /// name (`request(_:didParse:)` is not `request(_:didCreate:)`), always;
    /// the types as written tell overloads apart, and are asked only when
    /// either type has several such methods of the name (`contested`).
    fn same_signature(&self, m: DefRef, base: DefRef, contested: bool) -> bool {
        let (x, y) = (&self.def(m).plist, &self.def(base).plist);
        if self.files[m.0].lang == Some(Lang::Swift)
            && !(x.len() == y.len() && x.iter().zip(y).all(|(p, q)| p.name == q.name))
        {
            return false;
        }
        !contested || self.same_parameters(m, base)
    }

    /// `(base method, the method written for it)`: a type's method of the
    /// name of a method its base, interface, trait or mixin declares. A call
    /// of the base's method is a call of whichever of these the object has,
    /// and no call names them.
    ///
    /// The nearest base that declares the name answers; what it overrides in
    /// turn is its own pair. Go is left out: its interfaces are satisfied by
    /// shape, no type says which it implements, and an embedded type's method
    /// of the same name is hidden, not dispatched to.
    fn dispatch_pairs(&self) -> Vec<(DefRef, DefRef)> {
        const DEPTH: usize = 8;
        let mut out: Vec<(DefRef, DefRef)> = Vec::new();
        for (f, facts) in self.files.iter().enumerate() {
            let Some(fam) = self.family[f] else { continue };
            if fam == Family::Go {
                continue;
            }
            if fam == Family::Rust {
                // `impl Trait for Type { fn m }`: the methods written under
                // the `impl`, which the type need not be the project's
                // (`impl AsBody for &File`)
                let mut impls: Vec<(u32, &str, DefRef)> = Vec::new();
                for r in &facts.refs {
                    let Some(owner) = r.from_owner.as_deref() else { continue };
                    if r.kind != RefKind::Implements {
                        continue;
                    }
                    *self.types_only.borrow_mut() = true;
                    let found = self.resolve_path(f, None, &r.path, true);
                    *self.types_only.borrow_mut() = false;
                    if let Some((Target::Def(b), _)) = found {
                        impls.push((r.line, owner, b));
                    }
                }
                if impls.is_empty() {
                    continue;
                }
                impls.sort_by_key(|i| i.0);
                for (di, d) in facts.defs.iter().enumerate() {
                    let Some(owner) = d.owner.as_deref() else { continue };
                    // the `impl` the method is written under: the last one
                    // for its type that starts above it
                    let Some(&(_, _, b)) =
                        impls.iter().rev().find(|i| i.1 == owner && i.0 <= d.def_line)
                    else {
                        continue;
                    };
                    for base in self.members(b, &d.name) {
                        if self.overrides((f, di), base) {
                            out.push((base, (f, di)));
                        }
                    }
                }
                continue;
            }
            for (ci, c) in facts.defs.iter().enumerate() {
                if !c.kind.is_class_like() || c.anonymous || c.misread || c.block {
                    continue;
                }
                let c = (f, ci);
                let first = self.bases(c);
                if first.is_empty() {
                    continue;
                }
                let mut own: Vec<DefRef> = self.children[f]
                    .get(&ci)
                    .into_iter()
                    .flat_map(|m| m.values().flatten().map(|&d| (f, d)))
                    .collect();
                let cname = &self.def(c).name;
                for &m in self.owner_methods.get(&(fam, cname.clone())).into_iter().flatten() {
                    if self.owner_scope_matches(c, m) {
                        own.push(m);
                    }
                }
                own.sort_unstable();
                own.dedup();
                let counted = fam.overloads();
                for &m in &own {
                    let name = &self.def(m).name;
                    // the type's other methods of the name and as many parameters
                    let rivals = counted
                        && own.iter().any(|&o| {
                            o != m
                                && self.def(o).name == *name
                                && self.def(o).params == self.def(m).params
                                && self.overrides(o, m)
                        });
                    let mut level = first.clone();
                    let mut seen: HashSet<DefRef> = HashSet::from([c]);
                    for _ in 0..DEPTH {
                        level.retain(|b| seen.insert(*b));
                        if level.is_empty() {
                            break;
                        }
                        let mut next: Vec<DefRef> = Vec::new();
                        for &b in &level {
                            let mut found: Vec<DefRef> = self
                                .members(b, name)
                                .into_iter()
                                .filter(|&x| self.overrides(m, x))
                                .collect();
                            if counted {
                                let contested = rivals || found.len() > 1;
                                found.retain(|&x| self.same_signature(m, x, contested));
                            }
                            if found.is_empty() {
                                next.extend(self.bases(b));
                            } else {
                                out.extend(found.into_iter().map(|x| (x, m)));
                            }
                        }
                        level = next;
                    }
                }
            }
        }
        out.sort_unstable();
        out.dedup();
        out
    }

    fn ext_node_id(
        &self,
        e: usize,
        rest: &[String],
        symbol: bool,
    ) -> (String, String, &'static str) {
        let exts = self.exts.borrow();
        let base = &exts[e];
        let label = if symbol && !rest.is_empty() {
            let sep = match base.eco {
                "crate" => "::",
                "php" => "\\",
                _ => ".",
            };
            format!("{}{}{}", base.label, sep, rest.join(sep))
        } else {
            base.label.clone()
        };
        (format!("ext_{}_{}", base.eco, slug(&label)), label, base.eco)
    }
}

struct EdgeOut {
    source: String,
    target: String,
    relation: &'static str,
    confidence: Confidence,
    file: usize,
    line: u32,
    context: &'static str,
    runs: Option<Runs>,
}

/// What resolving one file's references gives, before the files' results
/// are put together.
#[derive(Default)]
struct FileOut {
    edges: Vec<EdgeOut>,
    left: Vec<Value>,
    orm: Vec<(String, String, usize, u32, bool)>,
    ext: Vec<(String, String, &'static str)>,
    stats: ResolveStats,
    /// What resolving the file read (`trace.rs`).
    frame: Rc<trace::Frame>,
}

/// The order the files' references are resolved in: file order, or for a
/// check that the order leaves the graph as it is (tests, benchmarks)
/// `GRAPHLORE_RESOLVE_ORDER=reverse` or `shuffle:SEED`.
fn resolve_order(n: usize) -> Vec<usize> {
    let mut order: Vec<usize> = (0..n).collect();
    match std::env::var("GRAPHLORE_RESOLVE_ORDER").as_deref() {
        Ok("reverse") => order.reverse(),
        Ok(v) if v.starts_with("shuffle:") => {
            let seed: u64 = v["shuffle:".len()..].parse().unwrap_or(1);
            let key = |i: usize| {
                let mut h = seed ^ 0x9e37_79b9_7f4a_7c15;
                h ^= i as u64;
                h = h.wrapping_mul(0xbf58_476d_1ce4_e5b9);
                h ^ (h >> 31)
            };
            order.sort_by_key(|&i| key(i));
        }
        _ => {}
    }
    order
}

/// Resident memory now, in MB, on stderr with `what` (a measurement:
/// `GRAPHLORE_TRACE_STATS=1`; Linux only).
fn rss(what: &str) {
    if !std::env::var("GRAPHLORE_TRACE_STATS").is_ok_and(|v| v == "1") {
        return;
    }
    let status = std::fs::read_to_string("/proc/self/status").unwrap_or_default();
    let kb = status
        .lines()
        .find_map(|l| l.strip_prefix("VmRSS:"))
        .and_then(|v| v.trim().trim_end_matches("kB").trim().parse::<u64>().ok())
        .unwrap_or(0);
    eprintln!("rss {what}: {} MB", kb / 1024);
}

/// Sizes of what each file's resolution read, on stderr (a measurement).
fn trace_stats(per_file: &[FileOut]) {
    let mut files: Vec<usize> = per_file.iter().map(|o| o.frame.files.len()).collect();
    let mut keys: Vec<usize> = per_file.iter().map(|o| o.frame.keys.len()).collect();
    let all = per_file.iter().filter(|o| o.frame.all).count();
    files.sort_unstable();
    keys.sort_unstable();
    let q =
        |v: &[usize], x: f64| v.get(((v.len() as f64 - 1.0) * x) as usize).copied().unwrap_or(0);
    eprintln!(
        "trace: {} files; files read p50 {} p90 {} p99 {} max {}; keys p50 {} p90 {} p99 {} max {}; reading all {}",
        per_file.len(),
        q(&files, 0.5),
        q(&files, 0.9),
        q(&files, 0.99),
        files.last().unwrap_or(&0),
        q(&keys, 0.5),
        q(&keys, 0.9),
        q(&keys, 0.99),
        keys.last().unwrap_or(&0),
        all
    );
}

/// What incremental resolution is given (`reuse.rs`): the hash of each
/// file's content, the last build's cache, and the key of what every file's
/// resolution depends on beside what it reads. With no key, nothing is
/// reused and no cache is made.
#[derive(Default)]
pub struct Incremental<'a> {
    pub content: &'a [u64],
    pub previous: Option<&'a reuse::Stored>,
    pub key: Option<u64>,
}

impl FileOut {
    fn from_stored(s: &reuse::StoredFile) -> FileOut {
        let edges = s
            .edges
            .iter()
            .map(|e| EdgeOut {
                source: e.source.clone(),
                target: e.target.clone(),
                relation: e.relation,
                confidence: if e.inferred { Confidence::Inferred } else { Confidence::Extracted },
                file: e.file as usize,
                line: e.line,
                context: e.context,
                runs: e.runs,
            })
            .collect();
        let orm = s
            .orm
            .iter()
            .map(|(t, src, f, line, w)| (t.clone(), src.clone(), *f as usize, *line, *w))
            .collect();
        let ext = s
            .ext
            .iter()
            .map(|(id, label, eco)| (id.clone(), label.clone(), crate::cache::intern(eco)))
            .collect();
        let frame = trace::Frame {
            files: s.files.clone().into_boxed_slice(),
            keys: s.keys.clone().into_boxed_slice(),
            all: s.all,
            used: s.used.clone().into_boxed_slice(),
            parts: Box::new([]),
        };
        FileOut {
            edges,
            left: Vec::new(),
            orm,
            ext,
            stats: ResolveStats { unresolved: s.unresolved, ..Default::default() },
            frame: Rc::new(frame),
        }
    }

    fn to_stored(&self) -> reuse::StoredFile {
        reuse::StoredFile {
            edges: self
                .edges
                .iter()
                .map(|e| reuse::StoredEdge {
                    source: e.source.clone(),
                    target: e.target.clone(),
                    relation: e.relation,
                    inferred: e.confidence == Confidence::Inferred,
                    file: e.file as u32,
                    line: e.line,
                    context: e.context,
                    runs: e.runs,
                })
                .collect(),
            orm: self
                .orm
                .iter()
                .map(|(t, src, f, line, w)| (t.clone(), src.clone(), *f as u32, *line, *w))
                .collect(),
            ext: self.ext.iter().map(|(a, b, c)| (a.clone(), b.clone(), c.to_string())).collect(),
            unresolved: self.stats.unresolved,
            files: self.frame.files.to_vec(),
            keys: self.frame.keys.to_vec(),
            all: self.frame.all,
            used: self.frame.used.to_vec(),
        }
    }
}

/// What `resolve` gives the build.
pub struct Resolution {
    /// The external, resource and route nodes.
    pub nodes: Vec<Value>,
    pub links: crate::graph::links::Resolved,
    pub stats: BTreeMap<String, ResolveStats>,
    /// The calls left without an edge (`BuildOptions::unresolved`).
    pub unresolved: Vec<Value>,
    /// Milliseconds of `resolve.index`, `resolve.main`, `resolve.post`.
    pub phases: Vec<(&'static str, u128)>,
    /// The cache for the next update (`Incremental::key` given), and how
    /// many files' resolution was taken from the last one.
    pub stored: Option<reuse::Stored>,
    pub reused: usize,
}

/// Every edge the files' facts prove, plus the external nodes they point at.
pub fn resolve(
    root: &Path,
    files: &[FileFacts],
    asm: &mut Assembled,
    manifests: Vec<(String, PathBuf)>,
    repos: &[String],
    diagnose: bool,
    incremental: Incremental<'_>,
) -> Resolution {
    // milliseconds of its own phases: the indexes, the main loop, the rest
    let started = std::time::Instant::now();
    let mut timings: Vec<(&'static str, u128)> = Vec::new();
    let mut edges: Vec<EdgeOut> = Vec::new();
    let mut stored: Option<reuse::Stored> = None;
    let mut reused = 0;
    // calls left without an edge whose name is one definition of the project
    let mut left: Vec<Value> = Vec::new();
    let mut stats: BTreeMap<String, ResolveStats> = BTreeMap::new();
    let mut ext_nodes: Vec<(String, String, &'static str)> = Vec::new();
    let mut resource_nodes: Vec<(String, String, ResourceKind)> = Vec::new();
    let route_nodes: Vec<http::RouteNode>;
    {
        rss("before the indexes");
        let p = Project::new(root, files, asm, manifests, repos);
        rss("indexes");
        // what can be taken from the last build: a file whose reads are all
        // as they were (`reuse.rs`); never when the calls left unresolved
        // are asked for, which the cache does not keep
        let prints: Vec<u64> = match incremental.key {
            Some(_) => (0..files.len())
                .map(|f| p.file_print(f, incremental.content.get(f).copied().unwrap_or(0)))
                .collect(),
            None => Vec::new(),
        };
        let key_prints = if incremental.key.is_some() { p.key_prints() } else { HashMap::new() };
        let reuse_ok: Vec<bool> = match incremental.previous {
            Some(prev) if !diagnose => reuse::reusable(prev, &prints, &key_prints),
            _ => vec![false; files.len()],
        };
        rss("prints");
        timings.push(("resolve.index", started.elapsed().as_millis()));
        let mut ext_seen: HashSet<String> = HashSet::new();
        // an external node, in the order first seen: one file's, then all
        let ext_in = |seen: &mut Vec<(String, String, &'static str)>,
                      e: usize,
                      rest: &[String],
                      symbol: bool| {
            let (id, label, eco) = p.ext_node_id(e, rest, symbol);
            if !seen.iter().any(|s| s.0 == id) {
                seen.push((id.clone(), label, eco));
            }
            id
        };
        let mut ext_target_id = |node: (String, String, &'static str)| {
            if ext_seen.insert(node.0.clone()) {
                ext_nodes.push(node);
            }
        };
        // each file's references resolved on their own, and their results
        // put together in file order (so that the order they are resolved
        // in leaves the graph as it is)
        let mut per_file: Vec<FileOut> = (0..files.len()).map(|_| FileOut::default()).collect();
        let order = resolve_order(files.len());
        let stats_on = std::env::var("GRAPHLORE_TRACE_STATS").is_ok_and(|v| v == "1");
        trace::PROFILE.store(stats_on, std::sync::atomic::Ordering::Relaxed);
        trace::ENABLED.store(
            !std::env::var("GRAPHLORE_TRACE").is_ok_and(|v| v == "0"),
            std::sync::atomic::Ordering::Relaxed,
        );
        for (n, f) in order.into_iter().enumerate() {
            p.forget();
            if n % 500 == 0 {
                rss(&format!("file {n}"));
            }
            let facts = &files[f];
            let out = &mut per_file[f];
            let Some(fam) = p.family[f] else { continue };
            if reuse_ok[f] {
                if let Some(Some(stored)) = incremental.previous.map(|s| &s.files[f]) {
                    *out = FileOut::from_stored(stored);
                    // (the import uses it noted, which the imports' edges read)
                    let mut used = p.used.borrow_mut();
                    for &(a, b, c) in &stored.used {
                        used.entry((a as usize, b as usize)).or_default().insert(c as usize);
                    }
                    reused += 1;
                    continue;
                }
            }
            p.trace.open();
            p.trace.file(f);
            let st = &mut out.stats;
            for (ri, r) in facts.refs.iter().enumerate() {
                let src = p.source_of(f, r);
                // a data model's table, read or written through the ORM
                if let Some((table, write)) = p.orm_use(f, r) {
                    out.orm.push((table, src.clone(), f, r.line, write));
                }
                let heritage = matches!(
                    r.kind,
                    RefKind::Inherits
                        | RefKind::Implements
                        | RefKind::Base
                        | RefKind::MixesIn
                        | RefKind::Embeds
                );
                let scope_def = if heritage {
                    r.from_def.and_then(|d| facts.defs[d].parent)
                } else {
                    r.from_def
                };
                // attribute reads, values and bare Ruby names never fall back
                // to a name guess
                let state = matches!(r.kind, RefKind::Read | RefKind::Write);
                let strict = heritage
                    || state
                    || matches!(r.kind, RefKind::Attr)
                    || r.context == "implicit_call";
                // a bare Ruby name that is a local is the local, not a call
                // (typed or not: `s = stubs; s.match(x)` calls nothing at `s`)
                // (only once the local exists: in Ruby the first assignment
                // the parser meets makes it, so a use above it is a call)
                if r.context == "implicit_call"
                    && r.path.len() == 1
                    && p.is_local_name(f, scope_def, &r.path[0])
                    && !p.ruby_local_starts_after(f, scope_def, &r.path[0], r.byte)
                {
                    continue;
                }
                // (`typeof x` in a type names the value `x`: a value meaning)
                *p.types_only.borrow_mut() =
                    heritage || (r.kind == RefKind::Type && r.context != "type_query");
                *p.calling.borrow_mut() = matches!(r.kind, RefKind::Call | RefKind::New);
                *p.recv.borrow_mut() = r.recv.map(|i| (f, i));
                *p.args.borrow_mut() = r.args;
                *p.self_arg.borrow_mut() = r.self_arg;
                let is_call = matches!(r.kind, RefKind::Call | RefKind::New);
                *p.cur.borrow_mut() = is_call.then_some((f, ri));
                *p.site.borrow_mut() = Some((f, r.byte));
                *p.ref_kind.borrow_mut() = Some(r.kind);
                p.guessed.set(false);
                p.embedded_hit.set(None);
                let found = if let Some(place) = &r.implicit {
                    // Swift `.status(301)`: a static member of the type the
                    // place it stands in asks for
                    let saved = p.suspend();
                    let found = p.implicit_member(f, scope_def, r, place);
                    p.restore(saved);
                    found
                } else if r.from_owner.is_some() && heritage {
                    p.resolve_path(f, None, &r.path, true)
                } else if fam == Family::CSharp && r.kind == RefKind::Decorator {
                    p.csharp_attribute(f, scope_def, &r.path, strict)
                } else if fam == Family::CSharp
                    && r.path.last().is_some_and(|s| s.starts_with("operator checked "))
                {
                    // a `checked` context takes the checked operator, else the
                    // plain one (the standard's 12.8.20)
                    p.resolve_path(f, scope_def, &r.path, strict).or_else(|| {
                        let mut plain = r.path.clone();
                        if let Some(last) = plain.last_mut() {
                            *last = format!("operator{}", &last["operator checked ".len()..]);
                        }
                        p.resolve_path(f, scope_def, &plain, strict)
                    })
                } else {
                    p.resolve_path(f, scope_def, &r.path, strict)
                };
                *p.types_only.borrow_mut() = false;
                *p.calling.borrow_mut() = false;
                *p.recv.borrow_mut() = None;
                // which of several overloads: the one the arguments are for
                let mut tied: Vec<DefRef> = Vec::new();
                let found = match found {
                    Some((Target::Def(m), conf))
                        if is_call
                            && p.def(m).kind.is_callable()
                            && r.path.last().is_some_and(|s| s != "()") =>
                    {
                        *p.recv.borrow_mut() = r.recv.map(|i| (f, i));
                        let picked = p.pick_overload(f, scope_def, m, r);
                        *p.recv.borrow_mut() = None;
                        // one of several that fit alike: each is a guess
                        let conf = if picked.len() > 1 { Confidence::Inferred } else { conf };
                        tied = picked[1..].to_vec();
                        Some((Target::Def(picked[0]), conf))
                    }
                    other => other,
                };
                // `io.Copy(&f.basicWriter, r)`: the embedded field itself,
                // no use of its type
                if let Some(e) = p.embedded_hit.take() {
                    if matches!(&found, Some((Target::Def(t), _)) if *t == e)
                        && matches!(r.kind, RefKind::Read | RefKind::Attr)
                    {
                        *p.args.borrow_mut() = None;
                        *p.self_arg.borrow_mut() = false;
                        *p.cur.borrow_mut() = None;
                        *p.site.borrow_mut() = None;
                        *p.ref_kind.borrow_mut() = None;
                        continue;
                    }
                }
                *p.args.borrow_mut() = None;
                *p.self_arg.borrow_mut() = false;
                *p.cur.borrow_mut() = None;
                *p.site.borrow_mut() = None;
                *p.ref_kind.borrow_mut() = None;
                // C# `Ensure.NotNull(x)`, `KnownHeaders.Accept`: the type a
                // static member is reached through is used too (as C and
                // C++'s scoped paths are)
                if fam == Family::CSharp
                    && r.path.len() >= 2
                    && matches!(
                        r.kind,
                        RefKind::Call | RefKind::Attr | RefKind::Read | RefKind::Write
                    )
                    && matches!(&found, Some((Target::Def(_), _)))
                    && !matches!(r.path[0].as_str(), "self" | "super" | "base" | "()")
                    && !p.is_local_name(f, scope_def, &r.path[0])
                {
                    if let Some((Target::Def(c), Confidence::Extracted)) =
                        p.resolve_path(f, scope_def, &r.path[..1], true)
                    {
                        // (written with no type argument: of `Maybe` and
                        // `Maybe<T>`, the one that takes none)
                        let c = if p.def(c).type_params.is_empty() {
                            c
                        } else {
                            p.fqn_of(c.0, c.1)
                                .and_then(|fq| {
                                    p.fqn.get(&fq)?.iter().copied().find(|o| {
                                        p.def(*o).kind.is_class_like()
                                            && p.def(*o).type_params.is_empty()
                                    })
                                })
                                .unwrap_or(c)
                        };
                        if p.def(c).kind.is_class_like() {
                            if let Some(id) = p.node_id(c).filter(|id| *id != src) {
                                out.edges.push(EdgeOut {
                                    source: src.clone(),
                                    target: id.to_string(),
                                    relation: "references",
                                    confidence: Confidence::Extracted,
                                    file: f,
                                    line: r.line,
                                    context: "static",
                                    runs: None,
                                });
                            }
                        }
                    }
                }
                // a guess by name alone on another object never links a
                // definition to itself: `base.evaluate()` inside `evaluate()`
                // is a wrapper handing on to what it wraps
                if p.guessed.get() && tied.is_empty() && r.path.len() >= 2 {
                    if let Some((Target::Def(t), Confidence::Inferred)) = &found {
                        if t.0 == f && r.from_def == Some(t.1) {
                            continue;
                        }
                    }
                }
                // a Kotlin operator (`a + b`) is the operator of a's type, never
                // a guess by its name (`plus` of some other type)
                if (matches!(r.context, "operator" | "language_call")
                    || r.context.starts_with("indexer"))
                    && !matches!(
                        &found,
                        Some((Target::Def(t), Confidence::Extracted)) if p.def(*t).kind.is_callable()
                    )
                {
                    continue;
                }
                // a local called (`handler(x)`, `builder::add`) is the value
                // it holds being called, not its type being constructed
                if is_call && r.path.len() == 1 && p.is_local_name(f, scope_def, &r.path[0]) {
                    if let Some((Target::Def(t), _)) = &found {
                        if p.def(*t).kind.is_class_like() {
                            continue;
                        }
                    }
                }
                let built_type = match &found {
                    Some((Target::Def(t), _)) if p.def(*t).kind.is_class_like() => Some(*t),
                    _ => None,
                };
                // C#: a type is told from its namesakes by the number of type
                // arguments it is written with (`RestResponse` beside
                // `RestResponse<T>`); one written with arguments is never a
                // type that takes none (`List<string>` beside a class `List`)
                let found = match (found, r.arity) {
                    (Some((Target::Def(t), conf)), Some(want))
                        if fam == Family::CSharp && p.def(t).kind.is_class_like() =>
                    {
                        let want = usize::from(want);
                        let has = p.def(t).type_params.len();
                        let other = || {
                            let fq = p.fqn_of(t.0, t.1)?;
                            p.fqn.get(&fq)?.iter().copied().find(|o| {
                                p.def(*o).kind.is_class_like()
                                    && p.def(*o).type_params.len() == want
                            })
                        };
                        if has == want {
                            Some((Target::Def(t), conf))
                        } else if let Some(o) = other() {
                            Some((Target::Def(o), conf))
                        } else if want > 0 && has == 0 {
                            None
                        } else {
                            Some((Target::Def(t), conf))
                        }
                    }
                    (found, _) => found,
                };
                // Rust: `self.proxy` with no call is the field, beside a
                // method of its name (a method is never read without a call)
                let found = match found {
                    Some((Target::Def(t), conf))
                        if matches!(r.kind, RefKind::Attr | RefKind::Read | RefKind::Write)
                            && r.path.len() >= 2
                            && fam == Family::Rust
                            && p.def(t).kind.is_callable() =>
                    {
                        let name = p.def(t).name.clone();
                        let field = p.class_of(t.0, Some(t.1)).and_then(|c| {
                            p.members(c, &name)
                                .into_iter()
                                .find(|m| p.def(*m).kind == DefKind::Property)
                        });
                        Some((Target::Def(field.unwrap_or(t)), conf))
                    }
                    other => other,
                };
                let (target, relation, conf) = match found {
                    Some((Target::Def(t), conf)) => {
                        // `throw ParserError.noValue("x")`: a case built with
                        // what it carries is a use of its enum, and so is a
                        // case named as a value (`return Color.Red`)
                        let made = (matches!(r.kind, RefKind::Call | RefKind::New | RefKind::Attr)
                            && p.def(t).kind == DefKind::EnumMember)
                            .then(|| p.def(t).parent.map(|e| (t.0, e)))
                            .flatten()
                            .filter(|e| p.def(*e).kind.is_class_like());
                        let (t, kind) = match made {
                            Some(e) => (e, RefKind::Type),
                            None => (t, r.kind),
                        };
                        let Some(id) = p.node_id(t) else { continue };
                        let tk = p.def(t).kind;
                        let rel = match kind {
                            RefKind::Call | RefKind::New => {
                                // a C# indexer: read, written, or named by an
                                // object initializer
                                if r.context.starts_with("indexer") {
                                    if p.def(t).name != crate::refs::INDEXER {
                                        continue;
                                    }
                                    match r.context {
                                        "indexer=" => "writes",
                                        "indexer{}" => "references",
                                        _ => "reads",
                                    }
                                // a Lombok accessor called: the field it reads
                                // or writes
                                } else if tk.is_state() && r.kind == RefKind::Call {
                                    let called = r.path.last().map(String::as_str).unwrap_or("");
                                    // (Go `s.run()` on a field of function
                                    // type: the field is read)
                                    if fam == Family::Go && called == p.def(t).name {
                                        "reads"
                                    } else if called != p.def(t).name {
                                        if called.starts_with("set") {
                                            "writes"
                                        } else {
                                            "reads"
                                        }
                                    } else {
                                        continue;
                                    }
                                } else if !(tk.is_callable()
                                    || tk.is_class_like()
                                    || tk == DefKind::TypeAlias)
                                {
                                    // a type alias called: a conversion (Go
                                    // `HandlerFunc(f)`)
                                    continue;
                                } else if fam == Family::Go
                                    && r.kind == RefKind::Call
                                    && p.is_type_like(t)
                                {
                                    // Go: a type called is a conversion, a use
                                    // of the type (`T{}` is the construction)
                                    "references"
                                } else {
                                    "calls"
                                }
                            }
                            RefKind::Inherits => "inherits",
                            RefKind::Implements => "implements",
                            RefKind::Base => {
                                if tk == DefKind::Interface {
                                    "implements"
                                } else {
                                    "inherits"
                                }
                            }
                            RefKind::MixesIn => "mixes_in",
                            RefKind::Embeds => "embeds",
                            RefKind::Type => {
                                if !(tk.is_class_like() || tk == DefKind::TypeAlias) {
                                    continue;
                                }
                                "references"
                            }
                            RefKind::Decorator => "references",
                            // a member read on its own: state is read, a method
                            // taken as a value is referred to
                            RefKind::Attr if tk.is_state() => "reads",
                            // C++ `port::CondVar cv;`: a type named through
                            // its namespace (an enumerator named became a
                            // reference to its enum above)
                            RefKind::Attr
                                if tk.is_class_like()
                                    && p.family[f] == Some(Family::C)
                                    && !p
                                        .def(t)
                                        .parent
                                        .is_some_and(|e| p.def((t.0, e)).kind == DefKind::Enum) =>
                            {
                                "references"
                            }
                            // a type named as a value through what holds it
                            // (`take(MyClass.Nested)`, `Sealed.A -> …`)
                            // (a generic method's bare name, `Maybe.Nothing<T>()`,
                            // reaches here alone: no type named through anything)
                            RefKind::Attr
                                if tk.is_class_like()
                                    && conf == Confidence::Extracted
                                    && r.path.len() >= 2
                                    // (… which is the type or namespace it is
                                    // declared in, or a module: `Maybe.Nothing`
                                    // beside a class `Nothing` is the method)
                                    && p.def(t).parent.is_none_or(|up| {
                                        p.def((t.0, up)).name == r.path[r.path.len() - 2]
                                    }) =>
                            {
                                "references"
                            }
                            RefKind::Attr => {
                                if !tk.is_callable() {
                                    continue;
                                }
                                // Scala `r.size`: a method that takes nothing
                                // is called by naming it
                                let scala = p.files[f].lang == Some(crate::lang::Lang::Scala);
                                if scala && p.def(t).params.is_none_or(|n| n == 0) {
                                    "calls"
                                } else {
                                    "references"
                                }
                            }
                            // a function named as a value (`const k = helper`,
                            // `cb = helper`, `exports.f = helper`), where a bare
                            // function name is one
                            RefKind::Read
                                if conf == Confidence::Extracted
                                    && r.path.len() == 1
                                    && r.context != "object"
                                    && (tk.is_class_like()
                                        || (tk.is_callable()
                                            && matches!(
                                                p.family[f],
                                                Some(Family::Ecma | Family::Python | Family::Go)
                                            ))) =>
                            {
                                "references"
                            }
                            RefKind::Read | RefKind::Write => {
                                if !tk.is_state() {
                                    continue;
                                }
                                // a mutating method's name means a change only
                                // on a collection or a builder
                                if r.context == "mutate" && !p.def(t).container {
                                    continue;
                                }
                                // a constructor setting its own object's fields
                                // builds it; it couples nothing
                                if r.kind == RefKind::Write
                                    && tk == DefKind::Property
                                    && p.constructs(f, r.from_def)
                                {
                                    continue;
                                }
                                if r.kind == RefKind::Read {
                                    "reads"
                                } else {
                                    "writes"
                                }
                            }
                            RefKind::Indirect => {
                                if conf != Confidence::Extracted {
                                    continue;
                                }
                                if tk.is_callable() {
                                    // Ruby has no bare function values: a method
                                    // named as an argument is called there
                                    if p.family[f] == Some(Family::Ruby) {
                                        "calls"
                                    } else {
                                        "indirect_call"
                                    }
                                } else if tk.is_class_like() {
                                    "uses" // a type handed over as a value
                                } else {
                                    continue;
                                }
                            }
                        };
                        // a write known only from the method's name is a guess
                        let conf = if r.context == "mutate" {
                            conf.max(Confidence::Inferred)
                        } else {
                            conf
                        };
                        (id.to_string(), rel, conf)
                    }
                    Some((Target::Ext(e, rest), conf)) => match r.kind {
                        RefKind::Call | RefKind::New => {
                            (ext_in(&mut out.ext, e, &rest, false), "calls", conf)
                        }
                        RefKind::Decorator => {
                            (ext_in(&mut out.ext, e, &rest, true), "references", conf)
                        }
                        RefKind::Type
                        | RefKind::Indirect
                        | RefKind::Attr
                        | RefKind::Read
                        | RefKind::Write => continue,
                        k => (
                            ext_in(&mut out.ext, e, &rest, true),
                            external_base(k, fam, &rest),
                            conf,
                        ),
                    },
                    Some(_) => continue,
                    None => {
                        if heritage || r.kind == RefKind::Decorator {
                            // a base or decorator the project does not define
                            let label = r.path.join(if fam == Family::Rust { "::" } else { "." });
                            let e = p.ext(&label, fam.ecosystem());
                            let id = ext_in(&mut out.ext, e, &[], false);
                            let rel = if r.kind == RefKind::Decorator {
                                "references"
                            } else {
                                external_base(r.kind, fam, &r.path)
                            };
                            (id, rel, Confidence::Extracted)
                        } else {
                            // `Model.validate(x)`: the method comes from a base
                            // the project does not define; the use is still of
                            // `Model`
                            let head_type = (r.kind == RefKind::Call
                                && r.path.len() >= 2
                                && !matches!(r.path[0].as_str(), "self" | "super" | "()")
                                && !p.is_local_name(f, scope_def, &r.path[0]))
                            .then(|| p.resolve_path(f, scope_def, &r.path[..1], true))
                            .flatten();
                            if diagnose && is_call {
                                out.left.extend(p.explain_unresolved(f, scope_def, r));
                            }
                            if let Some((Target::Def(t), conf)) = head_type {
                                if p.def(t).kind.is_class_like() {
                                    if let Some(id) = p.node_id(t) {
                                        if id != src {
                                            out.edges.push(EdgeOut {
                                                source: src,
                                                target: id.to_string(),
                                                relation: "references",
                                                confidence: conf,
                                                file: f,
                                                line: r.line,
                                                context: "call",
                                                runs: None,
                                            });
                                        }
                                        continue;
                                    }
                                }
                            }
                            // a name that is no state is most of what is read
                            if !matches!(r.kind, RefKind::Indirect | RefKind::Read | RefKind::Write)
                            {
                                st.unresolved += 1;
                            }
                            continue;
                        }
                    }
                };
                if target == src && relation != "calls" {
                    continue;
                }
                // constructing a type runs the constructor it declares
                let built = match built_type {
                    Some(t) if relation == "calls" => p.constructors(t),
                    _ => Vec::new(),
                };
                // of several overloads, the one that takes as many arguments
                // as the site passes; when that names none or several, no
                // constructor is named (the type still is). A Kotlin class has
                // a primary constructor that is no definition: there the
                // overload that fits is a guess
                // A Kotlin class's primary constructor is written in its
                // header and is no definition: its parameters are the
                // class's, and it competes with the secondary ones. When it
                // is the one called, the type is all there is to name; when
                // the class writes none (or the site does not decide), a
                // secondary constructor that fits is a guess
                let kotlin = facts.lang == Some(Lang::Kotlin);
                let primary = built_type
                    .filter(|t| kotlin && !built.is_empty() && p.def(*t).params.is_some());
                *p.recv.borrow_mut() = r.recv.map(|i| (f, i));
                let scored: Vec<(DefRef, i32)> = built
                    .iter()
                    .chain(primary.iter())
                    .filter_map(|c| p.fit(f, scope_def, *c, r).map(|s| (*c, s)))
                    .collect();
                *p.recv.borrow_mut() = None;
                let fits = p.exact_first(p.top_fits(&scored), r);
                let one = if built.len() == 1 && !kotlin {
                    built.first()
                } else if fits.len() == 1 && (r.args.is_some() || built.len() == 1) {
                    fits.first().filter(|c| Some(**c) != primary)
                } else {
                    None
                };
                let sure = !kotlin || primary.is_some();
                let picked = one.and_then(|c| p.node_id(*c));
                if let Some(id) = picked.filter(|id| *id != src) {
                    out.edges.push(EdgeOut {
                        source: src.clone(),
                        target: id.to_string(),
                        relation: "calls",
                        confidence: if sure { conf } else { Confidence::Inferred },
                        file: f,
                        line: r.line,
                        context: "constructor",
                        runs: None,
                    });
                }
                // the other overloads that fit the call as well as this one
                if relation == "calls" {
                    for other in &tied {
                        let Some(id) = p.node_id(*other) else { continue };
                        if id != target {
                            out.edges.push(EdgeOut {
                                source: src.clone(),
                                target: id.to_string(),
                                relation,
                                confidence: Confidence::Inferred,
                                file: f,
                                line: r.line,
                                context: r.context,
                                runs: None,
                            });
                        }
                    }
                }
                out.edges.push(EdgeOut {
                    source: src,
                    target,
                    relation,
                    confidence: conf,
                    file: f,
                    line: r.line,
                    context: if r.kind == RefKind::Decorator {
                        "decorator"
                    } else if matches!(r.kind, RefKind::Read | RefKind::Attr)
                        && relation == "references"
                    {
                        "value"
                    } else {
                        r.context
                    },
                    runs: None,
                });
            }
            out.frame = p.trace.close_file();
        }
        rss("main loop");
        // the passes after the main loop start from no file's cached work
        // and no reference's state: which file was resolved last differs
        // between an update and a full build
        p.forget();
        p.neutral();
        if std::env::var("GRAPHLORE_TRACE_STATS").is_ok_and(|v| v == "1") {
            trace_stats(&per_file);
            let mut scans: Vec<(u32, usize)> =
                p.trace.scans.borrow().iter().map(|(k, v)| (*k, *v)).collect();
            scans.sort_by_key(|x| std::cmp::Reverse(x.1));
            eprintln!("trace: scans at line (0: unbalanced) {scans:?}");
            let mut sites: Vec<((&str, u32), usize)> =
                p.trace.sites.borrow().iter().map(|(k, v)| (*k, *v)).collect();
            sites.sort_by_key(|x| std::cmp::Reverse(x.1));
            sites.truncate(25);
            eprintln!("trace: reads by site {sites:?}");
            eprintln!(
                "trace: frames closed, empty, own files, own keys, parts {:?}",
                p.trace.counts.borrow()
            );
            eprintln!(
                "trace: kept as empty, its part, shared small, new small, new large, large entries {:?}",
                p.trace.kept.borrow()
            );
        }
        if let Some(key) = incremental.key {
            let files_out: Vec<Option<reuse::StoredFile>> = per_file
                .iter()
                .enumerate()
                .map(|(f, out)| p.family[f].map(|_| out.to_stored()))
                .collect();
            let asked: HashSet<u64> =
                files_out.iter().flatten().flat_map(|s| s.keys.iter().copied()).collect();
            let keys = asked
                .into_iter()
                .map(|k| (k, key_prints.get(&k).copied().unwrap_or(reuse::ABSENT)))
                .collect();
            stored = Some(reuse::Stored { key, files: files_out, prints: prints.clone(), keys });
            rss("cache made");
        }
        let mut orm_uses: Vec<(String, String, usize, u32, bool)> = Vec::new();
        for (f, out) in per_file.into_iter().enumerate() {
            edges.extend(out.edges);
            left.extend(out.left);
            orm_uses.extend(out.orm);
            for id in out.ext {
                ext_target_id(id);
            }
            if let Some(fam) = p.family[f] {
                let st = stats.entry(fam.name().to_string()).or_default();
                st.unresolved += out.stats.unresolved;
            }
        }
        timings.push(("resolve.main", started.elapsed().as_millis()));
        // a name read as a value says nothing more where the same definition
        // already calls, builds or extends what it names (`new Foo()`, `class
        // A < B`)
        {
            let stronger: HashSet<(String, String)> = edges
                .iter()
                .filter(|e| {
                    matches!(
                        e.relation,
                        "calls"
                            | "indirect_call"
                            | "inherits"
                            | "implements"
                            | "mixes_in"
                            | "embeds"
                    )
                })
                .map(|e| (e.source.clone(), e.target.clone()))
                .collect();
            edges.retain(|e| {
                !(e.context == "value"
                    && e.relation == "references"
                    && stronger.contains(&(e.source.clone(), e.target.clone())))
            });
        }
        // C, C++: a function put in a slot of a struct is what a call through
        // the slot reaches (`dispatches_to`, an interface method to its
        // implementations)
        for (f, facts) in files.iter().enumerate() {
            for s in &facts.slots {
                let Some(slot) = p.slot_of(f, s) else { continue };
                let Some(Target::Def(v)) = p.resolve_name(f, s.from_def, &s.value) else {
                    continue;
                };
                if !p.def(v).kind.is_callable() {
                    continue;
                }
                let (Some(from), Some(to)) = (p.node_id(slot), p.node_id(v)) else { continue };
                if from != to {
                    edges.push(EdgeOut {
                        source: from.to_string(),
                        target: to.to_string(),
                        relation: "dispatches_to",
                        confidence: Confidence::Extracted,
                        file: f,
                        line: s.line,
                        context: "slot",
                        runs: None,
                    });
                }
            }
        }
        // a method written for a method of a base, an interface or a trait is
        // what a call of that method reaches, and no call names it
        for (base, m) in p.dispatch_pairs() {
            let node =
                |r: DefRef| p.node_id(r).or_else(|| p.definition_of(r).and_then(|d| p.node_id(d)));
            let (Some(from), Some(to)) = (node(base), node(m)) else { continue };
            if from != to {
                edges.push(EdgeOut {
                    source: from.to_string(),
                    target: to.to_string(),
                    relation: "dispatches_to",
                    confidence: Confidence::Extracted,
                    file: m.0,
                    line: p.def(m).def_line,
                    context: "override",
                    runs: None,
                });
            }
        }
        // resources: one node per (kind, key) that two definitions or more
        // touch; a file the project holds is its own node
        // (source, file, line, writes, context, certain)
        type Use = (String, usize, u32, bool, &'static str, bool);
        let mut groups: BTreeMap<(ResourceKind, String), Vec<Use>> = BTreeMap::new();
        for (f, facts) in files.iter().enumerate() {
            for u in &facts.resources {
                let probe = RefFact {
                    line: u.line,
                    kind: RefKind::Read,
                    path: Vec::new(),
                    context: "",
                    from_def: u.from_def,
                    from_owner: None,
                    args: None,
                    self_arg: false,
                    hints: Vec::new(),
                    implicit: None,
                    targs: 0,
                    arity: None,
                    this_of: None,
                    recv: None,
                    byte: u.byte,
                };
                let src = p.source_of(f, &probe);
                let entry = (src, f, u.line, u.write, u.context, u.certain);
                groups.entry((u.kind, u.key.clone())).or_default().push(entry);
            }
        }
        for (table, src, f, line, write) in orm_uses {
            let entry = (src, f, line, write, "orm", false);
            groups.entry((ResourceKind::Table, table)).or_default().push(entry);
        }
        let by_path: HashMap<&str, usize> =
            files.iter().enumerate().map(|(i, x)| (x.path.as_str(), i)).collect();
        for ((kind, key), uses) in groups {
            let touchers: HashSet<&str> = uses.iter().map(|u| u.0.as_str()).collect();
            let st = stats.entry("resources".into()).or_default();
            // an event couples code only when the project both emits and
            // listens for it
            let one_sided = kind == ResourceKind::Event
                && (uses.iter().all(|u| u.3) || uses.iter().all(|u| !u.3));
            if touchers.len() < 2 || one_sided {
                st.unresolved += 1; // touched once, or from one side: no coupling to show
                continue;
            }
            let target = if kind == ResourceKind::File {
                let k = key.trim_start_matches("./");
                by_path.get(k).map(|&i| p.hubs[i].clone())
            } else {
                None
            };
            let target = target.unwrap_or_else(|| {
                let id = format!("res_{}_{}", kind.as_str(), slug(&key));
                resource_nodes.push((id.clone(), key.clone(), kind));
                id
            });
            for (src, f, line, write, context, certain) in uses {
                edges.push(EdgeOut {
                    source: src,
                    target: target.clone(),
                    relation: if write { "writes" } else { "reads" },
                    confidence: if certain { Confidence::Extracted } else { Confidence::Inferred },
                    file: f,
                    line,
                    context,
                    runs: None,
                });
            }
        }
        // routes, joined across files, and their handlers
        route_nodes = http::compose(&p, files);
        for (r, h) in route_nodes.iter().flat_map(|r| r.handlers.iter().map(move |h| (r, h))) {
            edges.push(EdgeOut {
                source: r.id.clone(),
                target: h.clone(),
                relation: "handles",
                confidence: Confidence::Extracted,
                file: r.file,
                line: r.line,
                context: "route",
                runs: None,
            });
        }
        // requests, to the routes they reach
        let (links, http) = http::link_calls(&p, files, &route_nodes);
        for l in links {
            edges.push(EdgeOut {
                source: l.source,
                target: l.target,
                relation: "http_calls",
                confidence: l.confidence,
                file: l.file,
                line: l.line,
                context: "http",
                runs: None,
            });
        }
        let st = stats.entry("http".into()).or_default();
        for (k, v) in [
            ("client_calls", http.calls),
            ("linked", http.linked),
            ("unlinked", http.unlinked),
            ("test_calls", http.test_calls),
            ("external", http.external),
            ("dynamic", http.dynamic),
        ] {
            st.edges.insert(k.into(), v);
        }
        // imports, once the uses have said which files of a package each one
        // reaches
        for (f, facts) in files.iter().enumerate() {
            let Some(fam) = p.family[f] else { continue };
            for (i, imp) in facts.imports.iter().enumerate() {
                let res = &p.scopes[f].modules[i];
                let used = p.used.borrow().get(&(f, i)).cloned().unwrap_or_default();
                let out = modules::import_edges(&p, fam, f, imp, res, &used);
                if out.is_empty() && matches!(res, ModRes::Missing) {
                    stats.entry(fam.name().to_string()).or_default().unresolved_imports += 1;
                }
                for (target, relation, context) in out {
                    let id = match target {
                        Target::Def(t) => match p.node_id(t) {
                            Some(id) => id.to_string(),
                            None => continue,
                        },
                        Target::Module(fs) => {
                            for g in fs {
                                if g != f {
                                    edges.push(EdgeOut {
                                        source: p.hubs[f].clone(),
                                        target: p.hubs[g].clone(),
                                        relation,
                                        confidence: Confidence::Extracted,
                                        file: f,
                                        line: imp.line,
                                        context,
                                        runs: Some(imp.runs),
                                    });
                                }
                            }
                            continue;
                        }
                        Target::Ext(e, rest) => {
                            let (id, label, eco) = p.ext_node_id(e, &rest, true);
                            ext_target_id((id.clone(), label, eco));
                            id
                        }
                        _ => continue,
                    };
                    edges.push(EdgeOut {
                        source: p.hubs[f].clone(),
                        target: id,
                        relation,
                        confidence: Confidence::Extracted,
                        file: f,
                        line: imp.line,
                        context,
                        runs: Some(imp.runs),
                    });
                }
            }
        }
    }
    // one edge per (source, target, relation): the first site, the earliest
    // time an import runs, the surest confidence
    let mut seen: HashMap<(String, String, &'static str), usize> = HashMap::new();
    let mut kept: Vec<EdgeOut> = Vec::new();
    for e in edges {
        let key = (e.source.clone(), e.target.clone(), e.relation);
        match seen.get(&key) {
            Some(&i) => {
                let k = &mut kept[i];
                k.confidence = k.confidence.min(e.confidence);
                if let (Some(a), Some(b)) = (k.runs, e.runs) {
                    k.runs = Some(a.min(b));
                }
            }
            None => {
                seen.insert(key, kept.len());
                kept.push(e);
            }
        }
    }
    for e in &kept {
        let fam = files[e.file].lang.map(Family::of).map(|f| f.name()).unwrap_or("?");
        let st = stats.entry(fam.to_string()).or_default();
        *st.edges.entry(e.relation.to_string()).or_default() += 1;
        if e.confidence == Confidence::Inferred {
            st.inferred += 1;
        }
    }
    // external ids are claimed last; one that a project node already holds
    // gets a suffix, and its edges follow
    let mut renamed: HashMap<String, String> = HashMap::new();
    resource_nodes.sort();
    resource_nodes.dedup();
    let mut nodes: Vec<Value> = resource_nodes
        .into_iter()
        .map(|(id, key, kind)| {
            let claimed = asm.ids.claim(id.clone());
            if claimed != id {
                renamed.insert(id, claimed.clone());
            }
            json!({
                "id": claimed,
                "label": key,
                "norm_label": key.to_lowercase(),
                "file_type": "concept",
                "type": "resource",
                "resource": kind.as_str(),
                "key": key,
                "source_file": "",
            })
        })
        .collect();
    let ext: Vec<Value> = ext_nodes
        .into_iter()
        .map(|(id, label, eco)| {
            let claimed = asm.ids.claim(id.clone());
            if claimed != id {
                renamed.insert(id, claimed.clone());
            }
            let id = claimed;
            json!({
                "id": id,
                "label": label,
                "norm_label": label.to_lowercase(),
                "file_type": "concept",
                "type": "external",
                "external": true,
                "source_file": "",
                "ecosystem": eco,
            })
        })
        .collect();
    nodes.extend(ext);
    for r in route_nodes {
        let claimed = asm.ids.claim(r.id.clone());
        if claimed != r.id {
            renamed.insert(r.id.clone(), claimed.clone());
        }
        let mut v = json!({
            "id": claimed,
            "label": r.label,
            "norm_label": r.label.to_lowercase(),
            "file_type": "route",
            "type": "route",
            "method": r.method,
            "pattern": r.pattern,
            "key": r.key,
            "framework": r.framework,
            "source_file": files[r.file].path,
            "source_location": format!("L{}", r.line),
        });
        if r.unresolved {
            v["prefix_unresolved"] = json!(true);
        }
        nodes.push(v);
    }
    let rename = |id: String| renamed.get(&id).cloned().unwrap_or(id);
    let edges = kept
        .into_iter()
        .map(|e| crate::graph::links::TypedEdge {
            source: rename(e.source),
            target: rename(e.target),
            relation: e.relation,
            confidence: e.confidence.label(),
            confidence_score: e.confidence.score(),
            file: e.file,
            line: e.line,
            context: e.context,
            runs: e.runs.map(Runs::as_str),
        })
        .collect();
    let links = crate::graph::links::Resolved {
        edges,
        paths: files.iter().map(|f| f.path.clone()).collect(),
    };
    // each as the time it took
    timings.push(("resolve.post", started.elapsed().as_millis()));
    for i in (1..timings.len()).rev() {
        timings[i].1 -= timings[i - 1].1;
    }
    Resolution { nodes, links, stats, unresolved: left, phases: timings, stored, reused }
}

/// ORM methods that read rows of a model's table, and that write them.
const ORM_READS: &[&str] = &[
    "get",
    "filter",
    "filter_by",
    "all",
    "first",
    "last",
    "find",
    "find_by",
    "findOne",
    "findById",
    "findAll",
    "findBy",
    "findMany",
    "findUnique",
    "findFirst",
    "where",
    "select",
    "count",
    "exists",
    "aggregate",
    "exclude",
    "order_by",
    "values",
    "pluck",
    "paginate",
    "Find",
    "FindAsync",
    "Where",
    "FirstOrDefault",
    "FirstOrDefaultAsync",
    "SingleOrDefault",
    "SingleOrDefaultAsync",
    "Any",
    "AnyAsync",
    "ToList",
    "ToListAsync",
    "CountAsync",
    "findAndCountAll",
    "fetch",
];
const ORM_WRITES: &[&str] = &[
    "create",
    "save",
    "update",
    "delete",
    "destroy",
    "insert",
    "update_or_create",
    "get_or_create",
    "bulk_create",
    "bulk_update",
    "persist",
    "remove",
    "upsert",
    "increment",
    "decrement",
    "updateOne",
    "updateMany",
    "deleteOne",
    "deleteMany",
    "insertMany",
    "findOneAndUpdate",
    "findByIdAndUpdate",
    "findOneAndDelete",
    "findByIdAndDelete",
    "Add",
    "AddAsync",
    "AddRange",
    "Remove",
    "RemoveRange",
    "Update",
    "UpdateRange",
    "Attach",
    "save!",
    "create!",
    "update!",
    "destroy!",
    "delete_all",
    "update_all",
    "insert_all",
    "upsert_all",
    "forceDelete",
    "truncate",
];

/// Names a type's constructor-like functions go by.
fn is_factory(name: &str) -> bool {
    matches!(
        name,
        "new"
            | "create"
            | "of"
            | "from"
            | "build"
            | "builder"
            | "default"
            | "instance"
            | "getInstance"
            | "get_instance"
            | "shared"
            | "init"
            | "make"
            | "open"
            | "parse"
    ) || name.starts_with("new_")
        || name.starts_with("New")
        || name.starts_with("from_")
        || name.starts_with("with_")
        || name.starts_with("create")
}

/// The relation to an external base: C# names its interfaces `IThing`.
fn external_base(kind: RefKind, fam: Family, path: &[String]) -> &'static str {
    match kind {
        RefKind::Implements => "implements",
        RefKind::MixesIn => "mixes_in",
        RefKind::Embeds => "embeds",
        RefKind::Base => {
            let last = path.last().map(String::as_str).unwrap_or("");
            let mut ch = last.chars();
            let iface = fam == Family::CSharp
                && ch.next() == Some('I')
                && ch.next().is_some_and(|c| c.is_uppercase());
            if iface {
                "implements"
            } else {
                "inherits"
            }
        }
        _ => "inherits",
    }
}

/// The reasons of an unresolved row whose receiver has no type the source
/// gives: any implementation of a library type could be it.
const UNTYPED_RECEIVER_REASONS: &[&str] = &[
    "untyped local or parameter",
    "closure parameter outside any definition",
    "local the walk cannot type",
    "head name not found",
    "field or variable: its type names nothing",
];

/// Library bases that declare no member a call would name: markers,
/// equality and ordering, serialization, the roots of errors.
const MARKER_BASES: &[&str] = &[
    "Sendable",
    "Equatable",
    "Hashable",
    "Comparable",
    "Codable",
    "Encodable",
    "Decodable",
    "Identifiable",
    "CustomStringConvertible",
    "CustomDebugStringConvertible",
    "Stringable",
    "JsonSerializable",
    "Serializable",
    "Cloneable",
    "Error",
    "Exception",
    "Throwable",
    "RuntimeException",
    "IllegalStateException",
    "IllegalArgumentException",
    "IEquatable",
    "IComparable",
    "Debug",
    "Clone",
    "Copy",
    "PartialEq",
    "Eq",
    "Hash",
    "Default",
    "Display",
    "Closeable",
    "AutoCloseable",
    "IDisposable",
    "IAsyncDisposable",
    "Disposable",
];

/// The template a C++ specialization's name specializes: `AsyncWrapper` of
/// `AsyncWrapper<T, true>`, `cpr.AsyncWrapper` of `cpr.AsyncWrapper<T, true>`.
fn specialized(name: &str) -> Option<&str> {
    let i = name.find('<')?;
    (i > 0 && name.ends_with('>')).then(|| &name[..i])
}

/// The arguments a C++ specialization's name writes: `T`, `true` of
/// `AsyncWrapper<T, true>` (top-level commas only).
fn specialization_args(name: &str) -> Vec<String> {
    let Some(i) = name.find('<') else { return Vec::new() };
    let inner = name[i + 1..].strip_suffix('>').unwrap_or(&name[i + 1..]);
    let (mut out, mut cur, mut depth) = (Vec::new(), String::new(), 0i32);
    for ch in inner.chars() {
        match ch {
            '<' | '(' | '[' => depth += 1,
            '>' | ')' | ']' => depth -= 1,
            ',' if depth == 0 => {
                out.push(cur.trim().to_string());
                cur.clear();
                continue;
            }
            _ => {}
        }
        cur.push(ch);
    }
    if !cur.trim().is_empty() {
        out.push(cur.trim().to_string());
    }
    out
}

/// Whether a template argument is a value written out (`true`, `3`,
/// `'c'`): a specialization for it fits only that value.
fn literal_arg(a: &str) -> bool {
    matches!(a, "true" | "false" | "nullptr")
        || a.chars().next().is_some_and(|c| c.is_ascii_digit() || c == '\'' || c == '-')
}

/// A path in someone else's code or minified (`benchmarks/unresolved.py`'s
/// and the analysis scope's rule, by the path alone).
fn vendored_path(path: &str) -> bool {
    let parts: Vec<&str> = path.split('/').collect();
    let name = parts.last().copied().unwrap_or("");
    name.contains(".min.")
        || parts[..parts.len().saturating_sub(1)].iter().any(|p| {
            matches!(
                *p,
                "vendor" | "node_modules" | "wwwroot" | "third_party" | "dist" | "bower_components"
            )
        })
}
