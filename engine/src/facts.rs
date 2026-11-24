//! What one file yields: definitions with their spans, document headings, and
//! (from phase 2 on) imports and references. The graph is assembled from these.

use serde::{Deserialize, Serialize};

use crate::lang::Lang;

/// What a definition is. Decides its label (`name()`, `.name()`, bare), the
/// relation that links it to its parent, and whether it becomes a graph node.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
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
    /// A property or field of a type (C# / Swift / Kotlin properties, TS class
    /// fields). Not a span `spans.py` reports.
    Property,
    /// A module-level constant (`const X = …` in TS/JS, Rust `const`).
    Constant,
    /// A module-level or package-level variable: state that every function of
    /// the module can read and write (Python module assignments, Go `var`,
    /// JS `let`, C globals, Rust `static`).
    Variable,
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

    /// State a definition can read or write: a variable, a property, a constant.
    pub fn is_state(self) -> bool {
        matches!(self, DefKind::Property | DefKind::Constant | DefKind::Variable)
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
            DefKind::Property => "property",
            DefKind::Constant => "constant",
            DefKind::Variable => "variable",
            DefKind::Other => "other",
        }
    }
}

/// One definition. Lines are 1-based and inclusive.
#[derive(Clone, Debug, Serialize, Deserialize)]
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
    /// The type an extension function or method is written for (Kotlin `fun
    /// Km.toSpec()`, C# `static R Name(this string s)`): a member of that type
    /// to its callers, a plain function where it is defined.
    #[serde(default)]
    pub receiver: Option<String>,
    /// A class with no name of its own (Java `new Callback<T>() { … }`, a
    /// Kotlin `object : Listener { … }`), which goes by the type it
    /// implements: no name lookup finds it.
    #[serde(default)]
    pub anonymous: bool,
    /// What the grammar took for a declaration but the source makes a
    /// variable (C++ `Pool pool(context);` beside a local `context`): kept
    /// for the spans, no node and no name.
    #[serde(default)]
    pub misread: bool,
    /// A misread declaration `spans.py`'s grammar reads as the variable it
    /// is (`Random rnd(seed());`): no span either.
    #[serde(default)]
    pub unspanned: bool,
    /// A closure no definition holds (`test('…', () => { … })`, an RSpec
    /// `it '…' do … end`): no node and no name, only the scope of the locals
    /// declared in it. Its parent is the block around it, if any.
    #[serde(default)]
    pub block: bool,
    /// Written in a closure that is called where it is written, as the value
    /// of a declaration of this name (`var Dataset = (function () { function
    /// Dataset() {} return Dataset })()`): the scope around that closure
    /// knows it by its name too.
    #[serde(default)]
    pub returned_out: bool,
    /// A C# property with a body (`get { … }`, `set => …`, `=> expr`): what
    /// its body uses is the property's use, as a method's is the method's.
    #[serde(default)]
    pub computed: bool,
    /// C, C++: a field declared with an anonymous struct type (`struct {
    /// void *data; void (*addRead)(void *); } ev;`): the bytes of that body,
    /// whose members the grammar gives to the struct around.
    #[serde(default)]
    pub inline_body: Option<(usize, usize)>,
    /// JS `var req = Object.create(http.IncomingMessage.prototype)`: what the
    /// object is created from (`http.IncomingMessage.prototype`).
    #[serde(default)]
    pub created_from: Option<Vec<String>>,
    /// JS `const Article = mongoose.model('Article')`: the model it names.
    #[serde(default)]
    pub model_name: Option<String>,
    /// How many parameters a callable declares.
    #[serde(default)]
    pub params: Option<u8>,
    /// Those parameters, in order: what tells one overload from another.
    #[serde(default)]
    pub plist: Vec<ParamFact>,
    /// Names a type answers to without a definition of them: Ruby
    /// `attr_accessor :status`, the members of the `Struct.new(:a, :b)` it
    /// inherits, `def_delegators`.
    #[serde(default)]
    pub attrs: Vec<String>,
    /// The names of the type parameters a callable declares (`<T>`).
    #[serde(default)]
    pub type_params: Vec<String>,
    /// For each of `type_params`, the first type it is bound by (`<S: Sink>`,
    /// `where T : Foo`): what a member asked of the parameter is a member of.
    #[serde(default)]
    pub type_bounds: Vec<Option<Vec<String>>>,
    /// The type parameters of a type that are bound by the type itself
    /// (`interface Builder<T : Builder<T>>`): a method that returns one
    /// returns the type it was called on.
    #[serde(default)]
    pub self_params: Vec<String>,
    /// The receiver of the callable's last function-typed parameter (Kotlin
    /// `block: Builder.() -> Unit`): what `this` is in a lambda passed to it.
    #[serde(default)]
    pub lambda_receiver: Option<Vec<String>>,
    /// What a callable's `return`s and expression body construct or call
    /// (`return Foo()`, `fun f() = Builder(x)`, `return self`), as paths: the
    /// result of one that declares none.
    #[serde(default)]
    pub returns: Vec<Vec<String>>,
    /// Inside a test region of a source file (Rust `#[cfg(test)]`, `#[test]`).
    pub is_test: bool,
    /// The object a function is assigned to as a property (JS `res.send =
    /// function`, `View.prototype.render = …`, `{ get() {} }` in `const app =
    /// {…}`): what `this` means inside it when no class encloses it.
    pub object: Option<String>,
    /// Found by the reference walk (a property, a constant), not by the
    /// `spans.py` rule: left out of `file_spans`.
    pub extra: bool,
    /// A C/C++ prototype or a method declared in its class: a declaration
    /// whose body is elsewhere.
    pub decl_only: bool,
    /// A variable or field that holds a collection or a builder (`{}`, `[]`,
    /// `new Map()`, `mutableListOf()`, `List<T>`): a call of a mutating method
    /// on it (`add`, `set`, `clear`) changes it. On anything else such a name
    /// says nothing (`client.delete(url)` sends a request).
    #[serde(default)]
    pub container: bool,
    /// The database table a data model maps to: a class with `__tablename__`,
    /// `@Table`, `[Table]`, `$table`, or one an ORM base makes a model (the
    /// class name, lower-cased), an EF `DbSet<T> Users` property, a Mongoose
    /// `mongoose.model('User')`.
    #[serde(default)]
    pub table: Option<String>,
    /// A variable's or constant's value when it is a short string literal
    /// (`API_V1_STR = "/api/v1"`), for a route prefix that names it.
    #[serde(default)]
    pub value: Option<String>,
    /// What constructs a variable's value (`settings = Settings()`: `Settings`),
    /// so `settings.API_V1_STR` resolves through it.
    #[serde(default)]
    pub value_path: Option<Vec<String>>,
    /// The element type of the collection a field is declared as, or a
    /// callable declares it returns (of a map: its values').
    #[serde(default)]
    pub elem: Option<Vec<String>>,
    /// That collection is a map: `elem` is its values' type, and iterating it
    /// gives entries (or keys), not values.
    #[serde(default)]
    pub keyed: bool,
    /// The type of that map's keys.
    #[serde(default)]
    pub key: Option<Vec<String>>,
    /// The names a callable returns as a tuple or array literal (`return
    /// [copiedText, copy]`), in order: what a part names when its type does
    /// not.
    #[serde(default)]
    pub returned_parts: Vec<String>,
    /// The members of an object literal a function returns, as `(key, the
    /// name its value is)`: `return { show, hide: close }` gives `(show,
    /// show)` and `(hide, close)`.
    #[serde(default)]
    pub returned_members: Vec<(String, String)>,
    /// The lines a name is seen in, when that is narrower than its parent:
    /// an RSpec `let(:conn)` is a name of the `describe` / `context` block
    /// it is written in.
    #[serde(default)]
    pub scope_lines: Option<(u32, u32)>,
    /// The type arguments written on a field's declared type, or on the type a
    /// callable declares it returns (`Response<User>`: `[["User"]]`); a type
    /// parameter of the class around is written as its name.
    #[serde(default)]
    pub targs: Vec<Vec<String>>,
    /// For each of `targs`, the element type when it is a collection
    /// (`Call<List<Contributor>>`: `Contributor`).
    #[serde(default)]
    pub targ_elems: Vec<Option<Vec<String>>>,
    /// A class's bases written with type arguments (`class UserRepo :
    /// Repo<User>`: `(["Repo"], [["User"]])`).
    #[serde(default)]
    pub base_targs: Vec<(Vec<String>, Vec<Vec<String>>)>,
    /// The parts of the tuple a callable declares it returns (`-> (A, B)`,
    /// `Pair<A, B>`, Go `(A, B, error)`), or a Rust tuple struct's numbered
    /// fields, in order; an empty path for a part with no type to name.
    #[serde(default)]
    pub rets: Vec<Vec<String>>,
    /// Which of those parts is a closed wrapper (a Rust tuple struct's
    /// `Arc<Mutex<State>>` field); empty when none is recorded.
    #[serde(default)]
    pub part_sealed: Vec<bool>,
    /// The type a variable, field or constant is declared with, as written.
    #[serde(default)]
    pub declared_type: Option<Vec<String>>,
    /// The first name of the outer type that declaration writes, before a
    /// wrapper is taken off (`std` of `std::future<T>`): only the diagnostics
    /// read it.
    #[serde(default)]
    pub written_head: Option<String>,
    /// The arguments a C++ alias or typedef writes for the template it names,
    /// as written (`Response`, `true` of `AsyncWrapper<Response, true>`):
    /// what picks one of the template's specializations.
    #[serde(default)]
    pub alias_args: Vec<String>,
    /// What the wrapper a field is declared as, or a callable declares it
    /// returns, holds: `Option<Foo>`, `Promise<Foo>`, `Box<dyn Foo>` → `Foo`.
    #[serde(default)]
    pub wraps: Option<Vec<String>>,
    /// That wrapper holds its value closed (`Option`, `Optional`, `Mutex`,
    /// `Promise`, `Task`): what is called on it is the wrapper's own method
    /// until it is opened (`unwrap()`, `get()`, `await`, `?`, `.Result`).
    #[serde(default)]
    pub sealed: bool,
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
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Heading {
    pub level: u32,
    pub text: String,
    pub line: u32,
    pub end_line: u32,
}

