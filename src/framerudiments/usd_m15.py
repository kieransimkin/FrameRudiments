"""M15.4 bounded, optional OpenUSD mesh/camera/AOV interchange.

Only a trusted, single-layer, pinned ASCII USD scene is admitted. pxr runs in a
separate time-limited worker, never in the ordinary renderer. Hydra and arbitrary
USD composition are NOT supported by this adapter.
"""
from __future__ import annotations

from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys

SCHEMA = "framerudiments.usd-m15.4.v1"
MAX_SOURCE_BYTES = 4 * 1024 * 1024
MAX_TRIANGLES = 4096
MAX_POINTS = 8192
MAX_PRIMS = 128
MAX_RESULT_BYTES = 8 * 1024 * 1024
MAX_SIDE = 1024
_SHA = re.compile(r"[0-9a-fA-F]{64}\Z")
_REJECT_METADATA = re.compile(r"\b(subLayers|references|payload|payloads|clips|relocates|variantSets|inherits|specializes|asset|asset\[)\b")
_ALLOWED_TYPES = {"Xform", "Scope", "Mesh", "Camera", "Material", "Shader"}


def digest(path):
    with Path(path).open("rb") as src:
        return hashlib.file_digest(src, "sha256").hexdigest()


def admitted_file(path, sha256):
    """Admission is dependency-light: no USD shared library is loaded here."""
    if not isinstance(sha256, str) or not _SHA.fullmatch(sha256):
        raise ValueError("USD input requires an exact SHA-256 pin")
    raw_path = Path(path)
    if raw_path.is_symlink():
        raise ValueError("USD input must not be a symlink")
    path = raw_path.resolve(strict=True)
    if path.suffix.lower() != ".usda" or not path.is_file() or not 1 <= path.stat().st_size <= MAX_SOURCE_BYTES:
        raise ValueError("M15.4 accepts only bounded, regular .usda scene files")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != sha256.lower():
        raise ValueError("USD scene bytes changed or do not match SHA-256")
    try:
        source = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("USD scene must be UTF-8 ASCII USD") from exc
    # USDA @...@ denotes asset paths or composition arcs. Even a quoted/path
    # reference is prohibited in this single-layer profile; no external reads.
    # Deliberately conservative: unsupported text is rejected, not reparsed.
    if not source.lstrip().startswith("#usda 1.0") or "@" in source or _REJECT_METADATA.search(source):
        raise ValueError("USD composition, references, assets or unsupported metadata are prohibited")
    if "\x00" in source:
        raise ValueError("Invalid USD scene text")
    return path


def fps_value(fps):
    if not isinstance(fps, str) or len(fps) > 32:
        raise ValueError("USD output FPS needs a rational string")
    try:
        rate = Fraction(fps)
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError("USD output FPS is not an exact fraction") from exc
    if not Fraction(1, 1) <= rate <= Fraction(120, 1) or max(rate.numerator, rate.denominator) > 1_000_000:
        raise ValueError("USD output FPS outside accepted bounds")
    return rate


def frame_spec(path, sha256, *, camera, frame, fps, width, height):
    source = admitted_file(path, sha256)
    if not isinstance(camera, str) or not re.fullmatch(r"/[A-Za-z_][A-Za-z0-9_]*(?:/[A-Za-z_][A-Za-z0-9_]*)*", camera):
        raise ValueError("USD camera needs a literal absolute prim path")
    if type(frame) is not int or not 0 <= frame <= 1_000_000:
        raise ValueError("USD output frame must be a bounded nonnegative integer")
    if (type(width) is not int or type(height) is not int or
            not 16 <= width <= MAX_SIDE or not 16 <= height <= MAX_SIDE or width * height > 512 * 512):
        raise ValueError("USD image dimensions exceed the 262,144-pixel budget")
    rate = fps_value(fps)
    return {"source": str(source), "sha256": sha256.lower(), "camera": camera,
            "frame": frame, "fps": str(rate), "width": width, "height": height}


