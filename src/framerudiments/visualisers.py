"""Full-frame, sequential MilkDrop post-processing for composed lyric videos."""
from array import array
import base64
import json
import mmap
import os
from pathlib import Path
import shutil
import subprocess

from PIL import Image

from .encoding import audio_filters
from .io import fields, integer, number, sha256

ASSETS = Path(__file__).with_name("visualiser_assets")
PRESETS = ("feedback", "kaleidoscope", "julia")


def effect_specs(project):
    configs = project.data.get("effects", [])
    if not isinstance(configs, list) or len(configs) > 8:
        raise ValueError("effects must be an ordered list with at most eight stages")
    specs = []
    for cfg in configs:
        fields(cfg, {"id", "engine", "preset", "input_mix", "seed", "trusted_preset"}, "full-frame effect")
        name = cfg.get("id")
        if not isinstance(name, str) or not name or name in [item["id"] for item in specs]:
            raise ValueError("Full-frame effect ids must be nonempty and unique")
        engine = cfg.get("engine")
        if engine not in {"butterchurn", "projectm"}:
            raise ValueError("Full-frame engine must be butterchurn or projectm")
        preset = cfg.get("preset", "feedback")
        if isinstance(preset, str):
            if preset not in PRESETS:
                raise ValueError(f"Unknown built-in visualiser preset: {preset}")
            path = ASSETS / f"{preset}.{'json' if engine == 'butterchurn' else 'milk'}"
            ref = project.ref(dict(path=str(path), sha256=sha256(path)))
        else:
            if cfg.get("trusted_preset") is not True:
                raise ValueError("External presets contain executable equations/shaders: set trusted_preset true after review")
            ref = project.ref(preset)
        specs.append(dict(id=name, engine=engine, preset=ref,
                          input_mix=number(cfg.get("input_mix", .65), "input mix", minimum=.001, maximum=1),
                          seed=integer(cfg.get("seed", 0), "visualiser seed", maximum=2**32 - 1)))
    return specs


