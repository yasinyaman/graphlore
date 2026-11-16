//! Resources: what code reads and writes outside the program's own memory, by
//! a key written in the source. Two functions that read and write one
//! environment variable, one file, one table or one event are coupled although
//! neither calls the other.
//!
//! Each use is a call of a known API (per language, by the path of what is
//! called) whose key is a string literal: `os.getenv("DATABASE_URL")`,
//! `fs.writeFileSync("out.json", s)`, `emitter.emit("ready")`, or a string
//! literal that is SQL (`"SELECT … FROM users"`). A key computed at run time
//! names nothing and is only counted.

use std::sync::OnceLock;

use regex::Regex;
use serde::{Deserialize, Serialize};
use tree_sitter::Node;

use crate::facts::Context;
use crate::generic::{kids, line, text};
use crate::lang::Lang;
use crate::refs::{callee, is_call_kind, path_of, string_value};

/// What kind of thing outside the program a key names.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ResourceKind {
    /// An environment variable.
    Env,
    /// A file, by its path.
    File,
    /// A database table.
    Table,
    /// An event name an emitter or bus carries.
    Event,
    /// A key in a cache or a key-value store (`localStorage`, Redis).
    Key,
}

impl ResourceKind {
    pub fn as_str(self) -> &'static str {
        match self {
            ResourceKind::Env => "env",
            ResourceKind::File => "file",
            ResourceKind::Table => "table",
            ResourceKind::Event => "event",
            ResourceKind::Key => "key",
        }
    }
}

/// One read or write of a resource.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ResourceUse {
    pub line: u32,
    pub kind: ResourceKind,
    pub write: bool,
    pub key: String,
    /// `env`, `open`, `sql`, `emit`, `listen`, `cache`.
    #[serde(deserialize_with = "crate::cache::static_str")]
    pub context: Context,
    /// Whether the API alone says what it is (`os.getenv`), as opposed to a
    /// method name that could belong to anything (`emit`, `get` on a cache).
    pub certain: bool,
    pub from_def: Option<usize>,
    #[serde(skip)]
    pub byte: usize,
}

type Langs = &'static [Lang];

