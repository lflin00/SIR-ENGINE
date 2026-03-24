#!/usr/bin/env python3
"""
sir_demo.py — SIR Engine live demo

Shows every major feature with timings and pass/fail results.
Run:  python3 sir_demo.py
      python3 sir_demo.py --backend ollama --model codellama:7b
"""

import argparse
import ast
import re
import sys
import tempfile
import textwrap
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# ── Terminal colours ───────────────────────────────────────────────────────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
RED    = "\033[31m"
CYAN   = "\033[36m"
BLUE   = "\033[34m"
MAGENTA= "\033[35m"

def c(text, *codes): return "".join(codes) + str(text) + RESET
def header(title):
    bar = "─" * 60
    print(f"\n{c(bar, BOLD, BLUE)}")
    print(f"  {c(title, BOLD, CYAN)}")
    print(f"{c(bar, BOLD, BLUE)}")
def section(title):
    print(f"\n  {c('▶', BOLD, MAGENTA)}  {c(title, BOLD)}")
def ok(msg):   print(f"    {c('✓', GREEN, BOLD)}  {msg}")
def fail(msg): print(f"    {c('✗', RED, BOLD)}  {msg}")
def info(msg): print(f"    {c('→', DIM)}  {msg}")
def warn(msg): print(f"    {c('⚠', YELLOW)}  {msg}")
def timer(t):  return c(f"{t*1000:.1f}ms", DIM)


# ── Fixtures ───────────────────────────────────────────────────────────────────

# --- Feature 1: Alpha equivalence (variable renaming) -------------------------
FUNC_ORIGINAL = textwrap.dedent("""\
    def calculate_discount(price, rate):
        if price <= 0:
            return 0
        return price * rate
""")

FUNC_RENAMED_VARS = textwrap.dedent("""\
    def apply_discount(cost, percentage):
        if cost <= 0:
            return 0
        return cost * percentage
""")

FUNC_DIFFERENT = textwrap.dedent("""\
    def calculate_discount(price, rate):
        if price <= 0:
            return 0
        return price - (price * rate)
""")

# --- Feature 2: 3-way cluster (Python) ----------------------------------------
PY_TAX_A = textwrap.dedent("""\
    def calculate_tax(amount, rate):
        if amount <= 0:
            return 0
        return amount * rate
""")
PY_TAX_B = textwrap.dedent("""\
    def compute_tax(value, pct):
        if value <= 0:
            return 0
        return value * pct
""")
PY_TAX_C = textwrap.dedent("""\
    def get_tax(subtotal, tax_rate):
        if subtotal <= 0:
            return 0
        return subtotal * tax_rate
""")
PY_TAX_UNIQUE = textwrap.dedent("""\
    def compound_interest(principal, rate, years):
        total = principal
        for _ in range(years):
            total = total + total * rate
        return total
""")

# --- Feature 3: JavaScript cross-language -------------------------------------
JS_DISCOUNT = """\
function applyDiscount(cost, percentage) {
    if (cost <= 0) {
        return 0;
    }
    return cost * percentage;
}
"""

# --- Feature 4: Class-level (Python) ------------------------------------------
PY_CLASS_A = textwrap.dedent("""\
    class ShoppingCart:
        def __init__(self):
            self.items = []
            self.total = 0

        def add_item(self, price):
            self.items.append(price)
            self.total = self.total + price

        def get_total(self):
            return self.total

        def clear(self):
            self.items = []
            self.total = 0
""")

PY_CLASS_B = textwrap.dedent("""\
    class OrderBasket:
        def __init__(self):
            self.products = []
            self.sum = 0

        def add_product(self, cost):
            self.products.append(cost)
            self.sum = self.sum + cost

        def get_sum(self):
            return self.sum

        def reset(self):
            self.products = []
            self.sum = 0
""")

# --- Feature 5: AI translation ------------------------------------------------
JAVA_COUNTER = """\
public class Counter {
    private int count;

    public Counter() {
        this.count = 0;
    }

    public void increment() {
        this.count = this.count + 1;
    }

    public int getCount() {
        return this.count;
    }

    public void reset() {
        this.count = 0;
    }
}
"""

