"""Analysis backend adapters for REA.

This package contains backend adapters for various reverse engineering tools:
- builtin_static: Zero-dependency Python static analysis
- ghidra: NSA Ghidra SRE Suite
- radare2: radare2 reverse engineering framework
- rizin: Rizin binary analysis framework
"""
from __future__ import annotations

from .builtin_static import BuiltinStaticBackend
from .ghidra import GhidraBackend
from .radare2 import Radare2Backend
from .rizin import RizinBackend

__all__ = [
    "BuiltinStaticBackend",
    "GhidraBackend",
    "Radare2Backend",
    "RizinBackend",
]