/// `(languages, dotted callee suffix, kind, writes)`: the key is the first
/// string argument.
const CALLS: &[(Langs, &str, ResourceKind, bool)] = &[
    // environment
    (&[Lang::Python], "os.getenv", ResourceKind::Env, false),
    (&[Lang::Python], "os.environ.get", ResourceKind::Env, false),
    (&[Lang::Python], "environ.get", ResourceKind::Env, false),
    (&[Lang::Python], "os.putenv", ResourceKind::Env, true),
    (&[Lang::Python], "os.environ.setdefault", ResourceKind::Env, true),
    (&[Lang::Python], "os.environ.pop", ResourceKind::Env, true),
    (&[Lang::Go], "os.Getenv", ResourceKind::Env, false),
    (&[Lang::Go], "os.LookupEnv", ResourceKind::Env, false),
    (&[Lang::Go], "os.Setenv", ResourceKind::Env, true),
    (&[Lang::Go], "os.Unsetenv", ResourceKind::Env, true),
    (&[Lang::Java, Lang::Kotlin, Lang::Scala], "System.getenv", ResourceKind::Env, false),
    (&[Lang::Scala], "sys.env.get", ResourceKind::Env, false),
    (&[Lang::CSharp], "Environment.GetEnvironmentVariable", ResourceKind::Env, false),
    (&[Lang::CSharp], "Environment.SetEnvironmentVariable", ResourceKind::Env, true),
    (&[Lang::Rust], "env::var", ResourceKind::Env, false),
    (&[Lang::Rust], "env::var_os", ResourceKind::Env, false),
    (&[Lang::Rust], "env::set_var", ResourceKind::Env, true),
    (&[Lang::Rust], "env::remove_var", ResourceKind::Env, true),
    (&[Lang::Ruby], "ENV.fetch", ResourceKind::Env, false),
    (&[Lang::Ruby], "ENV.key?", ResourceKind::Env, false),
    (&[Lang::Php, Lang::C, Lang::Cpp], "getenv", ResourceKind::Env, false),
    (&[Lang::Php, Lang::C, Lang::Cpp], "putenv", ResourceKind::Env, true),
    (&[Lang::C, Lang::Cpp], "setenv", ResourceKind::Env, true),
    (&[Lang::Php], "env", ResourceKind::Env, false),
    (&[Lang::JavaScript, Lang::TypeScript, Lang::Tsx], "Deno.env.get", ResourceKind::Env, false),
    (&[Lang::Swift], "ProcessInfo.processInfo.environment", ResourceKind::Env, false),
    // files
    (&[Lang::JavaScript, Lang::TypeScript, Lang::Tsx], "readFile", ResourceKind::File, false),
    (&[Lang::JavaScript, Lang::TypeScript, Lang::Tsx], "readFileSync", ResourceKind::File, false),
    (
        &[Lang::JavaScript, Lang::TypeScript, Lang::Tsx],
        "createReadStream",
        ResourceKind::File,
        false,
    ),
    (&[Lang::JavaScript, Lang::TypeScript, Lang::Tsx], "writeFile", ResourceKind::File, true),
    (&[Lang::JavaScript, Lang::TypeScript, Lang::Tsx], "writeFileSync", ResourceKind::File, true),
    (&[Lang::JavaScript, Lang::TypeScript, Lang::Tsx], "appendFile", ResourceKind::File, true),
    (&[Lang::JavaScript, Lang::TypeScript, Lang::Tsx], "appendFileSync", ResourceKind::File, true),
    (
        &[Lang::JavaScript, Lang::TypeScript, Lang::Tsx],
        "createWriteStream",
        ResourceKind::File,
        true,
    ),
    (&[Lang::JavaScript, Lang::TypeScript, Lang::Tsx], "fs.unlink", ResourceKind::File, true),
    (&[Lang::JavaScript, Lang::TypeScript, Lang::Tsx], "fs.unlinkSync", ResourceKind::File, true),
    (&[Lang::Go], "os.ReadFile", ResourceKind::File, false),
    (&[Lang::Go], "os.Open", ResourceKind::File, false),
    (&[Lang::Go], "ioutil.ReadFile", ResourceKind::File, false),
    (&[Lang::Go], "os.WriteFile", ResourceKind::File, true),
    (&[Lang::Go], "ioutil.WriteFile", ResourceKind::File, true),
    (&[Lang::Go], "os.Create", ResourceKind::File, true),
    (&[Lang::Go], "os.Remove", ResourceKind::File, true),
    (&[Lang::Java, Lang::Kotlin], "Files.readString", ResourceKind::File, false),
    (&[Lang::Java, Lang::Kotlin], "Files.readAllBytes", ResourceKind::File, false),
    (&[Lang::Java, Lang::Kotlin], "Files.readAllLines", ResourceKind::File, false),
    (&[Lang::Java, Lang::Kotlin], "Files.writeString", ResourceKind::File, true),
    (&[Lang::Java, Lang::Kotlin], "Files.write", ResourceKind::File, true),
    (&[Lang::Java, Lang::Kotlin], "Files.delete", ResourceKind::File, true),
    (&[Lang::Java, Lang::Kotlin, Lang::Scala], "FileReader", ResourceKind::File, false),
    (&[Lang::Java, Lang::Kotlin, Lang::Scala], "FileInputStream", ResourceKind::File, false),
    (&[Lang::Java, Lang::Kotlin, Lang::Scala], "FileWriter", ResourceKind::File, true),
    (&[Lang::Java, Lang::Kotlin, Lang::Scala], "FileOutputStream", ResourceKind::File, true),
    (&[Lang::Java, Lang::Kotlin, Lang::Scala], "PrintWriter", ResourceKind::File, true),
    (&[Lang::CSharp], "File.ReadAllText", ResourceKind::File, false),
    (&[Lang::CSharp], "File.ReadAllBytes", ResourceKind::File, false),
    (&[Lang::CSharp], "File.ReadAllLines", ResourceKind::File, false),
    (&[Lang::CSharp], "File.OpenRead", ResourceKind::File, false),
    (&[Lang::CSharp], "StreamReader", ResourceKind::File, false),
    (&[Lang::CSharp], "File.WriteAllText", ResourceKind::File, true),
    (&[Lang::CSharp], "File.WriteAllBytes", ResourceKind::File, true),
    (&[Lang::CSharp], "File.AppendAllText", ResourceKind::File, true),
    (&[Lang::CSharp], "File.Delete", ResourceKind::File, true),
    (&[Lang::CSharp], "StreamWriter", ResourceKind::File, true),
    (&[Lang::Rust], "fs::read", ResourceKind::File, false),
    (&[Lang::Rust], "fs::read_to_string", ResourceKind::File, false),
    (&[Lang::Rust], "File::open", ResourceKind::File, false),
    (&[Lang::Rust], "fs::write", ResourceKind::File, true),
    (&[Lang::Rust], "File::create", ResourceKind::File, true),
    (&[Lang::Rust], "fs::remove_file", ResourceKind::File, true),
    (&[Lang::Ruby], "File.read", ResourceKind::File, false),
    (&[Lang::Ruby], "File.readlines", ResourceKind::File, false),
    (&[Lang::Ruby], "IO.read", ResourceKind::File, false),
    (&[Lang::Ruby], "File.write", ResourceKind::File, true),
    (&[Lang::Ruby], "IO.write", ResourceKind::File, true),
    (&[Lang::Ruby], "File.delete", ResourceKind::File, true),
    (&[Lang::Php], "file_get_contents", ResourceKind::File, false),
    (&[Lang::Php], "file", ResourceKind::File, false),
    (&[Lang::Php], "file_put_contents", ResourceKind::File, true),
    (&[Lang::Php], "unlink", ResourceKind::File, true),
    (&[Lang::Python], "os.remove", ResourceKind::File, true),
    (&[Lang::Python], "os.unlink", ResourceKind::File, true),
    (&[Lang::Swift], "FileManager.default.contents", ResourceKind::File, false),
    (&[Lang::Swift], "FileManager.default.createFile", ResourceKind::File, true),
    (&[Lang::Swift], "FileManager.default.removeItem", ResourceKind::File, true),
];

