"""Pinned, separately installed upstream preset libraries; never bundled as MIT assets."""
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile
import urllib.request
import zipfile

from .io import sha256, write_json

PACKS = {
    "butterchurn": dict(engine="butterchurn", version="2.4.7", licence="MIT",
        url="https://registry.npmjs.org/butterchurn-presets/-/butterchurn-presets-2.4.7.tgz",
        sha256="d9c067b354e4ed87b3a1dace1dc59c9d64e6a99300a82d5fa086f696e35204a7"),
    "cream": dict(engine="projectm", repository="projectM-visualizer/presets-cream-of-the-crop",
        commit="0180df21f5e0bd39b9060cc5de420ed2f1f9e509",
        sha256="5600f974497d8f73295393fbdf866895ceb89efe82892f5e2d019358b2659202"),
    "classic": dict(engine="projectm", repository="projectM-visualizer/presets-projectm-classic",
        commit="14a6244a7d32eb7e114e1a92d1cb93358cdcc54a",
        sha256="328a41acf9216057fce0d873b244ca06e1aee5b466f7560df4903fdf28832ad0"),
    "original": dict(engine="projectm", repository="projectM-visualizer/presets-milkdrop-original",
        commit="e03b83e3338d8f1ed6cbcf908c719f249ef24288",
        sha256="4479bbd10e4f4a005d636f7b0ed26004042541a62a925ca97e12ff13765b688d"),
    "en-d": dict(engine="projectm", repository="projectM-visualizer/presets-en-d",
        commit="fff71ea81223109f3558351667eef851f2781c96",
        sha256="6badaf79e1ff095a5b56d38a380dd9b9ac8c2b5c6f6770879cbc0f65d549b5e1"),
    "textures": dict(engine="textures", repository="projectM-visualizer/presets-milkdrop-texture-pack",
        commit="6368812f27bc747b517218fbf89d21d59afce4d9",
        sha256="38e93612f79a313830480d7458d8eb065d2b67992211a5dd0c92ef87b8607f6a"),
}
for _pin in PACKS.values():
    if "url" not in _pin:
        _pin["url"] = f"https://codeload.github.com/{_pin['repository']}/zip/{_pin['commit']}"
        _pin["licence"] = "No explicit preset/texture grant established; retain upstream notices; not redistributed in FrameRudiments"


def archive_members(data, *, tar=False):
    """Read bounded ordinary files only; never extract paths, links or devices."""
    if tar:
        archive = tarfile.open(fileobj=io.BytesIO(data))
        entries = ((x.name, x.size, lambda x=x: archive.extractfile(x).read())
                   for x in archive.getmembers() if x.isfile())
    else:
        archive = zipfile.ZipFile(io.BytesIO(data))
        entries = ((x.filename, x.file_size, lambda x=x: archive.read(x))
                   for x in archive.infolist() if not x.is_dir())
    total = 0
    with archive:
        for name, size, read in entries:
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
                raise ValueError(f"Unsafe archive member: {name}")
            total += size
            if size > 32 * 1024**2 or total > 512 * 1024**2:
                raise ValueError("Preset archive exceeds bounded extraction size")
            yield str(PurePosixPath(*path.parts[1:])), read()


def install_library(directory, *, packs=tuple(PACKS), cache=None):
    """Explicit download/installation. Calling this acknowledges upstream executable presets."""
    out = Path(directory).resolve()
    if out.exists():
        raise ValueError("Choose a new preset-library directory")
    if not packs or set(packs) - PACKS.keys():
        raise ValueError("Unknown or empty preset-pack selection")
    out.mkdir(parents=True)
    rows, receipts, textures = [], [], []
    seen = {}
    for name in packs:
        pin = PACKS[name]
        filename = "butterchurn-presets-2.4.7.tgz" if name == "butterchurn" else name + ".zip"
        cached = Path(cache) / filename if cache else None
        if cached and cached.is_file():
            data = cached.read_bytes()
        else:
            with urllib.request.urlopen(pin["url"], timeout=180) as response:
                data = response.read(64 * 1024**2 + 1)
        if hashlib.sha256(data).hexdigest() != pin["sha256"]:
            raise ValueError(f"Pinned preset archive checksum differs: {name}")
        pack = out / name
        pack.mkdir()
        notices, count = [], 0
        for original, content in archive_members(data, tar=name == "butterchurn"):
            path = PurePosixPath(original)
            if path.name.lower().startswith(("license", "copying", "readme")):
                target = pack / path.name
                target.write_bytes(content)
                notices.append(dict(path=str(target), sha256=sha256(target)))
            suffix = path.suffix.lower()
            is_preset = (name == "butterchurn" and original.startswith("presets/converted/") and suffix == ".json"
                         or pin["engine"] == "projectm" and suffix in {".milk", ".prjm"})
            is_texture = name == "textures" and suffix in {".png", ".jpg", ".jpeg", ".bmp", ".dds", ".tga"}
            if not is_preset and not is_texture:
                continue
            digest = hashlib.sha256(content).hexdigest()
            if is_texture:
                target = pack / path.name
                if target.exists() and target.read_bytes() != content:
                    raise ValueError(f"Texture basename collision: {original}")
                target.write_bytes(content)
                textures.append(dict(path=str(target), sha256=digest, original=original))
                continue
            count += 1
            key = (pin["engine"], digest)
            alias = dict(pack=name, original=original, name=path.stem)
            if key in seen:
                seen[key]["aliases"].append(alias)
                continue
            target = pack / (digest + suffix)
            target.write_bytes(content)
            row = dict(id=pin["engine"] + ":" + digest, engine=pin["engine"], name=path.stem,
                       preset=dict(path=str(target), sha256=digest), aliases=[alias],
                       source_pack=name, licence=pin["licence"], compatibility="unverified until actual rendering")
            rows.append(row)
            seen[key] = row
        receipts.append(dict(id=name, **pin, preset_files=count, notices=notices))
    result = dict(schema="framerudiments.preset-library.v1", packs=receipts, presets=rows,
                  textures=textures, limits="Source membership and syntax are not shader/GPU acceptance or rights clearance.")
    write_json(out / "catalogue.json", result)
    return result


def load_library(path):
    path = Path(path).resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != "framerudiments.preset-library.v1":
        raise ValueError("Expected framerudiments.preset-library.v1")
    ids = [x["id"] for x in data["presets"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate preset identities")
    return data
