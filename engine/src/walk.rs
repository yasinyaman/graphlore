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
    /// The repositories of a workspace, root-relative (`svc-a`,
    /// `services/billing`): empty for a project that is one repository.
    pub repos: Vec<String>,
    /// Go `vendor/` directories left out (`go_vendor_dir`), root-relative,
    /// with the number of source files in each.
    pub vendored_skipped: Vec<(String, usize)>,
}

/// A Go module's `vendor/` directory, written by `go mod vendor`: copies of
/// the dependencies, with `modules.txt` listing them, beside the module's
/// `go.mod`. A build leaves it out unless asked (`read_vendor`): what it
/// holds is other projects' code, often more of it than the project's own
/// (4,394 of kubernetes' 18,946 Go files). The imports that name it become
/// external modules, as they are without a vendor directory.
fn go_vendor_dir(dir: &Path) -> bool {
    dir.file_name().is_some_and(|n| n == "vendor")
        && dir.join("modules.txt").is_file()
        && dir.parent().is_some_and(|p| p.join("go.mod").is_file())
}

/// The source files under `dir` (what a build would have read there).
fn count_sources(dir: &Path) -> usize {
    WalkBuilder::new(dir)
        .standard_filters(false)
        .follow_links(false)
        .build()
        .flatten()
        .filter(|e| e.file_type().is_some_and(|t| t.is_file()))
        .filter(|e| lang::detect(&e.path().to_string_lossy(), None).is_some())
        .count()
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
    repo: &str,
    out_dir: &str,
    seen: &std::collections::HashSet<String>,
) -> Vec<String> {
    let at = if repo.is_empty() { root.to_path_buf() } else { root.join(repo) };
    let listed = std::process::Command::new("git")
        .arg("-C")
        .arg(&at)
        .args(["ls-files", "-z"])
        .stderr(std::process::Stdio::null())
        .output();
    let Ok(out) = listed else { return Vec::new() };
    if !out.status.success() {
        return Vec::new();
    }
    // the root's own ignore files, and a workspace repository's
    let mut ignores = Vec::new();
    for dir in [root.to_path_buf(), at.clone()] {
        if !ignores.is_empty() && dir == root {
            break;
        }
        let mut own = ignore::gitignore::GitignoreBuilder::new(&dir);
        for name in [".graphloreignore", ".graphifyignore"] {
            let file = dir.join(name);
            if file.is_file() {
                let _ = own.add(file);
            }
        }
        if let Ok(g) = own.build() {
            ignores.push(g);
        }
    }
    let mut more = Vec::new();
    for raw in out.stdout.split(|b| *b == 0) {
        let Ok(in_repo) = std::str::from_utf8(raw) else { continue };
        if in_repo.is_empty() {
            continue;
        }
        let full = if repo.is_empty() { in_repo.to_string() } else { format!("{repo}/{in_repo}") };
        let rel = full.as_str();
        if seen.contains(rel) {
            continue;
        }
        let parts: Vec<&str> = rel.split('/').collect();
        let dirs = &parts[..parts.len() - 1];
        if parts.iter().any(|p| p.starts_with('.'))
            || dirs
                .iter()
                .any(|d| *d == out_dir || *d == crate::LEGACY_OUT_DIR_NAME || SKIP_DIRS.contains(d))
        {
            continue;
        }
        if ignores.iter().any(|g| {
            let own = if g.path() == root { rel } else { in_repo };
            g.matched_path_or_any_parents(own, false).is_ignore()
        }) {
            continue;
        }
        more.push(rel.to_string());
    }
    more
}

/// The commit a workspace repository's HEAD is at (`None`: no git, or no
/// commit yet).
pub fn repo_commit(root: &Path, repo: &str) -> Option<String> {
    let out = std::process::Command::new("git")
        .arg("-C")
        .arg(root.join(repo))
        .args(["rev-parse", "HEAD"])
        .stderr(std::process::Stdio::null())
        .output()
        .ok()?;
    let sha = String::from_utf8(out.stdout).ok()?.trim().to_string();
    (out.status.success() && !sha.is_empty()).then_some(sha)
}

/// The name of the file that lists a workspace's repositories.
pub const WORKSPACE_FILE: &str = ".graphlore-workspace.json";

