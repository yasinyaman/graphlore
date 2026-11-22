//! Parsing one file: a per-thread parser per language, a deadline per parse,
//! and the facts the language module extracts.

use std::cell::{Cell, RefCell};
use std::collections::hash_map::Entry;
use std::collections::HashMap;
use std::ops::ControlFlow;
use std::time::{Duration, Instant};

use tree_sitter::{ParseOptions, ParseState, Parser, Tree};

use crate::docs::{self, DocKind};
use crate::facts::FileFacts;
use crate::generic;
use crate::lang::{self, Lang};

thread_local! {
    static PARSERS: RefCell<HashMap<Lang, Parser>> = RefCell::new(HashMap::new());
    /// The language of the tree this thread parsed last: the one it reads.
    static CURRENT: Cell<Option<Lang>> = const { Cell::new(None) };
}

thread_local! {
    /// Names of library wrappers (`Optional`, `Future`, `Box`) that the file
    /// being read means as the project's own type of that name.
    static SHADOWED: RefCell<Option<std::sync::Arc<std::collections::HashSet<String>>>> =
        const { RefCell::new(None) };
}

/// Whether `name`, a library wrapper's, is the project's own type in the file
/// this thread is reading (`lib::reread_shadowed` says so).
pub fn shadowed(name: &str) -> bool {
    SHADOWED.with(|s| s.borrow().as_ref().is_some_and(|set| set.contains(name)))
}

/// Run `read` with `names` taken as the project's own types.
pub fn with_shadowed<T>(
    names: std::sync::Arc<std::collections::HashSet<String>>,
    read: impl FnOnce() -> T,
) -> T {
    SHADOWED.with(|s| *s.borrow_mut() = Some(names));
    let out = read();
    SHADOWED.with(|s| *s.borrow_mut() = None);
    out
}

/// The language of the tree this thread is reading (the last one it parsed).
/// A node does not say it: several grammars report no name.
pub fn current() -> Option<Lang> {
    CURRENT.with(Cell::get)
}

/// Parse `src` as `lang`, giving up after `deadline`. `Err(true)` when it timed
/// out, `Err(false)` when the grammar could not be loaded.
pub fn parse(lang: Lang, src: &[u8], deadline: Option<Duration>) -> Result<Tree, bool> {
    CURRENT.with(|c| c.set(Some(lang)));
    PARSERS.with(|cell| {
        let mut parsers = cell.borrow_mut();
        let parser = match parsers.entry(lang) {
            Entry::Occupied(e) => e.into_mut(),
            Entry::Vacant(v) => {
                let mut p = Parser::new();
                p.set_language(&(lang.spec().grammar)()).map_err(|_| false)?;
                v.insert(p)
            }
        };
        let start = Instant::now();
        let mut timed_out = false;
        let mut progress = |_: &ParseState| {
            if deadline.is_some_and(|d| start.elapsed() > d) {
                timed_out = true;
                ControlFlow::Break(())
            } else {
                ControlFlow::Continue(())
            }
        };
        let mut read = |i: usize, _| if i < src.len() { &src[i..] } else { &[][..] };
        let tree = parser.parse_with_options(
            &mut read,
            None,
            Some(ParseOptions::new().progress_callback(&mut progress)),
        );
        // a cancelled parse leaves the parser mid-document
        parser.reset();
        match tree {
            Some(t) => Ok(t),
            None => Err(timed_out),
        }
    })
}