/// Calls that open a file in a mode: `(languages, callee suffix, index of the
/// mode argument)`. A mode with `w`, `a`, `x` or `+` writes; any other reads.
const OPENS: &[(Langs, &str, usize)] = &[
    (&[Lang::Python], "open", 1),
    (&[Lang::Python], "io.open", 1),
    (&[Lang::Python], "codecs.open", 1),
    (&[Lang::Php, Lang::C, Lang::Cpp], "fopen", 1),
    (&[Lang::Ruby], "File.open", 1),
    (&[Lang::Go], "os.OpenFile", 1),
];

/// Method names that publish an event (the key is the first argument) and
/// that listen for one.
const EMITS: &[&str] = &["emit", "publish", "trigger", "broadcast", "dispatchEvent", "fire"];
const LISTENS: &[&str] =
    &["on", "once", "addListener", "addEventListener", "subscribe", "listen", "prependListener"];
/// Event names every stream, socket or page emits: one name on unrelated
/// objects, so they say nothing about which code talks to which.
pub const GENERIC_EVENTS: &[&str] = &[
    "data",
    "end",
    "close",
    "error",
    "finish",
    "open",
    "message",
    "connect",
    "connection",
    "abort",
    "aborted",
    "timeout",
    "response",
    "request",
    "drain",
    "readable",
    "pipe",
    "unpipe",
    "change",
    "click",
    "load",
    "resize",
    "scroll",
    "keydown",
    "keyup",
    "keypress",
    "input",
    "submit",
    "focus",
    "blur",
    "DOMContentLoaded",
    "mousedown",
    "mouseup",
    "mousemove",
    "mouseover",
    "mouseout",
    "mouseenter",
    "mouseleave",
    "touchstart",
    "touchend",
    "touchmove",
    "beforeunload",
    "unload",
    "popstate",
    "hashchange",
    "visibilitychange",
    "online",
    "offline",
    "storage",
    "continue",
    "socket",
    "upgrade",
    "lookup",
    "exit",
    "spawn",
    "disconnect",
    "listening",
    "secureConnect",
    "dblclick",
    "contextmenu",
    "wheel",
    "select",
    "paste",
    "copy",
    "cut",
    "drag",
    "drop",
    "dragstart",
    "dragend",
    "dragover",
    "transitionend",
    "animationend",
    "ready",
    "start",
    "stop",
    "success",
    "fail",
    "done",
    "complete",
    "progress",
    "cancel",
    "update",
    "remove",
    "add",
];

/// A receiver named like a store of keys, and the methods that read and write one.
const KEY_STORES: &[&str] =
    &["localstorage", "sessionstorage", "redis", "cache", "memcache", "store", "kv", "session"];
