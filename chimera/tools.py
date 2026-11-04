"""Trusted, bounded workspace operations; no shell or generated-code execution."""
from dataclasses import dataclass, asdict
from pathlib import Path
import fnmatch, hashlib, json, ntpath, os

MAX_FILE = 2_000_000
CREATE_FILE_SUFFIXES = {
    ".txt", ".md", ".json", ".csv", ".py", ".pyw", ".js", ".jsx", ".mjs",
    ".ts", ".tsx", ".html", ".htm", ".css", ".scss", ".sql", ".xml",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".log", ".bat", ".cmd",
    ".ps1", ".sh", ".c", ".h", ".cpp", ".hpp", ".java", ".kt", ".rs",
    ".go", ".r", ".ipynb", ".svg",
}
MAX_RESULTS = 200
MAX_SCAN_FILES = 2000
MAX_SCAN_DIRS = 5000
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
    "create_file": {"description": "Create a new plain-text or source file inside the attached workspace without overwriting existing files. Supports .txt and common text/source extensions. The path must be workspace-relative.", "arguments": {"path": "string", "content": "string"}, "permission": "execution.write"},
    "create_document": {"description": "Create a real Microsoft Word .docx document in the attached workspace. Provide a workspace-relative .docx path, document title, and content. Does not require Microsoft Word to be installed.", "arguments": {"path": "string", "title": "string", "content": "string"}, "permission": "execution.write"},
    "create_pdf": {"description": "Create a genuine, parseable PDF in the attached workspace from supplied text. Requires explicit Build Mode approval; verifies extracted text, page count, file size and SHA-256 before returning.", "arguments": {"path": "string", "title": "string", "content": "string"}, "permission": "execution.write"},
    "computer.list_applications": {"description": "List currently visible Windows applications and observed window identities", "arguments": {}, "permission": "computer.observe"},
    "computer.list_installed_applications": {"description": "List installed, user-launchable Windows Start-menu apps, including packaged applications and their registered application IDs", "arguments": {}, "permission": "computer.observe"},
    "computer.list_windows": {"description": "List observed visible top-level windows and process identities", "arguments": {}, "permission": "computer.observe"},
    "computer.inspect_controls": {"description": "Inspect controls belonging to a freshly observed operator-authorized application", "arguments": {"session_id": "string", "hwnd": "integer"}, "permission": "computer.observe"},
    "computer.inspect_elements": {"description": "Take a fresh, bounded UI Automation observation of one authorized application window. Optional hwnd selects an exact top-level window returned by computer.inspect_window; depth is 0-8 and limit is 1-500. Returns element names, control types, bounds, patterns, and short-lived reference_id values. For custom drawing surfaces such as Paint, identify the largest enabled, onscreen Pane/Custom/Document-like child with a usable rectangle; never guess coordinates from the whole window.", "arguments": {"session_id": "string", "hwnd": "integer", "depth": "integer", "limit": "integer"}, "permission": "computer.observe"},
    "computer.inspect_window": {"description": "Inspect windows belonging to an operator-authorized application", "arguments": {"session_id": "string"}, "permission": "computer.observe"},
    "computer.inspect_pattern_support": {"description": "Inspect supported semantic patterns for an observed element", "arguments": {"session_id": "string", "reference_id": "string"}, "permission": "computer.observe"},
    "computer.launch": {"description": "Launch an installed Windows app after approval. executable accepts an exact .exe path, a known desktop app name, or a Start-menu app name such as Paint, Notepad, Photos, Calculator, or Terminal, including packaged apps.", "arguments": {"executable": "string"}, "permission": "computer.launch"},
    "computer.screenshot": {"description": "Capture a fresh window observation from an operator-authorized application", "arguments": {"session_id": "string", "hwnd": "integer", "path": "string"}, "permission": "computer.screenshot"},
    "computer.inspect_visual_target": {"description": "Capture a freshly observed, foreground, authorized UIA element and describe it with an explicitly configured local vision GGUF. Requires screenshot approval. Returns visual observations only; it does not prove any action completed.", "arguments": {"session_id": "string", "reference_id": "string", "question": "string"}, "permission": "computer.screenshot"},
    "computer.cua_list_apps": {"description": "List applications reported by the optional Cua Driver background desktop backend. Read-only; does not authorize any application.", "arguments": {}, "permission": "computer.observe"},
    "computer.cua_list_windows": {"description": "List visible CUA windows for an exact Chimera-authorized application process. Uses the existing process/session authorization.", "arguments": {"session_id": "string"}, "permission": "computer.observe"},
    "computer.cua_inspect_window": {"description": "Take a fresh Cua Driver accessibility-tree snapshot of one exact authorized window. Returns snapshot_ref-scoped element_token values, roles, labels and window-local element frames. Take a new snapshot after every action.", "arguments": {"session_id": "string", "window_id": "integer"}, "permission": "computer.observe"},
    "computer.cua_capture_window": {"description": "Capture a screenshot and accessibility snapshot of one exact authorized CUA window. Requires screenshot approval. Optionally include a precise question to inspect the captured screenshot with the configured local vision model. Screenshot pixels are window-local; any coordinates must come from this fresh snapshot.", "arguments": {"session_id": "string", "window_id": "integer", "question": "string"}, "permission": "computer.screenshot"},
    "computer.cua_click": {"description": "Click one exact CUA element_token or screenshot pixel in the authorized window using BACKGROUND delivery only. Requires approval. Provide snapshot_ref and either element_token OR x/y. A refusal may recommend foreground escalation, but this tool never escalates automatically. Always take a new snapshot afterwards.", "arguments": {"session_id": "string", "window_id": "integer", "snapshot_ref": "string", "element_token": "string", "x": "number", "y": "number", "button": "left|right|middle", "count": "integer"}, "permission": "computer.input"},
    "computer.cua_click_foreground": {"description": "Retry one exact click with explicit FOREGROUND delivery after Cua Driver refused background delivery. This is a separate, higher-risk action that briefly activates the approved window and restores the previous foreground. Requires fresh explicit approval; never call unless a preceding CUA result explicitly reported background_unavailable.", "arguments": {"session_id": "string", "window_id": "integer", "snapshot_ref": "string", "element_token": "string", "x": "number", "y": "number", "button": "left|right|middle", "count": "integer"}, "permission": "computer.input.foreground"},
    "computer.cua_draw_path": {"description": "Draw 1-16 disconnected paths on a fresh CUA canvas element. Each path has 2-64 normalized points (0.02-0.98), at most 256 points total. Requires approval. It uses background-only drag segments, re-snapshots after every segment, stops on refusal/target change and reports pixel-diff evidence only. Provide the snapshot_ref and exact canvas element_token from the same window snapshot.", "arguments": {"session_id": "string", "window_id": "integer", "snapshot_ref": "string", "canvas_element_token": "string", "strokes": "array", "duration_ms": "integer", "steps": "integer"}, "permission": "computer.input"},
    "computer.close_window": {"description": "Gracefully send WM_CLOSE to one exact, freshly observed window belonging to an authorized application. Requires operator approval. Does not forcibly terminate the process; unsaved-work dialogs may require manual handoff.", "arguments": {"session_id": "string", "hwnd": "integer"}, "permission": "computer.close"},
    "computer.invoke_element": {"description": "Invoke a freshly observed UI Automation control such as a button or menu item after approval", "arguments": {"session_id": "string", "reference_id": "string"}, "permission": "computer.invoke"},
    "computer.set_value": {"description": "Set a freshly observed editable UI Automation ValuePattern control after approval", "arguments": {"session_id": "string", "reference_id": "string", "value": "string"}, "permission": "computer.input"},
    "computer.read_text": {"description": "Read bounded text from a freshly observed Text, Value, or accessible Name pattern", "arguments": {"session_id": "string", "reference_id": "string"}, "permission": "computer.observe"},
    "computer.get_range_value": {"description": "Read current/min/max/step values from a UI Automation RangeValue control", "arguments": {"session_id": "string", "reference_id": "string"}, "permission": "computer.observe"},
    "computer.set_range_value": {"description": "Set a UI Automation slider/spinner/range value within its declared bounds after approval", "arguments": {"session_id": "string", "reference_id": "string", "value": "number"}, "permission": "computer.input"},
    "computer.read_grid": {"description": "Read a bounded snapshot of a UI Automation Grid or DataGrid (up to 20 rows and 10 columns)", "arguments": {"session_id": "string", "reference_id": "string"}, "permission": "computer.observe"},
    "computer.scroll_element": {"description": "Scroll an observed UI Automation scroll container by line or page after approval", "arguments": {"session_id": "string", "reference_id": "string", "direction": "string", "amount": "string", "count": "integer"}, "permission": "computer.input"},
    "computer.scroll_into_view": {"description": "Scroll an observed UI Automation item into view after approval", "arguments": {"session_id": "string", "reference_id": "string"}, "permission": "computer.input"},
    "computer.toggle_element": {"description": "Toggle a freshly observed UI Automation element after approval", "arguments": {"session_id": "string", "reference_id": "string"}, "permission": "computer.input"},
    "computer.select_element": {"description": "Select a freshly observed UI Automation selection item after approval", "arguments": {"session_id": "string", "reference_id": "string"}, "permission": "computer.input"},
    "computer.expand_element": {"description": "Expand a freshly observed UI Automation item after approval", "arguments": {"session_id": "string", "reference_id": "string"}, "permission": "computer.input"},
    "computer.collapse_element": {"description": "Collapse a freshly observed UI Automation item after approval", "arguments": {"session_id": "string", "reference_id": "string"}, "permission": "computer.input"},
    "computer.visual_action": {"description": "Approved scoped click, draw, text, key or scroll on a fresh UIA target. multi_stroke draws multiple pen-up paths in one approval.", "arguments": {"session_id": "string", "reference_id": "string", "action": "string", "points": "array", "strokes": "array", "button": "string", "duration_ms": "integer", "text": "string", "key": "string", "direction": "string", "steps": "integer"}, "permission": "computer.input"},
    "computer.inspect_pattern_support": {"description": "Inspect supported patterns for a freshly observed UIA element", "arguments": {"session_id": "string", "reference_id": "string"}, "permission": "computer.observe"},
}
CAPABILITY = {"capability_id": "builtin.workspace", "version": "1.0.0", "description": "Bounded operations over a user-selected workspace", "permissions": ["workspace.read", "execution.write"], "operations": OPERATIONS}

