"""Trusted, bounded workspace operations; no shell or generated-code execution."""
from dataclasses import dataclass, asdict
from pathlib import Path
import hashlib, json, os

MAX_FILE = 2_000_000
MAX_RESULTS = 200
MAX_SCAN_FILES = 2000
MAX_SCAN_BYTES = 50_000_000

@dataclass(frozen=True)
class OperationResult:
    operation_id: str
    status: str
    data: dict
    error: str | None = None

OPERATIONS = {
    "list_files": {"description": "List files under the selected workspace", "arguments": {"relative": "string", "recursive": "boolean"}, "permission": "workspace.read"},
    "inspect_file": {"description": "Inspect metadata for one workspace file", "arguments": {"path": "string"}, "permission": "workspace.read"},
    "search_files": {"description": "Find files by name under the workspace", "arguments": {"pattern": "string"}, "permission": "workspace.read"},
    "search_text": {"description": "Search text in common source and text files", "arguments": {"query": "string"}, "permission": "workspace.read"},
    "read_text": {"description": "Read a bounded UTF-8 text file", "arguments": {"path": "string"}, "permission": "workspace.read"},
    "create_artifact": {"description": "Create a new Markdown or text artifact in the execution output directory", "arguments": {"name": "string", "content": "string"}, "permission": "execution.write"},
    "computer.list_applications": {"description": "List currently visible Windows applications and observed window identities", "arguments": {}, "permission": "computer.observe"},
    "computer.list_windows": {"description": "List observed visible top-level windows and process identities", "arguments": {}, "permission": "computer.observe"},
    "computer.inspect_controls": {"description": "Inspect controls belonging to a freshly observed application window", "arguments": {"hwnd": "integer"}, "permission": "computer.observe"},
    "computer.inspect_elements": {"description": "Inspect accessibility elements of a freshly observed application window", "arguments": {"hwnd": "integer"}, "permission": "computer.observe"},
    "computer.inspect_window": {"description": "Inspect a freshly observed application window", "arguments": {"hwnd": "integer"}, "permission": "computer.observe"},
    "computer.inspect_pattern_support": {"description": "Inspect supported semantic patterns for an observed element", "arguments": {"session_id": "string", "reference_id": "string"}, "permission": "computer.observe"},
    "computer.focus": {"description": "Focus a freshly observed application window", "arguments": {"hwnd": "integer"}, "permission": "computer.observe"},
    "computer.focus_window": {"description": "Focus a freshly observed application window", "arguments": {"hwnd": "integer"}, "permission": "computer.observe"},
    "computer.launch": {"description": "Launch an allowlisted Windows application after approval", "arguments": {"executable": "string"}, "permission": "computer.launch"},
    "computer.type_text": {"description": "Type text into a freshly observed control after approval", "arguments": {"hwnd": "integer", "text": "string"}, "permission": "computer.input"},
    "computer.screenshot": {"description": "Capture an observed application window into the selected workspace", "arguments": {"hwnd": "integer", "path": "string"}, "permission": "computer.screenshot"},
    "computer.close": {"description": "Close a freshly observed application window after approval", "arguments": {"hwnd": "integer"}, "permission": "computer.close"},
    "computer.invoke_element": {"description": "Invoke a freshly observed UIA element reference", "arguments": {"session_id": "string", "reference_id": "string"}, "permission": "computer.invoke"},
    "computer.set_value": {"description": "Set a freshly observed UIA value element", "arguments": {"session_id": "string", "reference_id": "string", "value": "string"}, "permission": "computer.input"},
    "computer.read_text": {"description": "Read text from a freshly observed UIA element", "arguments": {"session_id": "string", "reference_id": "string"}, "permission": "computer.observe"},
    "computer.focus_element": {"description": "Focus a freshly observed UIA element", "arguments": {"session_id": "string", "reference_id": "string"}, "permission": "computer.observe"},
    "computer.toggle_element": {"description": "Toggle a freshly observed UIA element", "arguments": {"session_id": "string", "reference_id": "string"}, "permission": "computer.input"},
    "computer.select_element": {"description": "Select a freshly observed UIA element", "arguments": {"session_id": "string", "reference_id": "string"}, "permission": "computer.input"},
    "computer.expand_element": {"description": "Expand a freshly observed UIA element", "arguments": {"session_id": "string", "reference_id": "string"}, "permission": "computer.input"},
    "computer.collapse_element": {"description": "Collapse a freshly observed UIA element", "arguments": {"session_id": "string", "reference_id": "string"}, "permission": "computer.input"},
    "computer.inspect_pattern_support": {"description": "Inspect supported patterns for a freshly observed UIA element", "arguments": {"session_id": "string", "reference_id": "string"}, "permission": "computer.observe"},
}
CAPABILITY = {"capability_id": "builtin.workspace", "version": "1.0.0", "description": "Bounded operations over a user-selected workspace", "permissions": ["workspace.read", "execution.write"], "operations": OPERATIONS}

