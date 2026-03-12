#!/usr/bin/env python3
"""
sir_mcp.py — SIR Engine MCP Server

Exposes SIR Engine as a Model Context Protocol server so any
MCP-compatible AI coding tool (Claude Code, Cursor, etc.) can
call it as a tool during code generation.

TOOLS EXPOSED:
  sir_check_function   — check if a function already exists semantically
  sir_check_class      — check if a class already exists semantically
  sir_scan_codebase    — build/refresh the hash index for a codebase
  sir_health           — get health score for a codebase
  sir_duplicates       — list all duplicate function clusters with file/line details
  sir_merge_preview    — preview what a merge would do (no file writes)
  sir_merge_apply      — apply a merge after user confirmation

USAGE:
  # Start the MCP server
  python3 sir_mcp.py --path ./my_project

  # Add to Claude Code config (~/.claude/claude_desktop_config.json):
  {
    "mcpServers": {
      "sir-engine": {
        "command": "python3",
        "args": ["/path/to/SIR_MAIN/sir_mcp.py", "--path", "/path/to/your/project"]
      }
    }
  }

PROTOCOL:
  Communicates via stdin/stdout using JSON-RPC 2.0.
  Each message is a JSON object terminated by newline.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

# ─────────────────────────────────────────────
#  Bootstrap — find sir1.py and sir2_core.py
# ─────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

try:
    from sir1 import AlphaRenamer, encode_to_sir, CanonConfig, sir_hash
    SIR1_AVAILABLE = True
except ImportError:
    SIR1_AVAILABLE = False

try:
    from sir2_core import extract_classes, scan_for_class_dupes
    SIR2_AVAILABLE = True
except ImportError:
    SIR2_AVAILABLE = False


# ─────────────────────────────────────────────
#  Hash index — in-memory store of codebase hashes
# ─────────────────────────────────────────────

class HashIndex:
    """
    In-memory index of all function and class hashes in a codebase.
    Built once on startup, refreshed on demand.
    """
    def __init__(self):
        self.functions: Dict[str, List[Dict]] = {}  # hash -> [{file, name, lineno}]
        self.classes: Dict[str, List[Dict]] = {}     # hash -> [{file, name, lineno}]
        self.path: Optional[Path] = None
        self.total_functions = 0
        self.total_classes = 0

    # Directories whose contents should not be indexed (outputs, demos, caches)
    IGNORE_DIRS = {
        "restored_funcs", "restored_funcs3", "restored_one",
        "bench", "demo_scan", "__pycache__", "node_modules",
        ".sir_cache",
    }

    def build(self, root: Path) -> Dict:
        """Scan a directory and build the hash index."""
        self.path = root
        self.functions = {}
        self.classes = {}
        self.total_functions = 0
        self.total_classes = 0

        py_files = [
            f for f in root.rglob("*.py")
            if not any(part in self.IGNORE_DIRS for part in f.parts)
        ]

        for f in py_files:
            # Skip test files and the SIR Engine files themselves
            if f.name.startswith("test_") or f.name.startswith("sir_") or f.name == "sir1.py":
                continue
            try:
                src = f.read_text(encoding="utf-8", errors="replace")
                self._index_functions(src, str(f))
                self._index_classes(src, str(f))
            except Exception:
                continue

        return {
            "files_scanned": len(py_files),
            "total_functions": self.total_functions,
            "total_classes": self.total_classes,
            "path": str(root),
        }

    def _index_functions(self, src: str, filepath: str):
        if not SIR1_AVAILABLE:
            return
        try:
            tree = ast.parse(src)
        except SyntaxError:
            return

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            seg = ast.get_source_segment(src, node)
            if not seg:
                continue
            try:
                cfg = CanonConfig(mode="semantic")
                sir = encode_to_sir(seg, cfg)
                h = sir_hash(sir)
                if h not in self.functions:
                    self.functions[h] = []
                self.functions[h].append({
                    "file": filepath,
                    "name": node.name,
                    "lineno": node.lineno,
                })
                self.total_functions += 1
            except Exception:
                continue

    def _index_classes(self, src: str, filepath: str):
        if not SIR2_AVAILABLE:
            return
        classes = extract_classes(src, filepath)
        for cls in classes:
            h = cls.class_hash
            if h not in self.classes:
                self.classes[h] = []
            self.classes[h].append({
                "file": filepath,
                "name": cls.name,
                "lineno": cls.lineno,
            })
            self.total_classes += 1

    def check_function(self, src: str) -> Dict:
        """Check if a function snippet already exists in the index."""
        if not SIR1_AVAILABLE:
            return {"error": "sir1.py not available"}
        try:
            cfg = CanonConfig(mode="semantic")
            sir = encode_to_sir(src, cfg)
            h = sir_hash(sir)
            matches = self.functions.get(h, [])
            return {
                "hash": h[:20],
                "duplicate_found": len(matches) > 0,
                "matches": matches,
                "message": _format_function_message(matches) if matches else "No duplicate found.",
            }
        except Exception as e:
            return {"error": str(e)}

    def check_class(self, src: str, filename: str = "<input>") -> Dict:
        """Check if a class snippet already exists in the index."""
        if not SIR2_AVAILABLE:
            return {"error": "sir2_core.py not available"}
        try:
            classes = extract_classes(src, filename)
            if not classes:
                return {"error": "No class found in provided source."}
            cls = classes[0]
            matches = self.classes.get(cls.class_hash, [])
            return {
                "hash": cls.class_hash[:20],
                "duplicate_found": len(matches) > 0,
                "matches": matches,
                "message": _format_class_message(cls.name, matches) if matches else "No duplicate found.",
            }
        except Exception as e:
            return {"error": str(e)}

    def health(self) -> Dict:
        """Return health score for the indexed codebase."""
        total = self.total_functions
        dupes = sum(len(v) - 1 for v in self.functions.values() if len(v) > 1)
        score = max(0, 100 - int((dupes / total * 100) if total > 0 else 0))
        return {
            "health_score": score,
            "total_functions": total,
            "duplicate_functions": dupes,
            "total_classes": self.total_classes,
            "duplicate_classes": sum(len(v) - 1 for v in self.classes.values() if len(v) > 1),
        }


# ─────────────────────────────────────────────
#  Duplicate cluster & merge helpers
# ─────────────────────────────────────────────

def _get_duplicate_clusters(index: "HashIndex") -> List[Dict]:
    """Return all function clusters with 2+ occurrences, sorted by count desc."""
    clusters = []
    for h, occurrences in index.functions.items():
        if len(occurrences) > 1:
            clusters.append({
                "hash": h[:20],
                "count": len(occurrences),
                "occurrences": sorted(occurrences, key=lambda o: (o["file"], o["lineno"])),
            })
    clusters.sort(key=lambda c: c["count"], reverse=True)
    return clusters


def _file_to_module(filepath: str, root: Path) -> str:
    """Convert an absolute file path to a dotted Python module path relative to root."""
    try:
        rel = Path(filepath).resolve().relative_to(root.resolve())
        return str(rel).replace(os.sep, ".").removesuffix(".py")
    except ValueError:
        return Path(filepath).stem


def _build_merge_plan(clusters: List[Dict], root: Path) -> List[Dict]:
    """
    For each cluster designate the first occurrence (alphabetically by file) as
    canonical. Return a list of per-cluster plans describing what will change.
    """
    plan = []
    for i, cluster in enumerate(clusters):
        canonical = cluster["occurrences"][0]
        canonical_module = _file_to_module(canonical["file"], root)
        duplicates = cluster["occurrences"][1:]

        removals = []
        for dup in duplicates:
            alias = dup["name"] if dup["name"] != canonical["name"] else None
            if alias:
                import_stmt = f"from {canonical_module} import {canonical['name']} as {dup['name']}"
            else:
                import_stmt = f"from {canonical_module} import {canonical['name']}"
            removals.append({
                "file": dup["file"],
                "function_name": dup["name"],
                "lineno": dup["lineno"],
                "import_statement": import_stmt,
            })

        plan.append({
            "cluster_index": i,
            "hash": cluster["hash"],
            "canonical": {
                "file": canonical["file"],
                "name": canonical["name"],
                "lineno": canonical["lineno"],
                "module": canonical_module,
            },
            "removals": removals,
        })
    return plan


def _remove_function_from_source(src: str, func_name: str, lineno: int):
    """Remove a function definition from source. Returns (new_src, removed_src)."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return src, ""

    lines = src.splitlines(keepends=True)
    for node in ast.walk(tree):
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == func_name
                and node.lineno == lineno):
            end = node.end_lineno
            removed = "".join(lines[node.lineno - 1:end])
            # Drop the function lines; also drop one trailing blank line if present
            after = lines[end:]
            if after and after[0].strip() == "":
                after = after[1:]
            new_lines = lines[:node.lineno - 1] + after
            return "".join(new_lines), removed

    return src, ""


