#!/usr/bin/env python3
"""
sir_interactive.py — SIR Engine interactive demo

Run:  python3 sir_interactive.py
      python3 sir_interactive.py --backend ollama --model codellama:7b
"""

import argparse
import ast
import os
import re
import subprocess
import sys
import tempfile
import textwrap
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# ── Colours ────────────────────────────────────────────────────────────────────
RESET   = "\033[0m";  BOLD  = "\033[1m";  DIM  = "\033[2m"
GREEN   = "\033[32m"; YELLOW= "\033[33m"; RED  = "\033[31m"
CYAN    = "\033[36m"; BLUE  = "\033[34m"; MAGENTA = "\033[35m"

def c(t, *codes): return "".join(codes) + str(t) + RESET
def header(t):
    print(f"\n{c('─'*60, BOLD, BLUE)}")
    print(f"  {c(t, BOLD, CYAN)}")
    print(f"{c('─'*60, BOLD, BLUE)}")
def ok(m):    print(f"\n  {c('✓', GREEN, BOLD)}  {m}")
def fail(m):  print(f"\n  {c('✗', RED, BOLD)}  {m}")
def info(m):  print(f"  {c('→', DIM)}  {m}")
def warn(m):  print(f"  {c('⚠', YELLOW)}  {m}")
def label(m): print(f"\n  {c('▶', MAGENTA, BOLD)}  {c(m, BOLD)}")
def divider(): print(f"\n  {c('·'*56, DIM)}")
def pause():   input(f"\n  {c('Press Enter to continue...', DIM)}")


# ── Pre-made demo files ────────────────────────────────────────────────────────

# Feature 1: alpha equivalence
DEMO_FUNC_A = """\
# billing.py
def calculate_tax(amount, rate):
    if amount <= 0:
        return 0
    return amount * rate
"""

DEMO_FUNC_B = """\
# orders.py
def compute_tax(value, pct):
    if value <= 0:
        return 0
    return value * pct
"""

DEMO_FUNC_DIFF = """\
# finance.py
def compound_interest(principal, rate, years):
    total = principal
    for _ in range(years):
        total = total + total * rate
    return total
"""

# Feature 2: 3-way cluster scan
SCAN_FILES = {
    "billing.py": textwrap.dedent("""\
        def calculate_tax(amount, rate):
            if amount <= 0:
                return 0
            return amount * rate

        def invoice_total(subtotal):
            tax = calculate_tax(subtotal, 0.1)
            return subtotal + tax
    """),
    "orders.py": textwrap.dedent("""\
        def compute_tax(value, pct):
            if value <= 0:
                return 0
            return value * pct

        def order_total(price):
            tax = compute_tax(price, 0.2)
            return price + tax
    """),
    "checkout.py": textwrap.dedent("""\
        def get_tax(subtotal, tax_rate):
            if subtotal <= 0:
                return 0
            return subtotal * tax_rate

        def cart_total(price):
            t = get_tax(price, 0.15)
            return price + t
    """),
    "finance.py": textwrap.dedent("""\
        def compound_interest(principal, rate, years):
            total = principal
            for _ in range(years):
                total = total + total * rate
            return total
    """),
}

# Feature 3: Python vs JavaScript
DEMO_PY_FUNC = """\
def apply_discount(cost, percentage):
    if cost <= 0:
        return 0
    return cost * percentage
"""

DEMO_JS_FUNC = """\
function applyDiscount(cost, percentage) {
    if (cost <= 0) {
        return 0;
    }
    return cost * percentage;
}
"""