class WorkspaceTools:
    def __init__(self, workspace: Path, output: Path):
        self.workspace = Path(workspace).resolve(); self.output = Path(output).resolve()
        if not self.workspace.is_dir(): raise ValueError("workspace must be an existing directory")
        if self.output != self.workspace and self.workspace not in self.output.parents:
            raise ValueError("artifact output directory must remain inside the selected workspace")
    @staticmethod
    def _known_desktop_directory() -> Path:
        """Resolve the user's configured Windows Desktop, including redirection."""
        if os.name == "nt":
            try:
                import winreg
                key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                    value, _kind = winreg.QueryValueEx(key, "Desktop")
                return Path(os.path.expandvars(value)).expanduser().resolve()
            except (OSError, ImportError):
                pass
        return (Path.home() / "Desktop").resolve()

    def _path(self, value, root=None):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("path must be a non-empty string")
        base = root or self.workspace
        # With the system drive as workspace, "Desktop/..." is a known-folder
        # alias rather than a literal C:\\Desktop directory. Do not apply
        # this alias inside project workspaces or non-workspace output roots.
        relative = Path(value)
        if (
            root is None
            and self.workspace == Path(self.workspace.anchor).resolve()
            and relative.parts
            and relative.parts[0].casefold() == "desktop"
        ):
            desktop = self._known_desktop_directory()
            target = (desktop.joinpath(*relative.parts[1:])).resolve()
        else:
            target = (base / value).resolve()
        if target != self.workspace and self.workspace not in target.parents:
            raise ValueError("path escapes the selected workspace")
        return target

    def _iter_files(self, root: Path, recursive=True, max_files=MAX_SCAN_FILES):
        """Walk a workspace without following links or failing on protected folders."""
        pending = [Path(root)]
        visited_dirs = 0
        yielded = 0
        while pending and visited_dirs < MAX_SCAN_DIRS and yielded < max_files:
            current = pending.pop()
            visited_dirs += 1
            try:
                with os.scandir(current) as iterator:
                    entries = sorted(iterator, key=lambda item: item.name.casefold())
            except OSError:
                continue
            child_dirs = []
            for entry in entries:
                try:
                    if entry.is_symlink():
                        continue
                    if os.name == "nt":
                        entry_attributes = getattr(
                            entry.stat(follow_symlinks=False), "st_file_attributes", 0
                        )
                        # Windows junctions and mount-point reparse targets can
                        # loop back into the drive. Never recurse through them.
                        if entry_attributes & 0x400:  # FILE_ATTRIBUTE_REPARSE_POINT
                            continue
                    if entry.is_dir(follow_symlinks=False):
                        if recursive and visited_dirs + len(pending) + len(child_dirs) < MAX_SCAN_DIRS:
                            child_dirs.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        yield Path(entry.path)
                        yielded += 1
                        if yielded >= max_files:
                            return
                except OSError:
                    continue
            pending.extend(reversed(child_dirs))

    def run(self, operation_id: str, arguments: dict, approved=False) -> OperationResult:
        if operation_id not in OPERATIONS: return OperationResult(operation_id, "failed", {}, "unknown operation")
        if not isinstance(arguments, dict): return OperationResult(operation_id, "failed", {}, "arguments must be an object")
        try:
            if operation_id == "list_files":
                root = self._path(arguments.get("relative", "."))
                if not root.is_dir():
                    raise ValueError("the requested listing path is not a directory")
                items = sorted(
                    self._iter_files(root, recursive=arguments.get("recursive", True), max_files=MAX_RESULTS)
                )
                return OperationResult(operation_id, "completed", {
                    "files": [p.relative_to(self.workspace).as_posix() for p in items],
                    "truncated": len(items) >= MAX_RESULTS,
                })
            if operation_id == "inspect_file":
                path = self._path(arguments.get("path")); stat = path.stat()
                if stat.st_size > MAX_FILE: raise ValueError("file exceeds the inspect limit")
                return OperationResult(operation_id, "completed", {"path": str(path.relative_to(self.workspace)), "bytes": stat.st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "is_text_candidate": path.suffix.casefold() in {".py", ".js", ".ts", ".json", ".md", ".txt", ".toml", ".yaml", ".yml"}})
            if operation_id == "search_files":
                pattern = arguments.get("pattern")
                if not isinstance(pattern, str) or not pattern.strip() or len(pattern) > 240:
                    raise ValueError("pattern must be a non-empty filename pattern of at most 240 characters")
                matches = []
                for candidate in self._iter_files(self.workspace, recursive=True, max_files=MAX_SCAN_FILES):
                    if fnmatch.fnmatch(candidate.name.casefold(), pattern.casefold()):
                        matches.append(candidate.relative_to(self.workspace).as_posix())
                        if len(matches) >= MAX_RESULTS:
                            break
                return OperationResult(operation_id, "completed", {
                    "files": matches,
                    "truncated": len(matches) >= MAX_RESULTS,
                })
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
                for candidate in self._iter_files(
                    self.workspace, recursive=True, max_files=MAX_SCAN_FILES
                ):
                    if len(hits) >= MAX_RESULTS or scanned_files >= MAX_SCAN_FILES or scanned_bytes >= MAX_SCAN_BYTES:
                        break
                    if candidate.suffix.casefold() not in text_suffixes:
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

            if operation_id == "create_file":
                relative = arguments.get("path")
                content = arguments.get("content")
                if not isinstance(relative, str) or not relative.strip():
                    raise ValueError("file path must be a non-empty workspace-relative path")
                normalized_path = relative.replace("\\", "/")
                windows_drive, _ = ntpath.splitdrive(relative)
                if normalized_path.startswith("/") or Path(relative).is_absolute() or windows_drive:
                    raise ValueError("file path must be workspace-relative; use a name such as hello.txt")
                if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_FILE:
                    raise ValueError("file content exceeds the 2 MB limit")
                target = self._path(relative)
                if target.suffix.casefold() not in CREATE_FILE_SUFFIXES:
                    raise ValueError(
                        "new files must use a supported text/source extensions "
                        f"({', '.join(sorted(CREATE_FILE_SUFFIXES))})"
                    )
                if target.exists():
                    raise ValueError("file already exists; refusing to overwrite")
                if not approved:
                    return OperationResult(operation_id, "awaiting_approval", {}, "explicit Build Mode write approval is required")
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("x", encoding="utf-8", newline="") as stream:
                    stream.write(content)
                return OperationResult(operation_id, "completed", {
                    "path": target.relative_to(self.workspace).as_posix(),
                    "bytes": len(content.encode("utf-8")),
                    "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                })
            if operation_id == "create_document":
                relative = arguments.get("path")
                title = arguments.get("title")
                content = arguments.get("content")
                if not isinstance(relative, str) or not relative.strip():
                    raise ValueError("document path must be a non-empty workspace-relative path")
                normalized_path = relative.replace("\\", "/")
                windows_drive, _ = ntpath.splitdrive(relative)
                if normalized_path.startswith("/") or Path(relative).is_absolute() or windows_drive:
                    raise ValueError("document path must be workspace-relative; use a name such as Desktop/Report.docx")
                if not isinstance(title, str) or not title.strip():
                    raise ValueError("document title must be a non-empty string")
                if not isinstance(content, str) or not content.strip() or len(content.encode("utf-8")) > MAX_FILE:
                    raise ValueError("document content must be non-empty and no larger than 2 MB")
                target = self._path(relative)
                if target.suffix.casefold() != ".docx":
                    raise ValueError("Word documents must use the .docx extension; use create_file only for plain text files")
                if target.exists():
                    raise ValueError("file already exists; refusing to overwrite")
                if not approved:
                    return OperationResult(operation_id, "awaiting_approval", {}, "explicit Build Mode document-write approval is required")
                target.parent.mkdir(parents=True, exist_ok=True)
                from .documents import create_docx
                create_docx(target, title=title.strip(), content=content)
                data = target.read_bytes()
                return OperationResult(operation_id, "completed", {
                    "path": target.relative_to(self.workspace).as_posix(),
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "format": "docx",
                })
            if operation_id == "create_pdf":
                relative = arguments.get("path")
                title = arguments.get("title")
                content = arguments.get("content")
                if not isinstance(relative, str) or not relative.strip():
                    raise ValueError("PDF path must be a non-empty workspace-relative path")
                normalized_path = relative.replace("\\", "/")
                windows_drive, _ = ntpath.splitdrive(relative)
                if normalized_path.startswith("/") or Path(relative).is_absolute() or windows_drive:
                    raise ValueError("PDF path must be workspace-relative; use Desktop/Report.pdf")
                if not isinstance(title, str) or not title.strip():
                    raise ValueError("PDF title must be a non-empty string")
                if not isinstance(content, str) or not content.strip() or len(content.encode("utf-8")) > MAX_FILE:
                    raise ValueError("PDF content must be non-empty and no larger than 2 MB")
                target = self._path(relative)
                if target.suffix.casefold() != ".pdf":
                    raise ValueError("PDF output must use the .pdf extension")
                if target.exists():
                    raise ValueError("file already exists; refusing to overwrite")
                if not approved:
                    return OperationResult(operation_id, "awaiting_approval", {}, "explicit Build Mode PDF-write approval is required")
                from .pdf_documents import create_pdf
                verification = create_pdf(target, title=title.strip(), content=content)
                data = target.read_bytes()
                return OperationResult(operation_id, "completed", {
                    "path": target.relative_to(self.workspace).as_posix(),
                    **verification,
                    "sha256": hashlib.sha256(data).hexdigest(),
                })
        except (OSError, UnicodeError, ValueError) as exc: return OperationResult(operation_id, "failed", {}, str(exc))

def tool_catalog() -> dict: return {"capability": CAPABILITY, "operations": OPERATIONS}