def _add_import_to_source(src: str, module: str, func_name: str, alias: str = None) -> str:
    """Insert an import line after the last top-level import in the file."""
    if alias:
        import_line = f"from {module} import {func_name} as {alias}\n"
    else:
        import_line = f"from {module} import {func_name}\n"

    try:
        tree = ast.parse(src)
    except SyntaxError:
        return import_line + src

    last_import_line = 0
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            last_import_line = node.lineno

    lines = src.splitlines(keepends=True)
    new_lines = lines[:last_import_line] + [import_line] + lines[last_import_line:]
    return "".join(new_lines)


def _apply_merge_plan(plan: List[Dict]) -> Dict:
    """Apply the merge plan: remove duplicate bodies and add imports."""
    files_modified = []
    errors = []

    for cluster_plan in plan:
        canonical = cluster_plan["canonical"]
        for removal in cluster_plan["removals"]:
            filepath = removal["file"]
            func_name = removal["function_name"]
            lineno = removal["lineno"]
            import_stmt = removal["import_statement"]
            alias = func_name if func_name != canonical["name"] else None

            try:
                src = Path(filepath).read_text(encoding="utf-8")
                new_src, removed = _remove_function_from_source(src, func_name, lineno)
                if not removed:
                    errors.append(f"Could not locate {func_name}() at line {lineno} in {filepath}")
                    continue
                new_src = _add_import_to_source(new_src, canonical["module"], canonical["name"], alias)
                Path(filepath).write_text(new_src, encoding="utf-8")
                files_modified.append({
                    "file": filepath,
                    "removed_function": func_name,
                    "added_import": import_stmt,
                })
            except Exception as e:
                errors.append(f"Failed to modify {filepath}: {e}")

    return {
        "files_modified": files_modified,
        "errors": errors,
        "success": len(errors) == 0,
    }