class WorkspaceTools:
    def __init__(self, workspace: Path, output: Path):
        self.workspace = Path(workspace).resolve(); self.output = Path(output).resolve()
        if not self.workspace.is_dir(): raise ValueError("workspace must be an existing directory")
        if self.output != self.workspace and self.workspace not in self.output.parents:
            raise ValueError("artifact output directory must remain inside the selected workspace")
    def _path(self, value, root=None):
        if not isinstance(value, str) or not value.strip(): raise ValueError("path must be a non-empty string")
        base = root or self.workspace; target = (base / value).resolve()
        if target != base and base not in target.parents: raise ValueError("path escapes the selected workspace")
        return target
    def run(self, operation_id: str, arguments: dict, approved=False) -> OperationResult:
        if operation_id not in OPERATIONS: return OperationResult(operation_id, "failed", {}, "unknown operation")
        if not isinstance(arguments, dict): return OperationResult(operation_id, "failed", {}, "arguments must be an object")
        try:
            if operation_id == "list_files":
                root = self._path(arguments.get("relative", ".")); items = sorted(root.rglob("*") if arguments.get("recursive", True) else root.glob("*"))
                return OperationResult(operation_id, "completed", {"files": [str(p.relative_to(self.workspace)) for p in items if p.is_file() and not p.is_symlink()][:MAX_RESULTS]})
            if operation_id == "inspect_file":
                path = self._path(arguments.get("path")); stat = path.stat()
                if stat.st_size > MAX_FILE: raise ValueError("file exceeds the inspect limit")
                return OperationResult(operation_id, "completed", {"path": str(path.relative_to(self.workspace)), "bytes": stat.st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "is_text_candidate": path.suffix.casefold() in {".py", ".js", ".ts", ".json", ".md", ".txt", ".toml", ".yaml", ".yml"}})
            if operation_id == "search_files":
                pattern = arguments.get("pattern");
                if not isinstance(pattern, str): raise ValueError("pattern must be a string")
                return OperationResult(operation_id, "completed", {"files": [str(p.relative_to(self.workspace)) for p in self.workspace.rglob(pattern) if p.is_file() and not p.is_symlink()][:MAX_RESULTS]})
            if operation_id == "read_text":
                path = self._path(arguments.get("path")); data = path.read_bytes()
                if len(data) > MAX_FILE: raise ValueError("file exceeds the read limit")
                return OperationResult(operation_id, "completed", {"path": str(path.relative_to(self.workspace)), "content": data.decode("utf-8")})
            if operation_id == "search_text":
                query = arguments.get("query")
                if not isinstance(query, str) or not query or len(query) > 1000:
                    raise ValueError("query must be a non-empty string of at most 1000 characters")
                hits = []
                scanned_files = 0
                scanned_bytes = 0
                text_suffixes = {".py", ".js", ".ts", ".json", ".md", ".txt", ".toml", ".yaml", ".yml"}
                for candidate in self.workspace.rglob("*"):
                    if len(hits) >= MAX_RESULTS or scanned_files >= MAX_SCAN_FILES or scanned_bytes >= MAX_SCAN_BYTES:
                        break
                    if candidate.is_symlink() or not candidate.is_file() or candidate.suffix.casefold() not in text_suffixes:
                        continue
                    try:
                        path = self._path(str(candidate.relative_to(self.workspace)))
                        size = path.stat().st_size
                        if size > MAX_FILE or scanned_bytes + size > MAX_SCAN_BYTES:
                            continue
                        scanned_files += 1
                        scanned_bytes += size
                        with path.open("r", encoding="utf-8") as stream:
                            for number, line in enumerate(stream, 1):
                                if query.casefold() in line.casefold():
                                    hits.append({
                                        "path": str(path.relative_to(self.workspace)),
                                        "line": number,
                                        "text": line.rstrip()[:500],
                                    })
                                    if len(hits) >= MAX_RESULTS:
                                        break
                    except (OSError, UnicodeError, ValueError):
                        continue
                return OperationResult(operation_id, "completed", {
                    "matches": hits,
                    "scanned_files": scanned_files,
                    "scanned_bytes": scanned_bytes,
                    "truncated": scanned_files >= MAX_SCAN_FILES or scanned_bytes >= MAX_SCAN_BYTES or len(hits) >= MAX_RESULTS,
                })
            if operation_id == "create_artifact":
                if not approved: return OperationResult(operation_id, "awaiting_approval", {}, "explicit write approval is required")
                name, content = arguments.get("name"), arguments.get("content")
                if not isinstance(name, str) or Path(name).name != name or Path(name).suffix.casefold() not in {".md", ".txt", ".json"}: raise ValueError("artifact name must be a simple .md, .txt, or .json filename")
                if not isinstance(content, str) or len(content) > MAX_FILE: raise ValueError("artifact content exceeds the limit")
                target = self._path(name, self.output)
                if target.exists(): raise ValueError("artifact already exists; refusing to overwrite")
                self.output.mkdir(parents=True, exist_ok=True)
                with target.open("x", encoding="utf-8") as stream: stream.write(content)
                return OperationResult(operation_id, "completed", {"artifact": str(target), "bytes": len(content.encode("utf-8"))})
        except (OSError, UnicodeError, ValueError) as exc: return OperationResult(operation_id, "failed", {}, str(exc))

def tool_catalog() -> dict: return {"capability": CAPABILITY, "operations": OPERATIONS}