/// The facts of one source file. `lang` is the detected language (or `None` for
/// a document, which `doc` then names).
pub fn file_facts(
    rel: &str,
    src: &[u8],
    lang: Option<Lang>,
    doc: Option<DocKind>,
    deadline: Option<Duration>,
) -> FileFacts {
    if let Some(kind) = doc {
        let mut facts = FileFacts::new(rel.to_string(), None, true, src);
        facts.headings = docs::headings(kind, src);
        return facts;
    }
    let mut facts = FileFacts::new(rel.to_string(), lang, false, src);
    let Some(lang) = lang else { return facts };
    // C, C++: the branches of an `#if` that each leave a brace open are one
    // piece of code written twice; the parser is given the first
    // (C#: an `#if` in the middle of an expression is parsed once)
    // (C, C++: `class EXPORT_MACRO Name {` is read without the macro)
    let unmarked = match lang {
        Lang::C | Lang::Cpp => blank_class_macros(src),
        _ => None,
    };
    let src = unmarked.as_deref().unwrap_or(src);
    // (and `int n GUARDED_BY(mu);` without the macro)
    let unannotated = match lang {
        Lang::C | Lang::Cpp => blank_annotation_macros(src),
        _ => None,
    };
    let src = unannotated.as_deref().unwrap_or(src);
    let blanked = match lang {
        Lang::C | Lang::Cpp => blank_duplicate_branches(src),
        Lang::CSharp => blank_expression_branches(src),
        _ => None,
    };
    let src = blanked.as_deref().unwrap_or(src);
    match parse(lang, src, deadline) {
        Ok(tree) => {
            let root = tree.root_node();
            let module = lang.module();
            facts.parse.has_error = root.has_error();
            facts.defs = generic::walk_defs(module, root, src);
            facts.test_regions = module.test_regions(root, src);
            crate::refs::read(module, root, src, &mut facts);
            crate::http::read(lang, root, src, &mut facts);
            for d in facts.defs.iter_mut() {
                d.is_test = facts
                    .test_regions
                    .iter()
                    .any(|(lo, hi)| *lo <= d.def_line && d.def_line <= *hi);
            }
        }
        Err(timed_out) => facts.parse.timed_out = timed_out,
    }
    facts
}

/// The facts of a file given only its path and contents (detects the language).
pub fn facts_for_source(rel: &str, src: &[u8], deadline: Option<Duration>) -> FileFacts {
    if let Some(kind) = docs::detect(rel) {
        return file_facts(rel, src, None, Some(kind), deadline);
    }
    file_facts(rel, src, lang::detect(rel, Some(src)), None, deadline)
}