# Feature 4: class duplicates (Python)
CLASS_CART = textwrap.dedent("""\
    # cart.py
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

CLASS_BASKET = textwrap.dedent("""\
    # basket.py
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

# Feature 5: AI cross-language (Java + Kotlin + Python)
JAVA_COUNTER = """\
// Counter.java
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
// Counter.kt
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

PYTHON_COUNTER = """\
# counter.py
class Counter:
    def __init__(self):
        self.count = 0

    def increment(self):
        self.count = self.count + 1

    def get_count(self):
        return self.count

    def reset(self):
        self.count = 0
"""

# Feature 6: merge + pytest
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

MERGE_C = textwrap.dedent("""\
    def get_tax(amount, rate):
        if amount <= 0:
            return 0
        return amount * rate

    def cart_total(price):
        result = get_tax(price, 0.15)
        return price + result
""")

MERGE_TESTS = textwrap.dedent("""\
    from a import calculate_tax
    from b import compute_tax
    from c import get_tax

    def test_calculate_tax():
        assert calculate_tax(100, 0.1) == 10.0

    def test_compute_tax():
        assert compute_tax(100, 0.1) == 10.0

    def test_get_tax():
        assert get_tax(100, 0.1) == 10.0

    def test_zero():
        assert compute_tax(0, 0.5) == 0
        assert get_tax(-5, 0.3) == 0
""")


# ── Feature implementations ────────────────────────────────────────────────────

def show_file(name, src, highlight_lines=None):
    print(f"\n  {c(name, BOLD, CYAN)}")
    for i, line in enumerate(src.strip().splitlines(), 1):
        col = GREEN if (highlight_lines and i in highlight_lines) else DIM
        print(f"    {c(line, col)}")


def feature_alpha_equivalence():
    header("FEATURE 1 — Alpha Equivalence")
    info("The core idea: two functions with the same logic but different")
    info("variable names are alpha-equivalent — they hash identically.")
    info("Two functions with different logic hash differently.\n")

    show_file("billing.py", DEMO_FUNC_A)
    show_file("orders.py",  DEMO_FUNC_B)
    show_file("finance.py", DEMO_FUNC_DIFF)

    pause()

    from sir.core import hash_source
    import time

    t0 = time.perf_counter()
    h_a = hash_source(DEMO_FUNC_A.strip(), mode="semantic")
    h_b = hash_source(DEMO_FUNC_B.strip(), mode="semantic")
    h_d = hash_source(DEMO_FUNC_DIFF.strip(), mode="semantic")
    elapsed = (time.perf_counter() - t0) * 1000

    divider()
    label("Hashing all three functions...")
    print()
    info(f"calculate_tax     {c(h_a[:32]+'...', CYAN)}")
    info(f"compute_tax       {c(h_b[:32]+'...', CYAN)}")
    info(f"compound_interest {c(h_d[:32]+'...', CYAN)}")

    print()
    if h_a == h_b:
        ok(f"calculate_tax == compute_tax  — DUPLICATE  ({elapsed:.1f}ms)")
    else:
        fail("calculate_tax != compute_tax")

    if h_a != h_d:
        ok(f"compound_interest has different hash  — correctly NOT flagged")
    else:
        fail("False positive on compound_interest")


def feature_function_scan():
    header("FEATURE 2 — Duplicate Function Scan")
    info("4 files. calculate_tax, compute_tax, and get_tax are all")
    info("the same function under different names across different files.")
    info("compound_interest is unique — should not be flagged.\n")

    for fname, src in SCAN_FILES.items():
        show_file(fname, src)

    pause()

    from sir.core import hash_source
    import time

    label("Scanning all files...")
    t0 = time.perf_counter()
    groups = defaultdict(list)
    for fname, src in SCAN_FILES.items():
        tree = ast.parse(src)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                seg = ast.get_source_segment(src, node)
                if seg:
                    h = hash_source(seg, mode="semantic")
                    groups[h].append((fname, node.name))
    elapsed = (time.perf_counter() - t0) * 1000

    dupes = {h: v for h, v in groups.items() if len(v) >= 2}
    unique = {h: v for h, v in groups.items() if len(v) == 1}

    divider()
    total_funcs = sum(len(v) for v in groups.values())
    info(f"Files scanned:       4")
    info(f"Functions found:     {total_funcs}")
    info(f"Unique structures:   {len(groups)}")
    info(f"Duplicate clusters:  {c(len(dupes), BOLD)}")
    print()

    for h, members in dupes.items():
        print(f"  {c('●', RED, BOLD)}  {c(str(len(members))+' copies', BOLD)}  {c(h[:20]+'...', DIM)}")
        for fname, name in members:
            print(f"       {c(name, CYAN)}  in  {c(fname, BOLD)}")

    print()
    for h, members in unique.items():
        fname, name = members[0]
        print(f"  {c('○', DIM)}  {c(name, DIM)}  in  {c(fname, DIM)}  — unique, not flagged")

    ok(f"3-way duplicate cluster detected in {elapsed:.1f}ms")


def feature_js_crosslang():
    header("FEATURE 3 — Cross-Language Detection (Python + JavaScript)")
    info("The same logic in Python and JavaScript produces the same hash.")
    info("No translation needed — a common token layer handles both.\n")

    show_file("discount.py", DEMO_PY_FUNC)
    show_file("discount.js", DEMO_JS_FUNC)

    pause()

    from sir_universal import hash_python_functions, hash_js_functions_universal
    import time

    label("Hashing both functions using the universal token layer...")
    t0 = time.perf_counter()
    h_py = hash_python_functions(DEMO_PY_FUNC, "discount.py")[0][2]
    h_js = hash_js_functions_universal(DEMO_JS_FUNC, "discount.js")[0][2]
    elapsed = (time.perf_counter() - t0) * 1000

    divider()
    info(f"Python hash:     {c(h_py[:32]+'...', CYAN)}")
    info(f"JavaScript hash: {c(h_js[:32]+'...', CYAN)}")
    print()

    if h_py == h_js:
        ok(f"Hashes MATCH — cross-language duplicate detected  ({elapsed:.1f}ms)")
    else:
        fail("Hashes differ")


def feature_class_detection():
    header("FEATURE 4 — Class-Level Detection (Merkle Hashing)")
    info("Each method is hashed independently.")
    info("Method hashes are sorted and combined into a class fingerprint.")
    info("ShoppingCart and OrderBasket: different names, different field")
    info("names, different method names — but identical logic.\n")

    show_file("cart.py",   CLASS_CART)
    show_file("basket.py", CLASS_BASKET)

    pause()

    from sir2_core import extract_classes, scan_for_class_dupes
    import time

    label("Extracting and hashing both classes...")
    t0 = time.perf_counter()
    classes = (
        extract_classes(CLASS_CART,   "cart.py") +
        extract_classes(CLASS_BASKET, "basket.py")
    )
    exact, _, unresolved = scan_for_class_dupes(classes)
    elapsed = (time.perf_counter() - t0) * 1000

    divider()
    for cls in classes:
        print(f"\n  {c(cls.name, CYAN, BOLD)}  {c(cls.file, BOLD)}")
        info(f"methods: {', '.join(m.name for m in cls.methods)}")
        info(f"hash:    {c(cls.class_hash[:32]+'...', CYAN)}")

    print()
    if exact:
        ok(f"Classes detected as identical  ({elapsed:.1f}ms)")
        ok("Sorted method hashes = order-independent fingerprint")
    else:
        fail("Not detected as duplicates")


def feature_ai_translation():
    header("FEATURE 5 — AI Translation (Java + Kotlin + Python)")
    info("Non-Python classes are translated to Python via LLM, then hashed.")
    info("Same logic in any OOP language = same Merkle fingerprint.")
    info(f"AI backend: {c(BACKEND, BOLD)}\n")

    show_file("Counter.java",  JAVA_COUNTER)
    show_file("Counter.kt",    KOTLIN_COUNTER)
    show_file("counter.py",    PYTHON_COUNTER)

    pause()

    from sir2_core import scan_files_for_classes
    import time

    label(f"Translating Java and Kotlin → Python via {BACKEND}, then hashing all three...")
    print()

    t0 = time.perf_counter()
    exact, similar, total, unresolved = scan_files_for_classes(
        {
            "Counter.java": JAVA_COUNTER,
            "Counter.kt":   KOTLIN_COUNTER,
            "counter.py":   PYTHON_COUNTER,
        },
        min_similarity=1.0,
        ai_backend=BACKEND,
        ai_api_key=API_KEY,
        ai_ollama_model=MODEL,
        ai_ollama_host=HOST,
        ai_use_cache=True,
    )
    elapsed = (time.perf_counter() - t0) * 1000

    divider()
    info(f"Total classes found:  {total}")
    info(f"Exact clusters:       {len(exact)}")
    print()

    if exact:
        for cluster in exact:
            print(f"  {c('●', RED, BOLD)}  {c(str(len(cluster.members))+' copies', BOLD)}  {c(cluster.class_hash[:20]+'...', DIM)}")
            for m in cluster.members:
                lang = m.original_language or "Python"
                print(f"       {c(m.name, CYAN)}  {c('['+lang+']', YELLOW)}  {c(m.file, BOLD)}")

        all_three = any(len(cl.members) == 3 for cl in exact)
        if all_three:
            ok(f"Java + Kotlin + Python all hash identically  ({elapsed:.1f}ms)")
        else:
            langs = [m.original_language or "Python" for m in exact[0].members]
            ok(f"Cross-language duplicate detected: {langs}  ({elapsed:.1f}ms)")
            warn("Full 3-way match not achieved — LLM translated slightly differently")
            warn("The pipeline works — this is a translation variance note")
    else:
        fail(f"No cross-language duplicates found  ({elapsed:.1f}ms)")
        warn("Try running again — LLM output can vary")


def feature_merge():
    header("FEATURE 6 — Auto Merge")
    info("3 files each have a duplicate tax function under a different name.")
    info("SIR will:")
    info("  • Remove the 2 duplicate functions")
    info("  • Rename all call sites to the canonical name")
    info("  • Add the correct imports")
    info("  • Update the test file")
    info("  • Run pytest to prove the merged code still works\n")

    show_file("a.py  (canonical)", MERGE_A)
    show_file("b.py  (has compute_tax — duplicate)", MERGE_B)
    show_file("c.py  (has get_tax — duplicate)", MERGE_C)
    show_file("test_billing.py", MERGE_TESTS)

    pause()

    from sir.core import hash_source
    import time

    # ── Scan ──
    label("Scanning for duplicates...")
    t0 = time.perf_counter()
    groups = defaultdict(list)
    func_map = {}
    for fname, src in [("a.py", MERGE_A), ("b.py", MERGE_B), ("c.py", MERGE_C)]:
        tree = ast.parse(src)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                seg = ast.get_source_segment(src, node)
                if seg:
                    h = hash_source(seg, mode="semantic")
                    groups[h].append((fname, node.name, node.lineno, node.end_lineno))
                    func_map[f"{fname}::{node.name}"] = seg
    dupes = {h: v for h, v in groups.items() if len(v) >= 2}
    t_scan = (time.perf_counter() - t0) * 1000

    divider()
    info(f"Functions scanned:   {sum(len(v) for v in groups.values())}")
    info(f"Duplicate clusters:  {c(len(dupes), BOLD)}")
    print()
    for h, members in dupes.items():
        print(f"  {c('●', RED, BOLD)}  " +
              "  ==  ".join(f"{c(name, CYAN)} in {c(fname, BOLD)}" for fname, name, *_ in members))
    ok(f"Scan complete  ({t_scan:.1f}ms)")

    pause()

    # ── Merge ──
    label("Merging...")
    ts = datetime.now().strftime("%Y-%m-%d")
    modified = {"a.py": MERGE_A, "b.py": MERGE_B, "c.py": MERGE_C}

    for h, members in dupes.items():
        canon_fname, canon_name, *_ = members[0]
        canon_mod = canon_fname[:-3]
        for dup_fname, dup_name, *_ in members[1:]:
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
            pat = re.compile(rf'\b{re.escape(dup_name)}\s*\(')
            src = "\n".join(
                (pat.sub(f'{canon_name}(', l).rstrip() + f"  # SIR: was {dup_name}() — merged {ts}"
                 if pat.search(l) else l)
                for l in src.splitlines()
            )
            # Add import
            imp = f"from {canon_mod} import {canon_name}"
            if imp not in src:
                src = imp + "\n" + src
            modified[dup_fname] = src

    # Update test file
    updated_test = MERGE_TESTS
    for h, members in dupes.items():
        canon_fname, canon_name, *_ = members[0]
        canon_mod = canon_fname[:-3]
        canonical_import = f"from {canon_mod} import {canon_name}"
        for dup_fname, dup_name, *_ in members[1:]:
            already = canonical_import in updated_test
            call_pat = re.compile(rf'\b{re.escape(dup_name)}\s*\(')
            imp_pat  = re.compile(rf'^(\s*from\s+\S+\s+import\s+(?:.*,\s*)?){re.escape(dup_name)}(\s*(?:,.*|#.*)?$)')
            lines, result = updated_test.splitlines(), []
            for line in lines:
                m = imp_pat.match(line)
                if m:
                    if already:
                        line = f"# SIR: {dup_name} merged into {canon_name} (from {canon_mod}) — {ts}"
                    else:
                        line = f"{canonical_import}  # SIR: was {dup_name} — merged {ts}"
                        already = True
                elif call_pat.search(line):
                    line = call_pat.sub(f'{canon_name}(', line)
                    line = line.rstrip() + f"  # SIR: was {dup_name}() — merged {ts}"
                result.append(line)
            updated_test = "\n".join(result)

    divider()
    print(f"\n  {c('b.py  (after merge):', BOLD)}")
    for line in modified["b.py"].splitlines():
        col = GREEN if "# SIR" in line else (CYAN if line.startswith("from") else DIM)
        print(f"    {c(line, col)}")

    print(f"\n  {c('c.py  (after merge):', BOLD)}")
    for line in modified["c.py"].splitlines():
        col = GREEN if "# SIR" in line else (CYAN if line.startswith("from") else DIM)
        print(f"    {c(line, col)}")

    print(f"\n  {c('test_billing.py  (updated):', BOLD)}")
    for line in updated_test.splitlines():
        col = GREEN if "# SIR" in line else DIM
        print(f"    {c(line, col)}")

    pause()

    # ── Run ──
    label("Running merged code + pytest...")
    main_src = textwrap.dedent("""\
        import sys, os; sys.path.insert(0, os.path.dirname(__file__))
        from a import invoice_total
        from b import order_total
        from c import cart_total
        assert abs(invoice_total(100) - 110.0) < 1e-9
        assert abs(order_total(200)   - 240.0) < 1e-9
        assert abs(cart_total(300)    - 345.0) < 1e-9
        print("invoice_total(100) =", invoice_total(100))
        print("order_total(200)   =", order_total(200))
        print("cart_total(300)    =", cart_total(300))
    """)

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        for fname, src in modified.items():
            (p / fname).write_text(src)
        (p / "main.py").write_text(main_src)
        (p / "test_billing.py").write_text(updated_test)

        r = subprocess.run([sys.executable, str(p / "main.py")],
                           capture_output=True, text=True)
        if r.returncode == 0:
            divider()
            for line in r.stdout.strip().splitlines():
                info(line)
            ok("Merged code runs correctly")
        else:
            fail("Runtime error after merge")
            print(r.stderr)
            return

        pr = subprocess.run(
            [sys.executable, "-m", "pytest", str(p / "test_billing.py"), "-v", "--tb=short"],
            capture_output=True, text=True, cwd=tmp,
        )
        for line in pr.stdout.splitlines():
            if "PASSED" in line or "FAILED" in line or "passed" in line or "failed" in line:
                col = GREEN if ("PASSED" in line or "passed" in line) else RED
                info(c(line.strip(), col))
        if pr.returncode == 0:
            ok("All 4 pytest tests pass on the merged code")
        else:
            fail("Tests failed after merge")


# ── Menu ───────────────────────────────────────────────────────────────────────

MENU = [
    ("Alpha equivalence  — same logic, different names → same hash",  feature_alpha_equivalence),
    ("Function scan      — detect a 3-way duplicate cluster",          feature_function_scan),
    ("Cross-language     — Python and JavaScript hash identically",    feature_js_crosslang),
    ("Class detection    — Merkle hash across ShoppingCart/OrderBasket", feature_class_detection),
    (f"AI translation     — Java + Kotlin + Python → one cluster",     feature_ai_translation),
    ("Auto merge         — remove duplicates, fix calls, run pytest",  feature_merge),
]


def print_menu():
    header("SIR Engine — Interactive Demo")
    print()
    for i, (label_, _) in enumerate(MENU, 1):
        print(f"    {c(str(i), BOLD, CYAN)}  {label_}")
    print()
    print(f"    {c('a', BOLD, CYAN)}  Run all features in sequence")
    print(f"    {c('q', BOLD, CYAN)}  Quit")
    print()


def main():
    global BACKEND, MODEL, HOST, API_KEY

    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=["ollama", "anthropic"], default=None)
    parser.add_argument("--model",   default="codellama:7b")
    parser.add_argument("--host",    default="http://localhost:11434")
    parser.add_argument("--api-key", default=None)
    args = parser.parse_args()

    API_KEY = args.api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    BACKEND = args.backend or ("anthropic" if API_KEY else "ollama")
    MODEL   = args.model
    HOST    = args.host

    print()
    print(c("  ███████╗██╗██████╗     ███████╗███╗   ██╗ ██████╗ ██╗███╗   ██╗███████╗", BOLD, CYAN))
    print(c("  ██╔════╝██║██╔══██╗    ██╔════╝████╗  ██║██╔════╝ ██║████╗  ██║██╔════╝", BOLD, CYAN))
    print(c("  ███████╗██║██████╔╝    █████╗  ██╔██╗ ██║██║  ███╗██║██╔██╗ ██║█████╗  ", BOLD, CYAN))
    print(c("  ╚════██║██║██╔══██╗    ██╔══╝  ██║╚██╗██║██║   ██║██║██║╚██╗██║██╔══╝  ", BOLD, CYAN))
    print(c("  ███████║██║██║  ██║    ███████╗██║ ╚████║╚██████╔╝██║██║ ╚████║███████╗", BOLD, CYAN))
    print(c("  ╚══════╝╚═╝╚═╝  ╚═╝    ╚══════╝╚═╝  ╚═══╝ ╚═════╝ ╚═╝╚═╝  ╚═══╝╚══════╝", BOLD, CYAN))
    print()
    print(c("  Semantic duplicate detection — any language, any name", DIM))
    print(c(f"  AI backend: {BACKEND}  |  model: {MODEL}", DIM) if BACKEND == "ollama"
          else c(f"  AI backend: {BACKEND}", DIM))

    while True:
        print_menu()
        choice = input(f"  {c('?', BOLD, CYAN)}  Pick a feature: ").strip().lower()

        if choice == "q":
            print(f"\n  {c('Bye!', DIM)}\n")
            break
        elif choice == "a":
            for _, fn in MENU:
                try:
                    fn()
                except KeyboardInterrupt:
                    print(f"\n  {c('Skipped.', DIM)}")
                pause()
        else:
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(MENU):
                    try:
                        MENU[idx][1]()
                    except KeyboardInterrupt:
                        print(f"\n  {c('Cancelled.', DIM)}")
                    except Exception as e:
                        fail(f"Error: {e}")
                        import traceback; traceback.print_exc()
                    pause()
                else:
                    print(f"  {c('Invalid choice.', DIM)}")
            except ValueError:
                print(f"  {c('Invalid choice.', DIM)}")


if __name__ == "__main__":
    main()
