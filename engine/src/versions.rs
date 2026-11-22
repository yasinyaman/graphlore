//! The language version a project says it is written in.
//!
//! Each language's build manifest names one: the `go` line of `go.mod`, a
//! crate's `edition`, `<LangVersion>` or the target framework of a `.csproj`,
//! `requires-python`, `maven.compiler.release`, … (`docs/rule-sources.md` has
//! the list). A file's version is the one of the nearest manifest at or above
//! its directory that states one for the file's language; none says nothing,
//! and the reader then takes the newest, as it always did.
//!
//! No resolution rule reads this yet: it is recorded in the graph's header
//! (`graph.language_versions`) and is here for the rules that depend on it.

use std::path::{Path, PathBuf};

use regex::Regex;

/// One version a manifest states: for the files of `lang` under `dir`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Declared {
    /// The manifest's directory, root-relative (`""` for the root).
    pub dir: String,
    /// The language's name, as `Lang::name` gives it.
    pub lang: &'static str,
    pub version: String,
    /// The manifest it was read from, root-relative.
    pub from: String,
    /// Not stated, but what the manifest's target implies (a C# project's
    /// framework): a version stated above it in the tree comes first.
    pub derived: bool,
}

fn dir_of(rel: &str) -> &str {
    rel.rsplit_once('/').map_or("", |x| x.0)
}

fn first(re: &str, src: &str) -> Option<String> {
    Regex::new(re).ok()?.captures(src)?.get(1).map(|m| m.as_str().trim().to_string())
}

/// The lowest version a requirement admits: `>=3.9,<4` is 3.9, `^8.1` 8.1.
fn lowest(spec: &str) -> Option<String> {
    let re = Regex::new(r"(\d+(?:\.\d+)?)").ok()?;
    let mut found: Vec<(u32, u32, String)> = Vec::new();
    for part in spec.split([',', '|', ' ']) {
        let part = part.trim();
        if part.is_empty() || part.starts_with('<') || part.starts_with('!') {
            continue;
        }
        if let Some(m) = re.captures(part).and_then(|c| c.get(1)) {
            let text = m.as_str();
            let mut it = text.split('.').map(|x| x.parse::<u32>().unwrap_or(0));
            found.push((it.next().unwrap_or(0), it.next().unwrap_or(0), text.to_string()));
        }
    }
    found.sort();
    found.into_iter().next().map(|x| x.2)
}

/// The C# version a target framework compiles with when the project names
/// none (the SDK's defaults: `net8.0` is C# 12, .NET Framework 7.3).
fn csharp_default(tfm: &str) -> Option<&'static str> {
    let t = tfm.trim().to_ascii_lowercase();
    let t = t.split(';').next().unwrap_or(&t).split('-').next().unwrap_or(&t).to_string();
    Some(match t.as_str() {
        "net10.0" => "14",
        "net9.0" => "13",
        "net8.0" => "12",
        "net7.0" => "11",
        "net6.0" => "10",
        "net5.0" => "9",
        "netcoreapp3.0" | "netcoreapp3.1" | "netstandard2.1" => "8",
        _ if t.starts_with("netstandard") || t.starts_with("netcoreapp") => "7.3",
        _ if t.starts_with("net4") || t.starts_with("net3") || t.starts_with("net2") => "7.3",
        _ => return None,
    })
}

/// The mark of a version a manifest implies and does not state.
const DERIVED: &str = "\u{0}derived";

/// Java's `1.8` is 8.
fn plain_java(v: String) -> String {
    v.strip_prefix("1.")
        .filter(|rest| rest.parse::<u32>().is_ok_and(|n| n >= 5))
        .map_or(v.clone(), str::to_string)
}