/// The repositories of a workspace: the `repos` a `.graphlore-workspace.json`
/// at the root names (keys of an object, or a list), else, when the root is
/// no git repository itself, every directory one or two levels down that
/// holds a `.git` (a checkout, a worktree, a submodule's file). A project
/// that is one repository, with submodules or not, has none: its graph keeps
/// its one `built_at_commit`.
pub fn workspace_repos(root: &Path, out_dir: &str) -> Vec<String> {
    if let Ok(text) = std::fs::read_to_string(root.join(WORKSPACE_FILE)) {
        if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) {
            let names: Vec<String> = match &v["repos"] {
                serde_json::Value::Object(m) => m.keys().cloned().collect(),
                serde_json::Value::Array(a) => {
                    a.iter().filter_map(|x| x.as_str().map(String::from)).collect()
                }
                _ => Vec::new(),
            };
            let mut names: Vec<String> = names
                .into_iter()
                .map(|n| n.trim_matches('/').to_string())
                .filter(|n| {
                    !n.is_empty()
                        && !n.split('/').any(|p| p == ".." || p == "." || p.is_empty())
                        && root.join(n).is_dir()
                })
                .collect();
            names.sort();
            names.dedup();
            if !names.is_empty() {
                return names;
            }
        }
    }
    if root.join(".git").exists() {
        return Vec::new();
    }
    let mut found = Vec::new();
    let skip = |name: &str| {
        name.starts_with('.')
            || name == out_dir
            || name == crate::LEGACY_OUT_DIR_NAME
            || SKIP_DIRS.contains(&name)
    };
    let Ok(level1) = std::fs::read_dir(root) else { return found };
    let mut dirs: Vec<(String, PathBuf)> = level1
        .flatten()
        .filter(|e| e.file_type().is_ok_and(|t| t.is_dir()))
        .filter_map(|e| Some((e.file_name().to_str()?.to_string(), e.path())))
        .filter(|(n, _)| !skip(n))
        .collect();
    dirs.sort();
    for (name, path) in dirs {
        if path.join(".git").exists() {
            found.push(name);
            continue;
        }
        if path.join("pyvenv.cfg").exists() {
            continue;
        }
        let Ok(level2) = std::fs::read_dir(&path) else { continue };
        let mut inner: Vec<String> = level2
            .flatten()
            .filter(|e| e.file_type().is_ok_and(|t| t.is_dir()))
            .filter_map(|e| e.file_name().to_str().map(String::from))
            .filter(|n| !skip(n) && path.join(n).join(".git").exists())
            .map(|n| format!("{name}/{n}"))
            .collect();
        inner.sort();
        found.extend(inner);
    }
    found
}

/// The directories of the Sphinx projects whose `conf.py` makes `.txt` a
/// source suffix (`source_suffix = ".txt"`, or `.txt` in its list or dict):
/// Django writes its whole documentation so. `""` is the root.
fn sphinx_txt_roots(found: &[(String, PathBuf, u64)]) -> Vec<String> {
    let mut roots = Vec::new();
    for (rel, path, _) in found {
        let (dir, base) = rel.rsplit_once('/').unwrap_or(("", rel.as_str()));
        if base != "conf.py" {
            continue;
        }
        let Ok(text) = std::fs::read_to_string(path) else { continue };
        if source_suffix_has_txt(&text) {
            roots.push(dir.to_string());
        }
    }
    roots
}

/// Whether a `conf.py`'s `source_suffix` assignment names `.txt`. The value is
/// read from the `=` to the end of its line, or past it to the bracket that
/// closes a list, tuple or dict.
fn source_suffix_has_txt(conf: &str) -> bool {
    let mut lines = conf.lines();
    while let Some(line) = lines.next() {
        let Some(rest) = line.strip_prefix("source_suffix") else { continue };
        let Some(value) = rest.trim_start().strip_prefix('=') else { continue };
        let mut value = value.to_string();
        let depth = |v: &str| {
            v.chars().filter(|c| "[({".contains(*c)).count() as i64
                - v.chars().filter(|c| "])}".contains(*c)).count() as i64
        };
        while depth(&value) > 0 {
            let Some(more) = lines.next() else { break };
            value.push('\n');
            value.push_str(more);
        }
        if value.contains("'.txt'") || value.contains("\".txt\"") {
            return true;
        }
    }
    false
}

