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
            for (di, d) in f.defs.iter().enumerate() {
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
                let l = &self.files[f].locals[li];
                if l.path.is_empty() {
                    return None;
                }
                let t = self.type_of_path(f, Some(x), &l.path, l.declared)?;
                let conf = if l.declared { Confidence::Extracted } else { Confidence::Inferred };
                return Some((t, conf));
            }
            cur = defs[x].parent;
        }
        None
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
        self.resolve_path_inner(f, d, path, bases).or_else(|| {
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

    fn resolve_path_inner(
        &self,
        f: usize,
        d: Option<usize>,
        path: &[String],
        bases: bool,
    ) -> Option<(Target, Confidence)> {
        let fam = self.family[f]?;
        let (head, rest) = path.split_first()?;
        let mut conf = Confidence::Extracted;
        // `val builder = builder(x).tag(y)`: inside its own initializer the
        // name is still the outer one
        let own_init = self.own_initializer(f, d, path);
        let mut t = match head.as_str() {
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
                && (self.is_untyped_local(f, d, head) || self.is_external_local(f, d, head)) =>
            {
                if rest.len() == 1 && !bases && *self.calling.borrow() {
                    let on = self.local_written_type(f, d, head);
                    // a type the project does not define has no method of
                    // the project's; a local of no known type is an unknown
                    // receiver: a distinctive method name may say what it is
                    // (not where every type of the module is in scope with
                    // no import to say which: Ruby, Swift)
                    let open_world = matches!(fam, Family::Ruby | Family::Swift);
                    if open_world || self.is_external_local(f, d, head) {
                        return self.extension_method(f, &rest[0], on.as_deref());
                    }
                    return self.unknown_receiver(f, &rest[0], on.as_deref());
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
            if self.def(p).kind.is_state() && !rest.is_empty() {
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
        for (i, seg) in rest.iter().enumerate() {
            if seg == "()" {
                // the result of a call: constructing a type gives one of it;
                // anything else returns what the walk cannot know
                if let Target::Def(c) = t {
                    if self.def(c).kind.is_class_like() {
                        continue;
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
            let found = self.member(&t, seg, 3);
            // a property with more after it: go on through its type
            let through_property = matches!(&found, Some(Target::Def(p))
                if matches!(self.def(*p).kind, DefKind::Property | DefKind::Constant))
                && i + 1 < rest.len();
            if through_property {
                if let Target::Def(c) = t {
                    if let Some((ft, cf)) = self.field_type(c, seg, 3) {
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
        Some((t, conf))
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
        let here = dir_of(&self.files[f].path);
        let pkg = self.files[f].package.as_deref();
        // Rust: a method of an `impl Trait for` a type the project does not
        // define (`impl RequestExt for http::Request<()>`) extends that type
        let mut pool: Vec<DefRef> =
            self.extensions.get(name).into_iter().flatten().copied().collect();
        if fam == Some(Family::Rust) {
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
            _ => None,
        }
    }

    /// The bare name of the type a Rust method is written for, when the
    /// project does not define that type (`http::Request<()>` -> `Request`).
    fn extended_type(&self, r: DefRef) -> Option<String> {
        if self.family[r.0] != Some(Family::Rust) {
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
            for r in &facts.refs {
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
                let found = if r.from_owner.is_some() && heritage {
                    p.resolve_path(f, None, &r.path, true)
                } else {
                    p.resolve_path(f, scope_def, &r.path, strict)
                };
                *p.types_only.borrow_mut() = false;
                *p.calling.borrow_mut() = false;
                *p.recv.borrow_mut() = None;
                *p.args.borrow_mut() = None;
                *p.self_arg.borrow_mut() = false;
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
                let fits: Vec<DefRef> = built
                    .iter()
                    .copied()
                    .filter(|c| r.args.is_some() && p.def(*c).params == r.args)
                    .collect();
                let kotlin = facts.lang == Some(Lang::Kotlin);
                let one = if built.len() == 1 && !kotlin {
                    built.first()
                } else if fits.len() == 1 {
                    fits.first()
                } else {
                    None
                };
                let sure = !kotlin;
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
