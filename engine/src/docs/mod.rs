//! Documents read without an LLM: the heading outline of Markdown and
//! reStructuredText files.

use crate::facts::Heading;

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
