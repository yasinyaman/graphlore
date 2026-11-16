//! Route goldens: the full path of each route, joined across files from the
//! prefixes of the routers it is mounted under, and the `handles` edge to its
//! handler.

use std::collections::BTreeSet;
use std::path::PathBuf;

use graphlore_engine::{build_graph, BuildOptions};
use serde_json::Value;

fn project(name: &str, files: &[(&str, &str)]) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("graphlore-http-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    for (rel, src) in files {
        let path = dir.join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, src).unwrap();
    }
    dir
}

/// `(route label, handler label)` of every `handles` edge, and the graph.
fn routes(name: &str, files: &[(&str, &str)]) -> (BTreeSet<(String, String)>, Value) {
    let dir = project(name, files);
    let (g, _) = build_graph(&BuildOptions::new(&dir)).unwrap();
    std::fs::remove_dir_all(&dir).unwrap();
    let label = |id: &str| {
        g["nodes"]
            .as_array()
            .unwrap()
            .iter()
            .find(|n| n["id"] == id)
            .map(|n| n["label"].as_str().unwrap().to_string())
            .unwrap_or_else(|| format!("?{id}"))
    };
    let out = g["links"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|e| e["relation"] == "handles")
        .map(|e| (label(e["source"].as_str().unwrap()), label(e["target"].as_str().unwrap())))
        .collect();
    (out, g)
}

#[track_caller]
fn has(got: &BTreeSet<(String, String)>, want: &[(&str, &str)]) {
    for (r, h) in want {
        assert!(got.contains(&(r.to_string(), h.to_string())), "missing {r} -> {h} in {got:#?}");
    }
}

#[test]
fn fastapi_prefixes_across_files() {
    let (got, g) = routes(
        "fastapi",
        &[
            (
                "app/core/config.py",
                "class Settings:\n    API_V1_STR: str = \"/api/v1\"\n\n\nsettings = Settings()\n",
            ),
            (
                "app/api/routes/users.py",
                "from fastapi import APIRouter\n\nrouter = APIRouter(prefix=\"/users\", tags=[\"users\"])\n\n\n@router.get(\"/{user_id}\")\ndef read_user(user_id: int):\n    return user_id\n\n\n@router.post(\"/\")\ndef create_user():\n    return 1\n",
            ),
            (
                "app/api/main.py",
                "from fastapi import APIRouter\n\nfrom app.api.routes import users\n\napi_router = APIRouter()\napi_router.include_router(users.router)\n",
            ),
            (
                "app/main.py",
                "from fastapi import FastAPI\n\nfrom app.api.main import api_router\nfrom app.core.config import settings\n\napp = FastAPI()\napp.include_router(api_router, prefix=settings.API_V1_STR)\n\n\n@app.get(\"/health\")\ndef health():\n    return \"ok\"\n",
            ),
        ],
    );
    has(
        &got,
        &[
            ("GET /api/v1/users/{user_id}", "read_user()"),
            ("POST /api/v1/users", "create_user()"),
            ("GET /health", "health()"),
        ],
    );
    let r = g["nodes"]
        .as_array()
        .unwrap()
        .iter()
        .find(|n| n["label"] == "GET /api/v1/users/{user_id}")
        .unwrap();
    assert_eq!(r["type"], "route");
    assert_eq!(r["key"], "GET /api/v1/users/{}");
    assert_eq!(r["framework"], "fastapi");
    assert_eq!(r["source_file"], "app/api/routes/users.py");
    assert!(r.get("prefix_unresolved").is_none());
}

#[test]
fn flask_blueprint_and_django() {
    let (got, _) = routes(
        "flask",
        &[
            (
                "shop/views.py",
                "from flask import Blueprint\n\nbp = Blueprint(\"shop\", __name__, url_prefix=\"/shop\")\n\n\n@bp.route(\"/items\", methods=[\"GET\", \"POST\"])\ndef items():\n    return []\n",
            ),
            (
                "shop/app.py",
                "from flask import Flask\n\nfrom shop.views import bp\n\napp = Flask(__name__)\napp.register_blueprint(bp, url_prefix=\"/v2\")\n",
            ),
            (
                "site/urls.py",
                "from django.urls import path\n\nfrom site import views\n\nurlpatterns = [path(\"about/\", views.about)]\n",
            ),
            ("site/views.py", "def about(request):\n    return None\n"),
        ],
    );
    has(
        &got,
        &[
            ("GET /v2/shop/items", "items()"),
            ("POST /v2/shop/items", "items()"),
            ("ANY /about", "about()"),
        ],
    );
}