const KEY_READS: &[&str] =
    &["get", "getItem", "hget", "hgetall", "exists", "mget", "smembers", "lrange", "fetch", "read"];
const KEY_WRITES: &[&str] = &[
    "set",
    "setItem",
    "removeItem",
    "hset",
    "del",
    "delete",
    "incr",
    "decr",
    "expire",
    "lpush",
    "rpush",
    "sadd",
    "srem",
    "setex",
    "write",
    "put",
];

/// Record the resources `n` uses. Called for every node of a file.
pub fn visit<'t>(
    n: Node<'t>,
    src: &[u8],
    lang: Option<Lang>,
    anc: &[Node<'t>],
    out: &mut Vec<ResourceUse>,
) {
    let Some(lang) = lang else { return };
    let k = n.kind();
    if is_call_kind(k) || matches!(k, "new_expression" | "object_creation_expression") {
        call(n, src, lang, out);
    }
    env_access(n, src, lang, anc, out);
    if is_string(k) && !anc.last().is_some_and(|p| is_string(p.kind())) {
        sql(n, src, out);
    }
}

fn is_string(k: &str) -> bool {
    matches!(
        k,
        "string"
            | "string_literal"
            | "interpreted_string_literal"
            | "raw_string_literal"
            | "template_string"
            | "encapsed_string"
            | "verbatim_string_literal"
            | "text_block"
            | "line_string_literal"
            | "multi_line_string_literal"
            | "heredoc_body"
    )
}

fn langs_match(langs: Langs, lang: Lang) -> bool {
    langs.is_empty() || langs.contains(&lang)
}

/// Whether `path` ends with the segments of `suffix` (`.`- or `::`-separated).
fn ends_with(path: &[String], suffix: &str) -> bool {
    let segs: Vec<&str> = suffix.split(['.', ':']).filter(|s| !s.is_empty()).collect();
    path.len() >= segs.len()
        && path[path.len() - segs.len()..].iter().zip(&segs).all(|(a, b)| a == b)
}

/// The argument expressions of a call, unwrapped.
fn arguments<'t>(n: Node<'t>) -> Vec<Node<'t>> {
    let args = n.child_by_field_name("arguments").or_else(|| {
        kids(n).into_iter().find(|c| c.kind().contains("argument") || c.kind() == "call_suffix")
    });
    let Some(args) = args else { return Vec::new() };
    let mut out = Vec::new();
    for a in kids(args) {
        let mut a = a;
        // `argument`, `value_argument`, `value_arguments` (Kotlin), keyword
        // arguments' values are not positional
        for _ in 0..3 {
            if matches!(a.kind(), "argument" | "value_argument" | "value_arguments") {
                match a.named_child(0) {
                    Some(c) => a = c,
                    None => break,
                }
            } else {
                break;
            }
        }
        if a.kind() == "keyword_argument" {
            continue;
        }
        out.push(a);
    }
    out
}

/// The text of a string literal with no interpolation in it.
fn literal(n: Node, src: &[u8]) -> Option<String> {
    if !is_string(n.kind()) {
        return None;
    }
    if kids(n).iter().any(|c| {
        let k = c.kind();
        k.contains("interpolation") || k.contains("substitution") || k == "template_substitution"
    }) {
        return None;
    }
    let v = string_value(n, src);
    (!v.is_empty() && v.len() <= 200 && !v.contains('\n')).then_some(v)
}

/// A file path, not a stream (`php://memory`, `http://…`) or a descriptor.
fn is_path(key: &str) -> bool {
    !key.contains("://") && !key.starts_with("/dev/") && key != "-"
}

fn push(
    out: &mut Vec<ResourceUse>,
    n: Node,
    kind: ResourceKind,
    write: bool,
    key: String,
    context: &'static str,
    certain: bool,
) {
    out.push(ResourceUse {
        line: line(n),
        kind,
        write,
        key,
        context,
        certain,
        from_def: None,
        byte: n.start_byte(),
    });
}

