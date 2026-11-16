//! reStructuredText section titles: a line underlined (and optionally
//! overlined) by a punctuation character at least as long as the title. Levels
//! follow the order in which the adornment styles first appear.

use crate::facts::Heading;

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
