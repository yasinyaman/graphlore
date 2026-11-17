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

#[track_caller]
fn has_call(got: &BTreeSet<(String, String, String)>, want: &[(&str, &str, &str)]) {
    for (s, r, c) in want {
        assert!(
            got.contains(&(s.to_string(), r.to_string(), c.to_string())),
            "missing {s} -> {r} ({c}) in {got:#?}"
        );
    }
}

#[test]
fn spring_controllers_and_feign_clients() {
    let (got, g) = routes(
        "spring",
        &[
            (
                "src/main/java/app/OwnerController.java",
                "package app;\n\nimport org.springframework.web.bind.annotation.*;\n\n@RestController\n@RequestMapping(\"/api/owners\")\nclass OwnerController {\n    @GetMapping(\"/{id}\")\n    public Owner show(@PathVariable int id) { return null; }\n\n    @RequestMapping(value = \"/{id}\", method = {RequestMethod.PUT, RequestMethod.PATCH}, produces = \"application/json\")\n    public Owner update(@PathVariable int id) { return null; }\n}\n",
            ),
            (
                "src/main/kotlin/app/client/OwnerClient.kt",
                "package app.client\n\nimport feign.Param\nimport feign.RequestLine\n\ninterface OwnerClient {\n    @RequestLine(\"GET /api/owners/{id}\")\n    fun owner(@Param(\"id\") id: Int): Owner\n}\n",
            ),
            (
                "src/main/java/app/PetsApi.java",
                "package app;\n\nimport org.springframework.cloud.openfeign.FeignClient;\nimport org.springframework.web.bind.annotation.*;\n\n@FeignClient(\"pets\")\ninterface PetsApi {\n    @PutMapping(\"/api/owners/{id}\")\n    void save(@PathVariable int id);\n}\n",
            ),
        ],
    );
    has(
        &got,
        &[
            ("GET /api/owners/{id}", ".show()"),
            ("PUT /api/owners/{id}", ".update()"),
            ("PATCH /api/owners/{id}", ".update()"),
        ],
    );
    // a @FeignClient interface's mapping is a request, not a route
    assert_eq!(got.len(), 3, "{got:#?}");
    has_call(
        &calls(&g),
        &[
            (".owner()", "GET /api/owners/{id}", "EXTRACTED"),
            (".save()", "PUT /api/owners/{id}", "EXTRACTED"),
        ],
    );
}

#[test]
fn jaxrs_resources_and_retrofit_interfaces() {
    let (got, g) = routes(
        "jaxrs",
        &[
            (
                "src/main/java/app/TagsResource.java",
                "package app;\n\nimport jakarta.ws.rs.GET;\nimport jakarta.ws.rs.Path;\n\n@Path(\"/tags\")\npublic class TagsResource {\n    @GET\n    public String all() { return \"\"; }\n\n    @GET\n    @Path(\"/{name}\")\n    public String one(String name) { return name; }\n}\n",
            ),
            (
                "android/src/main/java/app/TagsService.java",
                "package app;\n\nimport retrofit2.Call;\nimport retrofit2.http.GET;\nimport retrofit2.http.Path;\n\npublic interface TagsService {\n    @GET(\"tags/{name}\")\n    Call<String> tag(@Path(\"name\") String name);\n}\n",
            ),
        ],
    );
    has(&got, &[("GET /tags", ".all()"), ("GET /tags/{name}", ".one()")]);
    assert_eq!(got.len(), 2, "a Retrofit interface declares no route: {got:#?}");
    has_call(&calls(&g), &[(".tag()", "GET /tags/{name}", "EXTRACTED")]);
}

#[test]
fn ktor_nested_routes_and_their_controllers() {
    let (got, _) = routes(
        "ktor",
        &[
            (
                "src/main/kotlin/app/Router.kt",
                "package app\n\nimport io.ktor.routing.Routing\nimport io.ktor.routing.get\nimport io.ktor.routing.post\nimport io.ktor.routing.route\n\nfun Routing.users(userController: UserController) {\n    route(\"users\") {\n        post { userController.register(this.context) }\n        post(\"login\") { userController.login(this.context) }\n        route(\"{id}\") {\n            get { userController.show(this.context) }\n        }\n    }\n}\n",
            ),
            (
                "src/main/kotlin/app/UserController.kt",
                "package app\n\nclass UserController {\n    fun register(ctx: Any) {}\n    fun login(ctx: Any) {}\n    fun show(ctx: Any) {}\n}\n",
            ),
        ],
    );
    has(
        &got,
        &[
            ("POST /users", ".register()"),
            ("POST /users/login", ".login()"),
            ("GET /users/{id}", ".show()"),
        ],
    );
}

