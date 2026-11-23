//! The kernel of graphlore's built-in code search (`src/graphlore/search.py`):
//! its tokenizer and its BM25F loop, ported so that both give the same terms
//! and bit-identical scores. The contract is `tests/fixtures/search_golden.json`
//! (`KERNEL_ABI`), which `tests/search_kernel.rs` here and graphlore's
//! `test_builtin_search_kernel_matches_the_golden_contract` hold both to; a
//! change to `_PyKernel`, `_word_terms`, `_stem`, `_WORD` or `_PART` is a change
//! here too.
//!
//! Every logarithm is computed by the Python caller (each term's idf), so the
//! loop is additions, multiplications and divisions in the order Python does
//! them; Rust never fuses a multiply and an add on its own.

use std::collections::HashMap;

use crate::search_digits::DIGIT_RANGES;

/// The version of the contract: the shape of `tokenize` and `bm25f` and the
/// rules behind them. graphlore uses the kernel only when it expects this one.
pub const KERNEL_ABI: u32 = 1;

/// `_WORD`: `[A-Za-z0-9_$\u0080-\U0010ffff]`.
fn is_word_char(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '_' || c == '$' || c as u32 >= 0x80
}

/// The "lower case" class of `_PART`: `[a-z\u0080-\U0010ffff]` (a non-ASCII
/// letter counts as lower case).
fn is_lower_class(c: char) -> bool {
    c.is_ascii_lowercase() || c as u32 >= 0x80
}

/// `str.isdigit`: non-empty and every character a digit (ASCII, or one of
/// Python's other digits).
fn is_digits(s: &str) -> bool {
    !s.is_empty()
        && s.chars().all(|c| {
            c.is_ascii_digit() || {
                let u = c as u32;
                u >= 0x80
                    && DIGIT_RANGES
                        .binary_search_by(|&(lo, hi)| {
                            if hi < u {
                                std::cmp::Ordering::Less
                            } else if lo > u {
                                std::cmp::Ordering::Greater
                            } else {
                                std::cmp::Ordering::Equal
                            }
                        })
                        .is_ok()
            }
        })
}

/// `_PART.findall(word)`: camel humps, acronyms and digit runs, in order.
/// The pattern is `[A-Z]+(?![a-z…])|[A-Z]?[a-z…]+|[0-9]+`, tried in that
/// order at each position; a position nothing matches at (`_`, `$`) is
/// skipped. The look-ahead of the first alternative backtracks: a run of
/// capitals followed by a lower-case character gives up its last capital
/// (`HTTPServer` is `HTTP`, `Server`).
fn parts(word: &[char]) -> Vec<&[char]> {
    let mut out = Vec::new();
    let mut i = 0;
    let n = word.len();
    while i < n {
        let c = word[i];
        if c.is_ascii_uppercase() {
            let mut j = i;
            while j < n && word[j].is_ascii_uppercase() {
                j += 1;
            }
            let run = j - i;
            if j == n || !is_lower_class(word[j]) {
                out.push(&word[i..j]);
                i = j;
                continue;
            }
            if run >= 2 {
                // the capital before the lower-case letter starts the next hump
                out.push(&word[i..j - 1]);
                i = j - 1;
                continue;
            }
            // one capital, then lower case: the second alternative
            let mut k = i + 1;
            while k < n && is_lower_class(word[k]) {
                k += 1;
            }
            out.push(&word[i..k]);
            i = k;
        } else if is_lower_class(c) {
            let mut k = i;
            while k < n && is_lower_class(word[k]) {
                k += 1;
            }
            out.push(&word[i..k]);
            i = k;
        } else if c.is_ascii_digit() {
            let mut k = i;
            while k < n && word[k].is_ascii_digit() {
                k += 1;
            }
            out.push(&word[i..k]);
            i = k;
        } else {
            i += 1;
        }
    }
    out
}

fn char_len(s: &str) -> usize {
    s.chars().count()
}

/// `_stem`: plurals, -ing, -ed, a trailing -e. Lengths count characters, as
/// Python's do; every suffix is ASCII, so it is cut by its bytes.
pub fn stem(word: &str) -> String {
    let mut w = word.to_string();
    let len = char_len(&w);
    if len > 4 && (w.ends_with("ies") || w.ends_with("ied")) {
        w.truncate(w.len() - 3);
        w.push('y');
    } else if len > 4 && w.ends_with("sses") {
        w.truncate(w.len() - 2);
    } else if len > 3
        && w.ends_with('s')
        && !(w.ends_with("ss") || w.ends_with("us") || w.ends_with("is"))
    {
        w.truncate(w.len() - 1);
    }
    let len = char_len(&w);
    if len > 5 && w.ends_with("ing") {
        w.truncate(w.len() - 3);
    } else if len > 4 && w.ends_with("ed") {
        w.truncate(w.len() - 2);
    }
    if char_len(&w) > 3 && w.ends_with('e') {
        w.truncate(w.len() - 1);
    }
    w
}