/// A name of code written in a document: a Markdown code span (`` `Client` ``),
/// a reStructuredText role (``:class:`Client` ``), inline literal
/// (``` ``Client`` ```) or object directive (`.. class:: Client`). `docs::link`
/// turns the ones that name one project definition into `documents` edges.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct DocMention {
    /// As written, without decoration: `httpx.Client.send`, `Model.save`.
    pub name: String,
    pub line: u32,
    pub form: MentionForm,
    /// reST: the module a `.. module::` / `.. currentmodule::` above it names,
    /// which a name relative to it is read under first.
    pub module: Option<String>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum MentionForm {
    /// A code span or inline literal: something written as code.
    Literal,
    /// A role naming an object (`:func:`, `:class:`, `:meth:` …).
    Role,
    /// A directive describing an object (`.. class::`, `.. autofunction::`).
    Directive,
}

impl MentionForm {
    pub fn context(self) -> &'static str {
        match self {
            MentionForm::Literal => "literal",
            MentionForm::Role => "role",
            MentionForm::Directive => "directive",
        }
    }
}

/// When an import runs: what graphlore's `ImportChecker` calls its kind.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
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
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
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
#[derive(Clone, Debug, Serialize, Deserialize)]
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
    /// Rust `pub use`: what it imports, the module passes on.
    #[serde(default)]
    pub public: bool,
    #[serde(skip)]
    pub byte: usize,
    /// Rust: the block a `use` is written in (`fn f() { use m::g; … }`), whose
    /// names only it sees; `None` for a `use` of the module.
    #[serde(default)]
    pub scope: Option<(usize, usize)>,
    /// Rust 2018 paths: the inline modules around a `use` whose path starts at
    /// a plain name, which is looked up in the innermost of them first.
    #[serde(default)]
    pub within: Vec<String>,
}