#[test]
fn aspnet_controllers_minimal_apis_and_http_client() {
    let (got, g) = routes(
        "aspnet",
        &[
            (
                "Api/Controllers/TodosController.cs",
                "using Microsoft.AspNetCore.Mvc;\n\n[ApiController]\n[Route(\"api/[controller]\")]\npublic class TodosController : ControllerBase\n{\n    [HttpGet(\"{id}\")]\n    public IActionResult Get(int id) => Ok();\n\n    [HttpPost(\"~/archive\")]\n    public IActionResult Archive() => Ok();\n}\n",
            ),
            (
                "Api/UsersApi.cs",
                "public static class UsersApi\n{\n    public static void MapUsers(this IEndpointRouteBuilder routes)\n    {\n        var group = routes.MapGroup(\"/users\");\n        group.MapGet(\"/{id}\", (int id) => UserService.Find(id));\n        routes.MapGroup(\"/admin\").MapDelete(\"/{id}\", RemoveUser);\n    }\n\n    public static void MapOther(this IEndpointRouteBuilder routes)\n    {\n        var group = routes.MapGroup(\"/other\");\n        group.MapPost(\"/\", () => 1);\n    }\n\n    static void RemoveUser(int id) {}\n}\n\npublic class UserService\n{\n    public static object Find(int id) => null;\n}\n",
            ),
            (
                "Web/UsersClient.cs",
                "public class UsersClient(HttpClient client)\n{\n    public async Task<User?> GetUserAsync(int id)\n    {\n        return await client.GetFromJsonAsync<User>($\"users/{id}\");\n    }\n\n    public async Task ArchiveAsync()\n    {\n        await client.SendAsync(new HttpRequestMessage(HttpMethod.Post, \"/archive\"));\n    }\n}\n",
            ),
            (
                "Web/Cache.cs",
                "public class Cache(IDistributedCache cache)\n{\n    public Task<byte[]?> Load() => cache.GetAsync(\"users\");\n}\n",
            ),
        ],
    );
    has(
        &got,
        &[
            ("GET /api/Todos/{id}", ".Get()"),
            ("POST /archive", ".Archive()"),
            ("GET /users/{id}", ".Find()"),
            ("DELETE /admin/{id}", ".RemoveUser()"),
            ("POST /other", ".MapOther()"),
        ],
    );
    let c = calls(&g);
    has_call(
        &c,
        &[
            (".GetUserAsync()", "GET /users/{id}", "EXTRACTED"),
            (".ArchiveAsync()", "POST /archive", "EXTRACTED"),
        ],
    );
    assert!(!c.iter().any(|(s, _, _)| s == ".Load()"), "a cache is not an HTTP client: {c:#?}");
}

#[test]
fn gin_groups_across_files_and_chi_lambdas() {
    let (got, g) = routes(
        "gin",
        &[
            ("go.mod", "module example.com/app\n\ngo 1.22\n"),
            (
                "routes/main.go",
                "package routes\n\nimport \"github.com/gin-gonic/gin\"\n\nvar router = gin.Default()\n\nfunc getRoutes() {\n\tv1 := router.Group(\"/v1\")\n\taddUserRoutes(v1)\n\tv2 := router.Group(\"/v2\")\n\taddUserRoutes(v2)\n\trouter.GET(\"/health\", health)\n}\n\nfunc health(c *gin.Context) {}\n",
            ),
            (
                "routes/users.go",
                "package routes\n\nimport \"github.com/gin-gonic/gin\"\n\nfunc addUserRoutes(rg *gin.RouterGroup) {\n\tusers := rg.Group(\"/users\")\n\tusers.GET(\"comments\", listComments)\n\tusers.POST(\"/\", func(c *gin.Context) { createUser(c) })\n}\n\nfunc listComments(c *gin.Context) {}\n\nfunc createUser(c *gin.Context) {}\n",
            ),
            (
                "api/router.go",
                "package api\n\nimport (\n\t\"net/http\"\n\n\t\"github.com/go-chi/chi/v5\"\n)\n\nfunc Router() http.Handler {\n\tr := chi.NewRouter()\n\tr.Route(\"/api\", func(r chi.Router) {\n\t\tr.Get(\"/items/{id}\", getItem)\n\t})\n\treturn r\n}\n\nfunc getItem(w http.ResponseWriter, r *http.Request) {}\n",
            ),
            (
                "cmd/check/main.go",
                "package main\n\nimport \"net/http\"\n\nfunc main() {\n\thttp.Get(\"http://localhost:8080/v1/users/comments\")\n}\n",
            ),
        ],
    );
    has(
        &got,
        &[
            ("GET /v1/users/comments", "listComments()"),
            ("GET /v2/users/comments", "listComments()"),
            ("POST /v1/users", "createUser()"),
            ("GET /health", "health()"),
            ("GET /api/items/{id}", "getItem()"),
        ],
    );
    has_call(&calls(&g), &[("main()", "GET /v1/users/comments", "EXTRACTED")]);
}