fn call(n: Node, src: &[u8], lang: Lang, out: &mut Vec<ResourceUse>) {
    let Some((_, path)) = callee(n, src) else { return };
    if path.is_empty() {
        return;
    }
    let args = arguments(n);
    let first = args.first().and_then(|a| literal(*a, src));
    for (langs, suffix, kind, write) in CALLS {
        if langs_match(langs, lang) && ends_with(&path, suffix) {
            if let Some(key) = first.clone().filter(|k| *kind != ResourceKind::File || is_path(k)) {
                let ctx = if *kind == ResourceKind::Env { "env" } else { "file" };
                push(out, n, *kind, *write, key, ctx, true);
            }
            return;
        }
    }
    for (langs, suffix, mode_at) in OPENS {
        if langs_match(langs, lang) && ends_with(&path, suffix) && path.len() <= 2 {
            if let Some(key) = first.clone().filter(|k| is_path(k)) {
                let mode = args.get(*mode_at).map(|m| text(*m, src)).unwrap_or_default();
                let write = mode.contains(['w', 'a', 'x', '+'])
                    || ["O_WRONLY", "O_RDWR", "O_CREATE", "O_APPEND"]
                        .iter()
                        .any(|f| mode.contains(f));
                push(out, n, ResourceKind::File, write, key, "open", true);
            }
            return;
        }
    }
    let method = path.last().map(String::as_str).unwrap_or("");
    let Some(key) = first else { return };
    if path.len() >= 2 && (EMITS.contains(&method) || LISTENS.contains(&method)) {
        // an event name: a word, not a URL, a selector or a sentence
        if key.chars().all(|c| c.is_alphanumeric() || matches!(c, ':' | '.' | '_' | '-' | '/'))
            && !key.starts_with('/')
            && !GENERIC_EVENTS.contains(&key.as_str())
        {
            let write = EMITS.contains(&method);
            push(
                out,
                n,
                ResourceKind::Event,
                write,
                key,
                if write { "emit" } else { "listen" },
                false,
            );
        }
        return;
    }
    if path.len() >= 2 {
        let receiver = path[path.len() - 2].to_ascii_lowercase();
        if KEY_STORES.iter().any(|s| receiver.contains(s)) {
            let write = KEY_WRITES.contains(&method);
            if write || KEY_READS.contains(&method) {
                push(out, n, ResourceKind::Key, write, key, "cache", false);
            }
        }
    }
}

/// `os.environ["A"]`, `process.env.A`, `process.env["A"]`, `ENV["A"]`,
/// `$_ENV["A"]`: read, or written when assigned to.
fn env_access<'t>(
    n: Node<'t>,
    src: &[u8],
    lang: Lang,
    anc: &[Node<'t>],
    out: &mut Vec<ResourceUse>,
) {
    let k = n.kind();
    let (object, key) = match (lang, k) {
        (Lang::Python, "subscript") => {
            (n.child_by_field_name("value"), n.child_by_field_name("subscript"))
        }
        (Lang::JavaScript | Lang::TypeScript | Lang::Tsx, "subscript_expression") => {
            (n.child_by_field_name("object"), n.child_by_field_name("index"))
        }
        (Lang::JavaScript | Lang::TypeScript | Lang::Tsx, "member_expression") => {
            let obj = n.child_by_field_name("object");
            let is_env = obj
                .and_then(|o| path_of(o, src))
                .is_some_and(|p| p == ["process", "env"] || p == ["import", "meta", "env"]);
            if is_env {
                if let Some(prop) = n.child_by_field_name("property") {
                    let write = crate::state::writes_to(n, anc);
                    push(out, n, ResourceKind::Env, write, text(prop, src), "env", true);
                }
            }
            return;
        }
        (Lang::Ruby, "element_reference") => {
            (n.child_by_field_name("object"), kids(n).into_iter().nth(1))
        }
        (Lang::Php, "subscript_expression") => (n.named_child(0), n.named_child(1)),
        _ => return,
    };
    let (Some(object), Some(key)) = (object, key) else { return };
    let env = match lang {
        // `os.environ`; a bare `environ` is as often WSGI's request dict
        Lang::Python => path_of(object, src).is_some_and(|p| ends_with(&p, "os.environ")),
        Lang::Ruby => text(object, src) == "ENV",
        Lang::Php => text(object, src) == "$_ENV",
        _ => path_of(object, src).is_some_and(|p| p == ["process", "env"]),
    };
    if !env {
        return;
    }
    if let Some(key) = literal(key, src) {
        let write = crate::state::writes_to(n, anc);
        push(out, n, ResourceKind::Env, write, key, "env", true);
    }
}

