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
    // LINQ (`Enumerable`)
    "All",
    "Any",
    "Select",
    "SelectMany",
    "Where",
    "First",
    "FirstOrDefault",
    "Single",
    "SingleOrDefault",
    "Last",
    "LastOrDefault",
    "Count",
    "Sum",
    "OrderBy",
    "OrderByDescending",
    "GroupBy",
    "ToList",
    "ToArray",
    "ToDictionary",
    "Distinct",
    "Skip",
    "Take",
    "Concat",
    "Aggregate",
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
fn is_collection_step(seg: &str) -> bool {
    seg == "partition"
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

pub struct Project<'a> {
    pub root: PathBuf,
    pub files: &'a [FileFacts],
    pub by_path: HashMap<&'a str, usize>,
    pub family: Vec<Option<Family>>,
    pub manifests: Vec<(String, PathBuf)>,
    hubs: &'a [String],
    def_ids: &'a [Vec<Option<String>>],
    /// Per file: top-level definitions (no definition encloses them, or only
    /// namespaces) by bare name.
    top: Vec<HashMap<String, Vec<usize>>>,
    /// Per file: lexical children of each definition by bare name.
    children: Vec<HashMap<usize, HashMap<String, Vec<usize>>>>,
    /// Every definition by bare name.
    by_name: HashMap<String, Vec<DefRef>>,
    /// Extension functions and methods (`Def::receiver`), by name.
    extensions: HashMap<String, Vec<DefRef>>,
    /// Every definition by qualname.
    by_qual: HashMap<String, Vec<DefRef>>,
    /// Package-qualified names (`a.b.C`, `Shop.Models.Item`) of the families
    /// that have packages or namespaces.
    fqn: HashMap<String, Vec<DefRef>>,
    /// Package / namespace names that exist, per family.
    namespaces: HashSet<(Family, String)>,
    /// Package / namespace / module -> files.
    pkg_files: HashMap<(Family, String), Vec<usize>>,
    /// `(family, owner type name)` -> methods attached by owner (Go receivers,
    /// Rust impls, C++ `A::m`, Swift extensions).
    owner_methods: HashMap<(Family, String), Vec<DefRef>>,
    modsys: modules::ModuleSystems,
    scopes: Vec<Scope>,
    exts: RefCell<Vec<ExtNode>>,
    ext_index: RefCell<HashMap<(&'static str, String), usize>>,
    bases_memo: RefCell<HashMap<DefRef, Vec<DefRef>>>,
    /// `(file, definition, name)` -> typed local / parameter.
    locals: HashMap<(usize, usize, String), usize>,
    /// type -> field name -> the field's local (its declared or assigned type).
    fields: HashMap<DefRef, HashMap<String, (usize, usize)>>,
    depth: RefCell<usize>,
    /// Set while a type reference is resolved: names then mean types only
    /// (`Item Item { get; set; }`: the property's type, not the property).
    types_only: RefCell<bool>,
    /// The path being resolved is the callee of a call.
    calling: RefCell<bool>,
    /// The receiver lambda the use being resolved sits in: `(file, index)`.
    recv: RefCell<Option<(usize, u16)>>,
    /// Names some type answers to without defining them (`FileFacts::attrs`).
    attrs: HashSet<(Family, String)>,
    /// The types that declare a name without defining it (`Def::attrs`).
    attr_owners: HashMap<(Family, String), Vec<DefRef>>,
    /// Per file: the member names used on each name, per definition.
    used_on: RefCell<HashMap<usize, Rc<UsedOn>>>,
    /// What `structural_type` found for a local: `(file, definition, name)`.
    structural: RefCell<HashMap<(usize, usize, String), Option<DefRef>>>,
    /// The types of locals already worked out: `(file, index of the local)`.
    local_types: RefCell<HashMap<(usize, usize), Typed>>,
    /// Whether a local assigned a value holds it in a sealed wrapper
    /// (`let r = load();` with `fn load() -> Result<Foo, E>`), per local.
    sealed_locals: RefCell<HashMap<(usize, usize), bool>>,
    /// How many parts the tuple a local's value is has (0: no tuple), per
    /// local.
    tuple_locals: RefCell<HashMap<(usize, usize), usize>>,
    /// What a call reference calls, with its overload picked, per reference
    /// (`call_target`): what an implicit member in its arguments is typed by.
    call_targets: RefCell<HashMap<(usize, usize), Called>>,
    /// Whether the reference being resolved was answered by a name alone
    /// (`fallback_method`, a single extension in sight): no scope said so.
    guessed: std::cell::Cell<bool>,
    /// The bindings a local's value gives, per local (`local_value_binds`).
    value_binds: RefCell<HashMap<(usize, usize), Option<Binds>>>,
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
    /// How many arguments the call being resolved passes.
    args: RefCell<Option<u8>>,
    /// One of them is `this`.
    self_arg: RefCell<bool>,
    /// The receiver type of each such lambda, once worked out.
    recv_types: RefCell<HashMap<(usize, u16), Typed>>,
    /// Import -> files a resolved use went through it to.
    used: RefCell<HashMap<(usize, usize), HashSet<usize>>>,
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
    pub fn new(
        root: &Path,
        files: &'a [FileFacts],
        asm: &'a Assembled,
        manifests: Vec<(String, PathBuf)>,
    ) -> Project<'a> {
        let by_path = files.iter().enumerate().map(|(i, f)| (f.path.as_str(), i)).collect();
        let family: Vec<Option<Family>> = files.iter().map(|f| f.lang.map(Family::of)).collect();
        let mut p = Project {
            root: root.to_path_buf(),
            files,
            by_path,
            family,
            manifests,
            hubs: &asm.hubs,
            def_ids: &asm.def_ids,
            top: Vec::new(),
            children: Vec::new(),
            by_name: HashMap::new(),
            extensions: HashMap::new(),
            by_qual: HashMap::new(),
            fqn: HashMap::new(),
            namespaces: HashSet::new(),
            pkg_files: HashMap::new(),
            owner_methods: HashMap::new(),
            modsys: modules::ModuleSystems::default(),
            scopes: Vec::new(),
            exts: RefCell::new(Vec::new()),
            ext_index: RefCell::new(HashMap::new()),
            bases_memo: RefCell::new(HashMap::new()),
            locals: HashMap::new(),
            fields: HashMap::new(),
            depth: RefCell::new(0),
            types_only: RefCell::new(false),
            calling: RefCell::new(false),
            recv: RefCell::new(None),
            attrs: HashSet::new(),
            attr_owners: HashMap::new(),
            used_on: RefCell::new(HashMap::new()),
            structural: RefCell::new(HashMap::new()),
            local_types: RefCell::new(HashMap::new()),
            sealed_locals: RefCell::new(HashMap::new()),
            tuple_locals: RefCell::new(HashMap::new()),
            call_targets: RefCell::new(HashMap::new()),
            guessed: std::cell::Cell::new(false),
            value_binds: RefCell::new(HashMap::new()),
            end_binds: RefCell::new(None),
            typing_value: RefCell::new(false),
            typing: RefCell::new(HashSet::new()),
            cur: RefCell::new(None),
            args: RefCell::new(None),
            self_arg: RefCell::new(false),
            recv_types: RefCell::new(HashMap::new()),
            used: RefCell::new(HashMap::new()),
        };
        p.index();
        p.index_locals();
        p.modsys = modules::ModuleSystems::build(&p);
        p.scopes = (0..files.len()).map(|f| p.scope_of(f)).collect();
        // the files a file's imported names live in (`use A\B\C`, `import a.b.C`)
        for f in 0..files.len() {
            let mut more = Vec::new();
            for (b, _) in p.scopes[f].bindings.values() {
                if let Some(Target::Def((g, _))) = p.eval(b, 2) {
                    more.push(g);
                }
            }
            p.scopes[f].imported.extend(more);
        }
        // Rust: what a module star-imported (`use crate::prelude::*;`)
        // passes on with `pub use crate::error::Error;`. Only a star import:
        // a crate root re-exports most of the crate, and a file that names
        // one thing out of it does not see the rest
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
            p.scopes[f].imported.extend(more);
        }
        // … and the files a module it imports passes on (`module.exports =
        // require('./lib/app')`, `exports.response = res`, `export * from`):
        // a package's entry stands for the files it is made of
        let passes: Vec<Vec<usize>> = (0..files.len()).map(|g| p.passes_on(g)).collect();
        if passes.iter().any(|x| !x.is_empty()) {
            for (f, scope) in p.scopes.iter_mut().enumerate() {
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
                self.attrs.extend(f.attrs.iter().map(|a| (fam, a.clone())));
            }
            for (di, d) in f.defs.iter().enumerate() {
                if let Some(fam) = fam {
                    for a in &d.attrs {
                        self.attr_owners.entry((fam, a.clone())).or_default().push((fi, di));
                    }
                }
                // a class with no name of its own answers to none, nor a
                // variable the grammar took for a declaration
                if d.anonymous || d.misread {
                    continue;
                }
                // a method declared for a type (Go receiver, Rust impl, C++
                // `A::m`) is no module-level name
                // C++ `class AsyncWrapper<T, true>`: a specialization answers
                // to the template's name too
                let bare = (fam == Some(Family::C) && d.kind.is_class_like())
                    .then(|| specialized(&d.name))
                    .flatten();
                // (nor a Rust associated type: `type Out = Wire;` in an `impl`)
                let owned = d.kind.is_callable()
                    || (fam == Some(Family::Rust) && d.kind == DefKind::TypeAlias);
                if is_top(&f.defs, di) && !(d.owner.is_some() && owned) {
                    top.entry(d.name.clone()).or_default().push(di);
                    if let Some(b) = bare {
                        top.entry(b.to_string()).or_default().push(di);
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
                    self.by_qual.entry(qual_key(fam, q)).or_default().push((fi, di));
                }
                if self.def_ids[fi].get(di).is_some_and(|x| x.is_some()) {
                    self.by_name.entry(d.name.clone()).or_default().push((fi, di));
                }
                // (an RSpec `let` is a name of its block only: the scoped
                // lookup in `resolve_name_any` is its one way in)
                if d.scope_lines.is_none() {
                    self.by_qual.entry(qual_key(fam, &d.qualname)).or_default().push((fi, di));
                }
                if d.receiver.is_some() && !d.decl_only {
                    self.extensions.entry(d.name.clone()).or_default().push((fi, di));
                }
                if d.kind == DefKind::Namespace {
                    let ns = d.qualname.replace("::", ".");
                    self.namespaces.insert((fam, ns.clone()));
                    self.pkg_files.entry((fam, ns)).or_default().push(fi);
                }
                if let Some(fq) = self.fqn_of(fi, di) {
                    self.fqn.entry(fq).or_default().push((fi, di));
                }
                if let Some(owner) = &d.owner {
                    // (and a Rust associated type: `type Out = Wire;` in an `impl`)
                    if d.kind.is_callable() || (fam == Family::Rust && d.kind == DefKind::TypeAlias)
                    {
                        let bare = owner.rsplit(['.', ':']).next().unwrap_or(owner).to_string();
                        self.owner_methods.entry((fam, bare)).or_default().push((fi, di));
                    }
                }
            }
            if let (Some(fam), Some(pkg)) = (fam, f.package.as_ref()) {
                let pkg = pkg.replace('\\', ".");
                let mut parts: Vec<&str> = pkg.split('.').collect();
                while !parts.is_empty() {
                    self.namespaces.insert((fam, parts.join(".")));
                    parts.pop();
                }
                self.pkg_files.entry((fam, pkg)).or_default().push(fi);
            }
            self.top.push(top);
            self.children.push(children);
        }
    }

    fn index_locals(&mut self) {
        let mut locals = HashMap::new();
        let mut fields: HashMap<DefRef, HashMap<String, (usize, usize)>> = HashMap::new();
        for (fi, f) in self.files.iter().enumerate() {
            for (li, l) in f.locals.iter().enumerate() {
                // a local no definition holds (a callback's at file level) is
                // the file's, unless the file defines that name itself (a
                // module constant stays the definition it is)
                let d = match l.from_def {
                    Some(d) => d,
                    // (JS / TS only: C#'s top-level programs, Swift's and
                    // Kotlin's scripts read their file-level names otherwise)
                    // (nor a name the file imports, `var request =
                    // require('supertest')`, nor one with no value the walk
                    // reads, which would only hide what other rules find:
                    // `const EventHandler = { trigger() {} }`)
                    None if l.field
                        || self.family[fi] != Some(Family::Ecma)
                        // (a top-level definition of the name is what it
                        // means; a member of an object literal is no such name)
                        || self.top[fi]
                            .get(&l.name)
                            .is_some_and(|ds| ds.iter().any(|&x| f.defs[x].object.is_none()))
                        || l.path.is_empty()
                        || f.imports.iter().any(|i| {
                            i.alias.as_deref() == Some(l.name.as_str())
                                || i.names.iter().any(|(o, a)| a.as_deref().unwrap_or(o) == l.name)
                        }) =>
                    {
                        continue
                    }
                    None => FILE_SCOPE,
                };
                if l.field {
                    if let Some(c) = self.class_of(fi, Some(d)) {
                        // (PHP `$this->article = $article;` tells a property's
                        // type only when its declaration writes none:
                        // `protected Article $article;` is its own type)
                        let written = !l.declared
                            && self.family[fi] == Some(Family::Php)
                            && matches!(l.path.as_slice(), [v] if v.starts_with('$'))
                            && self.children[c.0]
                                .get(&c.1)
                                .and_then(|m| m.get(&l.name))
                                .is_some_and(|ds| {
                                    ds.iter().any(|&x| {
                                        let x = &self.files[c.0].defs[x];
                                        x.kind.is_state() && x.declared_type.is_some()
                                    })
                                });
                        if written {
                            continue;
                        }
                        // (the property's own initializer says what it holds,
                        // before a constructor parameter of its name: Kotlin
                        // `class CodeWriter(out: Appendable) { private var out
                        // = LineWrapper(out) }`)
                        let own = d != FILE_SCOPE && {
                            let x = &f.defs[d];
                            x.kind.is_state() && x.name == l.name
                        };
                        let entry = fields.entry(c).or_default();
                        if own {
                            entry.insert(l.name.clone(), (fi, li));
                        } else {
                            entry.entry(l.name.clone()).or_insert((fi, li));
                        }
                    }
                } else {
                    locals.insert((fi, d, l.name.clone()), li);
                }
            }
        }
        self.locals = locals;
        self.fields = fields;
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
        let found = self.resolve_path(f, d, path, true);
        match found? {
            (Target::Def(r), conf) => {
                let def = self.def(r);
                let k = def.kind;
                // C, C++ `using AsyncResponseC = AsyncWrapper<Response, true>;`,
                // `typedef struct conn conn_t;`: the type the alias names, and of
                // a template's specializations the one its arguments fit
                let c_alias = k == DefKind::TypeAlias && self.family[r.0] == Some(Family::C);
                // Rust `type Out = Wire;` in an `impl` or a trait: the
                // associated type is what it names (`Self::Out`); an alias of
                // its own module is followed by nothing here
                let rust_assoc = k == DefKind::TypeAlias
                    && self.family[r.0] == Some(Family::Rust)
                    && (def.owner.is_some()
                        || def
                            .parent
                            .is_some_and(|p| self.files[r.0].defs[p].kind.is_class_like()));
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
                    (c_alias || rust_assoc)
                        .then_some(def.declared_type.as_ref())
                        .flatten()
                        .and_then(|ty| self.type_of_path(r.0, def.parent, ty, true))
                        .filter(|t| *t != r && self.def(*t).kind.is_class_like())
                });
                if let Some(t) = aliased {
                    Some((self.specialization_for(t, &def.alias_args), conf))
                } else if k.is_class_like() || k == DefKind::TypeAlias {
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
    /// grammar reads the block as a constructor named `extension`.
    fn in_extension_block(&self, c: DefRef) -> bool {
        self.def(c).parent.is_some_and(|p| {
            let block = &self.files[c.0].defs[p];
            block.kind == DefKind::Constructor && block.name == "extension"
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
            score += self.arg_fits(f, d, c, &a.kind, &param.ty)?;
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
        match arg {
            Arg::Str => literal(Prim::Str),
            Arg::Num => literal(Prim::Num),
            Arg::Bool => literal(Prim::Bool),
            Arg::Class => literal(Prim::Class),
            // a lambda is something to call: no string, number, flag or
            // class (it may be an interface of one method, which a class of
            // the project can be)
            Arg::Func => match prim {
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
            .filter(|t| self.def(*t).kind.is_class_like());
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
        self.type_of_path(c.0, self.def(c).parent, ty, true)
            .filter(|t| self.def(*t).kind.is_class_like())
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

    /// C#: a member of a project type the project's own source generator
    /// writes (`client.Options.EncodeQuery` of a `ReadOnlyRestClientOptions`
    /// made from `[GenerateImmutable] RestClientOptions`): the type answers no
    /// such member in the source, the project has an `ISourceGenerator` /
    /// `IIncrementalGenerator`, and a type whose name the receiver's contains
    /// declares the member.
    fn generated_member(&self, f: usize, d: Option<usize>, path: &[String]) -> bool {
        let Some((name, prefix)) = path.split_last() else { return false };
        if prefix.is_empty() || !self.has_source_generator() {
            return false;
        }
        let Some(_guard) = DepthGuard::enter(&LIBRARY_DEPTH, 2) else { return false };
        let Some(t) = self.type_of_path(f, d, prefix, false) else { return false };
        let tname = self.def(t).name.clone();
        if self.answers(t, name, true, 4) {
            return false;
        }
        self.by_name.get(name.as_str()).into_iter().flatten().any(|m| {
            let owner = self.def(*m).parent.map(|p| (m.0, p));
            owner.is_some_and(|o| {
                let oname = &self.def(o).name;
                o != t && oname.len() < tname.len() && tname.contains(oname.as_str())
            })
        })
    }

    /// Whether the project has a C# source generator (a class implementing
    /// `ISourceGenerator` or `IIncrementalGenerator`).
    fn has_source_generator(&self) -> bool {
        self.files.iter().any(|g| {
            g.lang == Some(crate::lang::Lang::CSharp)
                && g.refs.iter().any(|r| {
                    matches!(r.kind, RefKind::Inherits | RefKind::Implements | RefKind::Base)
                        && r.path.last().is_some_and(|n| {
                            n == "ISourceGenerator" || n == "IIncrementalGenerator"
                        })
                })
        })
    }

    /// Kotlin: a member a compiler plugin adds to an annotated class, called on
    /// the class (`UserResponse.serializer()` of a `@Serializable` one,
    /// kotlinx.serialization).
    fn plugin_member(&self, f: usize, d: Option<usize>, path: &[String]) -> bool {
        const GENERATED: &[(&str, &str)] = &[("Serializable", "serializer")];
        let [head, name] = path else { return false };
        let Some((Target::Def(c), _)) = self.resolve_path(f, d, std::slice::from_ref(head), true)
        else {
            return false;
        };
        GENERATED.iter().any(|(annotation, member)| {
            name == member
                && self.files[c.0].refs.iter().any(|r| {
                    r.from_def == Some(c.1)
                        && r.kind == RefKind::Decorator
                        && r.path.last().is_some_and(|n| n == annotation)
                })
        })
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
                // (extensions written for other types are no overloads:
                // `PropertySpec.Builder.addOriginatingKSFile` beside
                // `TypeAliasSpec.Builder.addOriginatingKSFile`)
                x.kind.is_callable()
                    && !x.decl_only
                    && x.owner == d.owner
                    && x.receiver == d.receiver
            })
            .collect();
        out.sort();
        // Swift: those an extension of the type adds under the name
        // (`extension Session { func request(_ endpoint: Endpoint) }`)
        if self.family[m.0] == Some(Family::Swift) {
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
        let all = self.overloads(m);
        if all.len() < 2 {
            return vec![m];
        }
        let fits: Vec<(DefRef, i32)> =
            all.iter().filter_map(|c| self.fit(f, d, *c, r).map(|s| (*c, s))).collect();
        let mut best = self.exact_first(self.top_fits(&fits), r);
        if best.is_empty() {
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
        if let Some(known) = self.recv_types.borrow().get(&(f, i)) {
            return *known;
        }
        let scope = self.files[f].receivers.get(usize::from(i))?;
        // what names the receiver is written outside the lambda
        let before = self.recv.replace(scope.outer.map(|o| (f, o)));
        let types = self.types_only.replace(false);
        let calling = self.calling.replace(false);
        let out = match &scope.of {
            ReceiverOf::Value(path) => self.type_of_path_inner(f, d, path),
            // the value's own type first; else, when the call is a library's,
            // the project type its one type argument names (`INFERRED`)
            ReceiverOf::Given(path, given) => self.type_of_path_inner(f, d, path).or_else(|| {
                let callee = path.strip_suffix(&["()".to_string()]).unwrap_or(path);
                // (a guess by the method's name is no project call)
                let library = !matches!(
                    self.resolve_path(f, d, callee, false),
                    Some((Target::Def(_), Confidence::Extracted))
                );
                let t = self.type_of_path(f, d, given, true)?;
                (library && self.def(t).kind.is_class_like()).then_some((t, Confidence::Inferred))
            }),
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
        self.recv_types.borrow_mut().insert((f, i), out);
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
        // (`Self::Out` is seen from the method: its own type's member)
        let scope_for = |p: &[String]| {
            if p.len() > 1 && p[0] == "self" {
                Some(r.1)
            } else {
                self.def(r).parent
            }
        };
        if let Some(inner) = self.def(r).wraps.as_ref().filter(|_| self.def(r).kind.is_callable()) {
            let t = self.type_of_path(r.0, scope_for(inner), inner, true)?;
            return self.def(t).kind.is_class_like().then_some((t, true));
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
            return found.filter(|t| self.def(*t).kind.is_class_like()).map(|t| (t, false));
        };
        let path = rt.path.clone();
        if path == ["self"] {
            // `-> Self`, `: this`: the method's own type (builders)
            return self.class_of(r.0, Some(r.1)).map(|t| (t, true));
        }
        let declared = self
            .type_of_path(r.0, scope_for(&path), &path, true)
            .filter(|t| self.def(*t).kind.is_class_like());
        if declared.is_some() {
            return declared.map(|t| (t, true));
        }
        // TS `(): ClientRequest => new Http2ClientRequest() as unknown as
        // ClientRequest`: a type of the project's built under a cast, where
        // the type written is none (a guess: the cast says otherwise)
        let cast = self.def(r).cast_from.as_ref()?;
        self.type_of_path(r.0, Some(r.1), cast, true)
            .filter(|t| self.def(*t).kind.is_class_like())
            .map(|t| (t, false))
    }

    /// The type of a local name or parameter seen from `d`.
    fn local_type(&self, f: usize, d: Option<usize>, name: &str) -> Option<(DefRef, Confidence)> {
        let mut cur = local_start(d);
        while let Some(x) = cur {
            if let Some(&li) = self.locals.get(&(f, x, name.to_string())) {
                // typing a local can come back to it (`let session =
                // session(using: x)`: the value names the local): the second
                // time round it has no type
                if let Some(known) = self.local_types.borrow().get(&(f, li)) {
                    return *known;
                }
                // worked out from the top, the answer is the local's own and
                // is kept; inside another typing it may be cut short
                let whole = self.typing.borrow().is_empty() && *self.depth.borrow() == 0;
                if !self.typing.borrow_mut().insert((f, li)) {
                    return None;
                }
                // a local's type is the same whatever use asks for it: its
                // value's path is what is called there (`x = f()` -> `f`),
                // a written type is not
                let value = !self.files[f].locals[li].declared;
                let calling = self.calling.replace(value);
                let typing_value = self.typing_value.replace(value);
                let recv = self.recv.replace(None);
                let out = self.local_type_of(f, x, li, name);
                *self.recv.borrow_mut() = recv;
                *self.typing_value.borrow_mut() = typing_value;
                *self.calling.borrow_mut() = calling;
                self.typing.borrow_mut().remove(&(f, li));
                if whole {
                    self.local_types.borrow_mut().insert((f, li), out);
                }
                return out;
            }
            cur = self.up(f, x);
        }
        None
    }

    /// Rust: of several associated types `name` of type `c` (one per `impl`
    /// block), the one written in file `f` nearest before definition `d`, else
    /// nearest after it; `None` when there are not several.
    fn assoc_type_near(&self, c: DefRef, name: &str, f: usize, d: Option<usize>) -> Option<DefRef> {
        let at = self.files[f].defs[d?].start_byte;
        let cands: Vec<DefRef> = self
            .members(c, name)
            .into_iter()
            .filter(|m| self.def(*m).kind == DefKind::TypeAlias)
            .collect();
        if cands.len() < 2 {
            return None;
        }
        let here = cands.iter().filter(|m| m.0 == f);
        let before = here.clone().filter(|m| self.def(**m).start_byte <= at);
        before
            .max_by_key(|m| self.def(**m).start_byte)
            .or_else(|| here.min_by_key(|m| self.def(**m).start_byte))
            .copied()
    }

    /// Rust: the type a closure's parameter `name@byte` (of a closure written
    /// in definition `x`) is handed, as the function the closure is passed
    /// to writes it (`handler: impl Fn(Uri, Request<()>)`, `F: Fn(Req)`):
    /// the path, the file and scope it is written in, and how sure the call is.
    fn closure_param_written(
        &self,
        f: usize,
        x: usize,
        name: &str,
    ) -> Option<(Vec<String>, usize, Option<usize>, Confidence)> {
        if self.family[f] != Some(Family::Rust) {
            return None;
        }
        let (param, byte) = name.split_once('@')?;
        let byte: usize = byte.parse().ok()?;
        let (_, callee, place, names) =
            self.files[f].closure_args.iter().find(|(b, ..)| *b == byte)?;
        let k = names.iter().position(|n| n == param)?;
        let calling = self.calling.replace(true);
        let found = self.resolve_path(f, scope(x), callee, true);
        *self.calling.borrow_mut() = calling;
        let Some((Target::Def(c), conf)) = found else { return None };
        let def = self.def(c);
        if !def.kind.is_callable() {
            return None;
        }
        let ty = def.plist.get(usize::from(*place))?.fn_args.get(k)?;
        (!ty.is_empty()).then(|| (ty.clone(), c.0, Some(c.1), conf))
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
            // Rust: a closure's parameter is what the function it is passed
            // to says its `Fn` takes
            if let Some((ty, g, scope, conf)) = self.closure_param_written(f, x, name) {
                return self
                    .type_of_path(g, scope, &ty, true)
                    .filter(|t| self.def(*t).kind.is_class_like())
                    .map(|t| (t, conf));
            }
            // no type written, nothing typeable assigned: the one
            // type that answers everything asked of it here
            return self.structural_type(f, x, name).map(|t| (t, Confidence::Inferred));
        }
        let t = self
            .type_of_path(f, scope(x), &l.path, l.declared)
            .or_else(|| self.given_result(f, x, l))
            .or_else(|| self.bound_result(f, x, l))
            .or_else(|| self.accessed(f, x, l))
            .or_else(|| self.wrapped_arg(f, x, l));
        let Some(t) = t else {
            // a value the walk cannot type, unless it is of a
            // library's type: the same
            let external = l.declared
                || matches!(
                    l.path.first().and_then(|h| self.resolve_name(f, scope(x), h)),
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

    /// The type of a local assigned a generic call that is handed a type
    /// (`val s = retrofit.create(Service::class.java)`, `mock<Foo>()`,
    /// `sp.GetRequiredService<IFoo>()`): that type, when the function called
    /// returns its own type parameter — or is not the project's, and is not
    /// named like something that wraps what it is given (`listOf<Foo>()`).
    fn given_result(&self, f: usize, d: usize, l: &crate::facts::Local) -> Option<DefRef> {
        let given = l.given.as_ref()?;
        let name = l.path.last()?;
        let before = self.recv.replace(None);
        let callee = self.resolve_path(f, scope(d), &l.path, true);
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
        self.type_of_path(f, scope(d), given, true).filter(|t| self.def(*t).kind.is_class_like())
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
                .filter(|t| self.def(*t).kind.is_class_like())
                .map(|t| (t, Confidence::Extracted))
        };
        if let [name] = path {
            let defs = &self.files[f].defs;
            let mut cur = local_start(d);
            while let Some(x) = cur {
                if let Some(&li) = self.locals.get(&(f, x, name.clone())) {
                    let l = &self.files[f].locals[li];
                    // a parameter's is on the definition's parameter list
                    let param =
                        scope(x).and_then(|x| defs[x].plist.iter().find(|p| p.name == *name));
                    let elem = l.elem.clone().or_else(|| param.and_then(|p| p.elem.clone()));
                    let keyed = l.keyed || param.is_some_and(|p| p.keyed);
                    if let Some(e) = elem {
                        // (the first of a map's pair is its key)
                        if how == crate::refs::KEY && keyed {
                            let key = l.key.clone().or_else(|| param.and_then(|p| p.key.clone()));
                            return key.and_then(|k| typed(f, scope(x), &k));
                        }
                        let fits = match how {
                            crate::refs::ITERATED | crate::refs::KEY => !keyed,
                            crate::refs::VALUE => keyed,
                            _ => true,
                        };
                        return if fits { typed(f, scope(x), &e) } else { None };
                    }
                    // an optional is a collection of at most one
                    // (`opt.ifPresent { it.run() }`)
                    if l.sealed && how != crate::refs::VALUE {
                        return typed(f, scope(x), &l.path);
                    }
                    // another name for a collection: `val ys = xs`
                    let alias = !l.declared
                        && !l.path.is_empty()
                        && l.path[0] != *name
                        && !l.path.is_empty();
                    return if alias {
                        self.elem_of_as(f, scope(x), &l.path, how)
                            .map(|(t, _)| (t, Confidence::Inferred))
                    } else {
                        None
                    };
                }
                cur = self.up(f, x);
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
            .filter(|t| self.def(*t).kind.is_class_like())
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
        self.elem_of_as(f, scope(d), of, how).map(|(t, _)| t)
    }

    /// The type of a local assigned a call of a function that returns its
    /// own type parameter, bound by an argument: `fun <T> pick(x: T): T`
    /// gives the argument's type, `fun <T> head(xs: List<T>): T` its element's.
    fn bound_result(&self, f: usize, d: usize, l: &crate::facts::Local) -> Option<DefRef> {
        if l.value_args.is_empty() {
            return None;
        }
        let recv = self.recv.replace(None);
        let callee = self.resolve_path(f, scope(d), &l.path, true);
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
                return self.value_type_of(f, scope(d), q);
            }
            if p.elem.as_ref().is_some_and(|e| e.len() == 1 && e[0] == *t) {
                return self.elem_of(f, scope(d), q).map(|(x, _)| x);
            }
        }
        None
    }

    /// Rust: what a wrapper's own constructor is passed is what the local
    /// holds (`Arc::downgrade(&self.pool)`, `Arc::new(x)`, `Rc::clone(&x)`;
    /// the wrapper itself answers for what it holds).
    fn wrapped_arg(&self, f: usize, x: usize, l: &crate::facts::Local) -> Option<DefRef> {
        if self.family[f] != Some(Family::Rust) {
            return None;
        }
        let [w, m] = l.path.as_slice() else { return None };
        if !matches!(
            w.as_str(),
            "Arc" | "Rc" | "Box" | "Weak" | "Cell" | "RefCell" | "Mutex" | "RwLock"
        ) || !matches!(m.as_str(), "new" | "downgrade" | "clone" | "from")
            || self.resolve_name(f, scope(x), w).is_some_and(|t| matches!(t, Target::Def(_)))
        {
            return None;
        }
        let [a] = l.value_args.as_slice() else { return None };
        let crate::facts::Arg::Path(q) = &a.kind else { return None };
        self.value_type_of(f, scope(x), q).filter(|t| self.def(*t).kind.is_class_like())
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
        if let Some(known) = self.used_on.borrow().get(&f) {
            return known.clone();
        }
        let mut out: UsedOn = HashMap::new();
        for r in &self.files[f].refs {
            let (Some(d), [head, member, ..]) = (r.from_def, r.path.as_slice()) else { continue };
            if member.starts_with(['(', '[']) || matches!(head.as_str(), "self" | "super" | "()") {
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
        if let Some(known) = self.structural.borrow().get(&key) {
            return *known;
        }
        self.structural.borrow_mut().insert(key.clone(), None); // a cycle ends here
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
        self.structural.borrow_mut().insert(key, found);
        found
    }

    /// A parameter or local whose type is not known: it shadows every outer
    /// name, so nothing is resolved through it.
    fn is_untyped_local(&self, f: usize, d: Option<usize>, name: &str) -> bool {
        let mut cur = local_start(d);
        while let Some(x) = cur {
            if let Some(&li) = self.locals.get(&(f, x, name.to_string())) {
                return self.files[f].locals[li].path.is_empty();
            }
            cur = self.up(f, x);
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
    /// The members named `name` of a namespace: its block's, and in C and
    /// C++ those of every block that opens the same namespace
    /// (`namespace cpr { … }` in each header), its own first.
    fn namespace_members(&self, c: DefRef, name: &str) -> Vec<DefRef> {
        let own = self.children[c.0].get(&c.1).and_then(|m| m.get(name));
        let mut out: Vec<DefRef> = own.into_iter().flatten().map(|&k| (c.0, k)).collect();
        if self.family[c.0] != Some(Family::C) {
            return out;
        }
        let key = qual_key(Family::C, &self.def(c).qualname);
        for &other in self.by_qual.get(&key).into_iter().flatten() {
            if other == c || self.def(other).kind != DefKind::Namespace {
                continue;
            }
            let kids = self.children[other.0].get(&other.1).and_then(|m| m.get(name));
            out.extend(kids.into_iter().flatten().map(|&k| (other.0, k)));
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
    /// JS / TS: a module variable's member that an object literal it holds
    /// defines (`const test = Object.assign(runTest, { before() {} })`,
    /// `const api = { get() {} }`), seen from any file that names it.
    fn held_object_member(&self, v: DefRef, name: &str) -> Option<DefRef> {
        let def = self.def(v);
        if self.family[v.0] != Some(Family::Ecma) || !def.kind.is_state() || def.parent.is_some() {
            return None;
        }
        let cands: Vec<DefRef> = self.files[v.0]
            .defs
            .iter()
            .enumerate()
            .filter(|(_, x)| {
                x.name == name
                    && x.object.as_deref() == Some(def.name.as_str())
                    && x.kind.is_callable()
            })
            .map(|(i, _)| (v.0, i))
            .collect();
        self.best(cands)
    }

    /// JS / TS: `name` of what `head` holds, when it is the result of a call
    /// (a local the walk cannot type, a module variable with no type) and the
    /// function called returns an object literal with a function of that name
    /// (`return { contentType: () => … }`, `return { setConfig }`).
    fn returned_member(
        &self,
        f: usize,
        d: Option<usize>,
        head: &str,
        name: &str,
    ) -> Option<DefRef> {
        let _guard = DepthGuard::enter(&LIBRARY_DEPTH, 2)?;
        let (value, g, scope): (Vec<String>, usize, Option<usize>) = if self
            .is_local_name(f, d, head)
        {
            // (a type that answers the name is what is meant)
            if self.local_type(f, d, head).is_some_and(|(t, _)| self.answers(t, name, true, 4)) {
                return None;
            }
            let mut cur = local_start(d);
            let mut found = None;
            while let Some(x) = cur {
                if let Some(&li) = self.locals.get(&(f, x, head.to_string())) {
                    let l = &self.files[f].locals[li];
                    if !l.declared && !l.path.is_empty() {
                        found = Some((l.path.clone(), f, scope(x)));
                    }
                    break;
                }
                cur = self.up(f, x);
            }
            found?
        } else {
            let Some((Target::Def(v), _)) = self.resolve_path(f, d, &[head.to_string()], true)
            else {
                return None;
            };
            let def = self.def(v);
            if !def.kind.is_state()
                || self.held_type(v).is_some_and(|t| self.answers(t, name, true, 4))
            {
                return None;
            }
            (def.value_path.clone()?, v.0, def.parent)
        };
        if value.last().is_some_and(|s| s.starts_with('[')) {
            return None;
        }
        let calling = self.calling.replace(true);
        let callee = self.resolve_path(g, scope, &value, true);
        *self.calling.borrow_mut() = calling;
        let Some((Target::Def(c), _)) = callee else { return None };
        let def = self.def(c);
        if !def.kind.is_callable() {
            return None;
        }
        let (_, local) = def.returned_members.iter().find(|(k, _)| k == name)?;
        self.children[c.0]
            .get(&c.1)?
            .get(local)?
            .iter()
            .map(|&x| (c.0, x))
            .find(|x| self.def(*x).kind.is_callable())
    }

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
            for (g, facts) in self.files.iter().enumerate() {
                if self.family[g] != Some(Family::Ecma) {
                    continue;
                }
                for obj in facts.defs.iter().filter(|x| {
                    x.kind.is_state()
                        && x.created_from.as_deref().is_some_and(|p| {
                            p.len() >= 2
                                && p[p.len() - 2] == created
                                && p[p.len() - 1] == "prototype"
                        })
                }) {
                    built = true;
                    cands.extend(facts.defs.iter().enumerate().filter_map(|(i, y)| {
                        (y.name == name && y.object.as_deref() == Some(obj.name.as_str()))
                            .then_some((g, i))
                    }));
                }
            }
            // (only a project that builds the object itself)
            if !built {
                return None;
            }
            // (`app.response.message = function`: what the app adds to them)
            for (g, facts) in self.files.iter().enumerate() {
                if self.family[g] == Some(Family::Ecma) {
                    cands.extend(facts.defs.iter().enumerate().filter_map(|(i, y)| {
                        (y.name == name && y.object.as_deref() == Some(alias)).then_some((g, i))
                    }));
                }
            }
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
        for (g, facts) in self.files.iter().enumerate() {
            if facts.models.iter().any(|(n, _)| n == model) {
                cands.extend(facts.defs.iter().enumerate().filter_map(|(i, y)| {
                    (y.name == name && y.object.as_deref() == Some("methods")).then_some((g, i))
                }));
            }
        }
        self.best(cands)
    }

    /// Whether the project builds a framework's request or response object
    /// itself (`Object.create(http.IncomingMessage.prototype)`).
    fn builds_framework_object(&self) -> bool {
        self.files.iter().any(|facts| {
            facts.defs.iter().any(|x| {
                x.created_from.as_deref().is_some_and(|p| {
                    p.len() >= 2
                        && p[p.len() - 1] == "prototype"
                        && matches!(p[p.len() - 2].as_str(), "IncomingMessage" | "ServerResponse")
                })
            })
        })
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
        self.files.iter().any(|facts| facts.request_props.contains(&prop))
    }

    /// The path a local's declaration gives it (its type as written).
    fn local_path(&self, f: usize, d: Option<usize>, name: &str) -> Option<&Vec<String>> {
        let mut cur = local_start(d);
        while let Some(x) = cur {
            if let Some(&li) = self.locals.get(&(f, x, name.to_string())) {
                return Some(&self.files[f].locals[li].path);
            }
            cur = self.up(f, x);
        }
        None
    }

    /// The first name of the type a local's declaration writes (C, C++:
    /// `std` of `std::promise<void> release;`).
    fn local_written_head(&self, f: usize, d: Option<usize>, name: &str) -> Option<&str> {
        let mut cur = local_start(d);
        while let Some(x) = cur {
            if let Some(&li) = self.locals.get(&(f, x, name.to_string())) {
                return self.files[f].locals[li].written_head.as_deref();
            }
            cur = self.up(f, x);
        }
        None
    }

    fn is_external_local(&self, f: usize, d: Option<usize>, name: &str) -> bool {
        let mut cur = local_start(d);
        while let Some(x) = cur {
            if let Some(&li) = self.locals.get(&(f, x, name.to_string())) {
                let l = &self.files[f].locals[li];
                let head = l.path.first().map(String::as_str).unwrap_or("");
                // Rust: a closure's parameter the function it is passed to
                // hands a library's type (`|uri, req|` for `Fn(Uri, Request<()>)`)
                if head.is_empty() {
                    if let Some((ty, g, scope, _)) = self.closure_param_written(f, x, name) {
                        return match self.resolve_path(g, scope, &ty, true) {
                            Some((Target::Ext(..), _)) => true,
                            Some(_) => false,
                            None => ty.len() >= 2 && self.rust_crate_name(&ty[0]),
                        };
                    }
                }
                if head.is_empty() || matches!(head, "self" | "super" | "()") {
                    return false;
                }
                if matches!(self.resolve_name(f, scope(x), head), Some(Target::Ext(_, _))) {
                    return true;
                }
                // (C# `string value`, `int n`: the language's own types)
                if l.declared
                    && self.family[f] == Some(Family::CSharp)
                    && l.path.len() == 1
                    && CSHARP_PREDEFINED.contains(&l.path[0].as_str())
                {
                    return true;
                }
                // (Kotlin `String::class`: a `KClass`, imported or not)
                if l.declared
                    && l.name.starts_with(crate::refs::CAST)
                    && l.path.len() == 1
                    && l.path[0] == crate::refs::CLASS_LITERAL_TYPE
                    && !matches!(self.resolve_name(f, scope(x), head), Some(Target::Def(_)))
                {
                    return true;
                }
                // (Java `String readBody`, `((Class<?>) type)`: `java.lang`)
                if l.declared && self.java_library_type(f, scope(x), &l.path.clone()) {
                    return true;
                }
                // (`let res = crate::get(url).call().unwrap()`, `call` declared
                // `-> Result<Response<Body>, Error>` with `Response` a library's)
                let path = l.path.clone();
                // (`val o = spec.toJavaFileObject()`, declared to give a
                // `JavaFileObject`: a value path names the callable bare)
                let called_library = !l.declared
                    && matches!(self.family[f], Some(Family::Jvm))
                    && path.last().is_some_and(|s| !s.starts_with('[') && s != "()")
                    && DepthGuard::enter(&LIBRARY_DEPTH, 2).is_some_and(|_guard| {
                        let calling = self.calling.replace(true);
                        let held = self.resolve_path(f, scope(x), &path, false);
                        *self.calling.borrow_mut() = calling;
                        matches!(held, Some((Target::Def(c), _)) if self.gives_library_type(c))
                    });
                return called_library
                    || self.library_result(f, scope(x), &path)
                    || self.library_part(f, scope(x), &path);
            }
            cur = self.up(f, x);
        }
        false
    }

    /// The scope above `x` in a search for a local: its parent definition,
    /// then the file's own locals (a callback's variables, `name@byte`, seen
    /// from a function written inside it), then none.
    fn up(&self, f: usize, x: usize) -> Option<usize> {
        if x == FILE_SCOPE {
            None
        } else {
            self.files[f].defs[x].parent.or(Some(FILE_SCOPE))
        }
    }

    /// The parameter `name` of the callable around `d`, with that callable.
    fn param_of(
        &self,
        f: usize,
        d: Option<usize>,
        name: &str,
    ) -> Option<(&crate::facts::ParamFact, DefRef)> {
        let defs = &self.files[f].defs;
        let mut cur = d;
        while let Some(x) = cur {
            if defs[x].kind.is_callable() {
                // (a local of the name in between hides it)
                return defs[x].plist.iter().find(|p| p.name == name).map(|p| (p, (f, x)));
            }
            cur = defs[x].parent;
        }
        None
    }

    /// Kotlin: a call by the name of an extension the file sees (its own, one
    /// of its package, one it imports, a member extension of a type around)
    /// on a receiver the walk could not type is that extension: an extension
    /// from elsewhere cannot be called without an import
    /// (`declaredFields.asSequence().find { … }.toOptional()`,
    /// `user.email.isEmailValid()`). The receiver's written type, where the
    /// source states it, must be the extension's or a type parameter of it;
    /// a name Kotlin's own types answer to is theirs.
    fn visible_extension(
        &self,
        f: usize,
        d: Option<usize>,
        path: &[String],
        bases: bool,
    ) -> Option<(Target, Confidence)> {
        let lang = self.files[f].lang;
        if !matches!(lang, Some(crate::lang::Lang::Kotlin | crate::lang::Lang::CSharp))
            || bases
            || !*self.calling.borrow()
            || *self.types_only.borrow()
            || path.len() < 2
        {
            return None;
        }
        let name = path.last()?;
        let core = if lang == Some(crate::lang::Lang::CSharp) {
            CSHARP_CORE_METHODS
        } else {
            KOTLIN_CORE_METHODS
        };
        if name.starts_with('[') || name == "()" || core.contains(&name.as_str()) {
            return None;
        }
        // (a capital head that names nothing of the project is a library's
        // type, and the call its static member: `HttpUtility.UrlEncode(x)`,
        // never `string.UrlEncode` of the project)
        // (only the call made on the type itself: `Class.forName(…).kotlin
        // .toFileSpec()` calls it on what the chain gives)
        let head = path[0].as_str();
        if path.len() == 2
            && head.chars().next().is_some_and(char::is_uppercase)
            && !self.is_local_name(f, d, head)
            && self.resolve_name(f, d, head).is_none()
        {
            return None;
        }
        let _guard = DepthGuard::enter(&LIBRARY_DEPTH, 1)?;
        let on = self.written_receiver(f, d, &path[..path.len() - 1]);
        let found = self.extension_method(f, name, on.as_deref())?;
        if let (Some(on), (Target::Def(x), Confidence::Inferred)) = (&on, &found) {
            let def = self.def(*x);
            let fits = def
                .receiver
                .as_deref()
                .is_none_or(|r| receiver_matches(r, on) || def.type_params.iter().any(|t| t == r));
            if !fits {
                return None;
            }
        }
        Some(found)
    }

    /// The written type's last name of what a path holds, where the source
    /// states it: a field's or local's declared type, a call's written result.
    fn written_receiver(&self, f: usize, d: Option<usize>, prefix: &[String]) -> Option<String> {
        let (callee, called) = match prefix.split_last() {
            Some((last, rest)) if last == "()" => (rest, true),
            _ => (prefix, false),
        };
        if callee.is_empty() {
            return None;
        }
        if !called && callee.len() == 1 {
            let mut cur = local_start(d);
            while let Some(x) = cur {
                if let Some(&li) = self.locals.get(&(f, x, callee[0].clone())) {
                    let l = &self.files[f].locals[li];
                    return l.declared.then(|| l.path.last().cloned()).flatten();
                }
                cur = self.up(f, x);
            }
        }
        let calling = self.calling.replace(called);
        let held = self.resolve_path(f, d, callee, false);
        *self.calling.borrow_mut() = calling;
        let Some((Target::Def(c), _)) = held else { return None };
        let def = self.def(c);
        if called && def.kind.is_callable() {
            let written = def.wraps.clone().or_else(|| {
                self.files[c.0]
                    .refs
                    .iter()
                    .find(|x| {
                        x.from_def == Some(c.1)
                            && x.kind == RefKind::Type
                            && x.context == "return_type"
                    })
                    .map(|x| x.path.clone())
            });
            return written.and_then(|w| w.last().cloned());
        }
        if !called && def.kind.is_state() {
            return def.declared_type.as_ref().and_then(|t| t.last().cloned());
        }
        None
    }

    /// PHP: whether a written type is a library's: one a `use` names, or a
    /// class of the global namespace the project does not define (PHP's own,
    /// `\ReflectionProperty`, or an extension's).
    fn php_library_type(&self, g: usize, scope: Option<usize>, ty: &[String]) -> bool {
        let mut ty: Vec<String> = ty.to_vec();
        if let Some(h) = ty.first_mut() {
            *h = h.trim_start_matches('\\').to_string();
        }
        if ty.first().is_none_or(|h| h.is_empty() || h.starts_with('$')) {
            return false;
        }
        let Some(_guard) = DepthGuard::enter(&LIBRARY_DEPTH, 3) else { return false };
        let types = self.types_only.replace(true);
        let held = self.resolve_path(g, scope, &ty, true);
        *self.types_only.borrow_mut() = types;
        match held {
            Some((Target::Ext(..), _)) => true,
            Some(_) => false,
            None => {
                ty.len() == 1
                    && ty[0].chars().next().is_some_and(char::is_uppercase)
                    && !self.by_name.get(ty[0].as_str()).is_some_and(|ds| {
                        ds.iter().any(|x| {
                            self.family[x.0] == Some(Family::Php)
                                && self.def(*x).kind.is_class_like()
                        })
                    })
            }
        }
    }

    /// PHP: whether a local or field holds a library's value: declared with a
    /// library's type, built from one (`new \ReflectionProperty(…)`), assigned
    /// a parameter declared with one (`$this->request = $request;`), or what a
    /// project callable declared to give one returns.
    fn php_holds_library(&self, g: usize, scope: Option<usize>, l: &crate::facts::Local) -> bool {
        if l.path.is_empty() {
            return false;
        }
        if l.declared {
            return self.php_library_type(g, scope, &l.path);
        }
        if let [v] = l.path.as_slice() {
            if let Some(name) = v.strip_prefix('$') {
                let mut cur = local_start(scope);
                while let Some(x) = cur {
                    if let Some(&li) = self.locals.get(&(g, x, name.to_string())) {
                        let other = &self.files[g].locals[li];
                        return other.declared
                            && self.php_library_type(g, crate::resolve::scope(x), &other.path);
                    }
                    cur = self.up(g, x);
                }
                return false;
            }
        }
        self.php_library_type(g, scope, &l.path) || self.library_result(g, scope, &l.path)
    }

    /// PHP: whether what a call's path is made on is a library's value: a
    /// local (`$x`) or a field of the object (`$this->x`) that holds one.
    fn php_library_receiver(&self, f: usize, d: Option<usize>, path: &[String]) -> bool {
        if self.family[f] != Some(Family::Php) || path.len() < 2 {
            return false;
        }
        let Some(_guard) = DepthGuard::enter(&LIBRARY_DEPTH, 2) else { return false };
        let head = path[0].as_str();
        if let Some(name) = head.strip_prefix('$') {
            let mut cur = local_start(d);
            while let Some(x) = cur {
                if let Some(&li) = self.locals.get(&(f, x, name.to_string())) {
                    let l = &self.files[f].locals[li];
                    return self.php_holds_library(f, scope(x), l);
                }
                cur = self.up(f, x);
            }
            return false;
        }
        if head == "self" {
            let Some(c) = self.class_of(f, d) else { return false };
            if let Some((g, li)) = self.field_of(c, &path[1]) {
                let l = &self.files[g].locals[li];
                return self.php_holds_library(g, l.from_def, l);
            }
            return self.members(c, &path[1]).into_iter().any(|m| {
                let def = self.def(m);
                def.kind.is_state()
                    && def
                        .declared_type
                        .as_ref()
                        .is_some_and(|t| self.php_library_type(m.0, def.parent, t))
            });
        }
        false
    }

    /// PHP: whether a local the walk cannot type holds what a library's call
    /// gives: a method of a library's value (`$cacheMethod->invoke(…)`, a
    /// `ReflectionMethod`), or one a project type lacks and takes from its
    /// library base (`$tags->findOneBy(…)`, a Doctrine `ServiceEntityRepository`).
    fn php_library_value(&self, f: usize, d: Option<usize>, name: &str) -> bool {
        if self.family[f] != Some(Family::Php) {
            return false;
        }
        let Some(_guard) = DepthGuard::enter(&LIBRARY_DEPTH, 2) else { return false };
        let mut cur = local_start(d);
        let found = loop {
            let Some(x) = cur else { return false };
            if let Some(&li) = self.locals.get(&(f, x, name.to_string())) {
                break (x, li);
            }
            cur = self.up(f, x);
        };
        let (x, li) = found;
        let l = &self.files[f].locals[li];
        let mut p: &[String] = &l.path;
        while p.last().is_some_and(|s| s == "()") {
            p = &p[..p.len() - 1];
        }
        let [receiver @ .., method] = p else { return false };
        if receiver.is_empty() {
            return false;
        }
        let at = scope(x);
        if let [v] = receiver {
            if let Some(v) = v.strip_prefix('$') {
                let mut cur = local_start(at);
                while let Some(y) = cur {
                    if let Some(&vi) = self.locals.get(&(f, y, v.to_string())) {
                        let other = &self.files[f].locals[vi];
                        if self.php_holds_library(f, scope(y), other) {
                            return true;
                        }
                        break;
                    }
                    cur = self.up(f, y);
                }
            }
        }
        match self.resolve_path(f, at, receiver, true) {
            Some((Target::Ext(..), _)) => true,
            Some((Target::Def(t), _)) => {
                self.def(t).kind.is_class_like()
                    && self.has_outside_base(t)
                    && !self.answers(t, method, true, 4)
            }
            _ => false,
        }
    }

    /// PHP: whether a call in the middle of a path gives a library's value:
    /// a project callable declared to give one (`$mock->getLastRequest()`,
    /// `: ?RequestInterface`), an invokable object (`$handler(…)`, its
    /// `__invoke` declared to give a `PromiseInterface`), or a member the
    /// receiver's type lacks and only its subclasses declare, each to give a
    /// library's value (`RequestException::create(…)->getResponse()`).
    fn php_library_step(&self, f: usize, d: Option<usize>, path: &[String]) -> bool {
        if self.family[f] != Some(Family::Php) || path.len() < 3 {
            return false;
        }
        let Some(_guard) = DepthGuard::enter(&LIBRARY_DEPTH, 2) else { return false };
        let gives = |c: DefRef| -> bool {
            if self.def(c).kind.is_callable() {
                return self.gives_library_type(c);
            }
            let invoke = self.members(c, "__invoke");
            !invoke.is_empty() && invoke.into_iter().all(|m| self.gives_library_type(m))
        };
        for i in 1..path.len() - 1 {
            if path[i] != "()" {
                continue;
            }
            match self.resolve_path(f, d, &path[..i], true) {
                Some((Target::Def(c), _)) if gives(c) => return true,
                Some(_) => {}
                None => {
                    // the member a subclass declares
                    if i < 2 {
                        return false;
                    }
                    let Some((Target::Def(t), _)) = self.resolve_path(f, d, &path[..i - 1], true)
                    else {
                        return false;
                    };
                    let below = |p: DefRef| {
                        let mut seen = vec![p];
                        let mut k = 0;
                        while k < seen.len() && k < 16 {
                            for b in self.bases(seen[k]) {
                                if b == t {
                                    return true;
                                }
                                if !seen.contains(&b) {
                                    seen.push(b);
                                }
                            }
                            k += 1;
                        }
                        false
                    };
                    let held: Vec<DefRef> = self
                        .by_name
                        .get(path[i - 1].as_str())
                        .into_iter()
                        .flatten()
                        .copied()
                        .filter(|&m| {
                            self.family[m.0] == Some(Family::Php)
                                && self.def(m).kind.is_callable()
                                && self.def(m).parent.is_some_and(|p| below((m.0, p)))
                        })
                        .collect();
                    return !held.is_empty()
                        && held.into_iter().all(|m| self.gives_library_type(m));
                }
            }
        }
        false
    }

    /// Java: whether a written type is a library's: one an import names,
    /// a `java.lang` type the project does not define, or a qualified name
    /// whose head is no package or name of the project (`okhttp3.Response`).
    fn java_library_type(&self, g: usize, scope: Option<usize>, ty: &[String]) -> bool {
        let kotlin = self.files[g].lang == Some(crate::lang::Lang::Kotlin);
        let csharp = self.files[g].lang == Some(crate::lang::Lang::CSharp);
        if !(kotlin || csharp || self.files[g].lang == Some(crate::lang::Lang::Java))
            || ty.is_empty()
        {
            return false;
        }
        if csharp && ty.len() == 1 && CSHARP_PREDEFINED.contains(&ty[0].as_str()) {
            return true;
        }
        // (a type written, resolved as one: a local of the type's name is no
        // value here, and resolving it as one comes back to the locals)
        let Some(_guard) = DepthGuard::enter(&LIBRARY_DEPTH, 3) else { return false };
        let types = self.types_only.replace(true);
        let held = self.resolve_path(g, scope, ty, true);
        *self.types_only.borrow_mut() = types;
        match held {
            Some((Target::Ext(..), _)) => return true,
            Some(_) => return false,
            None => {}
        }
        // (C#: a namespace or type no import names is nothing to go by)
        if csharp {
            return false;
        }
        let head = ty[0].as_str();
        match self.resolve_name(g, scope, head) {
            Some(Target::Ext(..)) => true,
            Some(_) => false,
            None if ty.len() == 1 => {
                JAVA_LANG_TYPES.contains(&head) || (kotlin && KOTLIN_IMPLICIT_TYPES.contains(&head))
            }
            None => {
                head.chars().next().is_some_and(|c| c.is_lowercase())
                    && !self.pkg_files.keys().any(|(fam, pkg)| {
                        *fam == Family::Jvm && (pkg == head || pkg.starts_with(&format!("{head}.")))
                    })
            }
        }
    }

    /// Java: whether what a path is called on is a library's value: the
    /// head a field, a variable or a local declared with a library's type
    /// (`rawResponse.headers()` of an `okhttp3.Response`, `SET.indexOf(…)` of
    /// a `String`, `server.takeRequest()` of a `MockWebServer`).
    fn java_library_receiver(&self, f: usize, d: Option<usize>, path: &[String]) -> bool {
        let lang = self.files[f].lang;
        if !matches!(
            lang,
            Some(crate::lang::Lang::Java | crate::lang::Lang::Kotlin | crate::lang::Lang::CSharp)
        ) || path.len() < 2
        {
            return false;
        }
        let Some(_guard) = DepthGuard::enter(&LIBRARY_DEPTH, 2) else { return false };
        let head = &path[0];
        let mut cur = local_start(d);
        while let Some(x) = cur {
            if let Some(&li) = self.locals.get(&(f, x, head.clone())) {
                let l = &self.files[f].locals[li];
                if l.declared && self.java_library_type(f, scope(x), &l.path) {
                    return true;
                }
                // a method that gives the type parameter the local binds to
                // a library's type: `response.body()?.repeat(5)` of a
                // `Response<String>`
                if l.declared && path.len() >= 4 && path[2] == "()" {
                    let Some(c) = self.type_of_path(f, scope(x), &l.path, true) else {
                        return false;
                    };
                    let Some(m) = self.class_member(c, &path[1], 4) else { return false };
                    let written = self.files[m.0].refs.iter().find(|r| {
                        r.from_def == Some(m.1)
                            && r.kind == RefKind::Type
                            && r.context == "return_type"
                    });
                    let Some([t]) = written.map(|r| r.path.as_slice()) else { return false };
                    let at = self.def(c).type_params.iter().position(|p| p == t);
                    return at
                        .and_then(|i| l.targs.get(i))
                        .is_some_and(|arg| self.java_library_type(f, scope(x), arg));
                }
                return false;
            }
            cur = self.up(f, x);
        }
        match self.resolve_path(f, d, &path[..1], true) {
            Some((Target::Def(v), _)) if self.def(v).kind.is_state() => {
                let def = self.def(v);
                def.declared_type
                    .as_ref()
                    .is_some_and(|t| self.java_library_type(v.0, def.parent, t))
            }
            // a field beside a method of its name is no definition of its own
            // (one node per name in a type), but its declaration is kept:
            // `callAdapterFactories.indexOf(…)` of a `List<…>`
            _ => self.class_of(f, d).is_some_and(|c| {
                let held = self.fields.get(&c).and_then(|m| m.get(head.as_str()));
                held.is_some_and(|&(g, li)| {
                    let l = &self.files[g].locals[li];
                    l.declared && self.java_library_type(g, Some(c.1), &l.path)
                })
            }),
        }
    }

    /// Whether a step before a path's last name is a field or property
    /// declared with a library's type (`server.http.listen()`, `http:
    /// http.Server`): what follows is that library type's.
    fn library_field_step(&self, f: usize, d: Option<usize>, path: &[String]) -> bool {
        let Some(_guard) = DepthGuard::enter(&LIBRARY_DEPTH, 2) else { return false };
        for k in 2..path.len() {
            let seg = path[k - 1].as_str();
            if seg == "()" || seg.starts_with('[') {
                return false;
            }
            match self.resolve_path(f, d, &path[..k], true) {
                Some((Target::Def(v), _)) if self.def(v).kind.is_state() => {
                    let def = self.def(v);
                    let ty = def.declared_type.as_ref();
                    if ty.is_some_and(|t| {
                        matches!(
                            self.resolve_path(v.0, def.parent, t, true),
                            Some((Target::Ext(..), _))
                        )
                    }) {
                        return true;
                    }
                }
                Some((Target::Ext(..), _)) => return true,
                _ => return false,
            }
        }
        false
    }

    /// Rust: `std`, `core`, `alloc` or a crate a `Cargo.toml` depends on.
    fn rust_crate_name(&self, name: &str) -> bool {
        matches!(name, "std" | "core" | "alloc") || self.modsys.rs_dependencies.contains(name)
    }

    /// Rust: whether what a call's path is made on is a library's value:
    /// `self` in an `impl` for a library type (`impl UriExt for Uri`), a
    /// field or tuple field declared with a library type (`self.builder` an
    /// `http::request::Builder`, `self.0` a `&Response<B>`), or a local typed
    /// by an alias of one (`type Call<T> = ureq_proto::client::Call<T>`).
    fn rust_library_receiver(&self, f: usize, d: Option<usize>, path: &[String]) -> bool {
        let Some(_guard) = DepthGuard::enter(&LIBRARY_DEPTH, 2) else { return false };
        let ext = |g: usize, scope: Option<usize>, ty: &[String]| match self
            .resolve_path(g, scope, ty, true)
        {
            Some((Target::Ext(..), _)) => true,
            Some(_) => false,
            // (`ureq_proto::client::Call`, a crate used by its path)
            None => ty.len() >= 2 && self.rust_crate_name(&ty[0]),
        };
        let head = path[0].as_str();
        if head == "self" {
            let defs = &self.files[f].defs;
            let mut cur = d;
            while let Some(x) = cur {
                if defs[x].kind.is_class_like() {
                    break;
                }
                if let Some(owner) = &defs[x].owner {
                    let plain = owner.split('<').next().unwrap_or(owner).trim();
                    let bare = plain.rsplit(['.', ':']).next().unwrap_or(plain);
                    if self.type_named(f, bare).is_none()
                        && matches!(self.resolve_name(f, Some(x), bare), Some(Target::Ext(..)))
                    {
                        return true;
                    }
                    break;
                }
                cur = defs[x].parent;
            }
        }
        // a step before the name: a field or tuple field of a library type
        for k in 2..path.len() {
            let seg = path[k - 1].as_str();
            if seg == "()" || (seg.starts_with('[') && !seg.starts_with("[#")) {
                break;
            }
            if let Some(i) = seg.strip_prefix("[#").and_then(|x| x.strip_suffix(']')) {
                let Ok(i) = i.parse::<usize>() else { break };
                let holder = if k == 2 && head == "self" {
                    self.class_of(f, d)
                } else {
                    self.type_of_path(f, d, &path[..k - 1], false)
                };
                if let Some(t) = holder {
                    let def = self.def(t);
                    if def.rets.get(i).is_some_and(|r| ext(t.0, def.parent, r)) {
                        return true;
                    }
                }
                break;
            }
            match self.resolve_path(f, d, &path[..k], true) {
                Some((Target::Def(v), _)) if self.def(v).kind.is_state() => {
                    let def = self.def(v);
                    if def.declared_type.as_ref().is_some_and(|t| ext(v.0, def.parent, t)) {
                        return true;
                    }
                }
                Some((Target::Ext(..), _)) => return true,
                _ => break,
            }
        }
        // a local or closure parameter of a library's type (`|uri, req|` for
        // `Fn(Uri, Request<()>)`)
        if path.len() >= 2 && self.is_local_name(f, d, head) && self.is_external_local(f, d, head) {
            return true;
        }
        // a value of the function's own type parameter put through a std
        // conversion (`path.as_ref()` of `path: P` with `P: AsRef<Path>`), or
        // a parameter written with the language's own type (`uri: &str`)
        if path.len() >= 2 && !head.contains('@') {
            if let Some((p, c)) = self.param_of(f, d, head) {
                let def = self.def(c);
                let written = p.written.trim_start_matches('&').trim_start_matches("mut");
                let own = p.ty.len() == 1 && def.type_params.contains(&p.ty[0]);
                if own && matches!(path[1].as_str(), "as_ref" | "as_mut" | "into" | "borrow") {
                    return true;
                }
                let bare = written.split('<').next().unwrap_or("");
                // (not a type of the project's of that name: ureq's `Duration`)
                if RUST_OWN_TYPES.contains(&bare)
                    && !matches!(self.resolve_name(c.0, Some(c.1), bare), Some(Target::Def(_)))
                {
                    return true;
                }
            }
        }
        // a local typed by an alias of a library type
        if path.len() >= 2 {
            if let Some((t, _)) = self.local_type(f, d, head) {
                let def = self.def(t);
                if def.kind == DefKind::TypeAlias
                    && def.declared_type.as_ref().is_some_and(|ty| ext(t.0, def.parent, ty))
                {
                    return true;
                }
            }
        }
        false
    }

    /// Rust: a member of the project type `c` dereferences to (`impl Deref
    /// for AgentRef { type Target = Agent; }`), when `c` has none of its own.
    fn deref_member(&self, c: DefRef, name: &str) -> Option<DefRef> {
        if self.family[c.0] != Some(Family::Rust) {
            return None;
        }
        let cname = &self.def(c).name;
        let _guard = DepthGuard::enter(&LIBRARY_DEPTH, 2)?;
        for (g, facts) in self.files.iter().enumerate() {
            if self.family[g] != Some(Family::Rust) {
                continue;
            }
            for (ty, target) in &facts.derefs {
                if ty.last() != Some(cname) || self.type_of_path(g, None, ty, true) != Some(c) {
                    continue;
                }
                let Some(t) = self.type_of_path(g, None, target, true) else { continue };
                if t != c && self.def(t).kind.is_class_like() {
                    return self.class_member(t, name, 4);
                }
            }
        }
        None
    }

    /// Rust `x.body_mut()` on a value of a library's generic type holding one
    /// project type (`Response<Body>`, `TlsStream<ErrorCapture<…>>`): that
    /// type, and how many segments the value and the accessor call take. The
    /// value is a local or a field declared so, or the result of a project
    /// callable declared so (unwrapped).
    fn library_accessor(
        &self,
        f: usize,
        d: Option<usize>,
        path: &[String],
    ) -> Option<(DefRef, usize, Option<Binds>)> {
        let accessors = if self.family[f] == Some(Family::Go) {
            GO_LIBRARY_ACCESSORS
        } else {
            LIBRARY_ACCESSORS
        };
        let k = path.iter().position(|s| accessors.contains(&s.as_str()))?;
        // (it resolves a path itself, which can come back here)
        let _guard = DepthGuard::enter(&LIBRARY_DEPTH, 2)?;
        if k == 0 || path.get(k + 1).is_none_or(|s| s != "()") {
            return None;
        }
        let mut prefix: Vec<String> = path[..k].to_vec();
        let mut at = d;
        // what the value is declared with: (type, type arguments, where)
        let mut declared: Option<Declared> = None;
        if prefix.len() == 1 {
            let mut cur = local_start(d);
            while let Some(x) = cur {
                if let Some(&li) = self.locals.get(&(f, x, prefix[0].clone())) {
                    let l = &self.files[f].locals[li];
                    if l.declared {
                        declared = Some((l.path.clone(), l.targs.clone(), Vec::new(), f, scope(x)));
                    } else if !l.path.is_empty() && l.path[0] != prefix[0] {
                        // (`let res = crate::get(url).call().unwrap()`: its value)
                        prefix = l.path.clone();
                        at = scope(x);
                    }
                    break;
                }
                cur = self.up(f, x);
            }
        }
        let prefix = &prefix[..];
        let d = at;
        let declared = declared.or_else(|| {
            // a field, or the result of a call
            let mut end = prefix.len();
            while end > 0 {
                let seg = prefix[end - 1].as_str();
                if seg == "()"
                    || seg.starts_with('[')
                    || UNWRAPPERS.contains(&seg)
                    || seg == "clone"
                {
                    end -= 1;
                } else {
                    break;
                }
            }
            if end == 0 {
                return None;
            }
            let Some((Target::Def(c), _)) = self.resolve_path(f, d, &prefix[..end], true) else {
                return None;
            };
            let def = self.def(c);
            if def.kind.is_state() {
                return def
                    .declared_type
                    .clone()
                    .map(|t| (t, def.targs.clone(), def.inner_targs.clone(), c.0, def.parent));
            }
            if def.kind.is_callable() && end < prefix.len() {
                return def
                    .wraps
                    .clone()
                    .map(|t| (t, def.targs.clone(), def.inner_targs.clone(), c.0, def.parent));
            }
            None
        });
        let (ty, targs, inner, g, scope) = declared?;
        // the type is a library's, and holds one type of the project
        if !matches!(self.resolve_path(g, scope, &ty, true), Some((Target::Ext(..), _))) {
            return None;
        }
        let held: Vec<(usize, DefRef)> = targs
            .iter()
            .enumerate()
            .filter_map(|(i, a)| self.type_of_path(g, scope, a, true).map(|t| (i, t)))
            .filter(|(_, t)| self.def(*t).kind.is_class_like())
            .collect();
        match held.as_slice() {
            // (`TlsStream<ErrorCapture<TransportAdapter>>`: what the held
            // type's own parameters stand for)
            [(i, one)] => {
                let binds =
                    inner.get(*i).and_then(|args| self.binds_for(*one, args, &[], g, scope, None));
                Some((*one, k + 2, binds))
            }
            _ => None,
        }
    }

    /// Rust: whether a value path ends at a numbered part of a project type
    /// declared with a library's type (`BodyInner::ByteVec(v)`, the variant
    /// `ByteVec(io::Cursor<Vec<u8>>)`; `self.0` of `struct D(&Response)`).
    fn library_part(&self, f: usize, d: Option<usize>, path: &[String]) -> bool {
        if self.family[f] != Some(Family::Rust) {
            return false;
        }
        let Some((last, of)) = path.split_last() else { return false };
        let Some(i) = crate::refs::tuple_index(last) else { return false };
        if of.is_empty() {
            return false;
        }
        let Some(_guard) = DepthGuard::enter(&LIBRARY_DEPTH, 2) else { return false };
        let Some((Target::Def(c), _)) = self.resolve_path(f, d, of, true) else { return false };
        let def = self.def(c);
        if !(def.kind.is_class_like() || def.kind == DefKind::EnumMember) {
            return false;
        }
        let Some(ty) = def.rets.get(i).filter(|t| !t.is_empty()) else { return false };
        match self.resolve_path(c.0, def.parent, ty, true) {
            Some((Target::Ext(..), _)) => true,
            Some(_) => false,
            None => ty.len() >= 2 && self.rust_crate_name(&ty[0]),
        }
    }

    /// Whether a value path ends at a call of a project callable declared to
    /// give a library's type (what it wraps, or its written result), past
    /// the unwrapping (`.unwrap()`, `?`, `.clone()`) that keeps it.
    fn library_result(&self, f: usize, d: Option<usize>, path: &[String]) -> bool {
        let Some(_guard) = DepthGuard::enter(&LIBRARY_DEPTH, 2) else { return false };
        let mut end = path.len();
        while end > 0 {
            let seg = path[end - 1].as_str();
            if seg == "()" || seg.starts_with('[') || UNWRAPPERS.contains(&seg) || seg == "clone" {
                end -= 1;
            } else {
                break;
            }
        }
        if end == 0 || end == path.len() {
            return false;
        }
        let Some((Target::Def(c), _)) = self.resolve_path(f, d, &path[..end], true) else {
            return false;
        };
        self.gives_library_type(c)
    }

    /// Whether a callable's written result is a library's type (`func room(id
    /// string) broadcast.Broadcaster`).
    fn gives_library_type(&self, c: DefRef) -> bool {
        let def = self.def(c);
        if !def.kind.is_callable() {
            return false;
        }
        let written = def.wraps.clone().or_else(|| {
            self.files[c.0]
                .refs
                .iter()
                .find(|x| {
                    x.from_def == Some(c.1) && x.kind == RefKind::Type && x.context == "return_type"
                })
                .map(|x| x.path.clone())
        });
        written.is_some_and(|w| {
            matches!(self.resolve_path(c.0, def.parent, &w, true), Some((Target::Ext(..), _)))
        })
    }

    /// A local that does not hide the outer name: a plain assignment where
    /// assigning declares nothing, or, for a bare call (`called`), a local
    /// with a written type (`baseUrl(…)` beside `String baseUrl` calls the
    /// method, not the string) or one assigned from the outer thing of its own
    /// name (`let agent = agent();`: the call is the function's; a longer
    /// initializer is `own_initializer`'s).
    fn local_keeps_outer(&self, f: usize, d: Option<usize>, name: &str, called: bool) -> bool {
        let mut cur = local_start(d);
        while let Some(x) = cur {
            if let Some(&li) = self.locals.get(&(f, x, name.to_string())) {
                let l = &self.files[f].locals[li];
                // (Rust: a local and a function share one namespace, so a
                // declared local called is the local, `current_time()` of a
                // `current_time: Arc<dyn Fn() -> Instant>`; Go the same: `m
                // := mx` walks `mx` as the value it calls)
                let rust = matches!(self.family[f], Some(Family::Rust | Family::Go));
                // (a file's own local stands for every callback's of the name:
                // a bare call is the function the file defines, `u()` beside a
                // `var u` of another callback in a minified bundle)
                return l.assigned
                    || (called
                        && (x == FILE_SCOPE
                            || (l.declared && !rust)
                            || l.path.first().is_some_and(|h| h == name)));
            }
            cur = self.up(f, x);
        }
        false
    }

    /// Whether `path` is the value a local named like its head is declared
    /// with (`val builder = builder(x).tag(y)`): the declaration does not
    /// see itself, so there the head is the outer name.
    fn own_initializer(&self, f: usize, d: Option<usize>, path: &[String]) -> bool {
        let Some(name) = path.first() else { return false };
        let mut cur = local_start(d);
        while let Some(x) = cur {
            if let Some(&li) = self.locals.get(&(f, x, name.clone())) {
                let l = &self.files[f].locals[li];
                // the whole value, or a part of its chain (`tls::Config::builder()`
                // in `let tls = tls::Config::builder().build()`)
                return l.path.len() > 1
                    && l.path[0] == *name
                    && (path.starts_with(&l.path) || (path.len() > 1 && l.path.starts_with(path)));
            }
            cur = self.up(f, x);
        }
        false
    }

    /// A "local" recorded from a plain assignment that declares nothing.
    fn local_is_hint(&self, f: usize, d: Option<usize>, name: &str) -> bool {
        let mut cur = local_start(d);
        while let Some(x) = cur {
            if let Some(&li) = self.locals.get(&(f, x, name.to_string())) {
                return self.files[f].locals[li].assigned;
            }
            cur = self.up(f, x);
        }
        false
    }

    /// Whether a name is a parameter or local of the enclosing definitions.
    fn is_local_name(&self, f: usize, d: Option<usize>, name: &str) -> bool {
        let mut cur = local_start(d);
        while let Some(x) = cur {
            if self.locals.contains_key(&(f, x, name.to_string())) {
                return true;
            }
            cur = self.up(f, x);
        }
        false
    }

    /// The other declarations of a C# `partial` type: the definitions of its
    /// fully qualified name with as many type parameters (`RestResponse<T>`
    /// beside `RestResponse` is another type).
    fn partials(&self, c: DefRef) -> Vec<DefRef> {
        let def = self.def(c);
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

    /// The field `name` of type `c`, declared on it or on another declaration
    /// of a partial type.
    fn field_of(&self, c: DefRef, name: &str) -> Option<(usize, usize)> {
        let own = |t: &DefRef| self.fields.get(t).and_then(|m| m.get(name)).copied();
        own(&c).or_else(|| self.partials(c).iter().find_map(own))
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
                    s.bindings.entry(name).or_insert((bind, i));
                }
                if imp.form == ImportForm::Wildcard || modules::is_star(fam, imp) {
                    s.stars.push((t, i));
                }
            }
            s.modules.push(res);
        }
        // C#: the namespaces the project makes every file see (its manifest's
        // `<Using>` items, the SDK's implicit usings, `global using`), as
        // `using`s of the file's own; no import edge is written for them
        if fam == Family::CSharp {
            let own: Vec<&str> = self.files[f].imports.iter().map(|i| i.module.as_str()).collect();
            for ns in self.modsys.cs_usings(&self.files[f].path) {
                if own.contains(&ns.as_str()) {
                    continue;
                }
                let mut imp = self.files[f].imports.first().cloned().unwrap_or_else(|| {
                    crate::facts::ImportFact {
                        line: 0,
                        form: ImportForm::Wildcard,
                        module: String::new(),
                        names: Vec::new(),
                        alias: None,
                        runs: crate::facts::Runs::ModuleLevel,
                        is_static: false,
                        system: false,
                        public: false,
                        global: false,
                        byte: 0,
                    }
                });
                imp.form = ImportForm::Wildcard;
                imp.module = ns;
                imp.alias = None;
                imp.is_static = false;
                imp.names.clear();
                if let ModRes::Target(t) = modules::resolve_import(self, fam, f, &imp) {
                    s.stars.push((t, usize::MAX));
                }
            }
        }
        s
    }

    // ---- lookups ---------------------------------------------------------

    /// The best of several definitions: one with a body over a prototype.
    fn best(&self, cands: impl IntoIterator<Item = DefRef>) -> Option<DefRef> {
        let mut v: Vec<DefRef> = cands.into_iter().collect();
        v.sort_by_key(|r| (self.def(*r).decl_only, *r));
        v.dedup();
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
        self.best(
            files
                .iter()
                .flat_map(|&f| self.top[f].get(name).into_iter().flatten().map(move |&d| (f, d)))
                // (an RSpec `let` is a name of its block only)
                .filter(|r| {
                    self.def(*r).kind != DefKind::Extension && self.def(*r).scope_lines.is_none()
                }),
        )
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
            Some(Family::Rust) => self.modsys.crate_of(c.0) == self.modsys.crate_of(m.0),
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
        if let Some(b) = self.bases_memo.borrow().get(&c) {
            return b.clone();
        }
        self.bases_memo.borrow_mut().insert(c, Vec::new()); // a cycle ends here
        let mut out = self.written_bases(c);
        // a C# partial type has the bases any of its declarations writes
        for p in self.partials(c) {
            for b in self.written_bases(p) {
                if b != c && !out.contains(&b) {
                    out.push(b);
                }
            }
        }
        self.bases_memo.borrow_mut().insert(c, out.clone());
        out
    }

    /// Whether a type writes a base the project does not define: its members
    /// are not all the project's.
    /// Kotlin `object : SimpleTypeVisitor8<…>() { … super.visitUnknown(…) }`
    /// written in a function: the anonymous object is no definition, and the
    /// base it writes is the function's; whether such a base, up to the type
    /// around, is one the project does not define.
    fn anonymous_outside_base(&self, f: usize, d: Option<usize>) -> bool {
        let defs = &self.files[f].defs;
        let mut cur = d;
        while let Some(x) = cur.filter(|&x| defs[x].kind.is_callable()) {
            let outside = self.files[f].refs.iter().any(|r| {
                r.from_def == Some(x)
                    && matches!(r.kind, RefKind::Inherits | RefKind::Implements | RefKind::Base)
                    && !matches!(
                        self.resolve_path(f, defs[x].parent, &r.path, true),
                        Some((Target::Def(_), _))
                    )
            });
            if outside {
                return true;
            }
            cur = defs[x].parent;
        }
        false
    }

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
        let mut out = Vec::new();
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
            if let Some((Target::Def(b), _)) = self.resolve_path(c.0, outer, &r.path, true) {
                if b != c {
                    out.push(b);
                } else if let Some(other) = self.namesake(c) {
                    // C# `RestResponse<T> : RestResponse`: the base is the
                    // other type of the name, not the class itself
                    out.push(other);
                }
            }
        }
        out
    }

    /// A member of a type, through its bases.
    fn class_member(&self, c: DefRef, name: &str, depth: usize) -> Option<DefRef> {
        if let Some(m) = self.best(self.members(c, name)) {
            return Some(m);
        }
        if depth == 0 {
            return None;
        }
        self.bases(c).into_iter().find_map(|b| self.class_member(b, name, depth - 1))
    }

    /// The constructors a type declares, in source order.
    fn constructors(&self, c: DefRef) -> Vec<DefRef> {
        let full = &self.def(c).name;
        // (a C++ specialization's constructors carry the template's name)
        let cname = &specialized(full).unwrap_or(full).to_string();
        let mut out: Vec<DefRef> = self.children[c.0]
            .get(&c.1)
            .into_iter()
            .flat_map(|m| m.values().flatten())
            .map(|&d| (c.0, d))
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

    /// `name` looked up inside a target.
    /// Go: an embedded struct is a field named after its type (`cb.base` of
    /// `type Breaker struct { base }`), which is that type.
    fn embedded_field(&self, c: DefRef, name: &str) -> Option<DefRef> {
        if self.family[c.0] != Some(Family::Go) {
            return None;
        }
        self.bases(c).into_iter().find(|b| self.def(*b).name == name)
    }

    fn member(&self, t: &Target, name: &str, depth: usize) -> Option<Target> {
        match t {
            Target::Def(c) => {
                let d = self.def(*c);
                if d.kind == DefKind::Namespace {
                    return self.best(self.namespace_members(*c, name)).map(Target::Def);
                }
                if name == "new" && self.family[c.0] == Some(Family::Ruby) {
                    return Some(Target::Def(*c)); // `Klass.new` constructs Klass
                }
                self.class_member(*c, name, 4)
                    .or_else(|| self.reopened_member(*c, name))
                    .or_else(|| self.deref_member(*c, name))
                    .or_else(|| self.embedded_field(*c, name))
                    // an enum entry is a value of its enum: `Kind.CLASS.keyword`
                    // is `Kind`'s (an entry with a body of its own answers first)
                    .or_else(|| {
                        let parent = d.parent.map(|x| (c.0, x))?;
                        (d.kind == DefKind::EnumMember && self.def(parent).kind.is_class_like())
                            .then(|| self.class_member(parent, name, 4))
                            .flatten()
                    })
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
                let fam = self.namespaces.iter().find(|(_, n)| *n == full).map(|(f, _)| *f)?;
                let _ = fam;
                Some(Target::Ns(full))
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
        // enclosing definitions: nested functions; class members where
        // methods see them
        let mut cur = d;
        while let Some(x) = cur {
            if let Some(k) = self.children[f].get(&x).and_then(|m| m.get(name)) {
                let callable_scope = defs[x].kind.is_callable();
                let member_scope = fam.implicit_this() && defs[x].kind.is_class_like();
                if callable_scope || member_scope || defs[x].kind == DefKind::Namespace {
                    if let Some(r) = self.best(k.iter().map(|&i| (f, i))) {
                        if self.acceptable(&Target::Def(r)) {
                            return Some(Target::Def(r));
                        }
                    }
                }
            }
            if fam.implicit_this() && defs[x].kind.is_class_like() {
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
        if let Some(r) = self.top_in(&[f], name) {
            if self.acceptable(&Target::Def(r)) {
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
        for facts in self.files {
            for path in &facts.spec_includes {
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
        // a module called, constructed or named as a type is what it exports
        // as itself (`const express = require('../'); express()`)
        if let Some((Target::Module(fs), conf)) = &found {
            if *self.calling.borrow() || *self.types_only.borrow() || bases {
                if let Some(t) = self.module_default(fs, 3) {
                    return Some((t, *conf));
                }
            }
        }
        found.or_else(|| {
            // Kotlin: what the walk cannot type, called by the name of an
            // extension the file sees, is that extension
            if let Some(found) = self.visible_extension(f, d, path, bases) {
                return Some(found);
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
            // `let request = request.with_agent(a)`: the name before the
            // declaration is one the facts no longer hold (a parameter it
            // shadows); only the method's name says what is called
            (path.len() == 2
                && !bases
                && *self.calling.borrow()
                && self.own_initializer(f, d, path))
            .then(|| self.unknown_receiver(f, &path[1], None))
            .flatten()
        })
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
        // Rust: what a library's generic type gives through an accessor of
        // the one project type it holds (`res.body_mut()` of a
        // `Response<Body>`, `stream.get_mut()` of a `TlsStream<ErrorCapture>`)
        if fam == Family::Go && !bases {
            if let Some((t, skip, binds)) = self.library_accessor(f, d, path) {
                let rest = &path[skip..];
                if rest.is_empty() {
                    return None;
                }
                return self.walk_bound(t, binds, rest, Confidence::Inferred);
            }
        }
        if fam == Family::Rust && !bases {
            // a path into a crate the project depends on (`serde_json::from_reader`)
            // names that crate's item: no guess by its last name
            if path.len() >= 2
                && self.rust_crate_name(&path[0])
                && !self.is_local_name(f, d, &path[0])
                && self.resolve_name(f, d, &path[0]).is_none()
            {
                return None;
            }
            if let Some((t, skip, binds)) = self.library_accessor(f, d, path) {
                let rest = &path[skip..];
                if rest.is_empty() {
                    return None;
                }
                return self.walk_bound(t, binds, rest, Confidence::Inferred);
            }
        }
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
            // `test.before()` of `const test = Object.assign(runTest, {
            // before() {} })`: the member the literal it holds defines
            if !self.is_local_name(f, d, head) {
                // (its own file or one this file imports, never a namesake
                // found by directory: bootstrap.js beside bootstrap.esm.js)
                let in_sight = |v: DefRef| {
                    v.0 == f || self.scopes.get(f).is_some_and(|s| s.imported.contains(&v.0))
                };
                if let Some(Target::Def(v)) = self.resolve_name(f, d, head).filter(|t| match t {
                    Target::Def(v) => in_sight(*v),
                    _ => false,
                }) {
                    if let Some(m) = self.held_object_member(v, &rest[0]) {
                        return self.walk(Target::Def(m), &rest[1..], Confidence::Extracted);
                    }
                }
            }
            // `const captured = captureRequest()`, `captured.contentType()`:
            // a member of the object literal the function returns
            if !own_init {
                if let Some(m) = self.returned_member(f, d, head, &rest[0]) {
                    return self.walk(Target::Def(m), &rest[1..], Confidence::Inferred);
                }
            }
        }
        let mut t = match head.as_str() {
            _ if head_many.is_some() => {
                let (elem, c) = head_many?;
                conf = c;
                many = true;
                keyed = self.local_keyed(f, d, head);
                keys = keyed.then(|| self.local_key(f, d, head)).flatten();
                Target::Def(elem)
            }
            "self" => {
                if rest.is_empty() {
                    return None;
                }
                // Ruby `self.class.from(x)`: a method of the class itself
                if fam == Family::Ruby && rest.len() > 1 && rest[0] == "class" {
                    let c = self.class_of(f, d)?;
                    let skip = if rest.get(1).is_some_and(|s| s == "()") { 2 } else { 1 };
                    return self.walk(Target::Def(c), &rest[skip..], conf);
                }
                // Kotlin `x.apply { this.m() }`: `this` is the innermost
                // receiver the walk can type, before the class around
                if fam == Family::Jvm {
                    let recv = *self.recv.borrow();
                    if let Some((file, at)) = recv.filter(|(file, _)| *file == f) {
                        let _ = file;
                        if let Some((t, cf)) = self.receiver_type(f, d, at) {
                            conf = conf.max(cf);
                            return self.walk(Target::Def(t), rest, conf);
                        }
                    }
                }
                match self.class_of(f, d) {
                    // Rust `Self::Result` where the type has one `type Result`
                    // per `impl` (`impl Handler<A> for DbExecutor`, `impl
                    // Handler<B> for …`): the one of this method's block, the
                    // nearest before it in the file
                    Some(c) if fam == Family::Rust && d.is_some() => {
                        match self.assoc_type_near(c, &rest[0], f, d) {
                            Some(a) => return self.walk(Target::Def(a), &rest[1..], conf),
                            None => Target::Def(c),
                        }
                    }
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
                let t = modules::rust_parent(self, f)?;
                return self.walk(t, rest, conf);
            }
            "super" => {
                let c = self.class_of(f, d)?;
                // Python `super().m`: the call adds nothing
                let rest = if rest.first().is_some_and(|s| s == "()") { &rest[1..] } else { rest };
                let name = rest.first()?;
                let m = self.bases(c).into_iter().find_map(|b| self.class_member(b, name, 4))?;
                return self.walk(Target::Def(m), &rest[1..], conf);
            }
            "crate" if fam == Family::Rust => modules::crate_root(self, f)?,
            // Rust `().chain(next)`: what a trait the project implements for
            // the unit type gives, when only one of them has the name
            crate::refs::UNIT if fam == Family::Rust => {
                let name = rest.first()?;
                let mut found: Vec<DefRef> = Vec::new();
                for (g, file) in self.files.iter().enumerate() {
                    if self.family[g] != Some(Family::Rust) {
                        continue;
                    }
                    for r in &file.refs {
                        if !matches!(r.kind, RefKind::Implements | RefKind::Base)
                            || r.from_owner.as_deref() != Some("()")
                        {
                            continue;
                        }
                        let Some(tr) = self.type_of_path(g, None, &r.path, true) else { continue };
                        if let Some(m) = self.class_member(tr, name, 4) {
                            if !found.contains(&m) {
                                found.push(m);
                            }
                        }
                    }
                }
                let [m] = found.as_slice() else { return None };
                return self.walk_bound(*m, None, &rest[1..], conf);
            }
            "()" => {
                // a receiver the walk could not name: a method by name alone
                if rest.len() != 1 || bases {
                    return None;
                }
                return self.unknown_receiver(f, &rest[0], None);
            }
            // PHP `$x`: a variable. Its declared or assigned type, else only
            // a distinctive method name
            v if v.starts_with('$') => match self.local_type(f, d, &v[1..]) {
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
            _ if !own_init
                && (self.is_untyped_local(f, d, head) || self.is_external_local(f, d, head))
                && (*self.types_only.borrow() || self.local_type(f, d, head).is_none())
                // (Java, C#: methods and variables are named apart, and a bare
                // call beside a typed local is the method: `baseUrl(…)` beside
                // `String baseUrl`)
                && !(rest.is_empty()
                    && matches!(fam, Family::Jvm | Family::CSharp)
                    && self.local_keeps_outer(f, d, head, *self.calling.borrow())) =>
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
                // an extension called on a local of a library type, with more
                // after it: on through what the extension gives
                // (`String::class.asClassName().constructorReference()`)
                if rest.len() >= 3 && rest[1] == "()" && self.is_external_local(f, d, head) {
                    // (it types what the extension returns, which can come back
                    // here)
                    let _guard = DepthGuard::enter(&LIBRARY_DEPTH, 2)?;
                    let on = self.local_written_type(f, d, head);
                    let calling = self.calling.replace(true);
                    let ext = self.extension_method(f, &rest[0], on.as_deref());
                    *self.calling.borrow_mut() = calling;
                    if let Some((Target::Def(x), cf)) = ext {
                        if self.def(x).receiver.is_some() {
                            let (r, declared) = self.return_type(x)?;
                            let cf = if declared { cf } else { cf.max(Confidence::Inferred) };
                            let conf = conf.max(cf);
                            if let Some(found) = self.walk(Target::Def(r), &rest[2..], conf) {
                                return Some(found);
                            }
                            // (or an extension of what it gives:
                            // `Int::class.asTypeName().jvmWildcard()`)
                            if rest.len() == 3 {
                                return self
                                    .extension_for(f, &rest[2], r)
                                    .map(|(t, c)| (t, conf.max(c)));
                            }
                            return None;
                        }
                    }
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
                        // (a property of the name beside it: Kotlin `var
                        // primaryConstructor: FunSpec?` beside `fun
                        // primaryConstructor(…)`, `primaryConstructor?.parameter(n)`)
                        if fam == Family::Jvm {
                            let prop = self.class_of(f, d).and_then(|c| {
                                self.members(c, head)
                                    .into_iter()
                                    .find(|m| self.def(*m).kind.is_state())
                            });
                            if let Some(p) = prop {
                                return Some(Target::Def(p));
                            }
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
                // PHP `Handler\HostValidator::check()` in `namespace
                // GuzzleHttp;`: a namespace block of the head's name (`namespace
                // GuzzleHttp\Handler { … }` in a bootstrap file) holds only its
                // own block; the name is the file's namespace's first
                Some(Target::Def(c))
                    if fam == Family::Php
                        && !rest.is_empty()
                        && self.def(c).kind == DefKind::Namespace
                        && self.qualified(f, d, path).is_some() =>
                {
                    return self.qualified(f, d, path);
                }
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
        // a constructor named with a member after it is its class
        // (`FakeCall.<Error>sneakyThrow(e)` inside `FakeCall`)
        if let Target::Def(m) = t {
            let def = self.def(m);
            if def.kind == DefKind::Constructor
                && def.name == *head
                && rest.first().is_some_and(|s| s != "()")
            {
                if let Some(c) =
                    def.parent.map(|x| (m.0, x)).filter(|c| self.def(*c).kind.is_class_like())
                {
                    t = Target::Def(c);
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
                    .and_then(|(args, elems, x)| self.binds_for(c, args, elems, f, scope(x), None))
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
        for (i, seg) in rest.iter().enumerate() {
            if skip > 0 {
                skip -= 1;
                if skip == 0 && opened_after(i + 1) {
                    sealed = false;
                }
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
                // Rust `self.0` on `struct Script(Arc<Mutex<State>>)`, and
                // what `BodyInner::Mine(h)` binds (a tuple variant's field)
                if def.kind.is_class_like() || def.kind == DefKind::EnumMember {
                    // (`self.0()` of a `struct CurrentTime(Arc<dyn Fn() ->
                    // Instant>)`: a function called, which constructs nothing)
                    if i + 1 == rest.len() && self.linking_call() && fam == Family::Rust {
                        return None;
                    }
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
            if seg == "()" {
                // the result of a call: constructing a type gives one of it;
                // anything else returns what the walk cannot know
                if let Target::Def(c) = t {
                    if self.def(c).kind.is_class_like() {
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
                            // (`-> Result<Option<T>, E>` opened by `?`: what
                            // it gave may be another, `?.unwrap()`)
                            if self.def(c).sealed && opened_after(i + 1) {
                                unwrapping = true;
                            }
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
                // (what a project function gives is a library's value: no
                // guess by the name called on it, `room(id).Submit(post)`)
                // (Java, Kotlin: or a type parameter the receiver binds to a
                // library's type, `response.body()?.repeat(5)` of a
                // `Response<String>`)
                let library = matches!(t, Target::Def(c) if fam == Family::Go
                    && self.gives_library_type(c))
                    || (fam == Family::Jvm && self.java_library_receiver(f, d, path));
                if rest.len() == i + 2 && !bases && !library {
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
            // Rust `x.clone()`, `x.to_owned()` on a type that defines no such
            // method: the std trait's, which gives the same type; and a
            // wrapper's opening (`pool.upgrade()`, `lock().unwrap()`) on what
            // the wrapper was typed as (a value being typed only: as the call
            // linked, it is the library's)
            if found.is_none()
                && fam == Family::Rust
                && (matches!(seg.as_str(), "clone" | "to_owned")
                    || UNWRAPPERS.contains(&seg.as_str()))
                && !(i + 1 == rest.len() && self.linking_call())
            {
                if let Target::Def(c) = t {
                    if self.def(c).kind.is_class_like() {
                        skip = usize::from(rest.get(i + 1).is_some_and(|s| s == "()"));
                        continue;
                    }
                }
            }
            // Rust, C, C++, Java: fields and methods are named apart, and a
            // name with no call after it is the field (`self.input.unconsumed()`
            // beside `fn input(&self)`)
            if let (Some(Target::Def(m)), Target::Def(c)) = (&found, &t) {
                // (a call's own path has no `()` after its last name)
                let called = rest.get(i + 1).is_some_and(|s| s == "()")
                    || (i + 1 == rest.len() && *self.calling.borrow());
                if !called
                    && self.def(*m).kind.is_callable()
                    && matches!(fam, Family::Rust | Family::C | Family::Jvm)
                    && self.def(*c).kind.is_class_like()
                {
                    let field =
                        self.members(*c, seg).into_iter().find(|x| self.def(*x).kind.is_state());
                    if let Some(x) = field {
                        found = Some(Target::Def(x));
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
                if fam == Family::Python
                    && self.def(m).kind.is_callable()
                    && next.is_some_and(|s| s != "()" && !s.starts_with('['))
                    && self.is_property(m)
                {
                    if let Some((r, declared)) = self.return_type(m) {
                        if !declared {
                            conf = conf.max(Confidence::Inferred);
                        }
                        found = Some(Target::Def(r));
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
                    if let Some((ft, cf)) = self.field_type(c, seg, 3).or_else(declared) {
                        conf = conf.max(cf);
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
                    // Kotlin `x.apply { … }` / `x.also { … }` give `x` back
                    // (a type with members of those names answers above)
                    if fam == Family::Jvm
                        && matches!(seg.as_str(), "apply" | "also")
                        && rest.get(i + 1).is_some_and(|s| s == "()")
                        && i + 2 < rest.len()
                        && self.def(c).kind.is_class_like()
                    {
                        skip = 1;
                        continue;
                    }
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
                    // (also while a value is typed, which asks for types:
                    // `val best = ClassName(…).member("x")`)
                    let extensions = !bases || *self.calling.borrow();
                    if self.def(c).kind.is_class_like() && i + 1 == rest.len() && extensions {
                        return self.extension_for(f, seg, c);
                    }
                    // one called in the middle of a chain: the `()` after it
                    // gives what it returns (`PropertySpec.builder(…)
                    // .addOriginatingKSFile(f).mutable(…)`)
                    if self.def(c).kind.is_class_like()
                        && rest.get(i + 1).is_some_and(|s| s == "()")
                        && extensions
                    {
                        let calling = self.calling.replace(true);
                        let ext = self.extension_for(f, seg, c);
                        *self.calling.borrow_mut() = calling;
                        if let Some((Target::Def(x), cf)) = ext {
                            if self.def(x).receiver.is_some() {
                                conf = conf.max(cf);
                                t = Target::Def(x);
                                continue;
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
        let mut cur = local_start(d);
        let (x, li) = loop {
            let x = cur?;
            if let Some(&li) = self.locals.get(&(f, x, name.to_string())) {
                break (x, li);
            }
            cur = self.up(f, x);
        };
        let l = &self.files[f].locals[li];
        if l.declared || l.path.is_empty() || l.path.last().is_some_and(|s| s.starts_with('[')) {
            return None;
        }
        if let Some(known) = self.value_binds.borrow().get(&(f, li)) {
            return known.clone();
        }
        let whole = self.typing.borrow().is_empty() && *self.depth.borrow() == 0;
        if *self.depth.borrow() > 3 || !self.typing.borrow_mut().insert((f, li)) {
            return None;
        }
        *self.depth.borrow_mut() += 1;
        let recv = self.recv.replace(None);
        let types = self.types_only.replace(false);
        self.end_binds.replace(None);
        let value = self.resolve_path(f, scope(x), &l.path, true);
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
                        .or_else(|| self.binds_from_args(c, r, l, f, x))
                })
            }
            _ => None,
        };
        if whole {
            self.value_binds.borrow_mut().insert((f, li), out.clone());
        }
        out
    }

    /// The bindings a constructor's arguments give its class's type
    /// parameters (`fn wrap(stream: S) -> Self` in `ErrorCapture<S>`, called
    /// as `ErrorCapture::wrap(TransportAdapter::new())`): a parameter written
    /// as the class's own parameter takes the type of what is passed.
    fn binds_from_args(
        &self,
        c: DefRef,
        owner: DefRef,
        l: &crate::facts::Local,
        f: usize,
        x: usize,
    ) -> Option<Binds> {
        let params = &self.def(owner).type_params;
        if params.is_empty() || l.value_args.is_empty() {
            return None;
        }
        let def = self.def(c);
        let mut any = false;
        let args = params
            .iter()
            .map(|tp| {
                let passed = def
                    .plist
                    .iter()
                    .zip(&l.value_args)
                    .take_while(|(_, a)| a.label.is_none())
                    .find(|(p, _)| p.ty.len() == 1 && p.ty[0] == *tp)
                    .and_then(|(_, a)| match &a.kind {
                        crate::facts::Arg::Path(q) => self.value_type_of(f, scope(x), q),
                        _ => None,
                    })
                    .filter(|t| self.def(*t).kind.is_class_like());
                match passed {
                    Some(t) => {
                        any = true;
                        let td = self.def(t);
                        BoundArg {
                            path: vec![td.name.clone()],
                            elem: None,
                            file: t.0,
                            scope: td.parent,
                        }
                    }
                    None => BoundArg { path: Vec::new(), elem: None, file: f, scope: scope(x) },
                }
            })
            .collect();
        any.then_some(Binds { owner, args })
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
        if def.type_params.contains(name) {
            return None;
        }
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
        let mut cur = local_start(d);
        while let Some(x) = cur {
            if let Some(&li) = self.locals.get(&(f, x, name.to_string())) {
                let l = &self.files[f].locals[li];
                return (!l.targs.is_empty()).then_some((&l.targs[..], &l.targ_elems[..], x));
            }
            cur = self.up(f, x);
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
        let mut cur = local_start(d);
        let (x, li) = loop {
            let x = cur?;
            if let Some(&li) = self.locals.get(&(f, x, head.clone())) {
                break (x, li);
            }
            cur = self.up(f, x);
        };
        let l = &self.files[f].locals[li];
        if l.declared || l.path.is_empty() || l.path.iter().any(|s| s.starts_with("[#")) {
            return None;
        }
        // the value is a call of a function that returns a tuple
        let known = self.tuple_locals.borrow().get(&(f, li)).copied();
        let parts = match known {
            Some(n) => n,
            None => {
                let whole = self.typing.borrow().is_empty() && *self.depth.borrow() == 0;
                if !self.typing.borrow_mut().insert((f, li)) {
                    return None;
                }
                let recv = self.recv.replace(None);
                let value = self.resolve_path(f, scope(x), &l.path, true);
                *self.recv.borrow_mut() = recv;
                self.typing.borrow_mut().remove(&(f, li));
                let n = match value {
                    Some((Target::Def(c), _)) if self.def(c).kind.is_callable() => {
                        self.def(c).rets.len()
                    }
                    _ => 0,
                };
                if whole {
                    self.tuple_locals.borrow_mut().insert((f, li), n);
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
        if let Some(known) = self.call_targets.borrow().get(&(f, ri)) {
            return *known;
        }
        // (a call that comes back to itself has no answer)
        self.call_targets.borrow_mut().insert((f, ri), None);
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
        self.call_targets.borrow_mut().insert((f, ri), out);
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
        let mut cands = self.by_name.get(name.as_str())?.iter().copied().filter(|c| {
            self.def(*c).kind.is_callable()
                && self.family[c.0] == Some(fam)
                && self.node_id(*c).is_some()
        });
        let (c, None) = (cands.next()?, cands.next()) else { return None };
        let marker = r.path.iter().any(|s| s.starts_with('['));
        let head = r.path[0].as_str();
        // (PHP: `$x` is the local `x`)
        let bare = head.split('@').next().unwrap_or(head).trim_start_matches('$');
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
        } else if self.is_local_name(f, d, head) || self.is_local_name(f, d, bare) {
            let head = if self.is_local_name(f, d, head) { head } else { bare };
            if self.is_untyped_local(f, d, head) {
                "untyped local or parameter".to_string()
            } else if self.is_external_local(f, d, head) {
                "local of a library type".to_string()
            } else {
                match self.local_type(f, d, head) {
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
        // Java: a fully qualified name of a package the project has none of
        // (`new okhttp3.Response.Builder()`)
        let outside_head = outside_head
            || (reason == "head name not found"
                && r.path.len() >= 3
                && !self.is_local_name(f, d, bare_head)
                && self.java_library_type(f, d, &r.path[..2]));
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
        // Rust: `std::`, `core::`, `alloc::` or a crate the project depends
        // on (`serde_json::from_reader`, `axum::Server::bind`)
        let crate_head = fam == Family::Rust
            && r.path.len() >= 2
            && !self.is_local_name(f, d, bare_head)
            && self.rust_crate_name(bare_head)
            && !matches!(self.resolve_path(f, d, &r.path[..1], true), Some((Target::Def(_), _)));
        // a typed local whose type is made of a library's (`type
        // ExtendedServer = https.Server & { url: string }`, `class X extends
        // EventEmitter`), the name not its own: the library part's
        let library_part = reason.starts_with("typed local: no such member")
            && self
                .local_type(f, d, head.trim_start_matches('$'))
                .is_some_and(|(t, _)| self.has_outside_base(t) && !self.answers(t, name, true, 4));
        // (PHP `(new RedirectTestMethodRequest(…))->withBody(…)`, the class
        // extending a library's `Request`: a type built in the path)
        let library_part = library_part
            || (fam == Family::Php
                && reason == "type: no such member"
                && r.path.get(1).is_some_and(|s| s == "()")
                && matches!(
                    self.resolve_path(f, d, &r.path[..1], true),
                    Some((Target::Def(t), _)) if self.def(t).kind.is_class_like()
                        && self.has_outside_base(t)
                        && !self.answers(t, name, true, 4)
                ));
        let library_like = if reason == "library name"
            || reason == "local of a library type"
            || std_held
            || library_part
            || (fam == Family::Rust
                && r.path.len() >= 2
                && !crate_head
                && self.rust_library_receiver(f, d, &r.path))
            || (matches!(fam, Family::Ecma | Family::Go | Family::Php)
                && r.path.len() >= 3
                && self.library_field_step(f, d, &r.path))
            // (Go `room(id).Submit(post)`, `room` declared to give a
            // `broadcast.Broadcaster`; Java `call.request().tag(…)`, `request()`
            // declared to give an `okhttp3.Request`)
            || (matches!(fam, Family::Go | Family::Jvm | Family::Php)
                && r.path.len() >= 3
                && self.library_result(f, d, &r.path[..r.path.len() - 1]))
            || self.java_library_receiver(f, d, &r.path)
            || self.php_library_receiver(f, d, &r.path)
            || self.php_library_step(f, d, &r.path)
        {
            Some("the receiver is a library's")
        } else if fam == Family::Php
            && reason == "local the walk cannot type"
            && self.php_library_value(f, d, bare)
        {
            Some("the local holds what a library's call gives")
        } else if outside_head || std_head || crate_head {
            Some("the head is a library's type or global")
        } else if core.contains(&name.as_str())
            || COMMON_METHODS.iter().any(|m| m.to_lowercase() == lower)
            || self.builtin(f, name)
        {
            Some("a name the language's own types answer to")
        } else if c.0 != f && test(c.0, cd) && !crate::community::is_test_path(&self.files[f].path)
        {
            Some("the one definition is test code, the call is not")
        } else if fam == Family::Php
            && r.path.len() == 1
            && !head.starts_with('$')
            && cd.kind == DefKind::Method
        {
            // (PHP calls a method only through `$this->`, `self::` or an
            // object: a bare `rewind($fp)` is PHP's own function)
            Some("the one definition is a method, the call a function")
        } else if matches!(fam, Family::CSharp | Family::Jvm)
            && cd.receiver.is_some()
            && !self.extension_visible(f, c)
        {
            // (C#, Kotlin: an extension is called only where its namespace is
            // used; `req.With(…)` on MockHttp's request, `AddAuthentication()`
            // beside another project's extension of the name)
            Some("the one definition is an extension the caller does not see")
        } else if fam == Family::CSharp && self.generated_member(f, d, &r.path) {
            // (restsharp's `ReadOnlyRestClientOptions`, which its own source
            // generator fills from `RestClientOptions`)
            Some("the member is one a compiler plugin generates")
        } else if matches!(fam, Family::CSharp | Family::Jvm)
            && r.path.len() >= 3
            && !r.path[1].starts_with(['[', '('])
            && !self.by_name.contains_key(r.path[1].as_str())
            && (head.contains('@') || self.is_untyped_local(f, d, head))
        {
            // (`c.HttpContext.GetTokenAsync()`: no type of the project has a
            // member `HttpContext`, so what it gives is a library's)
            Some("the receiver is a library's")
        } else if fam == Family::Jvm && self.plugin_member(f, d, &r.path) {
            // (`UserResponse.serializer()` of a `@Serializable` class)
            Some("the member is one a compiler plugin generates")
        } else if matches!(head, "self" | "super")
            && r.path.len() == 2
            && (self.class_of(f, d).is_some_and(|t| self.has_outside_base(t))
                || self.anonymous_outside_base(f, d))
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
        } else if fam == Family::Ecma
            && r.path.len() >= 2
            && cd.object.is_some()
            && cd.parent.is_some()
            && UNTYPED_RECEIVER_REASONS.contains(&reason.as_str())
        {
            // (`promise.buffer = function () {…}` inside `asPromise`: a
            // member a function puts on a value it makes, called on one the
            // walk cannot type)
            Some("the member is put on a value at run time")
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
            "in_definition": d.is_some(),
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
        let mut cur = local_start(d);
        while let Some(x) = cur {
            if let Some(&li) = self.locals.get(&(f, x, name.to_string())) {
                let l = &self.files[f].locals[li];
                if l.declared || l.sealed {
                    return l.sealed;
                }
                if l.path.is_empty() || l.path.last().is_some_and(|s| s == crate::refs::OPENED) {
                    return false;
                }
                if let Some(&known) = self.sealed_locals.borrow().get(&(f, li)) {
                    return known;
                }
                // (worked out from the top it is the local's own answer and
                // kept, as `local_type` keeps a type)
                let whole = self.typing.borrow().is_empty() && *self.depth.borrow() == 0;
                if *self.depth.borrow() > 3 || !self.typing.borrow_mut().insert((f, li)) {
                    return false;
                }
                *self.depth.borrow_mut() += 1;
                let recv = self.recv.replace(None);
                let types = self.types_only.replace(false);
                let value = self.resolve_path(f, scope(x), &l.path, true);
                *self.types_only.borrow_mut() = types;
                *self.recv.borrow_mut() = recv;
                *self.depth.borrow_mut() -= 1;
                self.typing.borrow_mut().remove(&(f, li));
                let sealed = matches!(value, Some((Target::Def(c), _))
                    if self.def(c).kind.is_callable() && self.def(c).sealed);
                if whole {
                    self.sealed_locals.borrow_mut().insert((f, li), sealed);
                }
                return sealed;
            }
            cur = self.up(f, x);
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
        let mut cur = local_start(d);
        while let Some(x) = cur {
            if let Some(&li) = self.locals.get(&(f, x, name.to_string())) {
                let l = &self.files[f].locals[li];
                let key = l.key.clone().or_else(|| {
                    scope(x)
                        .and_then(|x| defs[x].plist.iter().find(|p| p.name == *name))
                        .and_then(|p| p.key.clone())
                })?;
                return self
                    .type_of_path(f, scope(x), &key, true)
                    .filter(|t| self.def(*t).kind.is_class_like());
            }
            cur = self.up(f, x);
        }
        None
    }

    /// The function a local holds when it is a part of a callable's tuple
    /// that names one (`const [text, copy] = useCopy()`).
    fn local_function(&self, f: usize, d: Option<usize>, name: &str) -> Option<DefRef> {
        let mut cur = local_start(d);
        let (x, li) = loop {
            let x = cur?;
            if let Some(&li) = self.locals.get(&(f, x, name.to_string())) {
                break (x, li);
            }
            cur = self.up(f, x);
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
        let found = self.resolve_path(f, scope(x), &l.path, true);
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
        let mut cur = local_start(d);
        while let Some(x) = cur {
            if let Some(&li) = self.locals.get(&(f, x, name.to_string())) {
                return self.files[f].locals[li].keyed
                    || scope(x)
                        .is_some_and(|x| defs[x].plist.iter().any(|p| p.name == *name && p.keyed));
            }
            cur = self.up(f, x);
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

    /// A walk from `start` under bindings `b` of its type parameters, with
    /// calls in it: a call gives what its callable declares it returns, or
    /// the type a parameter it returns stands for (`fn get_mut(&mut self) ->
    /// &mut S` under `S = TransportAdapter`); an unwrapper the type has no
    /// member for is skipped (`?.unwrap()`).
    fn walk_bound(
        &self,
        start: DefRef,
        mut b: Option<Binds>,
        rest: &[String],
        conf: Confidence,
    ) -> Option<(Target, Confidence)> {
        let mut t = start;
        let mut on: Option<DefRef> = None;
        let mut skip_call = false;
        for seg in rest {
            if seg == "()" {
                if std::mem::take(&mut skip_call) {
                    continue;
                }
                let def = self.def(t);
                if def.kind.is_class_like() {
                    continue;
                }
                if !def.kind.is_callable() {
                    return None;
                }
                let next = self.return_type(t).map(|(r, _)| r).or_else(|| {
                    let p = self.return_path(t)?;
                    self.bound_type(b.as_ref(), p, t, on).filter(|(_, n)| !n).map(|(r, _)| r)
                })?;
                b = None;
                t = next;
                continue;
            }
            if seg == crate::refs::OPENED {
                continue;
            }
            match self.member(&Target::Def(t), seg, 3) {
                Some(Target::Def(m)) => {
                    on = Some(t);
                    t = m;
                }
                None if UNWRAPPERS.contains(&seg.as_str()) && self.def(t).kind.is_class_like() => {
                    skip_call = true;
                }
                _ => return None,
            }
        }
        Some((Target::Def(t), conf))
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
                let found = self
                    .fqn
                    .get(&key)
                    .or_else(|| self.by_qual.get(&key))
                    .into_iter()
                    .flatten()
                    .copied()
                    .filter(|r| self.family[r.0] == Some(fam))
                    // Ruby: a method at another file's top level is there
                    // only for the files that require it
                    .filter(|r| {
                        fam != Family::Ruby
                            || !pre.is_empty()
                            || r.0 == f
                            || !self.def(*r).kind.is_callable()
                            || self.scopes[f].imported.contains(&r.0)
                    });
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
        for (fi, file) in self.files.iter().enumerate() {
            if self.family[fi] != Some(Family::Php) {
                continue;
            }
            for r in &file.refs {
                if matches!(r.kind, RefKind::Inherits | RefKind::Base)
                    && r.path.last().is_some_and(|n| n == "Authenticatable")
                {
                    let Some(d) = r.from_def else { continue };
                    if found.is_some_and(|x| x != (fi, d)) {
                        return None;
                    }
                    found = Some((fi, d));
                }
            }
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

    /// An extension named `name` written for type `c`, or for one of its
    /// bases (`client.BuildUri(r)` on a `RestClient`, written for
    /// `IRestClient`): that is what the language calls. Else what
    /// `extension_method` makes of the type's own name.
    fn extension_for(&self, f: usize, name: &str, c: DefRef) -> Option<(Target, Confidence)> {
        // (by the qualified name: `PropertySpec.Builder`, not every `Builder`)
        let own = self.extension_method(f, name, Some(&self.def(c).qualname));
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
                let found = self.extension_method(f, name, Some(&self.def(*b).qualname));
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

    /// Whether file `f` sees the extension `r`: its own, one in a file it
    /// imports, in its directory or in its package, a namespace around its
    /// own (C#), one it `using`s, or the global namespace of its project.
    fn extension_visible(&self, f: usize, r: DefRef) -> bool {
        let fam = self.family[f];
        let here = dir_of(&self.files[f].path);
        let pkg = self.files[f].package.as_deref();
        r.0 == f
        || self.scopes[f].imported.contains(&r.0)
        || dir_of(&self.files[r.0].path) == here
        || (pkg.is_some() && self.files[r.0].package.as_deref() == pkg)
        // C#: a namespace sees what the ones around it declare
        // (`RestSharp.Tests` sees `RestSharp`'s extensions)
        || (fam == Some(Family::CSharp)
        && pkg.zip(self.files[r.0].package.as_deref()).is_some_and(|(mine, its)| {
        mine.strip_prefix(its).is_some_and(|rest| rest.starts_with('.'))
        }))
        || self.uses_namespace_of(f, r.0)
        // C#: the global namespace, seen by every file of the
        // project (`public static class OpenApiOptionsExtensions`
        // with no namespace, called from `Program.cs`)
        || (fam == Some(Family::CSharp)
        && self.files[r.0].package.is_none()
        && self.modsys.cs_project_of(&self.files[f].path).is_some_and(|p| {
        self.modsys.cs_project_of(&self.files[r.0].path) == Some(p)
        }))
    }

    /// An extension function or method named `name` (`Def::receiver`) that
    /// file `f` can see: its own, one in a file it imports, in its directory
    /// or in its package. `on` is the receiver's type when the source writes
    /// it: the extension written for that type is `EXTRACTED`; otherwise a
    /// single candidate (the file's own first) is `INFERRED`.
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
            .filter(|r| self.extension_visible(f, *r))
            .collect();
        if let Some(on) = on {
            let exact: Vec<DefRef> = seen
                .iter()
                .copied()
                .filter(|r| {
                    let written = self.def(*r).receiver.clone().or_else(|| self.extended_type(*r));
                    written.is_some_and(|t| receiver_matches(&t, on))
                })
                .collect();
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
        let in_ns = |ns: &str| {
            self.pkg_files.get(&(fam, ns.to_string())).is_some_and(|fs| fs.contains(&g))
                || self.files[g].package.as_deref() == Some(ns)
        };
        self.scopes[f].stars.iter().any(|(star, _)| match star {
            Target::Ns(ns) => in_ns(ns),
            Target::Module(fs) => fs.contains(&g),
            // (C#: `using RestSharp.Serializers;` where a file opens `namespace
            // RestSharp.Serializers { … }` as a block, a definition of its own)
            Target::Def(c) if self.def(*c).kind == DefKind::Namespace => {
                let d = self.def(*c);
                in_ns(&d.qualname) || self.fqn_of(c.0, c.1).is_some_and(|q| in_ns(&q)) || (c.0 == g)
            }
            _ => false,
        })
    }

    /// The type a local is declared with, bare (`string`, `KmProperty`).
    fn local_written_type(&self, f: usize, d: Option<usize>, name: &str) -> Option<String> {
        let mut cur = local_start(d);
        while let Some(x) = cur {
            if let Some(&li) = self.locals.get(&(f, x, name.to_string())) {
                let l = &self.files[f].locals[li];
                return l.path.last().filter(|_| l.declared).cloned();
            }
            cur = self.up(f, x);
        }
        None
    }

    /// The one name a local's value is built from (`var m = new Message()`),
    /// where no type is written: a hint for an extension written for it.
    fn local_built_type(&self, f: usize, d: Option<usize>, name: &str) -> Option<String> {
        let mut cur = local_start(d);
        while let Some(x) = cur {
            if let Some(&li) = self.locals.get(&(f, x, name.to_string())) {
                return match self.files[f].locals[li].path.as_slice() {
                    [one] => Some(one.clone()),
                    _ => None,
                };
            }
            cur = self.up(f, x);
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
        self.def(t).kind.is_class_like().then_some((t, Confidence::Extracted))
    }

    /// The type of a variable from what constructs its value
    /// (`settings = Settings()`).
    fn state_type(&self, p: DefRef) -> Option<(DefRef, Confidence)> {
        let d = self.def(p);
        let path = d.value_path.as_ref()?;
        let t = self.type_of_path(p.0, d.parent, path, false)?;
        self.def(t).kind.is_class_like().then_some((t, Confidence::Inferred))
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
        while let Some(x) = cur {
            // (an extra definition that is a callable, RSpec's `let`, is
            // the source of its own block's uses)
            let skip =
                defs[x].extra && !defs[x].kind.is_callable() && !(swift && r.kind != RefKind::Type);
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

/// Every edge the files' facts prove, plus the external nodes they point at.
pub fn resolve(
    root: &Path,
    files: &[FileFacts],
    asm: &mut Assembled,
    manifests: Vec<(String, PathBuf)>,
    diagnose: bool,
) -> (Vec<Value>, Vec<Value>, BTreeMap<String, ResolveStats>, Vec<Value>) {
    let mut edges: Vec<EdgeOut> = Vec::new();
    // calls left without an edge whose name is one definition of the project
    let mut left: Vec<Value> = Vec::new();
    let mut stats: BTreeMap<String, ResolveStats> = BTreeMap::new();
    let mut ext_nodes: Vec<(String, String, &'static str)> = Vec::new();
    let mut resource_nodes: Vec<(String, String, ResourceKind)> = Vec::new();
    let route_nodes: Vec<http::RouteNode>;
    {
        let p = Project::new(root, files, asm, manifests);
        let mut ext_seen: HashSet<String> = HashSet::new();
        let mut ext_target = |e: usize, rest: &[String], symbol: bool| -> String {
            let (id, label, eco) = p.ext_node_id(e, rest, symbol);
            if ext_seen.insert(id.clone()) {
                ext_nodes.push((id.clone(), label, eco));
            }
            id
        };
        let mut orm_uses: Vec<(String, String, usize, u32, bool)> = Vec::new();
        for (f, facts) in files.iter().enumerate() {
            let Some(fam) = p.family[f] else { continue };
            let st = stats.entry(fam.name().to_string()).or_default();
            for (ri, r) in facts.refs.iter().enumerate() {
                let src = p.source_of(f, r);
                // a data model's table, read or written through the ORM
                if let Some((table, write)) = p.orm_use(f, r) {
                    orm_uses.push((table, src.clone(), f, r.line, write));
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
                if r.context == "implicit_call"
                    && r.path.len() == 1
                    && p.is_local_name(f, scope_def, &r.path[0])
                {
                    continue;
                }
                *p.types_only.borrow_mut() = heritage || r.kind == RefKind::Type;
                *p.calling.borrow_mut() = matches!(r.kind, RefKind::Call | RefKind::New);
                *p.recv.borrow_mut() = r.recv.map(|i| (f, i));
                *p.args.borrow_mut() = r.args;
                *p.self_arg.borrow_mut() = r.self_arg;
                let is_call = matches!(r.kind, RefKind::Call | RefKind::New);
                *p.cur.borrow_mut() = is_call.then_some((f, ri));
                p.guessed.set(false);
                let found = if let Some(place) = &r.implicit {
                    // Swift `.status(301)`: a static member of the type the
                    // place it stands in asks for
                    let saved = p.suspend();
                    let found = p.implicit_member(f, scope_def, r, place);
                    p.restore(saved);
                    found
                } else if r.from_owner.is_some() && heritage {
                    p.resolve_path(f, None, &r.path, true)
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
                *p.args.borrow_mut() = None;
                *p.self_arg.borrow_mut() = false;
                *p.cur.borrow_mut() = None;
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
                let (target, relation, conf) = match found {
                    Some((Target::Def(t), conf)) => {
                        let Some(id) = p.node_id(t) else { continue };
                        let tk = p.def(t).kind;
                        let rel = match r.kind {
                            RefKind::Call | RefKind::New => {
                                // a Lombok accessor called: the field it reads
                                // or writes
                                if tk.is_state() && r.kind == RefKind::Call {
                                    let called = r.path.last().map(String::as_str).unwrap_or("");
                                    if called != p.def(t).name {
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
                            RefKind::Attr => {
                                if !tk.is_callable() {
                                    continue;
                                }
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
                            (ext_target(e, &rest, false), "calls", conf)
                        }
                        RefKind::Decorator => (ext_target(e, &rest, true), "references", conf),
                        RefKind::Type
                        | RefKind::Indirect
                        | RefKind::Attr
                        | RefKind::Read
                        | RefKind::Write => continue,
                        k => (ext_target(e, &rest, true), external_base(k, fam, &rest), conf),
                    },
                    Some(_) => continue,
                    None => {
                        if heritage || r.kind == RefKind::Decorator {
                            // a base or decorator the project does not define
                            let label = r.path.join(if fam == Family::Rust { "::" } else { "." });
                            let e = p.ext(&label, fam.ecosystem());
                            let id = ext_target(e, &[], false);
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
                                left.extend(p.explain_unresolved(f, scope_def, r));
                            }
                            if let Some((Target::Def(t), conf)) = head_type {
                                if p.def(t).kind.is_class_like() {
                                    if let Some(id) = p.node_id(t) {
                                        if id != src {
                                            edges.push(EdgeOut {
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
                    edges.push(EdgeOut {
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
                            edges.push(EdgeOut {
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
                edges.push(EdgeOut {
                    source: src,
                    target,
                    relation,
                    confidence: conf,
                    file: f,
                    line: r.line,
                    context: if r.kind == RefKind::Decorator { "decorator" } else { r.context },
                    runs: None,
                });
            }
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
                        Target::Ext(e, rest) => ext_target(e, &rest, true),
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
    let mut links = Vec::with_capacity(kept.len());
    for e in kept {
        let fam = files[e.file].lang.map(Family::of).map(|f| f.name()).unwrap_or("?");
        let st = stats.entry(fam.to_string()).or_default();
        *st.edges.entry(e.relation.to_string()).or_default() += 1;
        if e.confidence == Confidence::Inferred {
            st.inferred += 1;
        }
        let mut v = json!({
            "source": e.source,
            "target": e.target,
            "relation": e.relation,
            "confidence": e.confidence.label(),
            "confidence_score": e.confidence.score(),
            "weight": 1.0,
            "source_file": files[e.file].path,
            "source_location": format!("L{}", e.line),
            "context": e.context,
        });
        if let Some(r) = e.runs {
            v["runs"] = json!(r.as_str());
        }
        links.push(v);
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
    if !renamed.is_empty() {
        for l in links.iter_mut() {
            for end in ["source", "target"] {
                if let Some(new) = l[end].as_str().and_then(|t| renamed.get(t)) {
                    l[end] = json!(new);
                }
            }
        }
    }
    (nodes, links, stats, left)
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

/// Accessors of a library's generic type that give the value it holds
/// (`Response::body_mut`, `TlsStream::get_mut`, `BufReader::into_inner`).
/// The scope of the locals no definition holds: a callback's variables at
/// file level (`test('…', async t => { const request = …; request.m() })`).
const FILE_SCOPE: usize = usize::MAX;

/// Where a search for a local name starts: the definition around, or the
/// file's own locals for a use no definition holds.
fn local_start(d: Option<usize>) -> Option<usize> {
    Some(d.unwrap_or(FILE_SCOPE))
}

/// A local's scope as a definition to resolve from (`None` for the file's).
fn scope(x: usize) -> Option<usize> {
    (x != FILE_SCOPE).then_some(x)
}

/// Rust's own types, as a parameter writes them (`uri: &str`): a receiver of
/// one is the language's.
/// Whether an extension's written receiver (`PropertySpec.Builder`, `Builder`,
/// `string`) is the type `on` names, written bare or qualified: equal, or one a
/// dotted suffix of the other.
fn receiver_matches(written: &str, on: &str) -> bool {
    let suffix = |long: &str, short: &str| {
        long.len() > short.len()
            && long.ends_with(short)
            && long.as_bytes()[long.len() - short.len() - 1] == b'.'
    };
    written.eq_ignore_ascii_case(on) || suffix(written, on) || suffix(on, written)
}

/// Kotlin: names the standard library's types and scope functions answer to,
/// never taken for a project extension on a receiver the walk cannot type.
const KOTLIN_CORE_METHODS: &[&str] = &[
    "let",
    "apply",
    "also",
    "run",
    "with",
    "takeIf",
    "takeUnless",
    "use",
    "map",
    "mapNotNull",
    "flatMap",
    "filter",
    "filterNot",
    "filterIsInstance",
    "first",
    "firstOrNull",
    "last",
    "lastOrNull",
    "single",
    "singleOrNull",
    "find",
    "forEach",
    "forEachIndexed",
    "any",
    "all",
    "none",
    "count",
    "sum",
    "sumOf",
    "toList",
    "toMutableList",
    "toSet",
    "toMutableSet",
    "toMap",
    "toMutableMap",
    "toTypedArray",
    "joinToString",
    "plus",
    "minus",
    "contains",
    "get",
    "set",
    "getOrNull",
    "getOrElse",
    "getOrPut",
    "isEmpty",
    "isNotEmpty",
    "isNullOrEmpty",
    "isNullOrBlank",
    "isBlank",
    "isNotBlank",
    "orEmpty",
    "repeat",
    "toString",
    "equals",
    "hashCode",
    "compareTo",
    "sortedBy",
    "sortedWith",
    "sorted",
    "groupBy",
    "associate",
    "associateBy",
    "associateWith",
    "distinct",
    "reversed",
    "zip",
    "asSequence",
    "iterator",
    "trim",
    "split",
    "replace",
    "startsWith",
    "endsWith",
    "substring",
    "lowercase",
    "uppercase",
    "format",
    "toInt",
    "toLong",
    "toDouble",
    "toBoolean",
    "copy",
    "invoke",
    "add",
    "addAll",
    "remove",
    "removeAll",
    "clear",
    "put",
    "putAll",
    "require",
    "check",
    "error",
    "lazy",
];

/// C#'s predefined types (`string`, `int`, …): never a project type.
const CSHARP_PREDEFINED: &[&str] = &[
    "string", "int", "long", "short", "byte", "sbyte", "uint", "ulong", "ushort", "bool", "double",
    "float", "decimal", "char", "object", "dynamic", "nint", "nuint",
];

/// Kotlin: the types every file sees without an import (`kotlin.*`,
/// `kotlin.collections.*`, …).
const KOTLIN_IMPLICIT_TYPES: &[&str] = &[
    "String",
    "Int",
    "Long",
    "Short",
    "Byte",
    "Double",
    "Float",
    "Boolean",
    "Char",
    "Any",
    "Unit",
    "Nothing",
    "Number",
    "Array",
    "List",
    "MutableList",
    "Map",
    "MutableMap",
    "Set",
    "MutableSet",
    "Collection",
    "MutableCollection",
    "Iterable",
    "Sequence",
    "Pair",
    "Triple",
    "Regex",
    "CharSequence",
    "Comparable",
    "Throwable",
    "Exception",
    "Lazy",
    "Result",
    "StringBuilder",
    "IntArray",
    "ByteArray",
    "LongArray",
    "ArrayList",
    "HashMap",
    "HashSet",
    "LinkedHashMap",
    "LinkedHashSet",
];

/// Java: the types of `java.lang`, which every file sees without an import.
const JAVA_LANG_TYPES: &[&str] = &[
    "String",
    "Object",
    "Class",
    "Integer",
    "Long",
    "Short",
    "Byte",
    "Double",
    "Float",
    "Boolean",
    "Character",
    "Number",
    "Math",
    "StrictMath",
    "System",
    "Thread",
    "Runtime",
    "Throwable",
    "Exception",
    "RuntimeException",
    "Error",
    "StringBuilder",
    "StringBuffer",
    "CharSequence",
    "Iterable",
    "Comparable",
    "Runnable",
    "Void",
    "Enum",
    "Record",
    "ClassLoader",
    "Process",
    "ProcessBuilder",
    "ThreadLocal",
    "AutoCloseable",
    "Cloneable",
    "IllegalArgumentException",
    "IllegalStateException",
    "NullPointerException",
    "UnsupportedOperationException",
    "IndexOutOfBoundsException",
    "InterruptedException",
];

const RUST_OWN_TYPES: &[&str] = &[
    "str", "String", "bool", "char", "u8", "u16", "u32", "u64", "u128", "usize", "i8", "i16",
    "i32", "i64", "i128", "isize", "f32", "f64", "Vec", "HashMap", "HashSet", "BTreeMap",
    "BTreeSet", "VecDeque", "Path", "PathBuf", "Duration",
];

/// A value's declared type, its type arguments, and the file and scope
/// they are written in.
type Declared = (Vec<String>, Vec<Vec<String>>, Vec<Vec<Vec<String>>>, usize, Option<usize>);

const LIBRARY_ACCESSORS: &[&str] =
    &["get_ref", "get_mut", "into_inner", "body", "body_mut", "into_body", "inner", "inner_mut"];

/// Go: what a generic of the standard library hands back of the one type it
/// holds (`sw.Load()` of an `atomic.Pointer[window]`).
const GO_LIBRARY_ACCESSORS: &[&str] = &["Load", "Swap"];

thread_local! {
    /// How deep `library_accessor` / `library_result` are in each other.
    static LIBRARY_DEPTH: std::cell::Cell<u8> = const { std::cell::Cell::new(0) };
}

/// A depth counter held for a scope: `enter` refuses past `max`.
struct DepthGuard(&'static std::thread::LocalKey<std::cell::Cell<u8>>);

impl DepthGuard {
    fn enter(key: &'static std::thread::LocalKey<std::cell::Cell<u8>>, max: u8) -> Option<Self> {
        let depth = key.with(std::cell::Cell::get);
        if depth >= max {
            return None;
        }
        key.with(|c| c.set(depth + 1));
        Some(DepthGuard(key))
    }
}

impl Drop for DepthGuard {
    fn drop(&mut self) {
        self.0.with(|c| c.set(c.get().saturating_sub(1)));
    }
}