/// C#: an `#if` … `#else` … `#endif` that opens in the middle of an expression
/// or a declaration (the code line before it ends none: `? null` before
/// `#if NET` / `: new A(x)` / `#else` / `: new A()`) is one piece of code
/// written once per configuration; the parser, seeing every branch, loses the
/// type around. The source with the directive lines and every branch but the
/// first blanked, or `None` when no group is like that; of a group with one
/// branch only the two directive lines go, its lines being part of the
/// expression. A group whose first branch leaves a brace open or closes one
/// it did not open (`#if NET` / `if (!OperatingSystem.IsBrowser()) {` /
/// `#endif`, closed by a `}` in a later group) is treated the same: the
/// parser, pairing the brace inside the group, loses the members after it.
/// `spans.py`'s `_blank_expression_branches` is the same rule.
pub fn blank_expression_branches(src: &[u8]) -> Option<Vec<u8>> {
    if !src.windows(3).any(|w| w == b"#if") {
        return None;
    }
    let lines: Vec<&[u8]> = src.split(|b| *b == b'\n').collect();
    let trim = |l: &[u8]| -> (usize, usize) {
        let lo = l.iter().position(|c| !c.is_ascii_whitespace()).unwrap_or(l.len());
        let hi = l.iter().rposition(|c| !c.is_ascii_whitespace()).map_or(lo, |i| i + 1);
        (lo, hi)
    };
    let mut blank: Vec<(usize, usize)> = Vec::new(); // line ranges, inclusive
                                                     // (opened inside an expression, its directive lines, the braces its
                                                     // first branch leaves open)
    let mut stack: Vec<(bool, Vec<usize>, i64)> = Vec::new();
    let mut last: Option<u8> = None; // how the last line of code ends
    for (li, line) in lines.iter().enumerate() {
        let (lo, hi) = trim(line);
        let t = &line[lo..hi];
        if t.first() == Some(&b'#') {
            let rest = &t[1..];
            let i = rest.iter().position(|c| !c.is_ascii_whitespace()).unwrap_or(rest.len());
            let word: Vec<u8> =
                rest[i..].iter().take_while(|c| c.is_ascii_alphabetic()).copied().collect();
            match word.as_slice() {
                b"if" => {
                    let inside = last.is_some_and(|c| !b";{}],)".contains(&c));
                    stack.push((inside, vec![li], 0));
                }
                b"elif" | b"else" => {
                    if let Some(g) = stack.last_mut() {
                        g.1.push(li);
                    }
                }
                b"endif" => {
                    if let Some((inside, directives, open)) = stack.pop() {
                        if inside || open != 0 {
                            blank.push((directives[0], directives[0]));
                            blank.push((directives.get(1).copied().unwrap_or(li), li));
                        }
                    }
                }
                _ => {}
            }
            continue;
        }
        let cut = t.windows(2).position(|w| w == b"//").unwrap_or(t.len());
        let (_, end) = trim(&t[..cut]);
        if end > 0 {
            last = Some(t[end - 1]);
            let code = &t[..end];
            let open = code.iter().filter(|c| **c == b'{').count() as i64
                - code.iter().filter(|c| **c == b'}').count() as i64;
            for g in stack.iter_mut().filter(|g| g.1.len() == 1) {
                g.2 += open;
            }
        }
    }
    if blank.is_empty() {
        return None;
    }
    let mut out = src.to_vec();
    let mut starts = Vec::with_capacity(lines.len());
    let mut at = 0;
    for l in &lines {
        starts.push(at);
        at += l.len() + 1;
    }
    for (lo, hi) in blank {
        for li in lo..=hi {
            for b in &mut out[starts[li]..starts[li] + lines[li].len()] {
                if *b != b'\r' {
                    *b = b' ';
                }
            }
        }
    }
    Some(out)
}

/// C, C++: `class LEVELDB_EXPORT Env {`, `struct API_EXPORT Options : Base {`.
/// A macro between the keyword and the name is nothing to the language once
/// expanded (an attribute, or nothing), but the parser, which sees no
/// expansion, takes it for the type's name and the rest for a function
/// (`Env { … }`), so the members belong to no class. The source with such a
/// macro blanked (each byte a space: bytes and lines stay where they were), or
/// `None` when there is none. A macro is a name in capitals of three
/// characters at least, with another name after it and then `{`, a base list
/// or `final`. `spans.py`'s `_blank_class_macros` is the same rule.
pub fn blank_class_macros(src: &[u8]) -> Option<Vec<u8>> {
    static PATTERN: std::sync::OnceLock<regex::bytes::Regex> = std::sync::OnceLock::new();
    let pattern = PATTERN.get_or_init(|| {
        regex::bytes::Regex::new(
            r"\b(?:class|struct|union)[ \t]+([A-Z][A-Z0-9_]{2,})[ \t]+[A-Za-z_][A-Za-z0-9_]*[ \t\r\n]*(?:\{|:[^:]|final\b)",
        )
        .expect("a valid pattern")
    });
    let mut out: Option<Vec<u8>> = None;
    for found in pattern.captures_iter(src) {
        let Some(m) = found.get(1) else { continue };
        let buf = out.get_or_insert_with(|| src.to_vec());
        for b in &mut buf[m.start()..m.end()] {
            *b = b' ';
        }
    }
    out
}

