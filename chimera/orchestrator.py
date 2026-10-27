import uuid
from pathlib import Path
from .audit import AuditRecord, AuditStore
from .models import now_iso
from .policy import validate_path
from .supervisor import ExecutionSupervisor

class Orchestrator:
    def __init__(self, audit: AuditStore, workspace: Path): self.audit, self.workspace = audit, Path(workspace).resolve()
    def run_command(self, task: str, command: list[str]) -> AuditRecord:
        task_id = str(uuid.uuid4()); start = now_iso(); result = ExecutionSupervisor().run(command, validate_path(self.workspace, self.workspace))
        record = AuditRecord(task_id, None, start, now_iso(), command=" ".join(command), exit_code=result.exit_code, error=result.stderr or None, duration_ms=result.duration_ms, final_status="failed" if result.timed_out or result.exit_code else "completed")
        self.audit.record(record); return record

