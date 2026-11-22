//! reStructuredText section titles: a line underlined (and optionally
//! overlined) by a punctuation character at least as long as the title. Levels
//! follow the order in which the adornment styles first appear.

use super::mention;
use crate::facts::{DocMention, Heading, MentionForm};

const ADORNMENT: &str = "=-~^\"'`#*+_:.<>";

fn adornment(line: &str) -> Option<char> {
    let t = line.trim_end();
    let first = t.chars().next()?;
    if t.chars().count() >= 2 && ADORNMENT.contains(first) && t.chars().all(|c| c == first) {
        Some(first)
    } else {
        None
    }
}

pub fn headings(src: &[u8]) -> Vec<Heading> {
    let text = String::from_utf8_lossy(src);
    let lines: Vec<&str> = text.lines().collect();
    let mut styles: Vec<(char, bool)> = Vec::new();
    let mut out = Vec::new();
    let mut i = 0;
    let mut in_literal = false;
    while i + 1 < lines.len() {
        let title = lines[i];
        // `::` opens an indented literal block whose lines are not titles
        if title.trim_end().ends_with("::") {
            in_literal = true;
        } else if !title.is_empty() && !title.starts_with([' ', '\t']) {
            in_literal = false;
        }
        let under = lines[i + 1];
        let overline = i > 0 && adornment(lines[i - 1]).is_some();
        if !in_literal
            && !title.trim().is_empty()
            && !title.starts_with([' ', '\t'])
            && adornment(title).is_none()
        {
            if let Some(ch) = adornment(under) {
                if under.trim_end().chars().count() >= title.trim_end().chars().count() {
                    let style = (ch, overline && adornment(lines[i - 1]) == Some(ch));
                    let level = match styles.iter().position(|s| *s == style) {
                        Some(p) => p + 1,
                        None => {
                            styles.push(style);
                            styles.len()
                        }
                    } as u32;
                    let line = (if style.1 { i } else { i + 1 }) as u32;
                    out.push(Heading {
                        level,
                        text: title.trim().to_string(),
                        line,
                        end_line: line,
                    });
                    i += 2;
                    continue;
                }
            }
        }
        i += 1;
    }
    out
}

/// Roles that name an object of code, in any domain (`:class:`, `:py:meth:`,
/// `:c:func:`); `:mod:`, `:doc:`, `:ref:`, `:setting:` and the rest name none.
const CODE_ROLES: &[&str] = &[
    "class",
    "meth",
    "func",
    "attr",
    "exc",
    "data",
    "obj",
    "const",
    "type",
    "member",
    "struct",
    "macro",
    "enum",
    "enumerator",
    "function",
    "method",
    "var",
    "func_ref",
];
/// Directives that describe an object of code (Sphinx's domains and autodoc).
const OBJECT_DIRECTIVES: &[&str] = &[
    "class",
    "function",
    "method",
    "attribute",
    "exception",
    "data",
    "decorator",
    "staticmethod",
    "classmethod",
    "property",
    "autoclass",
    "autofunction",
    "automethod",
    "autoattribute",
    "autoexception",
    "autodata",
    "autodecorator",
    "autoproperty",
];
/// Directives whose indented content is code, not text.
const CODE_DIRECTIVES: &[&str] = &[
    "code-block",
    "code",
    "sourcecode",
    "literalinclude",
    "doctest",
    "testcode",
    "testoutput",
    "console",
    "ipython",
    "highlight",
    "math",
    "raw",
];

fn indent(line: &str) -> usize {
    line.len() - line.trim_start().len()
}

/// `.. name:: rest` (a domain prefix such as `py:` dropped from the name).
fn directive(line: &str) -> Option<(&str, &str)> {
    let t = line.trim_start().strip_prefix("..")?;
    if !t.starts_with([' ', '\t']) {
        return None;
    }
    let (name, rest) = t.trim_start().split_once("::")?;
    if name.is_empty() || name.contains(char::is_whitespace) {
        return None;
    }
    let name = name.rsplit(':').next().unwrap_or(name);
    Some((name, rest.trim()))
}

