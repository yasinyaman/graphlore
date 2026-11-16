//! Nodes and links from file facts, in graphify's shape and label conventions:
//! a hub node per file labelled with its basename, functions `name()`, methods
//! `.name()`, types bare; `contains` from a file or a non-class parent,
//! `method` from a class-like parent to a callable, `case_of` to an enum member.

use serde_json::{json, Map, Value};

use super::ids::{file_slugs, slug, IdAllocator};
use crate::facts::{Def, DefKind, FileFacts};

pub struct Assembled {
    pub nodes: Vec<Value>,
    pub links: Vec<Value>,
    pub id_collisions: usize,
    /// The hub node id of each file, in `files` order.
    pub hubs: Vec<String>,
    /// The node id of each definition (`None` when it is no node), per file.
    pub def_ids: Vec<Vec<Option<String>>>,
    /// The definition each definition hangs off (see `resolve_parents`), per file.
    pub parents: Vec<Vec<Option<usize>>>,
    /// The ids handed out, so that the nodes added later stay unique.
    pub ids: IdAllocator,
}

fn basename(rel: &str) -> &str {
    rel.rsplit('/').next().unwrap_or(rel)
}

fn location(line: u32, end: u32) -> String {
    if end > line {
        format!("L{line}-L{end}")
    } else {
        format!("L{line}")
    }
}

pub fn link(source: &str, target: &str, relation: &str, file: &str, line: u32) -> Value {
    json!({
        "source": source,
        "target": target,
        "relation": relation,
        "confidence": "EXTRACTED",
        "confidence_score": 1.0,
        "weight": 1.0,
        "source_file": file,
        "source_location": format!("L{line}"),
    })
}

/// The definition a def hangs off, among those that become nodes: its nearest
/// emitted lexical ancestor when that is class-like; otherwise the type it is
/// declared for (Go receiver, Rust `impl`, `Session::Get`, Swift extension)
/// when the file defines it; otherwise the nearest emitted ancestor, if any.
pub fn resolve_parents(defs: &[Def], emitted: &[bool]) -> Vec<Option<usize>> {
    let mut out: Vec<Option<usize>> = Vec::with_capacity(defs.len());
    for (i, d) in defs.iter().enumerate() {
        let mut lexical = d.parent;
        while let Some(p) = lexical {
            if emitted[p] {
                break;
            }
            lexical = defs[p].parent;
        }
        let class_parent = lexical.filter(|p| defs[*p].kind.is_class_like());
        let resolved = class_parent.or_else(|| {
            let owner = d.owner.as_deref()?;
            let bare = owner.rsplit(['.', ':']).next().unwrap_or(owner);
            defs.iter()
                .enumerate()
                .find(|(j, c)| *j != i && emitted[*j] && c.kind.is_class_like() && c.name == bare)
                .map(|(j, _)| j)
        });
        out.push(resolved.or(lexical));
    }
    out
}

fn def_label(d: &Def, method: bool) -> String {
    if d.kind.is_callable() {
        if method {
            format!(".{}()", d.name)
        } else {
            format!("{}()", d.name)
        }
    } else {
        d.name.clone()
    }
}

