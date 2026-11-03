"""Progressive format and artifact identification engine for REA.

Inspects byte signatures, structural headers, and metadata across a broad
range of binary, archive, firmware, and script formats without execution.
"""
from __future__ import annotations

import hashlib
import struct
import zipfile
from pathlib import Path
from typing import Any

from .artifact import ArtifactMetadata
from .security import AnalysisLimits, validate_artifact_path


class ArtifactDetector:
    """Detects binary and software artifact formats with confidence scoring and diagnostic honesty."""

    PE_MACHINE_MAP = {
        0x14C: "x86 (i386)",
        0x8664: "x86_64 (AMD64)",
        0x1C0: "ARM",
        0xAA64: "ARM64 (AArch64)",
        0x1C4: "ARM Thumb-2",
        0x200: "IA64 (Itanium)",
        0x5032: "RISC-V 32-bit",
        0x5064: "RISC-V 64-bit",
    }

    ELF_MACHINE_MAP = {
        0x03: "x86 (i386)",
        0x3E: "x86_64 (AMD64)",
        0x28: "ARM",
        0xB7: "ARM64 (AArch64)",
        0x08: "MIPS",
        0xF3: "RISC-V",
        0x14: "PowerPC",
        0x15: "PowerPC 64-bit",
        0x02: "SPARC",
        0x2B: "SPARC V9",
        0x32: "IA64",
    }

    ELF_OSABI_MAP = {
        0: "System V (Generic UNIX)",
        1: "HP-UX",
        2: "NetBSD",
        3: "Linux",
        6: "Solaris",
        7: "AIX",
        9: "FreeBSD",
        12: "OpenBSD",
    }

    def detect(self, path: str | Path) -> ArtifactMetadata:
        """Analyze a file and construct initialized ArtifactMetadata."""
        valid_path = validate_artifact_path(path)
        file_size = valid_path.stat().st_size
        file_name = valid_path.name

        # Calculate hashes
        sha256_hash = hashlib.sha256()
        md5_hash = hashlib.md5()
        sha1_hash = hashlib.sha1()

        with open(valid_path, "rb") as f:
            while chunk := f.read(65536):
                sha256_hash.update(chunk)
                md5_hash.update(chunk)
                sha1_hash.update(chunk)

        sha256_str = sha256_hash.hexdigest()
        md5_str = md5_hash.hexdigest()
        sha1_str = sha1_hash.hexdigest()

        # Read initial header window (first 4KB or less)
        with open(valid_path, "rb") as f:
            header_sample = f.read(min(file_size, 4096))

        artifact = ArtifactMetadata(
            file_path=str(valid_path),
            file_name=file_name,
            sha256=sha256_str,
            md5=md5_str,
            sha1=sha1_str,
            size_bytes=file_size,
        )

        if file_size == 0:
            artifact.format_type = "Empty File"
            artifact.detection_confidence = 1.0
            artifact.diagnostics.append("File size is 0 bytes; no executable headers present.")
            return artifact

        # 1. PE Check
        if self._check_pe(header_sample, valid_path, file_size, artifact):
            return artifact

        # 2. ELF Check
        if self._check_elf(header_sample, file_size, artifact):
            return artifact

        # 3. Mach-O Check
        if self._check_macho(header_sample, file_size, artifact):
            return artifact

        # 4. APK / DEX Check
        if self._check_dex_or_apk(header_sample, valid_path, file_size, artifact):
            return artifact

        # 5. Archive / Embedded Check
        if self._check_archives(header_sample, file_size, artifact):
            return artifact

        # 6. Firmware Check
        if self._check_firmware(header_sample, file_size, artifact):
            return artifact

        # 7. Static Library Check
        if self._check_static_library(header_sample, file_size, artifact):
            return artifact

        # 8. Script / Source Code Check
        if self._check_script_or_text(header_sample, file_size, artifact):
            return artifact

        # 9. Fallback Unknown
        artifact.format_type = "Unknown Binary"
        artifact.detection_confidence = 0.2
        artifact.diagnostics.append(
            f"Unrecognized file magic. First 16 bytes: {header_sample[:16].hex()}"
        )
        return artifact

    def _check_pe(
        self, sample: bytes, path: Path, file_size: int, artifact: ArtifactMetadata
    ) -> bool:
        if not sample.startswith(b"MZ"):
            return False

        if len(sample) < 0x40:
            artifact.format_type = "Truncated DOS Executable"
            artifact.detection_confidence = 0.5
            artifact.target_os = "MS-DOS"
            artifact.diagnostics.append("File begins with 'MZ' DOS header but is too short for PE header.")
            return True

        pe_offset = struct.unpack_from("<I", sample, 0x3C)[0]
        if pe_offset >= file_size:
            artifact.format_type = "Corrupted/Truncated PE Binary"
            artifact.detection_confidence = 0.6
            artifact.target_os = "Windows"
            artifact.diagnostics.append(
                f"DOS header indicates PE offset at {hex(pe_offset)}, which exceeds file size ({file_size} bytes)."
            )
            return True

        # Read around PE header if pe_offset is outside 4KB sample
        pe_sig = b""
        with open(path, "rb") as f:
            f.seek(pe_offset)
            pe_hdr = f.read(32)
            if pe_hdr.startswith(b"PE\x00\x00"):
                pe_sig = b"PE\x00\x00"

        if pe_sig != b"PE\x00\x00":
            artifact.format_type = "MS-DOS Executable"
            artifact.target_os = "MS-DOS"
            artifact.detection_confidence = 0.8
            artifact.diagnostics.append("Valid DOS signature present, but PE signature is absent.")
            return True

        # Parse basic PE Machine and characteristics
        machine = struct.unpack_from("<H", pe_hdr, 4)[0]
        num_sections = struct.unpack_from("<H", pe_hdr, 6)[0]
        timedatestamp = struct.unpack_from("<I", pe_hdr, 8)[0]
        opt_hdr_size = struct.unpack_from("<H", pe_hdr, 20)[0]
        characteristics = struct.unpack_from("<H", pe_hdr, 22)[0]

        is_dll = bool(characteristics & 0x2000)
        arch = self.PE_MACHINE_MAP.get(machine, f"Unknown PE Machine (0x{machine:04x})")

        # Check Optional Header Magic
        opt_magic = 0
        if opt_hdr_size >= 2:
            with open(path, "rb") as f:
                f.seek(pe_offset + 24)
                opt_magic_bytes = f.read(2)
                if len(opt_magic_bytes) == 2:
                    opt_magic = struct.unpack("<H", opt_magic_bytes)[0]

        is_64 = opt_magic == 0x20B
        fmt = "PE32+" if is_64 else ("PE32" if opt_magic == 0x10B else "PE (No Optional Header)")

        # Check for .NET / CLR COM Descriptor
        is_dotnet = False
        try:
            with open(path, "rb") as f:
                # In PE32, COM Descriptor is data directory index 14 (offset 24 + 96 + 14*8 = 208)
                # In PE32+, COM Descriptor is at offset 24 + 112 + 14*8 = 248
                clr_dir_offset = (pe_offset + 24 + 112 + 14 * 8) if is_64 else (pe_offset + 24 + 96 + 14 * 8)
                if clr_dir_offset + 8 <= file_size and opt_hdr_size >= (208 if not is_64 else 224):
                    f.seek(clr_dir_offset)
                    clr_rva, clr_size = struct.unpack("<II", f.read(8))
                    if clr_rva > 0 and clr_size > 0:
                        is_dotnet = True
        except Exception:
            pass

        if is_dotnet:
            artifact.format_type = ".NET Assembly"
            artifact.sub_format = f"{fmt} DLL" if is_dll else f"{fmt} Executable"
        else:
            artifact.format_type = fmt
            artifact.sub_format = "Dynamic Link Library (DLL)" if is_dll else "Executable (EXE)"

        artifact.architecture = arch
        artifact.endianness = "little"
        artifact.target_os = "Windows"
        artifact.is_64_bit = is_64
        artifact.detection_confidence = 0.99
        artifact.compilation_metadata = {
            "timestamp_epoch": timedatestamp,
            "number_of_sections": num_sections,
            "characteristics": hex(characteristics),
            "is_dll": is_dll,
            "is_dotnet": is_dotnet,
        }
        return True

    def _check_elf(self, sample: bytes, file_size: int, artifact: ArtifactMetadata) -> bool:
        if not sample.startswith(b"\x7fELF"):
            return False

        if len(sample) < 16:
            artifact.format_type = "Truncated ELF Binary"
            artifact.detection_confidence = 0.6
            artifact.target_os = "UNIX/Linux"
            artifact.diagnostics.append("ELF magic found but header is truncated before identity table.")
            return True

        ei_class = sample[4]  # 1 = 32-bit, 2 = 64-bit
        ei_data = sample[5]   # 1 = little endian, 2 = big endian
        ei_osabi = sample[7]

        endian = "<" if ei_data == 1 else (">" if ei_data == 2 else None)
        if not endian:
            artifact.format_type = "Malformed ELF Binary"
            artifact.detection_confidence = 0.6
            artifact.diagnostics.append(f"Invalid ELF endianness indicator: {ei_data}")
            return True

        is_64 = ei_class == 2
        arch = "Unknown"
        sub_fmt = "Executable"

        if len(sample) >= (64 if is_64 else 52):
            e_type = struct.unpack_from(f"{endian}H", sample, 16)[0]
            e_machine = struct.unpack_from(f"{endian}H", sample, 18)[0]
            arch = self.ELF_MACHINE_MAP.get(e_machine, f"Unknown ELF Machine (0x{e_machine:04x})")
            if e_type == 1:
                sub_fmt = "Relocatable Object (.o)"
            elif e_type == 2:
                sub_fmt = "Executable"
            elif e_type == 3:
                sub_fmt = "Shared Object (.so)"
            elif e_type == 4:
                sub_fmt = "Core Dump"

        artifact.format_type = "ELF64" if is_64 else "ELF32"
        artifact.sub_format = sub_fmt
        artifact.architecture = arch
        artifact.endianness = "little" if ei_data == 1 else "big"
        artifact.target_os = self.ELF_OSABI_MAP.get(ei_osabi, f"POSIX/UNIX (ABI {ei_osabi})")
        artifact.is_64_bit = is_64
        artifact.detection_confidence = 0.99
        return True

    def _check_macho(self, sample: bytes, file_size: int, artifact: ArtifactMetadata) -> bool:
        if len(sample) < 4:
            return False
        magic = struct.unpack_from(">I", sample, 0)[0]
        if magic in (0xFEEDFACE, 0xFEEDFACF, 0xCEFAEDFE, 0xCFFAEDFE, 0xCAFEBABE, 0xBEBAFECA):
            is_64 = magic in (0xFEEDFACF, 0xCFFAEDFE)
            is_fat = magic in (0xCAFEBABE, 0xBEBAFECA)
            endian = "little" if magic in (0xCEFAEDFE, 0xCFFAEDFE) else "big"

            artifact.format_type = "Mach-O (Universal/Fat)" if is_fat else ("Mach-O 64-bit" if is_64 else "Mach-O 32-bit")
            artifact.target_os = "macOS / iOS / Darwin"
            artifact.is_64_bit = is_64
            artifact.endianness = endian
            artifact.detection_confidence = 0.95
            return True
        return False

    def _check_dex_or_apk(
        self, sample: bytes, path: Path, file_size: int, artifact: ArtifactMetadata
    ) -> bool:
        # Standalone DEX
        if sample.startswith(b"dex\n") and len(sample) >= 8:
            version = sample[4:7].decode("ascii", errors="replace")
            artifact.format_type = "DEX (Dalvik Executable)"
            artifact.sub_format = f"DEX Version {version}"
            artifact.target_os = "Android"
            artifact.detection_confidence = 0.99
            return True

        # Android Package (APK): ZIP containing AndroidManifest.xml and classes.dex
        if sample.startswith(b"PK\x03\x04"):
            try:
                if zipfile.is_zipfile(path):
                    with zipfile.ZipFile(path, "r") as zf:
                        names = set(zf.namelist())
                        if "AndroidManifest.xml" in names and any(n.endswith(".dex") for n in names):
                            artifact.format_type = "APK (Android Application Package)"
                            artifact.sub_format = "Android Signed ZIP Archive"
                            artifact.target_os = "Android"
                            artifact.detection_confidence = 0.98
                            dex_files = [n for n in names if n.endswith(".dex")]
                            artifact.compilation_metadata["contained_dex_count"] = len(dex_files)
                            artifact.compilation_metadata["contained_dex_files"] = dex_files[:10]
                            return True
            except Exception:
                pass
        return False

    def _check_archives(self, sample: bytes, file_size: int, artifact: ArtifactMetadata) -> bool:
        if sample.startswith(b"PK\x03\x04"):
            artifact.format_type = "Archive"
            artifact.sub_format = "ZIP Archive"
            artifact.detection_confidence = 0.95
            return True
        if sample.startswith(b"7z\xbc\xaf\x27\x1c"):
            artifact.format_type = "Archive"
            artifact.sub_format = "7-Zip Archive (7z)"
            artifact.detection_confidence = 0.99
            return True
        if sample.startswith(b"\x1f\x8b"):
            artifact.format_type = "Archive"
            artifact.sub_format = "GZIP Compressed Stream"
            artifact.detection_confidence = 0.95
            return True
        if len(sample) >= 262 and sample[257:262] == b"ustar":
            artifact.format_type = "Archive"
            artifact.sub_format = "TAR Archive"
            artifact.detection_confidence = 0.95
            return True
        return False

    def _check_firmware(self, sample: bytes, file_size: int, artifact: ArtifactMetadata) -> bool:
        # U-Boot legacy image: 0x27051956
        if len(sample) >= 4 and sample.startswith(b"\x27\x05\x19\x56"):
            artifact.format_type = "Firmware Image"
            artifact.sub_format = "U-Boot Legacy Image"
            artifact.target_os = "Embedded Linux / U-Boot"
            artifact.detection_confidence = 0.95
            return True

        # SquashFS: 'hsqs' or 'sqsh'
        if sample.startswith(b"hsqs") or sample.startswith(b"sqsh"):
            artifact.format_type = "Firmware Filesystem"
            artifact.sub_format = "SquashFS Compressed Filesystem"
            artifact.target_os = "Embedded Linux"
            artifact.detection_confidence = 0.95
            return True

        # Device Tree Blob (DTB): 0xd00dfeed
        if len(sample) >= 4 and sample.startswith(b"\xd0\x0d\xfe\xed"):
            artifact.format_type = "Firmware Device Tree"
            artifact.sub_format = "Device Tree Blob (DTB)"
            artifact.target_os = "Embedded System"
            artifact.detection_confidence = 0.95
            return True

        return False

    def _check_static_library(
        self, sample: bytes, file_size: int, artifact: ArtifactMetadata
    ) -> bool:
        if sample.startswith(b"!<arch>\n"):
            artifact.format_type = "Static Library"
            artifact.sub_format = "Unix / COFF Archive (.a / .lib)"
            artifact.detection_confidence = 0.95
            return True
        return False

    def _check_script_or_text(
        self, sample: bytes, file_size: int, artifact: ArtifactMetadata
    ) -> bool:
        if sample.startswith(b"#!"):
            line = sample.split(b"\n")[0].decode("ascii", errors="replace")
            artifact.format_type = "Script / Source Code"
            artifact.sub_format = f"Shebang Interpreter ({line.strip()})"
            artifact.detection_confidence = 0.90
            return True

        # Check if ASCII/UTF-8 printable text
        try:
            text = sample.decode("utf-8")
            if all(c.isprintable() or c in "\r\n\t " for c in text):
                artifact.format_type = "Plain Text / Source Code"
                artifact.detection_confidence = 0.75
                return True
        except UnicodeDecodeError:
            pass

        return False


def detect_artifact(path: str | Path) -> ArtifactMetadata:
    """Convenience helper to run format detection."""
    return ArtifactDetector().detect(path)
