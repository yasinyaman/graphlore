//! The graph's edges while it is built. Most of them are the resolver's, and
//! a JSON object per edge costs about ten times the edge (`serde_json` keeps
//! each object's keys in order: on kubernetes, 2.4 million edges took 4 GB as
//! objects). So the resolver's edges stay typed (`TypedEdge`) through coupling,
//! communities and the write, and the readers see every edge through `Link`.
//! graph.json is written with the same keys in the same order either way.

use serde::ser::{Serialize, SerializeMap, SerializeSeq, Serializer};
use serde_json::{json, Map, Value};

/// One edge the resolver proved, as graph.json writes it: `source`, `target`,
/// `relation`, `confidence`, `confidence_score`, `weight` (1.0),
/// `source_file`, `source_location` (`L<line>`), `context`, then `runs` for
/// an import.
pub struct TypedEdge {
    pub source: String,
    pub target: String,
    pub relation: &'static str,
    pub confidence: &'static str,
    pub confidence_score: f64,
    /// An index into `Resolved::paths`.
    pub file: usize,
    pub line: u32,
    pub context: &'static str,
    pub runs: Option<&'static str>,
}

/// The resolver's edges, with the paths their `file` indexes.
#[derive(Default)]
pub struct Resolved {
    pub edges: Vec<TypedEdge>,
    pub paths: Vec<String>,
}

impl Resolved {
    pub fn len(&self) -> usize {
        self.edges.len()
    }

    pub fn is_empty(&self) -> bool {
        self.edges.is_empty()
    }
}

/// An edge as its readers see it, whichever form it is kept in.
#[derive(Clone, Copy)]
pub enum Link<'a> {
    Json(&'a Value),
    Typed(&'a TypedEdge, &'a [String]),
}

impl<'a> Link<'a> {
    fn text(v: &'a Value, key: &str) -> &'a str {
        v.get(key).and_then(Value::as_str).unwrap_or("")
    }

    pub fn source(&self) -> &'a str {
        match self {
            Link::Json(v) => Self::text(v, "source"),
            Link::Typed(e, _) => &e.source,
        }
    }

    pub fn target(&self) -> &'a str {
        match self {
            Link::Json(v) => Self::text(v, "target"),
            Link::Typed(e, _) => &e.target,
        }
    }

    pub fn relation(&self) -> &'a str {
        match self {
            Link::Json(v) => Self::text(v, "relation"),
            Link::Typed(e, _) => e.relation,
        }
    }

    pub fn confidence(&self) -> &'a str {
        match self {
            Link::Json(v) => Self::text(v, "confidence"),
            Link::Typed(e, _) => e.confidence,
        }
    }

    pub fn context(&self) -> Option<&'a str> {
        match self {
            Link::Json(v) => v.get("context").and_then(Value::as_str),
            Link::Typed(e, _) => Some(e.context),
        }
    }

    /// `source_file`, as graph.json holds it (`Null` when the edge has none).
    pub fn source_file(&self) -> Value {
        match self {
            Link::Json(v) => v.get("source_file").cloned().unwrap_or(Value::Null),
            Link::Typed(e, paths) => Value::String(paths[e.file].clone()),
        }
    }

    /// `source_location`, as graph.json holds it (`Null` when the edge has none).
    pub fn source_location(&self) -> Value {
        match self {
            Link::Json(v) => v.get("source_location").cloned().unwrap_or(Value::Null),
            Link::Typed(e, _) => Value::String(format!("L{}", e.line)),
        }
    }
}

/// What the readers of edges take: a list of link objects (a graph.json read
/// back, a test's edges) or the build's `Links`.
pub trait LinkSource {
    fn links(&self) -> Box<dyn Iterator<Item = Link<'_>> + '_>;
}

impl LinkSource for [Value] {
    fn links(&self) -> Box<dyn Iterator<Item = Link<'_>> + '_> {
        Box::new(self.iter().map(Link::Json))
    }
}

impl LinkSource for Vec<Value> {
    fn links(&self) -> Box<dyn Iterator<Item = Link<'_>> + '_> {
        self.as_slice().links()
    }
}

/// The edges of a build, in graph.json's order: `head` (the assembler's
/// containment), the resolver's, then `tail` (documents, coupling).
#[derive(Default)]
pub struct Links {
    pub head: Vec<Value>,
    pub resolved: Resolved,
    pub tail: Vec<Value>,
}

impl Links {
    pub fn len(&self) -> usize {
        self.head.len() + self.resolved.len() + self.tail.len()
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// Every edge as a link object, in order.
    pub fn into_values(self) -> Vec<Value> {
        let Links { mut head, resolved, tail } = self;
        head.reserve(resolved.len() + tail.len());
        for e in &resolved.edges {
            let mut v = json!({
                "source": e.source,
                "target": e.target,
                "relation": e.relation,
                "confidence": e.confidence,
                "confidence_score": e.confidence_score,
                "weight": 1.0,
                "source_file": resolved.paths[e.file],
                "source_location": format!("L{}", e.line),
                "context": e.context,
            });
            if let Some(r) = e.runs {
                v["runs"] = json!(r);
            }
            head.push(v);
        }
        head.extend(tail);
        head
    }
}

impl LinkSource for Links {
    fn links(&self) -> Box<dyn Iterator<Item = Link<'_>> + '_> {
        let paths = self.resolved.paths.as_slice();
        Box::new(
            self.head
                .iter()
                .map(Link::Json)
                .chain(self.resolved.edges.iter().map(move |e| Link::Typed(e, paths)))
                .chain(self.tail.iter().map(Link::Json)),
        )
    }
}

struct TypedOut<'a>(&'a TypedEdge, &'a [String]);

impl Serialize for TypedOut<'_> {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        let TypedOut(e, paths) = self;
        let mut m = s.serialize_map(Some(if e.runs.is_some() { 10 } else { 9 }))?;
        m.serialize_entry("source", &e.source)?;
        m.serialize_entry("target", &e.target)?;
        m.serialize_entry("relation", e.relation)?;
        m.serialize_entry("confidence", e.confidence)?;
        m.serialize_entry("confidence_score", &e.confidence_score)?;
        m.serialize_entry("weight", &1.0f64)?;
        m.serialize_entry("source_file", &paths[e.file])?;
        m.serialize_entry("source_location", &format_args!("L{}", e.line))?;
        m.serialize_entry("context", e.context)?;
        if let Some(r) = e.runs {
            m.serialize_entry("runs", r)?;
        }
        m.end()
    }
}

impl Serialize for Links {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        let mut seq = s.serialize_seq(Some(self.len()))?;
        for v in &self.head {
            seq.serialize_element(v)?;
        }
        for e in &self.resolved.edges {
            seq.serialize_element(&TypedOut(e, &self.resolved.paths))?;
        }
        for v in &self.tail {
            seq.serialize_element(v)?;
        }
        seq.end()
    }
}

/// graph.json's top level with its `links` kept apart: written as `top` is,
/// `links` serialized at the place of its key.
pub struct GraphOut<'a> {
    pub top: &'a Map<String, Value>,
    pub links: &'a Links,
}

impl Serialize for GraphOut<'_> {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        let mut m = s.serialize_map(Some(self.top.len()))?;
        for (k, v) in self.top {
            if k == "links" {
                m.serialize_entry(k, self.links)?;
            } else {
                m.serialize_entry(k, v)?;
            }
        }
        m.end()
    }
}