pub fn assemble(files: &[FileFacts]) -> Assembled {
    let paths: Vec<String> = files.iter().map(|f| f.path.clone()).collect();
    let slugs = file_slugs(&paths);
    let mut ids = IdAllocator::default();
    let mut nodes = Vec::new();
    let mut links = Vec::new();

    let mut hubs = Vec::with_capacity(files.len());
    let mut all_def_ids = Vec::with_capacity(files.len());
    let mut all_parents = Vec::with_capacity(files.len());
    for (facts, file_slug) in files.iter().zip(&slugs) {
        let rel = facts.path.as_str();
        let hub = ids.claim(file_slug.clone());
        hubs.push(hub.clone());
        let label = basename(rel);
        let mut hub_node = Map::new();
        hub_node.insert("id".into(), json!(hub));
        hub_node.insert("label".into(), json!(label));
        hub_node.insert("norm_label".into(), json!(label.to_lowercase()));
        hub_node
            .insert("file_type".into(), json!(if facts.is_document { "document" } else { "code" }));
        hub_node.insert("source_file".into(), json!(rel));
        hub_node.insert("source_location".into(), json!(location(1, facts.lines.max(1))));
        hub_node.insert("kind".into(), json!("file"));
        if let Some(lang) = facts.lang {
            hub_node.insert("language".into(), json!(lang.name()));
        }
        if facts.is_document {
            hub_node.insert("node_kind".into(), json!("page"));
        }
        nodes.push(Value::Object(hub_node));

        if facts.is_document {
            let mut heading_ids: Vec<(u32, String)> = Vec::new(); // open (level, id)
            for h in &facts.headings {
                while heading_ids.last().is_some_and(|(lvl, _)| *lvl >= h.level) {
                    heading_ids.pop();
                }
                let parent = heading_ids.last().map(|(_, id)| id.clone()).unwrap_or(hub.clone());
                let id = ids.claim(format!("{file_slug}_{}", slug(&h.text)));
                nodes.push(json!({
                    "id": id,
                    "label": h.text,
                    "norm_label": h.text.to_lowercase(),
                    "file_type": "document",
                    "source_file": rel,
                    "source_location": location(h.line, h.end_line),
                    "kind": "heading",
                    "node_kind": "heading",
                    "level": h.level,
                    "end_line": h.end_line,
                }));
                links.push(link(&parent, &id, "contains", rel, h.line));
                heading_ids.push((h.level, id));
            }
            all_def_ids.push(Vec::new());
            all_parents.push(Vec::new());
            continue;
        }

        let Some(lang) = facts.lang else {
            all_def_ids.push(vec![None; facts.defs.len()]);
            all_parents.push(vec![None; facts.defs.len()]);
            continue;
        };
        let module = lang.module();
        let defs = &facts.defs;
        let emitted: Vec<bool> = defs.iter().map(|d| module.emits_node(d.kind)).collect();
        let parents = resolve_parents(defs, &emitted);
        let mut node_ids: Vec<Option<String>> = vec![None; defs.len()];
        let mut chains: Vec<Vec<String>> = vec![Vec::new(); defs.len()];

        for (i, d) in defs.iter().enumerate() {
            if !emitted[i] {
                continue;
            }
            let parent = parents[i];
            let mut chain = parent.map(|p| chains[p].clone()).unwrap_or_default();
            chain.push(d.name.clone());
            let class_parent = parent.is_some_and(|p| defs[p].kind.is_class_like());
            let mut kind = d.kind;
            if kind == DefKind::Function && class_parent {
                kind = DefKind::Method;
            }
            let method = matches!(kind, DefKind::Method | DefKind::Constructor) || class_parent;
            let label = def_label(d, method);
            let id = ids.claim(format!("{file_slug}_{}", slug(&chain.join("_"))));

            let mut node = Map::new();
            node.insert("id".into(), json!(id));
            node.insert("label".into(), json!(label));
            node.insert("norm_label".into(), json!(label.to_lowercase()));
            node.insert("file_type".into(), json!("code"));
            node.insert("source_file".into(), json!(rel));
            node.insert("source_location".into(), json!(location(d.def_line, d.end_line)));
            node.insert("kind".into(), json!(kind.as_str()));
            node.insert("qualname".into(), json!(d.qualname));
            node.insert("language".into(), json!(lang.name()));
            node.insert("region_start".into(), json!(d.region_start));
            node.insert("end_line".into(), json!(d.end_line));
            if kind.is_callable() || kind.is_class_like() {
                node.insert("_callable".into(), json!(true));
            }
            if kind.is_class_like() {
                node.insert("_callable_class".into(), json!(true));
            }
            if d.is_test {
                node.insert("test".into(), json!(true));
            }
            nodes.push(Value::Object(node));

            let (source, relation) = match parent {
                Some(p) => {
                    let rel = if kind == DefKind::EnumMember {
                        "case_of"
                    } else if kind.is_callable() && class_parent {
                        "method"
                    } else {
                        "contains"
                    };
                    (node_ids[p].clone().unwrap_or(hub.clone()), rel)
                }
                None => {
                    (hub.clone(), if kind == DefKind::EnumMember { "case_of" } else { "contains" })
                }
            };
            links.push(link(&source, &id, relation, rel, d.def_line));
            node_ids[i] = Some(id);
            chains[i] = chain;
        }
        all_def_ids.push(node_ids);
        all_parents.push(parents);
    }
    Assembled {
        nodes,
        links,
        id_collisions: ids.collisions,
        hubs,
        def_ids: all_def_ids,
        parents: all_parents,
        ids,
    }
}
