# Reviewing a change: `graphlore review` and the GitHub Action

`graphlore review` reads a commit range off the code graph and writes one
report for a pull request: the definitions the change touches, what depends
on them, the tests that reach them, and the import cycles and layer rules it
brings in. It asks no model and reaches no network, so the same commit gives
the same report every time, on a laptop or in CI.

Back to the [README](../README.md) · [Tools](tools.md) · [Concepts](concepts.md)

## On the command line

```bash
graphlore review --base origin/main
```

```bash
graphlore review --base HEAD~3 --format json --output review.json
```

- `--base` (default `HEAD~1`) is compared through its **merge base** with
  `--head` (default `HEAD`): what the branch changed, not what the target
  branch did since. `--head` must be the commit checked out, because the graph
  and the definitions' spans are read from the files.
- The graph must be built at the head commit. When it is missing or older,
  `review` builds it with graphlore's native engine (an update when one
  exists); `--no-build` refuses instead (exit 2). A graph built by graphify
  works when it records the head commit (`built_at_commit`).
- `--hops` (default 3) is how far dependents are followed.
- `--fail-on` takes gates that make the command exit 1:
  - `cycle`: a runtime import cycle introduced (one of its imports is written
    on a changed line);
  - `layer`: a rule of `.graphlore-layers.json` broken on a changed line (see
    `graphlore_layers` in the [tool reference](tools.md));
  - `untested`: a changed definition that no test reaches, directly or through
    one caller. This one is a heuristic: a call through an object or a callback
    is not in the graph.
- Exit codes: 0 clean, 1 a `--fail-on` gate failed, 2 no review could be made
  (not a repository, an unknown ref, a shallow clone without the base commit,
  another commit checked out).

## What the report holds

| Part | How it is made |
|---|---|
| Files | `graphlore_diff`'s changeset: added, removed, renamed and structurally modified files. Cosmetic-only files (comments, formatting) are counted and left out |
| Changed | Each changed line of a source file (`git diff -U0`), blank lines left out, given to the innermost definition around it; a line no definition holds is the file's top level. A definition deleted from a file shows as a change to what held it. Documents and configuration files are listed with the files only |
| What depends on it | One walk from all changed definitions over the graph's edges plus the ones graphlore resolves from the source, as `graphlore_impact` walks: nearest first, production code before tests, the most connected first; the first 60 rows, the rest counted. Shared state, docstrings and documents (a heading naming the code) are not walked; containment is walked, so the files importing a changed file are reached, but the file or class holding a changed definition is not listed |
| Removed | The definitions the base had and the head has not, by qualified name, read from both commits' sources with the span engine; a rename shows as the old name removed. The first 40, the rest counted |
| Tests | `graphlore_tests_for` on each changed production function, method or type (the first 40; a variable, constant or field is read, not called, and is not looked up): tests that call it, or call something that calls it; and the ones no test reaches |
| Import cycles | Cycles between files that pass through a changed file, each with when its imports run (runtime, deferred, type-only) and whether the change `introduced` it |
| Layer rules | With a `.graphlore-layers.json`, the violations in changed files, `introduced` when the import is on a changed line |

The Markdown starts with `<!-- graphlore-review -->` (the Action finds its own
comment by it), quotes every name from the repository as a code span, so no
name from the code under review can become a link, markup or a mention, and
stops at 20,000 characters with a line saying so (`--format json` has
everything). An empty "what depends on it" means no edge was indexed, not that
nothing depends on the code.

## In GitHub Actions

```yaml
name: graphlore review
on: pull_request
permissions:
  contents: read
  pull-requests: write
jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0          # the base commit's history
      - uses: yasinyaman/graphlore@v0.8.0
        with:
          fail-on: cycle,layer    # optional; empty reports only
```

The Action installs graphlore from its own copy with the `engine` extra,
builds the graph, runs `graphlore review` against the pull request's base
commit, writes the report to the job summary and posts it on the pull request,
updating its own earlier comment on each push. A pull request from a fork gets
a read-only token: the report is then in the job summary only. Don't switch to
`pull_request_target` to get around that; it runs with write access on code
from the fork. Inputs: `base`, `fail-on`, `hops`, `project` (a directory inside
the repository), `comment` (`"false"`: the summary only), `graphlore-version`
(a release from PyPI instead of the Action's own copy), `python-version`,
`token`. Outputs: `exit-code`, `report` (the Markdown file).

Pin the Action to a release tag: the tag decides which graphlore runs.

## In GitLab CI

```yaml
graphlore-review:
  image: python:3.12
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
  variables:
    GIT_DEPTH: 0
  script:
    - pip install "graphlore[engine]==0.8.0"
    - git fetch origin "$CI_MERGE_REQUEST_TARGET_BRANCH_NAME"
    - graphlore review --base "origin/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME" --output review.md --fail-on cycle
  artifacts:
    when: always
    paths: [review.md]
```

Posting `review.md` as a merge-request note needs a token with `api` scope
and a call to the notes API; the job above keeps it as an artifact.

## Limits

- The graph is the graph: a call through an object, a callback or dynamic
  dispatch that the graph does not hold is not a dependent, and a test that
  reaches the code that way is not found.
- A definition is mapped by its span at the head commit. A removed one (a
  definition deleted or renamed, every definition of a removed file) is named
  from the two commits' sources under "Removed", but its former callers are
  not: the head graph has no node for it, so nothing depends on it there.
- One project per run (`--project`); a monorepo with several graphs runs it
  once per project.
