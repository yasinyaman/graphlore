//! Documents read without an LLM: the heading outline of Markdown and
//! reStructuredText files, and the names of code they write (`mentions`), which
//! `link` joins to the definitions they name.

use crate::facts::{DocMention, Heading, MentionForm};

pub mod link;
mod markdown;
mod rst;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DocKind {
    Markdown,
    Rst,
}

pub fn detect(rel: &str) -> Option<DocKind> {
    let lower = rel.to_ascii_lowercase();
    if lower.ends_with(".md") || lower.ends_with(".markdown") || lower.ends_with(".mdx") {
        Some(DocKind::Markdown)
    } else if lower.ends_with(".rst") {
        Some(DocKind::Rst)
    } else {
        None
    }
}

/// The headings of a document, each with the line its section ends on.
pub fn headings(kind: DocKind, src: &[u8]) -> Vec<Heading> {
    let mut found = match kind {
        DocKind::Markdown => markdown::headings(src),
        DocKind::Rst => rst::headings(src),
    };
    close_sections(&mut found, crate::facts::count_lines(src));
    found
}

/// The names of code a document writes, in the order written.
pub fn mentions(kind: DocKind, src: &[u8]) -> Vec<DocMention> {
    match kind {
        DocKind::Markdown => markdown::mentions(src),
        DocKind::Rst => rst::mentions(src),
    }
}

/// The name of code in `raw` (a code span's or a role's text), or `None` when
/// it is no name: a dotted, `::` or `#` path of identifiers, optionally called
/// (`f()`, `Client.send(request)`), with a role's decoration (`~`, `!`, a
/// leading `.`) taken off. `Literal` text of one segment counts only when it
/// is shaped like code (`send_request`, `Client`, `run()`): a code span holds
/// plain words too.
pub(crate) fn mention(
    raw: &str,
    line: u32,
    form: MentionForm,
    module: Option<&str>,
) -> Option<DocMention> {
    let mut t = raw.trim();
    let called = match t.find('(') {
        Some(open) if t.ends_with(')') => {
            t = &t[..open];
            true
        }
        Some(_) => return None,
        None => false,
    };
    let t = t.trim().trim_start_matches(['~', '!']).trim_start_matches('.');
    if t.is_empty() || t.len() > 200 {
        return None;
    }
    let path = t.replace("::", ".").replace('#', ".");
    let segments: Vec<&str> = path.split('.').collect();
    if segments.len() > 8 || !segments.iter().all(|s| is_identifier(s)) {
        return None;
    }
    if form == MentionForm::Literal && segments.len() == 1 && !called {
        let s = segments[0];
        if !s.contains('_') && !s.chars().any(|c| c.is_ascii_uppercase()) {
            return None;
        }
    }
    Some(DocMention { name: path, line, form, module: module.map(str::to_string) })
}

fn is_identifier(s: &str) -> bool {
    let mut chars = s.chars();
    matches!(chars.next(), Some(c) if c.is_ascii_alphabetic() || c == '_' || c == '$')
        && chars.all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '$')
}

/// The code spans of one stretch of Markdown text that starts on line `first`:
/// `(content, line)` for each run of backticks closed by one of the same length.
pub(crate) fn code_spans(text: &str, first: u32) -> Vec<(String, u32)> {
    let bytes = text.as_bytes();
    let mut out = Vec::new();
    let mut i = 0;
    let mut line = first;
    while i < bytes.len() {
        match bytes[i] {
            b'\n' => {
                line += 1;
                i += 1;
            }
            b'\\' => i += 2,
            b'`' => {
                let start = i;
                while i < bytes.len() && bytes[i] == b'`' {
                    i += 1;
                }
                let run = i - start;
                let mut j = i;
                let mut close = None;
                while j < bytes.len() {
                    if bytes[j] == b'`' {
                        let k = j;
                        while j < bytes.len() && bytes[j] == b'`' {
                            j += 1;
                        }
                        if j - k == run {
                            close = Some(k);
                            break;
                        }
                    } else {
                        j += 1;
                    }
                }
                let Some(end) = close else { continue };
                out.push((text[i..end].to_string(), line));
                line += text[start..j].matches('\n').count() as u32;
                i = j;
            }
            _ => i += 1,
        }
    }
    out
}

/// A section runs to the line before the next heading of the same or a higher
/// level, or to the end of the file.
fn close_sections(found: &mut [Heading], last_line: u32) {
    for i in 0..found.len() {
        let level = found[i].level;
        let end = found[i + 1..]
            .iter()
            .find(|h| h.level <= level)
            .map(|h| h.line.saturating_sub(1))
            .unwrap_or(last_line);
        found[i].end_line = end.max(found[i].line);
    }
}
