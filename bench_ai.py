#!/usr/bin/env python3
"""
bench_ai.py — Benchmark the SIR AI translation pipeline.

Tests:
  1. Function scan on non-Python files (Java demo files)
  2. Class scan on non-Python files (Java demo files)
  3. Hash stability — translate the same function twice, check if hashes match

Usage:
  python3 bench_ai.py [--backend ollama|anthropic] [--model codellama:7b]
"""

from __future__ import annotations

import argparse
import time
from collections import defaultdict
from pathlib import Path

from sir_ai_translate import (
    extract_raw_classes,
    extract_raw_functions,
    translate_class_to_python,
    translate_to_python,
    detect_language,
    check_ollama,
)
from sir2_core import extract_classes, scan_for_class_dupes
from sir.core import hash_source

# ─────────────────────────────────────────────
#  Config
# ─────────────────────────────────────────────

DEMO_DIR = Path(__file__).parent / "demo_scan"
JAVA_FILES = sorted(DEMO_DIR.glob("*.java"))

# Known ground-truth duplicate pairs for accuracy scoring
# (file_a, class_a, file_b, class_b)
KNOWN_CLASS_DUPES = [
    ("MathUtils.java", "MathUtils", "ArithmeticHelper.java", "ArithmeticHelper"),
]

# (file_a, fn_a, file_b, fn_b)
KNOWN_FUNCTION_DUPES = [
    ("MathUtils.java", "add",            "ArithmeticHelper.java", "add"),
    ("MathUtils.java", "multiply",       "ArithmeticHelper.java", "multiply"),
    ("MathUtils.java", "isPositive",     "ArithmeticHelper.java", "isPositive"),
    ("MathUtils.java", "computeSum",     "ArithmeticHelper.java", "addValues"),
    ("MathUtils.java", "computeProduct", "ArithmeticHelper.java", "multiplyValues"),
]

SEP = "─" * 60


def fmt_conf(conf: str) -> str:
    icons = {"HIGH": "🟢 HIGH", "MEDIUM": "🟡 MEDIUM", "LOW": "🔴 LOW", "FAILED": "❌ FAILED"}
    return icons.get(conf, conf)