def _format_function_message(matches: List[Dict]) -> str:
    lines = ["⚠️  DUPLICATE DETECTED — this function already exists semantically:"]
    for m in matches:
        lines.append(f"  → {m['name']}() in {m['file']} at line {m['lineno']}")
    lines.append("Consider reusing the existing function instead of creating a new one.")
    return "\n".join(lines)


def _format_class_message(new_name: str, matches: List[Dict]) -> str:
    lines = [f"⚠️  DUPLICATE DETECTED — {new_name} already exists semantically:"]
    for m in matches:
        lines.append(f"  → {m['name']} in {m['file']} at line {m['lineno']}")
    lines.append("Consider reusing or extending the existing class.")
    return "\n".join(lines)


# ─────────────────────────────────────────────
#  MCP Protocol — JSON-RPC 2.0 over stdin/stdout
# ─────────────────────────────────────────────

def send(obj: Dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def send_result(request_id: Any, result: Any) -> None:
    send({"jsonrpc": "2.0", "id": request_id, "result": result})


def send_error(request_id: Any, code: int, message: str) -> None:
    send({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


TOOLS = [
    {
        "name": "sir_check_function",
        "description": (
            "Check if a Python function already exists semantically in the codebase. "
            "Detects duplicates even if variable names, argument names, or formatting differ. "
            "Call this before writing a new function to avoid creating semantic duplicates."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "The full Python function source code to check."
                }
            },
            "required": ["source"]
        }
    },
    {
        "name": "sir_check_class",
        "description": (
            "Check if a Python class already exists semantically in the codebase. "
            "Detects duplicate classes even if class name, method names, or attribute names differ. "
            "Call this before writing a new class to avoid creating semantic duplicates."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "The full Python class source code to check."
                }
            },
            "required": ["source"]
        }
    },
    {
        "name": "sir_scan_codebase",
        "description": (
            "Scan the project codebase and build or refresh the semantic hash index. "
            "Call this at the start of a session or after significant changes to the codebase."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Optional path to scan. Defaults to the path set at server startup."
                }
            },
            "required": []
        }
    },
    {
        "name": "sir_health",
        "description": (
            "Get the semantic health score for the codebase. "
            "Returns a 0-100 score based on duplicate function ratio, "
            "plus counts of duplicate functions and classes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "sir_duplicates",
        "description": (
            "List all duplicate function clusters in the indexed codebase. "
            "Each cluster shows every occurrence of the same semantic function: "
            "file path, function name, and line number. "
            "Call sir_scan_codebase first to ensure the index is fresh."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "sir_merge_preview",
        "description": (
            "Preview what a merge would do for duplicate function clusters. "
            "Shows which occurrence becomes canonical and which files would have their "
            "duplicate body removed and replaced with an import. "
            "Does NOT modify any files. Always call this before sir_merge_apply "
            "and present the plan to the user for confirmation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "cluster_index": {
                    "type": "integer",
                    "description": "0-based index of a specific cluster to preview. Omit to preview all clusters."
                }
            },
            "required": []
        }
    },
    {
        "name": "sir_merge_apply",
        "description": (
            "Apply a merge for duplicate function clusters: removes duplicate function bodies "
            "and replaces them with imports from the canonical location. "
            "IMPORTANT: You MUST call sir_merge_preview first and get EXPLICIT user confirmation "
            "before calling this tool. This modifies source files on disk."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "cluster_index": {
                    "type": "integer",
                    "description": "0-based index of a specific cluster to merge. Omit to merge all clusters."
                },
                "confirmed": {
                    "type": "boolean",
                    "description": "Must be true. Signals that the user has reviewed the preview and approved the merge."
                }
            },
            "required": ["confirmed"]
        }
    }
]


