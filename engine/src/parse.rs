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
    let blanked =
        matches!(lang, Lang::C | Lang::Cpp).then(|| blank_duplicate_branches(src)).flatten();
    let src = blanked.as_deref().unwrap_or(src);
    let parsed = parse(lang, src, deadline);
    // C#: a source that does not parse as written, again with each `#if`'s
    // other branches blanked; kept when that parses clean
    let alternative = match &parsed {
        Ok(tree) if lang == Lang::CSharp && tree.root_node().has_error() => {
            blank_alternative_branches(src).and_then(|b| match parse(lang, &b, deadline) {
                Ok(t) if !t.root_node().has_error() => Some((b, t)),
                _ => None,
            })
        }
        _ => None,
    };
    let (src, parsed): (&[u8], _) = match &alternative {
        Some((b, t)) => (b.as_slice(), Ok(t.clone())),
        None => (src, parsed),
    };
    match parsed {
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
    blank_branches(src, false)
}

/// C#: an `#if` … `#else` … `#endif` whose branches leave as many braces open
/// as each other, none included (`? null\n#if NET\n: new X(a, b)\n#else\n: new
/// X(a)\n#endif`: one expression written per target framework), blanked to its
/// first branch. Asked only when the source as written does not parse.
/// `spans.py`'s `_blank_duplicate_branches(src, any_balance=True)` is the same.
pub fn blank_alternative_branches(src: &[u8]) -> Option<Vec<u8>> {
    blank_branches(src, true)
}

fn blank_branches(src: &[u8], any_balance: bool) -> Option<Vec<u8>> {
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
                            && (first != 0 || any_balance)
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
                        // (C#: a lone branch that opens or closes a brace,
                        // `#if NET` / `if (…) {` / `#endif` … `#if NET` / `}` /
                        // `#endif`: the code it wraps, unwrapped)
                        let mut first = first;
                        if !alike && any_balance && g.branches.len() == 1 && first != 0 {
                            blank.push((g.directives[0], li));
                            first = 0;
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
