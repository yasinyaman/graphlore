//! Parsing one file: a per-thread parser per language, a deadline per parse,
//! and the facts the language module extracts.

use std::cell::RefCell;
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
}

/// Parse `src` as `lang`, giving up after `deadline`. `Err(true)` when it timed
/// out, `Err(false)` when the grammar could not be loaded.
pub fn parse(lang: Lang, src: &[u8], deadline: Option<Duration>) -> Result<Tree, bool> {
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
    match parse(lang, src, deadline) {
        Ok(tree) => {
            let root = tree.root_node();
            let module = lang.module();
            facts.parse.has_error = root.has_error();
            facts.defs = generic::walk_defs(module, root, src);
            facts.test_regions = module.test_regions(root, src);
            crate::refs::read(module, root, src, &mut facts);
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
