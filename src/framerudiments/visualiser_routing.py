"""Source-clocked selection of isolated composited layers for MilkDrop pipelines.

Every routing decision is deterministic for a sequential run starting at frame 0;
state lives with the EffectChain, never with GPU texture or a Python scene hook.
Replay from zero when seeking a feedback-based visualiser.
"""
from collections import OrderedDict
import math

from PIL import Image

from .io import fields, number

MAX_TARGETS = 256
MODES = frozenset({"continuous", "first_pips", "never"})
RETRIGGERS = frozenset({"reappearance", "once", "lyric", "cue"})


def _policy(config, label, inherited=None):
    """Validate one element route. Fields omitted inherit from the default."""
    fields(config, {"feed", "overlay", "retrigger"}, label)
    parent = inherited or dict(feed=dict(mode="continuous"), overlay=False,
                               retrigger="reappearance")
    feed = config.get("feed", parent["feed"])
    if isinstance(feed, str):
        feed = {"mode": feed}
    fields(feed, {"mode", "duration_pips"}, label + ".feed")
    mode = feed.get("mode")
    if mode not in MODES:
        raise ValueError(f"{label}.feed.mode must be continuous, first_pips or never")
    if mode == "first_pips":
        duration = number(feed.get("duration_pips"), label + ".feed.duration_pips",
                          minimum=0, maximum=65536)
        feed = dict(mode=mode, duration_pips=duration)
    elif "duration_pips" in feed:
        raise ValueError(f"{label}.feed.duration_pips requires first_pips")
    else:
        feed = dict(mode=mode)
    overlay = config.get("overlay", parent["overlay"])
    if type(overlay) is not bool:
        raise ValueError(f"{label}.overlay must be boolean")
    retrigger = config.get("retrigger", parent["retrigger"])
    if retrigger not in RETRIGGERS:
        raise ValueError(f"{label}.retrigger must be reappearance, once, lyric or cue")
    return dict(feed=feed, overlay=overlay, retrigger=retrigger)


def _event_key(event):
    if not isinstance(event, dict):
        return None
    return (event.get("cycle"), event.get("source_order"), event.get("frame"),
            event.get("text"))


class VisualiserLayerRouting:
    """Per-element feed and clean-overlay policies for one sequential EffectChain.

    Input parts are ordered complete-frame RGBA *ink* surfaces. Transparent
    pixels genuinely mean no GPU input; the engine's input_mix scales the
    source alpha instead of painting opaque black over stored feedback.
    """

    def __init__(self, config, available, size):
        fields(config, {"default", "elements"}, "visualiser_routing")
        self.size = tuple(size)
        self.available = tuple(available)
        if len(self.available) > MAX_TARGETS or len(set(self.available)) != len(self.available):
            raise ValueError("Visualiser routing needs unique named elements (<=256)")
        self.default = _policy(config.get("default", {}), "visualiser_routing.default")
        rows = config.get("elements", {})
        if not isinstance(rows, dict) or len(rows) > MAX_TARGETS:
            raise ValueError("visualiser_routing.elements needs <=256 named policies")
        if set(rows) - set(self.available):
            raise ValueError(f"Unknown visualiser routing elements: {sorted(set(rows)-set(self.available))}")
        self.policies = {key: _policy(row, f"visualiser_routing.elements.{key}", self.default)
                         for key, row in rows.items()}
        self.states = {}  # populated only by sequential feedback frames
        self.last_report = {}
        self.next_frame = 0
        self.evidence = dict(default=self.default, elements=self.policies,
                             available=list(self.available),
                             timing="64 source-clock pips per beat; independent from output FPS",
                             start="first nontransparent pixel (rearmed on reappearance by default)",
                             gpu_input="per-pixel alpha; zero alpha does not overwrite feedback",
                             output="clean overlays after all Butterchurn/projectM stages")

    def route(self, context, parts):
        if context.frame != self.next_frame:
            raise ValueError("Visualiser routing requires sequential frames from zero; replay for seeking")
        pip = float(context.beat) * 64.0
        if not math.isfinite(pip):
            raise ValueError("Non-finite beat phase in visualiser routing")
        images = OrderedDict()
        for name, image in parts:
            if name in images or name not in self.available:
                raise ValueError(f"Duplicate or unknown isolated visualiser element {name!r}")
            if not isinstance(image, Image.Image) or image.mode != "RGBA" or image.size != self.size:
                raise ValueError("Visualiser layer surfaces must be full-frame RGBA")
            images[name] = image
        injected = Image.new("RGBA", self.size)
        overlay = Image.new("RGBA", self.size)
        report = {}
        for name in self.available:
            ink = images.get(name)
            visible = ink is not None and ink.getchannel("A").getbbox() is not None
            policy = self.policies.get(name, self.default)
            state = self.states.setdefault(name, dict(visible=False, onset=None, ever=False,
                                                       lyric=None, cue=None))
            lyric = _event_key(getattr(context, "lyric", None))
            cue = _event_key(getattr(context, "cue", None))
            new = bool(visible and not state["visible"])
            trigger = policy["retrigger"]
            if visible and ((trigger == "lyric" and state["lyric"] != lyric) or
                            (trigger == "cue" and state["cue"] != cue)):
                new = True
            if new and (trigger != "once" or not state["ever"]):
                state["onset"] = pip
                state["ever"] = True
            if not visible:
                state["onset"] = None if trigger != "once" else state["onset"]
            state["visible"] = bool(visible)
            state["lyric"], state["cue"] = lyric, cue
            mode = policy["feed"]["mode"]
            elapsed = max(0.0, pip - state["onset"]) if state["onset"] is not None else None
            feeding = bool(visible and (mode == "continuous" or
                           mode == "first_pips" and elapsed is not None and
                           elapsed < policy["feed"]["duration_pips"]))
            on_top = bool(visible and policy["overlay"])
            if feeding:
                injected.alpha_composite(ink)
            if on_top:
                overlay.alpha_composite(ink)
            if visible or name in self.policies:
                report[name] = dict(visible=bool(visible), feeding=feeding, overlay=on_top,
                                    first_pip=state["onset"], elapsed_pips=elapsed,
                                    policy=policy["feed"]["mode"])
        self.next_frame += 1
        self.last_report = dict(frame=context.frame, source_seconds=context.source_seconds,
                                beat=context.beat, elements=report,
                                feeding=[key for key, row in report.items() if row["feeding"]],
                                overlays=[key for key, row in report.items() if row["overlay"]])
        return injected, overlay
