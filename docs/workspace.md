# Several repositories as one workspace

A service and its client, an API and the web app that calls it, a library and
the repositories that use it: when the code you ask about spans checkouts,
graphlore can build them as one graph. Point it at the folder that holds them
and build as usual:

```bash
GRAPHLORE_PROJECT_DIR=~/work python -m graphlore
```

```text
~/work/
├── .graphlore-workspace.json   (optional)
├── api/        (a git checkout)
├── web/        (a git checkout)
└── libs/
    └── common/ (a git checkout)
```

The native engine builds it (a graph graphify wrote before 0.9.0 knows nothing
of repositories inside a folder).

## Which folders are repositories

A folder is a workspace when it is no git repository itself and holds
repositories one or two levels down (a directory with a `.git` directory, or
the `.git` file of a worktree). Hidden directories, `node_modules`,
`__pycache__`, virtualenvs and the output directory are not looked in.

A project that is one repository stays one, whatever it holds: a submodule or
a vendored checkout under it changes nothing.

To choose the repositories yourself, or to name the hosts they serve, write a
`.graphlore-workspace.json` at the folder's root:

```json
{
  "repos": {
    "api": {"hosts": ["api.internal", "api.example.com"]},
    "web": {},
    "libs/common": {}
  }
}
```

`repos` may also be a plain list of paths. A name outside the folder (`..`)
or a directory that is not there is left out.

## What the graph holds

- **Each repository's own files.** The walk honours each repository's
  `.gitignore`, and each repository is asked for its own tracked files
  (`git ls-files`), so a file one tracks is read even when an ignore rule
  matches it. A `.graphloreignore` at the folder's root and one in a
  repository both apply.
- **Each repository's commit.** The graph header has `graph.repos`
  (`{"api": {"commit": "…"}, …}`) in place of the one `built_at_commit` of a
  single repository.
- **Imports that stay home.** An import means a module of the importer's own
  repository first: two services that both have an `app.models`, a crate
  named `shared` in each repository, one fully qualified Java, C# or PHP name
  defined twice, a JavaScript or TypeScript package name (`package.json`'s
  `name`) or a Go module path (`go.mod`, a fork) in both, a Ruby constant or
  `require` path each repository defines. Another repository's module answers
  only when the importer's has none, and in Python only as a package of its
  own (`common_lib.models`), never as a loose module (`import utils`).
- **Requests between repositories.** A request to a relative path, to
  `localhost` or to an unknown base (`${API_URL}/users`) is linked to a route
  of any repository, as in one repository, the nearest first. A request to a
  host that `.graphlore-workspace.json` gives a repository
  (`https://api.internal/users`) is linked to that repository's routes only,
  `EXTRACTED` when one route answers. Without the file, a URL with a real host
  is another service's and is not linked.
- **Route ids that lead with the repository** (`route_api_get_users`), so a
  route another repository adds never renames one of yours.

## What the tools do with it

- **`scope="api"`** narrows any tool that takes a scope to one repository: a
  repository is a path prefix. `graphlore_search` and `graphlore_locate` take
  a `scope` too (for `graphlore_locate` the hits; the semantic cousins stay
  workspace-wide, where duplication across repositories shows).
- **`graphlore_overview`** lists the nodes and edges of each repository and
  the edges between them by relation (`http_calls`, imports).
- **`graphlore_freshness`** answers for each repository against the commit it
  was built at, with the lists put together (paths lead with the repository)
  and the strongest action of any. Answers that flag their own changed files,
  and the graph age in `graphlore_overview`, do the same.
- **`graphlore_diff`** diffs each repository that has both refs, or one with
  `repo="api"`; the refs are each repository's own.
- **Co-change** (`graphlore_surprises`, `graphlore_node_details`) reads each
  repository's history: files of two repositories never share a commit.
- **`graphlore review --repo api`** reviews one repository's commit range;
  the dependents and the tests it lists are the whole workspace graph's, so a
  change in `api` shows the code in `web` that calls it.
- **The hooks** (`graphlore setup claude-code --with-hooks`) say after a git
  command which repository's HEAD left the commit it was built at: the one the
  command ran in, or each of them.

## Limits

- The folder is one project: `graphlore_build(".")` builds every repository,
  and `update=True` re-reads the files that changed in any of them.
- A repository's untracked files that an ignore rule of the folder above it
  matches are not read (its tracked files are).
- A Swift module name two repositories share means the files of both (a C
  or C++ include path means the importer's own header, the nearest file).
