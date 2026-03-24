"""
sir_semantic.py — Two-pass semantic duplicate detection.

Pass 1 (SIR):  Structural / alpha-equivalence hashing. Fast, deterministic, free.
               Catches: same logic with different variable/function names.

Pass 2 (AI):   Semantic equivalence check on whatever SIR missed.
               Catches: x+x vs x*2, different algorithms with the same behaviour,
               rewritten control flow, etc.

Usage:
    from sir_semantic import semantic_scan

    results = semantic_scan(
        file_sources,           # {filename: source_code}
        backend="ollama",
        api_key="",
        ollama_model="codellama:7b",
        ollama_host="http://localhost:11434",
    )
    results.sir_duplicates      # list of SIR exact clusters
    results.semantic_duplicates # list of AI-confirmed semantic pairs
    results.candidate_pairs     # pairs sent to AI (for inspection)
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))
from sir.core import hash_source
from sir_ai_translate import call_ollama, call_anthropic


# ─────────────────────────────────────────────
#  Data structures
# ─────────────────────────────────────────────

@dataclass
class FuncInfo:
    file:       str
    name:       str
    lineno:     int
    end_lineno: int
    source:     str
    sir_hash:   str
    line_count: int
    param_count: int


@dataclass
class SIRCluster:
    """Group of functions that are structurally identical (SIR exact match)."""
    sir_hash: str
    members:  List[FuncInfo]


@dataclass
class SemanticPair:
    """Two functions that the AI says are semantically equivalent."""
    func_a:     FuncInfo
    func_b:     FuncInfo
    confidence: str          # HIGH / MEDIUM / LOW
    reason:     str          # one-line explanation from the AI
    ai_verdict: str          # raw EQUIVALENT / NOT_EQUIVALENT / UNCERTAIN


@dataclass
class SemanticScanResult:
    sir_duplicates:      List[SIRCluster]
    semantic_duplicates: List[SemanticPair]
    candidate_pairs:     int   # how many pairs were sent to AI
    total_functions:     int
    skipped_trivial:     int   # functions too short to analyse


# ─────────────────────────────────────────────
#  Cache — avoid re-asking the AI about the same pair
# ─────────────────────────────────────────────

_SEMANTIC_CACHE_DIR = Path(".sir_cache") / "semantic"

def _pair_cache_key(hash_a: str, hash_b: str) -> str:
    ordered = sorted([hash_a, hash_b])
    return hashlib.sha256(("||".join(ordered)).encode()).hexdigest()

def _cache_get(hash_a: str, hash_b: str) -> Optional[dict]:
    _SEMANTIC_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    f = _SEMANTIC_CACHE_DIR / f"{_pair_cache_key(hash_a, hash_b)}.json"
    if f.exists():
        try:
            return json.loads(f.read_text())
        except Exception:
            pass
    return None

def _cache_set(hash_a: str, hash_b: str, entry: dict) -> None:
    _SEMANTIC_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    f = _SEMANTIC_CACHE_DIR / f"{_pair_cache_key(hash_a, hash_b)}.json"
    f.write_text(json.dumps(entry))


# ─────────────────────────────────────────────
#  Pass 1 — SIR structural scan
# ─────────────────────────────────────────────

def _extract_functions(src: str, filename: str) -> List[FuncInfo]:
    """Extract all top-level functions from a Python source file."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    results = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            seg = ast.get_source_segment(src, node)
            if not seg:
                continue
            try:
                sir_hash = hash_source(seg, mode="semantic")
            except Exception:
                continue
            results.append(FuncInfo(
                file=filename,
                name=node.name,
                lineno=node.lineno,
                end_lineno=node.end_lineno,
                source=seg,
                sir_hash=sir_hash,
                line_count=node.end_lineno - node.lineno + 1,
                param_count=len(node.args.args),
            ))
    return results


def _sir_pass(file_sources: Dict[str, str]) -> Tuple[List[SIRCluster], List[FuncInfo]]:
    """
    Run the SIR structural scan.

    Returns:
        (clusters, unflagged_functions)
        clusters           — groups of structurally identical functions
        unflagged_functions — functions NOT in any cluster (candidates for AI pass)
    """
    all_funcs: List[FuncInfo] = []
    for fname, src in file_sources.items():
        if Path(fname).suffix == ".py":
            all_funcs.extend(_extract_functions(src, fname))

    groups: Dict[str, List[FuncInfo]] = defaultdict(list)
    for fn in all_funcs:
        groups[fn.sir_hash].append(fn)

    flagged_hashes = set()
    clusters: List[SIRCluster] = []
    for h, members in groups.items():
        if len(members) >= 2:
            clusters.append(SIRCluster(sir_hash=h, members=members))
            flagged_hashes.add(h)

    unflagged = [fn for fn in all_funcs if fn.sir_hash not in flagged_hashes]
    return clusters, unflagged


# ─────────────────────────────────────────────
#  Pass 2 — AI semantic check
# ─────────────────────────────────────────────

MIN_LINES      = 2    # skip trivial one-liners (too many false positives)
MAX_LINE_RATIO = 4.0  # don't compare a 2-line func to a 30-line one


def _build_candidate_pairs(funcs: List[FuncInfo]) -> List[Tuple[FuncInfo, FuncInfo]]:
    """
    Build pairs of functions to send to the AI.

    Pre-filters (reduce O(n²) to a manageable set):
      - Both functions must have >= MIN_LINES lines
      - Same parameter count
      - Line counts within MAX_LINE_RATIO of each other
      - Not the same function (same file + name)
    """
    candidates = []
    eligible = [fn for fn in funcs if fn.line_count >= MIN_LINES]

    for i, a in enumerate(eligible):
        for b in eligible[i + 1:]:
            if a.file == b.file and a.name == b.name:
                continue
            if a.param_count != b.param_count:
                continue
            ratio = max(a.line_count, b.line_count) / max(min(a.line_count, b.line_count), 1)
            if ratio > MAX_LINE_RATIO:
                continue
            candidates.append((a, b))

    return candidates