/// The names of code a reStructuredText document writes: object roles, inline
/// literals and object directives, outside literal blocks (after `::`) and code
/// directives. A method directive inside a class directive is named under the
/// class; `.. module::` / `.. currentmodule::` give what follows its module.
pub fn mentions(src: &[u8]) -> Vec<DocMention> {
    let text = String::from_utf8_lossy(src);
    let mut out = Vec::new();
    let mut module: Option<String> = None;
    // (indent of the directive, class name) of the class directive we are in
    let mut class: Option<(usize, String)> = None;
    // the indent a literal block or code directive's content must exceed
    let mut literal: Option<usize> = None;
    let mut opens_literal: Option<usize> = None;
    for (i, line) in text.lines().enumerate() {
        let at = i as u32 + 1;
        if line.trim().is_empty() {
            continue;
        }
        let ind = indent(line);
        if let Some(from) = opens_literal.take() {
            if ind > from {
                literal = Some(from);
            }
        }
        if let Some(from) = literal {
            if ind > from {
                continue;
            }
            literal = None;
        }
        if class.as_ref().is_some_and(|(ci, _)| ind <= *ci) {
            class = None;
        }
        if let Some((name, rest)) = directive(line) {
            if CODE_DIRECTIVES.contains(&name) {
                opens_literal = Some(ind);
                continue;
            }
            if matches!(name, "module" | "currentmodule" | "automodule") {
                let m = rest.split_whitespace().next().unwrap_or("");
                module = (!m.is_empty() && m != "None").then(|| m.to_string());
                continue;
            }
            if OBJECT_DIRECTIVES.contains(&name) {
                let target = rest.split(['(', ' ', '[']).next().unwrap_or("");
                let is_class =
                    matches!(name, "class" | "exception" | "autoclass" | "autoexception");
                let qualified = match &class {
                    Some((_, c)) if !is_class && !target.contains('.') => format!("{c}.{target}"),
                    _ => target.to_string(),
                };
                if let Some(m) = mention(&qualified, at, MentionForm::Directive, module.as_deref())
                {
                    out.push(m);
                }
                if is_class {
                    class = Some((ind, target.to_string()));
                }
                continue;
            }
        }
        if line.trim_end().ends_with("::") && directive(line).is_none() {
            opens_literal = Some(ind);
        }
        inline(line, at, module.as_deref(), &mut out);
    }
    out
}

/// Object roles and inline literals on one line of text.
fn inline(line: &str, at: u32, module: Option<&str>, out: &mut Vec<DocMention>) {
    let b = line.as_bytes();
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'`' && b.get(i + 1) == Some(&b'`') {
            // ``literal``
            if let Some(end) = line[i + 2..].find("``") {
                let raw = &line[i + 2..i + 2 + end];
                out.extend(mention(raw, at, MentionForm::Literal, module));
                i += 4 + end;
                continue;
            }
            break;
        }
        if b[i] == b':' {
            // :role:`target`, :domain:role:`target`
            let rest = &line[i + 1..];
            if let Some(tick) = rest.find(":`") {
                let role = &rest[..tick];
                if !role.is_empty()
                    && role
                        .chars()
                        .all(|c| c.is_ascii_alphanumeric() || c == ':' || c == '_' || c == '-')
                {
                    let body = &rest[tick + 2..];
                    if let Some(end) = body.find('`') {
                        let name = role.rsplit(':').next().unwrap_or(role);
                        if CODE_ROLES.contains(&name) {
                            let raw = &body[..end];
                            // `title <target>`
                            let target = match (raw.rfind('<'), raw.ends_with('>')) {
                                (Some(lt), true) => &raw[lt + 1..raw.len() - 1],
                                _ => raw,
                            };
                            out.extend(mention(target, at, MentionForm::Role, module));
                        }
                        i += 1 + tick + 2 + end + 1;
                        continue;
                    }
                }
            }
        }
        i += 1;
    }
}
