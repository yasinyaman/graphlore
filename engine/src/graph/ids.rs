//! Node ids in graphify's scheme, so ids agree across the two engines in the
//! common case: the file path without its extension, then the chain of
//! enclosing definitions, slugged (`core/src/lib.rs` + `Fixed.acquire` ->
//! `core_src_lib_fixed_acquire`).

use std::collections::{HashMap, HashSet};

/// Lower-case, `?` -> `_pred`, `!` -> `_bang`, every other run of
/// non-alphanumerics -> `_`, no leading or trailing `_`.
pub fn slug(s: &str) -> String {
    let mut words: Vec<String> = Vec::new();
    let mut word = String::new();
    for ch in s.chars() {
        if ch.is_alphanumeric() {
            word.extend(ch.to_lowercase());
            continue;
        }
        if !word.is_empty() {
            words.push(std::mem::take(&mut word));
        }
        match ch {
            '?' => words.push("pred".into()),
            '!' => words.push("bang".into()),
            _ => {}
        }
    }
    if !word.is_empty() {
        words.push(word);
    }
    words.join("_")
}

/// The path without the extension of its last component.
pub fn strip_ext(rel: &str) -> &str {
    let base_start = rel.rfind('/').map(|i| i + 1).unwrap_or(0);
    match rel[base_start..].rfind('.') {
        Some(dot) if dot > 0 => &rel[..base_start + dot],
        _ => rel,
    }
}

/// One slug per file: the extension is left out unless two files share the
/// rest of the path (`session.h` / `session.cpp`), in which case every file of
/// that group keeps it. Independent of the order files are listed in.
pub fn file_slugs(paths: &[String]) -> Vec<String> {
    let stems: Vec<String> = paths.iter().map(|p| slug(strip_ext(p))).collect();
    let mut counts: HashMap<&str, usize> = HashMap::new();
    for s in &stems {
        *counts.entry(s.as_str()).or_default() += 1;
    }
    paths
        .iter()
        .zip(&stems)
        .map(|(p, s)| if counts[s.as_str()] > 1 { slug(p) } else { s.clone() })
        .collect()
}

/// Hands out ids, suffixing `_2`, `_3`, … when one is taken: the first in path
/// and source order keeps the plain id.
#[derive(Default)]
pub struct IdAllocator {
    taken: HashSet<String>,
    pub collisions: usize,
}

impl IdAllocator {
    pub fn claim(&mut self, base: String) -> String {
        let base = if base.is_empty() { "_".to_string() } else { base };
        if self.taken.insert(base.clone()) {
            return base;
        }
        self.collisions += 1;
        let mut n = 2;
        loop {
            let candidate = format!("{base}_{n}");
            if self.taken.insert(candidate.clone()) {
                return candidate;
            }
            n += 1;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn slugs_match_graphify() {
        assert_eq!(slug(strip_ext("svc/__init__.py")), "svc_init");
        assert_eq!(slug(strip_ext("web/src/dto/create.dto.ts")), "web_src_dto_create_dto");
        assert_eq!(slug(strip_ext("README.md")), "readme");
        assert_eq!(slug("Shop_Cart_empty?"), "shop_cart_empty_pred");
        assert_eq!(slug("save!"), "save_bang");
    }

    #[test]
    fn same_stem_files_keep_their_extension() {
        let paths =
            vec!["a/session.cpp".to_string(), "a/session.h".to_string(), "b.go".to_string()];
        assert_eq!(file_slugs(&paths), vec!["a_session_cpp", "a_session_h", "b"]);
    }

    #[test]
    fn later_duplicates_are_suffixed() {
        let mut ids = IdAllocator::default();
        assert_eq!(ids.claim("x".into()), "x");
        assert_eq!(ids.claim("x".into()), "x_2");
        assert_eq!(ids.claim("x".into()), "x_3");
        assert_eq!(ids.collisions, 2);
    }
}
