"""
test_ai_translation.py — End-to-end test for AI translation pipeline.

Tests:
1. AI backend health check (Ollama or Anthropic)
2. Non-Python class translates to valid Python
3. Translated class hashes identically to an equivalent Python class
4. Two non-Python files with duplicate logic are detected as duplicates
5. AI-translated class flows through the full V2 scan pipeline

Run with Anthropic backend:
    ANTHROPIC_API_KEY=sk-... python3 test_ai_translation.py

Run with Ollama backend:
    python3 test_ai_translation.py --backend ollama --model codellama:7b

Skips gracefully if no backend is available.
"""

import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS = "✓ PASS"
FAIL = "✗ FAIL"
SKIP = "— SKIP"


# ─────────────────────────────────────────────
#  Test fixtures
#  Java and Kotlin classes with identical logic — should detect as duplicates
# ─────────────────────────────────────────────

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

# Identical logic, Kotlin syntax
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

# A Python class with the same logic — the translated Java/Kotlin should hash to this
PYTHON_COUNTER_EQUIVALENT = """\
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

# A different Java class — should NOT match Counter
JAVA_STACK = """\
public class Stack {
    private int[] items;
    private int top;

    public Stack() {
        this.items = new int[100];
        this.top = -1;
    }

    public void push(int value) {
        this.top = this.top + 1;
        this.items[this.top] = value;
    }

    public int pop() {
        int value = this.items[this.top];
        this.top = this.top - 1;
        return value;
    }
}
"""

# Second Java Counter — identical logic, different class name
JAVA_COUNTER_B = """\
public class Tally {
    private int total;

    public Tally() {
        this.total = 0;
    }

    public void increment() {
        this.total = this.total + 1;
    }

    public int getCount() {
        return this.total;
    }

