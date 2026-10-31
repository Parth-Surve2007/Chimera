"""Validated compact pixel-art planning and deterministic BMP rendering."""
from dataclasses import dataclass, asdict, replace
from pathlib import Path
import json, struct
from .image_spec import _color

GRID_WIDTH = GRID_HEIGHT = 32

def pixel_art_grammar() -> str:
    row = '"' + '" "."' .join([]) + '"'
    cells = ' '.join('pixel' for _ in range(32))
    return ('root ::= "{" ws "\\"version\\"" ws ":" ws "1," ws "\\"width\\"" ws ":" ws "32," ws "\\"height\\"" ws ":" ws "32," ws '
            '"\\"palette\\"" ws ":" ws "{" "\\".\\":\\"#FFFFFF\\"," "\\"R\\":\\"#D62828\\"," "\\"D\\":\\"#202020\\"," "\\"G\\":\\"#347A43\\"}" ws "," ws "\\"rows\\"" ws ":" ws "[" row ("," row)* "]" ws "}"\n'
            'row ::= "\\"" pixel pixel pixel pixel pixel pixel pixel pixel pixel pixel pixel pixel pixel pixel pixel pixel pixel pixel pixel pixel pixel pixel pixel pixel pixel pixel pixel pixel pixel pixel "\\""\n'
            'pixel ::= "." | "R" | "D" | "G"\n'
            'ws ::= [ \t\n]*')

@dataclass(frozen=True)
class PixelArtSpec:
    version: int
    width: int
    height: int
    palette: dict[str, tuple[int, int, int]]
    rows: tuple[str, ...]
    metadata: dict
    provenance: dict

def validate_pixel_art(data) -> PixelArtSpec:
    if not isinstance(data, dict): raise ValueError("pixel art must be a JSON object, not a sequence")
    if data.get("version") != 1: raise ValueError("pixel art version must be 1")
    width, height = data.get("width", GRID_WIDTH), data.get("height", GRID_HEIGHT)
    if width != GRID_WIDTH or height != GRID_HEIGHT: raise ValueError("pixel art generation grid must be 32x32")
    raw_palette = data.get("palette")
    if not isinstance(raw_palette, dict) or not raw_palette or len(raw_palette) > 12: raise ValueError("pixel art palette is invalid")
    palette = {}
    for name, value in raw_palette.items():
        if not isinstance(name, str) or len(name) != 1: raise ValueError("palette names must be one symbol")
        palette[name] = _color(value)
    rows = data.get("rows")
    if not isinstance(rows, list) or len(rows) != height or any(not isinstance(row, str) or len(row) != width for row in rows): raise ValueError("pixel grid dimensions are invalid")
    valid = set(palette)
    if any(ch not in valid for row in rows for ch in row): raise ValueError("pixel grid contains an unknown symbol")
    return PixelArtSpec(1, width, height, palette, tuple(rows), data.get("metadata", {}) if isinstance(data.get("metadata", {}), dict) else {}, data.get("provenance", {}) if isinstance(data.get("provenance", {}), dict) else {})

def pixel_art_prompt(request: str) -> str:
    return ("Return ONLY one compact JSON object, no markdown. Create generic 32x32 pixel art for the user's request. "
            "Required keys exactly: version=1,width=32,height=32,palette,rows. palette maps one-character symbols directly to named colors or #RRGGBB; "
            "rows is exactly 32 strings of exactly 32 characters. "
            "Use at most 8 palette symbols and only these color names when useful: black, white, red, green, blue, yellow, brown, orange, purple, gray. "
            "Make the composition recognizable through pixel placement, with no explanation. User request: " + request)

def plan_pixel_art(request: str, provider):
    prompt = pixel_art_prompt(request)
    if provider.status().state != "ready": raise ValueError("local model is unavailable for pixel-art composition")
    def infer(instruction):
        if hasattr(provider, "structured_with_metrics"):
            original = getattr(provider, "config", None)
            # A 32x32 grid needs more than the normal proposal budget. This is
            # still bounded and applies only to this compact pixel contract.
            if original is not None and original.max_tokens < 1024: provider.config = replace(original, max_tokens=1024)
            try:
                try: return provider.structured_with_metrics(instruction, grammar=pixel_art_grammar())
                except TypeError: return provider.structured_with_metrics(instruction)
            finally: provider.config = original
        from .local_model import _provider_structured
        return _provider_structured(provider, instruction), {}
    data, metrics = infer(prompt)
    for attempt in range(2):
        try:
            if not isinstance(data, dict): raise ValueError("model output must be a JSON object")
            data = dict(data); data["provenance"] = {"source": "local_model", "request": request}
            return validate_pixel_art(data), metrics
        except (TypeError, ValueError) as exc:
            if attempt: raise
            data, retry = infer(prompt + f" Validation failed: {exc}. Regenerate the complete 32x32 JSON exactly; do not explain.")
            metrics = {"initial": metrics, "regeneration": retry, "regenerated": True}

def render_pixel_art(path: Path, spec: PixelArtSpec, scale: int = 8) -> None:
    if not isinstance(scale, int) or scale < 1 or scale > 32: raise ValueError("scale is invalid")
    width, height = spec.width * scale, spec.height * scale
    rgb = [[spec.palette[ch] for ch in row] for row in spec.rows]
    row_size = (width * 3 + 3) & ~3; raw = bytearray()
    for y in range(height - 1, -1, -1):
        source = rgb[y // scale]
        for x in range(width):
            red, green, blue = source[x // scale]; raw.extend((blue, green, red))
        raw.extend(b"\0" * (row_size - width * 3))
    path.write_bytes(struct.pack("<2sIHHI", b"BM", 54 + len(raw), 0, 0, 54) + struct.pack("<IIIHHIIIIII", 40, width, height, 1, 24, 0, len(raw), 2835, 2835, 0, 0) + raw)

def verify_pixel_art(path: Path, spec: PixelArtSpec, scale: int = 8) -> dict:
    data = path.read_bytes()
    if data[:2] != b"BM" or len(data) < 54: return {"valid": False, "error": "invalid BMP"}
    width, height = struct.unpack_from("<ii", data, 18)
    expected = (spec.width * scale, spec.height * scale)
    if (width, height) != expected: return {"valid": False, "error": "dimensions do not match"}
    row_size = (width * 3 + 3) & ~3; checks = []
    for gy, row in enumerate(spec.rows):
        for gx, symbol in enumerate(row):
            offset = 54 + (height - 1 - gy * scale) * row_size + gx * scale * 3
            blue, green, red = data[offset:offset + 3]
            actual = (red, green, blue); expected_rgb = spec.palette[symbol]
            if actual != expected_rgb: return {"valid": False, "error": "pixel color mismatch", "grid": (gx, gy), "actual": actual, "expected": expected_rgb}
            if len(checks) < 8: checks.append({"grid": [gx, gy], "actual": actual})
    return {"valid": True, "dimensions": [width, height], "pixel_checks": checks, "semantic_verification": "unavailable"}

def spec_dict(spec: PixelArtSpec) -> dict:
    result = asdict(spec); result["rows"] = list(spec.rows); return result
