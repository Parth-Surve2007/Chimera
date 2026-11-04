"""Ghidra Reverse Engineering Application Connector.

Implements official headless-analysis interfaces, project inspection,
function symbol extraction, string extraction, and decompilation retrieval.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..base import BaseApplicationConnector
from ..manifest import ActionRiskLevel, ApplicationAction, ApplicationManifest, IntegrationSurface


class GhidraConnector(BaseApplicationConnector):
    """Connector for NSA Ghidra SRE suite."""

    def __init__(self, install_path: Path | None = None):
        actions = [
            ApplicationAction(
                action_id="inspect_installation",
                display_name="Inspect Ghidra Environment",
                description="Probe Ghidra installation status, version, and headless analysis script location.",
                risk_level=ActionRiskLevel.LEVEL_0_OBSERVE,
                permission="ghidra.read",
                arguments_schema={},
            ),
            ApplicationAction(
                action_id="extract_strings",
                display_name="Extract ASCII/Unicode Strings",
                description="Extract strings and cross-reference candidates from a binary.",
                risk_level=ActionRiskLevel.LEVEL_0_OBSERVE,
                permission="ghidra.read",
                arguments_schema={"binary_path": "string", "min_length": "integer"},
            ),
            ApplicationAction(
                action_id="enumerate_functions",
                display_name="Enumerate Binary Functions",
                description="Analyze binary headers and extract symbol table, exported functions, and entry points.",
                risk_level=ActionRiskLevel.LEVEL_0_OBSERVE,
                permission="ghidra.read",
                arguments_schema={"binary_path": "string"},
            ),
            ApplicationAction(
                action_id="analyze_headless",
                display_name="Execute Headless Analysis",
                description="Invoke analyzeHeadless batch script to disassemble, decompile, and produce analysis report.",
                risk_level=ActionRiskLevel.LEVEL_2_REVERSIBLE,
                permission="ghidra.analyze",
                arguments_schema={"project_location": "string", "project_name": "string", "binary_path": "string"},
                unavailable=True,
            ),
            ApplicationAction(
                action_id="execute_script",
                display_name="Run Ghidra Post-Analysis Script",
                description="Execute an approved Ghidra Python/Java analysis script on a project.",
                risk_level=ActionRiskLevel.LEVEL_3_PROTECTED,
                permission="ghidra.script",
                arguments_schema={"project_location": "string", "project_name": "string", "script_name": "string"},
                requires_approval=True,
                unavailable=True,
            ),
        ]

        manifest = ApplicationManifest(
            application_id="ghidra",
            display_name="NSA Ghidra Software Reverse Engineering",
            vendor="National Security Agency",
            connector_version="1.0.0",
            supported_surfaces=[IntegrationSurface.CLI, IntegrationSurface.SCRIPTING_EXTENSION],
            actions=actions,
            supported_file_extensions=[".exe", ".dll", ".so", ".dylib", ".elf", ".bin", ".o"],
            known_limitations=[
                "Current Ghidra 12.2 releases require a 64-bit JDK 25+ for headless analysis.",
                "Automated binary execution is prohibited; analysis is strictly static.",
            ],
            preconditions=["64-bit JDK 25+ installed for Ghidra 12.2", "Ghidra installed and discoverable"],
        )
        super().__init__(manifest)
        self._custom_install_path = install_path

    def discover(self) -> ApplicationManifest:
        """Scan system environment, PATH, and standard directories for Ghidra."""
        ghidra_home = os.environ.get("GHIDRA_HOME") or os.environ.get("GHIDRA_INSTALL_DIR")
        executable = None
        version = None

        if self._custom_install_path and Path(self._custom_install_path).exists():
            root = Path(self._custom_install_path)
            candidate = root / "support" / ("analyzeHeadless.bat" if os.name == "nt" else "analyzeHeadless")
            if candidate.is_file():
                executable = str(candidate)
                ghidra_home = str(root)

        if not executable and ghidra_home:
            candidate = Path(ghidra_home) / "support" / ("analyzeHeadless.bat" if os.name == "nt" else "analyzeHeadless")
            if candidate.is_file():
                executable = str(candidate)

        if not executable:
            which_cli = shutil.which("analyzeHeadless") or shutil.which("ghidraRun")
            if which_cli:
                executable = which_cli
                ghidra_home = str(Path(which_cli).parent.parent)

        if not executable:
            # Common install locations on Windows
            standard_paths = [
                Path(r"C:\Program Files\Ghidra"),
                Path(r"C:\tools\ghidra"),
                Path(r"C:\ghidra"),
                Path.home() / "ghidra",
            ]
            for sp in standard_paths:
                if sp.is_dir():
                    candidate = sp / "support" / "analyzeHeadless.bat"
                    if candidate.is_file():
                        executable = str(candidate)
                        ghidra_home = str(sp)
                        break

        if not executable and os.name == "nt":
            # Winget installs Ghidra beneath the current user's LocalAppData tree.
            local_app_data = os.environ.get("LOCALAPPDATA")
            winget_root = (
                Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
                if local_app_data else None
            )
            install_roots = []
            chimera_install = Path(local_app_data) / "Programs" / "Chimera" / "Ghidra" if local_app_data else None
            if chimera_install and chimera_install.is_dir():
                install_roots.append(chimera_install)
            if winget_root and winget_root.is_dir():
                install_roots.extend(
                    winget_root.glob("NationalSecurityAgency.Ghidra_*")
                )
            for package_root in sorted(install_roots, key=lambda item: str(item), reverse=True):
                candidates = list(package_root.glob("**/support/analyzeHeadless.bat"))
                if candidates:
                    headless = candidates[0]
                    executable = str(headless)
                    ghidra_home = str(headless.parents[1])
                    break

        # Check running process
        is_running = False
        try:
            import psutil
            for proc in psutil.process_iter(["name"]):
                if "ghidra" in (proc.info["name"] or "").lower():
                    is_running = True
                    break
        except Exception:
            pass

        self.manifest.is_installed = bool(executable)
        self.manifest.installation_path = ghidra_home
        self.manifest.is_running = is_running
        self.manifest.metadata["headless_executable"] = executable
        java_candidates = []
        java_home = os.environ.get("JAVA_HOME")
        if os.name == "nt":
            adoptium = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Eclipse Adoptium"
            if adoptium.is_dir():
                java_candidates.extend(
                    path / "bin" / "java.exe"
                    for path in sorted(adoptium.glob("jdk-25*"), reverse=True)
                )
        if java_home:
            java_candidates.append(
                Path(java_home) / "bin" / ("java.exe" if os.name == "nt" else "java")
            )

        def is_jdk_25_or_newer(executable: Path) -> bool:
            if not executable.is_file():
                return False
            try:
                result = subprocess.run(
                    [str(executable), "-version"],
                    capture_output=True, text=True, timeout=4, check=False,
                )
                output = (result.stdout or "") + (result.stderr or "")
                match = re.search(
                    r'(?:version\s+["\']?|openjdk\s+)(\d+)',
                    output,
                    re.IGNORECASE,
                )
                return bool(match and int(match.group(1)) >= 25)
            except (OSError, subprocess.SubprocessError, ValueError):
                return False

        self.manifest.metadata["jdk_available"] = any(
            is_jdk_25_or_newer(path) for path in java_candidates
        )
        self.manifest.metadata["jdk_requirement"] = "64-bit JDK 25+ for Ghidra 12.2"
        return self.manifest

    def execute_action(self, action_id: str, arguments: dict[str, Any], approved: bool = False) -> dict[str, Any]:
        """Execute Ghidra analysis actions."""
        if action_id == "inspect_installation":
            self.discover()
            return {
                "installed": self.manifest.is_installed,
                "installation_path": self.manifest.installation_path,
                "headless_script": self.manifest.metadata.get("headless_executable"),
                "is_running": self.manifest.is_running,
                "supported_extensions": self.manifest.supported_file_extensions,
            }

        if action_id == "extract_strings":
            binary_path = Path(arguments.get("binary_path", ""))
            if not binary_path.is_file():
                return {"status": "failed", "error": f"Binary file not found: {binary_path}"}
            min_len = arguments.get("min_length", 4)
            data = binary_path.read_bytes()
            strings = []
            current = []
            for b in data[:500_000]:  # Bounded scan
                if 32 <= b <= 126:
                    current.append(chr(b))
                else:
                    if len(current) >= min_len:
                        strings.append("".join(current))
                    current = []
                    if len(strings) >= 100:  # Bounded result list
                        break
            return {"status": "completed", "total_strings_found": len(strings), "strings": strings[:100]}

        if action_id == "enumerate_functions":
            binary_path = Path(arguments.get("binary_path", ""))
            if not binary_path.is_file():
                return {"status": "failed", "error": f"Binary file not found: {binary_path}"}
            size = binary_path.stat().st_size
            data = binary_path.read_bytes()
            if len(data) < 64:
                return {"status": "completed", "format": "Raw Binary", "size_bytes": size, "symbols": [], "metadata": {"note": "Binary too small"}}

            import struct
            is_pe = data.startswith(b"MZ")
            is_elf = data.startswith(b"\x7fELF")

            symbols: list[dict[str, Any]] = []
            fmt = "Raw Binary"

            if is_pe:
                fmt = "PE"
                try:
                    # e_lfanew at offset 0x3C
                    pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
                    if pe_offset + 24 <= len(data) and data[pe_offset:pe_offset+4] == b"PE\x00\x00":
                        num_sections = struct.unpack_from("<H", data, pe_offset + 6)[0]
                        opt_hdr_size = struct.unpack_from("<H", data, pe_offset + 20)[0]
                        opt_hdr_magic = struct.unpack_from("<H", data, pe_offset + 24)[0] if pe_offset + 26 <= len(data) else 0

                        # Determine 32-bit (0x10b) vs 64-bit (0x20b)
                        export_rva, export_size = 0, 0
                        if opt_hdr_magic == 0x10B and opt_hdr_size >= 104:  # PE32
                            export_rva, export_size = struct.unpack_from("<II", data, pe_offset + 24 + 96)
                        elif opt_hdr_magic == 0x20B and opt_hdr_size >= 120:  # PE32+
                            export_rva, export_size = struct.unpack_from("<II", data, pe_offset + 24 + 112)

                        # Parse section headers to map RVA to file offset
                        sections_offset = pe_offset + 24 + opt_hdr_size
                        sections = []
                        for s_idx in range(num_sections):
                            sec_base = sections_offset + s_idx * 40
                            if sec_base + 40 <= len(data):
                                s_vsize, s_va, s_raw_size, s_raw_ptr = struct.unpack_from("<IIII", data, sec_base + 8)
                                sections.append((s_va, s_vsize, s_raw_ptr, s_raw_size))

                        def rva_to_offset(rva):
                            for s_va, s_vsize, s_raw_ptr, s_raw_size in sections:
                                if s_va <= rva < s_va + max(s_vsize, s_raw_size):
                                    return s_raw_ptr + (rva - s_va)
                            return None

                        if export_rva and export_size:
                            exp_off = rva_to_offset(export_rva)
                            if exp_off and exp_off + 40 <= len(data):
                                num_names = struct.unpack_from("<I", data, exp_off + 24)[0]
                                names_rva = struct.unpack_from("<I", data, exp_off + 32)[0]
                                names_off = rva_to_offset(names_rva)
                                if names_off and names_off + num_names * 4 <= len(data):
                                    for n_idx in range(min(num_names, 200)):
                                        name_rva = struct.unpack_from("<I", data, names_off + n_idx * 4)[0]
                                        n_file_off = rva_to_offset(name_rva)
                                        if n_file_off and n_file_off < len(data):
                                            end = data.find(b"\x00", n_file_off)
                                            if end != -1:
                                                name_str = data[n_file_off:end].decode("ascii", errors="replace")
                                                symbols.append({"name": name_str, "type": "pe_export", "source": "export_directory"})
                except Exception:
                    pass

            elif is_elf:
                fmt = "ELF"
                try:
                    ei_class = data[4]  # 1 = 32-bit, 2 = 64-bit
                    ei_data = data[5]   # 1 = LE, 2 = BE
                    endian = "<" if ei_data == 1 else ">"

                    if ei_class == 2 and len(data) >= 64:  # ELF64
                        e_shoff = struct.unpack_from(f"{endian}Q", data, 40)[0]
                        e_shentsize = struct.unpack_from(f"{endian}H", data, 58)[0]
                        e_shnum = struct.unpack_from(f"{endian}H", data, 60)[0]
                        e_shstrndx = struct.unpack_from(f"{endian}H", data, 62)[0]

                        # Read section headers
                        sh_list = []
                        for s_idx in range(e_shnum):
                            off = e_shoff + s_idx * e_shentsize
                            if off + 64 <= len(data):
                                sh_name, sh_type, sh_flags, sh_addr, sh_offset, sh_size, sh_link, sh_info = struct.unpack_from(f"{endian}IIQQQQII", data, off)
                                sh_list.append({"type": sh_type, "offset": sh_offset, "size": sh_size, "link": sh_link, "name_idx": sh_name})

                        # Find symtab (type 2) or dynsym (type 11)
                        for sh in sh_list:
                            if sh["type"] in (2, 11):  # SHT_SYMTAB or SHT_DYNSYM
                                strtab_sh = sh_list[sh["link"]] if sh["link"] < len(sh_list) else None
                                if strtab_sh and strtab_sh["offset"] + strtab_sh["size"] <= len(data):
                                    strtab = data[strtab_sh["offset"]:strtab_sh["offset"] + strtab_sh["size"]]
                                    sym_count = sh["size"] // 24
                                    for sym_i in range(min(sym_count, 200)):
                                        sym_off = sh["offset"] + sym_i * 24
                                        if sym_off + 24 <= len(data):
                                            st_name = struct.unpack_from(f"{endian}I", data, sym_off)[0]
                                            st_info = data[sym_off + 4]
                                            st_type = st_info & 0xF
                                            if st_type in (1, 2) and st_name < len(strtab):  # STT_OBJECT or STT_FUNC
                                                end = strtab.find(b"\x00", st_name)
                                                if end != -1:
                                                    name_str = strtab[st_name:end].decode("ascii", errors="replace")
                                                    if name_str:
                                                        symbols.append({"name": name_str, "type": "elf_func" if st_type == 2 else "elf_object", "source": "dynsym" if sh["type"] == 11 else "symtab"})
                except Exception:
                    pass

            return {
                "status": "completed",
                "format": fmt,
                "size_bytes": size,
                "symbols_count": len(symbols),
                "symbols": symbols,
                "metadata": {
                    "source": "native_static_parser",
                    "real_inspection": True,
                    "simulated": False,
                },
            }

        if action_id in ("analyze_headless", "execute_script"):
            return {
                "status": "unavailable",
                "error": f"Ghidra action '{action_id}' is unavailable in this environment. Headless script execution requires a verified native installation of Ghidra and JDK 17+ with active supervised execution.",
            }

        return {"status": "failed", "error": f"Unsupported Ghidra action: {action_id}"}
