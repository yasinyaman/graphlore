//! Which files a build reads: everything under the root that git would not
//! ignore, minus the output directory, dependency and virtualenv folders, and
//! files too large to be source.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use ignore::WalkBuilder;

use crate::docs::{self, DocKind};
use crate::lang::{self, Lang};

/// Source extensions graphlore knows but no module reads yet: reported, not
/// silently dropped.
const KNOWN_UNREAD: &[&str] = &[
    "m", "mm", "lua", "zig", "ex", "exs", "dart", "erl", "hs", "ml", "clj", "pl", "r", "jl", "vue",
    "svelte",
];
/// Directory names never walked into, whatever the ignore files say.
const SKIP_DIRS: &[&str] = &["node_modules", "__pycache__", ".git", ".hg", ".svn"];

pub enum FileKind {
    Code(Lang),
    Doc(DocKind),
}

pub struct Candidate {
    pub rel: String,
    pub abs: PathBuf,
    pub kind: FileKind,
}

/// Build and package manifests the resolver reads (module paths, crate names,
/// path aliases, workspace packages, source roots).
pub const MANIFESTS: &[&str] = &[
    "go.mod",
    "Cargo.toml",
    "package.json",
    "tsconfig.json",
    "jsconfig.json",
    "Package.swift",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    // (read for the language version a project states: `versions.rs`)
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "build.sbt",
    "composer.json",
    "CMakeLists.txt",
    "Directory.Build.props",
    "libs.versions.toml",
    "Gemfile",
];

/// Manifests named by their extension (`App.csproj`, `tool.gemspec`).
pub const MANIFEST_SUFFIXES: &[&str] = &[".csproj", ".gemspec"];

#[derive(Default)]
pub struct WalkResult {
    pub files: Vec<Candidate>,
    /// `(root-relative path, absolute path)` of every manifest found.
    pub manifests: Vec<(String, PathBuf)>,
    pub too_large: Vec<String>,
    pub not_read: BTreeMap<String, usize>,
    pub non_utf8_paths: usize,
}

/// `/`-separated, root-relative; `None` for a name that is not valid UTF-8.
fn rel_path(root: &Path, path: &Path) -> Option<String> {
    let rel = path.strip_prefix(root).ok()?;
    let mut parts = Vec::new();
    for c in rel.components() {
        parts.push(c.as_os_str().to_str()?.to_string());
    }
    Some(parts.join("/"))
}

/// The files git tracks under `root` that the walk left out because an
/// ignore rule matched them. Hidden paths, the directories never walked into
/// and what `.graphloreignore` / `.graphifyignore` at the root name stay out:
/// those are the engine's own rules, not git's.
fn tracked_but_ignored(
    root: &Path,
    out_dir: &str,
    seen: &std::collections::HashSet<String>,
) -> Vec<String> {
    let listed = std::process::Command::new("git")
        .arg("-C")
        .arg(root)
        .args(["ls-files", "-z"])
        .stderr(std::process::Stdio::null())
        .output();
    let Ok(out) = listed else { return Vec::new() };
    if !out.status.success() {
        return Vec::new();
    }
    let mut own = ignore::gitignore::GitignoreBuilder::new(root);
    for name in [".graphloreignore", ".graphifyignore"] {
        let file = root.join(name);
        if file.is_file() {
            let _ = own.add(file);
        }
    }
    let own = own.build().ok();
    let mut more = Vec::new();
    for raw in out.stdout.split(|b| *b == 0) {
        let Ok(rel) = std::str::from_utf8(raw) else { continue };
        if rel.is_empty() || seen.contains(rel) {
            continue;
        }
        let parts: Vec<&str> = rel.split('/').collect();
        let dirs = &parts[..parts.len() - 1];
        if parts.iter().any(|p| p.starts_with('.'))
            || dirs.iter().any(|d| *d == out_dir || SKIP_DIRS.contains(d))
        {
            continue;
        }
        if own.as_ref().is_some_and(|g| g.matched_path_or_any_parents(rel, false).is_ignore()) {
            continue;
        }
        more.push(rel.to_string());
    }
    more
}

pub fn discover(root: &Path, out_dir_name: &str, documents: bool, max_bytes: u64) -> WalkResult {
    let mut result = WalkResult::default();
    let out_dir = out_dir_name.to_string();
    let walker = WalkBuilder::new(root)
        .hidden(true)
        .git_ignore(true)
        .git_global(false)
        .git_exclude(true)
        .require_git(false)
        .follow_links(false)
        .add_custom_ignore_filename(".graphifyignore")
        .add_custom_ignore_filename(".graphloreignore")
        .filter_entry(move |e| {
            let name = e.file_name().to_string_lossy();
            if e.depth() == 0 {
                return true;
            }
            if e.file_type().is_some_and(|t| t.is_dir()) {
                if name == out_dir || SKIP_DIRS.contains(&name.as_ref()) {
                    return false;
                }
                // a virtualenv, whatever it is called
                if e.path().join("pyvenv.cfg").exists() {
                    return false;
                }
            }
            true
        })
        .build();
    let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
    let mut found: Vec<(String, PathBuf, u64)> = Vec::new();
    for entry in walker.flatten() {
        if !entry.file_type().is_some_and(|t| t.is_file()) {
            continue;
        }
        let path = entry.path();
        let Some(rel) = rel_path(root, path) else {
            result.non_utf8_paths += 1;
            continue;
        };
        let size = entry.metadata().map(|m| m.len()).unwrap_or(0);
        seen.insert(rel.clone());
        found.push((rel, path.to_path_buf(), size));
    }
    // A file git tracks is part of the repository whatever an ignore rule
    // says of its name: git itself ignores only what is untracked
    // (`**/package.json` in a package's `.gitignore`, a line `test` beside a
    // committed `tests/unity/test/`). The walker cannot know what is tracked,
    // so git is asked; without git, or outside a repository, nothing is added.
    for rel in tracked_but_ignored(root, out_dir_name, &seen) {
        let path = root.join(&rel);
        let Ok(meta) = std::fs::symlink_metadata(&path) else { continue };
        if meta.is_file() {
            found.push((rel, path, meta.len()));
        }
    }
    for (rel, path, size) in found {
        let path = path.as_path();
        let base = rel.rsplit('/').next().unwrap_or(&rel);
        if MANIFESTS.contains(&base) || MANIFEST_SUFFIXES.iter().any(|x| base.ends_with(x)) {
            result.manifests.push((rel.clone(), path.to_path_buf()));
        }
        let kind = if let Some(lang) = lang::detect(&rel, None) {
            FileKind::Code(lang)
        } else if let Some(doc) = docs::detect(&rel) {
            if !documents {
                continue;
            }
            FileKind::Doc(doc)
        } else {
            if let Some(ext) = rel.rsplit('/').next().and_then(|b| b.rsplit_once('.')).map(|p| p.1)
            {
                let ext = ext.to_ascii_lowercase();
                if KNOWN_UNREAD.contains(&ext.as_str()) {
                    *result.not_read.entry(format!(".{ext}")).or_default() += 1;
                }
            }
            continue;
        };
        if size > max_bytes {
            result.too_large.push(rel);
            continue;
        }
        result.files.push(Candidate { rel, abs: path.to_path_buf(), kind });
    }
    result.files.sort_by(|a, b| a.rel.cmp(&b.rel));
    result.manifests.sort();
    result.too_large.sort();
    result
}