/// Tables a SQL string reads and writes.
fn sql(n: Node, src: &[u8], out: &mut Vec<ResourceUse>) {
    static VERB: OnceLock<Regex> = OnceLock::new();
    static READ: OnceLock<Regex> = OnceLock::new();
    static WRITE: OnceLock<Regex> = OnceLock::new();
    let verb = VERB.get_or_init(|| {
        Regex::new(r"(?is)^\W*(select|insert|update|delete|with|merge|replace)\s+\S").unwrap()
    });
    let t = text(n, src);
    let body = t.trim_start_matches(|c: char| "\"'`@$rRfFbBuU".contains(c));
    let Some(m) = verb.captures(body) else { return };
    if body.len() < 12 {
        return;
    }
    // `"Update own password"` is a sentence: lower-case SQL has to look like SQL
    let upper = m[1].chars().all(|c| c.is_ascii_uppercase());
    if !upper && !body.contains(['*', '=', '?', '(', ',']) && !body.contains("$1") {
        return;
    }
    let name = r#"([A-Za-z_"`\[][\w$."`\]]*)"#;
    let read = READ.get_or_init(|| Regex::new(&format!(r"(?i)\b(?:from|join)\s+{name}")).unwrap());
    let write = WRITE.get_or_init(|| {
        Regex::new(&format!(
            r"(?i)\b(?:insert\s+(?:or\s+\w+\s+)?into|delete\s+from|merge\s+into|replace\s+into)\s+{name}|\bupdate\s+{name}\s+set\b"
        ))
        .unwrap()
    });
    let clean = |s: &str| -> Option<String> {
        let s = s.trim_matches(|c| matches!(c, '"' | '`' | '[' | ']'));
        let last =
            s.rsplit('.').next().unwrap_or(s).trim_matches(|c| matches!(c, '"' | '`' | '[' | ']'));
        let low = last.to_ascii_lowercase();
        let keyword = matches!(
            low.as_str(),
            "select" | "where" | "set" | "values" | "lateral" | "unnest" | "dual" | "only" | "as"
        );
        (!low.is_empty() && !keyword && low.chars().all(|c| c.is_alphanumeric() || c == '_'))
            .then_some(low)
    };
    // names a `WITH x AS (…)` defines are the statement's own, not tables
    static CTE: OnceLock<Regex> = OnceLock::new();
    let cte = CTE.get_or_init(|| {
        Regex::new(r"(?i)(?:\bwith\s+(?:recursive\s+)?|,\s*)(\w+)\s+as\s*\(").unwrap()
    });
    let own: Vec<String> = cte.captures_iter(body).map(|c| c[1].to_ascii_lowercase()).collect();
    let clean = |s: &str| clean(s).filter(|t| !own.contains(t));
    let mut written = Vec::new();
    for c in write.captures_iter(body) {
        if let Some(t) = c.get(1).or_else(|| c.get(2)).and_then(|m| clean(m.as_str())) {
            written.push(t);
        }
    }
    for t in &written {
        push(out, n, ResourceKind::Table, true, t.clone(), "sql", true);
    }
    for c in read.captures_iter(body) {
        if let Some(t) = clean(&c[1]) {
            if !written.contains(&t) {
                push(out, n, ResourceKind::Table, false, t, "sql", true);
            }
        }
    }
}

