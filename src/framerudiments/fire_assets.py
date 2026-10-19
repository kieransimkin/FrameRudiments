"""Hash-pinned real/simulated flame flipbooks with explicit provenance and clocks."""

from fractions import Fraction
from collections import OrderedDict
import json
import math
from pathlib import Path
import subprocess
import tempfile

from PIL import Image, ImageChops, ImageFilter

from .io import file_ref, fields, integer, number, sha256, write_json


class FireAtlas:
    def __init__(self, reference, base=None):
        base = Path(base or ".")
        self.reference = file_ref(reference, base)
        self.path = Path(self.reference["path"])
        if self.path.stat().st_size > 1_048_576:
            raise ValueError("Atlas manifest exceeds one megabyte")
        data = json.loads(self.path.read_text(encoding="utf-8-sig"))
        fields(data, {"schema", "image", "columns", "rows", "frames", "fps", "provenance"}, "fire atlas")
        if data.get("schema") != "framerudiments.fire-atlas.v1":
            raise ValueError("Expected fire-atlas.v1")
        if not isinstance(data.get("provenance", {}), dict):
            raise ValueError("Atlas provenance must be an object")
        self.image_ref = file_ref(data["image"], self.path.parent)
        self.columns = integer(data["columns"], "atlas columns", minimum=1, maximum=32)
        self.rows = integer(data["rows"], "atlas rows", minimum=1, maximum=32)
        self.frames = integer(data["frames"], "atlas frames", minimum=1, maximum=self.columns * self.rows)
        try:
            self.fps = Fraction(str(data["fps"]))
        except (ValueError, ZeroDivisionError) as exc:
            raise ValueError("Invalid flame atlas frame rate") from exc
        if not 1 <= self.fps <= 240:
            raise ValueError("Atlas FPS must be between 1 and 240")
        with Image.open(self.image_ref["path"]) as image:
            if image.width * image.height > 67_108_864:
                raise ValueError("Atlas exceeds64 megapixels")
            if image.mode != "RGBA":
                raise ValueError("Fire atlas must be RGBA")
            self.image = image.copy()
        if self.image.width % self.columns or self.image.height % self.rows:
            raise ValueError("Atlas cells must divide exactly")
        self.cell_size = (self.image.width // self.columns, self.image.height // self.rows)
        self.duration = float(self.frames / self.fps)
        self._cells = OrderedDict()
        self.closed = False
        self.evidence = {
            "atlas": self.reference,
            "image": self.image_ref,
            "frames": self.frames,
            "fps": str(self.fps),
            "provenance": data.get("provenance", {}),
            "clock": "floor(asset_seconds * atlas_fps); explicit repeat/clamp",
        }

    def cell(self, index):
        if self.closed:
            raise RuntimeError("Fire atlas is closed")
        index = integer(index, "atlas frame", maximum=self.frames - 1)
        if index in self._cells:
            self._cells.move_to_end(index)
            return self._cells[index]
        w, h = self.cell_size
        x = index % self.columns * w
        y = index // self.columns * h
        result = self.image.crop((x, y, x + w, y + h))
        self._cells[index] = result
        if len(self._cells) > 96:
            self._cells.popitem(last=False)
        return result

    def close(self):
        if self.closed:
            return
        for image in self._cells.values():
            image.close()
        self._cells.clear()
        self.image.close()
        self.closed = True

    def frame_at(self, seconds, *, repeat=False):
        if type(repeat) is not bool:
            raise ValueError("Atlas repeat must be a boolean")
        seconds = number(seconds, "flame asset seconds", minimum=0)
        index = math.floor(Fraction(str(seconds)) * self.fps)
        if repeat:
            index %= self.frames
        else:
            index = min(self.frames - 1, index)
        return self.cell(index).copy()


def key_black(image, *, black_level=0.035, softness=0.025):
    """Black-screen emission key with unpremultiplication, not a hard luminance cut."""
    import numpy as np

    black = number(black_level, "black level", minimum=0, maximum=0.25)
    soft = number(softness, "key softness", minimum=0.001, maximum=0.2)
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32) / 255
    peak = np.max(rgb, axis=2)
    alpha = np.clip((peak - black) / (1 - black), 0, 1)
    edge = np.clip((peak - black) / soft, 0, 1)
    edge = edge * edge * (3 - 2 * edge)
    alpha *= edge
    straight = np.clip((rgb - black) / np.maximum(alpha[:, :, None], 1e-6), 0, 1)
    out = np.dstack((straight, alpha))
    return Image.fromarray(np.rint(out * 255).astype(np.uint8))


