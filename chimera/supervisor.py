import subprocess, time
from dataclasses import dataclass
from pathlib import Path

@dataclass
class ExecutionResult:
    command: list[str]; exit_code: int | None; stdout: str; stderr: str; duration_ms: int; timed_out: bool = False

class ExecutionSupervisor:
    def run(self, command: list[str], cwd: Path, timeout: float = 30, max_output: int = 20000) -> ExecutionResult:
        start = time.monotonic(); timed_out = False
        try:
            p = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout, shell=False)
            code = p.returncode; out, err = p.stdout, p.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out = True; code = None; out = (exc.stdout or ""); err = (exc.stderr or "") + "timeout"
        return ExecutionResult(command, code, out[:max_output], err[:max_output], int((time.monotonic()-start)*1000), timed_out)