def _native_value(number, label, *, minimum=-1e6, maximum=1e6):
    try:
        value = float(number)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"Invalid USD {label}") from exc
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"USD {label} outside supported bounds")
    return value


def _vector(vector, label, length, *, minimum=-1e6, maximum=1e6):
    if vector is None or len(vector) != length:
        raise ValueError(f"USD {label} requires {length} components")
    return [_native_value(v, label, minimum=minimum, maximum=maximum) for v in vector]


def _material_colour(prim, time, UsdGeom, UsdShade):
    """Only constant UsdPreviewSurface diffuse/opacity or displayColor.

    Connected shaders, texture paths and unsupported materials are rejected.
    Display colour is scene-linear authoring data, not an sRGB texture.
    """
    binding = UsdShade.MaterialBindingAPI(prim)
    material, _relationship = binding.ComputeBoundMaterial()
    if material:
        output = material.GetSurfaceOutput()
        connection = output.GetConnectedSource() if output else None
        if not connection or not connection[0]:
            raise ValueError("USD bound material has no surface shader")
        source, _name, _kind = connection
        shader = UsdShade.Shader(source.GetPrim())
        if not shader or str(shader.GetIdAttr().Get()) != "UsdPreviewSurface":
            raise ValueError("Only constant UsdPreviewSurface is supported")
        diffuse = shader.GetInput("diffuseColor")
        opacity = shader.GetInput("opacity")
        for item in (diffuse, opacity):
            if item and item.GetAttr().GetConnections():
                raise ValueError("Connected USD texture/shader inputs are not supported")
        colour = diffuse.Get(time) if diffuse else (0.5, 0.5, 0.5)
        transparency = opacity.Get(time) if opacity else 1
        name = str(material.GetPath())
    else:
        gprim = UsdGeom.Gprim(prim)
        colours = gprim.GetDisplayColorAttr().Get(time)
        values = gprim.GetDisplayOpacityAttr().Get(time)
        if colours and len(colours) != 1:
            raise ValueError("Only one constant displayColor per mesh is supported")
        if values and len(values) != 1:
            raise ValueError("Only one constant displayOpacity per mesh is supported")
        colour = colours[0] if colours else (0.5, 0.5, 0.5)
        transparency = values[0] if values else 1
        name = None
    rgb = _vector(colour, "material colour", 3, minimum=0, maximum=128)
    alpha = _native_value(transparency, "material opacity", minimum=0, maximum=1)
    return rgb, alpha, name


