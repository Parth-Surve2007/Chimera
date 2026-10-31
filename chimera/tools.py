"""Trusted, bounded workspace operations; no shell or generated-code execution."""
from dataclasses import dataclass, asdict
from pathlib import Path
import hashlib, json, os

MAX_FILE = 2_000_000
MAX_RESULTS = 200

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
        self.workspace = Path(workspace).resolve(); self.output = Path(output).resolve(); self.output.mkdir(parents=True, exist_ok=True)
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
                return OperationResult(operation_id, "completed", {"files": [str(p.relative_to(self.workspace)) for p in items if p.is_file()][:MAX_RESULTS]})
            if operation_id == "inspect_file":
                path = self._path(arguments.get("path")); stat = path.stat(); return OperationResult(operation_id, "completed", {"path": str(path.relative_to(self.workspace)), "bytes": stat.st_size, "sha256": hashlib.sha256(path.read_bytes()[:MAX_FILE]).hexdigest(), "is_text_candidate": path.suffix.casefold() in {".py", ".js", ".ts", ".json", ".md", ".txt", ".toml", ".yaml", ".yml"}})
            if operation_id == "search_files":
                pattern = arguments.get("pattern");
                if not isinstance(pattern, str): raise ValueError("pattern must be a string")
                return OperationResult(operation_id, "completed", {"files": [str(p.relative_to(self.workspace)) for p in self.workspace.rglob(pattern) if p.is_file()][:MAX_RESULTS]})
            if operation_id == "read_text":
                path = self._path(arguments.get("path")); data = path.read_bytes()
                if len(data) > MAX_FILE: raise ValueError("file exceeds the read limit")
                return OperationResult(operation_id, "completed", {"path": str(path.relative_to(self.workspace)), "content": data.decode("utf-8")})
            if operation_id == "search_text":
                query = arguments.get("query");
                if not isinstance(query, str) or not query: raise ValueError("query must be non-empty")
                hits = []
                for path in self.workspace.rglob("*"):
                    if len(hits) >= MAX_RESULTS or not path.is_file() or path.suffix.casefold() not in {".py", ".js", ".ts", ".json", ".md", ".txt", ".toml", ".yaml", ".yml"}: continue
                    try:
                        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                            if query.casefold() in line.casefold(): hits.append({"path": str(path.relative_to(self.workspace)), "line": number, "text": line[:500]})
                    except (OSError, UnicodeError): continue
                return OperationResult(operation_id, "completed", {"matches": hits})
            if operation_id == "create_artifact":
                if not approved: return OperationResult(operation_id, "awaiting_approval", {}, "explicit write approval is required")
                name, content = arguments.get("name"), arguments.get("content")
                if not isinstance(name, str) or Path(name).name != name or Path(name).suffix.casefold() not in {".md", ".txt", ".json"}: raise ValueError("artifact name must be a simple .md, .txt, or .json filename")
                if not isinstance(content, str) or len(content) > MAX_FILE: raise ValueError("artifact content exceeds the limit")
                target = self._path(name, self.output)
                if target.exists(): raise ValueError("artifact already exists; refusing to overwrite")
                target.write_text(content, encoding="utf-8"); return OperationResult(operation_id, "completed", {"artifact": str(target), "bytes": len(content.encode("utf-8"))})
        except (OSError, UnicodeError, ValueError) as exc: return OperationResult(operation_id, "failed", {}, str(exc))

def tool_catalog() -> dict: return {"capability": CAPABILITY, "operations": OPERATIONS}