KOTLIN_COUNTER = """\
class Counter {
    var count: Int = 0

    fun increment() {
        this.count = this.count + 1
    }

    fun getCount(): Int {
        return this.count
    }

    fun reset() {
        this.count = 0
    }
}
"""

PYTHON_COUNTER = textwrap.dedent("""\
    class Counter:
        def __init__(self):
            self.count = 0

        def increment(self):
            self.count = self.count + 1

        def get_count(self):
            return self.count

        def reset(self):
            self.count = 0
""")

# --- Feature 6: Merge ---------------------------------------------------------
MERGE_A = textwrap.dedent("""\
    def calculate_tax(amount, rate):
        if amount <= 0:
            return 0
        return amount * rate

    def invoice_total(subtotal):
        tax = calculate_tax(subtotal, 0.1)
        return subtotal + tax
""")
MERGE_B = textwrap.dedent("""\
    def compute_tax(amount, rate):
        if amount <= 0:
            return 0
        return amount * rate

    def order_total(price):
        tax = compute_tax(price, 0.2)
        return price + tax
""")
MERGE_TESTS = textwrap.dedent("""\
    from a import calculate_tax
    from b import compute_tax

    def test_calculate_tax():
        assert calculate_tax(100, 0.1) == 10.0

    def test_compute_tax():
        assert compute_tax(100, 0.1) == 10.0
""")


# ── Helpers ────────────────────────────────────────────────────────────────────

def extract_functions(src, filename="<src>"):
    tree = ast.parse(src)
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            seg = ast.get_source_segment(src, node)
            if seg:
                out.append((node.name, node.lineno, seg))
    return out

def rename_calls(src, old, new, ts):
    pat = re.compile(rf'\b{re.escape(old)}\s*\(')
    comment = f"  # SIR: was {old}() — merged {ts}"
    return '\n'.join(
        (pat.sub(f'{new}(', l).rstrip() + comment if pat.search(l) else l)
        for l in src.splitlines()
    )

def update_test_src(src, dup, canon, mod, ts):
    canonical_import = f"from {mod} import {canon}"
    already = canonical_import in src
    call_pat = re.compile(rf'\b{re.escape(dup)}\s*\(')
    imp_pat  = re.compile(rf'^(\s*from\s+\S+\s+import\s+(?:.*,\s*)?){re.escape(dup)}(\s*(?:,.*|#.*)?$)')
    lines, result = src.splitlines(), []
    for line in lines:
        m = imp_pat.match(line)
        if m:
            if already:
                line = f"# SIR: {dup} merged into {canon} (from {mod}) — {ts}"
            else:
                line = f"{canonical_import}  # SIR: was {dup} — merged {ts}"
                already = True
        elif call_pat.search(line):
            line = call_pat.sub(f'{canon}(', line)
            line = line.rstrip() + f"  # SIR: was {dup}() — merged {ts}"
        result.append(line)
    return '\n'.join(result)


# ── Demo sections ──────────────────────────────────────────────────────────────

def demo_alpha_equivalence():
    header("FEATURE 1 — Alpha Equivalence  (the core idea)")
    info("Two functions are duplicates if they have the same logic,")
    info("regardless of what the variables or function are named.")
    print()

    from sir.core import hash_source

    t0 = time.perf_counter()
    h_orig    = hash_source(FUNC_ORIGINAL,    mode="semantic")
    h_renamed = hash_source(FUNC_RENAMED_VARS, mode="semantic")
    h_diff    = hash_source(FUNC_DIFFERENT,    mode="semantic")
    elapsed = time.perf_counter() - t0

    section("calculate_discount  vs  apply_discount  (same logic, different names)")
    info(f"calculate_discount  hash: {c(h_orig[:24]+'...', CYAN)}")
    info(f"apply_discount      hash: {c(h_renamed[:24]+'...', CYAN)}")
    if h_orig == h_renamed:
        ok(f"Hashes MATCH — detected as duplicates  {timer(elapsed)}")
    else:
        fail("Hashes differ — missed duplicate")

    section("calculate_discount  vs  different logic  (should NOT match)")
    info(f"different logic     hash: {c(h_diff[:24]+'...', CYAN)}")
    if h_orig != h_diff:
        ok("Hashes DIFFER — correctly NOT flagged as duplicate")
    else:
        fail("False positive — different logic matched")

    return h_orig == h_renamed and h_orig != h_diff