/// Words a declarator name is never: what stands before a call statement
/// (`return FOO(x);`) and the words of a type (`static int ADD(int a);`).
const NOT_DECLARATOR: &[&str] = &[
    "return",
    "case",
    "throw",
    "else",
    "do",
    "goto",
    "co_return",
    "co_yield",
    "co_await",
    "sizeof",
    "alignof",
    "new",
    "delete",
    "typedef",
    "using",
    "namespace",
    "struct",
    "class",
    "union",
    "enum",
    "if",
    "while",
    "for",
    "switch",
    "catch",
    "int",
    "char",
    "void",
    "long",
    "short",
    "unsigned",
    "signed",
    "float",
    "double",
    "bool",
    "auto",
    "static",
    "inline",
    "extern",
    "volatile",
    "constexpr",
    "consteval",
    "constinit",
    "virtual",
    "explicit",
    "register",
    "mutable",
    "thread_local",
    "friend",
    "operator",
    "template",
    "typename",
    "public",
    "private",
    "protected",
    "const",
    "override",
    "final",
    "noexcept",
];
/// Of those, the words that end a function's declarator (`void g() const
/// LOCKS_EXCLUDED(mu)`).
const AFTER_PARAMS: &[&str] = &["const", "override", "final", "noexcept"];
/// How far back the `(` a parameter list opens with is looked for.
const LOOKBACK: usize = 2000;
const CONTROL: &[&str] = &[
    "if", "while", "for", "switch", "catch", "return", "sizeof", "alignof", "decltype", "typeof",
    "defined",
];