_SEMANTIC_PROMPT = """\
You are a code equivalence checker for a semantic duplicate detection tool.

Determine whether these two Python functions ALWAYS compute the same result \
for the same inputs. Focus on behaviour, not style.

Treat these as semantically equivalent:
- x + x  and  x * 2
- if x > 0: return True; else: return False  and  return x > 0
- a loop that builds a sum  and  sum(items)
- different but equivalent control flow

Do NOT treat as equivalent:
- Functions with different numbers of parameters
- Functions that differ by a constant (e.g. x*2 vs x*3)
- Functions that handle edge cases differently

Function A ({name_a} in {file_a}):
```python
{source_a}
```

Function B ({name_b} in {file_b}):
```python
{source_b}
```

Reply with EXACTLY one of these three lines (nothing else):
EQUIVALENT: <one sentence reason>
NOT_EQUIVALENT: <one sentence reason>
UNCERTAIN: <one sentence reason>"""


def _ask_ai(
    func_a: FuncInfo,
    func_b: FuncInfo,
    backend: str,
    api_key: str,
    ollama_model: str,
    ollama_host: str,
) -> dict:
    """Ask the AI if two functions are semantically equivalent. Returns cached result if available."""
    cached = _cache_get(func_a.sir_hash, func_b.sir_hash)
    if cached:
        cached["cache_hit"] = True
        return cached

    prompt = _SEMANTIC_PROMPT.format(
        name_a=func_a.name, file_a=func_a.file,
        name_b=func_b.name, file_b=func_b.file,
        source_a=func_a.source,
        source_b=func_b.source,
    )

    try:
        if backend == "anthropic":
            raw = call_anthropic(prompt, api_key)
        else:
            raw = call_ollama(prompt, model=ollama_model, host=ollama_host)
    except Exception as e:
        return {"verdict": "UNCERTAIN", "reason": f"AI call failed: {e}", "cache_hit": False}

    # Parse the one-line response
    raw = raw.strip()
    verdict, reason = "UNCERTAIN", raw
    for keyword in ("EQUIVALENT", "NOT_EQUIVALENT", "UNCERTAIN"):
        if raw.upper().startswith(keyword):
            verdict = keyword
            reason  = raw[len(keyword):].lstrip(": ").strip()
            break

    # Confidence: if it answered clearly → HIGH, UNCERTAIN → LOW
    confidence = "HIGH" if verdict == "EQUIVALENT" else \
                 "LOW"  if verdict == "UNCERTAIN"  else "MEDIUM"

    entry = {"verdict": verdict, "reason": reason,
             "confidence": confidence, "cache_hit": False}
    _cache_set(func_a.sir_hash, func_b.sir_hash, entry)
    return entry


# ─────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────

def semantic_scan(
    file_sources: Dict[str, str],
    backend: str = "ollama",
    api_key: str = "",
    ollama_model: str = "codellama:7b",
    ollama_host: str = "http://localhost:11434",
    min_confidence: str = "MEDIUM",  # MEDIUM = include HIGH+MEDIUM, HIGH = only HIGH
    progress_cb=None,                # optional callable(current, total, pair_label)
) -> SemanticScanResult:
    """
    Two-pass semantic duplicate scan.

    Pass 1: SIR structural scan (instant).
    Pass 2: AI semantic equivalence check on the remaining functions.

    Args:
        file_sources:    {filename: source_code} — .py files only for now
        backend:         "ollama" or "anthropic"
        api_key:         Anthropic API key (if backend == "anthropic")
        ollama_model:    Ollama model name
        ollama_host:     Ollama host URL
        min_confidence:  Minimum confidence to include in results
                         "HIGH"   — only include pairs the AI is certain about
                         "MEDIUM" — include HIGH and MEDIUM (default)
        progress_cb:     Optional callback(current, total, label) for progress updates

    Returns:
        SemanticScanResult
    """
    # ── Pass 1: SIR ──────────────────────────────────────────────────────────
    sir_clusters, unflagged = _sir_pass(file_sources)

    skipped = sum(1 for fn in unflagged if fn.line_count < MIN_LINES)
    total   = sum(
        len(fn_list)
        for fn_list in [
            *[c.members for c in sir_clusters],
            unflagged,
        ]
    )

    # ── Pass 2: Build candidate pairs ────────────────────────────────────────
    pairs = _build_candidate_pairs(unflagged)

    # ── Pass 2: Ask AI about each pair ───────────────────────────────────────
    semantic_dupes: List[SemanticPair] = []
    confidence_order = {"HIGH": 2, "MEDIUM": 1, "LOW": 0}
    min_conf_value   = confidence_order.get(min_confidence, 1)

    for i, (a, b) in enumerate(pairs):
        if progress_cb:
            progress_cb(i + 1, len(pairs), f"{a.name} ({a.file}) vs {b.name} ({b.file})")

        result = _ask_ai(a, b, backend, api_key, ollama_model, ollama_host)
        verdict    = result["verdict"]
        confidence = result["confidence"]

        if verdict == "EQUIVALENT" and confidence_order.get(confidence, 0) >= min_conf_value:
            semantic_dupes.append(SemanticPair(
                func_a=a, func_b=b,
                confidence=confidence,
                reason=result["reason"],
                ai_verdict=verdict,
            ))

    return SemanticScanResult(
        sir_duplicates=sir_clusters,
        semantic_duplicates=semantic_dupes,
        candidate_pairs=len(pairs),
        total_functions=total,
        skipped_trivial=skipped,
    )
