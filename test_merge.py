#!/usr/bin/env python3
"""
End-to-end test of the SIR merge pipeline.

Simulates what the Streamlit UI does:
  1. Scan uploaded files → Occur objects + func_code_map
  2. Auto merge → remove duplicates, rename call sites, add imports
  3. Update test files — rename calls + update imports, no removal
  4. Write output to a temp dir
  5. Execute the merged code + pytest to confirm correctness
"""

import ast
import re
import sys
import tempfile
import textwrap
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).parent))
from sir.core import hash_source


# ── Test fixtures ─────────────────────────────────────────────────────────────

# a.py: defines calculate_tax (the canonical copy) and uses it
A_SRC = textwrap.dedent("""\
    def calculate_tax(amount, rate):
        if amount <= 0:
            return 0
        return amount * rate

    def invoice_total(subtotal):
        tax = calculate_tax(subtotal, 0.1)
        return subtotal + tax
""")

# b.py: defines compute_tax (duplicate of calculate_tax) and calls it
B_SRC = textwrap.dedent("""\
    def compute_tax(amount, rate):
        if amount <= 0:
            return 0
        return amount * rate

    def order_total(price):
        tax = compute_tax(price, 0.2)
        return price + tax
""")

# c.py: a third file that also duplicates the function under a third name
C_SRC = textwrap.dedent("""\
    def get_tax(amount, rate):
        if amount <= 0:
            return 0
        return amount * rate

    def cart_total(price):
        result = get_tax(price, 0.15)
        return price + result
""")

# test_taxes.py: pytest tests calling all three duplicate functions
TEST_SRC = textwrap.dedent("""\
    from a import calculate_tax
    from b import compute_tax
    from c import get_tax

    def test_calculate_tax():
        assert calculate_tax(100, 0.1) == 10.0

    def test_compute_tax():
        assert compute_tax(100, 0.1) == 10.0

    def test_get_tax():
        assert get_tax(100, 0.1) == 10.0

    def test_zero_amount():
        assert compute_tax(0, 0.5) == 0
        assert get_tax(-5, 0.5) == 0
""")

# main.py: imports and calls all three wrappers; should still work after merge
MAIN_SRC = textwrap.dedent("""\
    import sys, os
    sys.path.insert(0, os.path.dirname(__file__))

    from a import invoice_total
    from b import order_total
    from c import cart_total

    r1 = invoice_total(100)
    r2 = order_total(200)
    r3 = cart_total(300)

    assert abs(r1 - 110.0) < 1e-9,  f"invoice_total failed: {r1}"
    assert abs(r2 - 240.0) < 1e-9,  f"order_total failed: {r2}"
    assert abs(r3 - 345.0) < 1e-9,  f"cart_total failed: {r3}"
    print(f"invoice_total(100) = {r1}")
    print(f"order_total(200)   = {r2}")
    print(f"cart_total(300)    = {r3}")
    print("ALL ASSERTIONS PASSED")
""")


# ── Helpers matching the UI logic exactly ─────────────────────────────────────

@dataclass
class Occur:
    file: str
    qualname: str
    lineno: int
    semantic_hash: str


def extract_functions(src: str, filename: str) -> List[Tuple[str, int, str]]:
    tree = ast.parse(src)
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            seg = ast.get_source_segment(src, node)
            if seg:
                out.append((node.name, node.lineno, seg))
    return out


def run_scan(file_sources: Dict[str, str]):
    """Replicate the UI scan: return (groups, func_code_map)."""
    groups = defaultdict(list)
    func_code_map = {}
    for fname, src in file_sources.items():
        for qualname, lineno, code in extract_functions(src, fname):
            func_code_map[f"{fname}::{qualname}"] = code
            try:
                h = hash_source(code, mode="semantic")
                groups[h].append(Occur(file=fname, qualname=qualname, lineno=lineno, semantic_hash=h))
            except Exception as e:
                print(f"  WARNING: could not hash {fname}::{qualname}: {e}")
    return groups, func_code_map


def rename_calls_with_comment(src: str, dup_name: str, canon_name: str, timestamp: str) -> str:
    pattern = re.compile(rf'\b{re.escape(dup_name)}\s*\(')
    comment = f"  # SIR: was {dup_name}() — merged {timestamp}"
    lines = src.splitlines()
    result = []
    for line in lines:
        if pattern.search(line):
            line = pattern.sub(f'{canon_name}(', line)
            line = line.rstrip() + comment
        result.append(line)
    return '\n'.join(result)


