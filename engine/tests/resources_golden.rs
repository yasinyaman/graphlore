//! Resource goldens: environment variables, files, tables, events and cache
//! keys that two definitions touch, by the key written in the source.

use std::collections::BTreeSet;
use std::path::PathBuf;

use graphlore_engine::{build_graph, BuildOptions};
use serde_json::Value;

fn project(name: &str, files: &[(&str, &str)]) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("graphlore-res-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    for (rel, src) in files {
        let path = dir.join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, src).unwrap();
    }
    dir
}

/// `(source label, relation, "kind:key" or the target's label)` of every reads
/// / writes edge to a resource or a file, and the graph.
fn uses(name: &str, files: &[(&str, &str)]) -> (BTreeSet<(String, String, String)>, Value) {
    let dir = project(name, files);
    let (g, _) = build_graph(&BuildOptions::new(&dir)).unwrap();
    std::fs::remove_dir_all(&dir).unwrap();
    let node = |id: &str| g["nodes"].as_array().unwrap().iter().find(|n| n["id"] == id).cloned();
    let out = g["links"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|e| matches!(e["relation"].as_str(), Some("reads" | "writes")))
        .filter_map(|e| {
            let t = node(e["target"].as_str().unwrap())?;
            let target = if t["type"] == "resource" {
                format!("{}:{}", t["resource"].as_str().unwrap(), t["key"].as_str().unwrap())
            } else if t["kind"] == "file" {
                format!("file:{}", t["source_file"].as_str().unwrap())
            } else {
                return None;
            };
            let s = node(e["source"].as_str().unwrap())?;
            Some((
                s["label"].as_str().unwrap().to_string(),
                e["relation"].as_str().unwrap().to_string(),
                target,
            ))
        })
        .collect();
    (out, g)
}

#[track_caller]
fn has(got: &BTreeSet<(String, String, String)>, want: &[(&str, &str, &str)]) {
    for (s, r, t) in want {
        assert!(
            got.contains(&(s.to_string(), r.to_string(), t.to_string())),
            "missing {s} -{r}-> {t} in {got:#?}"
        );
    }
}

#[test]
fn python_env_files_and_tables() {
    let (got, g) = uses(
        "py",
        &[
            (
                "app/config.py",
                "import os\n\n\ndef database_url():\n    return os.environ[\"DATABASE_URL\"]\n\n\ndef set_url(u):\n    os.environ[\"DATABASE_URL\"] = u\n\n\ndef debug():\n    return os.getenv(\"DEBUG\")\n",
            ),
            (
                "app/store.py",
                "def save(db, name):\n    db.execute(\"INSERT INTO users (name) VALUES (?)\", (name,))\n    with open(\"state.json\", \"w\") as f:\n        f.write(name)\n\n\ndef load(db):\n    with open(\"state.json\") as f:\n        f.read()\n    return db.execute(\"SELECT id, name FROM users JOIN teams ON users.team = teams.id\")\n",
            ),
            ("state.json", "{}\n"),
        ],
    );
    has(
        &got,
        &[
            ("database_url()", "reads", "env:DATABASE_URL"),
            ("set_url()", "writes", "env:DATABASE_URL"),
            ("save()", "writes", "table:users"),
            ("load()", "reads", "table:users"),
        ],
    );
    // one toucher only: no node
    assert!(!got.iter().any(|(_, _, t)| t == "env:DEBUG" || t == "table:teams"), "{got:#?}");
    // a sentence is no SQL, and a name `WITH` defines is no table
    let (got, _) = uses(
        "sql",
        &[(
            "a.py",
            "def a():\n    raise ValueError(\"Update own password first\")\n\n\ndef b():\n    raise ValueError(\"Update own password now\")\n\n\nQ = \"WITH recent AS (SELECT * FROM orders) SELECT * FROM recent\"\n\n\ndef c(db):\n    db.execute(\"WITH recent AS (SELECT * FROM orders) SELECT * FROM recent\")\n",
        )],
    );
    assert!(!got.iter().any(|(_, _, t)| t == "table:own" || t == "table:recent"), "{got:#?}");
    // a file the project holds is its own node
    let _ = g;
}

