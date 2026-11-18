"""Generate a real OpenUSD time-sampled mesh, compare float AOVs and publish Pages evidence."""
import argparse
from hashlib import sha256
import html
import json
from pathlib import Path

import numpy as np

from framerudiments.colour_aov import read_exr
from framerudiments.usd_m15 import inspect, render_exr, raster, capabilities, digest


def make_scene(path):
    # Generated using real pxr APIs rather than hand-written mock USDA syntax.
    from pxr import Gf, Usd, UsdGeom, UsdShade, Sdf

    stage = Usd.Stage.CreateNew(str(path))
    stage.SetStartTimeCode(0)
    stage.SetEndTimeCode(24)
    stage.SetTimeCodesPerSecond(24)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.Xform.Define(stage, "/World")
    camera = UsdGeom.Camera.Define(stage, "/World/Camera")
    camera.AddTranslateOp().Set(Gf.Vec3d(0, 0, 6))
    camera.GetFocalLengthAttr().Set(35)
    camera.GetHorizontalApertureAttr().Set(20)
    camera.GetVerticalApertureAttr().Set(15)
    camera.GetClippingRangeAttr().Set(Gf.Vec2f(0.1, 100))
    mesh = UsdGeom.Mesh.Define(stage, "/World/Triangle")
    mesh.CreateSubdivisionSchemeAttr().Set(UsdGeom.Tokens.none)
    mesh.GetFaceVertexCountsAttr().Set([3])
    mesh.GetFaceVertexIndicesAttr().Set([0, 1, 2])
    start = [Gf.Vec3f(-1.0, -1.0, 0), Gf.Vec3f(1.0, -1.0, 0), Gf.Vec3f(0, 1.0, 0)]
    finish = [Gf.Vec3f(-0.7, -0.9, 0), Gf.Vec3f(1.3, -0.9, 0), Gf.Vec3f(0.3, 1.1, 0)]
    mesh.GetPointsAttr().Set(start, Usd.TimeCode(0))
    mesh.GetPointsAttr().Set(finish, Usd.TimeCode(24))
    # Bound constant UsdPreviewSurface, not an invented texture or external LUT.
    material = UsdShade.Material.Define(stage, "/World/Paint")
    shader = UsdShade.Shader.Define(stage, "/World/Paint/PreviewSurface")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(1.15, 0.35, 0.09))
    shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(1.0)
    shader.CreateOutput("surface", Sdf.ValueTypeNames.Token)
    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(material)
    stage.GetRootLayer().Save()
    assert path.is_file()
    return sha256(path.read_bytes()).hexdigest()


def qualify(output):
    output = Path(output)
    if output.exists():
        raise ValueError("USD qualification output must not already exist")
    output.mkdir(parents=True)
    stage_path = output / "source.usda"
    source_sha = make_scene(stage_path)
    # Refuse newly created or mutated external arcs, as the runtime also does.
    assert "@" not in stage_path.read_text(encoding="utf-8")
    assert capabilities()["native_reader_available"]
    summaries = []
    for frame in (0, 12, 24):
        scene = inspect(stage_path, source_sha, camera="/World/Camera", frame=frame,
                        fps="24", width=160, height=120)
        assert scene["triangles"] == 1 and scene["points"] == 3
        assert scene["meshes"][0]["material"] == "/World/Paint"
        assert scene["time_seconds"] == str(__import__("fractions").Fraction(frame, 24))
        aov = raster(scene)
        assert aov.depth.max() > 0 and aov.depth.max() <= 10
        assert np.any(aov.alpha > 0)
        assert np.max(aov.rgb) > 1  # Real unbounded float colour, not SDR recovery.
        destination = output / f"frame-{frame:02d}.exr"
        receipt = render_exr(stage_path, source_sha, destination, camera="/World/Camera",
                             frame=frame, fps="24", width=160, height=120)
        assert receipt["hydra"] == "not_qualified"
        png = destination.with_suffix(".png")
        assert png.is_file() and png.stat().st_size > 100
        reopened = read_exr(destination, sha256=digest(destination))
        for key in aov.channels():
            assert np.array_equal(aov.channels()[key], reopened.channels()[key])
        summaries.append({"frame": frame, "time_seconds": scene["time_seconds"],
                          "triangles": scene["triangles"], "source_sha256": source_sha,
                          "exr_sha256": digest(destination), "png_sha256": digest(png),
                          "aov_channels": list(aov.channels()), "native_usd": True,
                          "hydra_qualified": False})
    assert len({item["png_sha256"] for item in summaries}) == 3
    report = {"schema": "framerudiments.usd-m15-qual.v1", "source_sha256": source_sha,
              "frames": summaries, "native_geometry": True, "hydra_qualified": False}
    (output / "qualification.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", "utf-8")
    cards = "\n".join(
        f'<figure><img src="frame-{r["frame"]:02d}.png" alt="Actual OpenUSD frame {r["frame"]}" width="640" height="480">'
        f'<figcaption>USD frame {r["frame"]} / 24 · time {html.escape(r["time_seconds"])} s · '
        f'<a href="frame-{r["frame"]:02d}.exr">Verified float32 EXR</a></figcaption></figure>'
        for r in summaries
    )
    page = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FrameRudiments · Native OpenUSD scene and AOV gallery</title>
<style>:root{color-scheme:dark;font-family:system-ui;background:#0b1220;color:#edf7ff}body{margin:auto;max-width:1100px;padding:2rem}
a{color:#68ead1}h1{font-size:clamp(2rem,5vw,4rem)}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:1rem}
figure{margin:0;padding:1rem;border:1px solid #3c5a75;border-radius:1rem;background:#182639}img{max-width:100%;height:auto;display:block}
figcaption{font-size:.9rem;color:#ccdfef;padding-top:1rem}</style>
<p><a href="../">Visualisation atlas</a> · <a href="../timeline/">Timeline viewer</a></p>
<h1>OpenUSD, pixel by pixel</h1><p>Real time-sampled USD geometry and a constant UsdPreviewSurface material, read by pxr in a bounded worker. Native CPU rasterisation yields positive camera-space depth in metres and camera-facing normals; float32 EXR channels are independently decoded and compared. Frames are actual CI outputs—not mockups.</p>
<p><strong>Hydra is not qualified.</strong> No hardware or GPU/USD renderer is claimed. Unsupported scene prims, external assets and USD compositions fail closed.</p>
<div class="grid">''' + cards + '''</div><p><a href="source.usda">Pinned source</a> · <a href="qualification.json">Frame hashes and proof</a></p></html>'''
    (output / "index.html").write_text(page, encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = qualify(args.output)
    print(json.dumps({"source_sha256": report["source_sha256"], "frames": len(report["frames"])}))


if __name__ == "__main__":
    main()
