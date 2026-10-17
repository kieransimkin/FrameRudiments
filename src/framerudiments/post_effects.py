"""Bounded, deterministic viewport effects applied after Rudiment rendering.

All effects are *opt-in*, use source-independent fixed coordinates/seed and
retain source alpha. No random state, network, shaders or image imports.
They change appearance, never calibrated spectrum measurements.
"""
import math

from PIL import Image, ImageFilter

# Numeric settings live in `visual`, and are therefore addressable using
# `modulations: [{"target": "visual.scanlines", ...}]` like existing grades.
POST_RANGES = {
    "pixel_size": (1, 48, 1),
    "scanlines": (0, 1, 0),
    "scanline_spacing": (2, 24, 4),
    "grain": (0, 1, 0),
    "grain_seed": (0, 65535, 0),
    "chromatic_shift": (0, 12, 0),
    "vignette": (0, 1, 0),
    "edge_mix": (0, 1, 0),
    "dither": (0, 1, 0),
    "halftone": (0, 1, 0),
    "solarize": (0, 1, 0),
    "glitch": (0, 1, 0),
    "sharpen": (0, 4, 0),
}


def _clamp(v):
    return max(0, min(255, round(v)))


def _hash(x, y, seed):
    # Counter-based hash: independent of traversal order, frame seek, threads.
    v = (x * 374761393 + y * 668265263 + seed * 1442695041) & 0xffffffff
    v = ((v ^ (v >> 13)) * 1274126177) & 0xffffffff
    return ((v ^ (v >> 16)) & 65535) / 65535


_BAYER4 = (0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5)


def apply_post_effects(image, settings):
    """Return RGBA output of exactly the input size; preserve transparent pixels.

    settings is a validated VisualStyle instance, not arbitrary user code.
    This pass is skipped byte-for-byte when every effect is at its default.
    """
    keys = ("pixel_size", "scanlines", "grain", "chromatic_shift", "vignette", "edge_mix",
            "dither", "halftone", "solarize", "glitch", "sharpen")
    if all(getattr(settings, key) == (1 if key == "pixel_size" else 0) for key in keys):
        return image
    if image.mode != "RGBA":
        raise ValueError("Post effects require RGBA viewport")
    w,h = image.size
    if settings.pixel_size > 1:
        k=max(1,round(settings.pixel_size))
        image = image.resize((max(1,math.ceil(w/k)),max(1,math.ceil(h/k))),Image.Resampling.NEAREST)
        image = image.resize((w,h),Image.Resampling.NEAREST)
    if settings.sharpen:
        image = image.filter(ImageFilter.UnsharpMask(radius=1.5,
                            percent=round(90*settings.sharpen),threshold=2))
    source = image.copy()
    original = source.load()
    pixels = image.load()
    edges = source.getchannel('A').filter(ImageFilter.FIND_EDGES).load() if settings.edge_mix else None
    shift=round(settings.chromatic_shift)
    period=max(2,round(settings.scanline_spacing))
    seed=round(settings.grain_seed)
    for y in range(h):
        scanline=max(0,1-settings.scanlines*(1 if y % period == 0 else .13))
        row_shift=round(settings.glitch*8*(_hash(0,y//4,seed)-.5)*2) if settings.glitch else 0
        for x in range(w):
            r,g,b,a=original[x,y]
            if a==0:
                # Preserves transparency regardless of any spatial effect.
                pixels[x,y]=(0,0,0,0)
                continue
            if shift:
                r=original[min(w-1,x+shift),y][0]
                b=original[max(0,x-shift),y][2]
            if row_shift:
                xr=max(0,min(w-1,x+row_shift))
                g=original[xr,y][1]
            tone=scanline
            if settings.vignette:
                dx=(x-(w-1)/2)/max(1,w/2)
                dy=(y-(h-1)/2)/max(1,h/2)
                tone*=1-settings.vignette*min(1,(dx*dx+dy*dy)*.75)
            if settings.halftone:
                # Dot-screen threshold in a repeatable four-pixel cell.
                dot=(x%4-1.5)**2+(y%4-1.5)**2
                screen=1-settings.halftone*.5*max(0,dot-1)/3.5
                tone*=screen
            noise=(_hash(x,y,seed)-.5)*settings.grain*100 if settings.grain else 0
            r,g,b=(_clamp(c*tone+noise) for c in (r,g,b))
            if settings.solarize:
                mix=settings.solarize
                r,g,b=(_clamp(c*(1-mix)+((255-c) if c>127 else c)*mix) for c in (r,g,b))
            if edges is not None:
                e=edges[x,y] * settings.edge_mix
                r,g,b=(_clamp(c+e) for c in (r,g,b))
            if settings.dither:
                dv=((_BAYER4[(y%4)*4+(x%4)]/16)-.5)*settings.dither*58
                r,g,b=(_clamp(c+dv) for c in (r,g,b))
            pixels[x,y]=(r,g,b,a)
    return image