def _extract_native(spec):
    # Called only in the opt-in, time-limited worker process.
    from pxr import Gf, Usd, UsdGeom, UsdShade

    source = admitted_file(spec["source"], spec["sha256"])
    stage = Usd.Stage.Open(str(source), load=Usd.Stage.LoadNone)
    if stage is None:
        raise ValueError("OpenUSD could not open the pinned scene")
    layers = stage.GetUsedLayers()
    if any(not layer.anonymous and Path(layer.realPath).resolve() != source for layer in layers):
        raise ValueError("USD scene unexpectedly uses external layers")
    axis = str(UsdGeom.GetStageUpAxis(stage))
    if axis not in {"Y", "Z"}:
        raise ValueError("USD up-axis is unsupported")
    metres = _native_value(UsdGeom.GetStageMetersPerUnit(stage), "metres per unit", minimum=1e-6, maximum=1000)
    tcs = _native_value(stage.GetTimeCodesPerSecond(), "time codes per second", minimum=1, maximum=1000)
    fps = Fraction(spec["fps"])
    sample = Fraction(spec["frame"]) * Fraction(str(tcs)) / fps
    if abs(float(sample)) > 1e6:
        raise ValueError("USD sample is beyond bounded timecode range")
    low, high = stage.GetStartTimeCode(), stage.GetEndTimeCode()
    if high > low and (float(sample) < low - 1e-8 or float(sample) > high + 1e-8):
        raise ValueError("USD sample outside authored stage time interval")
    time = Usd.TimeCode(float(sample))
    camera_prim = stage.GetPrimAtPath(spec["camera"])
    if not camera_prim or not camera_prim.IsA(UsdGeom.Camera):
        raise ValueError("USD camera prim missing or unsupported")
    xform = UsdGeom.XformCache(time)
    camera_matrix = xform.GetLocalToWorldTransform(camera_prim)
    view = camera_matrix.GetInverse()
    camera = UsdGeom.Camera(camera_prim)
    if str(camera.GetProjectionAttr().Get(time)) != "perspective":
        raise ValueError("Only perspective USD cameras are supported")
    focal = _native_value(camera.GetFocalLengthAttr().Get(time), "focal length", minimum=0.01)
    horizontal = _native_value(camera.GetHorizontalApertureAttr().Get(time), "horizontal aperture", minimum=0.01)
    vertical = _native_value(camera.GetVerticalApertureAttr().Get(time), "vertical aperture", minimum=0.01)
    clip = _vector(camera.GetClippingRangeAttr().Get(time), "camera clipping", 2, minimum=1e-6)
    if clip[0] >= clip[1]:
        raise ValueError("USD camera near/far planes are invalid")

    meshes, points_total, triangle_count, count = [], 0, 0, 0
    for prim in stage.Traverse():
        count += 1
        if count > MAX_PRIMS:
            raise ValueError("USD scene contains too many prims")
        kind = str(prim.GetTypeName())
        if kind not in _ALLOWED_TYPES:
            raise ValueError(f"Unsupported USD prim type: {kind}")
        if kind != "Mesh":
            continue
        mesh = UsdGeom.Mesh(prim)
        pts = mesh.GetPointsAttr().Get(time)
        counts = mesh.GetFaceVertexCountsAttr().Get(time)
        indices = mesh.GetFaceVertexIndicesAttr().Get(time)
        if pts is None or counts is None or indices is None or not pts:
            raise ValueError("USD mesh topology is missing")
        if any(x != 3 for x in counts) or len(indices) != sum(counts):
            raise ValueError("Only triangle-only, non-subdivided USD mesh topology is supported")
        if len(pts) + points_total > MAX_POINTS or len(counts) + triangle_count > MAX_TRIANGLES:
            raise ValueError("USD geometry exceeds vertex/triangle budgets")
        points_total += len(pts)
        triangle_count += len(counts)
        if str(mesh.GetSubdivisionSchemeAttr().Get(time)) not in ("none", ""):
            raise ValueError("USD subdivision meshes are not supported")
        if any(int(i) < 0 or int(i) >= len(pts) for i in indices):
            raise ValueError("USD face index is out of range")
        geometry_matrix = xform.GetLocalToWorldTransform(prim)
        rgb, alpha, material = _material_colour(prim, time, UsdGeom, UsdShade)
        projected = []
        for pt in pts:
            world = geometry_matrix.Transform(Gf.Vec3d(*pt))
            eye = view.Transform(world)
            eye_xyz = _vector(eye, "camera-space point", 3)
            depth = -eye_xyz[2]
            if depth <= clip[0] or depth >= clip[1]:
                raise ValueError("USD mesh vertex beyond near/far clip; partial clipping not supported")
            px = (eye_xyz[0] * focal / depth / horizontal + 0.5) * spec["width"]
            py = (0.5 - eye_xyz[1] * focal / depth / vertical) * spec["height"]
            projected.append([_native_value(px, "projected X"),
                              _native_value(py, "projected Y"), depth * metres,
                              eye_xyz[0], eye_xyz[1], eye_xyz[2]])
        meshes.append({"path": str(prim.GetPath()), "material": material,
                       "colour": rgb, "opacity": alpha, "vertices": projected,
                       "triangles": [[int(v) for v in indices[n:n+3]] for n in range(0, len(indices), 3)]})
    if not meshes:
        raise ValueError("USD scene has no supported mesh")
    if digest(source) != spec["sha256"]:
        raise ValueError("USD source changed during native stage evaluation")
    matrix = [[_native_value(camera_matrix[i][j], "camera matrix", minimum=-1e8, maximum=1e8)
               for j in range(4)] for i in range(4)]
    return {"schema": SCHEMA, "source_sha256": spec["sha256"],
            "up_axis": axis, "metres_per_unit": metres,
            "timecodes_per_second": str(Fraction(str(tcs))),
            "frame": spec["frame"], "fps": spec["fps"], "sample_timecode": str(sample),
            "time_seconds": str(Fraction(spec["frame"], 1) / fps),
            "camera": spec["camera"], "camera_to_world": matrix,
            "width": spec["width"], "height": spec["height"],
            "points": points_total, "triangles": triangle_count,
            "meshes": meshes, "hydra": "not_qualified", "native_geometry": True,
            "depth_units": "metres", "normal_space": "camera"}