def update_test_file(src: str, dup_name: str, canon_name: str, canon_module: str, timestamp: str) -> str:
    """Update a test file: rename import lines and call sites for a removed duplicate."""
    canonical_import = f"from {canon_module} import {canon_name}"
    already_imported = canonical_import in src
    call_pattern = re.compile(rf'\b{re.escape(dup_name)}\s*\(')
    import_pattern = re.compile(
        rf'^(\s*from\s+\S+\s+import\s+(?:.*,\s*)?){re.escape(dup_name)}(\s*(?:,.*|#.*)?$)'
    )
    lines = src.splitlines()
    result = []
    for line in lines:
        m = import_pattern.match(line)
        if m:
            if already_imported:
                line = f"# SIR: {dup_name} merged into {canon_name} (from {canon_module}) — {timestamp}"
            else:
                line = f"{canonical_import}  # SIR: was {dup_name} — merged {timestamp}"
                already_imported = True
        elif call_pattern.search(line):
            line = call_pattern.sub(f'{canon_name}(', line)
            line = line.rstrip() + f"  # SIR: was {dup_name}() — merged {timestamp}"
        result.append(line)
    return '\n'.join(result)


def run_merge(scan_sources, dupes, func_code_map, test_sources=None):
    """Replicate the UI auto-merge logic exactly."""
    import ast as _ast

    modified = dict(scan_sources)
    removed_count = 0

    # Step 1: remove duplicate function bodies (bottom-up per file)
    removals_by_file = {}
    for h, occs in dupes.items():
        for occ in occs[1:]:
            removals_by_file.setdefault(occ.file, []).append(occ)

    for fname, occ_list in removals_by_file.items():
        if fname not in modified:
            continue
        occ_list.sort(key=lambda o: o.lineno, reverse=True)
        for occ in occ_list:
            try:
                src = modified[fname]
                tree = _ast.parse(src)
                lines = src.splitlines()
                for node in _ast.walk(tree):
                    if (isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef))
                            and node.name == occ.qualname.split(".")[-1]):
                        new_lines = lines[:node.lineno - 1] + lines[node.end_lineno:]
                        modified[fname] = "\n".join(new_lines)
                        removed_count += 1
                        break
            except Exception:
                pass

    # Step 2: rename call sites and add imports
    merge_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    utils_functions = {}
    for h, occs in dupes.items():
        canon_occ = occs[0]
        canon_name = canon_occ.qualname.split(".")[-1]
        canon_module = canon_occ.file[:-3] if canon_occ.file.endswith(".py") else canon_occ.file
        code_key = f"{canon_occ.file}::{canon_occ.qualname}"
        if code_key in func_code_map:
            utils_functions[canon_name] = func_code_map[code_key]
        for occ in occs[1:]:
            dup_name = occ.qualname.split(".")[-1]
            fname = occ.file
            if fname not in modified:
                continue
            src = modified[fname]
            if dup_name != canon_name:
                src = rename_calls_with_comment(src, dup_name, canon_name, merge_ts)
            import_line = f"from {canon_module} import {canon_name}"
            if import_line not in src and f"def {canon_name}" not in src:
                src = import_line + "\n" + src
            modified[fname] = src

    # Step 3: utils.py
    utils_py = "# utils.py — canonical functions extracted by SIR Engine\n\n"
    for name, code in utils_functions.items():
        utils_py += code + "\n\n"

    # Step 4: update test files
    updated_tests = {}
    for tfname, tsrc in (test_sources or {}).items():
        for h, occs in dupes.items():
            canon_occ = occs[0]
            canon_name = canon_occ.qualname.split(".")[-1]
            canon_module = canon_occ.file[:-3] if canon_occ.file.endswith(".py") else canon_occ.file
            for occ in occs[1:]:
                dup_name = occ.qualname.split(".")[-1]
                if dup_name != canon_name:
                    tsrc = update_test_file(tsrc, dup_name, canon_name, canon_module, merge_ts)
        updated_tests[tfname] = tsrc

    return modified, utils_py, removed_count, updated_tests


# ── Assertions ────────────────────────────────────────────────────────────────

