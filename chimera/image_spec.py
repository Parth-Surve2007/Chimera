"""Validated natural-language image specifications and deterministic interpretation."""
from dataclasses import dataclass, asdict
import re
from .local_model import _provider_structured

@dataclass(frozen=True)
class ShapeSpec:
    kind: str; x: int; y: int; width: int; height: int; color: tuple[int, int, int]
@dataclass(frozen=True)
class TextSpec:
    text: str; x: int; y: int; color: tuple[int, int, int]
@dataclass(frozen=True)
class ImageSpec:
    width: int; height: int; background: tuple[int, int, int]; shapes: tuple[ShapeSpec, ...] = (); texts: tuple[TextSpec, ...] = ()

COLORS = {"black": (8, 8, 8), "white": (245, 245, 245), "red": (190, 55, 55), "green": (70, 155, 95), "blue": (55, 100, 190), "lightblue": (130, 190, 235), "paleblue": (175, 215, 240), "brown": (125, 75, 40), "yellow": (220, 185, 55), "bronze": (180, 125, 65), "purple": (140, 80, 170), "orange": (220, 115, 45)}
def _color(value):
    if isinstance(value, str):
        if value.casefold() in COLORS: return COLORS[value.casefold()]
        if len(value) == 7 and value.startswith("#"):
            try: return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))
            except ValueError: pass
    if isinstance(value, (list, tuple)) and len(value) == 3 and all(isinstance(v, int) and 0 <= v <= 255 for v in value): return tuple(value)
    raise ValueError("unsupported image color")
def validate_image_spec(data) -> ImageSpec:
    if not isinstance(data, dict): raise ValueError("image specification must be an object")
    width, height = data.get("width"), data.get("height")
    if not isinstance(width, int) or not isinstance(height, int) or not (16 <= width <= 2048 and 16 <= height <= 2048): raise ValueError("image dimensions must be integers between 16 and 2048")
    shapes = []
    for item in data.get("shapes", []):
        if not isinstance(item, dict) or item.get("kind") not in ("rectangle", "circle"): raise ValueError("unsupported shape")
        values = [item.get(k) for k in ("x", "y", "width", "height")]
        if not all(isinstance(v, int) for v in values) or values[2] <= 0 or values[3] <= 0 or values[0] < 0 or values[1] < 0 or values[0] + values[2] > width or values[1] + values[3] > height: raise ValueError("shape is outside image bounds")
        shapes.append(ShapeSpec(item["kind"], *values, _color(item.get("color"))))
    texts = []
    for item in data.get("texts", []):
        if not isinstance(item, dict) or not isinstance(item.get("text"), str) or not item["text"] or not isinstance(item.get("x"), int) or not isinstance(item.get("y"), int): raise ValueError("text specification is malformed")
        if item["x"] < 0 or item["y"] < 0 or item["x"] >= width or item["y"] >= height: raise ValueError("text is outside image bounds")
        texts.append(TextSpec(item["text"][:200], item["x"], item["y"], _color(item.get("color", "white"))))
    return ImageSpec(width, height, _color(data.get("background", "black")), tuple(shapes), tuple(texts))
def interpret_request(request: str, provider=None):
    if provider is not None and provider.status().state == "ready":
        prompt = "Return JSON only: width,height,background,shapes (kind rectangle/circle,x,y,width,height,color),texts (text,x,y,color). Keep all shapes inside bounds. Request: " + request
        return validate_image_spec(_provider_structured(provider, prompt)), "local_model"
    text = request.casefold(); dimensions = re.search(r"(\d{2,4})\s*[x×]\s*(\d{2,4})", text)
    width, height = (int(dimensions.group(1)), int(dimensions.group(2))) if dimensions else (320, 200)
    bg = next((COLORS[name] for name in COLORS if re.search(rf"\b{re.escape(name)}\s+background\b", text)), COLORS["black"])
    shapes = []
    for name, kind in (("circle", "circle"), ("square", "rectangle"), ("rectangle", "rectangle")):
        match = re.search(rf"\b(red|green|blue|yellow|bronze|purple|orange|white|black)\s+{name}\b", text)
        if match:
            w = h = min(width, height) // 3; shapes.append({"kind": kind, "x": (width - w) // 2, "y": (height - h) // 2, "width": w, "height": h, "color": match.group(1)}); break
    if not shapes: raise ValueError("request is not understood by the deterministic image parser")
    return validate_image_spec({"width": width, "height": height, "background": bg, "shapes": shapes}), "deterministic_parser"
def spec_dict(spec): return asdict(spec)
