//! Writing graph.json atomically: a temporary file next to the target, then a
//! rename, so a reader never sees half a graph.

use std::fs;
use std::io::{BufWriter, Write};
use std::path::Path;
use std::thread::sleep;
use std::time::Duration;

use serde::Serialize;

pub fn write_atomic<T: Serialize + ?Sized>(out: &Path, graph: &T) -> Result<(), String> {
    let dir = out.parent().ok_or_else(|| format!("no parent directory for {}", out.display()))?;
    fs::create_dir_all(dir).map_err(|e| format!("cannot create {}: {e}", dir.display()))?;
    let name = out.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    let tmp = dir.join(format!("{name}.tmp-{}", std::process::id()));
    let result = (|| {
        let file =
            fs::File::create(&tmp).map_err(|e| format!("cannot write {}: {e}", tmp.display()))?;
        let mut w = BufWriter::new(file);
        serde_json::to_writer(&mut w, graph).map_err(|e| e.to_string())?;
        w.flush().map_err(|e| e.to_string())?;
        w.get_ref().sync_all().map_err(|e| e.to_string())?;
        Ok::<(), String>(())
    })();
    if let Err(e) = result {
        let _ = fs::remove_file(&tmp);
        return Err(e);
    }
    // On Windows a reader holding the old file open makes the rename fail
    // briefly; retry before giving up.
    let mut last = None;
    for attempt in 0..5 {
        match fs::rename(&tmp, out) {
            Ok(()) => return Ok(()),
            Err(e) => {
                last = Some(e);
                sleep(Duration::from_millis(50 * (attempt + 1)));
            }
        }
    }
    let _ = fs::remove_file(&tmp);
    Err(format!(
        "cannot replace {}: {}",
        out.display(),
        last.map(|e| e.to_string()).unwrap_or_default()
    ))
}
