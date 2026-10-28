"""Behavioral verification boundary; never falls back to host execution."""
from dataclasses import dataclass
from pathlib import Path
import shutil, subprocess

@dataclass(frozen=True)
class BehaviorResult:
    status: str
    diagnostics: str
    evidence: dict

@dataclass(frozen=True)
class ReadinessReport:
    ready: bool
    checks: dict
    blockers: tuple[str, ...]

class VirtualBoxBackend:
    """Preflight-only VirtualBox backend; execution remains fail-closed until a dedicated guest is configured."""
    def __init__(self, vm_name: str | None = None, manage: str | None = None, expected_uuid: str | None = None, guest_control_verified: bool = False, runner_verified: bool = False, baseline_verified: bool = False):
        self.vm_name = vm_name or __import__("os").environ.get("CHIMERA_VBOX_VM", "ChimeraBehaviorVM")
        self.manage = manage or shutil.which("VBoxManage") or r"C:\Program Files\Oracle\VirtualBox\VBoxManage.exe"
        self.expected_uuid = expected_uuid or __import__("os").environ.get("CHIMERA_VBOX_UUID", "7c288538-3af3-4ccd-be0e-d8f3de1c5923")
        self.guest_control_verified = guest_control_verified; self.runner_verified = runner_verified; self.baseline_verified = baseline_verified

    def inspect(self) -> dict:
        if not Path(self.manage).is_file(): return {"available": False, "reason": "VBoxManage.exe is not installed"}
        try:
            listed = subprocess.run([self.manage, "list", "vms"], capture_output=True, text=True, timeout=5)
            info = subprocess.run([self.manage, "showvminfo", self.vm_name, "--machinereadable"], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"available": False, "reason": f"VirtualBox preflight failed: {exc}"}
        if info.returncode != 0: return {"available": False, "reason": f"dedicated VM {self.vm_name!r} is not registered", "registered_vms": listed.stdout.splitlines()}
        values = dict(line.split("=", 1) for line in info.stdout.splitlines() if "=" in line)
        network = [values.get(f"nic{i}") for i in range(1, 9)]
        safe = values.get("VMState") == '"poweroff"' and all(n in (None, '"none"') for n in network) and values.get("clipboard", '"disabled"') == '"disabled"' and values.get("draganddrop", '"disabled"') == '"disabled"'
        return {"available": safe, "vm": self.vm_name, "state": values.get("VMState"), "network": network, "clipboard": values.get("clipboard"), "draganddrop": values.get("draganddrop"), "reason": "ready" if safe else "VM must be powered off with all NICs disabled and integrations disabled"}

    def readiness(self) -> ReadinessReport:
        if not Path(self.manage).is_file(): return ReadinessReport(False, {"vboxmanage": "unavailable"}, ("VBoxManage is unavailable",))
        try:
            info = subprocess.run([self.manage, "showvminfo", self.vm_name, "--machinereadable"], capture_output=True, text=True, timeout=5)
            props = subprocess.run([self.manage, "guestproperty", "enumerate", self.vm_name], capture_output=True, text=True, timeout=5)
            snaps = subprocess.run([self.manage, "snapshot", self.vm_name, "list"], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired) as exc: return ReadinessReport(False, {"vboxmanage": "error"}, (f"VirtualBox inspection failed: {exc}",))
        if info.returncode != 0: return ReadinessReport(False, {"vm": "unregistered"}, (f"VM {self.vm_name!r} is not registered",))
        values = dict(line.split("=", 1) for line in info.stdout.splitlines() if "=" in line)
        actual_uuid = values.get("UUID", "").strip('"'); network = [values.get(f"nic{i}", '"none"').strip('"') for i in range(1, 9)]
        snapshot_text = (snaps.stdout + snaps.stderr).lower()
        checks = {"expected_uuid": actual_uuid == self.expected_uuid, "powered_off": values.get("VMState") == '"poweroff"', "network_disabled": all(n in ("none", "null") for n in network), "clipboard_disabled": values.get("clipboard", '"unknown"').strip('"') == "disabled", "dragdrop_disabled": values.get("draganddrop", '"unknown"').strip('"') == "disabled", "usb_disabled": values.get("usb", '"unknown"').strip('"') == "off", "audio_disabled": values.get("audio", '"unknown"').strip('"') == "none", "shared_folders_absent": not any("SharedFolder" in line for line in info.stdout.splitlines()), "guest_control_verified": self.guest_control_verified and bool(props.stdout.strip()), "runner_verified": self.runner_verified, "baseline_verified": self.baseline_verified, "snapshot_present": "no snapshots" not in snapshot_text}
        blockers = tuple(name.replace("_", " ") for name, passed in checks.items() if not passed)
        return ReadinessReport(not blockers, checks, blockers)

def available_backend() -> tuple[str | None, str]:
    vbox = VirtualBoxBackend(); inspection = vbox.inspect()
    if inspection.get("available"): return "virtualbox", "dedicated VirtualBox preflight passed"
    if shutil.which("docker"):
        try:
            probe = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=5)
            if probe.returncode == 0: return "docker", "Docker engine is available"
        except (OSError, subprocess.TimeoutExpired): pass
    return None, "No supported isolation backend is available. VirtualBox is installed but no dedicated hardened Chimera VM passed preflight; Windows Sandbox is unsupported on Home editions; Docker is not installed or running. Configure CHIMERA_VBOX_VM with a dedicated VM whose networking and integrations are disabled."

def verify_behavior(candidate_path: Path, candidate_digest: str, approved: bool, approved_digest: str | None) -> BehaviorResult:
    if not candidate_path.is_dir(): return BehaviorResult("blocked", "candidate package is missing", {})
    if not approved: return BehaviorResult("blocked", "explicit execution approval is required for this candidate", {})
    if approved_digest != candidate_digest: return BehaviorResult("blocked", "execution approval does not match the current manifest digest", {"approved_digest": approved_digest, "current_digest": candidate_digest})
    report = VirtualBoxBackend().readiness()
    if not report.ready: return BehaviorResult("blocked", "behavioral verification blocked: " + ", ".join(report.blockers), {"readiness": report.checks, "host_fallback": False})
    backend, detail = available_backend()
    if not backend: return BehaviorResult("blocked", detail, {"readiness": report.checks, "host_fallback": False})
    return BehaviorResult("blocked", "backend discovery succeeded but no supported JSON utility contract is configured for this candidate", {"backend": backend, "network": "disabled", "host_fallback": False})
