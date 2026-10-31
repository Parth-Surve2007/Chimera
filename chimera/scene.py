"""Extensible, data-only scene planning and deterministic raster rendering."""
from dataclasses import dataclass, asdict
from pathlib import Path
import struct
from .image_spec import _color
from .local_model import _provider_structured
import re

@dataclass(frozen=True)
class SceneObject:
    kind: str; x: int; y: int; width: int; height: int; fill: tuple[int, int, int]; stroke: tuple[int, int, int] | None = None; points: tuple[tuple[int, int], ...] = (); text: str = ""; opacity: int = 255
@dataclass(frozen=True)
class SceneLayer:
    name: str; objects: tuple[SceneObject, ...]; opacity: int = 255
@dataclass(frozen=True)
class SceneSpec:
    width: int; height: int; background: tuple[int, int, int]; layers: tuple[SceneLayer, ...]; source: str = "unknown"

KINDS = {"rectangle", "ellipse", "circle", "line", "polygon", "text"}
def validate_scene(data) -> SceneSpec:
    if not isinstance(data, dict): raise ValueError("scene must be an object")
    width, height = data.get("width"), data.get("height")
    if not isinstance(width, int) or not isinstance(height, int) or not 16 <= width <= 2048 or not 16 <= height <= 2048: raise ValueError("scene dimensions are invalid")
    layers = []
    for layer in data.get("layers", []):
        if not isinstance(layer, dict) or not isinstance(layer.get("name"), str): raise ValueError("scene layer is malformed")
        objects = []
        for obj in layer.get("objects", []):
            if not isinstance(obj, dict) or obj.get("kind") not in KINDS: raise ValueError("scene object kind is unsupported")
            values = [obj.get(k) for k in ("x", "y", "width", "height")]
            if not all(isinstance(v, int) for v in values) or values[2] < 0 or values[3] < 0 or values[0] < 0 or values[1] < 0 or values[0] + values[2] > width or values[1] + values[3] > height: raise ValueError("scene object is outside bounds")
            points = tuple(tuple(p) for p in obj.get("points", []))
            if any(len(p) != 2 or not all(isinstance(v, int) for v in p) or p[0] < 0 or p[1] < 0 or p[0] >= width or p[1] >= height for p in points): raise ValueError("scene polygon points are invalid")
            opacity = obj.get("opacity", 255); layer_opacity = layer.get("opacity", 255)
            if not isinstance(opacity, int) or not 0 <= opacity <= 255 or not isinstance(layer_opacity, int) or not 0 <= layer_opacity <= 255: raise ValueError("scene opacity is invalid")
            objects.append(SceneObject(obj["kind"], *values, _color(obj.get("fill", "white")), _color(obj["stroke"]) if obj.get("stroke") is not None else None, points, obj.get("text", "") if isinstance(obj.get("text", ""), str) else "", opacity))
        layers.append(SceneLayer(layer["name"], tuple(objects), layer_opacity))
    return SceneSpec(width, height, _color(data.get("background", "black")), tuple(layers), data.get("source", "unknown"))

def scene_from_image_spec(spec) -> SceneSpec:
    objects = []
    for shape in spec.shapes:
        objects.append({"kind": "ellipse" if shape.kind == "circle" else "rectangle", "x": shape.x, "y": shape.y, "width": shape.width, "height": shape.height, "fill": shape.color})
    for text in spec.texts: objects.append({"kind": "text", "x": text.x, "y": text.y, "width": max(1, len(text.text) * 6), "height": 8, "fill": text.color, "text": text.text})
    return validate_scene({"width": spec.width, "height": spec.height, "background": spec.background, "layers": [{"name": "default", "objects": objects}], "source": "image_spec"})

