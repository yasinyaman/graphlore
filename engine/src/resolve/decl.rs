//! The print of what other files read of a file's declarations
//! (`trace::part::DECL`): everything of its facts but a function body's
//! content, with each position as its rank among the file's declaration
//! positions.
//!
//! Resolution compares a file's positions with one another only (a block
//! `use` around a definition, the members inside an anonymous struct, the
//! RSpec block around a `let`), never with another file's, so what it gives
//! depends on their order, not on their values: a line added to a function
//! body moves every position after it and keeps their order. A body's own
//! content (its references and locals) is not in the print, and a read of it
//! is a read of the whole file (`part::FULL`).

use std::io::Write;

use crate::facts::{Def, FileFacts, ImportFact, Local, RefFact, RefKind};
use bincode::Options;

/// Whether a definition's locals are declarations (a class's fields, a
/// namespace's names), not a function body's.
pub fn holds_declarations(d: &Def) -> bool {
    !d.kind.is_callable() && !d.block
}

/// A reference written in a declaration (a base, a decorator, a declared
/// result), which other files read: in the print.
pub fn is_decl_ref(r: &RefFact) -> bool {
    matches!(
        r.kind,
        RefKind::Inherits
            | RefKind::Implements
            | RefKind::Base
            | RefKind::MixesIn
            | RefKind::Embeds
            | RefKind::Decorator
    ) || (r.kind == RefKind::Type && r.context == "return_type")
}

/// The locals of a file's declaration scopes, in order.
fn decl_locals(facts: &FileFacts) -> impl Iterator<Item = &Local> {
    facts
        .locals
        .iter()
        .filter(|l| l.from_def.and_then(|x| facts.defs.get(x)).is_some_and(holds_declarations))
}

/// The ranks of a file's declaration positions: lines and bytes apart (a
/// line is never compared with a byte).
pub struct Ranks {
    lines: Vec<u64>,
    bytes: Vec<u64>,
}

impl Ranks {
    pub fn line(&self, v: u32) -> u32 {
        rank(&self.lines, u64::from(v)) as u32
    }

    pub fn byte(&self, v: usize) -> usize {
        rank(&self.bytes, v as u64)
    }
}

/// Where `v` stands among `sorted`; a value not in it (none is asked for)
/// stands after the ones below it, apart from them.
fn rank(sorted: &[u64], v: u64) -> usize {
    match sorted.binary_search(&v) {
        Ok(i) => 2 * i,
        Err(i) => 2 * i + 1,
    }
}

/// The declaration positions of a file.
pub fn ranks(facts: &FileFacts) -> Ranks {
    let mut lines: Vec<u64> = Vec::new();
    let mut bytes: Vec<u64> = Vec::new();
    for d in &facts.defs {
        lines.extend([d.region_start, d.def_line, d.end_line].map(u64::from));
        if let Some((a, b)) = d.scope_lines {
            lines.extend([u64::from(a), u64::from(b)]);
        }
        bytes.extend([d.start_byte as u64, d.end_byte as u64]);
        if let Some((a, b)) = d.inline_body {
            bytes.extend([a as u64, b as u64]);
        }
    }
    for x in facts.refs.iter().filter(|x| is_decl_ref(x)) {
        lines.push(u64::from(x.line));
        bytes.push(x.byte as u64);
    }
    for l in decl_locals(facts) {
        bytes.extend([l.byte as u64, l.until as u64, l.after as u64]);
    }
    for i in &facts.imports {
        lines.push(u64::from(i.line));
        if let Some((a, b)) = i.scope {
            bytes.extend([a as u64, b as u64]);
        }
    }
    for &(a, b) in &facts.test_regions {
        lines.extend([u64::from(a), u64::from(b)]);
    }
    for (_, a, b) in &facts.shared_examples {
        lines.extend([u64::from(*a), u64::from(*b)]);
    }
    for (_, a) in &facts.example_includes {
        lines.push(u64::from(*a));
    }
    bytes.extend(facts.inline_namespaces.iter().map(|&b| b as u64));
    bytes.extend(facts.forwards.iter().map(|(b, _)| *b as u64));
    for v in [&mut lines, &mut bytes] {
        v.sort_unstable();
        v.dedup();
    }
    Ranks { lines, bytes }
}

struct Fnv(u64);

impl Write for Fnv {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        for &b in buf {
            self.0 ^= u64::from(b);
            self.0 = self.0.wrapping_mul(0x0000_0100_0000_01b3);
        }
        Ok(buf.len())
    }

    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