def inspect(path, sha256, *, camera, frame, fps, width, height, timeout=45):
    """Extract supported USD geometry via a bounded independent Python worker."""
    spec = frame_spec(path, sha256, camera=camera, frame=frame, fps=fps, width=width, height=height)
    if type(timeout) is not int or not 1 <= timeout <= 120:
        raise ValueError("USD worker timeout must be 1..120 seconds")
    cmd = [sys.executable, "-m", "framerudiments.usd_m15", "--worker", json.dumps(spec, sort_keys=True)]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("OpenUSD native worker exceeded its deadline") from exc
    if result.returncode or not 0 < len(result.stdout) <= MAX_RESULT_BYTES:
        raise RuntimeError("OpenUSD scene admission failed inside native worker")
    data = json.loads(result.stdout)
    if data.get("schema") != SCHEMA or data.get("source_sha256") != spec["sha256"] or data.get("camera") != camera:
        raise RuntimeError("OpenUSD worker returned an invalid scene receipt")
    if digest(spec["source"]) != spec["sha256"]:
        raise ValueError("USD scene changed after native stage evaluation")
    return data


def _normal(p, q, r):
    ux, uy, uz = (q[i] - p[i] for i in (3, 4, 5))
    vx, vy, vz = (r[i] - p[i] for i in (3, 4, 5))
    result = (uy*vz-uz*vy, uz*vx-ux*vz, ux*vy-uy*vx)
    length = math.sqrt(sum(x*x for x in result))
    if length <= 1e-12:
        raise ValueError("USD triangle has zero area")
    return [x/length for x in result]


def raster(scene):
    """Depth-tested CPU triangle raster into real M12 AOVs; no image inference."""
    import numpy as np
    from .colour_aov import AOVFrame

    if scene.get("schema") != SCHEMA or scene.get("hydra") != "not_qualified":
        raise ValueError("Unsupported USD scene report")
    width, height = scene["width"], scene["height"]
    if not 16 <= width <= MAX_SIDE or not 16 <= height <= MAX_SIDE or width * height > 512*512:
        raise ValueError("USD image dimensions outside AOV budget")
    rgb = np.zeros((height, width, 3), dtype=np.float32)
    alpha = np.zeros((height, width), dtype=np.float32)
    depth = np.zeros((height, width), dtype=np.float32)
    normals = np.zeros((height, width, 3), dtype=np.float32)
    nearest = np.full((height, width), np.inf, dtype=np.float64)
    for mesh in scene["meshes"]:
        vertices = mesh["vertices"]
        colour = np.array(mesh["colour"], dtype=np.float32)
        opacity = mesh["opacity"]
        for indices in mesh["triangles"]:
            p, q, r = [vertices[n] for n in indices]
            x0, y0, d0 = p[:3]
            x1, y1, d1 = q[:3]
            x2, y2, d2 = r[:3]
            surface_normal = _normal(p, q, r)
            if surface_normal[2] < 0:
                surface_normal = [-v for v in surface_normal]
            area = (x1-x0)*(y2-y0)-(y1-y0)*(x2-x0)
            if abs(area) < 1e-10:
                continue
            xa = max(0, math.ceil(min(x0, x1, x2)-0.5))
            xb = min(width-1, math.floor(max(x0, x1, x2)-0.5))
            ya = max(0, math.ceil(min(y0, y1, y2)-0.5))
            yb = min(height-1, math.floor(max(y0, y1, y2)-0.5))
            if xa > xb or ya > yb:
                continue
            inv = 1/area
            for y in range(ya, yb+1):
                sy = y + 0.5
                for x in range(xa, xb+1):
                    sx = x + 0.5
                    u = ((x1-sx)*(y2-sy)-(y1-sy)*(x2-sx))*inv
                    v = ((x2-sx)*(y0-sy)-(y2-sy)*(x0-sx))*inv
                    w = 1-u-v
                    if min(u,v,w) < -1e-9:
                        continue
                    z = 1/(u/d0+v/d1+w/d2)
                    if z < nearest[y,x]:
                        nearest[y,x] = z
                        rgb[y,x] = colour
                        alpha[y,x] = opacity
                        depth[y,x] = z
                        normals[y,x] = surface_normal
    return AOVFrame(rgb, "Linear-sRGB", alpha, depth, normals)