def handle_request(req: Dict, index: HashIndex, default_path: Path) -> None:
    req_id = req.get("id")
    method = req.get("method", "")
    params = req.get("params", {})

    # MCP handshake
    if method == "initialize":
        send_result(req_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "sir-engine", "version": "2.0.0"}
        })
        return

    if method == "notifications/initialized":
        return

    if method == "tools/list":
        send_result(req_id, {"tools": TOOLS})
        return

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})

        if tool_name == "sir_check_function":
            src = tool_args.get("source", "")
            if not src:
                send_error(req_id, -32602, "source is required")
                return
            result = index.check_function(src)
            send_result(req_id, {
                "content": [{"type": "text", "text": json.dumps(result, indent=2)}]
            })

        elif tool_name == "sir_check_class":
            src = tool_args.get("source", "")
            if not src:
                send_error(req_id, -32602, "source is required")
                return
            result = index.check_class(src)
            send_result(req_id, {
                "content": [{"type": "text", "text": json.dumps(result, indent=2)}]
            })

        elif tool_name == "sir_scan_codebase":
            path_str = tool_args.get("path", "")
            scan_path = Path(path_str).resolve() if path_str else default_path
            if not scan_path.exists():
                send_error(req_id, -32602, f"Path not found: {scan_path}")
                return
            result = index.build(scan_path)
            send_result(req_id, {
                "content": [{"type": "text", "text": json.dumps(result, indent=2)}]
            })

        elif tool_name == "sir_health":
            result = index.health()
            send_result(req_id, {
                "content": [{"type": "text", "text": json.dumps(result, indent=2)}]
            })

        elif tool_name == "sir_duplicates":
            clusters = _get_duplicate_clusters(index)
            result = {
                "total_clusters": len(clusters),
                "clusters": clusters,
            }
            send_result(req_id, {
                "content": [{"type": "text", "text": json.dumps(result, indent=2)}]
            })

        elif tool_name == "sir_merge_preview":
            if index.path is None:
                send_error(req_id, -32602, "No codebase indexed. Call sir_scan_codebase first.")
                return
            clusters = _get_duplicate_clusters(index)
            ci = tool_args.get("cluster_index")
            if ci is not None:
                if ci < 0 or ci >= len(clusters):
                    send_error(req_id, -32602, f"cluster_index {ci} out of range (0–{len(clusters)-1})")
                    return
                clusters = [clusters[ci]]
            plan = _build_merge_plan(clusters, index.path)
            result = {
                "clusters_to_merge": len(plan),
                "total_files_to_modify": sum(len(p["removals"]) for p in plan),
                "plan": plan,
            }
            send_result(req_id, {
                "content": [{"type": "text", "text": json.dumps(result, indent=2)}]
            })

        elif tool_name == "sir_merge_apply":
            if not tool_args.get("confirmed"):
                send_error(req_id, -32602, "confirmed must be true. Call sir_merge_preview first and get user approval.")
                return
            if index.path is None:
                send_error(req_id, -32602, "No codebase indexed. Call sir_scan_codebase first.")
                return
            clusters = _get_duplicate_clusters(index)
            ci = tool_args.get("cluster_index")
            if ci is not None:
                if ci < 0 or ci >= len(clusters):
                    send_error(req_id, -32602, f"cluster_index {ci} out of range (0–{len(clusters)-1})")
                    return
                clusters = [clusters[ci]]
            plan = _build_merge_plan(clusters, index.path)
            result = _apply_merge_plan(plan)
            # Refresh index after merge
            if result["success"]:
                index.build(index.path)
            send_result(req_id, {
                "content": [{"type": "text", "text": json.dumps(result, indent=2)}]
            })

        else:
            send_error(req_id, -32601, f"Unknown tool: {tool_name}")
        return

    # Unknown method — ignore notifications, error on requests
    if req_id is not None:
        send_error(req_id, -32601, f"Method not found: {method}")


# ─────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="SIR Engine MCP Server")
    ap.add_argument("--path", default=".", help="Project path to index (default: current directory)")
    args = ap.parse_args()

    default_path = Path(args.path).expanduser().resolve()

    # Build index on startup
    index = HashIndex()
    if default_path.exists():
        index.build(default_path)
        sys.stderr.write(f"SIR Engine MCP Server ready — indexed {index.total_functions} functions, {index.total_classes} classes in {default_path}\n")
        sys.stderr.flush()
    else:
        sys.stderr.write(f"Warning: path {default_path} not found. Use sir_scan_codebase tool to index.\n")
        sys.stderr.flush()

    # Main loop — read JSON-RPC messages from stdin
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            handle_request(req, index, default_path)
        except json.JSONDecodeError as e:
            send_error(None, -32700, f"Parse error: {e}")
        except Exception as e:
            send_error(None, -32603, f"Internal error: {e}")


if __name__ == "__main__":
    main()
