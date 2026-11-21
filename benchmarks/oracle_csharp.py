# ruff: noqa: E501
"""The native engine's C# edges against the C# compiler's own answers.

Roslyn, the C# compiler, is a library: for every name of a program its
semantic model gives the symbol the name is bound to and where the source
declares it. This script compiles the sources of a real repository with it
(one compilation of every `.cs` file, against the runtime's own assemblies;
it needs `dotnet` and, once, the `Microsoft.CodeAnalysis.CSharp` package),
builds the native graph of the same repository and compares, as
`oracle_typescript.py` does: an edge whose site the compiler has an answer
for, does it go to the declaration the compiler names; and a use the compiler
binds to a definition the graph has, is there an edge.

A name the compiler cannot bind without the repository's NuGet packages has
no answer here and is left out: what is compared is what the sources
themselves decide.

    python benchmarks/oracle_csharp.py /tmp/polyglot/restsharp --samples 20
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oracle_typescript as oracle  # noqa: E402

PROJECT = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net8.0</TargetFramework>
    <Nullable>disable</Nullable>
    <ImplicitUsings>enable</ImplicitUsings>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.CodeAnalysis.CSharp" Version="4.11.0" />
  </ItemGroup>
</Project>
"""

DUMP = r'''using System.Text.Json;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

var root = Path.GetFullPath(args[0]).TrimEnd('/') + "/";
var skip = new[] { "/bin/", "/obj/", "/.git/", "/node_modules/" };
var files = Directory.EnumerateFiles(root, "*.cs", SearchOption.AllDirectories)
    .Where(f => !skip.Any(s => f.Contains(s))).OrderBy(f => f).ToList();
var options = new CSharpParseOptions(LanguageVersion.Preview);
var trees = files.Select(f => CSharpSyntaxTree.ParseText(File.ReadAllText(f), options, f)).ToList();
var runtime = Path.GetDirectoryName(typeof(object).Assembly.Location)!;
var refs = Directory.EnumerateFiles(runtime, "*.dll")
    .Select(p => { try { return (MetadataReference)MetadataReference.CreateFromFile(p); } catch { return null; } })
    .Where(r => r != null).Select(r => r!).ToList();
var compilation = CSharpCompilation.Create("oracle", trees, refs,
    new CSharpCompilationOptions(OutputKind.DynamicallyLinkedLibrary));
var stdout = new StreamWriter(Console.OpenStandardOutput());
foreach (var tree in trees)
{
    var model = compilation.GetSemanticModel(tree);
    var rel = tree.FilePath.Substring(root.Length);
    foreach (var node in tree.GetRoot().DescendantNodes().OfType<SimpleNameSyntax>())
    {
        var symbol = model.GetSymbolInfo(node).Symbol;
        if (symbol == null) continue; // no answer: an unbound name, or several candidates
        if (symbol is ILocalSymbol or IParameterSymbol or IRangeVariableSymbol or ILabelSymbol
            or INamespaceSymbol or ITypeParameterSymbol or IDiscardSymbol) continue;
        var kind = symbol.Kind.ToString();
        // a constructed generic, a reduced extension: the definition written
        var def = symbol.OriginalDefinition;
        if (def is IMethodSymbol m) def = (m.ReducedFrom ?? m).OriginalDefinition;
        // `new Foo()` binds the name to the type; an accessor is its property
        var at = def.DeclaringSyntaxReferences.FirstOrDefault(r => r.SyntaxTree.FilePath.StartsWith(root));
        var line = node.GetLocation().GetLineSpan().StartLinePosition.Line + 1;
        string toFile = null; int toLine = 0;
        if (at != null)
        {
            toFile = at.SyntaxTree.FilePath.Substring(root.Length);
            // the line its name is on (attributes and modifiers can sit above)
            var decl = at.GetSyntax();
            var loc = def.Locations.FirstOrDefault(l => l.IsInSource && l.SourceTree == at.SyntaxTree) ?? decl.GetLocation();
            toLine = loc.GetLineSpan().StartLinePosition.Line + 1;
        }
        else if (def.DeclaringSyntaxReferences.Length > 0) continue;
        stdout.WriteLine(JsonSerializer.Serialize(new { file = rel, line, expr = node.Identifier.ValueText, to_file = toFile, to_line = toLine, what = kind }));
    }
}
stdout.Flush();
'''


def bindings(root: Path, tool: Path) -> list[dict[str, Any]]:
    done = subprocess.run(["dotnet", "run", "--project", str(tool), "-c", "Release", "--", str(root)],
                          capture_output=True, text=True,
                          env={**os.environ, "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_NOLOGO": "1"})
    rows = [json.loads(line) for line in done.stdout.splitlines() if line.startswith("{")]
    if not rows:
        raise SystemExit(f"Roslyn gave no bindings for {root}: {(done.stderr or done.stdout)[-600:]}")
    return rows


def uses_of(root: Path, tool: Path) -> list[dict[str, Any]]:
    """Roslyn's bindings in the shape `oracle.compare` reads."""
    uses = []
    for u in bindings(root, tool):
        decls = [(u["to_file"], u["to_line"])] if u.get("to_file") else []
        uses.append({"file": u["file"], "line": u["line"], "expr": u["expr"], "symbol": u["expr"],
                     "decls": decls, "lib": not decls, "alias": False,
                     "declares": (u["file"], u["line"]) in decls})
    return uses


def setup(tmp: Path) -> Path:
    """Write the dumping project under `tmp` and return its directory."""
    tool = tmp / "oracle"
    tool.mkdir()
    (tool / "oracle.csproj").write_text(PROJECT, encoding="utf-8")
    (tool / "Program.cs").write_text(DUMP, encoding="utf-8")
    return tool


def judge(root: Path, tool: Path) -> dict[str, Any] | None:
    uses = uses_of(root, tool)
    files: dict[str, list[str]] = {}
    for p in sorted(root.rglob("*.cs")):
        rel = str(p.relative_to(root))
        if not any(part in ("bin", "obj", "node_modules") or part.startswith(".") for part in p.relative_to(root).parts):
            files[rel] = p.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    result = oracle.compare(root.name, files, uses, slack=(-4, 4))
    if result:
        for row in result["wrong"] + result["missed"]:
            row["case"] = f"{root.name}/{row['file']}"
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("repos", nargs="+", help="repositories with C# sources")
    ap.add_argument("--samples", type=int, default=6)
    ap.add_argument("--json", help="write every wrong and missed row here")
    args = ap.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        tool = setup(Path(tmp))
        results = [r for r in (judge(Path(repo).resolve(), tool) for repo in args.repos) if r]
    oracle.report(results, len(args.repos), args.samples, args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
