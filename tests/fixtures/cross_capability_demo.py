"""Cross-Capability Demonstration Scenario for Phase 5.3.

Creates a synthetic workspace with a sample PE executable and demonstrates
the multi-step binary analysis workflow combining workspace and REA capabilities.
"""
import tempfile
import struct
from pathlib import Path


def create_synthetic_pe_executable(path: Path) -> None:
    """Create a minimal but valid PE executable for testing."""
    # PE header magic and DOS stub
    dos_stub = b"MZ\x90\x00" + b"\x00" * 60

    # PE signature and COFF header
    pe_signature = b"PE\x00\x00"
    machine = struct.pack("<H", 0x014c)  # IMAGE_FILE_MACHINE_I386
    number_of_sections = struct.pack("<H", 2)
    time_date_stamp = struct.pack("<I", 0x5A4D2E4F)
    pointer_to_symbol_table = struct.pack("<I", 0)
    number_of_symbols = struct.pack("<I", 0)
    size_of_optional_header = struct.pack("<H", 224)  # Standard PE32
    characteristics = struct.pack("<H", 0x0102)  # Executable, 32-bit

    coff_header = (
        pe_signature
        + machine
        + number_of_sections
        + time_date_stamp
        + pointer_to_symbol_table
        + number_of_symbols
        + size_of_optional_header
        + characteristics
    )

    # Optional header
    magic = struct.pack("<H", 0x010b)  # PE32
    major_linker_version = struct.pack("<B", 14)
    minor_linker_version = struct.pack("<B", 0)
    size_of_code = struct.pack("<I", 0x1000)
    size_of_initialized_data = struct.pack("<I", 0x1000)
    size_of_uninitialized_data = struct.pack("<I", 0)
    address_of_entry_point = struct.pack("<I", 0x1000)
    base_of_code = struct.pack("<I", 0x1000)
    base_of_data = struct.pack("<I", 0x2000)
    image_base = struct.pack("<I", 0x00400000)
    section_alignment = struct.pack("<I", 0x1000)
    file_alignment = struct.pack("<I", 0x200)
    major_os_version = struct.pack("<H", 4)
    minor_os_version = struct.pack("<H", 0)
    major_image_version = struct.pack("<H", 0)
    minor_image_version = struct.pack("<H", 0)
    major_subsystem_version = struct.pack("<H", 4)
    minor_subsystem_version = struct.pack("<H", 0)
    win32_version_value = struct.pack("<I", 0)
    size_of_image = struct.pack("<I", 0x3000)
    size_of_headers = struct.pack("<I", 0x400)
    checksum = struct.pack("<I", 0)
    subsystem = struct.pack("<H", 2)  # IMAGE_SUBSYSTEM_WINDOWS_GUI
    dll_characteristics = struct.pack("<H", 0)
    size_of_stack_reserve = struct.pack("<I", 0x100000)
    size_of_stack_commit = struct.pack("<I", 0x1000)
    size_of_heap_reserve = struct.pack("<I", 0x100000)
    size_of_heap_commit = struct.pack("<I", 0x1000)
    loader_flags = struct.pack("<I", 0)
    number_of_rva_and_sizes = struct.pack("<I", 16)

    optional_header = (
        magic
        + major_linker_version
        + minor_linker_version
        + size_of_code
        + size_of_initialized_data
        + size_of_uninitialized_data
        + address_of_entry_point
        + base_of_code
        + base_of_data
        + image_base
        + section_alignment
        + file_alignment
        + major_os_version
        + minor_os_version
        + major_image_version
        + minor_image_version
        + major_subsystem_version
        + minor_subsystem_version
        + win32_version_value
        + size_of_image
        + size_of_headers
        + checksum
        + subsystem
        + dll_characteristics
        + size_of_stack_reserve
        + size_of_stack_commit
        + size_of_heap_reserve
        + size_of_heap_commit
        + loader_flags
        + number_of_rva_and_sizes
    )

    # Data directory entries (16 entries, 8 bytes each)
    data_directories = b"\x00" * (16 * 8)

    # Section headers
    section1_name = b".text\x00\x00\x00"
    section1_virtual_size = struct.pack("<I", 0x1000)
    section1_virtual_address = struct.pack("<I", 0x1000)
    section1_size_of_raw_data = struct.pack("<I", 0x200)
    section1_pointer_to_raw_data = struct.pack("<I", 0x400)
    section1_pointer_to_relocations = struct.pack("<I", 0)
    section1_pointer_to_line_numbers = struct.pack("<I", 0)
    section1_number_of_relocations = struct.pack("<H", 0)
    section1_number_of_line_numbers = struct.pack("<H", 0)
    section1_characteristics = struct.pack("<I", 0x60000020)  # CODE, EXECUTABLE, READABLE

    section2_name = b".data\x00\x00\x00"
    section2_virtual_size = struct.pack("<I", 0x1000)
    section2_virtual_address = struct.pack("<I", 0x2000)
    section2_size_of_raw_data = struct.pack("<I", 0x200)
    section2_pointer_to_raw_data = struct.pack("<I", 0x600)
    section2_pointer_to_relocations = struct.pack("<I", 0)
    section2_pointer_to_line_numbers = struct.pack("<I", 0)
    section2_number_of_relocations = struct.pack("<H", 0)
    section2_number_of_line_numbers = struct.pack("<H", 0)
    section2_characteristics = struct.pack("<I", 0xC0000040)  # INITIALIZED_DATA, READABLE, WRITEABLE

    section_headers = (
        section1_name
        + section1_virtual_size
        + section1_virtual_address
        + section1_size_of_raw_data
        + section1_pointer_to_raw_data
        + section1_pointer_to_relocations
        + section1_pointer_to_line_numbers
        + section1_number_of_relocations
        + section1_number_of_line_numbers
        + section1_characteristics
        + section2_name
        + section2_virtual_size
        + section2_virtual_address
        + section2_size_of_raw_data
        + section2_pointer_to_raw_data
        + section2_pointer_to_relocations
        + section2_pointer_to_line_numbers
        + section2_number_of_relocations
        + section2_number_of_line_numbers
        + section2_characteristics
    )

    # Section data with embedded strings
    text_section_data = (
        b"\x90\x90\x90\x90"  # NOP sled
        + b"\xB8\x01\x00\x00\x00"  # MOV EAX, 1
        + b"\xC3"  # RET
        + b"\x00" * (0x200 - 9)
    )

    data_section_data = (
        b"ChimeraTestArtifact\x00"
        + b"ProductionBuild2024\x00"
        + b"SecretFunctionEntry\x00"
        + b"\x00" * (0x200 - 63)
    )

    # Combine all parts
    pe_file = (
        dos_stub
        + pe_signature
        + coff_header
        + optional_header
        + data_directories
        + section_headers
        + text_section_data
        + data_section_data
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pe_file)


def create_demo_workspace(base_path: Path) -> Path:
    """Create a complete synthetic workspace for demonstration."""
    workspace = base_path / "demo_binary_analysis"
    workspace.mkdir(parents=True, exist_ok=True)

    # Create synthetic PE executable
    exe_path = workspace / "sample.exe"
    create_synthetic_pe_executable(exe_path)

    # Create README
    readme_content = """# Binary Analysis Demo Workspace

This workspace contains a synthetic PE executable for testing Chimera's
cross-capability agentic workflows.

## Files
- sample.exe: Synthetic 32-bit Windows executable with embedded strings

## Objective
Analyze the executable, identify its format, architecture, sections, imports,
exports and interesting strings. Explain the findings and produce a structured
technical analysis report.
"""
    (workspace / "README.md").write_text(readme_content)

    # Create output directory
    (workspace / "output").mkdir(exist_ok=True)

    return workspace


if __name__ == "__main__":
    # Standalone execution to create demo workspace
    import sys

    if len(sys.argv) > 1:
        base = Path(sys.argv[1])
    else:
        base = Path(tempfile.gettempdir())

    workspace = create_demo_workspace(base)
    print(f"Demo workspace created at: {workspace}")
    print(f"Executable: {workspace / 'sample.exe'}")