/// C, C++: `int count GUARDED_BY(mu);` and `void f() REQUIRES(mu);` with the
/// macro blanked. An all-capital macro written after a declaration's name or
/// parameters (clang's thread-safety annotations, `ABSL_GUARDED_BY`) expands
/// to an attribute, but the parser takes it for the declarator: the field is
/// lost and a function named after the macro is made up. A macro counts when
/// what stands before it is a declarator name (an identifier after a type, not
/// a keyword) or a parameter list (`)` not closing an `if`, a `while`, …),
/// optionally followed by `const` / `override` / `noexcept`, on its line or at
/// the start of the next, and when its
/// arguments declare no parameter (`ADD(int a)` is a function). Each byte of
/// the macro becomes a space, or `None` when there is none. So does a macro
/// alone on an indented line before a line that starts with a call (Unity's
/// `EXPECT_ABORT_BEGIN` / `TEST_ASSERT(0);`, Qt's `Q_OBJECT` / `Window(int
/// w);`): a statement or nothing once expanded, which the parser takes for
/// the type of a declaration the next line would be. `spans.py`'s
/// `_blank_annotation_macros` is the same rule.
pub fn blank_annotation_macros(src: &[u8]) -> Option<Vec<u8>> {
    static PATTERN: std::sync::OnceLock<regex::bytes::Regex> = std::sync::OnceLock::new();
    static PARAM: std::sync::OnceLock<regex::bytes::Regex> = std::sync::OnceLock::new();
    if !src.contains(&b'(') {
        return None;
    }
    let pattern = PATTERN.get_or_init(|| {
        regex::bytes::Regex::new(
            r"(?m)^([^#\n]*?)([A-Za-z_][A-Za-z0-9_]*|\)|\])(?:[ \t]+|[ \t]*\r?\n[ \t]*)([A-Z][A-Z0-9_]{2,}[ \t]*\(([^()\n]*)\))[ \t]*(?:;|\{|=|,)",
        )
        .expect("a valid pattern")
    });
    let param = PARAM.get_or_init(|| {
        regex::bytes::Regex::new(r"[A-Za-z_][A-Za-z0-9_]*[ \t*&]+[A-Za-z_]")
            .expect("a valid pattern")
    });
    let trim_end = |b: &[u8]| -> usize {
        let mut k = b.len();
        while k > 0 && (b[k - 1] == b' ' || b[k - 1] == b'\t') {
            k -= 1;
        }
        k
    };
    static STATEMENT: std::sync::OnceLock<regex::bytes::Regex> = std::sync::OnceLock::new();
    let statement = STATEMENT.get_or_init(|| {
        regex::bytes::Regex::new(
            r"(?m)^[ \t]+([A-Z][A-Z0-9_]{2,})[ \t]*\r?\n[ \t]*[A-Za-z_][A-Za-z0-9_]*[ \t]*\(",
        )
        .expect("a valid pattern")
    });
    let mut out: Option<Vec<u8>> = None;
    for found in statement.captures_iter(src) {
        let Some(m) = found.get(1) else { continue };
        let buf = out.get_or_insert_with(|| src.to_vec());
        for b in &mut buf[m.start()..m.end()] {
            *b = b' ';
        }
    }
    for found in pattern.captures_iter(src) {
        let (Some(g1), Some(g2), Some(g3), Some(g4)) =
            (found.get(1), found.get(2), found.get(3), found.get(4))
        else {
            continue;
        };
        if param.is_match(g4.as_bytes()) {
            continue;
        }
        let before = &g1.as_bytes()[..trim_end(g1.as_bytes())];
        let mut word = g2.as_bytes();
        // where the `)` of a parameter list is
        let mut close = g2.start();
        let is = |w: &[u8], set: &[&str]| set.iter().any(|s| s.as_bytes() == w);
        if is(word, AFTER_PARAMS) {
            if before.last() != Some(&b')') {
                continue;
            }
            word = b")";
            close = g1.start() + before.len() - 1;
        } else if word != b")" && word != b"]" {
            // (`]`: an array's declarator, `int stats_[4] GUARDED_BY(mu);`)
            let typeish = before.last().is_some_and(|c| {
                c.is_ascii_alphanumeric() || matches!(c, b'_' | b'*' | b'&' | b'>')
            });
            if is(word, NOT_DECLARATOR) || !typeish {
                continue;
            }
        }
        if word == b")" {
            // the word before the `(` this closes, on this line or one above
            // (`Status Recover(VersionEdit* edit,` / `bool* save)` / `REQUIRES(mu);`)
            let before = &src[close.saturating_sub(LOOKBACK)..close];
            let mut depth = 1;
            let mut i = before.len() as isize - 1;
            while i >= 0 && depth > 0 {
                match before[i as usize] {
                    b')' => depth += 1,
                    b'(' => depth -= 1,
                    _ => {}
                }
                i -= 1;
            }
            if depth > 0 {
                continue;
            }
            let head = &before[..(i + 1) as usize];
            let head = &head[..trim_end(head)];
            let mut k = head.len();
            while k > 0
                && (head[k - 1].is_ascii_alphanumeric()
                    || matches!(head[k - 1], b'_' | b':' | b'~'))
            {
                k -= 1;
            }
            let callee = head[k..].rsplit(|c| *c == b':').next().unwrap_or_default();
            if callee.is_empty() || is(callee, CONTROL) {
                continue;
            }
        }
        let buf = out.get_or_insert_with(|| src.to_vec());
        for b in &mut buf[g3.start()..g3.end()] {
            *b = b' ';
        }
    }
    out
}

