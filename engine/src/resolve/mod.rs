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

use crate::facts::{Def, DefKind, FileFacts, ImportForm, RefFact, RefKind, Runs};
use crate::graph::assemble::Assembled;
use crate::graph::ids::slug;
use crate::lang::Lang;

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
    /// Import -> files a resolved use went through it to.
    used: RefCell<HashMap<(usize, usize), HashSet<usize>>>,
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
            used: RefCell::new(HashMap::new()),
        };
        p.index();
        p.index_locals();
        p.modsys = modules::ModuleSystems::build(&p);
        p.scopes = (0..files.len()).map(|f| p.scope_of(f)).collect();
        p
    }

    fn index(&mut self) {
        for (fi, f) in self.files.iter().enumerate() {
            let mut top: HashMap<String, Vec<usize>> = HashMap::new();
            let mut children: HashMap<usize, HashMap<String, Vec<usize>>> = HashMap::new();
            let fam = self.family[fi];
            for (di, d) in f.defs.iter().enumerate() {
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
                self.by_qual.entry(d.qualname.clone()).or_default().push((fi, di));
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
    fn type_of_path(&self, f: usize, d: Option<usize>, path: &[String]) -> Option<DefRef> {
        {
            let mut depth = self.depth.borrow_mut();
            if *depth > 3 {
                return None;
            }
            *depth += 1;
        }
        let out = self.type_of_path_inner(f, d, path);
        *self.depth.borrow_mut() -= 1;
        out
    }

    fn type_of_path_inner(&self, f: usize, d: Option<usize>, path: &[String]) -> Option<DefRef> {
        let found = self.resolve_path(f, d, path, true);
        match found? {
            (Target::Def(r), _) => {
                let def = self.def(r);
                let k = def.kind;
                if k.is_class_like() || k == DefKind::TypeAlias {
                    Some(r)
                } else if let Some(t) = k.is_callable().then(|| self.return_type(r)).flatten() {
                    // a call of a function whose declared result is a project type
                    Some(t)
                } else if k.is_callable() && path.len() == 2 && is_factory(&def.name) {
                    // `Foo::new()`, `Foo.create()`: a type's own constructor
                    let owner = self.class_of(r.0, Some(r.1))?;
                    (self.def(owner).name == path[0]).then_some(owner)
                } else if k == DefKind::Constructor {
                    self.class_of(r.0, Some(r.1))
                } else {
                    None
                }
            }
            _ => None,
        }
    }

    /// The project type a callable declares it returns.
    fn return_type(&self, r: DefRef) -> Option<DefRef> {
        let rt = self.files[r.0].refs.iter().find(|x| {
            x.from_def == Some(r.1) && x.kind == RefKind::Type && x.context == "return_type"
        })?;
        let path = rt.path.clone();
        let t = self.type_of_path(r.0, self.def(r).parent, &path)?;
        self.def(t).kind.is_class_like().then_some(t)
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
                let t = self.type_of_path(f, Some(x), &l.path)?;
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
            let t = self.type_of_path(fi, l.from_def, &l.path)?;
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
            .get(&d.qualname)
            .into_iter()
            .flatten()
            .copied()
            .filter(|c| !self.def(*c).decl_only && self.family[c.0] == fam)
            .collect();
        (cands.len() == 1).then(|| cands[0])
    }

    /// Top-level `name` in files.
    fn top_in(&self, files: &[usize], name: &str) -> Option<DefRef> {
        self.best(
            files
                .iter()
                .flat_map(|&f| self.top[f].get(name).into_iter().flatten().map(move |&d| (f, d))),
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
                self.class_member(*c, name, 4).map(Target::Def)
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
            .filter(|(_, d)| d.kind.is_class_like() && d.name == name)
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

    /// A bare name as the language sees it from `d` in file `f`.
    fn resolve_name(&self, f: usize, d: Option<usize>, name: &str) -> Option<Target> {
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
                        return Some(Target::Def(r));
                    }
                }
            }
            if fam.implicit_this() && defs[x].kind.is_class_like() {
                if let Some(m) = self.class_member((f, x), name, 4) {
                    return Some(Target::Def(m));
                }
            }
            cur = defs[x].parent;
        }
        if fam.implicit_this() {
            if let Some(c) = self.class_of(f, d) {
                if let Some(m) = self.class_member(c, name, 4) {
                    return Some(Target::Def(m));
                }
            }
        }
        if let Some(r) = self.top_in(&[f], name) {
            return Some(Target::Def(r));
        }
        let scope = &self.scopes[f];
        if let Some((b, imp)) = scope.bindings.get(name) {
            if let Some(t) = self.eval(b, 3) {
                self.note_use(f, *imp, &t);
                return Some(t);
            }
        }
        if let Some(t) = modules::scope_lookup(self, fam, f, d, name) {
            return Some(t);
        }
        for (star, imp) in &scope.stars {
            if let Some(t) = self.member(star, name, 2) {
                self.note_use(f, *imp, &t);
                return Some(t);
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
        let fam = self.family[f]?;
        let (head, rest) = path.split_first()?;
        let mut conf = Confidence::Extracted;
        let mut t = match head.as_str() {
            "self" => {
                if rest.is_empty() {
                    return None;
                }
                match self.class_of(f, d) {
                    Some(c) => Target::Def(c),
                    // JS `this` inside a function assigned to an object
                    None => {
                        let m = self.object_member(f, d, &rest[0])?;
                        return self.walk(Target::Def(m), &rest[1..], conf);
                    }
                }
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
                return self
                    .fallback_method(f, &rest[0])
                    .map(|r| (Target::Def(r), Confidence::Inferred));
            }
            _ if self.is_untyped_local(f, d, head) || self.is_external_local(f, d, head) => {
                return None
            }
            _ => match self
                .local_type(f, d, head)
                .map(|(t, c)| {
                    conf = c;
                    Target::Def(t)
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
                        let last = rest.last()?;
                        return self
                            .fallback_method(f, last)
                            .map(|r| (Target::Def(r), Confidence::Inferred));
                    } else {
                        return None;
                    }
                }
            },
        };
        for (i, seg) in rest.iter().enumerate() {
            if seg == "()" {
                // the result of a call: constructing a type gives one of it;
                // anything else returns what the walk cannot know
                if let Target::Def(c) = t {
                    if self.def(c).kind.is_class_like() {
                        continue;
                    }
                }
                if rest.len() == i + 2 && !bases {
                    return self
                        .fallback_method(f, &rest[i + 1])
                        .map(|r| (Target::Def(r), Confidence::Inferred));
                }
                return None;
            }
            match self.member(&t, seg, 3) {
                Some(next) => t = next,
                None => {
                    // `self.repo.find()`: through the field's type
                    let Target::Def(c) = t else { return None };
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
                    .filter(|r| self.family[r.0] == Some(fam));
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
        (cands.len() == 1).then(|| cands[0])
    }

    /// The node a use is made from: the innermost enclosing definition that
    /// is a node (an extension's type, an impl's type), else the file.
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
        while let Some(x) = cur {
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
        for (f, facts) in files.iter().enumerate() {
            let Some(fam) = p.family[f] else { continue };
            let st = stats.entry(fam.name().to_string()).or_default();
            for r in &facts.refs {
                let src = p.source_of(f, r);
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
                // attribute reads and values never fall back to a name guess
                let strict = heritage || matches!(r.kind, RefKind::Attr);
                let found = if r.from_owner.is_some() && heritage {
                    p.resolve_path(f, None, &r.path, true)
                } else {
                    p.resolve_path(f, scope_def, &r.path, strict)
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
                            RefKind::Attr => {
                                if !tk.is_callable() {
                                    continue;
                                }
                                "references"
                            }
                            RefKind::Indirect => {
                                if conf != Confidence::Extracted {
                                    continue;
                                }
                                if tk.is_callable() {
                                    "indirect_call"
                                } else if tk.is_class_like() {
                                    "uses" // a type handed over as a value
                                } else {
                                    continue;
                                }
                            }
                        };
                        (id.to_string(), rel, conf)
                    }
                    Some((Target::Ext(e, rest), conf)) => match r.kind {
                        RefKind::Call | RefKind::New => {
                            (ext_target(e, &rest, false), "calls", conf)
                        }
                        RefKind::Decorator => (ext_target(e, &rest, true), "references", conf),
                        RefKind::Type | RefKind::Indirect | RefKind::Attr => continue,
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
                            if r.kind != RefKind::Indirect {
                                st.unresolved += 1;
                            }
                            continue;
                        }
                    }
                };
                if target == src && relation != "calls" {
                    continue;
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
    let nodes: Vec<Value> = ext_nodes
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
    if !renamed.is_empty() {
        for l in links.iter_mut() {
            if let Some(new) = l["target"].as_str().and_then(|t| renamed.get(t)) {
                l["target"] = json!(new);
            }
        }
    }
    (nodes, links, stats)
}

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