/// The table a data model maps to (see `Def::table`): a class declaration with
/// `lang`, or a property / constant declaration (`lang` is `None` then).
pub fn declared_table(n: Node, src: &[u8], lang: Option<Lang>, name: &str) -> Option<String> {
    static EXPLICIT: OnceLock<Regex> = OnceLock::new();
    static IMPLICIT: OnceLock<Regex> = OnceLock::new();
    static DBSET: OnceLock<Regex> = OnceLock::new();
    static MONGOOSE: OnceLock<Regex> = OnceLock::new();
    let mut t = text(n, src);
    // a TS decorator sits on the `export` around the class
    if let Some(p) = n.parent().filter(|p| p.kind() == "export_statement") {
        t = text(p, src);
    }
    let t = &t[..t.len().min(6000)];
    let Some(lang) = lang else {
        // `public DbSet<Item> Items { get; set; }`, `const User = mongoose.model('User', …)`
        let dbset = DBSET.get_or_init(|| Regex::new(r"\bDbSet<\s*[\w.]+\s*>\s+(\w+)").unwrap());
        if let Some(c) = dbset.captures(t) {
            return Some(c[1].to_ascii_lowercase());
        }
        let mongoose = MONGOOSE
            .get_or_init(|| Regex::new(r#"\bmongoose\s*\.\s*model\s*\(\s*['"](\w+)['"]"#).unwrap());
        return mongoose.captures(t).map(|c| c[1].to_ascii_lowercase());
    };
    let explicit = EXPLICIT.get_or_init(|| {
        Regex::new(concat!(
            r#"(?:__tablename__|db_table|self\.table_name|\$table)\s*=\s*['"]([\w.]+)['"]|"#,
            r#"@(?:Table|Entity)\s*\(\s*(?:name\s*=\s*)?['"]([\w.]+)['"]|"#,
            r#"\[Table\(\s*"([\w.]+)""#
        ))
        .unwrap()
    });
    if let Some(c) = explicit.captures(t) {
        let table = c.get(1).or_else(|| c.get(2)).or_else(|| c.get(3))?.as_str();
        return Some(table.rsplit('.').next().unwrap_or(table).to_ascii_lowercase());
    }
    // a class an ORM base or annotation makes a model, named after itself
    let implicit = IMPLICIT.get_or_init(|| {
        Regex::new(concat!(
            r"^[^{:]*?(?:\((?:[\w.]*\.)?(?:Model|SQLModel)\b|<\s*(?:ApplicationRecord|ActiveRecord::Base)\b|",
            r"\bextends\s+(?:Model|Authenticatable|Eloquent)\b|@Entity\b|@Document\b)"
        ))
        .unwrap()
    });
    let header = t.split(['\n']).take(8).collect::<Vec<_>>().join("\n");
    let _ = lang;
    implicit.is_match(&header).then(|| name.to_ascii_lowercase())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn suffixes_match_whole_segments() {
        let p = |s: &str| s.split('.').map(String::from).collect::<Vec<_>>();
        assert!(ends_with(&p("os.getenv"), "os.getenv"));
        assert!(ends_with(&p("fs.promises.readFile"), "readFile"));
        assert!(!ends_with(&p("myos.getenv"), "os.getenv"));
        assert!(ends_with(&["std".into(), "env".into(), "var".into()], "env::var"));
    }

    #[test]
    fn data_models_name_their_tables() {
        let table = |lang: Option<Lang>, file: &str, src: &str, name: &str| {
            let l = crate::lang::detect(file, Some(src.as_bytes())).unwrap();
            let tree = crate::parse::parse(l, src.as_bytes(), None).ok().unwrap();
            let mut stack = vec![tree.root_node()];
            while let Some(n) = stack.pop() {
                if n.child_by_field_name("name").is_some_and(|x| text(x, src.as_bytes()) == name) {
                    return declared_table(n, src.as_bytes(), lang, name);
                }
                stack.extend(kids(n));
            }
            None
        };
        let py = "class User(models.Model):\n    class Meta:\n        db_table = 'app_users'\n\nclass Team(Base):\n    __tablename__ = 'teams'\n\nclass Plain(Base):\n    pass\n\nclass Post(models.Model):\n    pass\n";
        assert_eq!(table(Some(Lang::Python), "m.py", py, "User").as_deref(), Some("app_users"));
        assert_eq!(table(Some(Lang::Python), "m.py", py, "Team").as_deref(), Some("teams"));
        assert_eq!(table(Some(Lang::Python), "m.py", py, "Plain"), None);
        assert_eq!(table(Some(Lang::Python), "m.py", py, "Post").as_deref(), Some("post"));
        let java = "@Entity\n@Table(name = \"owners\")\npublic class Owner {}\n";
        assert_eq!(table(Some(Lang::Java), "O.java", java, "Owner").as_deref(), Some("owners"));
        let rb = "class User < ApplicationRecord\n  self.table_name = \"people\"\nend\nclass Tag < ApplicationRecord\nend\n";
        assert_eq!(table(Some(Lang::Ruby), "u.rb", rb, "User").as_deref(), Some("people"));
        assert_eq!(table(Some(Lang::Ruby), "u.rb", rb, "Tag").as_deref(), Some("tag"));
        let php = "<?php\nclass Article extends Model { protected $table = 'posts'; }\n";
        assert_eq!(table(Some(Lang::Php), "a.php", php, "Article").as_deref(), Some("posts"));
    }
}