def pack_frames(paths, output, *, fps, cell_size=(256, 256), provenance=None, columns=12):
    paths = list(map(Path, paths))
    output = Path(output)
    if not paths or len(paths) > 480:
        raise ValueError("Pack between1 and480 bounded flame frames")
    try:
        fps = Fraction(str(fps))
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError("Invalid flame atlas frame rate") from exc
    if not 1 <= fps <= 240:
        raise ValueError("Atlas FPS must be between1 and240")
    columns = integer(columns, "atlas columns", minimum=1, maximum=32)
    rows = math.ceil(len(paths) / columns)
    if rows > 32:
        raise ValueError("Too many atlas rows")
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    if not isinstance(cell_size, (tuple, list)) or len(cell_size) != 2:
        raise ValueError("Cell size needs width and height")
    w, h = (integer(v, "flame cell size", minimum=16, maximum=512) for v in cell_size)
    if w * h * columns * rows > 67_108_864:
        raise ValueError("Packed atlas exceeds64 megapixels")
    sheet = Image.new("RGBA", (w * columns, h * rows))
    for i, path in enumerate(paths):
        with Image.open(path) as source:
            tile = source.convert("RGBA")
            if tile.size != (w, h):
                tile = tile.convert("RGBa").resize((w, h), Image.Resampling.LANCZOS).convert("RGBA")
        sheet.alpha_composite(tile, (i % columns * w, i // columns * h))
    image = output / "flames.png"
    sheet.save(image)
    manifest = output / "atlas.json"
    write_json(
        manifest,
        {
            "schema": "framerudiments.fire-atlas.v1",
            "image": {"path": image.name, "sha256": sha256(image)},
            "columns": columns,
            "rows": rows,
            "frames": len(paths),
            "fps": str(fps),
            "provenance": provenance or {},
        },
    )
    return manifest


def pack_mantaflow(directory, output, *, licence, cell_size=(320, 256)):
    """Pack our bounded Blender bake with a stable luminous crop and public-safe receipt."""
    import numpy as np

    directory, output = Path(directory), Path(output)
    receipt_path = directory / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
    paths = sorted((directory / "frames").glob("*.png"))
    if receipt.get("backend") != "blender-mantaflow" or receipt.get("state") != "baked and rendered":
        raise ValueError("Expected a completed maintained Mantaflow bake")
    if len(paths) != receipt.get("frame_count") or not 12 <= len(paths) <= 480:
        raise ValueError("Incomplete or unbounded Mantaflow frame set")
    boxes = []
    size = None
    for path in paths:
        with Image.open(path) as tile:
            if tile.mode != "RGBA" or size is not None and tile.size != size:
                raise ValueError("Bake frames need matching RGBA dimensions")
            size = tile.size
            a = np.asarray(tile, dtype=np.float32)
            luminous = np.max(a[:, :, :3], axis=2)
            box = Image.fromarray(np.where(luminous > 12, 255, 0).astype(np.uint8)).getbbox()
            if box:
                boxes.append(box)
    if not boxes:
        raise ValueError("Bake has no luminous fire")
    crop = (
        max(0, min(b[0] for b in boxes) - 4),
        max(0, min(b[1] for b in boxes) - 8),
        min(size[0], max(b[2] for b in boxes) + 4),
        min(size[1], max(b[3] for b in boxes) + 1),
    )
    settings = {k: v for k, v in receipt["settings"].items() if k != "output"}
    provenance = dict(
        method="blender-mantaflow",
        blender=receipt["blender"],
        blender_build=receipt["blender_build"],
        settings=settings,
        volume_render_settings=receipt.get("volume_render_settings", {}),
        source_blend_sha256=sha256(directory / "source.blend"),
        frame_sha256=[sha256(p) for p in paths],
        stable_luminous_crop=crop,
        alpha_treatment="RGB emission keyed over black at0.035; Blender extinction alpha not used",
        licence=licence,
        scope=receipt["scope"],
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bake-pack-", dir=output.parent) as tmp:
        prepared = []
        for n, path in enumerate(paths):
            with Image.open(path) as tile:
                tile = tile.crop(crop)
                # Blender volume alpha represents extinction, not emitted radiance.
                # Re-key the RGB emission onto black so a low-smoke fire stays visible.
                tile = key_black(tile.convert("RGB"), black_level=0.035)
                dest = Path(tmp) / f"{n:04d}.png"
                tile.save(dest)
            prepared.append(dest)
        return pack_frames(prepared, output, fps=settings["fps"], cell_size=cell_size, provenance=provenance)


def prepare_footage(
    reference,
    output,
    *,
    ffmpeg,
    ffprobe,
    start=0,
    duration=4,
    fps=60,
    crop=None,
    rotation=0,
    black_level=0.035,
    cell_size=(256, 256),
    source_url="",
    licence="unverified",
):
    """Decode an explicitly supplied source only; never download or infer rights."""
    source = file_ref(reference, Path("."))
    output = Path(output)
    duration = number(duration, "fire excerpt duration", minimum=0.1, maximum=8)
    start = number(start, "fire excerpt start", minimum=0)
    fps = integer(fps, "fire atlas FPS", minimum=12, maximum=60)
    rotation = number(rotation, "fire rotation", minimum=-180, maximum=180)
    if Path(source["path"]).stat().st_size > 1_073_741_824:
        raise ValueError("Fire source exceeds1GiB")
    probe = json.loads(
        subprocess.check_output(
            [str(ffprobe), "-v", "error", "-show_streams", "-show_format", "-of", "json", source["path"]]
        )
    )
    stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
    if start + duration > float(probe["format"]["duration"]) + 0.001:
        raise ValueError("Fire excerpt exceeds source duration")
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="fire-key-", dir=output.parent) as scratch:
        scratch = Path(scratch)
        filters = []
        if crop:
            if len(crop) != 4:
                raise ValueError("Fire crop needs x,y,width,height")
            x, y, w, h = [integer(v, "fire crop") for v in crop]
            if min(w, h) < 2 or x + w > stream["width"] or y + h > stream["height"]:
                raise ValueError("Fire crop outside source")
            filters.append(f"crop={w}:{h}:{x}:{y}")
        filters += [f"fps={fps}", "scale=640:-2"]
        subprocess.run(
            [
                str(ffmpeg),
                "-n",
                "-v",
                "error",
                "-ss",
                str(start),
                "-i",
                source["path"],
                "-t",
                str(duration),
                "-an",
                "-vf",
                ",".join(filters),
                "-frames:v",
                str(math.ceil(duration * fps)),
                str(scratch / "raw-%04d.png"),
            ],
            check=True,
            capture_output=True,
        )
        keyed = []
        union = None
        for i, path in enumerate(sorted(scratch.glob("raw-*.png"))):
            with Image.open(path) as image:
                tile = key_black(image, black_level=black_level)
            if rotation:
                tile = tile.rotate(rotation, Image.Resampling.BICUBIC, expand=True)
            # Remove isolated key noise before establishing one stable crop for every frame.
            alpha = tile.getchannel("A")
            box = alpha.point(lambda a: 255 if a > 35 else 0).getbbox()
            if box:
                union = (
                    box
                    if union is None
                    else (
                        min(union[0], box[0]),
                        min(union[1], box[1]),
                        max(union[2], box[2]),
                        max(union[3], box[3]),
                    )
                )
            dest = scratch / f"key-{i:04d}.png"
            tile.save(dest)
            keyed.append(dest)
        if union is None:
            raise ValueError("Keyed fire asset contains no visible flame")
        for path in keyed:
            with Image.open(path) as tile:
                tile = tile.crop(union)
                # Feather crop boundaries; preserve natural thin tips and avoid rectangular seams.
                mask = Image.new("L", tile.size, 255)
                from PIL import ImageDraw

                d = ImageDraw.Draw(mask)
                d.rectangle((0, 0, tile.width - 1, tile.height - 1), outline=0, width=2)
                mask = mask.filter(ImageFilter.GaussianBlur(2))
                tile.putalpha(ImageChops.multiply(tile.getchannel("A"), mask))
                tile.save(path)
        provenance = {
            "method": "filmed flame / black-screen emission key",
            "source": {"sha256": source["sha256"], "bytes": Path(source["path"]).stat().st_size},
            "source_url": source_url,
            "licence": licence,
            "source_probe": {
                k: stream.get(k) for k in ("width", "height", "avg_frame_rate", "time_base", "duration")
            },
            "source_start_seconds": start,
            "duration_seconds": duration,
            "output_fps": fps,
            "crop": crop,
            "rotation": rotation,
            "black_level": black_level,
            "key": "sRGB approximate emission alpha/unpremultiplication",
            "audio": "discarded; never part of song soundtrack",
            "temporal_mapping": "Explicit FFmpeg fps resample; not original source FPS",
            "stable_alpha_crop": union,
        }
        return pack_frames(keyed, output, fps=fps, cell_size=cell_size, provenance=provenance)