#[test]
fn express_mounts_and_nest_global_prefix() {
    let (got, _) = routes(
        "express",
        &[
            (
                "server/routes/users.js",
                "const express = require('express');\nconst router = express.Router();\n\nfunction listUsers(req, res) {\n  res.json([]);\n}\n\nrouter.get('/', listUsers);\nrouter.get('/:id', function show(req, res) {\n  res.json({});\n});\n\nmodule.exports = router;\n",
            ),
            (
                "server/app.js",
                "const express = require('express');\nconst app = express();\n\napp.use('/api/users', require('./routes/users'));\n",
            ),
            (
                "nest/src/cats.controller.ts",
                "import { Controller, Get } from '@nestjs/common';\n\n@Controller('cats')\nexport class CatsController {\n  @Get(':id')\n  findOne() {\n    return 1;\n  }\n}\n",
            ),
            (
                "nest/src/main.ts",
                "import { NestFactory } from '@nestjs/core';\n\nasync function bootstrap() {\n  const app = await NestFactory.create(AppModule);\n  app.setGlobalPrefix('api');\n}\n",
            ),
        ],
    );
    has(&got, &[("GET /api/users", "listUsers()"), ("GET /api/cats/:id", ".findOne()")]);
    assert!(
        got.iter().any(|(r, _)| r == "GET /api/users/:id"),
        "the inline handler's route: {got:#?}"
    );
}

/// `(source label, route label, confidence)` of every `http_calls` edge.
fn calls(g: &Value) -> BTreeSet<(String, String, String)> {
    let label = |id: &str| {
        g["nodes"]
            .as_array()
            .unwrap()
            .iter()
            .find(|n| n["id"] == id)
            .map(|n| n["label"].as_str().unwrap().to_string())
            .unwrap_or_else(|| format!("?{id}"))
    };
    g["links"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|e| e["relation"] == "http_calls")
        .map(|e| {
            (
                label(e["source"].as_str().unwrap()),
                label(e["target"].as_str().unwrap()),
                e["confidence"].as_str().unwrap().to_string(),
            )
        })
        .collect()
}

#[test]
fn generated_sdk_reaches_fastapi_handlers() {
    let (_, g) = routes(
        "sdk",
        &[
            (
                "backend/app/api/routes/users.py",
                "from fastapi import APIRouter\n\nrouter = APIRouter(prefix=\"/users\")\n\n\n@router.get(\"/{user_id}\")\ndef read_user(user_id: int):\n    return user_id\n\n\n@router.get(\"/\")\ndef read_users():\n    return []\n",
            ),
            (
                "backend/app/main.py",
                "from fastapi import FastAPI\n\nfrom app.api.routes import users\n\napp = FastAPI()\napp.include_router(users.router, prefix=\"/api/v1\")\n",
            ),
            (
                "frontend/src/client/sdk.gen.ts",
                "import { client } from './client.gen';\n\nexport class UsersService {\n  public static readUserById(options: any) {\n    return (options.client ?? client).get({\n      responseType: 'json',\n      url: '/api/v1/users/{user_id}',\n      ...options,\n    });\n  }\n\n  public static readUsers(options?: any) {\n    return (options?.client ?? client).get({ url: '/api/v1/users/', ...options });\n  }\n}\n",
            ),
        ],
    );
    let got = calls(&g);
    for (s, r) in
        [(".readUserById()", "GET /api/v1/users/{user_id}"), (".readUsers()", "GET /api/v1/users")]
    {
        assert!(
            got.contains(&(s.to_string(), r.to_string(), "EXTRACTED".to_string())),
            "missing {s} -> {r} in {got:#?}"
        );
    }
    assert_eq!(g["graph"]["engine"], "graphlore");
}

#[test]
fn fetch_axios_and_python_clients() {
    let (_, g) = routes(
        "clients",
        &[
            (
                "server/app.js",
                "const express = require('express');\nconst app = express();\n\nfunction removeUser(req, res) {\n  res.end();\n}\n\nfunction listUsers(req, res) {\n  res.json([]);\n}\n\napp.delete('/api/users/:id', removeUser);\napp.get('/api/users', listUsers);\n",
            ),
            (
                "web/api.js",
                "import axios from 'axios';\n\nexport const api = axios.create({ baseURL: '/api' });\n\nexport function users() {\n  return api.get('/users');\n}\n\nexport function remove(id) {\n  return fetch(`/api/users/${id}`, { method: 'DELETE' });\n}\n\nexport function removeSeven() {\n  return fetch('/api/users/7', { method: 'DELETE' });\n}\n\nexport function elsewhere() {\n  return fetch('https://example.com/api/users');\n}\n",
            ),
            (
                "tools/check.py",
                "import requests\n\n\ndef check():\n    return requests.get(\"http://localhost:3000/api/users\")\n",
            ),
            (
                "tests/test_users.py",
                "import requests\n\n\ndef test_users():\n    requests.get(\"http://localhost:3000/api/users\")\n",
            ),
        ],
    );
    let got = calls(&g);
    for (s, r, c) in [
        ("users()", "GET /api/users", "EXTRACTED"),
        ("remove()", "DELETE /api/users/:id", "EXTRACTED"),
        ("removeSeven()", "DELETE /api/users/:id", "INFERRED"),
        ("check()", "GET /api/users", "EXTRACTED"),
    ] {
        assert!(
            got.contains(&(s.to_string(), r.to_string(), c.to_string())),
            "missing {s} -> {r} ({c}) in {got:#?}"
        );
    }
    assert!(!got.iter().any(|(s, _, _)| s == "elsewhere()" || s == "test_users()"), "{got:#?}");
}
