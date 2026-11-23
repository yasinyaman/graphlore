//! The search kernel against graphlore's golden contract
//! (`tests/fixtures/search_golden.json`): the terms `search.py`'s `_PyKernel`
//! gives, and its BM25F scores to the bit (`float.hex` there).

use std::path::PathBuf;

use graphlore_engine::search::{bm25f, tokenize, Params, Postings, KERNEL_ABI};
use serde_json::Value;

fn golden() -> Value {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("tests")
        .join("fixtures")
        .join("search_golden.json");
    serde_json::from_str(&std::fs::read_to_string(&path).expect("the golden file")).unwrap()
}

/// Python's `float.hex` of a normal number (`0x1.0c777bae0a5e5p+0`), read back.
fn from_hex(s: &str) -> f64 {
    let (sign, s) = s.strip_prefix('-').map_or((1.0, s), |r| (-1.0, r));
    let s = s.strip_prefix("0x").expect("0x");
    let (mant, exp) = s.split_once('p').expect("p");
    let (int, frac) = mant.split_once('.').unwrap_or((mant, ""));
    let mut v = u64::from_str_radix(int, 16).unwrap() as f64;
    let mut scale = 1.0 / 16.0;
    for c in frac.chars() {
        v += c.to_digit(16).unwrap() as f64 * scale;
        scale /= 16.0;
    }
    sign * v * 2f64.powi(exp.parse::<i32>().unwrap())
}

#[test]
fn the_kernel_meets_the_golden_contract() {
    let g = golden();
    assert_eq!(g["kernel_abi"].as_u64(), Some(u64::from(KERNEL_ABI)));
    for case in g["tokenize"].as_array().unwrap() {
        let text = case["text"].as_str().unwrap();
        let want: Vec<String> =
            case["terms"].as_array().unwrap().iter().map(|t| t.as_str().unwrap().into()).collect();
        assert_eq!(tokenize(text), want, "tokenize({text:?})");
    }
    let b = &g["bm25f"];
    let triple = |v: &Value| -> [i64; 3] {
        let a = v.as_array().unwrap();
        [a[0].as_i64().unwrap(), a[1].as_i64().unwrap(), a[2].as_i64().unwrap()]
    };
    let terms: Vec<(f64, Postings)> = b["terms"]
        .as_array()
        .unwrap()
        .iter()
        .map(|t| {
            let postings = t[1]
                .as_object()
                .unwrap()
                .iter()
                .map(|(c, v)| (c.parse::<i64>().unwrap(), triple(v)))
                .collect();
            (t[0].as_f64().unwrap(), postings)
        })
        .collect();
    let lengths = b["lengths"].as_object().unwrap();
    let avg: Vec<f64> = b["avg"].as_array().unwrap().iter().map(|x| x.as_f64().unwrap()).collect();
    let params: Vec<f64> =
        b["params"].as_array().unwrap().iter().map(|x| x.as_f64().unwrap()).collect();
    let got = bm25f(
        &terms,
        |cid| triple(&lengths[&cid.to_string()]),
        [avg[0], avg[1], avg[2]],
        &Params::from_slice(&params).unwrap(),
    );
    let want = b["expected"].as_object().unwrap();
    assert_eq!(got.len(), want.len());
    for (cid, score, matched) in got {
        let w = &want[&cid.to_string()];
        let expected = from_hex(w[0].as_str().unwrap());
        assert_eq!(score.to_bits(), expected.to_bits(), "chunk {cid}: {score} != {expected}");
        assert_eq!(u64::from(matched), w[1].as_u64().unwrap(), "chunk {cid}");
    }
}