/// C, C++: an `#if` … `#elif`/`#else` … `#endif` whose every branch leaves the
/// same number of braces open (not none) is one piece of code written once
/// per configuration (`if (error_code) {` under `#if` and again under
/// `#else`, closed once after `#endif`). The parser, seeing every branch,
/// opens one brace per branch and swallows the rest of the file into the
/// function around. The source with the directive lines and every branch but
/// the first blanked (each byte but a newline a space, so bytes and lines stay
/// where they were), or `None` when no group is like that. `spans.py`'s
/// `_blank_duplicate_branches` is the same rule.
pub fn blank_duplicate_branches(src: &[u8]) -> Option<Vec<u8>> {
    // the lines, as byte ranges without their newline
    let mut lines: Vec<(usize, usize)> = Vec::new();
    let mut start = 0;
    for (i, b) in src.iter().enumerate() {
        if *b == b'\n' {
            lines.push((start, i));
            start = i + 1;
        }
    }
    if start < src.len() {
        lines.push((start, src.len()));
    }
    // a group: its directive lines and, per branch, its first line and how
    // many braces it leaves open
    struct Group {
        directives: Vec<usize>,
        branches: Vec<(usize, i64)>,
    }
    let mut stack: Vec<Group> = Vec::new();
    let mut blank: Vec<(usize, usize)> = Vec::new(); // line ranges, inclusive
    let mut in_comment = false;
    let mut continued = false; // a directive line ending in `\`
    for (li, &(lo, hi)) in lines.iter().enumerate() {
        let line = &src[lo..hi];
        let was_continued = continued;
        continued = line.last() == Some(&b'\\') || (line.ends_with(b"\\\r"));
        if was_continued {
            continue;
        }
        let trimmed: &[u8] = {
            let i = line.iter().position(|c| !c.is_ascii_whitespace()).unwrap_or(line.len());
            &line[i..]
        };
        if !in_comment && trimmed.first() == Some(&b'#') {
            let rest = &trimmed[1..];
            let i = rest.iter().position(|c| !c.is_ascii_whitespace()).unwrap_or(rest.len());
            let word: Vec<u8> =
                rest[i..].iter().take_while(|c| c.is_ascii_alphabetic()).copied().collect();
            match word.as_slice() {
                b"if" | b"ifdef" | b"ifndef" => {
                    stack.push(Group { directives: vec![li], branches: vec![(li + 1, 0)] })
                }
                b"elif" | b"else" | b"elifdef" | b"elifndef" => {
                    if let Some(g) = stack.last_mut() {
                        g.directives.push(li);
                        g.branches.push((li + 1, 0));
                    }
                }
                b"endif" => {
                    if let Some(g) = stack.pop() {
                        let first = g.branches[0].1;
                        let alike = g.branches.len() >= 2
                            && first != 0
                            && g.branches.iter().all(|(_, d)| *d == first);
                        if alike {
                            for &d in &g.directives {
                                blank.push((d, d));
                            }
                            blank.push((li, li));
                            for w in g.directives.windows(2).skip(1) {
                                blank.push((w[0] + 1, w[1] - 1));
                            }
                            if let Some(&last) = g.directives.last() {
                                if g.directives.len() >= 2 {
                                    blank.push((last + 1, li - 1));
                                }
                            }
                        }
                        if let Some(parent) = stack.last_mut() {
                            if let Some(b) = parent.branches.last_mut() {
                                b.1 += first;
                            }
                        }
                    }
                }
                _ => {}
            }
            continue;
        }
        // braces outside comments, strings and character literals
        let mut delta = 0i64;
        let mut i = 0;
        while i < line.len() {
            let c = line[i];
            if in_comment {
                if c == b'*' && line.get(i + 1) == Some(&b'/') {
                    in_comment = false;
                    i += 1;
                }
            } else if c == b'/' && line.get(i + 1) == Some(&b'/') {
                break;
            } else if c == b'/' && line.get(i + 1) == Some(&b'*') {
                in_comment = true;
                i += 1;
            } else if c == b'"' || c == b'\'' {
                i += 1;
                while i < line.len() && line[i] != c {
                    if line[i] == b'\\' {
                        i += 1;
                    }
                    i += 1;
                }
            } else if c == b'{' {
                delta += 1;
            } else if c == b'}' {
                delta -= 1;
            }
            i += 1;
        }
        if let Some(b) = stack.last_mut().and_then(|g| g.branches.last_mut()) {
            b.1 += delta;
        }
    }
    if blank.is_empty() {
        return None;
    }
    let mut out = src.to_vec();
    for (a, b) in blank {
        for &(lo, hi) in lines.get(a..=b.min(lines.len().saturating_sub(1))).unwrap_or(&[]) {
            for byte in &mut out[lo..hi] {
                if *byte != b'\r' {
                    *byte = b' ';
                }
            }
        }
    }
    Some(out)
}
