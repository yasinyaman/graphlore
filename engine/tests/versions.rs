//! The language version a project states, read from each kind of manifest
//! and written to the graph's header.

use graphlore_engine::{build_graph, BuildOptions};

#[test]
fn each_manifest_states_its_languages_version() {
    let dir = std::env::temp_dir().join(format!("graphlore-versions-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    let put = |rel: &str, text: &str| {
        let path = dir.join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    };
    // one small project per language, each in its own directory
    put("go/go.mod", "module example.com/app\n\ngo 1.22\n");
    put("go/a.go", "package app\n\nfunc Run() int {\n\treturn 1\n}\n");
    put("rust/Cargo.toml", "[package]\nname = \"demo\"\nversion = \"0.1.0\"\nedition = \"2021\"\n");
    put("rust/src/lib.rs", "pub fn run() -> u32 {\n    1\n}\n");
    put("cs/App.csproj", "<Project Sdk=\"Microsoft.NET.Sdk\">\n  <PropertyGroup>\n    <TargetFramework>net8.0</TargetFramework>\n  </PropertyGroup>\n</Project>\n");
    put("cs/A.cs", "namespace App;\n\npublic class A\n{\n}\n");
    // (a version the project names wins over its framework's default)
    put("cs/old/Old.csproj", "<Project>\n  <PropertyGroup>\n    <TargetFramework>net8.0</TargetFramework>\n    <LangVersion>9.0</LangVersion>\n  </PropertyGroup>\n</Project>\n");
    put("cs/old/B.cs", "namespace Old\n{\n    public class B\n    {\n    }\n}\n");
    put("py/pyproject.toml", "[project]\nname = \"demo\"\nrequires-python = \">=3.9,<4\"\n");
    put("py/a.py", "def run():\n    return 1\n");
    put("ts/package.json", "{\n  \"name\": \"demo\",\n  \"devDependencies\": {\n    \"typescript\": \"^5.4.2\"\n  }\n}\n");
    put("ts/a.ts", "export function run(): number {\n  return 1;\n}\n");
    put("java/pom.xml", "<project>\n  <properties>\n    <maven.compiler.release>17</maven.compiler.release>\n  </properties>\n</project>\n");
    put("java/A.java", "class A {\n    int run() {\n        return 1;\n    }\n}\n");
    put("kt/build.gradle.kts", "plugins {\n    kotlin(\"jvm\") version \"2.0.21\"\n}\n\nkotlin {\n    jvmToolchain(21)\n}\n");
    put("kt/a.kt", "fun run(): Int = 1\n");
    put("swift/Package.swift", "// swift-tools-version:5.9\nimport PackageDescription\n");
    put("swift/Sources/App/a.swift", "func run() -> Int {\n    return 1\n}\n");
    put("scala/build.sbt", "scalaVersion := \"3.3.1\"\n");
    put("scala/A.scala", "object A {\n  def run(): Int = 1\n}\n");
    put("php/composer.json", "{\n  \"require\": {\n    \"php\": \"^8.1\"\n  }\n}\n");
    put("php/a.php", "<?php\n\nfunction run()\n{\n    return 1;\n}\n");
    put(
        "ruby/tool.gemspec",
        "Gem::Specification.new do |s|\n  s.required_ruby_version = \">= 3.1\"\nend\n",
    );
    put("ruby/a.rb", "def run\n  1\nend\n");
    put("cpp/CMakeLists.txt", "set(CMAKE_CXX_STANDARD 17)\nset(CMAKE_C_STANDARD 11)\n");
    put("cpp/a.cpp", "int run() {\n  return 1;\n}\n");
    put("cpp/b.c", "int run_c(void) {\n  return 1;\n}\n");
    // `latest` is a version of its own; a Gradle version catalog and a
    // Gemfile state one too; Java's `1.8` is 8
    put("cs/new/New.csproj", "<Project>\n  <PropertyGroup>\n    <TargetFrameworks>netstandard2.0;net8.0</TargetFrameworks>\n    <LangVersion>latest</LangVersion>\n  </PropertyGroup>\n</Project>\n");
    put("cs/new/C.cs", "namespace New;\n\npublic class C\n{\n}\n");
    // a version stated above a project that states none wins over what the
    // project's framework implies
    put("props/Directory.Build.props", "<Project>\n  <PropertyGroup>\n    <LangVersion>11.0</LangVersion>\n  </PropertyGroup>\n</Project>\n");
    put("props/lib/Lib.csproj", "<Project>\n  <PropertyGroup>\n    <TargetFramework>netstandard2.0</TargetFramework>\n  </PropertyGroup>\n</Project>\n");
    put("props/lib/D.cs", "namespace Lib\n{\n    public class D\n    {\n    }\n}\n");
    put("catalog/gradle/libs.versions.toml", "[versions]\nkotlin = \"2.1.20\"\n");
    put("catalog/app/a.kt", "fun other(): Int = 1\n");
    put("gem/Gemfile", "source \"https://rubygems.org\"\nruby \"3.2.2\"\n");
    put("gem/a.rb", "def other\n  1\nend\n");
    put("old/build.gradle", "sourceCompatibility = 1.8\n");
    put("old/A.java", "class Old {\n    int run() {\n        return 1;\n    }\n}\n");
    // no manifest: no version (the reader takes the newest, as before)
    put("loose/a.go", "package loose\n\nfunc Run() int {\n\treturn 1\n}\n");

    let (g, _) = build_graph(&BuildOptions::new(&dir)).unwrap();
    let got = &g["graph"]["language_versions"];
    let has = |lang: &str, version: &str| {
        // (a manifest that is itself a source file of the language counts:
        // `build.gradle.kts`, `Package.swift`, a `.gemspec`)
        assert!(got[lang][version].as_u64().is_some_and(|n| n >= 1), "{lang} {version} in {got}");
    };
    has("go", "1.22");
    has("rust", "2021");
    has("csharp", "12");
    has("csharp", "9.0");
    has("python", "3.9");
    has("typescript", "5.4");
    has("java", "17");
    has("kotlin", "2.0");
    has("swift", "5.9");
    has("scala", "3.3");
    has("php", "8.1");
    has("ruby", "3.1");
    has("csharp", "latest");
    has("csharp", "11.0");
    assert!(got["csharp"]["7.3"].is_null(), "{got}");
    has("kotlin", "2.1");
    has("ruby", "3.2");
    has("java", "8");
    has("cpp", "17");
    has("c", "11");
    // (the loose Go file has none: one Go file with a version, not two)
    assert_eq!(got["go"].as_object().unwrap().len(), 1);
    std::fs::remove_dir_all(&dir).unwrap();
}