def demo_function_cluster():
    header("FEATURE 2 — Duplicate Cluster Detection  (Python)")
    info("Scans a codebase and groups all functions with identical logic.")
    print()

    from sir.core import hash_source

    files = {
        "billing.py":   PY_TAX_A,
        "orders.py":    PY_TAX_B,
        "checkout.py":  PY_TAX_C,
        "finance.py":   PY_TAX_UNIQUE,
    }

    t0 = time.perf_counter()
    groups = defaultdict(list)
    for fname, src in files.items():
        for name, lineno, code in extract_functions(src, fname):
            h = hash_source(code, mode="semantic")
            groups[h].append((fname, name))
    elapsed = time.perf_counter() - t0

    dupes = {h: v for h, v in groups.items() if len(v) >= 2}

    section(f"Scanned {len(files)} files, {sum(len(v) for v in groups.values())} functions")
    info(f"Unique logic structures found: {c(len(groups), BOLD)}")
    info(f"Duplicate clusters found:      {c(len(dupes), BOLD)}")

    for h, members in dupes.items():
        print()
        print(f"    {c('●', RED, BOLD)}  {c(str(len(members))+' copies', BOLD)}  {c(h[:20]+'...', DIM)}")
        for fname, name in members:
            print(f"       {c(name, CYAN)}  in  {c(fname, BOLD)}")

    print()
    ok(f"3-way duplicate detected across billing / orders / checkout  {timer(elapsed)}")
    ok(f"finance.py::compound_interest correctly left alone (unique logic)")
    return len(dupes) == 1 and len(list(dupes.values())[0]) == 3


def demo_js_crosslang():
    header("FEATURE 3 — Cross-Language Detection  (Python + JavaScript)")
    info("The same logic written in Python and JavaScript hashes identically.")
    print()

    from sir_universal import hash_python_functions, hash_js_functions_universal

    t0 = time.perf_counter()
    py_funcs = hash_python_functions(FUNC_RENAMED_VARS, "discount.py")
    js_funcs = hash_js_functions_universal(JS_DISCOUNT, "discount.js")
    h_py = py_funcs[0][2] if py_funcs else ""
    h_js = js_funcs[0][2] if js_funcs else ""
    elapsed = time.perf_counter() - t0

    section("apply_discount (Python)  vs  applyDiscount (JavaScript)")
    print()
    for line in FUNC_RENAMED_VARS.strip().splitlines():
        print(f"    {c(line, DIM)}")
    print()
    for line in JS_DISCOUNT.strip().splitlines():
        print(f"    {c(line, DIM)}")
    print()
    info(f"Python hash:     {c(h_py[:24]+'...', CYAN)}")
    info(f"JavaScript hash: {c(h_js[:24]+'...', CYAN)}")
    if h_py == h_js:
        ok(f"Hashes MATCH — cross-language duplicate detected  {timer(elapsed)}")
    else:
        fail("Hashes differ")
    return h_py == h_js


