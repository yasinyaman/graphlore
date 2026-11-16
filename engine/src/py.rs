//! The Python module `graphlore_engine._native`. Builds release the GIL.

use std::path::PathBuf;
use std::time::Duration;

use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;

use crate::BuildOptions;

/// Build a graph. Returns the report as a JSON string.
#[pyfunction]
#[pyo3(signature = (root, out=None, commit=None, documents=true, out_dir_name="graphify-out",
                    max_file_bytes=crate::DEFAULT_MAX_FILE_BYTES, parse_timeout_ms=10_000,
                    threads=None))]
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
    };
    let report = py.detach(|| crate::build(&opts)).map_err(PyRuntimeError::new_err)?;
    serde_json::to_string(&report).map_err(|e| PyRuntimeError::new_err(e.to_string()))
}

/// `(region_start, end_line, def_line, qualname)` per definition of one file.
#[pyfunction]
fn file_spans(py: Python<'_>, src: &[u8], rel: &str) -> Vec<(u32, u32, u32, String)> {
    let src = src.to_vec();
    py.detach(|| crate::file_spans(&src, rel))
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

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", crate::VERSION)?;
    m.add("SCHEMA_VERSION", crate::SCHEMA_VERSION)?;
    m.add_function(wrap_pyfunction!(build, m)?)?;
    m.add_function(wrap_pyfunction!(file_spans, m)?)?;
    m.add_function(wrap_pyfunction!(file_facts, m)?)?;
    m.add_function(wrap_pyfunction!(languages, m)?)?;
    Ok(())
}
