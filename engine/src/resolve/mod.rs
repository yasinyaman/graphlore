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
    seg == crate::refs::ELEMENT
        || KEEPING_METHODS.contains(&seg)
        || ELEMENT_ACCESSORS.contains(&seg)
}

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
        p
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
                // a class with no name of its own answers to none
                if d.anonymous {
                    continue;
                }
                // a method declared for a type (Go receiver, Rust impl, C++
                // `A::m`) is no module-level name
                if is_top(&f.defs, di) && !(d.owner.is_some() && d.kind.is_callable()) {
                    top.entry(d.name.clone()).or_default().push(di);
                }
                if let Some(par) = d.parent {
                    children.entry(par).or_default().entry(d.name.clone()).or_default().push(di);
                }
                let Some(fam) = fam else { continue };
                if self.def_ids[fi].get(di).is_some_and(|x| x.is_some()) {
                    self.by_name.entry(d.name.clone()).or_default().push((fi, di));
                }
                self.by_qual.entry(qual_key(fam, &d.qualname)).or_default().push((fi, di));
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
                    if d.kind.is_callable() {
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
                let Some(d) = l.from_def else { continue };
                if l.field {
                    if let Some(c) = self.class_of(fi, Some(d)) {
                        fields.entry(c).or_default().entry(l.name.clone()).or_insert((fi, li));
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
            if last == crate::refs::ELEMENT {
                return self.elem_of(f, d, of);
            }
        }
        let found = self.resolve_path(f, d, path, true);
        match found? {
            (Target::Def(r), conf) => {
                let def = self.def(r);
                let k = def.kind;
                if k.is_class_like() || k == DefKind::TypeAlias {
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
                } else {
                    None
                }
            }
            _ => None,
        }
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
        // a C# extension's first parameter is the receiver
        let plist = match (&def.receiver, self.family[c.0]) {
            (Some(_), Some(Family::CSharp)) if !def.plist.is_empty() => &def.plist[1..],
            _ => &def.plist[..],
        };
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
                Some(Prim::Any) | None => Some(0),
                Some(_) => None,
            },
            Arg::Unknown | Arg::Null => Some(0),
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
                match prim {
                    Some(Prim::Any) => Some(0),
                    // an object of the project is no string, number or flag
                    Some(_) => None,
                    None => match self.param_type(c, ty) {
                        Some(pt) if pt == at || self.is_a(at, pt, 6) => Some(2),
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
                x.kind.is_callable() && !x.decl_only && x.owner == d.owner
            })
            .collect();
        out.sort();
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
        let t = self.type_of_path(r.0, self.def(r).parent, &path, true)?;
        self.def(t).kind.is_class_like().then_some((t, true))
    }

    /// The type of a local name or parameter seen from `d`.
    fn local_type(&self, f: usize, d: Option<usize>, name: &str) -> Option<(DefRef, Confidence)> {
        let defs = &self.files[f].defs;
        let mut cur = d;
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
                let recv = self.recv.replace(None);
                let out = self.local_type_of(f, x, li, name);
                *self.recv.borrow_mut() = recv;
                *self.calling.borrow_mut() = calling;
                self.typing.borrow_mut().remove(&(f, li));
                if whole {
                    self.local_types.borrow_mut().insert((f, li), out);
                }
                return out;
            }
            cur = defs[x].parent;
        }
        None
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
        let typed = |file: usize, scope: Option<usize>, elem: &[String]| {
            self.type_of_path(file, scope, elem, true)
                .filter(|t| self.def(*t).kind.is_class_like())
                .map(|t| (t, Confidence::Extracted))
        };
        if let [name] = path {
            let defs = &self.files[f].defs;
            let mut cur = d;
            while let Some(x) = cur {
                if let Some(&li) = self.locals.get(&(f, x, name.clone())) {
                    let l = &self.files[f].locals[li];
                    // a parameter's is on the definition's parameter list
                    let elem = l.elem.clone().or_else(|| {
                        defs[x].plist.iter().find(|p| p.name == *name).and_then(|p| p.elem.clone())
                    });
                    if let Some(e) = elem {
                        return typed(f, Some(x), &e);
                    }
                    // another name for a collection: `val ys = xs`
                    let alias = !l.declared
                        && !l.path.is_empty()
                        && l.path[0] != *name
                        && l.path.last().is_some_and(|s| s != crate::refs::ELEMENT);
                    return if alias {
                        self.elem_of(f, Some(x), &l.path).map(|(t, _)| (t, Confidence::Inferred))
                    } else {
                        None
                    };
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
        let found = self.resolve_path_inner(f, d, path, true, true);
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
        if of.is_empty() || !ELEMENT_ACCESSORS.contains(&method.as_str()) {
            return None;
        }
        self.elem_of(f, Some(d), of).map(|(t, _)| t)
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
                return l.assigned
                    || (called && (l.declared || l.path.first().is_some_and(|h| h == name)));
            }
            cur = defs[x].parent;
        }
        false
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
        while let Some(x) = cur {
            if self.locals.contains_key(&(f, x, name.to_string())) {
                return true;
            }
            cur = defs[x].parent;
        }
        false
    }

    /// The type of a field of a type (through its bases).
    fn field_type(&self, c: DefRef, name: &str, depth: usize) -> Option<(DefRef, Confidence)> {
        if let Some(&(fi, li)) = self.fields.get(&c).and_then(|m| m.get(name)) {
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
                .filter(|r| self.def(*r).kind != DefKind::Extension),
        )
    }

    /// The members of a type: its lexical children, and the methods attached
    /// to it by owner.
    fn members(&self, c: DefRef, name: &str) -> Vec<DefRef> {
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
    fn bases(&self, c: DefRef) -> Vec<DefRef> {
        if let Some(b) = self.bases_memo.borrow().get(&c) {
            return b.clone();
        }
        self.bases_memo.borrow_mut().insert(c, Vec::new()); // a cycle ends here
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
                }
            }
        }
        self.bases_memo.borrow_mut().insert(c, out.clone());
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
        let cname = &self.def(c).name;
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

    /// `name` looked up inside a target.
    fn member(&self, t: &Target, name: &str, depth: usize) -> Option<Target> {
        match t {
            Target::Def(c) => {
                let d = self.def(*c);
                if d.kind == DefKind::Namespace {
                    let kids = self.children[c.0].get(&c.1)?.get(name)?;
                    return self.best(kids.iter().map(|&k| (c.0, k))).map(Target::Def);
                }
                if name == "new" && self.family[c.0] == Some(Family::Ruby) {
                    return Some(Target::Def(*c)); // `Klass.new` constructs Klass
                }
                self.class_member(*c, name, 4)
                    .or_else(|| self.reopened_member(*c, name))
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
                let bare = owner.rsplit(['.', ':']).next().unwrap_or(owner);
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
        None
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
        self.resolve_path_inner(f, d, path, bases, false).or_else(|| {
            // an element (`xs[0].run()`) or a mapping (`xs.map { … }`) the
            // walk could not type is a receiver it cannot name: what is
            // called on it is found by a distinctive name alone
            let marker = |s: &String| s == crate::refs::ELEMENT || s == crate::refs::MANY;
            if let Some(at) = path.iter().rposition(marker) {
                let mut unnamed = vec!["()".to_string()];
                unnamed.extend_from_slice(&path[at + 1..]);
                return self.resolve_path_inner(f, d, &unnamed, bases, false);
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
        let (head, rest) = path.split_first()?;
        let mut conf = Confidence::Extracted;
        // Whether `t` stands for a collection of itself: after a name
        // declared as one (`xs: List<Foo>`), a call that returns one, a
        // method that keeps one (`filter`, `sorted`) or `[+]`; until an
        // element is taken (`first()`, `[]`).
        let mut many = false;
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
        let mut t = match head.as_str() {
            _ if head_many.is_some() => {
                let (elem, c) = head_many?;
                conf = c;
                many = true;
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
                match self.class_of(f, d) {
                    Some(c) => Target::Def(c),
                    // JS `this` inside a function assigned to an object; a
                    // method beside this one in an `impl` for a type the
                    // project does not define (`impl AuthorityExt for Authority`)
                    None => {
                        let m = self
                            .object_member(f, d, &rest[0])
                            .or_else(|| self.owner_sibling(f, d, &rest[0]))?;
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
                && (*self.types_only.borrow() || self.local_type(f, d, head).is_none()) =>
            {
                if rest.len() == 1 && !bases && *self.calling.borrow() {
                    let on = self.local_written_type(f, d, head);
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
                return None;
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
                if !bases && (rest.len() == 1 || (rest.len() == 2 && rest[0] == "()")) {
                    let on = self.local_written_type(f, d, head).filter(|_| rest.len() == 1);
                    return self.unknown_receiver(f, rest.last()?, on.as_deref());
                }
                return None;
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
            .or_else(|| self.resolve_name(f, d, head))
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
                    } else if !bases && (rest.len() == 1 || (rest.len() == 2 && rest[0] == "()")) {
                        return self.unknown_receiver(f, rest.last()?, None);
                    } else {
                        return None;
                    }
                }
            },
        };
        // a property named bare (implicit `this`), or a module variable, with
        // more after it: on through its type
        if let Target::Def(p) = t {
            if !many && self.def(p).kind.is_state() && collection_next(0) {
                if let Some(elem) = self.elem_type_of(p) {
                    t = Target::Def(elem);
                    many = true;
                }
            }
        }
        if let Target::Def(p) = t {
            if !many && self.def(p).kind.is_state() && !rest.is_empty() {
                let typed = self
                    .class_of(p.0, self.def(p).parent)
                    .and_then(|owner| self.field_type(owner, &self.def(p).name.clone(), 3))
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
                        let (b, imp) =
                            self.scopes[f].bindings.get(head.as_str()).filter(|_| top)?;
                        let m = self.eval(b, 3)?;
                        self.note_use(f, *imp, &m);
                        t = m;
                    }
                }
            }
        }
        // the type the member being walked through was looked up on
        let mut on: Option<DefRef> = None;
        let mut skip = 0;
        // an element was just taken and may still be in an optional
        let mut one = false;
        for (i, seg) in rest.iter().enumerate() {
            if skip > 0 {
                skip -= 1;
                continue;
            }
            // `[]`: one of the collection
            // (of an optional, `xs.find { … }?.let { … }`: what it holds)
            if seg == crate::refs::ELEMENT {
                if !many && !one {
                    return None;
                }
                many = false;
                continue;
            }
            // `[+]`: a collection of what the path named so far gives
            if seg == crate::refs::MANY {
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
                continue;
            }
            if many {
                // a method of collections: its name says what it gives
                // (called, or read like a property: Swift `xs.first`); as
                // the thing called it is the library's method, nothing here
                if i + 1 == rest.len() && *self.calling.borrow() {
                    return None;
                }
                if ELEMENT_ACCESSORS.contains(&seg.as_str()) {
                    many = false;
                    one = true;
                } else if !KEEPING_METHODS.contains(&seg.as_str()) {
                    return None;
                }
                skip = usize::from(rest.get(i + 1).is_some_and(|s| s == "()"));
                continue;
            }
            // `xs.first().unwrap()`, `xs.stream().findFirst().get()`: the
            // element out of the optional it came in, unless its type has
            // a member of that name
            if std::mem::take(&mut one) && UNWRAPPERS.contains(&seg.as_str()) {
                if let Target::Def(c) = t {
                    if self.class_member(c, seg, 4).is_none() {
                        // (as the thing called, it is the library's)
                        if i + 1 == rest.len() && *self.calling.borrow() {
                            return None;
                        }
                        one = true;
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
                        if let Some(elem) = self.elem_type_of(c) {
                            t = Target::Def(elem);
                            many = true;
                            skip = usize::from(flat);
                            continue;
                        }
                    }
                    // a method that declares its result: `builder().a().b()`
                    if self.def(c).kind.is_callable() {
                        if let Some((r, declared)) = self.return_type(c) {
                            if !declared {
                                conf = conf.max(Confidence::Inferred);
                            }
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
            let found = self.member(&t, seg, 3);
            // a property with more after it: go on through its type
            let through_property = matches!(&found, Some(Target::Def(p))
                if matches!(self.def(*p).kind, DefKind::Property | DefKind::Constant))
                && i + 1 < rest.len();
            // a field declared as a collection, with something of
            // collections after it
            if let Some(Target::Def(p)) = &found {
                let flat = rest.get(i + 1).is_some_and(|s| s == crate::refs::MANY);
                if self.def(*p).kind.is_state() && (flat || collection_next(i + 1)) {
                    if let Some(elem) = self.elem_type_of(*p) {
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
                        t = Target::Def(ft);
                        continue;
                    }
                }
                return None;
            }
            match found {
                Some(next) => t = next,
                None => {
                    // `self.repo.find()`: through the field's type
                    let Target::Def(c) = t else { return None };
                    // the last name is no member of the type: an extension
                    // written for it (`typeSpec.trimmedToString()`)
                    if self.def(c).kind.is_class_like() && i + 1 == rest.len() && !bases {
                        let on = self.def(c).name.clone();
                        return self.extension_method(f, seg, Some(&on));
                    }
                    if !self.def(c).kind.is_class_like() || i + 1 == rest.len() {
                        return None;
                    }
                    let (ft, cf) = self.field_type(c, seg, 3)?;
                    conf = conf.max(cf);
                    t = Target::Def(ft);
                }
            }
        }
        // a collection is no definition; one thing is no collection
        if many != want_many {
            return None;
        }
        Some((t, conf))
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
            prefixes.push(format!("{}.", pkg.replace('\\', ".")));
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
        let here = dir_of(&self.files[f].path);
        let pkg = self.files[f].package.as_deref();
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
            let mine: Vec<DefRef> = exact.iter().copied().filter(|r| r.0 == f).collect();
            match (mine.as_slice(), exact.as_slice()) {
                ([one], _) | (_, [one]) => return Some((Target::Def(*one), Confidence::Extracted)),
                _ => {}
            }
        }
        let mine: Vec<DefRef> = seen.iter().copied().filter(|r| r.0 == f).collect();
        match (mine.as_slice(), seen.as_slice()) {
            ([one], _) | (_, [one]) => Some((Target::Def(*one), Confidence::Inferred)),
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
        let cands: Vec<DefRef> = self
            .by_name
            .get(name)?
            .iter()
            .copied()
            .filter(|r| self.family[r.0] == fam)
            .filter(|r| {
                let d = self.def(*r);
                d.kind.is_callable()
                    && (d.kind == DefKind::Method || d.owner.is_some())
                    && !d.decl_only
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

    /// The type of a variable from what constructs its value
    /// (`settings = Settings()`).
    fn state_type(&self, p: DefRef) -> Option<(DefRef, Confidence)> {
        let d = self.def(p);
        let path = d.value_path.as_ref()?;
        let t = self.type_of_path(p.0, d.parent, path, false)?;
        self.def(t).kind.is_class_like().then_some((t, Confidence::Inferred))
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
            let skip = defs[x].extra && !(swift && r.kind != RefKind::Type);
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
) -> (Vec<Value>, Vec<Value>, BTreeMap<String, ResolveStats>) {
    let mut edges: Vec<EdgeOut> = Vec::new();
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
                let found = if r.from_owner.is_some() && heritage {
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
                                // a type alias called: a conversion (Go
                                // `HandlerFunc(f)`)
                                if !(tk.is_callable()
                                    || tk.is_class_like()
                                    || tk == DefKind::TypeAlias)
                                {
                                    continue;
                                }
                                "calls"
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
    (nodes, links, stats)
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