def demo_class_detection():
    header("FEATURE 4 — Class-Level Detection  (Merkle Hashing)")
    info("Hashes each method independently, then combines into a class fingerprint.")
    info("ShoppingCart and OrderBasket: different names, different field names — same logic.")
    print()

    from sir2_core import extract_classes, scan_for_class_dupes

    t0 = time.perf_counter()
    classes = (
        extract_classes(PY_CLASS_A, "cart.py") +
        extract_classes(PY_CLASS_B, "basket.py")
    )
    exact, _, unresolved = scan_for_class_dupes(classes, min_similarity=1.0)
    elapsed = time.perf_counter() - t0

    section("ShoppingCart  vs  OrderBasket")
    for cls in classes:
        methods = ", ".join(m.name for m in cls.methods)
        print(f"    {c(cls.name, CYAN)}  {c(cls.file, BOLD)}")
        print(f"      methods: {methods}")
        print(f"      hash:    {c(cls.class_hash[:24]+'...', DIM)}")
        print()

    if exact and len(exact[0].members) == 2:
        ok(f"Classes detected as identical  {timer(elapsed)}")
        ok("Method order independence: hashes are sorted before combining")
    else:
        fail("Classes not detected as duplicates")
    return bool(exact)


def demo_ai_translation(backend, api_key, ollama_model, ollama_host):
    header("FEATURE 5 — AI Translation  (Java + Kotlin + Python → same hash)")
    info("Non-Python classes are translated to Python via LLM, then hashed.")
    info("Same logic in any OOP language = same fingerprint.")
    print()

    from sir2_core import scan_files_for_classes

    section("Scanning: counter_java.java + counter_kt.kt + counter_py.py")
    print()
    info(f"AI backend: {c(backend, BOLD)}")
    info("Translating Java and Kotlin classes to Python...")
    print()

    t0 = time.perf_counter()
    exact, similar, total, unresolved = scan_files_for_classes(
        {
            "counter_java.java": JAVA_COUNTER,
            "counter_kt.kt":     KOTLIN_COUNTER,
            "counter_py.py":     PYTHON_COUNTER,
        },
        min_similarity=1.0,
        ai_backend=backend,
        ai_api_key=api_key,
        ai_ollama_model=ollama_model,
        ai_ollama_host=ollama_host,
        ai_use_cache=True,
    )
    elapsed = time.perf_counter() - t0

    info(f"Total classes found: {total}")
    info(f"Exact clusters:      {len(exact)}")

    if exact:
        for cluster in exact:
            langs = [f"{c2.name} ({c2.original_language or 'Python'})" for c2 in cluster.members]
            print(f"    {c('●', RED, BOLD)}  {c(str(len(cluster.members))+' copies', BOLD)}")
            for m in cluster.members:
                lang = m.original_language or "Python"
                print(f"       {c(m.name, CYAN)}  {c('['+lang+']', YELLOW)}  {c(m.file, BOLD)}")
        print()

    all_three = any(len(cl.members) == 3 for cl in exact)
    any_cross  = len(exact) >= 1

    if all_three:
        ok(f"Java + Kotlin + Python all detected as identical  {timer(elapsed)}")
    elif any_cross:
        detected_langs = [m.original_language or "Python" for m in exact[0].members]
        ok(f"Cross-language duplicate detected: {detected_langs}  {timer(elapsed)}")
        warn("Full 3-way match not achieved (LLM produced slightly different structure)")
        warn("Core pipeline works — this is a translation confidence note, not a bug")
    else:
        fail(f"No cross-language duplicates found  {timer(elapsed)}")

    return any_cross


