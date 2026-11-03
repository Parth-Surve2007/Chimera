"""Format-independent artifact data model and metadata representations."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SectionInfo:
    """Normalized metadata for a binary section or segment."""
    name: str
    virtual_address: int
    virtual_size: int
    raw_offset: int
    raw_size: int
    entropy: float | None = None
    flags_description: str | None = None
    raw_flags: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "virtual_address": hex(self.virtual_address),
            "virtual_size": self.virtual_size,
            "raw_offset": hex(self.raw_offset),
            "raw_size": self.raw_size,
            "entropy": round(self.entropy, 4) if self.entropy is not None else None,
            "flags_description": self.flags_description,
            "raw_flags": hex(self.raw_flags),
        }


@dataclass
class ExportSymbol:
    """Normalized exported function or data symbol."""
    name: str | None
    ordinal: int
    rva: int
    is_forwarded: bool = False
    forwarder_target: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ordinal": self.ordinal,
            "rva": hex(self.rva),
            "is_forwarded": self.is_forwarded,
            "forwarder_target": self.forwarder_target,
        }


@dataclass
class ImportSymbol:
    """Normalized imported symbol reference."""
    library: str
    symbol_name: str | None
    ordinal: int | None = None
    hint: int | None = None
    address_or_thunk: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "library": self.library,
            "symbol_name": self.symbol_name,
            "ordinal": self.ordinal,
            "hint": self.hint,
            "address_or_thunk": hex(self.address_or_thunk) if self.address_or_thunk is not None else None,
        }


@dataclass
class SymbolInfo:
    """Generic symbol table entry (ELF, Mach-O, COFF, etc.)."""
    name: str
    symbol_type: str  # "function", "object", "section", "file", "unknown"
    binding: str  # "global", "local", "weak", "unknown"
    defined: bool = True
    section_index: int | None = None
    value: int | None = None
    size: int | None = None
    source_table: str = "symbol_table"  # "symtab", "dynsym", "export_table"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "symbol_type": self.symbol_type,
            "binding": self.binding,
            "defined": self.defined,
            "section_index": self.section_index,
            "value": hex(self.value) if self.value is not None else None,
            "size": self.size,
            "source_table": self.source_table,
        }


@dataclass
class ArtifactMetadata:
    """Comprehensive format-independent representation of an analyzed artifact."""
    file_path: str
    file_name: str
    sha256: str
    md5: str | None = None
    sha1: str | None = None
    size_bytes: int = 0
    format_type: str = "Unknown"  # PE32, PE32+, ELF32, ELF64, Mach-O, .NET, APK, DEX, Firmware, Archive, Script, Unknown
    sub_format: str | None = None
    architecture: str | None = None  # x86, x86_64, ARM, AArch64, MIPS, RISC-V, etc.
    endianness: str | None = None  # little, big
    target_os: str | None = None  # Windows, Linux, macOS, Android, Embedded, etc.
    is_64_bit: bool | None = None
    entry_point_rva: int | None = None
    base_address: int | None = None
    sections: list[SectionInfo] = field(default_factory=list)
    exports: list[ExportSymbol] = field(default_factory=list)
    imports: list[ImportSymbol] = field(default_factory=list)
    symbols: list[SymbolInfo] = field(default_factory=list)
    compilation_metadata: dict[str, Any] = field(default_factory=dict)
    detection_confidence: float = 0.0  # 0.0 to 1.0
    overall_entropy: float | None = None
    diagnostics: list[str] = field(default_factory=list)
    embedded_artifacts: list[dict[str, Any]] = field(default_factory=list)
    analysis_provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "file_name": self.file_name,
            "sha256": self.sha256,
            "md5": self.md5,
            "sha1": self.sha1,
            "size_bytes": self.size_bytes,
            "format_type": self.format_type,
            "sub_format": self.sub_format,
            "architecture": self.architecture,
            "endianness": self.endianness,
            "target_os": self.target_os,
            "is_64_bit": self.is_64_bit,
            "entry_point_rva": hex(self.entry_point_rva) if self.entry_point_rva is not None else None,
            "base_address": hex(self.base_address) if self.base_address is not None else None,
            "sections_count": len(self.sections),
            "sections": [s.to_dict() for s in self.sections],
            "exports_count": len(self.exports),
            "exports": [e.to_dict() for e in self.exports],
            "imports_count": len(self.imports),
            "imports": [i.to_dict() for i in self.imports],
            "symbols_count": len(self.symbols),
            "symbols": [sym.to_dict() for sym in self.symbols],
            "compilation_metadata": self.compilation_metadata,
            "detection_confidence": self.detection_confidence,
            "overall_entropy": round(self.overall_entropy, 4) if self.overall_entropy is not None else None,
            "diagnostics": self.diagnostics,
            "embedded_artifacts": self.embedded_artifacts,
            "analysis_provenance": self.analysis_provenance,
        }