/// What a reference is.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
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
    /// A bare name read as a value: a module variable, a constant, a field
    /// seen without `this`.
    Read,
    /// A name or member assigned to, or changed in place (`x += 1`,
    /// `x[k] = v`, `x.append(v)`).
    Write,
}

/// A name with a known type: a typed parameter, a typed field, a variable
/// assigned a construction (`x = Foo()`, `let x = Foo::new()`).
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Local {
    pub name: String,
    /// The type's path, or the constructor call's (`["Foo", "new"]`).
    pub path: Vec<String>,
    /// Written as the variable's type (parameter, field, `Foo x`), not
    /// guessed from what it is assigned.
    pub declared: bool,
    /// A field of a type (`this.x` / `self.x`), not a local variable.
    pub field: bool,
    /// From a plain assignment where assigning declares nothing (`service =
    /// retrofit.create(…)` in Java): a hint of what the assigned variable
    /// holds, not a local of its own, so it hides no outer name.
    #[serde(default)]
    pub assigned: bool,
    /// The element type of the collection the local is declared as (`xs:
    /// List<Foo>`, `Foo[] xs`, `xs []Foo`): what a loop over it, a lambda
    /// given to its `forEach`, `xs.first()` and `xs[0]` have.
    #[serde(default)]
    pub elem: Option<Vec<String>>,
    /// Declared as a map (`elem` is its values' type; see `Def::keyed`).
    #[serde(default)]
    pub keyed: bool,
    /// The type of that map's keys.
    #[serde(default)]
    pub key: Option<Vec<String>>,
    /// The type arguments its declared type is written with (see
    /// `Def::targs`): what the class's type parameters stand for here.
    #[serde(default)]
    pub targs: Vec<Vec<String>>,
    /// For each of `targs`, the element type when it is a collection.
    #[serde(default)]
    pub targ_elems: Vec<Option<Vec<String>>>,
    /// The arguments of the call the value is, as far as the site shows
    /// (at most 4): what binds a type parameter the function returns.
    #[serde(default)]
    pub value_args: Vec<ArgFact>,
    /// The type the value's call is handed explicitly: its one type argument
    /// (`mock<Foo>()`, `GetService<IFoo>()`) or a class literal among its
    /// arguments (`create(Service::class.java)`, `typeof(Bar)`).
    #[serde(default)]
    pub given: Option<Vec<String>>,
    /// Declared as a wrapper that holds its value closed (`opt: Optional<Foo>`,
    /// `Arc<Mutex<Pool>>`): the path is what it holds (see `Def::sealed`).
    #[serde(default)]
    pub sealed: bool,
    /// The first name of the type its declaration writes, as written (see
    /// `Def::written_head`): only the diagnostics read it.
    #[serde(default)]
    pub written_head: Option<String>,
    pub from_def: Option<usize>,
    /// Where it is declared: a use written before it is no use of it, and of
    /// two of one name the one declared last before a use is meant.
    #[serde(default)]
    pub byte: usize,
    /// Where the block it is declared in ends (0: the whole definition, or a
    /// language without block scopes): of two of one name, a use has the one
    /// whose block it is written in.
    #[serde(default)]
    pub until: usize,
    /// Where its declaration ends, for one declared with a value (0: none):
    /// inside its own value the name is still the outer one.
    #[serde(default)]
    pub after: usize,
}