def assert_no_dup_function(src: str, dup_name: str, file_label: str):
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == dup_name:
            raise AssertionError(f"FAIL: {file_label} still defines '{dup_name}' — should have been removed")
    print(f"  ✓  '{dup_name}' removed from {file_label}")


def assert_import_present(src: str, import_line: str, file_label: str):
    if import_line not in src:
        raise AssertionError(f"FAIL: {file_label} missing import '{import_line}'")
    print(f"  ✓  import '{import_line}' present in {file_label}")


def assert_no_call(src: str, call_name: str, file_label: str):
    # Check no live calls to the old name remain — ignore occurrences inside comments
    pattern = re.compile(rf'\b{re.escape(call_name)}\s*\(')
    for line in src.splitlines():
        code_part = line.split('#')[0]  # strip comment
        if pattern.search(code_part):
            raise AssertionError(f"FAIL: {file_label} still has a live call to '{call_name}': {line.strip()!r}")
    print(f"  ✓  no live calls to '{call_name}' remain in {file_label}")


def assert_call_renamed(src: str, new_name: str, old_name: str, file_label: str):
    if not re.search(rf'\b{re.escape(new_name)}\s*\(', src):
        raise AssertionError(f"FAIL: {file_label} has no call to '{new_name}' — call site not renamed")
    print(f"  ✓  calls renamed to '{new_name}' in {file_label}")
    # Check the SIR comment is present on renamed lines
    for line in src.splitlines():
        if re.search(rf'\b{re.escape(new_name)}\s*\(', line):
            if f"# SIR: was {old_name}()" not in line:
                raise AssertionError(f"FAIL: {file_label} renamed call missing SIR comment: {line.strip()!r}")
    print(f"  ✓  SIR comment present on renamed call sites in {file_label}")


# ── Main test ─────────────────────────────────────────────────────────────────