/// A definition with its positions as ranks.
fn ranked_def(d: &Def, r: &Ranks) -> Def {
    let mut d = d.clone();
    d.region_start = r.line(d.region_start);
    d.def_line = r.line(d.def_line);
    d.end_line = r.line(d.end_line);
    d.scope_lines = d.scope_lines.map(|(a, b)| (r.line(a), r.line(b)));
    d.start_byte = r.byte(d.start_byte);
    d.end_byte = r.byte(d.end_byte);
    d.inline_body = d.inline_body.map(|(a, b)| (r.byte(a), r.byte(b)));
    d
}

fn ranked_local(l: &Local, r: &Ranks) -> Local {
    let mut l = l.clone();
    l.byte = r.byte(l.byte);
    l.until = r.byte(l.until);
    l.after = r.byte(l.after);
    l
}

fn ranked_ref(x: &RefFact, r: &Ranks) -> RefFact {
    let mut x = x.clone();
    x.line = r.line(x.line);
    x.byte = r.byte(x.byte);
    x
}

fn ranked_import(i: &ImportFact, r: &Ranks) -> ImportFact {
    let mut i = i.clone();
    i.line = r.line(i.line);
    i.scope = i.scope.map(|(a, b)| (r.byte(a), r.byte(b)));
    i
}

/// A position written into a name (`user@483`, a closure's parameter):
/// a declaration that holds one is printed with its file's content, so the
/// byte it names counts as it is.
fn names_a_position(bytes: &[u8]) -> bool {
    bytes.windows(2).any(|w| w[0] == b'@' && w[1].is_ascii_digit())
}

/// The print of a file's declarations, with the ranks it was made with
/// (for the parts of the print that come from resolution: `Scope::scoped`).
/// `content` is the print of the whole file, used when a declaration names
/// a position (`names_a_position`).
pub fn print(facts: &FileFacts, content: u64) -> (u64, Ranks) {
    let r = ranks(facts);
    let mut h = Fnv(0xcbf2_9ce4_8422_2325);
    let mut buf: Vec<u8> = Vec::new();
    for d in &facts.defs {
        buf.clear();
        if codec().serialize_into(&mut buf, &ranked_def(d, &r)).is_err() || names_a_position(&buf) {
            let _ = h.write(b"content");
            let _ = h.write(&content.to_le_bytes());
            return (h.0, r);
        }
        let _ = h.write(&buf);
    }
    // the references of declarations: a lambda receiver or an implicit
    // member names a body's content by its index (printed with the content)
    for x in facts.refs.iter().filter(|x| is_decl_ref(x)) {
        buf.clear();
        if x.recv.is_some()
            || x.implicit.is_some()
            || codec().serialize_into(&mut buf, &ranked_ref(x, &r)).is_err()
            || names_a_position(&buf)
        {
            let _ = h.write(b"content");
            let _ = h.write(&content.to_le_bytes());
            return (h.0, r);
        }
        let _ = h.write(&buf);
    }
    // the locals of declaration scopes, with their index among all the
    // file's locals (what `fields` and `locals` name them by)
    for (li, l) in facts.locals.iter().enumerate() {
        if !l.from_def.and_then(|x| facts.defs.get(x)).is_some_and(holds_declarations) {
            continue;
        }
        buf.clear();
        let ranked = (li, ranked_local(l, &r));
        if codec().serialize_into(&mut buf, &ranked).is_err() || names_a_position(&buf) {
            let _ = h.write(b"content");
            let _ = h.write(&content.to_le_bytes());
            return (h.0, r);
        }
        let _ = h.write(&buf);
    }
    let imports: Vec<ImportFact> = facts.imports.iter().map(|i| ranked_import(i, &r)).collect();
    let rest = (
        &facts.path,
        &facts.lang,
        facts.is_document,
        &facts.package,
        &imports,
        &facts.exports,
        &facts.ns_aliases,
        facts.inline_namespaces.iter().map(|&b| r.byte(b)).collect::<Vec<_>>(),
        facts.forwards.iter().map(|(b, p)| (r.byte(*b), p)).collect::<Vec<_>>(),
        facts.test_regions.iter().map(|&(a, b)| (r.line(a), r.line(b))).collect::<Vec<_>>(),
    );
    let more = (
        &facts.attrs,
        &facts.spec_includes,
        facts
            .shared_examples
            .iter()
            .map(|(n, a, b)| (n, r.line(*a), r.line(*b)))
            .collect::<Vec<_>>(),
        facts.example_includes.iter().map(|(n, a)| (n, r.line(*a))).collect::<Vec<_>>(),
        &facts.models,
        &facts.runtime_props,
        &facts.request_props,
        &facts.request_aliases,
    );
    if codec().serialize_into(&mut h, &rest).is_err()
        || codec().serialize_into(&mut h, &more).is_err()
    {
        let _ = h.write(&content.to_le_bytes());
    }
    (h.0, r)
}

fn codec() -> impl Options {
    bincode::DefaultOptions::new().with_fixint_encoding()
}