#[test]
fn axum_nest_across_files_and_actix_configure() {
    let (got, _) = routes(
        "axum",
        &[
            ("Cargo.toml", "[package]\nname = \"app\"\nversion = \"0.1.0\"\n"),
            (
                "src/main.rs",
                "mod users;\n\nuse axum::{routing::get, Router};\n\nfn app() -> Router {\n    Router::new()\n        .route(\"/health\", get(health))\n        .nest(\"/api\", users::router())\n}\n\nasync fn health() {}\n",
            ),
            (
                "src/users.rs",
                "use axum::{routing::{get, post}, Router};\n\npub fn router() -> Router {\n    Router::new().route(\"/users/:id\", get(show_user).post(update_user))\n}\n\nasync fn show_user() {}\n\nasync fn update_user() {}\n",
            ),
            (
                "web/src/lib.rs",
                "use actix_web::{web, App};\n\n#[actix_web::get(\"/ping\")]\nasync fn ping() {}\n\npub fn app() {\n    App::new().service(web::scope(\"/v1\").configure(routes));\n}\n\nfn routes(cfg: &mut web::ServiceConfig) {\n    cfg.service(web::resource(\"items\").route(web::post().to(add_item)));\n}\n\nasync fn add_item() {}\n",
            ),
        ],
    );
    has(
        &got,
        &[
            ("GET /health", "health()"),
            ("GET /api/users/:id", "show_user()"),
            ("POST /api/users/:id", "update_user()"),
            ("GET /ping", "ping()"),
            ("POST /v1/items", "add_item()"),
        ],
    );
}

#[test]
fn rails_routes_reach_controller_actions() {
    let (got, g) = routes(
        "rails",
        &[
            (
                "config/routes.rb",
                "Rails.application.routes.draw do\n  namespace :api do\n    resource :user, only: [:show]\n    resources :articles, param: :slug, only: [:index, :show] do\n      get :feed, on: :collection\n    end\n  end\n  get 'about', to: 'pages#about'\nend\n",
            ),
            (
                "app/controllers/api/users_controller.rb",
                "module Api\n  class UsersController < ApplicationController\n    def show; end\n  end\nend\n",
            ),
            (
                "app/controllers/api/articles_controller.rb",
                "module Api\n  class ArticlesController < ApplicationController\n    def index; end\n    def show; end\n    def feed; end\n  end\nend\n",
            ),
            ("app/controllers/pages_controller.rb", "class PagesController < ApplicationController\n  def about; end\nend\n"),
            ("spec/requests/articles_spec.rb", "describe 'articles' do\n  it 'lists' do\n    get '/api/articles'\n  end\nend\n"),
        ],
    );
    has(
        &got,
        &[
            ("GET /api/user", ".show()"),
            ("GET /api/articles", ".index()"),
            ("GET /api/articles/:slug", ".show()"),
            ("GET /api/articles/feed", ".feed()"),
            ("GET /about", ".about()"),
        ],
    );
    assert_eq!(g["graph"]["engine"], "graphlore");
    assert!(calls(&g).is_empty(), "a request spec is counted, not linked");
}

#[test]
fn laravel_route_files_and_symfony_attributes() {
    let (got, _) = routes(
        "laravel",
        &[
            (
                "app/Providers/RouteServiceProvider.php",
                "<?php\nnamespace App\\Providers;\n\nuse Illuminate\\Support\\Facades\\Route;\n\nclass RouteServiceProvider\n{\n    public function boot()\n    {\n        Route::prefix('api')->middleware('api')->group(base_path('routes/api.php'));\n    }\n}\n",
            ),
            (
                "routes/api.php",
                "<?php\nuse Illuminate\\Support\\Facades\\Route;\nuse App\\Http\\Controllers\\UserController;\nuse App\\Http\\Controllers\\ArticleController;\n\nRoute::prefix('users')->group(function () {\n    Route::post('login', [UserController::class, 'login']);\n});\nRoute::apiResource('articles', ArticleController::class)->only(['index', 'show']);\n",
            ),
            (
                "app/Http/Controllers/UserController.php",
                "<?php\nnamespace App\\Http\\Controllers;\n\nclass UserController\n{\n    public function login() {}\n}\n",
            ),
            (
                "app/Http/Controllers/ArticleController.php",
                "<?php\nnamespace App\\Http\\Controllers;\n\nclass ArticleController\n{\n    public function index() {}\n    public function show() {}\n}\n",
            ),
            (
                "src/Controller/BlogController.php",
                "<?php\nnamespace App\\Controller;\n\nuse Symfony\\Component\\Routing\\Attribute\\Route;\n\n#[Route('/blog')]\nclass BlogController\n{\n    #[Route('/{slug}', name: 'post', methods: ['GET'])]\n    public function post() {}\n}\n",
            ),
        ],
    );
    has(
        &got,
        &[
            ("POST /api/users/login", ".login()"),
            ("GET /api/articles", ".index()"),
            ("GET /api/articles/{id}", ".show()"),
            ("GET /blog/{slug}", ".post()"),
        ],
    );
}