def main():
    PASS = True

    print("=" * 60)
    print("SIR Merge End-to-End Test")
    print("=" * 60)

    file_sources = {"a.py": A_SRC, "b.py": B_SRC, "c.py": C_SRC}

    # ── 1. Scan ──────────────────────────────────────────────────────
    print("\n[1] Scanning for duplicates...")
    groups, func_code_map = run_scan(file_sources)
    dupes = {h: occs for h, occs in groups.items() if len(occs) >= 2}

    print(f"    Functions found:   {sum(len(v) for v in groups.values())}")
    print(f"    Unique structures: {len(groups)}")
    print(f"    Duplicate clusters: {len(dupes)}")

    if len(dupes) != 1:
        print(f"FAIL: expected 1 duplicate cluster, got {len(dupes)}")
        return False

    h, occs = next(iter(dupes.items()))
    print(f"    Cluster: {[f'{o.qualname} in {o.file}' for o in occs]}")
    # Canonical is the first occurrence
    canon = occs[0]
    dup1 = occs[1]
    dup2 = occs[2]

    # ── 2. Merge ─────────────────────────────────────────────────────
    print("\n[2] Running merge...")
    test_sources = {"test_taxes.py": TEST_SRC}
    modified, utils_py, removed_count, updated_tests = run_merge(
        file_sources, dupes, func_code_map, test_sources
    )
    print(f"    Removed {removed_count} duplicate function(s)")
    print(f"    Updated {len(updated_tests)} test file(s)")

    # ── 3. Static checks ─────────────────────────────────────────────
    print("\n[3] Static checks on merged output...")
    try:
        # canonical file (a.py) should be unchanged
        assert modified["a.py"] == A_SRC, "a.py should be unchanged (it owns the canonical)"
        print("  ✓  a.py is unchanged")

        # b.py: compute_tax removed, calls renamed, import added
        assert_no_dup_function(modified["b.py"], dup1.qualname, "b.py")
        assert_no_call(modified["b.py"], dup1.qualname, "b.py")
        assert_call_renamed(modified["b.py"], canon.qualname, dup1.qualname, "b.py")
        assert_import_present(modified["b.py"], f"from a import {canon.qualname}", "b.py")

        # c.py: get_tax removed, calls renamed, import added
        assert_no_dup_function(modified["c.py"], dup2.qualname, "c.py")
        assert_no_call(modified["c.py"], dup2.qualname, "c.py")
        assert_call_renamed(modified["c.py"], canon.qualname, dup2.qualname, "c.py")
        assert_import_present(modified["c.py"], f"from a import {canon.qualname}", "c.py")

        # utils.py contains canonical function
        assert canon.qualname in utils_py, "utils.py missing canonical function"
        print(f"  ✓  utils.py contains '{canon.qualname}'")

        # All modified files parse cleanly
        for fname, src in modified.items():
            ast.parse(src)
        print("  ✓  all modified files parse without SyntaxError")

    except AssertionError as e:
        print(f"\n{e}")
        PASS = False

    # ── 3b. Test file static checks ───────────────────────────────────
    print("\n[3b] Static checks on updated test files...")
    try:
        t = updated_tests["test_taxes.py"]
        # imports of dup functions should be gone
        assert "from b import compute_tax" not in t, "old import still present in test"
        assert "from c import get_tax" not in t, "old import still present in test"
        # canonical imports should be present
        assert "from a import calculate_tax" in t, "canonical import missing from test"
        print("  ✓  old imports replaced with canonical import")
        # call sites renamed — use word-boundary regex to avoid matching test function names
        _ct_pat = re.compile(r'\bcompute_tax\s*\(')
        _gt_pat = re.compile(r'\bget_tax\s*\(')
        code_lines = [l.split('#')[0] for l in t.splitlines()]
        assert not any(_ct_pat.search(l) for l in code_lines), "live compute_tax() call remains"
        assert not any(_gt_pat.search(l) for l in code_lines), "live get_tax() call remains"
        print("  ✓  no live calls to removed functions in test file")
        # SIR provenance comments present (import lines and/or call sites)
        assert any("compute_tax" in l and "SIR" in l for l in t.splitlines()), "SIR comment missing for compute_tax"
        assert any("get_tax" in l and "SIR" in l for l in t.splitlines()), "SIR comment missing for get_tax"
        print("  ✓  SIR provenance comments present in test file")
        # file still parses
        ast.parse(t)
        print("  ✓  updated test file parses without SyntaxError")

        print("\n    --- updated test file ---")
        for line in t.splitlines():
            print(f"      {line}")
    except AssertionError as e:
        print(f"\n{e}")
        PASS = False

    # ── 4. Runtime execution ─────────────────────────────────────────
    print("\n[4] Runtime execution in a temp directory...")
    import subprocess
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        for fname, src in modified.items():
            (tmp / fname).write_text(src)
        (tmp / "utils.py").write_text(utils_py)
        (tmp / "main.py").write_text(MAIN_SRC)
        # Write updated test files
        for tfname, tsrc in updated_tests.items():
            (tmp / tfname).write_text(tsrc)

        print("\n    --- merged file contents ---")
        for fname in ("a.py", "b.py", "c.py"):
            print(f"\n    [{fname}]")
            for line in (tmp / fname).read_text().splitlines():
                print(f"      {line}")

        print("\n    --- running main.py ---")
        result = subprocess.run(
            [sys.executable, str(tmp / "main.py")],
            capture_output=True, text=True
        )
        if result.stdout:
            for line in result.stdout.splitlines():
                print(f"    {line}")
        if result.stderr:
            print("    STDERR:")
            for line in result.stderr.splitlines():
                print(f"    {line}")
        if result.returncode != 0:
            print(f"    FAIL: main.py exited with code {result.returncode}")
            PASS = False
        else:
            print(f"    ✓  main.py exited cleanly (code 0)")

        # ── 5. Run pytest on updated test files ──────────────────────
        print("\n[5] Running pytest on updated test files...")
        pytest_result = subprocess.run(
            [sys.executable, "-m", "pytest", str(tmp / "test_taxes.py"), "-v"],
            capture_output=True, text=True, cwd=str(tmp)
        )
        if pytest_result.stdout:
            for line in pytest_result.stdout.splitlines():
                print(f"    {line}")
        if pytest_result.stderr:
            for line in pytest_result.stderr.splitlines():
                if "warning" not in line.lower():
                    print(f"    {line}")
        if pytest_result.returncode != 0:
            print(f"    FAIL: pytest exited with code {pytest_result.returncode}")
            PASS = False
        else:
            print(f"    ✓  all pytest tests pass on merged code")

    print("\n" + "=" * 60)
    if PASS:
        print("ALL TESTS PASSED")
    else:
        print("SOME TESTS FAILED")
    print("=" * 60)
    return PASS


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