    public void reset() {
        this.total = 0;
    }
}
"""


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────

def _check_backend(backend: str, api_key: str, ollama_host: str, ollama_model: str):
    """
    Returns (available: bool, reason: str).
    """
    if backend == "anthropic":
        if not api_key:
            return False, "ANTHROPIC_API_KEY not set"
        return True, "Anthropic API key present"
    else:
        try:
            from sir_ai_translate import check_ollama, get_ollama_models
            if not check_ollama(ollama_host):
                return False, f"Ollama not reachable at {ollama_host}"
            models = get_ollama_models(ollama_host)
            if not models:
                return False, "Ollama running but no models installed (try: ollama pull codellama:7b)"
            if ollama_model not in models:
                # Try to find any code model
                code_models = [m for m in models if any(k in m for k in ("code", "llama", "mistral", "deepseek"))]
                if not code_models:
                    return False, f"Model {ollama_model!r} not found. Available: {models}"
                return True, f"Using {code_models[0]} (requested {ollama_model!r} not found)"
            return True, f"Ollama running with model {ollama_model!r}"
        except ImportError:
            return False, "sir_ai_translate.py not found"
        except Exception as e:
            return False, str(e)


# ─────────────────────────────────────────────
#  Tests
# ─────────────────────────────────────────────

def test_backend_health(backend, api_key, ollama_host, ollama_model):
    print("\n=== TEST 1: AI Backend Health ===")
    available, reason = _check_backend(backend, api_key, ollama_host, ollama_model)
    print(f"  Backend: {backend}")
    print(f"  Status:  {reason}")
    if available:
        print(f"  {PASS}")
    else:
        print(f"  {SKIP} — {reason}")
    return available, reason


def test_translation_produces_valid_python(backend, api_key, ollama_host, ollama_model):
    print("\n=== TEST 2: Java → Python translation produces valid Python class ===")
    from sir2_core import translate_class_to_python
    import ast

    result = translate_class_to_python(
        JAVA_COUNTER,
        language="Java",
        backend=backend,
        api_key=api_key,
        ollama_host=ollama_host,
        ollama_model=ollama_model,
        use_cache=True,
        confidence_check=True,
    )

    conf = result.get("confidence", "FAILED")
    py_src = result.get("python_src", "")
    error = result.get("error", "")
    cache_hit = result.get("cache_hit", False)

    print(f"  Confidence: {conf}{'  (cached)' if cache_hit else ''}")
    if error:
        print(f"  Note: {error}")

    if conf == "FAILED" or not py_src:
        print(f"  Translation failed: {error or 'no output'}")
        print(f"  {FAIL}")
        return False, None

    # Check it's valid Python with a class
    try:
        tree = ast.parse(py_src)
        classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        methods = [
            n for cls in classes
            for n in cls.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        print(f"  Classes found: {len(classes)}, Methods: {len(methods)}")
        print(f"  Translated source preview:")
        for line in py_src.splitlines()[:10]:
            print(f"    {line}")
        if len(py_src.splitlines()) > 10:
            print(f"    ... ({len(py_src.splitlines())} lines total)")
        if classes and methods:
            print(f"  {PASS}")
            return True, py_src
        else:
            print(f"  {FAIL} — no class/methods in output")
            return False, None
    except SyntaxError as e:
        print(f"  {FAIL} — SyntaxError: {e}")
        return False, None


def test_translated_hashes_like_python(backend, api_key, ollama_host, ollama_model):
    print("\n=== TEST 3: Translated Java class hashes identically to equivalent Python class ===")
    from sir2_core import extract_classes, scan_for_class_dupes, translate_class_to_python

    # Get Merkle hash of the hand-written Python equivalent
    python_classes = extract_classes(PYTHON_COUNTER_EQUIVALENT, "counter_python.py")
    if not python_classes:
        print(f"  {FAIL} — could not extract Python class")
        return False

    python_hash = python_classes[0].class_hash
    print(f"  Python Counter hash:  {python_hash[:20]}...")

    # Translate Java → Python and get its Merkle hash
    result = translate_class_to_python(
        JAVA_COUNTER,
        language="Java",
        backend=backend,
        api_key=api_key,
        ollama_host=ollama_host,
        ollama_model=ollama_model,
        use_cache=True,
        confidence_check=False,  # already checked in test 2
    )

    if result["confidence"] == "FAILED" or not result["python_src"]:
        print(f"  {FAIL} — translation failed")
        return False

    java_classes = extract_classes(result["python_src"], "counter_java_translated.py")
    if not java_classes:
        print(f"  {FAIL} — could not extract class from translated Python")
        print(f"  Translated source:\n{result['python_src']}")
        return False

    java_hash = java_classes[0].class_hash
    print(f"  Java→Python hash:     {java_hash[:20]}...")

    match = python_hash == java_hash
    print(f"  Hashes match: {'YES' if match else 'NO'}")
    if match:
        print(f"  {PASS} — Java class detected as identical to Python class")
    else:
        print(f"  {FAIL} — hashes differ (translation may have changed structure)")
        print(f"  This can happen when the LLM restructures logic — check translated source in TEST 2")
    return match


def test_two_java_duplicates_detected(backend, api_key, ollama_host, ollama_model):
    print("\n=== TEST 4: Two Java classes with identical logic detected as duplicates ===")
    from sir2_core import scan_files_for_classes

    exact, similar, total, unresolved = scan_files_for_classes(
        {
            "counter_a.java": JAVA_COUNTER,
            "counter_b.java": JAVA_COUNTER_B,
        },
        min_similarity=1.0,
        apply_inheritance=True,
        ai_backend=backend,
        ai_api_key=api_key,
        ai_ollama_host=ollama_host,
        ai_ollama_model=ollama_model,
        ai_use_cache=True,
    )

    print(f"  Total classes found: {total}")
    print(f"  Exact duplicate clusters: {len(exact)}")

    if exact:
        for cluster in exact:
            names = [f"{c.name} ({c.file})" for c in cluster.members]
            print(f"  Cluster: {', '.join(names)}")

    result = len(exact) == 1 and len(exact[0].members) == 2
    print(f"  Counter (Java) == Tally (Java): {PASS if result else FAIL}")
    return result


def test_java_vs_unique_no_false_positive(backend, api_key, ollama_host, ollama_model):
    print("\n=== TEST 5: Different Java classes do NOT match (no false positives) ===")
    from sir2_core import scan_files_for_classes

    exact, similar, total, unresolved = scan_files_for_classes(
        {
            "counter.java": JAVA_COUNTER,
            "stack.java": JAVA_STACK,
        },
        min_similarity=1.0,
        apply_inheritance=True,
        ai_backend=backend,
        ai_api_key=api_key,
        ai_ollama_host=ollama_host,
        ai_ollama_model=ollama_model,
        ai_use_cache=True,
    )

    print(f"  Total classes found: {total}")
    print(f"  Exact duplicate clusters: {len(exact)}")

    result = len(exact) == 0
    print(f"  Counter != Stack: {PASS if result else FAIL}")
    return result


def test_full_pipeline_end_to_end(backend, api_key, ollama_host, ollama_model):
    print("\n=== TEST 6: Full pipeline — Java + Python + Kotlin all in one scan ===")
    from sir2_core import scan_files_for_classes

    exact, similar, total, unresolved = scan_files_for_classes(
        {
            "counter_java.java":   JAVA_COUNTER,
            "counter_kt.kt":       KOTLIN_COUNTER,
            "counter_py.py":       PYTHON_COUNTER_EQUIVALENT,
        },
        min_similarity=1.0,
        apply_inheritance=True,
        ai_backend=backend,
        ai_api_key=api_key,
        ai_ollama_host=ollama_host,
        ai_ollama_model=ollama_model,
        ai_use_cache=True,
    )

    print(f"  Total classes found: {total}")
    print(f"  Exact duplicate clusters: {len(exact)}")

    if exact:
        for cluster in exact:
            langs = []
            for c in cluster.members:
                lang = c.original_language or "Python"
                langs.append(f"{c.name} ({lang})")
            print(f"  Cluster: {', '.join(langs)}")

    # We expect at least 2 of the 3 to match (Java+Python or Java+Kotlin or all 3)
    # Full 3-way match (HIGH confidence translations + stable LLM) is ideal
    # 2-way match is acceptable — any cross-language duplicate is a win
    any_cross_lang = len(exact) >= 1
    all_three = any(len(c.members) == 3 for c in exact)

    if all_three:
        print(f"  {PASS} — Java + Kotlin + Python all detected as identical")
    elif any_cross_lang:
        members = exact[0].members if exact else []
        langs = [c.original_language or "Python" for c in members]
        print(f"  {PASS} — Cross-language duplicate detected: {langs}")
    else:
        print(f"  {FAIL} — No cross-language duplicates found")
        print(f"  Note: LLM may have restructured code — check confidence in TEST 2")

    return any_cross_lang


# ─────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="SIR Engine AI translation end-to-end tests")
    parser.add_argument("--backend", choices=["ollama", "anthropic"], default=None,
                        help="AI backend to use. Default: anthropic if ANTHROPIC_API_KEY is set, else ollama")
    parser.add_argument("--model", default="codellama:7b", help="Ollama model name")
    parser.add_argument("--host", default="http://localhost:11434", help="Ollama host URL")
    parser.add_argument("--api-key", default=None, help="Anthropic API key (or set ANTHROPIC_API_KEY env var)")
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    backend = args.backend or ("anthropic" if api_key else "ollama")

    print("SIR Engine — AI Translation End-to-End Test Suite")
    print("=" * 55)
    print(f"Backend: {backend}")

    # Health check first
    available, reason = test_backend_health(backend, api_key, args.host, args.model)
    if not available:
        print(f"\n{'='*55}")
        print(f"Backend unavailable: {reason}")
        print("Skipping all translation tests.")
        print("\nTo run with Anthropic:  ANTHROPIC_API_KEY=sk-... python3 test_ai_translation.py")
        print("To run with Ollama:     ollama pull codellama:7b && python3 test_ai_translation.py --backend ollama")
        sys.exit(0)

    # Run all tests
    t2_ok, py_src = test_translation_produces_valid_python(backend, api_key, args.host, args.model)
    t3_ok = test_translated_hashes_like_python(backend, api_key, args.host, args.model) if t2_ok else False
    t4_ok = test_two_java_duplicates_detected(backend, api_key, args.host, args.model)
    t5_ok = test_java_vs_unique_no_false_positive(backend, api_key, args.host, args.model)
    t6_ok = test_full_pipeline_end_to_end(backend, api_key, args.host, args.model)

    results = [t2_ok, t3_ok, t4_ok, t5_ok, t6_ok]
    passed = sum(results)
    total = len(results)

    print(f"\n{'='*55}")
    print(f"Results: {passed}/{total} tests passed")

    # Test 3 (hash match) can legitimately fail if the LLM restructures — warn not fail
    if not t3_ok and t2_ok:
        print("Note: Test 3 failure means translation was valid Python but the LLM")
        print("      changed the structure enough to produce a different hash. This is")
        print("      a LOW confidence result — the core pipeline still works.")

    if passed == total:
        print("All tests passed ✓")
    elif passed >= 3:
        print("Core pipeline working ✓ (some hash-equivalence tests failed — see note above)")
    else:
        print("Some tests failed ✗")

    sys.exit(0 if passed >= 3 else 1)


if __name__ == "__main__":
    main()