def plan_scene(request: str, provider) -> SceneSpec:
    if provider.status().state != "ready": raise ValueError("local model is unavailable for scene composition")
    size = re.search(r"\b(\d{2,4})\s*x\s*(\d{2,4})\b", request, re.I)
    width, height = (int(size.group(1)), int(size.group(2))) if size else (320, 200)
    prompt = (f"Return ONLY compact JSON, no markdown or explanation. Use exactly width {width} and height {height}. Make a simple illustration using at most 6 objects total. "
              "Each object MUST use kind (rectangle,ellipse,circle,polygon), x,y,width,height,fill; do not add other keys. "
              f"Use only these colors: black, white, red, green, blue, lightblue, paleblue, brown, yellow, orange, purple. Use integer coordinates with x+width <= {width} and y+height <= {height}, and polygons only for triangles. "
              "Required top-level keys: width,height,background,layers. User request: " + request)
    def infer(instruction):
        if hasattr(provider, "structured_with_metrics"): return provider.structured_with_metrics(instruction)
        return _provider_structured(provider, instruction), {}
    data, metrics = infer(prompt)
    role_map = {"background": "rectangle", "sky": "rectangle", "water": "rectangle", "reflection": "rectangle", "cloud": "ellipse", "sun": "ellipse", "mountain": "polygon", "boat": "polygon"}
    for attempt in range(2):
        try:
            if not isinstance(data, dict) or not isinstance(data.get("layers"), list): raise ValueError("scene layers must be an array")
            if isinstance(data.get("background"), dict):
                bg = data["background"]; data["background"] = bg.get("color", bg.get("fill", "black"))
            normalized = []
            for index, layer in enumerate(data["layers"]):
                if isinstance(layer, dict) and "objects" not in layer and ("type" in layer or "kind" in layer):
                    item = dict(layer); item["kind"] = item.pop("type", item.get("kind")); normalized.append({"name": f"layer-{index}", "objects": [item]})
                    continue
                layer = dict(layer); layer.setdefault("name", f"layer-{index}"); objects = []
                for item in layer.get("objects", []):
                    item = dict(item)
                    if "kind" not in item and "type" in item: item["kind"] = item.pop("type")
                    if "fill" not in item and "color" in item: item["fill"] = item.pop("color")
                    if item.get("kind") == "triangle":
                        item["kind"] = "polygon"
                    objects.append(item)
                layer["objects"] = objects; normalized.append(layer)
            data["layers"] = normalized
            for layer in data["layers"]:
                for obj in layer["objects"]:
                    if obj.get("kind") == "triangle": obj["kind"] = "polygon"
                    if obj.get("kind") == "circle" and "height" not in obj and "radius" in obj: obj["height"] = obj["width"] = int(obj["radius"] * 2)
                    if obj.get("kind") == "polygon" and not isinstance(obj.get("points"), list): obj.pop("points", None)
                    if obj.get("kind") in role_map:
                        obj["kind"] = role_map[obj["kind"]]
                        if obj["kind"] == "polygon" and not obj.get("points"):
                            x, y, w, h = obj["x"], obj["y"], obj["width"], obj["height"]; obj["points"] = [(x, y + h), (x + w // 2, y), (x + w, y + h)]
            data["source"] = "local_model"
            return validate_scene(data), metrics
        except ValueError as exc:
            if attempt == 1: raise
            data, retry_metrics = infer(prompt + f" Validation failed: {exc}. Regenerate one complete JSON object correcting that error. Do not explain.")
            metrics = {"initial": metrics, "regeneration": retry_metrics, "regenerated": True}

def render_scene(path: Path, scene: SceneSpec) -> None:
    pixels = [list(scene.background) for _ in range(scene.width * scene.height)]
    def blend(x, y, color, opacity):
        if not (0 <= x < scene.width and 0 <= y < scene.height): return
        old = pixels[y * scene.width + x]; a = opacity / 255
        pixels[y * scene.width + x] = tuple(int(old[i] * (1 - a) + color[i] * a) for i in range(3))
    def inside_polygon(x, y, points):
        result = False
        for i in range(len(points)):
            x1, y1 = points[i]; x2, y2 = points[(i + 1) % len(points)]
            if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1: result = not result
        return result
    for layer in scene.layers:
        for obj in layer.objects:
            opacity = obj.opacity * layer.opacity // 255
            for y in range(obj.y, min(scene.height, obj.y + obj.height + 1)):
                for x in range(obj.x, min(scene.width, obj.x + obj.width + 1)):
                    hit = obj.kind in ("rectangle", "text") or (obj.kind == "ellipse" and ((x-(obj.x+obj.width/2))/(obj.width/2 or 1))**2 + ((y-(obj.y+obj.height/2))/(obj.height/2 or 1))**2 <= 1) or (obj.kind == "circle" and ((x-(obj.x+obj.width/2))/(obj.width/2 or 1))**2 + ((y-(obj.y+obj.height/2))/(obj.height/2 or 1))**2 <= 1) or (obj.kind == "polygon" and inside_polygon(x, y, obj.points))
                    if hit: blend(x, y, obj.fill, opacity)
            if obj.kind == "line" and obj.points:
                for (x1, y1), (x2, y2) in zip(obj.points, obj.points[1:]):
                    for step in range(max(abs(x2-x1), abs(y2-y1)) + 1): blend(x1 + (x2-x1)*step//max(1, abs(x2-x1), abs(y2-y1)), y1 + (y2-y1)*step//max(1, abs(x2-x1), abs(y2-y1)), obj.stroke or obj.fill, opacity)
    row = (scene.width * 3 + 3) & ~3; raw = bytearray()
    for y in range(scene.height):
        for red, green, blue in pixels[(scene.height - 1 - y) * scene.width: (scene.height - y) * scene.width]: raw.extend((blue, green, red))
        raw.extend(b"\0" * (row - scene.width * 3))
    path.write_bytes(struct.pack("<2sIHHI", b"BM", 54 + len(raw), 0, 0, 54) + struct.pack("<IIIHHIIIIII", 40, scene.width, scene.height, 1, 24, 0, len(raw), 2835, 2835, 0, 0) + raw)