def run_benchmark(backend: str, model: str, host: str) -> None:
    kw = dict(backend=backend, ollama_model=model, ollama_host=host, use_cache=False)

    print(SEP)
    print(f"  SIR AI Benchmark — backend={backend}  model={model}")
    print(SEP)
    print(f"  Files: {[f.name for f in JAVA_FILES]}")
    print()

    # ── Load sources ──────────────────────────────────────────────────
    file_sources: dict[str, str] = {}
    for f in JAVA_FILES:
        file_sources[f.name] = f.read_text()

    # ═══════════════════════════════════════════════════════════════
    #  1. CLASS SCAN
    # ═══════════════════════════════════════════════════════════════
    print("[ 1 / 3 ]  Class scan (V2)")
    print(SEP)

    all_classes = []
    class_results = []  # (fname, cls_name, confidence, elapsed)
    translate_errors = 0

    for fname, src in file_sources.items():
        lang = detect_language(fname) or "Unknown"
        raw_classes = extract_raw_classes(src, lang)
        for cls_name, cls_lineno, cls_src in raw_classes:
            t0 = time.time()
            tr = translate_class_to_python(cls_src, lang, **kw)
            elapsed = time.time() - t0
            class_results.append((fname, cls_name, tr["confidence"], round(elapsed, 2)))
            if tr["confidence"] == "FAILED":
                translate_errors += 1
                print(f"  ❌  {cls_name} ({fname}): {tr['error']}")
                continue
            translated = extract_classes(tr["python_src"], fname)
            for cls in translated:
                cls.ai_translated = True
                cls.original_language = lang
                cls.lineno = cls_lineno
            all_classes.extend(translated)

    print(f"  Translations: {len(class_results)} attempted, {translate_errors} failed")
    for fname, cls_name, conf, elapsed in class_results:
        print(f"  {fmt_conf(conf):12s}  {cls_name:25s} ({fname})  {elapsed}s")

    exact_clusters, similar_pairs = scan_for_class_dupes(all_classes, min_similarity=0.5)

    print(f"\n  Exact duplicate clusters : {len(exact_clusters)}")
    for cluster in exact_clusters:
        names = "  ==  ".join(f"{c.name} ({c.file})" for c in cluster.members)
        print(f"    🔴  {names}")

    print(f"  Similar pairs (≥50%)     : {len(similar_pairs)}")
    for pair in similar_pairs:
        print(f"    🟡  {pair.similarity:.0%}  {pair.class_a.name} vs {pair.class_b.name}")

    # Accuracy vs ground truth
    detected_class_dupes = set()
    for cluster in exact_clusters:
        for i, a in enumerate(cluster.members):
            for b in cluster.members[i + 1:]:
                pair_key = tuple(sorted([(a.file, a.name), (b.file, b.name)]))
                detected_class_dupes.add(pair_key)

    gt_class = set(
        tuple(sorted([(fa, ca), (fb, cb)]))
        for fa, ca, fb, cb in KNOWN_CLASS_DUPES
    )
    tp = len(detected_class_dupes & gt_class)
    fp = len(detected_class_dupes - gt_class)
    fn = len(gt_class - detected_class_dupes)
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall    = tp / (tp + fn) if (tp + fn) else 1.0
    print(f"\n  Ground truth: {len(gt_class)} known duplicate pair(s)")
    print(f"  Precision: {precision:.0%}   Recall: {recall:.0%}   TP={tp} FP={fp} FN={fn}")

    # ═══════════════════════════════════════════════════════════════
    #  2. FUNCTION SCAN
    # ═══════════════════════════════════════════════════════════════
    print()
    print("[ 2 / 3 ]  Function scan")
    print(SEP)

    hash_to_funcs: dict[str, list] = defaultdict(list)
    fn_results = []  # (fname, fn_name, confidence, elapsed)
    fn_errors = 0

    for fname, src in file_sources.items():
        lang = detect_language(fname) or "Unknown"
        raw_funcs = extract_raw_functions(src, lang)
        # Skip constructors (capitalized names matching class name)
        raw_funcs = [(n, l, r) for n, l, r in raw_funcs if not n[0].isupper()]
        for fn_name, lineno, fn_src in raw_funcs:
            t0 = time.time()
            tr = translate_to_python(fn_src, lang, **kw)
            elapsed = time.time() - t0
            fn_results.append((fname, fn_name, tr["confidence"], round(elapsed, 2)))
            if tr["confidence"] == "FAILED":
                fn_errors += 1
                print(f"  ❌  {fn_name} ({fname}): {tr.get('error', '')}")
                continue
            h = hash_source(tr["python_src"], mode="semantic")
            hash_to_funcs[h].append({"name": fn_name, "file": fname})

    print(f"  Translations: {len(fn_results)} attempted, {fn_errors} failed")
    for fname, fn_name, conf, elapsed in fn_results:
        print(f"  {fmt_conf(conf):12s}  {fn_name:25s} ({fname})  {elapsed}s")

    dupe_clusters = {h: fns for h, fns in hash_to_funcs.items() if len(fns) > 1}
    print(f"\n  Duplicate function clusters: {len(dupe_clusters)}")
    for h, fns in dupe_clusters.items():
        names = "  ==  ".join(f"{f['name']} ({f['file']})" for f in fns)
        print(f"    🔴  {names}")

    # Accuracy
    detected_fn_dupes = set()
    for fns in dupe_clusters.values():
        for i, a in enumerate(fns):
            for b in fns[i + 1:]:
                detected_fn_dupes.add(tuple(sorted([(a["file"], a["name"]), (b["file"], b["name"])])))

    gt_fn = set(
        tuple(sorted([(fa, na), (fb, nb)]))
        for fa, na, fb, nb in KNOWN_FUNCTION_DUPES
    )
    tp = len(detected_fn_dupes & gt_fn)
    fp = len(detected_fn_dupes - gt_fn)
    fn_miss = len(gt_fn - detected_fn_dupes)
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall    = tp / (tp + fn_miss) if (tp + fn_miss) else 1.0
    print(f"\n  Ground truth: {len(gt_fn)} known duplicate pair(s)")
    print(f"  Precision: {precision:.0%}   Recall: {recall:.0%}   TP={tp} FP={fp} FN={fn_miss}")

    # ═══════════════════════════════════════════════════════════════
    #  3. HASH STABILITY (translate same function twice, compare)
    # ═══════════════════════════════════════════════════════════════
    print()
    print("[ 3 / 3 ]  Hash stability (double-translate each function)")
    print(SEP)

    stable = 0
    unstable = 0
    for fname, src in file_sources.items():
        lang = detect_language(fname) or "Unknown"
        raw_funcs = extract_raw_functions(src, lang)
        raw_funcs = [(n, l, r) for n, l, r in raw_funcs if not n[0].isupper()]
        for fn_name, _, fn_src in raw_funcs:
            tr1 = translate_to_python(fn_src, lang, confidence_check=False, use_cache=False, **{k: v for k, v in kw.items() if k != 'use_cache'})
            tr2 = translate_to_python(fn_src, lang, confidence_check=False, use_cache=False, **{k: v for k, v in kw.items() if k != 'use_cache'})
            if tr1["confidence"] == "FAILED" or tr2["confidence"] == "FAILED":
                continue
            h1 = hash_source(tr1["python_src"], mode="semantic")
            h2 = hash_source(tr2["python_src"], mode="semantic")
            if h1 == h2:
                stable += 1
                status = "stable  "
            else:
                unstable += 1
                status = "UNSTABLE"
            print(f"  {status}  {fn_name:25s} ({fname})")

    total_stability = stable + unstable
    pct = round(stable / total_stability * 100) if total_stability else 0
    print(f"\n  Stable: {stable}/{total_stability} ({pct}%)")

    # ── Summary ─────────────────────────────────────────────────────
    print()
    print(SEP)
    print("  SUMMARY")
    print(SEP)
    all_confs = [r[2] for r in class_results + fn_results]
    for c in ["HIGH", "MEDIUM", "LOW", "FAILED"]:
        n = all_confs.count(c)
        if n:
            print(f"  {fmt_conf(c):14s}  {n} translation(s)")
    print(f"  Hash stability          : {pct}%")
    print(SEP)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", default="ollama", choices=["ollama", "anthropic"])
    parser.add_argument("--model", default="codellama:7b")
    parser.add_argument("--host", default="http://localhost:11434")
    args = parser.parse_args()

    if args.backend == "ollama" and not check_ollama(args.host):
        print("ERROR: Ollama not running. Start it with: ollama serve")
        raise SystemExit(1)

    run_benchmark(backend=args.backend, model=args.model, host=args.host)