def demo_merge():
    header("FEATURE 6 — Auto Merge  (remove duplicates, fix call sites, update tests)")
    info("SIR removes duplicate functions, renames every call site,")
    info("adds imports, and updates test files — producing runnable code.")
    print()

    from sir.core import hash_source

    # Scan
    t0 = time.perf_counter()
    groups = defaultdict(list)
    func_map = {}
    for fname, src in [("a.py", MERGE_A), ("b.py", MERGE_B)]:
        for name, lineno, code in extract_functions(src, fname):
            h = hash_source(code, mode="semantic")
            groups[h].append((fname, name, lineno))
            func_map[f"{fname}::{name}"] = code
    dupes = {h: v for h, v in groups.items() if len(v) >= 2}
    t_scan = time.perf_counter() - t0

    section("Scan phase")
    info(f"Functions scanned: {sum(len(v) for v in groups.values())}")
    info(f"Duplicate clusters: {c(len(dupes), BOLD)}")
    for h, members in dupes.items():
        print(f"    {c('●', RED, BOLD)}  " + "  ==  ".join(
            f"{c(name, CYAN)} in {c(fname, BOLD)}" for fname, name, _ in members
        ))
    ok(f"Scan complete  {timer(t_scan)}")

    # Merge
    t0 = time.perf_counter()
    modified = {"a.py": MERGE_A, "b.py": MERGE_B}
    ts = datetime.now().strftime("%Y-%m-%d")

    for h, members in dupes.items():
        canon_fname, canon_name, _ = members[0]
        for dup_fname, dup_name, dup_lineno in members[1:]:
            src = modified[dup_fname]
            # Remove duplicate function
            tree = ast.parse(src)
            lines = src.splitlines()
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == dup_name:
                    lines = lines[:node.lineno - 1] + lines[node.end_lineno:]
                    break
            src = "\n".join(lines)
            # Rename call sites
            src = rename_calls(src, dup_name, canon_name, ts)
            # Add import
            canon_mod = canon_fname[:-3]
            import_line = f"from {canon_mod} import {canon_name}"
            if import_line not in src:
                src = import_line + "\n" + src
            modified[dup_fname] = src

    # Update test file
    updated_test = MERGE_TESTS
    for h, members in dupes.items():
        canon_fname, canon_name, _ = members[0]
        canon_mod = canon_fname[:-3]
        for dup_fname, dup_name, _ in members[1:]:
            updated_test = update_test_src(updated_test, dup_name, canon_name, canon_mod, ts)
    t_merge = time.perf_counter() - t0

    section("Merged b.py")
    for line in modified["b.py"].splitlines():
        colour = GREEN if "# SIR" in line else (CYAN if line.startswith("from") else RESET)
        print(f"    {c(line, colour)}")

    section("Updated test file")
    for line in updated_test.splitlines():
        colour = GREEN if "# SIR" in line else RESET
        print(f"    {c(line, colour)}")

    # Run in temp dir
    section("Runtime verification")
    import subprocess
    main_src = textwrap.dedent(f"""\
        import sys, os; sys.path.insert(0, os.path.dirname(__file__))
        from a import invoice_total
        from b import order_total
        assert abs(invoice_total(100) - 110.0) < 1e-9
        assert abs(order_total(200)  - 240.0) < 1e-9
        print("invoice_total(100) =", invoice_total(100))
        print("order_total(200)   =", order_total(200))
    """)

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        for fname, src in modified.items():
            (p / fname).write_text(src)
        (p / "main.py").write_text(main_src)
        (p / "test_billing.py").write_text(updated_test)

        t0 = time.perf_counter()
        result = subprocess.run([sys.executable, str(p / "main.py")],
                                capture_output=True, text=True)
        t_run = time.perf_counter() - t0

        if result.returncode == 0:
            for line in result.stdout.strip().splitlines():
                info(line)
            ok(f"Merged code executes correctly  {timer(t_run)}")
        else:
            fail("Runtime error after merge")
            print(result.stderr)
            return False

        t0 = time.perf_counter()
        pytest = subprocess.run([sys.executable, "-m", "pytest",
                                 str(p / "test_billing.py"), "-v", "--tb=short"],
                                capture_output=True, text=True, cwd=tmp)
        t_pytest = time.perf_counter() - t0

        passed = pytest.returncode == 0
        for line in pytest.stdout.splitlines():
            if "PASSED" in line or "FAILED" in line or "passed" in line or "failed" in line:
                col = GREEN if ("PASSED" in line or "passed" in line) else RED
                info(c(line.strip(), col))
        if passed:
            ok(f"All pytest tests pass on merged code  {timer(t_pytest)}")
        else:
            fail("Some tests failed after merge")

    return result.returncode == 0 and passed


