//! The Python module `graphlore_engine._native`. Builds release the GIL.

use std::path::PathBuf;
use std::time::Duration;

use pyo3::exceptions::{PyKeyError, PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::BuildOptions;

/// Build a graph. Returns the report as a JSON string.
#[pyfunction]
#[pyo3(signature = (root, out=None, commit=None, documents=true, out_dir_name="graphify-out",
                    max_file_bytes=crate::DEFAULT_MAX_FILE_BYTES, parse_timeout_ms=10_000,
                    threads=None, incremental=false, unresolved=None))]
#[allow(clippy::too_many_arguments)]
fn build(
    py: Python<'_>,
    root: PathBuf,
    out: Option<PathBuf>,
    commit: Option<String>,
    documents: bool,
    out_dir_name: &str,
    max_file_bytes: u64,
    parse_timeout_ms: u64,
    threads: Option<usize>,
    incremental: bool,
    unresolved: Option<PathBuf>,
) -> PyResult<String> {
    let opts = BuildOptions {
        root,
        out,
        commit,
        documents,
        out_dir_name: out_dir_name.to_string(),
        max_file_bytes,
        parse_timeout: (parse_timeout_ms > 0).then(|| Duration::from_millis(parse_timeout_ms)),
        threads,
        incremental,
        unresolved,
    };
    let report = py.detach(|| crate::build(&opts)).map_err(PyRuntimeError::new_err)?;
    serde_json::to_string(&report).map_err(|e| PyRuntimeError::new_err(e.to_string()))
}

/// Detect the communities of a graph.json again and rewrite it. Returns the
/// community report as a JSON string.
#[pyfunction]
fn cluster(py: Python<'_>, graph: PathBuf) -> PyResult<String> {
    let report = py.detach(|| crate::cluster(&graph)).map_err(PyRuntimeError::new_err)?;
    serde_json::to_string(&report).map_err(|e| PyRuntimeError::new_err(e.to_string()))
}

/// `(region_start, end_line, def_line, qualname)` per definition of one file.
#[pyfunction]
fn file_spans(py: Python<'_>, src: &[u8], rel: &str) -> Vec<(u32, u32, u32, String)> {
    let src = src.to_vec();
    py.detach(|| crate::file_spans(&src, rel))
}

/// `file_spans` without reading the rest of the file's facts.
#[pyfunction]
fn definition_spans(py: Python<'_>, src: &[u8], rel: &str) -> Vec<(u32, u32, u32, String)> {
    let src = src.to_vec();
    py.detach(|| crate::definition_spans(&src, rel))
}

/// Everything the engine reads from one file, as a JSON string.
#[pyfunction]
fn file_facts(py: Python<'_>, src: &[u8], rel: &str) -> PyResult<String> {
    let src = src.to_vec();
    let facts = py.detach(|| crate::file_facts(&src, rel));
    serde_json::to_string(&facts).map_err(|e| PyRuntimeError::new_err(e.to_string()))
}

/// `(language, [extensions])` for every language the engine reads.
#[pyfunction]
fn languages() -> Vec<(&'static str, Vec<&'static str>)> {
    crate::languages()
}

/// The kernel of graphlore's built-in search (`search.py`'s `_PyKernel`):
/// the same terms and bit-identical scores, `KERNEL_ABI` its contract.
#[pyclass(frozen, module = "graphlore_engine")]
struct SearchKernel;

#[pymethods]
impl SearchKernel {
    #[new]
    fn new() -> Self {
        SearchKernel
    }

    #[getter]
    fn name(&self) -> &'static str {
        "engine"
    }

    fn tokenize(&self, text: &str) -> Vec<String> {
        crate::search::tokenize(text)
    }

    /// `terms`: `[(idf, {chunk: (tf_name, tf_path, tf_body)})]`; `lengths`:
    /// `{chunk: (name, path, body)}`, read only for the chunks the postings
    /// name; returns `{chunk: (score, terms matched)}`, the chunk keys being
    /// the postings' own objects.
    fn bm25f<'py>(
        &self,
        py: Python<'py>,
        terms: Vec<(f64, Bound<'py, PyDict>)>,
        lengths: Bound<'py, PyDict>,
        avg: (f64, f64, f64),
        params: Vec<f64>,
    ) -> PyResult<Bound<'py, PyDict>> {
        let p = crate::search::Params::from_slice(&params)
            .ok_or_else(|| PyValueError::new_err("bm25f takes 7 parameters"))?;
        let mut keys: Vec<Bound<'py, PyAny>> = Vec::new();
        let mut seen: std::collections::HashMap<i64, usize> = std::collections::HashMap::new();
        let mut plain: Vec<(f64, crate::search::Postings)> = Vec::with_capacity(terms.len());
        let mut lens: Vec<[i64; 3]> = Vec::new();
        for (idf, postings) in &terms {
            let mut list = Vec::with_capacity(postings.len());
            for (k, v) in postings.iter() {
                let cid: i64 = k.extract()?;
                let (a, b, c): (i64, i64, i64) = v.extract()?;
                if let std::collections::hash_map::Entry::Vacant(e) = seen.entry(cid) {
                    let l = lengths.get_item(&k)?.ok_or_else(|| PyKeyError::new_err(cid))?;
                    let (ln, lp, lb): (i64, i64, i64) = l.extract()?;
                    e.insert(keys.len());
                    keys.push(k);
                    lens.push([ln, lp, lb]);
                }
                list.push((cid, [a, b, c]));
            }
            plain.push((*idf, list));
        }
        let avg = [avg.0, avg.1, avg.2];
        let scored = py.detach(|| crate::search::bm25f(&plain, |cid| lens[seen[&cid]], avg, &p));
        let out = PyDict::new(py);
        for (cid, score, matched) in scored {
            out.set_item(&keys[seen[&cid]], (score, matched))?;
        }
        Ok(out)
    }
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("KERNEL_ABI", crate::search::KERNEL_ABI)?;
    m.add_class::<SearchKernel>()?;
    m.add("__version__", crate::VERSION)?;
    m.add("SCHEMA_VERSION", crate::SCHEMA_VERSION)?;
    m.add_function(wrap_pyfunction!(build, m)?)?;
    m.add_function(wrap_pyfunction!(cluster, m)?)?;
    m.add_function(wrap_pyfunction!(file_spans, m)?)?;
    m.add_function(wrap_pyfunction!(definition_spans, m)?)?;
    m.add_function(wrap_pyfunction!(file_facts, m)?)?;
    m.add_function(wrap_pyfunction!(languages, m)?)?;
    Ok(())
}
