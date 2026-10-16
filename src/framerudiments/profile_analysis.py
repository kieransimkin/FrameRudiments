"""Run in a released PrismMoves/PixelCue interpreter; this module owns no image analysis."""
import argparse
import hashlib
from html import escape
import importlib.metadata
import json
from pathlib import Path
import statistics


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)+"\n", encoding="utf-8")


def verify_report(directory):
    for line in (directory / "SHA256SUMS.txt").read_text(encoding="ascii").splitlines():
        expected, name = line.split(maxsplit=1)
        name = name.lstrip("*")
        if Path(name).name != name or hashlib.sha256((directory/name).read_bytes()).hexdigest() != expected:
            raise ValueError("Retained analysis report checksum differs")


def measured_axes(profile):
    """Select named values from PixelCue's report; no replacement image engine."""
    c,t,g,f = (profile["measurements"][key] for key in ("colour","texture","composition","frequency"))
    axes = {f"colour.{name}":c[name] for name in
            ("mean_saturation","luminance_std","dark_fraction_below_0_05","light_fraction_above_0_8")}
    axes.update({f"texture.{name}":t[name] for name in
                 ("grayscale_entropy_bits","lbp_entropy_bits","glcm_contrast","glcm_homogeneity","glcm_energy")})
    axes.update({f"composition.{name}":g[name] for name in
                 ("edge_density_above_0_08","horizontal_mirror_mae","vertical_mirror_mae")})
    axes["colour.median_linear_luminance"] = c["relative_luminance_percentiles"][2]
    axes.update({f"frequency.{name}_power_fraction":value for name,value in
                 zip(("low","mid","high"),f["radial_power_fractions_low_mid_high"])})
    return axes