/// `_word_terms`: the terms of one identifier-like word, its whole folded
/// form when it has several parts and each part of two characters or more,
/// stemmed; a term made only of digits is none.
pub fn word_terms(word: &[char], out: &mut Vec<String>) {
    let parts: Vec<String> =
        parts(word).into_iter().map(|p| p.iter().collect::<String>().to_lowercase()).collect();
    if parts.len() == 1 {
        let p = &parts[0];
        if char_len(p) >= 2 && !is_digits(p) {
            out.push(stem(p));
        }
    } else if !parts.is_empty() {
        let whole: String = parts.concat();
        if !is_digits(&whole) {
            out.push(whole.clone());
        }
        for p in &parts {
            if char_len(p) >= 2 && !is_digits(p) {
                let t = stem(p);
                if t != whole {
                    out.push(t);
                }
            }
        }
    }
}

/// `_PyKernel.tokenize`: the terms of every word of `text`, in order.
pub fn tokenize(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut word: Vec<char> = Vec::new();
    for c in text.chars() {
        if is_word_char(c) {
            word.push(c);
        } else if !word.is_empty() {
            word_terms(&word, &mut out);
            word.clear();
        }
    }
    if !word.is_empty() {
        word_terms(&word, &mut out);
    }
    out
}

/// The BM25F parameters, in `search.py`'s order: `k1`, the weights of name,
/// path and body, then their length normalisations.
#[derive(Clone, Copy, Debug)]
pub struct Params {
    pub k1: f64,
    pub weights: [f64; 3],
    pub b: [f64; 3],
}

impl Params {
    pub fn from_slice(p: &[f64]) -> Option<Self> {
        match p {
            [k1, wn, wp, wb, bn, bp, bb] => {
                Some(Params { k1: *k1, weights: [*wn, *wp, *wb], b: [*bn, *bp, *bb] })
            }
            _ => None,
        }
    }
}

/// One term's contribution to one chunk: `_PyKernel.bm25f`'s inner loop,
/// operation for operation. `tf` and `len` are the term's counts and the
/// chunk's lengths in name, path and body.
pub fn term_score(idf: f64, tf: [i64; 3], len: [i64; 3], avg: [f64; 3], p: &Params) -> f64 {
    let mut tfw = 0.0_f64;
    for f in 0..3 {
        if tf[f] != 0 {
            tfw += p.weights[f] * tf[f] as f64 / (1.0 - p.b[f] + p.b[f] * len[f] as f64 / avg[f]);
        }
    }
    idf * tfw / (p.k1 + tfw)
}

/// One term's postings: `(chunk, (tf_name, tf_path, tf_body))`.
pub type Postings = Vec<(i64, [i64; 3])>;

/// `_PyKernel.bm25f` over plain values: for each term in the order given, its
/// idf and postings `(chunk, tf)`; `lengths` gives a chunk's lengths. Returns
/// `(chunk, score, terms matched)` in the order a chunk was first seen, the
/// score summed over the terms in their order.
pub fn bm25f<L>(
    terms: &[(f64, Postings)],
    mut lengths: L,
    avg: [f64; 3],
    p: &Params,
) -> Vec<(i64, f64, u32)>
where
    L: FnMut(i64) -> [i64; 3],
{
    let mut at: HashMap<i64, usize> = HashMap::new();
    let mut out: Vec<(i64, f64, u32)> = Vec::new();
    for (idf, postings) in terms {
        for &(cid, tf) in postings {
            let s = term_score(*idf, tf, lengths(cid), avg, p);
            match at.get(&cid) {
                Some(&i) => {
                    out[i].1 += s;
                    out[i].2 += 1;
                }
                None => {
                    at.insert(cid, out.len());
                    out.push((cid, s, 1));
                }
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parts_follow_the_python_pattern() {
        let p = |s: &str| -> Vec<String> {
            let w: Vec<char> = s.chars().collect();
            parts(&w).into_iter().map(|x| x.iter().collect()).collect()
        };
        assert_eq!(p("HTTPServer"), ["HTTP", "Server"]);
        assert_eq!(p("parseJSON2Response"), ["parse", "JSON", "2", "Response"]);
        assert_eq!(p("__init__"), ["init"]);
        assert_eq!(p("ABC"), ["ABC"]);
        assert_eq!(p("Äpfel"), ["Äpfel"]); // `Ä` is of the lower-case class
        assert_eq!(p("XMLHttpRequest"), ["XML", "Http", "Request"]);
    }

    #[test]
    fn digits_are_python_s() {
        assert!(is_digits("42") && is_digits("²³") && is_digits("٣"));
        assert!(!is_digits("") && !is_digits("x2") && !is_digits("½"));
    }
}
