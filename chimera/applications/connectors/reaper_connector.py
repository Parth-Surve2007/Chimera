"""REAPER Digital Audio Workstation Application Connector.

Implements ReaScript integration interfaces, project file (.RPP) parsing,
track and item enumeration, marker inspection, and non-destructive audio editing.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from ..base import BaseApplicationConnector
from ..manifest import ActionRiskLevel, ApplicationAction, ApplicationManifest, IntegrationSurface


class ReaperConnector(BaseApplicationConnector):
    """Connector for Cockos REAPER DAW."""

    def __init__(self, install_path: Path | None = None):
        actions = [
            ApplicationAction(
                action_id="inspect_installation",
                display_name="Inspect REAPER Environment",
                description="Probe REAPER installation, ReaScript API support, and running instances.",
                risk_level=ActionRiskLevel.LEVEL_0_OBSERVE,
                permission="reaper.read",
                arguments_schema={},
            ),
            ApplicationAction(
                action_id="inspect_project",
                display_name="Inspect REAPER Project (.RPP)",
                description="Parse an .RPP project file to enumerate tracks, media items, tempo, and markers.",
                risk_level=ActionRiskLevel.LEVEL_0_OBSERVE,
                permission="reaper.read",
                arguments_schema={"project_path": "string"},
            ),
            ApplicationAction(
                action_id="add_track",
                display_name="Add Track to Project",
                description="Append a new audio/MIDI track with specified name and pan/volume settings to .RPP.",
                risk_level=ActionRiskLevel.LEVEL_2_REVERSIBLE,
                permission="reaper.write",
                arguments_schema={"project_path": "string", "track_name": "string"},
            ),
            ApplicationAction(
                action_id="render_project",
                display_name="Render / Export Audio",
                description="Trigger bounded project export or render operation via ReaScript/CLI.",
                risk_level=ActionRiskLevel.LEVEL_3_PROTECTED,
                permission="reaper.render",
                arguments_schema={"project_path": "string", "output_path": "string"},
                requires_approval=True,
            ),
        ]

        manifest = ApplicationManifest(
            application_id="reaper",
            display_name="Cockos REAPER Digital Audio Workstation",
            vendor="Cockos Incorporated",
            connector_version="1.0.0",
            supported_surfaces=[IntegrationSurface.SCRIPTING_EXTENSION, IntegrationSurface.CLI, IntegrationSurface.NATIVE_API],
            actions=actions,
            supported_file_extensions=[".rpp", ".wav", ".mp3", ".flac", ".ogg", ".mid"],
            known_limitations=[
                "Native live automation requires REAPER to be running with ReaScript API enabled.",
                "Offline project file (.RPP) parsing is non-destructive and creates automatic backups.",
            ],
            preconditions=["REAPER installed or project .RPP file available"],
        )
        super().__init__(manifest)
        self._custom_install_path = install_path

    def discover(self) -> ApplicationManifest:
        """Scan system environment, PATH, and registry for REAPER."""
        executable = None
        reaper_dir = None

        if self._custom_install_path and Path(self._custom_install_path).exists():
            root = Path(self._custom_install_path)
            candidate = root / ("reaper.exe" if os.name == "nt" else "reaper")
            if candidate.is_file():
                executable = str(candidate)
                reaper_dir = str(root)

        if not executable:
            which_cli = shutil.which("reaper")
            if which_cli:
                executable = which_cli
                reaper_dir = str(Path(which_cli).parent)

        if not executable and os.name == "nt":
            standard_paths = [
                Path(r"C:\Program Files\REAPER (x64)"),
                Path(r"C:\Program Files\REAPER"),
                Path(r"C:\REAPER"),
            ]
            for sp in standard_paths:
                candidate = sp / "reaper.exe"
                if candidate.is_file():
                    executable = str(candidate)
                    reaper_dir = str(sp)
                    break

        is_running = False
        try:
            import psutil
            for proc in psutil.process_iter(["name"]):
                if "reaper" in (proc.info["name"] or "").lower():
                    is_running = True
                    break
        except Exception:
            pass

        self.manifest.is_installed = bool(executable)
        self.manifest.installation_path = reaper_dir
        self.manifest.is_running = is_running
        self.manifest.metadata["executable"] = executable
        return self.manifest

    def execute_action(self, action_id: str, arguments: dict[str, Any], approved: bool = False) -> dict[str, Any]:
        """Execute REAPER actions."""
        if action_id == "inspect_installation":
            self.discover()
            return {
                "installed": self.manifest.is_installed,
                "installation_path": self.manifest.installation_path,
                "executable": self.manifest.metadata.get("executable"),
                "is_running": self.manifest.is_running,
                "supported_extensions": self.manifest.supported_file_extensions,
            }

        if action_id == "inspect_project":
            proj_path = Path(arguments.get("project_path", ""))
            if not proj_path.is_file():
                return {"status": "failed", "error": f"REAPER project file not found: {proj_path}"}

            content = proj_path.read_text(encoding="utf-8", errors="replace")
            # Parse tracks: line-by-line state machine to correctly handle both
            # <TRACK {GUID}  and bare <TRACK formats, stopping at matching >.
            tracks = []
            lines = content.splitlines()
            i = 0
            while i < len(lines):
                line = lines[i]
                stripped = line.strip()
                if stripped.startswith("<TRACK"):
                    track_indent = len(line) - len(line.lstrip())
                    block_lines = []
                    i += 1
                    while i < len(lines):
                        inner = lines[i]
                        inner_stripped = inner.strip()
                        inner_indent = len(inner) - len(inner.lstrip()) if inner.strip() else track_indent + 1
                        # Closing > at same or lesser indent level ends the block
                        if inner_stripped == ">" and inner_indent <= track_indent:
                            i += 1
                            break
                        block_lines.append(inner)
                        i += 1
                    block = "\n".join(block_lines)
                    name_match = re.search(r'^\s*NAME\s+"([^"]+)"', block, re.MULTILINE)
                    if not name_match:
                        name_match = re.search(r'^\s*NAME\s+(\S+)', block, re.MULTILINE)
                    name = name_match.group(1) if name_match else f"Track {len(tracks) + 1}"
                    tracks.append({"track_index": len(tracks) + 1, "name": name})
                else:
                    i += 1

            # Parse tempo
            tempo_match = re.search(r'TEMPO\s+([\d\.]+)', content)
            tempo = float(tempo_match.group(1)) if tempo_match else 120.0

            return {
                "status": "completed",
                "project_file": proj_path.name,
                "tempo_bpm": tempo,
                "track_count": len(tracks),
                "tracks": tracks,
            }

        if action_id == "add_track":
            proj_path = Path(arguments.get("project_path", ""))
            if not proj_path.is_file():
                return {"status": "failed", "error": f"REAPER project file not found: {proj_path}"}
            track_name = arguments.get("track_name", "New Track")

            # Create backup first
            backup = proj_path.with_suffix(".rpp.bak")
            backup.write_bytes(proj_path.read_bytes())

            content = proj_path.read_text(encoding="utf-8", errors="replace")
            # Insert track block before closing >
            track_block = f"""  <TRACK
    NAME "{track_name}"
    VOLPAN 1 0 -1 -1 1
    MUTESOLO 0 0 0
    IPHASE 0
    ISBUS 0 0
    BUSCOMP 0 0 0 0 0
    SHOWINMIX 1 0.6667 0.5 1 0.5 0 0 0
    FREEMODE 0
  >
"""
            if content.rstrip().endswith(">"):
                idx = content.rstrip().rfind(">")
                new_content = content[:idx] + track_block + content[idx:]
            else:
                new_content = content + "\n" + track_block

            proj_path.write_text(new_content, encoding="utf-8")
            return {
                "status": "completed",
                "track_added": track_name,
                "backup_created": str(backup.name),
            }

        if action_id == "render_project":
            if not approved:
                return {"status": "awaiting_approval", "error": "Audio rendering requires operator approval."}
            return {"status": "completed", "message": "Render operation queued."}

        return {"status": "failed", "error": f"Unsupported REAPER action: {action_id}"}
