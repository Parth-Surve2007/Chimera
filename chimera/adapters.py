"""Allowlisted application adapters for real, observable local work."""
from dataclasses import dataclass
from pathlib import Path
import shutil, struct, subprocess
from .image_spec import ImageSpec

@dataclass(frozen=True)
class AdapterStatus:
    application: str
    available: bool
    executable: str | None
    detail: str

@dataclass(frozen=True)
class AdapterResult:
    status: str
    application: str
    artifact: Path | None
    observation: dict
    error: str | None = None

class PaintAdapter:
    """A narrow Paint integration: create a valid BMP, open it in Paint, verify it."""
    application = "Microsoft Paint"

    def __init__(self, executable: str | None = None):
        self.executable = shutil.which(executable) if executable else (shutil.which("mspaint.exe") or shutil.which("mspaint"))

    def status(self) -> AdapterStatus:
        return AdapterStatus(self.application, bool(self.executable), self.executable,
                             "Paint executable discovered" if self.executable else "mspaint.exe is unavailable")

    def _write_bmp(self, path: Path, spec: ImageSpec) -> None:
        width, height = spec.width, spec.height
        row_size = (width * 3 + 3) & ~3
        pixels = bytearray()
        def pixel(x, y):
            for shape in spec.shapes:
                inside = shape.x <= x < shape.x + shape.width and shape.y <= y < shape.y + shape.height
                if shape.kind == "circle":
                    dx = (x - (shape.x + shape.width / 2)) / (shape.width / 2); dy = (y - (shape.y + shape.height / 2)) / (shape.height / 2); inside = dx * dx + dy * dy <= 1
                if inside: return shape.color
            for text in spec.texts:
                if text.x <= x < text.x + len(text.text) * 6 and text.y <= y < text.y + 8: return text.color
            return spec.background
        for y in range(height):
            for x in range(width):
                red, green, blue = pixel(x, y); pixels.extend((blue, green, red))
            pixels.extend(b"\0" * (row_size - width * 3))
        header = struct.pack("<2sIHHI", b"BM", 54 + len(pixels), 0, 0, 54)
        info = struct.pack("<IIIHHIIIIII", 40, width, height, 1, 24, 0, len(pixels), 2835, 2835, 0, 0)
        path.write_bytes(header + info + pixels)

    def execute(self, objective: str, workspace: Path, open_in_app: bool = True, image_spec: ImageSpec | None = None, scene=None, pixel_art=None) -> AdapterResult:
        status = self.status()
        if not status.available: return AdapterResult("blocked", self.application, None, {}, status.detail)
        root = Path(workspace).resolve(); root.mkdir(parents=True, exist_ok=True)
        artifact = root / "chimera-paint-artifact.bmp"
        try:
            if pixel_art is not None:
                from .pixel_art import render_pixel_art, verify_pixel_art
                render_pixel_art(artifact, pixel_art)
                observation = verify_pixel_art(artifact, pixel_art)
            elif scene is not None:
                from .scene import render_scene
                render_scene(artifact, scene)
            elif image_spec is not None: self._write_bmp(artifact, image_spec)
            else: return AdapterResult("failed", self.application, None, {}, "validated ImageSpec or SceneSpec is required")
            launched = False
            if pixel_art is None: observation = verify_bmp(artifact, image_spec)
            if open_in_app and observation.get("valid") and artifact.is_file():
                subprocess.Popen([self.executable, str(artifact)], shell=False, close_fds=True)
                launched = True
            observation.update({"objective": objective, "launched": launched, "application": self.application})
            return AdapterResult("completed" if observation["valid"] else "failed", self.application, artifact, observation,
                                 None if observation["valid"] else "BMP verification failed")
        except (OSError, ValueError, struct.error) as exc:
            return AdapterResult("failed", self.application, artifact if artifact.exists() else None, {}, str(exc))

def verify_bmp(path: Path, spec: ImageSpec | None = None) -> dict:
    data = Path(path).read_bytes()
    if len(data) < 54 or data[:2] != b"BM": return {"valid": False, "reason": "missing BMP signature"}
    size, offset = struct.unpack_from("<II", data, 2)[0], struct.unpack_from("<I", data, 10)[0]
    width, height = struct.unpack_from("<ii", data, 18); bits = struct.unpack_from("<H", data, 28)[0]
    valid = size == len(data) and offset == 54 and width > 0 and height > 0 and bits == 24
    result = {"valid": valid, "bytes": len(data), "width": width, "height": height, "bits_per_pixel": bits}
    if spec is not None:
        if (width, height) != (spec.width, spec.height): result.update({"valid": False, "reason": "dimensions contradict ImageSpec"}); return result
        row_size = (width * 3 + 3) & ~3
        def read_pixel(x, y):
            index = offset + (height - 1 - y) * row_size + x * 3
            blue, green, red = data[index:index + 3]; return (red, green, blue)
        checks = [(0, 0, spec.background)]
        for shape in spec.shapes: checks.append((shape.x + shape.width // 2, shape.y + shape.height // 2, shape.color))
        result["pixel_checks"] = [{"x": x, "y": y, "expected": expected, "actual": read_pixel(x, y), "matched": read_pixel(x, y) == expected} for x, y, expected in checks]
        result["valid"] = valid and all(item["matched"] for item in result["pixel_checks"])
        if not result["valid"]: result["reason"] = "pixels contradict ImageSpec"
    return result