/// A reference's context. An alias, not `&'static str` written out, so that
/// serde does not take the field as borrowed from what it deserializes.
pub type Context = &'static str;

/// A use of a name.
#[derive(Clone, Debug, Serialize, Deserialize)]
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
    #[serde(deserialize_with = "crate::cache::static_str")]
    pub context: Context,
    /// The innermost definition the use sits in.
    pub from_def: Option<usize>,
    /// The type a use belongs to when no definition encloses it: a Rust
    /// `impl Trait for Type` (the use is `Type`'s).
    pub from_owner: Option<String>,
    /// The innermost lambda with a receiver the use sits in (an index into
    /// `FileFacts::receivers`): there a bare name is the receiver's member.
    #[serde(default)]
    pub recv: Option<u16>,
    /// How many arguments a call or construction passes, where the site
    /// shows them: what picks one of several constructors.
    #[serde(default)]
    pub args: Option<u8>,
    /// One of those arguments is `this` / `self`.
    #[serde(default)]
    pub self_arg: bool,
    /// What each argument is, as far as the site shows (at most 8).
    #[serde(default)]
    pub hints: Vec<ArgFact>,
    /// Where a Swift implicit member (`.status(301)`) takes its type from.
    #[serde(default)]
    pub implicit: Option<Implicit>,
    /// How many type arguments a call writes for what it calls
    /// (`client.Get<User>(id)`: 1); 0 when it writes none.
    #[serde(default)]
    pub targs: u8,
    /// C#, for a type written: how many type arguments it is written with
    /// (`RestResponse` is 0, `RestResponse<T>` 1). C# tells types of one
    /// name apart by it.
    #[serde(default)]
    pub arity: Option<u8>,
    /// JS `this.x` in a function expression no definition holds: what the
    /// function is attached to, `app` of `app[method] = function () {…}`, or
    /// the call it is passed to, `UserSchema.path.().validate` of
    /// `UserSchema.path('name').validate(function () {…})`.
    #[serde(default)]
    pub this_of: Option<Vec<String>>,
    #[serde(default)]
    pub byte: usize,
}

