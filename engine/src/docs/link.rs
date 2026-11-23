//! `documents` edges: from the heading a document names code under (or the
//! document itself, above its first heading) to the definition it names.
//!
//! A mention is a name, not a binding: no compiler says what `Client.send` in
//! a README means. So an edge is written only when exactly one definition of
//! the project answers to the name, and never to test code. The name answers
//! when its last segments are the end of the definition's qualified name and
//! the segments before them, if any, are a run of the definition's module path
//! (`httpx.Client` for `Client` in `httpx/_client.py`, `django.db.models.Model`
//! for `Model` in `django/db/models/base.py`). A reST name under a
//! `.. currentmodule::` is tried with the module first.
//!
//! The edge says that a document describes code, not that anything depends on
//! anything: graphlore reads `documents` as no dependency (graph.py's
//! `_NON_DEPENDENCY_RELATIONS`), and community detection skips it.

use std::collections::{BTreeSet, HashMap, HashSet};

use serde_json::Value;

use crate::facts::{FileFacts, MentionForm};
use crate::graph::assemble::{link, Assembled};

pub const RELATION: &str = "documents";

struct Candidate<'a> {
    id: &'a str,
    /// A variable, field or constant: a word a code span holds (``update_fields``,
    /// an argument's name) as often as it names one, so a bare literal skips it.
    state: bool,
    qual: Vec<&'a str>,
    module: Vec<&'a str>,
}

/// The module path of a source file: its directories and stem, an
/// `__init__` / `index` / `mod` / `lib` stem dropped.
fn module_of(path: &str) -> Vec<&str> {
    let stem = path.rsplit_once('.').map_or(path, |(s, _)| s);
    let mut parts: Vec<&str> = stem.split('/').collect();
    if parts.len() > 1 && matches!(parts.last(), Some(&("__init__" | "index" | "mod" | "lib"))) {
        parts.pop();
    }
    parts
}

/// `needle` is a contiguous run of `hay` (an empty needle always is).
fn is_run(needle: &[&str], hay: &[&str]) -> bool {
    needle.is_empty() || hay.windows(needle.len()).any(|w| w == needle)
}

fn answers(c: &Candidate, segments: &[&str]) -> bool {
    (1..=segments.len().min(c.qual.len())).any(|k| {
        let (lead, tail) = segments.split_at(segments.len() - k);
        c.qual[c.qual.len() - k..] == *tail && is_run(lead, &c.module)
    })
}

/// The one definition `name` names, or `None` when none or several do, or
/// when `name` is the path of a module or package of the project
/// (``django.contrib.auth`` is the package, not a function `auth` in it).
fn pick<'a>(
    index: &HashMap<&str, Vec<Candidate<'a>>>,
    modules: &HashSet<String>,
    name: &str,
    bare_literal: bool,
) -> Option<&'a str> {
    if name.contains('.') && modules.contains(name) {
        return None;
    }
    let segments: Vec<&str> = name.split('.').collect();
    let last = segments.last()?;
    let mut found = index
        .get(last)?
        .iter()
        .filter(|c| !(bare_literal && segments.len() == 1 && c.state) && answers(c, &segments))
        .map(|c| c.id);
    let first = found.next()?;
    found.all(|id| id == first).then_some(first)
}

/// The `documents` edges of the project's documents.
pub fn edges(files: &[FileFacts], assembled: &Assembled) -> Vec<Value> {
    let mut index: HashMap<&str, Vec<Candidate>> = HashMap::new();
    // every module path and package of the project, dotted
    let mut modules: HashSet<String> = HashSet::new();
    for (fi, f) in files.iter().enumerate() {
        if f.is_document || f.lang.is_none() || crate::community::is_test_path(&f.path) {
            continue;
        }
        let module = module_of(&f.path);
        for k in 1..=module.len() {
            modules.insert(module[..k].join("."));
        }
        for (di, d) in f.defs.iter().enumerate() {
            let Some(Some(id)) = assembled.def_ids.get(fi).and_then(|ids| ids.get(di)) else {
                continue;
            };
            if d.is_test || d.name.is_empty() {
                continue;
            }
            index.entry(d.name.as_str()).or_default().push(Candidate {
                id,
                state: d.kind.is_state(),
                qual: d.qualname.split('.').collect(),
                module: module.clone(),
            });
        }
    }
    let mut out = Vec::new();
    let mut seen: BTreeSet<(String, String)> = BTreeSet::new();
    for (fi, f) in files.iter().enumerate() {
        if !f.is_document || f.doc_mentions.is_empty() {
            continue;
        }
        let heading_ids = assembled.headings.get(fi).map(Vec::as_slice).unwrap_or(&[]);
        for m in &f.doc_mentions {
            let under = m.module.as_deref().map(|module| format!("{module}.{}", m.name));
            let bare_literal = m.form == MentionForm::Literal;
            let target = under.as_deref().and_then(|n| pick(&index, &modules, n, false));
            let (target, qualified) = match target {
                Some(t) => (t, true),
                None => match pick(&index, &modules, &m.name, bare_literal) {
                    Some(t) => (t, m.name.contains('.')),
                    None => continue,
                },
            };
            // the innermost section holding the line: the last heading that
            // starts at or before it and whose section reaches it
            let source = f
                .headings
                .iter()
                .zip(heading_ids)
                .filter(|(h, _)| h.line <= m.line && m.line <= h.end_line)
                .max_by_key(|(h, _)| (h.level, h.line))
                .map(|(_, id)| id.as_str())
                .unwrap_or(assembled.hubs[fi].as_str());
            if !seen.insert((source.to_string(), target.to_string())) {
                continue;
            }
            let mut e = link(source, target, RELATION, &f.path, m.line);
            // a directive describing a qualified object is the document's own
            // statement of what it describes; anything else is read off a name
            if !(m.form == MentionForm::Directive && qualified) {
                e["confidence"] = "INFERRED".into();
                e["confidence_score"] = 0.8.into();
            }
            e["context"] = m.form.context().into();
            out.push(e);
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn module_paths_drop_package_files() {
        assert_eq!(module_of("httpx/_client.py"), vec!["httpx", "_client"]);
        assert_eq!(module_of("django/db/models/__init__.py"), vec!["django", "db", "models"]);
        assert_eq!(module_of("src/lib.rs"), vec!["src"]);
    }

    #[test]
    fn a_name_answers_by_its_tail_and_a_run_of_the_module() {
        let c = Candidate {
            state: false,
            id: "x",
            qual: vec!["Model"],
            module: vec!["django", "db", "models", "base"],
        };
        assert!(answers(&c, &["Model"]));
        assert!(answers(&c, &["django", "db", "models", "Model"]));
        assert!(answers(&c, &["models", "Model"]));
        assert!(!answers(&c, &["forms", "Model"]));
        let m = Candidate {
            id: "y",
            state: false,
            qual: vec!["Client", "send"],
            module: vec!["httpx", "_client"],
        };
        assert!(answers(&m, &["send"]));
        assert!(answers(&m, &["Client", "send"]));
        assert!(answers(&m, &["httpx", "Client", "send"]));
        assert!(!answers(&m, &["Response", "send"]));
    }
}