def render_exr(path, sha256, output, *, camera, frame, fps, width, height, timeout=45):
    """No-overwrite EXR, provenance and PNG preview, with rollback of all outputs."""
    from PIL import Image
    from .colour_aov import scene_linear_to_srgb8, write_exr

    output = Path(output).absolute()
    preview = output.with_suffix(".png")
    if output.suffix.lower() != ".exr" or not output.parent.is_dir():
        raise ValueError("USD OpenEXR output needs a new .exr path in an existing directory")
    receipt_file = Path(str(output) + ".receipt.json")
    if any(p.exists() or p.is_symlink() for p in (output, preview, receipt_file)):
        raise ValueError("USD EXR, preview or receipt already exists")
    scene = inspect(path, sha256, camera=camera, frame=frame, fps=fps,
                    width=width, height=height, timeout=timeout)
    aov = raster(scene)
    result = None
    try:
        result = write_exr(output, aov, source_evidence={
            "producer": "openusd-owned-scene-cpu-raster", "scene_sha256": sha256.lower(),
            "sample_timecode": scene["sample_timecode"], "time_seconds": scene["time_seconds"],
            "camera": scene["camera"], "up_axis": scene["up_axis"],
            "depth_units": "camera_metres", "normal_space": "camera",
            "hydra": "not_qualified"})
        encoded = scene_linear_to_srgb8(aov.rgb, tone_map="reinhard")
        import io
        buf = io.BytesIO()
        Image.fromarray(encoded, mode="RGB").save(buf, format="PNG")
        import tempfile
        fd, pending = tempfile.mkstemp(prefix=".fr-usd-", suffix=".png", dir=output.parent)
        try:
            with os.fdopen(fd, "wb") as dest:
                dest.write(buf.getvalue())
            os.link(pending, preview)
        finally:
            Path(pending).unlink(missing_ok=True)
        return {**result, "preview_sha256": digest(preview), "source_sha256": sha256.lower(),
                "hydra": "not_qualified", "frame": frame}
    except BaseException:
        if result is not None:
            output.unlink(missing_ok=True)
            receipt_file.unlink(missing_ok=True)
        preview.unlink(missing_ok=True)
        raise


def capabilities():
    """A Python USD package is not a qualified Hydra renderer."""
    from importlib import metadata
    try:
        version = metadata.version("usd-core")
    except metadata.PackageNotFoundError:
        version = None
    return {"schema": SCHEMA, "usd_core_version": version, "native_reader_available": version is not None,
            "hydra_renderer_available": False, "hydra_qualified": False,
            "supported_scene": "single-layer, no-external-assets USDA, triangles+perspective camera"}


def _entry():
    if len(sys.argv) != 3 or sys.argv[1] != "--worker":
        raise SystemExit(2)
    try:
        spec = json.loads(sys.argv[2])
        if not isinstance(spec, dict) or set(spec) != {"source", "sha256", "camera", "frame", "fps", "width", "height"}:
            raise ValueError("Invalid USD worker payload")
        output = _extract_native(spec)
        sys.stdout.write(json.dumps(output, sort_keys=True, allow_nan=False))
    except Exception:
        raise SystemExit(3) from None


if __name__ == "__main__":
    _entry()
