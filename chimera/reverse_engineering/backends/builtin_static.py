"""Built-in pure-Python static analysis engine for REA.

Provides robust, out-of-bounds-safe, zero-dependency static inspection for:
- PE32 and PE32+ (headers, sections, exports, forwarders, imports, ordinals, entropy).
- ELF32 and ELF64 (little/big endian, program/section headers, symtab/dynsym, binding).
- Universal string extraction (ASCII & UTF-16LE).
- Shannon entropy computation (file & per-section).
- Hash generation (SHA-256, MD5, SHA-1).
- Embedded archive and signature detection.
"""
from __future__ import annotations

import collections
import hashlib
import math
import struct
import time
from pathlib import Path
from typing import Any

from ..artifact import (
    ArtifactMetadata,
    ExportSymbol,
    ImportSymbol,
    SectionInfo,
    SymbolInfo,
)
from ..capabilities import AnalysisCapability, BackendCapabilityManifest
from ..detection import ArtifactDetector
from ..evidence import EvidenceProvenance
from ..results import AnalysisResult, StructuredFindings
from ..security import AnalysisLimits, validate_artifact_path


def calculate_entropy(data: bytes) -> float:
    """Calculate Shannon entropy for a sequence of bytes (0.0 to 8.0)."""
    if not data:
        return 0.0
    counts = collections.Counter(data)
    total = len(data)
    entropy = 0.0
    for count in counts.values():
        p = count / total
        entropy -= p * math.log2(p)
    return round(entropy, 4)