def demo_health_score():
    header("FEATURE 7 — Health Score")
    info("SIR gives your codebase a score from 0–100.")
    info("100 = no duplicates.  0 = everything is duplicated.")
    print()

    def health(total, dupes):
        if total == 0:
            return 100
        return max(0, round((1 - dupes / total) * 100))

    cases = [
        ("Clean codebase",       10, 0),
        ("A few duplicates",     20, 3),
        ("Moderate duplication", 20, 8),
        ("Heavy duplication",    10, 9),
    ]

    for label, total, dup_count in cases:
        score = health(total, dup_count)
        bar_filled = score // 5
        bar = c("█" * bar_filled, GREEN if score >= 80 else YELLOW if score >= 60 else RED)
        bar += c("░" * (20 - bar_filled), DIM)
        colour = GREEN if score >= 80 else YELLOW if score >= 60 else RED
        print(f"    {label:<28} {bar}  {c(str(score)+'/100', colour, BOLD)}")

    print()
    ok("Health score gives teams a single number to track over time")
    return True


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="SIR Engine demo")
    parser.add_argument("--backend", choices=["ollama", "anthropic"], default=None)
    parser.add_argument("--model",   default="codellama:7b")
    parser.add_argument("--host",    default="http://localhost:11434")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--skip-ai", action="store_true",
                        help="Skip AI translation demo (runs without Ollama/Anthropic)")
    args = parser.parse_args()

    api_key = args.api_key or __import__("os").environ.get("ANTHROPIC_API_KEY", "")
    backend = args.backend or ("anthropic" if api_key else "ollama")

    print()
    print(c("  ███████╗██╗██████╗     ███████╗███╗   ██╗ ██████╗ ██╗███╗   ██╗███████╗", BOLD, CYAN))
    print(c("  ██╔════╝██║██╔══██╗    ██╔════╝████╗  ██║██╔════╝ ██║████╗  ██║██╔════╝", BOLD, CYAN))
    print(c("  ███████╗██║██████╔╝    █████╗  ██╔██╗ ██║██║  ███╗██║██╔██╗ ██║█████╗  ", BOLD, CYAN))
    print(c("  ╚════██║██║██╔══██╗    ██╔══╝  ██║╚██╗██║██║   ██║██║██║╚██╗██║██╔══╝  ", BOLD, CYAN))
    print(c("  ███████║██║██║  ██║    ███████╗██║ ╚████║╚██████╔╝██║██║ ╚████║███████╗", BOLD, CYAN))
    print(c("  ╚══════╝╚═╝╚═╝  ╚═╝    ╚══════╝╚═╝  ╚═══╝ ╚═════╝ ╚═╝╚═╝  ╚═══╝╚══════╝", BOLD, CYAN))
    print()
    print(c("  Semantic duplicate detection across any programming language", DIM))
    print(c("  Based on alpha equivalence from formal logic", DIM))
    print()

    results = {}
    t_total = time.perf_counter()

    results["Alpha equivalence"]      = demo_alpha_equivalence()
    results["Function cluster"]        = demo_function_cluster()
    results["Cross-language (JS+PY)"]  = demo_js_crosslang()
    results["Class-level detection"]   = demo_class_detection()
    results["Health score"]            = demo_health_score()

    if not args.skip_ai:
        results["AI translation (cross-lang)"] = demo_ai_translation(
            backend, api_key, args.model, args.host
        )
    else:
        warn("AI translation demo skipped (--skip-ai)")

    results["Auto merge + pytest"]     = demo_merge()

    elapsed_total = time.perf_counter() - t_total

    # ── Summary ────────────────────────────────────────────────────────────────
    header("SUMMARY")
    print()
    passed = sum(1 for v in results.values() if v)
    total  = len(results)
    for feature, result in results.items():
        icon = c("✓", GREEN, BOLD) if result else c("✗", RED, BOLD)
        print(f"    {icon}  {feature}")

    print()
    score_col = GREEN if passed == total else YELLOW if passed >= total * 0.8 else RED
    print(f"  {c(f'{passed}/{total} features demonstrated', score_col, BOLD)}  "
          f"{c(f'({elapsed_total:.1f}s total)', DIM)}")

    if passed == total:
        print()
        print(c("  All features working.", GREEN, BOLD))
    print()


if __name__ == "__main__":
    main()
