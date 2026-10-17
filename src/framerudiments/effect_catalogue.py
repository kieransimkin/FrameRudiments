"""Named *display* variations over existing calibrated bands or motion traces.

They deliberately do not claim new audio analysis: any statistical views use
observed sampled bands only, not continuous PCM features or refined FFT bins.
"""

SPECTRUM_EFFECTS = {
    "cityscape": "A skyline of unequal roofline bars from calibrated Fourier bands",
    "led-matrix": "Stacked discretised LED columns across frequency",
    "capsules": "Rounded spectrum amplitude capsules",
    "outline-blocks": "Outlined rather than filled frequency-bin rectangles",
    "peak-ladder": "Horizontal rungs marking each calibrated band level",
    "staircase": "Frequency-band zero-order-hold height step plot",
    "eq-curve": "Interpolated drawing through recorded band centres (no new DSP)",
    "ribbon-stack": "Layered observed spectral traces with fading ink",
    "peak-trails": "Trailing observed spectral bin markers over history",
    "gel-bars": "Glassy translucent frequency columns with highlights",
    "lollipop-grid": "Stem-and-bead plot on a frequency grid",
    "mountain-range": "Ridged layers of previous frequency-band frames",
    "band-delta": "Signed difference between current and previous observed band levels",
    "band-variance": "Variance of observed sampled band levels, not spectral variance analysis",
    "energy-tiles": "Bands drawn as colour-intensity tiles",
    "semi-circle": "Upper semicircular spectrum bars rather than a complete annulus",
    "fan-meter": "Frequency wedges along a semi-annular fan",
    "braided-lines": "Interlaced lines from sampled band histories",
    "rain-bars": "Dropping horizontal frequency strokes over sampled history",
    "bead-stacks": "Rounded dot columns with discrete energy steps",
    "dash-grid": "Multi-row dashed spectral equaliser",
    "thermometer": "Full-height level-sensitive opacity bars",
    "wave-fence": "Perspective fence of successive observed spectra",
    "partition": "Equal-width nested slices with area representing band level",
}

SPECTROGRAM_EFFECTS = {
    "pixel-mosaic": "Hard pixel cells showing observed time-frequency band values",
    "isometric-terrain": "Offset isometric horizon lines over observed spectral history",
    "voxel-city": "Height-encoded isometric blocks for time-frequency samples",
    "frequency-stripes": "Alternating vertical frequency columns in a scrolling heatmap",
    "dot-cloud": "Circular marks whose radius and alpha follow observed band power",
    "isobands": "Discrete filled colour thresholds from observed bands",
    "negative": "Inverted visual colour scale of time-frequency energy",
    "tape": "Horizontal film-like band-history lanes",
    "slit-scan": "Vertical slits displaying successive spectrum samples",
    "trails": "Diagonal trails of observed frequency-bin magnitudes",
    "hex-cells": "Hexagonal cells coloured by time-frequency energy",
    "bead-grid": "Elliptical beads arranged on time-frequency rows",
    "tunnel": "Concentric rectangular history frames with band colours",
    "record-grooves": "Concentric spectral history grooves in the style of a record",
    "quilt": "Alternating hard-edged time-frequency patches",
    "crosshatch": "Crosshatched screen whose density follows band magnitude",
    "triangle-tiles": "Tessellated triangle glyphs coloured by recorded spectral values",
    "pixel-rain": "Vertical pixel drop matrix showing previous calibrated band levels",
}

MOTION_EFFECTS = {
    "phosphor": "Age-decayed CRT-like sampled Rudiment trace",
    "echo": "Offset translucent history echoes of Rudiment channels",
    "sample-beads": "Observed Rudiment values drawn as discrete circles",
    "channel-braid": "Two Rudiment dimensions woven across the time axis",
    "pulse-train": "Signed Rudiment channel impulses from a zero baseline",
    "quantised": "Quantised display grid of Rudiment levels",
    "triggered": "Leading-edge display aligned to an observed Rudiment zero crossing",
    "spiral-trace": "Rudiment history rendered as a planar spiral with amplitude modulation",
    "butterfly": "Mirrored paired Rudiment traces across the centre line",
    "horizon": "Overlaid translucent Rudiment history ridges",
    "heat-ribbon": "Filled channel history with time-varying ink strength",
    "dotted-envelope": "Envelope-style upper/lower sampled Rudiment point clouds",
    "vertical-scope": "Rudiment time moving vertically with horizontal signal amplitude",
    "phase-trails": "Age-faded XY Rudiment motion portrait",
    "polar-radar": "Polar motion history with fading position spokes",
    "dna-helix": "Two out-of-phase Rudiment traces linked by time-axis rungs",
    "block-meter": "Discrete per-sample signed Rudiment blocks",
    "signal-staircase": "Stacked stepped traces in independently offset lanes",
}

EFFECT_SPECS = (
    [(f"spectrum-{name}", "spectrum", description, "spectral display typography")
     for name, description in SPECTRUM_EFFECTS.items()]
    + [(f"spectrogram-{name}", "spectrogram", description, "time-frequency data art")
       for name, description in SPECTROGRAM_EFFECTS.items()]
    + [(f"waveform-{name}", "motion", description, "motion oscilloscope")
       for name, description in MOTION_EFFECTS.items()]
)
assert len(EFFECT_SPECS) == 60
