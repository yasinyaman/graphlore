//! `cargo run --example facts -- <file> [as-name]`: print a file's imports and references.
fn main() {
    let path = std::env::args().nth(1).expect("file");
    let rel = std::env::args().nth(2).unwrap_or_else(|| path.clone());
    let src = std::fs::read(&path).unwrap();
    let f = graphlore_engine::file_facts(&src, &rel);
    println!("package: {:?}", f.package);
    for i in &f.imports {
        println!(
            "I L{} {:?} {:?} {:?} alias={:?} {:?}{}{}",
            i.line,
            i.form,
            i.module,
            i.names,
            i.alias,
            i.runs,
            if i.is_static { " static" } else { "" },
            if i.system { " system" } else { "" }
        );
    }
    for l in &f.locals {
        let from = l.from_def.map(|d| f.defs[d].qualname.clone()).unwrap_or_default();
        println!(
            "L {} : {} declared={} field={} in={}",
            l.name,
            l.path.join("."),
            l.declared,
            l.field,
            from
        );
    }
    for r in &f.refs {
        let from = r.from_def.map(|d| f.defs[d].qualname.clone()).unwrap_or_default();
        println!(
            "R L{} {:?} {} [{}] from={} owner={:?}",
            r.line,
            r.kind,
            r.path.join("."),
            r.context,
            from,
            r.from_owner
        );
    }
}