/// What types a Swift implicit member expression (`.name(…)`, the path
/// `[refs::IMPLICIT, name]`): the type is not written, the place is.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub enum Implicit {
    /// An argument of a call: the index of that call among the file's
    /// references, the argument's position and its label.
    Arg { call: u32, index: u8, label: Option<String> },
    /// The value of a declaration that writes its type (`let e: Endpoint =
    /// .status(1)`).
    Typed(Vec<String>),
    /// The value a function returns: its declared result.
    Return,
    /// Beside a value whose type it takes: the other side of `==`, the target
    /// of an assignment, the subject of a `switch` (`b == .body`, `case
    /// .body:`). The value's path.
    Value(Vec<String>),
}

/// A parameter of a callable.
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct ParamFact {
    /// The name a caller writes for it (Swift's external label; `_` for none).
    pub name: String,
    /// Its written type as a path; empty when it has none.
    pub ty: Vec<String>,
    /// The element type, when that type is a collection's.
    #[serde(default)]
    pub elem: Option<Vec<String>>,
    /// That type is a map's (see `Def::keyed`).
    #[serde(default)]
    pub keyed: bool,
    /// The type of that map's keys.
    #[serde(default)]
    pub key: Option<Vec<String>>,
    /// It has a default value.
    pub optional: bool,
    /// It takes any number of arguments (`vararg`, `...`, `params`).
    pub variadic: bool,
    /// Its type exactly as written, type arguments and all, without spaces
    /// (`IRequest<TResponse>`); empty where the grammar has no `type` field.
    #[serde(default)]
    pub written: String,
    /// The types of the union it is written with, `None` left out (Python
    /// `A | B | None`, `Union[A, B]`): what a member asked of it may be of.
    #[serde(default)]
    pub alts: Vec<Vec<String>>,
}

/// What an argument of a call is.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub enum Arg {
    Unknown,
    Str,
    Num,
    /// A number written as an integer literal (`3`, `0x1F`; not `3L`, `1.5`,
    /// `2f`): Java ranks `int` over `long`, `float`, `double` for it.
    Int,
    Bool,
    Null,
    /// A lambda or function literal.
    Func,
    /// A class literal: `Foo::class`, `Foo.class`, `typeof(Foo)`.
    Class,
    /// A name, a member or a call: the resolver says what type that gives.
    Path(Vec<String>),
    /// A Swift implicit member (`.post`, `.status(301)`): a static member of
    /// the parameter's own type, so only a type that has one of that name
    /// takes it.
    Implicit(String),
}

/// An argument: the name it is passed under, if any, and what it is.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ArgFact {
    pub label: Option<String>,
    pub kind: Arg,
}

/// Where the implicit receiver of a lambda comes from.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub enum ReceiverOf {
    /// `x.apply { … }`, `x.run { … }`, `with(x) { … }`: the value, as a path.
    Value(Vec<String>),
    /// `withIndent { … }`: the function called; its last parameter says the
    /// receiver when it is typed `T.() -> R` (`Def::lambda_receiver`).
    Callee(Vec<String>),
}

