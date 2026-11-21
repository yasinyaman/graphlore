# ruff: noqa: E501
"""The native engine's Go edges against the Go type checker's own answers.

Go's compiler front end is a library (`go/types`): for every identifier of a
package it records the object the identifier is bound to, with the position
that declares it. This script runs it on a real repository (it needs `go` and,
once, `golang.org/x/tools/go/packages` from the module proxy), builds the
native graph of the same repository and compares, as `oracle_typescript.py`
does: an edge whose site the compiler has an answer for, does it go to the
declaration the compiler names; and a use the compiler binds to a definition
the graph has, is there an edge.

Unlike the other oracles this one is no test suite: it is the compiler, on
the whole program.

    python benchmarks/oracle_go.py /tmp/polyglot/cobra --samples 20
    python benchmarks/oracle_go.py REPO REPO2 --json out.json
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

DUMP = r'''package main

import (
	"encoding/json"
	"go/ast"
	"go/types"
	"os"
	"path/filepath"
	"strings"

	"golang.org/x/tools/go/packages"
)

type use struct {
	File string `json:"file"`
	Line int    `json:"line"`
	Name string `json:"expr"`
	To   string `json:"to_file"`
	At   int    `json:"to_line"`
	Kind string `json:"what"`
}

func main() {
	root, _ := filepath.Abs(os.Args[1])
	cfg := &packages.Config{Mode: packages.NeedName | packages.NeedFiles | packages.NeedSyntax |
		packages.NeedTypes | packages.NeedTypesInfo | packages.NeedImports | packages.NeedDeps,
		Dir: root, Tests: true}
	pkgs, err := packages.Load(cfg, "./...")
	if err != nil {
		os.Stderr.WriteString(err.Error())
		os.Exit(1)
	}
	out := json.NewEncoder(os.Stdout)
	seen := map[string]bool{}
	for _, p := range pkgs {
		if p.TypesInfo == nil {
			continue
		}
		for id, obj := range p.TypesInfo.Uses {
			if obj == nil || obj.Pkg() == nil {
				continue
			}
			at := p.Fset.Position(id.Pos())
			if !strings.HasPrefix(at.Filename, root+"/") {
				continue
			}
			kind := "other"
			switch o := obj.(type) {
			case *types.Func:
				kind = "func"
			case *types.TypeName:
				kind = "type"
			case *types.Var:
				kind = "var"
				if o.IsField() {
					kind = "field"
				}
			case *types.Const:
				kind = "const"
			case *types.PkgName:
				continue
			}
			to := p.Fset.Position(obj.Pos())
			u := use{File: at.Filename[len(root)+1:], Line: at.Line, Name: id.Name, Kind: kind}
			if strings.HasPrefix(to.Filename, root+"/") {
				u.To, u.At = to.Filename[len(root)+1:], to.Line
			}
			key := u.File + ":" + string(rune(at.Line)) + ":" + string(rune(at.Column)) + u.Name
			if seen[key] {
				continue
			}
			seen[key] = true
			_ = ast.Inspect
			out.Encode(u)
		}
	}
}
'''


def bindings(root: Path, tool: Path) -> list[dict[str, Any]]:
    done = subprocess.run(["go", "run", ".", str(root)], cwd=tool, capture_output=True, text=True,
                          env={**os.environ, "GOFLAGS": "-mod=mod"})
    if done.returncode != 0 and not done.stdout:
        raise SystemExit(f"go/types did not load {root}: {done.stderr[-600:]}")
    return [json.loads(line) for line in done.stdout.splitlines() if line.startswith("{")]


def judge(root: Path, tool: Path) -> dict[str, Any] | None:
    raw = bindings(root, tool)
    files: dict[str, list[str]] = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        if p.is_file() and (p.suffix == ".go" or p.name in ("go.mod", "go.sum")) and "/." not in "/" + rel:
            files[rel] = p.read_text(encoding="utf-8", errors="replace").splitlines()
    uses = []
    for u in raw:
        decls = [(u["to_file"], u["to_line"])] if u.get("to_file") else []
        uses.append({"file": u["file"], "line": u["line"], "expr": u["expr"], "symbol": u["expr"],
                     "decls": decls, "lib": not decls, "alias": False,
                     "declares": (u["file"], u["line"]) in decls})
    result = oracle.compare(root.name, files, uses, slack=(-3, 3))
    if result:
        for row in result["wrong"] + result["missed"]:
            row["case"] = f"{root.name}/{row['file']}"
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("repos", nargs="+", help="Go repositories (each with a go.mod)")
    ap.add_argument("--samples", type=int, default=6)
    ap.add_argument("--json", help="write every wrong and missed row here")
    args = ap.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        tool = Path(tmp)
        (tool / "main.go").write_text(DUMP, encoding="utf-8")
        (tool / "go.mod").write_text("module graphlore.dev/oracle\n\ngo 1.22\n", encoding="utf-8")
        got = subprocess.run(["go", "get", "golang.org/x/tools/go/packages@latest"], cwd=tool,
                             capture_output=True, text=True)
        if got.returncode != 0:
            print(f"golang.org/x/tools is not reachable: {got.stderr[-300:]}", file=sys.stderr)
            return 1
        results = [r for r in (judge(Path(repo).resolve(), tool) for repo in args.repos) if r]
    oracle.report(results, len(args.repos), args.samples, args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