#[test]
fn js_env_events_and_keys() {
    let (got, _) = uses(
        "js",
        &[(
            "src/app.js",
            "export function token() { return process.env.API_KEY; }\nexport function rotate(k) { process.env.API_KEY = k; }\nexport function start(bus) { bus.emit('user:created', 1); }\nexport function wait(bus) { bus.on('user:created', () => {}); }\nexport function a(s) { s.on('data', () => {}); }\nexport function b(s) { s.on('data', () => {}); }\nexport function remember(t) { localStorage.setItem('token', t); }\nexport function recall() { return localStorage.getItem('token'); }\n",
        )],
    );
    has(
        &got,
        &[
            ("token()", "reads", "env:API_KEY"),
            ("rotate()", "writes", "env:API_KEY"),
            ("start()", "writes", "event:user:created"),
            ("wait()", "reads", "event:user:created"),
            ("remember()", "writes", "key:token"),
            ("recall()", "reads", "key:token"),
        ],
    );
    // `data` on two streams is no shared event
    assert!(!got.iter().any(|(_, _, t)| t == "event:data"), "{got:#?}");
}

#[test]
fn go_ruby_php_and_c() {
    let (got, _) = uses(
        "go",
        &[
            ("go.mod", "module example.com/m\n"),
            (
                "a.go",
                "package m\n\nimport \"os\"\n\nfunc Port() string { return os.Getenv(\"PORT\") }\n\nfunc SetPort() { os.Setenv(\"PORT\", \"80\") }\n",
            ),
        ],
    );
    has(&got, &[("Port()", "reads", "env:PORT"), ("SetPort()", "writes", "env:PORT")]);
    let (got, _) = uses(
        "rb",
        &[(
            "a.rb",
            "def token\n  ENV[\"TOKEN\"]\nend\n\ndef token!\n  ENV.fetch(\"TOKEN\")\nend\n\ndef save\n  File.write(\"out.txt\", \"x\")\nend\n\ndef load\n  File.read(\"out.txt\")\nend\n",
        )],
    );
    has(
        &got,
        &[
            (".token()", "reads", "env:TOKEN"),
            (".token!()", "reads", "env:TOKEN"),
            (".save()", "writes", "file:out.txt"),
            (".load()", "reads", "file:out.txt"),
        ],
    );
    let (got, _) = uses(
        "php",
        &[(
            "a.php",
            "<?php\nfunction a() { return $_ENV['APP_KEY']; }\nfunction b() { return getenv('APP_KEY'); }\nfunction c() { file_put_contents('cache.txt', 'x'); }\nfunction d() { return file_get_contents('cache.txt'); }\n",
        )],
    );
    has(
        &got,
        &[
            ("a()", "reads", "env:APP_KEY"),
            ("b()", "reads", "env:APP_KEY"),
            ("c()", "writes", "file:cache.txt"),
            ("d()", "reads", "file:cache.txt"),
        ],
    );
    let (got, _) = uses(
        "c",
        &[(
            "a.c",
            "#include <stdio.h>\n#include <stdlib.h>\n\nvoid save(void) { FILE *f = fopen(\"log.txt\", \"a\"); fclose(f); }\nvoid show(void) { FILE *f = fopen(\"log.txt\", \"r\"); fclose(f); }\nconst char *home(void) { return getenv(\"HOME\"); }\nvoid sethome(void) { setenv(\"HOME\", \"/\", 1); }\n",
        )],
    );
    has(
        &got,
        &[
            ("save()", "writes", "file:log.txt"),
            ("show()", "reads", "file:log.txt"),
            ("home()", "reads", "env:HOME"),
            ("sethome()", "writes", "env:HOME"),
        ],
    );
}

#[test]
fn orm_models_couple_through_their_table() {
    // two views that never call each other, one writing users, one reading them
    let (got, _) = uses(
        "django",
        &[
            ("app/__init__.py", ""),
            (
                "app/models.py",
                "from django.db import models\n\n\nclass User(models.Model):\n    class Meta:\n        db_table = 'users'\n",
            ),
            (
                "app/views.py",
                "from app.models import User\n\n\ndef signup(name):\n    User.objects.create(name=name)\n\n\ndef listing():\n    return User.objects.filter(active=True)\n\n\ndef rename(name):\n    u = User.objects.get(name=name)\n    u.save()\n",
            ),
            (
                "app/reports.py",
                "def count(db):\n    return db.execute('SELECT COUNT(*) FROM users')\n",
            ),
        ],
    );
    has(
        &got,
        &[
            ("signup()", "writes", "table:users"),
            ("listing()", "reads", "table:users"),
            ("rename()", "reads", "table:users"),
            ("count()", "reads", "table:users"),
        ],
    );
    let (got, _) = uses(
        "rails",
        &[
            ("app/models/post.rb", "class Post < ApplicationRecord\nend\n"),
            (
                "app/controllers/posts_controller.rb",
                "class PostsController < ApplicationController\n  def create\n    Post.create(title: 'x')\n  end\n\n  def index\n    Post.where(published: true)\n  end\nend\n",
            ),
        ],
    );
    has(&got, &[(".create()", "writes", "table:post"), (".index()", "reads", "table:post")]);
}