class EffectAudio:
    """Windowed native-rate PCM from the encoder's exact excerpt/repeat/fade chain."""
    def __init__(self, project, ffmpeg, output):
        self.project, self.handle, self.data = project, None, None
        path = Path(output) / "effects-audio.f32"
        command = [ffmpeg, "-n", "-v", "error", "-i", project.audio_ref["path"], "-map", "0:a:0",
                   "-af", audio_filters(project), "-ac", "2", "-ar", str(project.sample_rate),
                   "-f", "f32le", str(path)]
        subprocess.run(command, check=True, capture_output=True)
        self.samples = path.stat().st_size // 8
        expected = (project.end_sample - project.start_sample) * project.repeats
        if self.samples != expected or path.stat().st_size % 8:
            raise ValueError("Effect PCM does not match the encoded programme sample count")
        self.evidence = dict(sha256=sha256(path), samples_per_channel=self.samples,
                             sample_rate=project.sample_rate, channels=2, format="f32le",
                             filters=audio_filters(project),
                             window="1024 stereo samples ending at floor(output_seconds * native sample_rate); zero padded")
        self.handle = path.open("rb")
        self.data = mmap.mmap(self.handle.fileno(), 0, access=mmap.ACCESS_READ)

    def at(self, frame):
        end = min(self.samples, frame * self.project.sample_rate // self.project.fps)
        start = max(0, end - 1024)
        values = array("f")
        values.frombytes(bytes((1024 - (end - start)) * 8) + self.data[start * 8:end * 8])
        if __import__("sys").byteorder != "little":
            values.byteswap()
        return values

    def close(self):
        if self.data is not None:
            self.data.close()
            self.data = None
        if self.handle:
            self.handle.close()
            self.handle = None


class ButterchurnBackend:
    def __init__(self, size, fps, preset, mix, seed, work):
        runtime = Path(os.environ.get("FRAMERUDIMENTS_BUTTERCHURN_RUNTIME", ""))
        browser = os.environ.get("FRAMERUDIMENTS_BROWSER")
        node = os.environ.get("FRAMERUDIMENTS_NODE") or shutil.which("node")
        if not node or not browser or not Path(browser).is_file() or not (runtime / "package-lock.json").is_file():
            raise ValueError("Butterchurn needs its locked Node runtime, FRAMERUDIMENTS_BROWSER and Node.js >=22.12")
        if sha256(runtime / "package-lock.json") != sha256(ASSETS / "package-lock.json"):
            raise ValueError("Butterchurn runtime package lock differs from the tested pin")
        profile_root = Path(os.environ.get("FRAMERUDIMENTS_BROWSER_PROFILE_ROOT") or work).resolve()
        profile_root.mkdir(parents=True, exist_ok=True)
        self.size, self.fps, self.process_handle = size, fps, None
        self.log = (Path(work) / f"butterchurn-{seed}-{len(list(Path(work).glob('butterchurn-*.log')))}.log").open("wb")
        try:
            self.process_handle = subprocess.Popen([node, str(ASSETS / "butterchurn-worker.mjs")],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
                text=True, encoding="utf-8", bufsize=1)
            result = self._request(dict(command="init", runtime=str(runtime.resolve()), browser=browser,
                width=size[0], height=size[1], fps=fps, preset=json.loads(Path(preset).read_text(encoding="utf-8")),
                mix=mix, seed=seed, work=str(Path(work).resolve()), profile_root=str(profile_root)))
            self.evidence = dict(result["evidence"], package_lock_sha256=sha256(ASSETS / "package-lock.json"),
                                 host_bridge_sha256=sha256(ASSETS / "butterchurn-page.js"))
        except BaseException:
            self.close()
            raise

    def _request(self, message):
        proc = self.process_handle
        try:
            proc.stdin.write(json.dumps(message, allow_nan=False) + "\n")
            proc.stdin.flush()
            line = proc.stdout.readline()
            if not line:
                raise RuntimeError("Butterchurn worker exited unexpectedly; inspect its log")
            result = json.loads(line)
            if not result.get("ok"):
                raise RuntimeError("Butterchurn: " + result.get("error", "unknown worker error"))
            return result
        except (BrokenPipeError, OSError) as exc:
            raise RuntimeError("Butterchurn worker communication failed") from exc

    def process(self, image, context, pcm):
        return self.advance(image, context, pcm, capture=True)

    def advance(self, image, context, pcm, *, capture=False):
        def encode(data):
            return base64.b64encode(data).decode("ascii")
        # Butterchurn's documented offline input uses 8-bit WebAudio-style PCM.
        left = bytes(max(0, min(255, round(128 + value * 128))) for value in pcm[0::2])
        right = bytes(max(0, min(255, round(128 + value * 128))) for value in pcm[1::2])
        mono = bytes(max(0, min(255, round(128 + (lv + rv) * 64))) for lv, rv in zip(pcm[0::2], pcm[1::2]))
        pixels = image.convert("RGBA").tobytes()
        rgba = None if pixels == getattr(self, "last_pixels", None) else encode(pixels)
        result = self._request(dict(command="frame", time=context.output_seconds, capture=capture,
            rgba=rgba, left=encode(left), right=encode(right), mono=encode(mono)))
        self.last_pixels = pixels
        if abs(result["time"] - context.output_seconds) > 1e-9 or result["fps"] != self.fps:
            raise RuntimeError("Butterchurn did not honour the exact frame clock")
        return Image.frombytes("RGBA", self.size, base64.b64decode(result["rgba"], validate=True)) if capture else None

    def close(self):
        proc = self.process_handle
        if proc:
            if proc.poll() is None:
                if hasattr(self, "evidence"):
                    result = self._request(dict(command="close"))
                    self.evidence["cleanup"] = result["cleanup"]
                    if not all(result["cleanup"].values()):
                        raise RuntimeError("Butterchurn owned browser cleanup was not verified")
                else:
                    proc.stdin.close()
                proc.wait(timeout=75)
            proc.stdin.close()
            proc.stdout.close()
            self.process_handle = None
        self.log.close()


class EffectChain:
    def __init__(self, project, specs, ffmpeg, output, *, factories=None, routing=None):
        self.backends, self.next_frame, self.audio = [], 0, None
        self.closed = False
        self.specs = specs
        self.routing = routing
        if factories is None:
            from .projectm_backend import ProjectMBackend
            factories = dict(butterchurn=ButterchurnBackend, projectm=ProjectMBackend)
        try:
            self.audio = EffectAudio(project, ffmpeg, output)
            for spec in specs:
                backend = factories[spec["engine"]]((project.width, project.height), project.fps,
                    spec["preset"]["path"], spec["input_mix"], spec["seed"], output)
                self.backends.append(backend)
        except BaseException:
            self.close()
            raise

    def process(self, image, context, *, isolated_layers=None):
        if context.frame != self.next_frame:
            raise ValueError("MilkDrop feedback requires sequential frames from zero; recreate and replay to seek")
        overlay = None
        if self.routing is not None:
            if isolated_layers is None:
                raise ValueError("Routed visualisers require the isolated compositor surfaces")
            image, overlay = self.routing.route(context, isolated_layers)
        pcm = self.audio.at(context.frame)
        for backend in self.backends:
            image = backend.process(image, context, pcm)
            if image.size != (self.audio.project.width, self.audio.project.height) or image.mode != "RGBA":
                raise ValueError("Visualiser must return frame-sized RGBA")
        self.next_frame += 1
        if overlay is not None:
            final = image.convert("RGBA")
            final.alpha_composite(overlay)
            image = final
        return image.convert("RGB")

    def evidence(self):
        return dict(stage="after scene, layers, identity, captions and lyric art; before encoding",
                    sequential_frames=self.next_frame, pcm=self.audio.evidence,
                    routing=self.routing.evidence if self.routing is not None else None,
                    stages=[dict(spec, runtime=backend.evidence) for spec, backend in zip(self.specs, self.backends)],
                    limits="GPU/driver and engine noise can vary; not a cross-platform bit-exact contract. Text bounds refer to pre-effect composition.")

    def close(self):
        if self.closed:
            return
        failures = []
        for backend in reversed(self.backends):
            try:
                backend.close()
            except Exception as exc:
                failures.append(exc)
        if self.audio:
            self.audio.close()
        if failures:
            raise RuntimeError("Effect cleanup failed: " + "; ".join(str(exc) for exc in failures))
        self.closed = True