def summarise(manifest, *, max_edge=256, models=(), models_dir=None):
    from prismmoves.cli import main as prism_main
    from pixelcue.visual_service import compare_profiles

    manifest = Path(manifest).resolve()
    content = manifest.read_bytes()
    captures = json.loads(content)
    versions = {name: importlib.metadata.version(name) for name in ("prismmoves", "pixelcue", "numpy", "Pillow")}
    settings = dict(models_dir=str(models_dir) if models_dir else None, manifest_sha256=hashlib.sha256(content).hexdigest(), max_edge=max_edge,
                    models=list(models), versions=versions)
    destination = manifest.parent / "identity"
    if (destination / "response.json").is_file():
        verify_report(destination)
        existing = json.loads((destination / "response.json").read_text(encoding="utf-8"))
        if existing.get("settings") == settings:
            return existing
        archived = destination.with_name("identity-" + existing["settings"]["manifest_sha256"][:16])
        if archived.exists() or archived.resolve().parent != manifest.parent:
            raise ValueError("Retain the existing identity; its archive target already exists")
        destination.rename(archived)
    destination.mkdir(exist_ok=True)
    profiles, paths, receipts = {}, {}, []
    for condition in captures["conditions"]:
        for sample in condition["captures"]:
            for key in ("input", "output"):
                ref = sample[key]
                data = Path(ref["path"]).read_bytes()
                if hashlib.sha256(data).hexdigest() != ref["sha256"]:
                    raise ValueError("Capture changed before PrismMoves analysis")
                paths[ref["sha256"]] = ref["path"]
    entries = list(paths.items())
    for start in range(0, len(entries), 64):
        selected = entries[start:start+64]
        batch = destination / f"prism-{start//64:04}"
        args = [path for _, path in selected] + ["--out", str(batch), "--max-edge", str(max_edge),
                "--role", "controlled preset-response capture", "--lineage", captures["preset"]["id"]]
        if models_dir:
            args += ["--models-dir", str(models_dir)]
        if models:
            args += ["--models", *models]
        if batch.exists():
            verify_report(batch)
            report = json.loads((batch / "analysis.json").read_text(encoding="utf-8"))
            expected = {digest for digest, _ in selected}
            actual = {item["analysis"]["source"]["sha256"] for item in report["items"] if item["status"] == "completed"}
            if expected != actual:
                raise ValueError("Interrupted Prism batch evidence differs; use a new capture attempt")
            code = 2 if any(item["status"] != "completed" or
                any(model["status"] != "completed" for model in item.get("analysis", {}).get("models", []))
                for item in report["items"]) else 0
        else:
            code = prism_main(args)
            if code not in (0, 2) or not (batch / "analysis.json").is_file():
                raise RuntimeError("PrismMoves did not produce a usable evidence report")
            report = json.loads((batch / "analysis.json").read_text(encoding="utf-8"))
        for item in report["items"]:
            if item["status"] == "completed":
                profile = item["analysis"]
                profiles[profile["source"]["sha256"]] = profile
        receipts.append(dict(directory=batch.name, exit_code=code))
    conditions, paired = [], []
    for condition in captures["conditions"]:
        result = {key: condition[key] for key in ("id", "sample", "imagery", "audio_mode", "state", "fps", "dimensions", "title")}
        result["source_to_output"], result["temporal"], result["captures"] = [], [], condition["captures"]
        previous = None
        for sample in condition["captures"]:
            a, b = (profiles.get(sample[key]["sha256"]) for key in ("input", "output"))
            if a and b:
                distance = compare_profiles([a, b])["pairs"][0]
                source_axes, output_axes = measured_axes(a), measured_axes(b)
                distance["measured_axis_changes"] = {key:dict(input=value, output=output_axes[key],
                    signed_difference=output_axes[key]-value) for key,value in source_axes.items()}
                distance.update(frame=sample["frame"], output_seconds=sample["output_seconds"])
                result["source_to_output"].append(distance)
            if previous and b:
                result["temporal"].append(dict(from_frame=previous[0], to_frame=sample["frame"],
                    comparison=compare_profiles([previous[1], b])["pairs"][0]))
            if b:
                previous = sample["frame"], b
        if "error" in condition:
            result["error"] = condition["error"]
        conditions.append(result)
    # Match only equal frame/imagery/sample conditions. Keep separate axes and exact controls.
    for i, a in enumerate(conditions):
        for b in conditions[i+1:]:
            relation = None
            if a["sample"] == b["sample"] and a["imagery"] == b["imagery"] and a["audio_mode"] != b["audio_mode"]:
                relation = "programme versus silence; projectM unseeded noise remains a confound"
            elif a["imagery"] == b["imagery"] and a["imagery"] != "cover-lyrics" and a["audio_mode"] == b["audio_mode"] == "programme" and a["sample"] != b["sample"]:
                relation = "same static card, different tune excerpts"
            if relation:
                by_frame = {x["frame"]: x for x in b["captures"]}
                comparisons = []
                for x in a["captures"]:
                    if x["frame"] in by_frame:
                        left = profiles.get(x["output"]["sha256"])
                        right = profiles.get(by_frame[x["frame"]]["output"]["sha256"])
                        if left and right:
                            comparisons.append(dict(frame=x["frame"], comparison=compare_profiles([left, right])["pairs"][0]))
                paired.append(dict(a=a["id"], b=b["id"], relation=relation, comparisons=comparisons))
    axes = ("hue_js_divergence_bits", "texture_lbp_js_divergence_bits", "saturation_absolute_difference")
    synthesis = {}
    for axis in axes:
        values = [x[axis] for c in conditions for x in c["source_to_output"] if x[axis] is not None]
        synthesis[axis] = dict(min=min(values), median=statistics.median(values), max=max(values), n=len(values)) if values else None
    axis_ranges = {}
    for c in conditions:
        for sample in c["source_to_output"]:
            for key,value in sample["measured_axis_changes"].items():
                axis_ranges.setdefault(key,[]).append(value["signed_difference"])
    axis_ranges = {key:dict(min=min(values),median=statistics.median(values),max=max(values),n=len(values))
                   for key,values in axis_ranges.items()}
    response = dict(measured_axis_change_ranges=axis_ranges, schema="framerudiments.preset-response.v1", settings=settings, preset=captures["preset"],
        state="completed" if captures["state"] == "captured" and all(x["exit_code"] == 0 for x in receipts) else "partial",
        prism_reports=receipts, conditions=conditions, controlled_comparisons=paired, measured_response_ranges=synthesis,
        interpretation="Input-conditioned measured visual profile. Review each condition family and contact sheet before inferring a reusable artistic identity.",
        unknowns=["Unmeasured: semantic motif, artistic intent, actual lyric legibility, flash safety and audience response.",
                  "Frame grabs share a capture lineage; they are not independent artwork authorities.",
                  "No single aggregate identity/aesthetic score. GPU/driver/engine randomness and frame cadence affect results.",
                  "Statistics cannot attribute every change to audio alone; retain silence and shared-card controls."])
    lines = [f"# {captures['preset']['name']}", "", response["interpretation"], "",
             f"PrismMoves {versions['prismmoves']}; PixelCue {versions['pixelcue']}. State: {response['state']}.", "",
             "## Measured response", "", "Observed source-to-output ranges across the retained conditions:", "",
             "| Axis | Minimum | Median | Maximum | Pairs |", "|---|---:|---:|---:|---:|"]
    for axis, value in synthesis.items():
        if value:
            lines.append(f"| {axis} | {value['min']:.5f} | {value['median']:.5f} | {value['max']:.5f} | {value['n']} |")
    for axis, value in axis_ranges.items():
        lines.append(f"| signed change: {axis} | {value['min']:.5f} | {value['median']:.5f} | {value['max']:.5f} | {value['n']} |")
    lines += ["", "The JSON retains per-condition, temporal, programme/silence and shared-card comparisons. These are separate measured axes; no brand or quality score is calculated.", "", "## Evidence and review", ""]
    for c in conditions:
        lines += [f"### {c['id']}", "", f"{c['title']}; {c['state']}; {c['audio_mode']}. Source and output at matching output-frame times:", ""]
        for sample in c["captures"]:
            for key in ("input", "output"):
                rel = os_relative(sample[key]["path"], destination)
                lines += [f"![{key} frame at {sample['output_seconds']:.3f} seconds]({rel})", ""]
        if c.get("error"):
            lines += [f"Unavailable: {c['error']}", ""]
    lines += ["## Interpretation and unknowns", "", *[f"- {x}" for x in response["unknowns"]], "",
              "Preserve the source manifests, raw PrismMoves reports and condition groups. Inspect the sample lyrics and cover images, then record a reviewed visual thesis, core consistencies, controlled variation and outliers. Missing evidence remains unknown; failed presets receive no invented identity.", ""]
    (destination / "report.md").write_text("\n".join(lines), encoding="utf-8")
    gallery = []
    for c in conditions:
        gallery.append(f"<section><h2>{escape(c['id'])}</h2><p>{escape(c['state'])}</p>")
        for sample in c["captures"]:
            gallery.append(f"<figure><figcaption>{sample['output_seconds']:.3f}s — input / output</figcaption>")
            for key in ("input", "output"):
                gallery.append(f'<img loading="lazy" width="256" alt="{key} matching-time capture" src="{escape(os_relative(sample[key]["path"], destination), quote=True)}">')
            gallery.append("</figure>")
        gallery.append("</section>")
    (destination / "report.html").write_text('<!doctype html><meta charset="utf-8"><title>Preset response evidence</title><style>body{font:16px system-ui;background:#121923;color:#eee;max-width:1100px;margin:auto;padding:24px}img{max-width:46%;height:auto;margin:8px}figure{border:1px solid #526;padding:8px}pre{white-space:pre-wrap}</style>'+
        f"<h1>{escape(captures['preset']['name'])}</h1><p>{escape(response['interpretation'])}</p>"+
        "<p>Images are actual retained renderer captures. Static cards are generated calibration illustrations. Playback/flash safety and legibility remain unverified.</p>"+
        "".join(gallery)+"<h2>Measured response and unknowns</h2><pre>"+escape(json.dumps(dict(ranges=synthesis, axis_changes=axis_ranges, unknowns=response["unknowns"]), indent=2))+"</pre>", encoding="utf-8")
    dump(destination / "response.json", response)
    checksums = "".join(hashlib.sha256((destination/name).read_bytes()).hexdigest()+"  "+name+"\n"
                        for name in ("response.json", "report.md", "report.html"))
    (destination / "SHA256SUMS.txt").write_text(checksums,encoding="ascii")
    return response


def os_relative(path, base):
    import os
    return Path(os.path.relpath(path, base)).as_posix()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest")
    parser.add_argument("--max-edge", type=int, default=256)
    parser.add_argument("--models-dir", type=Path)
    parser.add_argument("--model", action="append", choices=("clip", "dinov2", "blip", "ocr"), default=[])
    args = parser.parse_args()
    result = summarise(args.manifest, max_edge=args.max_edge, models=args.model, models_dir=args.models_dir)
    print(json.dumps(dict(state=result["state"], conditions=len(result["conditions"]))))
