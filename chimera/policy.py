from pathlib import Path

class PermissionErrorPolicy(ValueError): pass

def validate_path(path: Path, workspace: Path) -> Path:
    path, workspace = Path(path).resolve(), Path(workspace).resolve()
    if path != workspace and workspace not in path.parents: raise PermissionErrorPolicy("path escapes task workspace")
    return path

def check_permissions(requested: list[str], allowed: set[str] | None = None):
    allowed = allowed or set()
    denied = set(requested) - allowed
    if denied: raise PermissionErrorPolicy(f"permissions require approval: {', '.join(sorted(denied))}")

