"""Governed Blender connector for deterministic 3D scene creation.

The language model supplies a bounded scene recipe, never executable Python.
Chimera validates the recipe, requests operator approval, and invokes Blender
in background mode with a fixed script and a JSON configuration file.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ..base import BaseApplicationConnector
from ..manifest import ActionRiskLevel, ApplicationAction, ApplicationManifest, IntegrationSurface


_BLENDER_SCRIPT = r'''
import bpy
import json
import sys
from mathutils import Vector

args = sys.argv[sys.argv.index("--") + 1:]
config_path = args[args.index("--config") + 1]
with open(config_path, "r", encoding="utf-8") as handle:
    config = json.load(handle)

# Start from a known empty scene. No user-supplied Python is evaluated.
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)

primitive_ops = {
    "cube": bpy.ops.mesh.primitive_cube_add,
    "sphere": bpy.ops.mesh.primitive_uv_sphere_add,
    "cylinder": bpy.ops.mesh.primitive_cylinder_add,
    "cone": bpy.ops.mesh.primitive_cone_add,
    "torus": bpy.ops.mesh.primitive_torus_add,
    "plane": bpy.ops.mesh.primitive_plane_add,
}

for item in config["recipe"]["objects"]:
    primitive_ops[item["type"]](location=item["location"], rotation=item["rotation"])
    obj = bpy.context.object
    obj.name = item["name"]
    obj.scale = item["scale"]
    material = bpy.data.materials.new(name=(item["name"] + "_Material")[:63])
    material.diffuse_color = (*item["color"], 1.0)
    obj.data.materials.append(material)

scene = bpy.context.scene
camera_data = bpy.data.cameras.new("Chimera_Camera")
camera = bpy.data.objects.new("Chimera_Camera", camera_data)
scene.collection.objects.link(camera)
camera.location = (8.0, -10.0, 7.0)
direction = Vector((0.0, 0.0, 0.0)) - camera.location
camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
camera.data.lens = 50
scene.camera = camera

light_data = bpy.data.lights.new(name="Chimera_Key_Light", type="AREA")
light = bpy.data.objects.new(name="Chimera_Key_Light", object_data=light_data)
scene.collection.objects.link(light)
light.location = (4.0, -4.0, 7.0)
light.data.energy = 1800
light.data.shape = "DISK"
light.data.size = 6.0

if scene.world is None:
    scene.world = bpy.data.worlds.new("Chimera_World")
scene.world.use_nodes = True
background = scene.world.node_tree.nodes.get("Background")
if background:
    background.inputs["Color"].default_value = (0.12, 0.14, 0.18, 1.0)
    background.inputs["Strength"].default_value = 0.7

scene.render.resolution_x = config["resolution"][0]
scene.render.resolution_y = config["resolution"][1]
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
scene.render.filepath = config["render_path"]

# Prefer Eevee for a fast, reproducible preview. Fall back across Blender versions.
try:
    scene.render.engine = "BLENDER_EEVEE_NEXT"
except Exception:
    try:
        scene.render.engine = "BLENDER_EEVEE"
    except Exception:
        pass

bpy.ops.wm.save_as_mainfile(filepath=config["blend_path"])
bpy.ops.render.render(write_still=True)
print("CHIMERA_BLENDER_RESULT=" + json.dumps({
    "blend_path": config["blend_path"],
    "render_path": config["render_path"],
    "objects_created": len(config["recipe"]["objects"]),
}))
'''


class BlenderConnector(BaseApplicationConnector):
    """Create simple Blender projects and rendered previews using a safe recipe DSL."""

    PRIMITIVES = {"cube", "sphere", "cylinder", "cone", "torus", "plane"}
    MAX_OBJECTS = 20

    def __init__(self, executable: str | Path | None = None):
        self._configured_executable = str(executable) if executable else None
        manifest = ApplicationManifest(
            application_id="blender",
            display_name="Blender 3D",
            vendor="Blender Foundation",
            connector_version="1.0.0",
            supported_surfaces=[IntegrationSurface.CLI, IntegrationSurface.SCRIPTING_EXTENSION],
            actions=[
                ApplicationAction(
                    action_id="inspect_installation",
                    display_name="Inspect Blender Installation",
                    description="Check whether Blender's local executable is available and report its path.",
                    risk_level=ActionRiskLevel.LEVEL_0_OBSERVE,
                    permission="blender.read",
                    arguments_schema={},
                ),
                ApplicationAction(
                    action_id="create_scene",
                    display_name="Create and Render a 3D Scene",
                    description=(
                        "Create a Blender .blend project and PNG preview from a bounded JSON recipe. "
                        "Supports cube, sphere, cylinder, cone, torus and plane objects with transforms "
                        "and simple RGB materials. Requires explicit approval before writing files."
                    ),
                    risk_level=ActionRiskLevel.LEVEL_3_PROTECTED,
                    permission="blender.project.write",
                    arguments_schema={
                        "output_blend_path": "string",
                        "output_render_path": "string",
                        "recipe_json": "string",
                    },
                    expected_side_effects=["Creates a .blend project", "Creates a PNG render preview"],
                    reversibility="reversible",
                    requires_approval=True,
                ),
            ],
            supported_file_extensions=[".blend", ".png", ".obj", ".stl", ".glb", ".gltf"],
            known_limitations=[
                "This first connector creates bounded primitive-based scenes; it is not a general mesh modeller.",
                "Blender must be installed locally; no binary or add-on is downloaded.",
                "The model supplies scene data, never arbitrary Blender Python.",
                "File creation requires explicit operator approval.",
            ],
            preconditions=["Blender executable available on PATH or CHIMERA_BLENDER_EXECUTABLE configured"],
        )
        super().__init__(manifest)

    def _find_executable(self) -> str | None:
        candidates: list[str] = []
        if self._configured_executable:
            candidates.append(self._configured_executable)
        env_value = os.environ.get("CHIMERA_BLENDER_EXECUTABLE")
        if env_value:
            candidates.append(env_value)
        found = shutil.which("blender")
        if found:
            candidates.append(found)
        if os.name == "nt":
            roots = [
                Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Blender Foundation",
                Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Blender Foundation",
            ]
            for root in roots:
                if root.is_dir():
                    candidates.extend(str(path / "blender.exe") for path in sorted(root.glob("Blender *"), reverse=True))
        for raw in candidates:
            path = Path(raw).expanduser()
            if path.is_file():
                return str(path.resolve())
        return None

    def discover(self) -> ApplicationManifest:
        executable = self._find_executable()
        self.manifest.is_installed = executable is not None
        self.manifest.installation_path = str(Path(executable).parent) if executable else None
        self.manifest.metadata["executable"] = executable
        return self.manifest

    @staticmethod
    def _output_path(raw: Any, suffix: str, label: str) -> Path:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(f"{label} must be a non-empty absolute path")
        path = Path(raw).expanduser()
        if not path.is_absolute():
            raise ValueError(f"{label} must be an absolute path")
        if path.suffix.lower() != suffix:
            raise ValueError(f"{label} must use the {suffix} extension")
        parent = path.parent.resolve(strict=True)
        if not parent.is_dir():
            raise ValueError(f"{label} parent directory does not exist")
        resolved = parent / path.name
        if resolved.exists() or resolved.is_symlink():
            raise FileExistsError(f"{label} already exists; choose a new filename")
        return resolved

    @classmethod
    def validate_recipe(cls, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, str) or len(raw) > 24_000:
            raise ValueError("recipe_json must be a JSON string no larger than 24,000 characters")
        try:
            recipe = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"recipe_json is invalid JSON: {exc.msg}") from None
        if not isinstance(recipe, dict) or set(recipe) - {"title", "objects"}:
            raise ValueError("recipe must be an object containing only title and objects")
        title = recipe.get("title", "Chimera Scene")
        if not isinstance(title, str) or not title.strip() or len(title) > 80:
            raise ValueError("recipe title must contain 1 to 80 characters")
        objects = recipe.get("objects")
        if not isinstance(objects, list) or not 1 <= len(objects) <= cls.MAX_OBJECTS:
            raise ValueError(f"recipe must contain between 1 and {cls.MAX_OBJECTS} objects")

        normalized = []
        names: set[str] = set()
        for index, item in enumerate(objects):
            if not isinstance(item, dict):
                raise ValueError(f"objects[{index}] must be an object")
            allowed = {"name", "type", "location", "rotation", "scale", "color"}
            if set(item) - allowed:
                raise ValueError(f"objects[{index}] contains unsupported properties")
            name = item.get("name", f"Object_{index + 1}")
            kind = item.get("type")
            if not isinstance(name, str) or not name.strip() or len(name) > 48:
                raise ValueError(f"objects[{index}].name must contain 1 to 48 characters")
            if any(not (ch.isalnum() or ch in " _-") for ch in name):
                raise ValueError(f"objects[{index}].name may contain only letters, numbers, spaces, _ and -")
            if name in names:
                raise ValueError(f"duplicate object name: {name}")
            names.add(name)
            if kind not in cls.PRIMITIVES:
                raise ValueError(f"objects[{index}].type must be one of {sorted(cls.PRIMITIVES)}")

            def vector(field: str, default: tuple[float, float, float], low: float, high: float) -> list[float]:
                value = item.get(field, list(default))
                if not isinstance(value, (list, tuple)) or len(value) != 3:
                    raise ValueError(f"objects[{index}].{field} must contain exactly 3 numbers")
                result = []
                for component in value:
                    if isinstance(component, bool) or not isinstance(component, (int, float)):
                        raise ValueError(f"objects[{index}].{field} must contain finite numbers")
                    number = float(component)
                    if not math.isfinite(number) or number < low or number > high:
                        raise ValueError(f"objects[{index}].{field} values must be between {low} and {high}")
                    result.append(number)
                return result

            location = vector("location", (0.0, 0.0, 0.0), -100.0, 100.0)
            rotation = vector("rotation", (0.0, 0.0, 0.0), -6.2832, 6.2832)
            scale = vector("scale", (1.0, 1.0, 1.0), 0.01, 20.0)
            color = vector("color", (0.75, 0.35, 0.15), 0.0, 1.0)
            normalized.append({
                "name": name, "type": kind, "location": location,
                "rotation": rotation, "scale": scale, "color": color,
            })
        return {"title": title, "objects": normalized}

    def execute_action(self, action_id: str, arguments: dict[str, Any], approved: bool = False) -> dict[str, Any]:
        if action_id == "inspect_installation":
            manifest = self.discover()
            return {
                "status": "completed",
                "installed": manifest.is_installed,
                "executable": manifest.metadata.get("executable"),
            }
        if action_id != "create_scene":
            return {"status": "failed", "error": f"Unsupported Blender action: {action_id}"}
        if not approved:
            return {"status": "awaiting_approval", "permission": "blender.project.write"}

        try:
            manifest = self.discover()
            executable = manifest.metadata.get("executable")
            if not executable:
                return {"status": "unavailable", "error": "Blender executable was not found; configure CHIMERA_BLENDER_EXECUTABLE."}
            recipe = self.validate_recipe(arguments.get("recipe_json"))
            blend_path = self._output_path(arguments.get("output_blend_path"), ".blend", "output_blend_path")
            render_path = self._output_path(arguments.get("output_render_path"), ".png", "output_render_path")
            if blend_path == render_path:
                raise ValueError("project and render paths must be different")

            config = {
                "blend_path": str(blend_path),
                "render_path": str(render_path),
                "recipe": recipe,
                "resolution": [1024, 1024],
            }
            config_name = None
            script_name = None
            try:
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as handle:
                    json.dump(config, handle, ensure_ascii=False)
                    config_name = handle.name
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".py", delete=False) as handle:
                    handle.write(_BLENDER_SCRIPT)
                    script_name = handle.name
                completed = subprocess.run(
                    [executable, "--background", "--factory-startup", "--python", script_name, "--", "--config", config_name],
                    capture_output=True,
                    text=True,
                    timeout=180,
                    shell=False,
                    check=False,
                )
                if completed.returncode != 0:
                    return {
                        "status": "failed",
                        "error": f"Blender exited with code {completed.returncode}",
                        "stderr": (completed.stderr or "")[-3000:],
                        "stdout": (completed.stdout or "")[-1500:],
                    }
            except subprocess.TimeoutExpired:
                return {"status": "failed", "error": "Blender scene creation exceeded the 180-second limit"}
            finally:
                for temporary in (config_name, script_name):
                    if temporary:
                        try:
                            Path(temporary).unlink(missing_ok=True)
                        except OSError:
                            pass

            if not blend_path.is_file() or blend_path.stat().st_size <= 0:
                return {"status": "failed", "error": "Blender exited without producing a non-empty .blend project"}
            if not render_path.is_file() or render_path.stat().st_size <= 0:
                return {"status": "failed", "error": "Blender exited without producing a non-empty PNG preview"}
            return {
                "status": "completed",
                "verified": True,
                "project_path": str(blend_path),
                "render_path": str(render_path),
                "objects_created": len(recipe["objects"]),
                "render_resolution": [1024, 1024],
                "execution": "local_blender_background",
                "stdout": (completed.stdout or "")[-1500:],
            }
        except (ValueError, OSError, FileExistsError) as exc:
            return {"status": "failed", "error": str(exc)}