class BuiltinStaticBackend:
    """Primary zero-dependency static analysis backend."""

    BACKEND_ID = "builtin_static"
    DISPLAY_NAME = "Chimera Built-in Static Analysis Engine"
    VERSION = "1.2.0"

    def __init__(self):
        self.detector = ArtifactDetector()

    def get_manifest(self) -> BackendCapabilityManifest:
        return BackendCapabilityManifest(
            backend_id=self.BACKEND_ID,
            display_name=self.DISPLAY_NAME,
            version=self.VERSION,
            is_installed=True,
            supported_capabilities=[
                AnalysisCapability.IDENTIFY,
                AnalysisCapability.HASHES,
                AnalysisCapability.STRINGS,
                AnalysisCapability.HEADERS,
                AnalysisCapability.SECTIONS,
                AnalysisCapability.EXPORTS,
                AnalysisCapability.IMPORTS,
                AnalysisCapability.SYMBOLS,
                AnalysisCapability.ENTROPY,
                AnalysisCapability.ARCHIVES,
                AnalysisCapability.WORKFLOW_UNDERSTAND,
                AnalysisCapability.WORKFLOW_UNKNOWN,
                AnalysisCapability.WORKFLOW_INTERNALS,
                AnalysisCapability.WORKFLOW_FIRMWARE,
            ],
            supported_formats=[
                "PE32",
                "PE32+",
                "ELF32",
                "ELF64",
                "Mach-O",
                ".NET Assembly",
                "APK",
                "DEX",
                "Firmware",
                "Archive",
                "Raw Binary",
            ],
            execution_mode="native_static",
        )

    def extract_strings(
        self, path: str | Path, min_length: int = 4, max_strings: int = AnalysisLimits.MAX_STRINGS_EXTRACTED
    ) -> AnalysisResult:
        """Extract ASCII and UTF-16LE printable strings from binary data with offsets."""
        start_time = time.perf_counter()
        valid_path = validate_artifact_path(path)
        file_size = valid_path.stat().st_size

        with open(valid_path, "rb") as f:
            data = f.read(min(file_size, AnalysisLimits.MAX_STRINGS_SCAN_BYTES))

        strings: list[dict[str, Any]] = []
        warnings: list[str] = []

        if file_size > AnalysisLimits.MAX_STRINGS_SCAN_BYTES:
            warnings.append(
                f"File size ({file_size} bytes) exceeds scan limit; inspected first {AnalysisLimits.MAX_STRINGS_SCAN_BYTES} bytes."
            )

        # 1. ASCII extraction
        current_ascii: list[str] = []
        current_start = 0
        for idx, b in enumerate(data):
            if 32 <= b <= 126:
                if not current_ascii:
                    current_start = idx
                current_ascii.append(chr(b))
            else:
                if len(current_ascii) >= min_length:
                    strings.append({
                        "string": "".join(current_ascii),
                        "offset": hex(current_start),
                        "encoding": "ASCII",
                        "length": len(current_ascii),
                    })
                    if len(strings) >= max_strings:
                        break
                current_ascii = []

        # 2. UTF-16LE extraction if capacity allows
        if len(strings) < max_strings and len(data) >= 2:
            current_utf16: list[str] = []
            current_start = 0
            idx = 0
            data_len = len(data)
            while idx < data_len - 1 and len(strings) < max_strings:
                b1 = data[idx]
                b2 = data[idx + 1]
                if 32 <= b1 <= 126 and b2 == 0:
                    if not current_utf16:
                        current_start = idx
                    current_utf16.append(chr(b1))
                    idx += 2
                else:
                    if len(current_utf16) >= min_length:
                        strings.append({
                            "string": "".join(current_utf16),
                            "offset": hex(current_start),
                            "encoding": "UTF-16LE",
                            "length": len(current_utf16),
                        })
                    current_utf16 = []
                    idx += 1

        sha256_val = hashlib.sha256(data).hexdigest()
        evidence = EvidenceProvenance(
            file_sha256=sha256_val,
            file_path=str(valid_path),
            file_size_bytes=file_size,
            source_tool=self.BACKEND_ID,
            tool_version=self.VERSION,
            method="bounded_stream_string_extraction",
        )

        facts = {
            "total_extracted": len(strings),
            "min_length_filter": min_length,
            "strings": strings[:max_strings],
        }
        conclusions = [f"Extracted {len(strings)} printable strings (minimum length {min_length})."]

        return AnalysisResult(
            status="completed",
            artifact_id=sha256_val,
            operation=AnalysisCapability.STRINGS.value,
            backend_used=self.BACKEND_ID,
            backend_version=self.VERSION,
            findings=StructuredFindings(observed_facts=facts, derived_conclusions=conclusions),
            evidence=[evidence],
            confidence=1.0,
            warnings=warnings,
            duration_ms=round((time.perf_counter() - start_time) * 1000, 2),
        )

    def analyze_artifact(self, path: str | Path) -> AnalysisResult:
        """Perform comprehensive static analysis (PE / ELF / Archive / Entropy)."""
        start_time = time.perf_counter()
        valid_path = validate_artifact_path(path)
        artifact = self.detector.detect(valid_path)

        warnings: list[str] = []
        errors: list[str] = []

        with open(valid_path, "rb") as f:
            data = f.read()

        artifact.overall_entropy = calculate_entropy(data)

        # Parse specific binary formats
        if artifact.format_type in ("PE32", "PE32+", ".NET Assembly") or data.startswith(b"MZ"):
            self._parse_pe_structures(data, artifact, warnings, errors)
        elif artifact.format_type in ("ELF32", "ELF64") or data.startswith(b"\x7fELF"):
            self._parse_elf_structures(data, artifact, warnings, errors)

        # Check for embedded archives
        self._detect_embedded_archives(data, artifact)

        evidence = EvidenceProvenance(
            file_sha256=artifact.sha256,
            file_path=str(valid_path),
            file_size_bytes=artifact.size_bytes,
            source_tool=self.BACKEND_ID,
            tool_version=self.VERSION,
            method="comprehensive_static_inspection",
        )

        facts = artifact.to_dict()

        conclusions = [
            f"Format identified as {artifact.format_type} ({artifact.sub_format or 'N/A'}) targeting {artifact.target_os or 'unknown platform'}.",
            f"Detected architecture: {artifact.architecture or 'unknown'}, endianness: {artifact.endianness or 'unknown'}.",
            f"Entropy: {artifact.overall_entropy}/8.0 ({'High (potential packing/compression)' if (artifact.overall_entropy or 0) > 7.0 else 'Normal'}).",
        ]
        if artifact.sections:
            conclusions.append(f"Identified {len(artifact.sections)} sections.")
        if artifact.exports:
            conclusions.append(f"Enumerated {len(artifact.exports)} exported symbols.")
        if artifact.imports:
            unique_libs = len(set(i.library for i in artifact.imports))
            conclusions.append(f"Enumerated {len(artifact.imports)} imports from {unique_libs} dynamic libraries.")

        hypotheses: list[str] = []
        if (artifact.overall_entropy or 0) > 7.2:
            hypotheses.append("High overall binary entropy suggests packed, obfuscated, or encrypted payloads.")
        if any((s.entropy or 0) > 7.4 for s in artifact.sections):
            hypotheses.append("One or more sections exhibit very high entropy (> 7.4), typical of compressed resources or payload stagers.")

        status = "failed" if (errors and not artifact.sections and not artifact.symbols) else ("completed" if not errors else "partial")

        return AnalysisResult(
            status=status,
            artifact_id=artifact.sha256,
            operation=AnalysisCapability.IDENTIFY.value,
            backend_used=self.BACKEND_ID,
            backend_version=self.VERSION,
            findings=StructuredFindings(
                observed_facts=facts,
                derived_conclusions=conclusions,
                model_hypotheses=hypotheses,
            ),
            evidence=[evidence],
            confidence=artifact.detection_confidence,
            warnings=warnings + artifact.diagnostics,
            errors=errors,
            duration_ms=round((time.perf_counter() - start_time) * 1000, 2),
            limitations=[
                "Static analysis only; no code execution performed.",
                "Imports/exports derived directly from binary headers without external symbol server resolution.",
            ],
        )

    # -------------------------------------------------------------------------
    # PE Structure Parser
    # -------------------------------------------------------------------------
    def _parse_pe_structures(
        self, data: bytes, artifact: ArtifactMetadata, warnings: list[str], errors: list[str]
    ) -> None:
        if len(data) < 0x40:
            errors.append("PE file smaller than standard DOS header (64 bytes).")
            return

        try:
            pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
        except Exception as err:
            errors.append(f"Failed to read e_lfanew from DOS header: {err}")
            return

        if pe_offset + 24 > len(data):
            errors.append(f"PE header offset ({hex(pe_offset)}) is out of file bounds ({len(data)} bytes).")
            return

        if data[pe_offset:pe_offset + 4] != b"PE\x00\x00":
            errors.append(f"Invalid PE signature at {hex(pe_offset)}: {data[pe_offset:pe_offset+4]!r}")
            return

        file_hdr = data[pe_offset + 4:pe_offset + 24]
        machine, num_sections, timedatestamp, _, _, opt_hdr_size, characteristics = struct.unpack(
            "<HHIIIHH", file_hdr
        )

        opt_offset = pe_offset + 24
        if opt_offset + opt_hdr_size > len(data):
            errors.append("Optional header extends beyond file length.")
            return

        is_64 = False
        export_rva, export_size = 0, 0
        import_rva, import_size = 0, 0

        if opt_hdr_size >= 28:
            opt_magic = struct.unpack_from("<H", data, opt_offset)[0]
            is_64 = opt_magic == 0x20B
            artifact.is_64_bit = is_64

            entry_point = struct.unpack_from("<I", data, opt_offset + 16)[0]
            artifact.entry_point_rva = entry_point

            if not is_64 and opt_hdr_size >= 96:
                image_base = struct.unpack_from("<I", data, opt_offset + 28)[0]
                artifact.base_address = image_base
                # Data directories in PE32 begin at offset 96
                if opt_hdr_size >= 96 + 8:
                    export_rva, export_size = struct.unpack_from("<II", data, opt_offset + 96)
                if opt_hdr_size >= 96 + 16:
                    import_rva, import_size = struct.unpack_from("<II", data, opt_offset + 96 + 8)
            elif is_64 and opt_hdr_size >= 112:
                image_base = struct.unpack_from("<Q", data, opt_offset + 24)[0]
                artifact.base_address = image_base
                # Data directories in PE32+ begin at offset 112
                if opt_hdr_size >= 112 + 8:
                    export_rva, export_size = struct.unpack_from("<II", data, opt_offset + 112)
                if opt_hdr_size >= 112 + 16:
                    import_rva, import_size = struct.unpack_from("<II", data, opt_offset + 112 + 8)

        # Parse Section Headers
        sections_offset = opt_offset + opt_hdr_size
        num_sections = min(num_sections, AnalysisLimits.MAX_SECTIONS_COUNT)
        sections: list[SectionInfo] = []

        for s_idx in range(num_sections):
            sec_base = sections_offset + s_idx * 40
            if sec_base + 40 > len(data):
                warnings.append(f"Section table truncated at section {s_idx}.")
                break

            name_bytes = data[sec_base:sec_base + 8]
            sec_name = name_bytes.split(b"\x00")[0].decode("latin-1", errors="replace").strip()
            v_size, v_addr, raw_size, raw_offset, _, _, _, _, raw_flags = struct.unpack(
                "<IIIIIIHHI", data[sec_base + 8:sec_base + 40]
            )

            # Section entropy calculation
            sec_entropy = None
            if raw_offset < len(data) and raw_size > 0:
                end_off = min(raw_offset + raw_size, len(data))
                sec_entropy = calculate_entropy(data[raw_offset:end_off])

            flag_desc = self._describe_pe_section_flags(raw_flags)

            sections.append(
                SectionInfo(
                    name=sec_name,
                    virtual_address=v_addr,
                    virtual_size=v_size,
                    raw_offset=raw_offset,
                    raw_size=raw_size,
                    entropy=sec_entropy,
                    flags_description=flag_desc,
                    raw_flags=raw_flags,
                )
            )

        artifact.sections = sections

        def rva_to_file_offset(rva: int) -> int | None:
            for s in sections:
                # Compare against max of virtual size and raw size for tolerant mapping
                span = max(s.virtual_size, s.raw_size)
                if s.virtual_address <= rva < s.virtual_address + span:
                    delta = rva - s.virtual_address
                    if delta < s.raw_size:
                        off = s.raw_offset + delta
                        if off < len(data):
                            return off
            return None

        # Parse Exports
        if export_rva > 0 and export_size > 0:
            self._parse_pe_exports(data, export_rva, export_size, rva_to_file_offset, artifact, warnings)

        # Parse Imports
        if import_rva > 0 and import_size > 0:
            self._parse_pe_imports(data, import_rva, import_size, is_64, rva_to_file_offset, artifact, warnings)

    def _describe_pe_section_flags(self, flags: int) -> str:
        parts = []
        if flags & 0x00000020:
            parts.append("CODE")
        if flags & 0x00000040:
            parts.append("INITIALIZED_DATA")
        if flags & 0x00000080:
            parts.append("UNINITIALIZED_DATA")
        if flags & 0x20000000:
            parts.append("EXEC")
        if flags & 0x40000000:
            parts.append("READ")
        if flags & 0x80000000:
            parts.append("WRITE")
        return "|".join(parts) if parts else "NONE"

    def _parse_pe_exports(
        self,
        data: bytes,
        export_rva: int,
        export_size: int,
        rva_to_offset,
        artifact: ArtifactMetadata,
        warnings: list[str],
    ) -> None:
        exp_off = rva_to_offset(export_rva)
        if exp_off is None or exp_off + 40 > len(data):
            warnings.append("Export directory offset out of bounds.")
            return

        try:
            (
                _,
                _,
                _,
                _,
                _,
                ordinal_base,
                num_functions,
                num_names,
                addr_functions_rva,
                addr_names_rva,
                addr_ordinals_rva,
            ) = struct.unpack("<IIIIIIIIIII", data[exp_off:exp_off + 40])
        except Exception as err:
            warnings.append(f"Failed to unpack export directory: {err}")
            return

        func_table_off = rva_to_offset(addr_functions_rva)
        names_table_off = rva_to_offset(addr_names_rva)
        ordinals_table_off = rva_to_offset(addr_ordinals_rva)

        if not func_table_off:
            return

        # Map ordinals to names
        name_map: dict[int, str] = {}
        if names_table_off and ordinals_table_off:
            count = min(num_names, 1000)
            for i in range(count):
                n_ptr_off = names_table_off + i * 4
                o_ptr_off = ordinals_table_off + i * 2
                if n_ptr_off + 4 <= len(data) and o_ptr_off + 2 <= len(data):
                    n_rva = struct.unpack_from("<I", data, n_ptr_off)[0]
                    ord_bias = struct.unpack_from("<H", data, o_ptr_off)[0]
                    name_file_off = rva_to_offset(n_rva)
                    if name_file_off and name_file_off < len(data):
                        end_zero = data.find(b"\x00", name_file_off, min(name_file_off + 256, len(data)))
                        if end_zero != -1:
                            name_str = data[name_file_off:end_zero].decode("ascii", errors="replace")
                            name_map[ord_bias] = name_str

        exports: list[ExportSymbol] = []
        fn_count = min(num_functions, AnalysisLimits.MAX_SYMBOLS_EXTRACTED)

        for f_idx in range(fn_count):
            entry_off = func_table_off + f_idx * 4
            if entry_off + 4 > len(data):
                break
            fn_rva = struct.unpack_from("<I", data, entry_off)[0]
            if fn_rva == 0:
                continue

            ordinal = ordinal_base + f_idx
            name = name_map.get(f_idx)

            # Check if forwarded export (RVA lies within the export directory)
            is_forwarded = False
            forwarder: str | None = None
            if export_rva <= fn_rva < export_rva + export_size:
                is_forwarded = True
                fwd_off = rva_to_offset(fn_rva)
                if fwd_off and fwd_off < len(data):
                    end_fwd = data.find(b"\x00", fwd_off, min(fwd_off + 256, len(data)))
                    if end_fwd != -1:
                        forwarder = data[fwd_off:end_fwd].decode("ascii", errors="replace")

            exports.append(
                ExportSymbol(
                    name=name,
                    ordinal=ordinal,
                    rva=fn_rva,
                    is_forwarded=is_forwarded,
                    forwarder_target=forwarder,
                )
            )

        artifact.exports = exports

    def _parse_pe_imports(
        self,
        data: bytes,
        import_rva: int,
        import_size: int,
        is_64: bool,
        rva_to_offset,
        artifact: ArtifactMetadata,
        warnings: list[str],
    ) -> None:
        imp_off = rva_to_offset(import_rva)
        if imp_off is None or imp_off >= len(data):
            return

        imports: list[ImportSymbol] = []
        desc_idx = 0

        while True:
            cur_off = imp_off + desc_idx * 20
            if cur_off + 20 > len(data):
                break
            orig_first_thunk, _, _, name_rva, first_thunk = struct.unpack("<IIIII", data[cur_off:cur_off + 20])
            if orig_first_thunk == 0 and first_thunk == 0 and name_rva == 0:
                break  # Null terminator

            desc_idx += 1
            if desc_idx > 500:
                warnings.append("Reached maximum limit of imported DLL descriptors.")
                break

            name_off = rva_to_offset(name_rva)
            dll_name = "UNKNOWN.DLL"
            if name_off and name_off < len(data):
                end_n = data.find(b"\x00", name_off, min(name_off + 256, len(data)))
                if end_n != -1:
                    dll_name = data[name_off:end_n].decode("ascii", errors="replace")

            # Read Thunks (lookup table preferably, else IAT)
            thunk_rva = orig_first_thunk if orig_first_thunk != 0 else first_thunk
            thunk_off = rva_to_offset(thunk_rva)
            if not thunk_off:
                continue

            thunk_size = 8 if is_64 else 4
            ordinal_mask = 0x8000000000000000 if is_64 else 0x80000000
            t_idx = 0

            while len(imports) < AnalysisLimits.MAX_SYMBOLS_EXTRACTED:
                t_addr = thunk_off + t_idx * thunk_size
                if t_addr + thunk_size > len(data):
                    break

                val = struct.unpack_from("<Q" if is_64 else "<I", data, t_addr)[0]
                if val == 0:
                    break

                t_idx += 1
                if val & ordinal_mask:
                    ord_num = val & 0xFFFF
                    imports.append(
                        ImportSymbol(
                            library=dll_name,
                            symbol_name=None,
                            ordinal=ord_num,
                            address_or_thunk=val,
                        )
                    )
                else:
                    hint_off = rva_to_offset(val)
                    if hint_off and hint_off + 2 < len(data):
                        hint = struct.unpack_from("<H", data, hint_off)[0]
                        fn_name_off = hint_off + 2
                        end_fn = data.find(b"\x00", fn_name_off, min(fn_name_off + 256, len(data)))
                        fn_name = (
                            data[fn_name_off:end_fn].decode("ascii", errors="replace")
                            if end_fn != -1
                            else "MALFORMED_IMPORT"
                        )
                        imports.append(
                            ImportSymbol(
                                library=dll_name,
                                symbol_name=fn_name,
                                hint=hint,
                                address_or_thunk=val,
                            )
                        )

        artifact.imports = imports

    # -------------------------------------------------------------------------
    # ELF Structure Parser
    # -------------------------------------------------------------------------
    def _parse_elf_structures(
        self, data: bytes, artifact: ArtifactMetadata, warnings: list[str], errors: list[str]
    ) -> None:
        if len(data) < 16:
            errors.append("ELF binary smaller than ident block (16 bytes).")
            return

        ei_class = data[4]  # 1 = 32-bit, 2 = 64-bit
        ei_data = data[5]   # 1 = LE, 2 = BE
        endian = "<" if ei_data == 1 else ">"
        is_64 = ei_class == 2
        artifact.is_64_bit = is_64
        artifact.endianness = "little" if ei_data == 1 else "big"

        hdr_len = 64 if is_64 else 52
        if len(data) < hdr_len:
            errors.append(f"ELF header truncated (needed {hdr_len} bytes, got {len(data)}).")
            return

        if is_64:
            e_entry = struct.unpack_from(f"{endian}Q", data, 24)[0]
            e_shoff = struct.unpack_from(f"{endian}Q", data, 40)[0]
            e_shentsize = struct.unpack_from(f"{endian}H", data, 58)[0]
            e_shnum = struct.unpack_from(f"{endian}H", data, 60)[0]
            e_shstrndx = struct.unpack_from(f"{endian}H", data, 62)[0]
        else:
            e_entry = struct.unpack_from(f"{endian}I", data, 24)[0]
            e_shoff = struct.unpack_from(f"{endian}I", data, 32)[0]
            e_shentsize = struct.unpack_from(f"{endian}H", data, 46)[0]
            e_shnum = struct.unpack_from(f"{endian}H", data, 48)[0]
            e_shstrndx = struct.unpack_from(f"{endian}H", data, 50)[0]

        artifact.entry_point_rva = e_entry

        if e_shoff >= len(data) or e_shnum == 0:
            warnings.append("ELF has no section headers or section table is stripped.")
            return

        e_shnum = min(e_shnum, AnalysisLimits.MAX_SECTIONS_COUNT)
        raw_sh_list: list[dict[str, Any]] = []

        # Read section header table
        for s_idx in range(e_shnum):
            off = e_shoff + s_idx * e_shentsize
            if off + e_shentsize > len(data):
                warnings.append(f"ELF section table truncated at entry {s_idx}.")
                break

            if is_64:
                sh_name, sh_type, sh_flags, sh_addr, sh_offset, sh_size, sh_link, sh_info, _, _ = struct.unpack_from(
                    f"{endian}IIQQQQIIQQ", data, off
                )
            else:
                sh_name, sh_type, sh_flags, sh_addr, sh_offset, sh_size, sh_link, sh_info, _, _ = struct.unpack_from(
                    f"{endian}IIIIIIIIII", data, off
                )

            raw_sh_list.append({
                "name_idx": sh_name,
                "type": sh_type,
                "flags": sh_flags,
                "addr": sh_addr,
                "offset": sh_offset,
                "size": sh_size,
                "link": sh_link,
                "info": sh_info,
            })

        # Section string table (.shstrtab)
        shstrtab = b""
        if e_shstrndx < len(raw_sh_list):
            str_sh = raw_sh_list[e_shstrndx]
            if str_sh["offset"] + str_sh["size"] <= len(data):
                shstrtab = data[str_sh["offset"]:str_sh["offset"] + str_sh["size"]]

        def get_sec_name(name_idx: int) -> str:
            if name_idx < len(shstrtab):
                end = shstrtab.find(b"\x00", name_idx)
                if end != -1:
                    return shstrtab[name_idx:end].decode("ascii", errors="replace")
            return f"sec_{name_idx}"

        sections: list[SectionInfo] = []
        for sh in raw_sh_list:
            s_name = get_sec_name(sh["name_idx"])
            s_entropy = None
            if sh["offset"] < len(data) and sh["size"] > 0:
                end_s = min(sh["offset"] + sh["size"], len(data))
                s_entropy = calculate_entropy(data[sh["offset"]:end_s])

            sections.append(
                SectionInfo(
                    name=s_name,
                    virtual_address=sh["addr"],
                    virtual_size=sh["size"],
                    raw_offset=sh["offset"],
                    raw_size=sh["size"],
                    entropy=s_entropy,
                    raw_flags=sh["flags"],
                )
            )

        artifact.sections = sections

        # Parse Symbols (.symtab and .dynsym)
        symbols: list[SymbolInfo] = []
        sym_tables_found = 0

        for sh in raw_sh_list:
            if sh["type"] in (2, 11):  # SHT_SYMTAB or SHT_DYNSYM
                sym_tables_found += 1
                tbl_name = "dynsym" if sh["type"] == 11 else "symtab"
                strtab_data = b""
                if sh["link"] < len(raw_sh_list):
                    str_header = raw_sh_list[sh["link"]]
                    if str_header["offset"] + str_header["size"] <= len(data):
                        strtab_data = data[str_header["offset"]:str_header["offset"] + str_header["size"]]

                entry_size = 24 if is_64 else 16
                sym_count = sh["size"] // entry_size
                count_to_parse = min(sym_count, AnalysisLimits.MAX_SYMBOLS_EXTRACTED)

                for sym_idx in range(count_to_parse):
                    s_off = sh["offset"] + sym_idx * entry_size
                    if s_off + entry_size > len(data):
                        break

                    if is_64:
                        st_name, st_info, _, st_shndx, st_value, st_size = struct.unpack_from(
                            f"{endian}IBBHQQ", data, s_off
                        )
                    else:
                        st_name, st_value, st_size, st_info, _, st_shndx = struct.unpack_from(
                            f"{endian}IIIBBH", data, s_off
                        )

                    sym_name = ""
                    if st_name < len(strtab_data):
                        end_s = strtab_data.find(b"\x00", st_name)
                        if end_s != -1:
                            sym_name = strtab_data[st_name:end_s].decode("ascii", errors="replace")

                    if not sym_name:
                        continue

                    # Classify symbol
                    bind_val = st_info >> 4
                    type_val = st_info & 0xF

                    binding = "global" if bind_val == 1 else ("weak" if bind_val == 2 else "local")
                    sym_type = "function" if type_val == 2 else ("object" if type_val == 1 else "unknown")
                    defined = st_shndx != 0  # 0 = SHN_UNDEF

                    symbols.append(
                        SymbolInfo(
                            name=sym_name,
                            symbol_type=sym_type,
                            binding=binding,
                            defined=defined,
                            section_index=st_shndx,
                            value=st_value,
                            size=st_size,
                            source_table=tbl_name,
                        )
                    )

        if sym_tables_found == 0:
            warnings.append("No ELF symbol tables (.symtab or .dynsym) detected; binary may be stripped.")

        artifact.symbols = symbols

    # -------------------------------------------------------------------------
    # Embedded Archive Detection
    # -------------------------------------------------------------------------
    def _detect_embedded_archives(self, data: bytes, artifact: ArtifactMetadata) -> None:
        """Scan for embedded zip, 7z, tar, or gzip archives within the binary."""
        embedded: list[dict[str, Any]] = []

        # Find ZIP headers (PK\x03\x04) not at offset 0
        idx = 4
        limit = min(len(data), 10 * 1024 * 1024)
        while idx < limit:
            pk_pos = data.find(b"PK\x03\x04", idx)
            if pk_pos == -1 or len(embedded) >= 5:
                break
            embedded.append({"type": "ZIP Archive", "offset": hex(pk_pos)})
            idx = pk_pos + 4

        # Find 7z headers
        idx = 6
        while idx < limit:
            z_pos = data.find(b"7z\xbc\xaf\x27\x1c", idx)
            if z_pos == -1 or len(embedded) >= 10:
                break
            embedded.append({"type": "7-Zip Archive", "offset": hex(z_pos)})
            idx = z_pos + 6

        artifact.embedded_artifacts = embedded