/// A `.txt` file under one of `roots` is a reStructuredText document, unless
/// its name says it is something else (`requirements.txt`, `robots.txt`).
fn sphinx_txt_doc(rel: &str, roots: &[String]) -> Option<DocKind> {
    if !rel.to_ascii_lowercase().ends_with(".txt") {
        return None;
    }
    let base = rel.rsplit('/').next().unwrap_or(rel).to_ascii_lowercase();
    if base.starts_with("requirements") || base == "robots.txt" || base == "cmakelists.txt" {
        return None;
    }
    roots.iter().any(|r| r.is_empty() || rel.starts_with(&format!("{r}/"))).then_some(DocKind::Rst)
}

pub fn discover(
    root: &Path,
    out_dir_name: &str,
    documents: bool,
    max_bytes: u64,
    read_vendor: bool,
) -> WalkResult {
    let mut result = WalkResult::default();
    let out_dir = out_dir_name.to_string();
    let vendored = std::sync::Arc::new(std::sync::Mutex::new(Vec::<PathBuf>::new()));
    let left_out = vendored.clone();
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
                if name == out_dir
                    || name == crate::LEGACY_OUT_DIR_NAME
                    || SKIP_DIRS.contains(&name.as_ref())
                {
                    return false;
                }
                // a virtualenv, whatever it is called
                if e.path().join("pyvenv.cfg").exists() {
                    return false;
                }
                if !read_vendor && go_vendor_dir(e.path()) {
                    if let Ok(mut v) = left_out.lock() {
                        v.push(e.path().to_path_buf());
                    }
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
    // In a workspace each repository is asked for its own files: the root's
    // `git ls-files` (when the root is a repository too) lists none of them.
    result.repos = workspace_repos(root, out_dir_name);
    let mut skipped: Vec<PathBuf> = vendored.lock().map(|v| v.clone()).unwrap_or_default();
    skipped.sort();
    let skipped_rel: Vec<String> =
        skipped.iter().filter_map(|d| rel_path(root, d)).map(|r| format!("{r}/")).collect();
    for (dir, rel) in skipped.iter().zip(&skipped_rel) {
        result.vendored_skipped.push((rel.trim_end_matches('/').to_string(), count_sources(dir)));
    }
    let mut repos = vec![String::new()];
    repos.extend(result.repos.iter().cloned());
    for repo in &repos {
        for rel in tracked_but_ignored(root, repo, out_dir_name, &seen) {
            if skipped_rel.iter().any(|d| rel.starts_with(d.as_str())) {
                continue;
            }
            let path = root.join(&rel);
            let Ok(meta) = std::fs::symlink_metadata(&path) else { continue };
            if meta.is_file() && seen.insert(rel.clone()) {
                found.push((rel, path, meta.len()));
            }
        }
    }
    let sphinx_txt = sphinx_txt_roots(&found);
    for (rel, path, size) in found {
        let path = path.as_path();
        let base = rel.rsplit('/').next().unwrap_or(&rel);
        if MANIFESTS.contains(&base) || MANIFEST_SUFFIXES.iter().any(|x| base.ends_with(x)) {
            result.manifests.push((rel.clone(), path.to_path_buf()));
        }
        let kind = if let Some(lang) = lang::detect(&rel, None) {
            FileKind::Code(lang)
        } else if let Some(doc) = docs::detect(&rel).or_else(|| sphinx_txt_doc(&rel, &sphinx_txt)) {
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_sphinx_source_suffix_naming_txt_is_read() {
        assert!(source_suffix_has_txt("source_suffix = '.txt'\n"));
        assert!(source_suffix_has_txt("source_suffix = {\n    '.rst': 'restructuredtext',\n    \".txt\": 'restructuredtext',\n}\n"));
        assert!(!source_suffix_has_txt("source_suffix = '.rst'\n# '.txt'\n"));
        assert!(!source_suffix_has_txt("html_extra = ['.txt']\n"));
        let roots = vec!["docs".to_string()];
        assert_eq!(sphinx_txt_doc("docs/ref/models.txt", &roots), Some(DocKind::Rst));
        assert_eq!(sphinx_txt_doc("docs/requirements.txt", &roots), None);
        assert_eq!(sphinx_txt_doc("tests/data.txt", &roots), None);
    }
}