fn of_manifest(base: &str, src: &str) -> Vec<(&'static str, String)> {
    let mut out: Vec<(&'static str, String)> = Vec::new();
    let mut add = |lang: &'static str, v: Option<String>| {
        if let Some(v) = v.filter(|v| !v.is_empty()) {
            out.push((lang, v));
        }
    };
    match base {
        "go.mod" => add("go", first(r"(?m)^go\s+(\d+\.\d+)", src)),
        "Cargo.toml" => add("rust", first(r#"(?m)^edition\s*=\s*"(\d{4})""#, src)),
        "pyproject.toml" => {
            add(
                "python",
                first(r#"(?m)^requires-python\s*=\s*"([^"]+)""#, src).and_then(|s| lowest(&s)),
            );
        }
        "setup.py" | "setup.cfg" => {
            add(
                "python",
                first(r#"python_requires\s*=\s*["']?([^"'\n]+)"#, src).and_then(|s| lowest(&s)),
            );
        }
        "package.json" => {
            let v = first(r#""typescript"\s*:\s*"([^"]+)""#, src).and_then(|s| lowest(&s));
            add("typescript", v.clone());
            add("tsx", v);
        }
        "Package.swift" => add("swift", first(r"swift-tools-version:\s*(\d+(?:\.\d+)?)", src)),
        "pom.xml" => {
            let java = first(r"<maven\.compiler\.release>\s*([\d.]+)\s*<", src)
                .or_else(|| first(r"<maven\.compiler\.source>\s*([\d.]+)\s*<", src))
                .or_else(|| first(r"<java\.version>\s*([\d.]+)\s*<", src))
                .or_else(|| first(r"<release>\s*([\d.]+)\s*</release>", src));
            add("java", java.map(plain_java));
            add("kotlin", first(r"<kotlin\.version>\s*(\d+\.\d+)", src));
            add("scala", first(r"<scala\.version>\s*(\d+\.\d+)", src));
        }
        "build.gradle" | "build.gradle.kts" => {
            let java = first(r"jvmToolchain\s*\(\s*(\d+)", src)
                .or_else(|| first(r"JavaLanguageVersion\.of\(\s*(\d+)", src))
                .or_else(|| {
                    first(r"sourceCompatibility\s*=?\s*JavaVersion\.VERSION_(\d+(?:_\d+)?)", src)
                })
                .or_else(|| first(r#"sourceCompatibility\s*=?\s*["']?(\d+(?:\.\d+)?)"#, src))
                .map(|v| plain_java(v.replace('_', ".")));
            add("java", java);
            let kotlin = first(r#"languageVersion\s*=\s*["'](\d+\.\d+)"#, src)
                .or_else(|| {
                    first(r"KotlinVersion\.KOTLIN_(\d+_\d+)", src).map(|v| v.replace('_', "."))
                })
                .or_else(|| first(r#"kotlin\(\s*"jvm"\s*\)\s*version\s*"(\d+\.\d+)"#, src))
                .or_else(|| {
                    first(
                        r#"org\.jetbrains\.kotlin\.jvm["']\s*\)?\s*version\s*["'](\d+\.\d+)"#,
                        src,
                    )
                });
            add("kotlin", kotlin);
        }
        "build.sbt" => {
            add("scala", first(r#"scalaVersion\s*:=\s*"(\d+\.\d+)"#, src));
        }
        "composer.json" => {
            add("php", first(r#""php"\s*:\s*"([^"]+)""#, src).and_then(|s| lowest(&s)));
        }
        "CMakeLists.txt" => {
            add("cpp", first(r"CMAKE_CXX_STANDARD\s+(\d+)", src));
            add("c", first(r"CMAKE_C_STANDARD\s+(\d+)", src));
        }
        // Gradle's version catalog: `kotlin = "2.1.0"` under `[versions]`
        "libs.versions.toml" => add("kotlin", first(r#"(?m)^kotlin\s*=\s*"(\d+\.\d+)"#, src)),
        "Gemfile" => add("ruby", first(r#"(?m)^ruby\s+["'](?:[~>=\s]*)(\d+\.\d+)"#, src)),
        ".ruby-version" => add("ruby", first(r"(\d+\.\d+)", src)),
        ".python-version" => add("python", first(r"(\d+\.\d+)", src)),
        _ if base.ends_with(".gemspec") => {
            add(
                "ruby",
                first(r#"required_ruby_version\s*=\s*.*?["']([^"']+)["']"#, src)
                    .and_then(|s| lowest(&s)),
            );
        }
        _ if base.ends_with(".csproj") || base == "Directory.Build.props" => {
            // (`latest`, `preview`, `latestMajor`: the newest the SDK has;
            // `default` is the framework's own)
            let stated = first(r"<LangVersion>\s*([^<\s]+)\s*<", src).and_then(|v| {
                let w = v.to_ascii_lowercase();
                if v.chars().next().is_some_and(|c| c.is_ascii_digit()) {
                    Some(v)
                } else if w.starts_with("latest") || w == "preview" {
                    Some("latest".to_string())
                } else {
                    None
                }
            });
            let by_target = first(r"<TargetFrameworks?>\s*([^<]+?)\s*<", src)
                .and_then(|t| csharp_default(&t).map(str::to_string));
            match (stated, by_target) {
                (Some(v), _) => add("csharp", Some(v)),
                // (marked: `read` strips the mark and sets `derived`)
                (None, Some(v)) => add("csharp", Some(format!("{v}{DERIVED}"))),
                _ => {}
            }
        }
        _ => {}
    }
    out
}

/// Every version the project's manifests state. `.ruby-version` and
/// `.python-version` at the root are read too (the walk skips hidden files).
pub fn read(root: &Path, manifests: &[(String, PathBuf)]) -> Vec<Declared> {
    let mut out = Vec::new();
    let mut all: Vec<(String, PathBuf)> = manifests.to_vec();
    for hidden in [".ruby-version", ".python-version"] {
        let path = root.join(hidden);
        if path.is_file() {
            all.push((hidden.to_string(), path));
        }
    }
    for (rel, abs) in all {
        let base = rel.rsplit('/').next().unwrap_or(&rel);
        let Ok(src) = std::fs::read_to_string(&abs) else { continue };
        // (`gradle/libs.versions.toml` speaks for the build it belongs to)
        let dir = match (base, dir_of(&rel)) {
            ("libs.versions.toml", d) if d == "gradle" || d.ends_with("/gradle") => dir_of(d),
            (_, d) => d,
        };
        for (lang, version) in of_manifest(base, &src) {
            let derived = version.ends_with(DERIVED);
            let version = version.trim_end_matches(DERIVED).to_string();
            out.push(Declared { dir: dir.to_string(), lang, version, from: rel.clone(), derived });
        }
    }
    out.sort_by(|a, b| (&a.dir, a.lang, &a.from).cmp(&(&b.dir, b.lang, &b.from)));
    out
}

/// The version of the file at `path` written in `lang`: the nearest manifest
/// at or above its directory that states one for that language (a manifest
/// in the directory itself before the directory's `Directory.Build.props`).
pub fn of_file<'a>(declared: &'a [Declared], path: &str, lang: &str) -> Option<&'a str> {
    declared
        .iter()
        .filter(|d| {
            d.lang == lang && (d.dir.is_empty() || path.starts_with(&format!("{}/", d.dir)))
        })
        // (one a manifest only implies counts when none above states one)
        .max_by_key(|d| (!d.derived, d.dir.len(), !d.from.ends_with(".props")))
        .map(|d| d.version.as_str())
}
