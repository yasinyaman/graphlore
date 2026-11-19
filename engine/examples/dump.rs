//! `cargo run --example dump -- <root>`: build a graph without writing it and
//! print its nodes and links, one per line.
fn main() {
    let root = std::env::args().nth(1).unwrap_or_else(|| ".".into());
    let (graph, report) =
        graphlore_engine::build_graph(&graphlore_engine::BuildOptions::new(root)).expect("build");
    for n in graph["nodes"].as_array().unwrap() {
        println!(
            "N {} | {} | {} {} {}",
            n["id"], n["label"], n["source_file"], n["source_location"], n["kind"]
        );
    }
    for e in graph["links"].as_array().unwrap() {
        println!("E {} -{}-> {}", e["source"], e["relation"], e["target"]);
    }
    eprintln!("{}", serde_json::to_string_pretty(&report).unwrap());
}