/// A lambda with a receiver, and the one around it.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ReceiverScope {
    pub of: ReceiverOf,
    pub outer: Option<u16>,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct ParseStatus {
    /// The tree has ERROR or MISSING nodes; the definitions found are partial.
    pub has_error: bool,
    /// The parse ran past its deadline and was abandoned.
    pub timed_out: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
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
    /// The names of code a document writes (`DocMention`).
    pub doc_mentions: Vec<DocMention>,
    pub test_regions: Vec<(u32, u32)>,
    pub imports: Vec<ImportFact>,
    /// `(local name, exported name)` for names a module exports under another
    /// name or as its default (JS/TS `export { a as b }`, `export default X`,
    /// `module.exports = X`).
    pub exports: Vec<(String, String)>,
    /// C++ `namespace u = util;`: the alias and the namespace it names.
    #[serde(default)]
    pub ns_aliases: Vec<(String, Vec<String>)>,
    /// C++ `inline namespace v2 { … }`: the first byte of each, whose members
    /// are members of the namespace around ([namespace.def]).
    #[serde(default)]
    pub inline_namespaces: Vec<usize>,
    /// Scala `export p.print`, `export q.{a, b}`: the first byte of the
    /// clause and the path to each member it forwards (`["p", "print"]`).
    #[serde(default)]
    pub forwards: Vec<(usize, Vec<String>)>,
    pub refs: Vec<RefFact>,
    pub locals: Vec<Local>,
    /// Names a type answers to without a definition of that name: Ruby
    /// `attr_accessor :status`, `Struct.new(:headers)`, `def_delegators`.
    #[serde(default)]
    pub attrs: Vec<String>,
    /// RSpec `config.include Helpers`: the modules every spec has the
    /// methods of.
    #[serde(default)]
    pub spec_includes: Vec<Vec<String>>,
    /// RSpec `shared_examples 'an adapter' do … end`: its name and the lines
    /// of its block.
    #[serde(default)]
    pub shared_examples: Vec<(String, u32, u32)>,
    /// RSpec `it_behaves_like 'an adapter'`, `include_examples '…'`: the name
    /// and the line, where the examples run with the names in scope there.
    #[serde(default)]
    pub example_includes: Vec<(String, u32)>,
    /// C, C++: a function put in a slot of a struct (`.close = netClose` in
    /// an initializer, `c->funcs->read = netRead`).
    #[serde(default)]
    pub slots: Vec<SlotFact>,
    /// JS `mongoose.model('Article', ArticleSchema)`: the models this file
    /// registers, with the schema each is made of.
    #[serde(default)]
    pub models: Vec<(String, String)>,
    /// JS: a function passed to a call (`User.load(options, function (err,
    /// user) {…})`): the byte it starts at (what its parameters are renamed
    /// with, `user@483`), the call's path and its parameters in order.
    #[serde(default)]
    pub callbacks: Vec<(usize, Vec<String>, Vec<String>)>,
    /// JS: names of properties defined at run time
    /// (`Object.defineProperty(this, 'router', …)`, the keys of `Object.create(p,
    /// { app: … })`).
    #[serde(default)]
    pub runtime_props: Vec<String>,
    /// JS `req.article = …`: properties project code puts on a request.
    #[serde(default)]
    pub request_props: Vec<String>,
    /// JS `const article = req.article`: a name given a request's property
    /// (the name, the property).
    #[serde(default)]
    pub request_aliases: Vec<(String, String)>,
    /// Lambdas with a receiver (Kotlin `x.apply { … }`, `withIndent { … }`).
    #[serde(default)]
    pub receivers: Vec<ReceiverScope>,
    /// Environment variables, files, tables, events and keys the file uses.
    #[serde(default)]
    pub resources: Vec<crate::resources::ResourceUse>,
    /// Web routes, routers, mounts and global prefixes the file declares.
    #[serde(default)]
    pub http: crate::http::HttpFacts,
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
            doc_mentions: Vec::new(),
            test_regions: Vec::new(),
            imports: Vec::new(),
            exports: Vec::new(),
            ns_aliases: Vec::new(),
            inline_namespaces: Vec::new(),
            forwards: Vec::new(),
            refs: Vec::new(),
            locals: Vec::new(),
            attrs: Vec::new(),
            spec_includes: Vec::new(),
            shared_examples: Vec::new(),
            example_includes: Vec::new(),
            slots: Vec::new(),
            models: Vec::new(),
            callbacks: Vec::new(),
            runtime_props: Vec::new(),
            request_props: Vec::new(),
            request_aliases: Vec::new(),
            receivers: Vec::new(),
            resources: Vec::new(),
            http: Default::default(),
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

/// A function put in a slot of a struct: what holds the struct (`byName` of
/// `static funcs byName = { .close = netClose }`, `c->f` of `c->f.read =
/// netRead`), the slot by name or by position in the initializer, and the
/// function's name.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct SlotFact {
    pub holder: Vec<String>,
    pub field: Option<String>,
    pub position: Option<u32>,
    pub value: String,
    pub line: u32,
    pub from_def: Option<usize>,
    #[serde(skip)]
    pub byte: usize,
}
