"""Auditable visualisation presets; display axes are not a replacement for DSP."""

from dataclasses import dataclass
from .effect_catalogue import EFFECT_SPECS
from .sprite_catalogue import SPRITE_SPECS


@dataclass(frozen=True)
class ViewSpec:
    name: str
    family: str
    description: str
    origin: str


# Preset names are stable project JSON renderer identifiers.  A display of
# a mel/log/bark/ERB axis does NOT compute a mel/CQT/wavelet transform.
SPECTRAL_VIEWS = (
    ViewSpec("spectrum-line", "spectrum", "Classic connected FFT-band magnitude trace", "spectrum analyser"),
    ViewSpec("spectrum-area", "spectrum", "Filled magnitude spectrum", "spectrum analyser"),
    ViewSpec("spectrum-dots", "spectrum", "Discrete frequency-bin points", "measurement plot"),
    ViewSpec("spectrum-stems", "spectrum", "Vertical impulse / lollipop spectrum", "spectrum analyser"),
    ViewSpec("spectrum-steps", "spectrum", "Sample-and-hold staircase bands", "digital spectrum analyser"),
    ViewSpec(
        "spectrum-mirror", "spectrum", "Mirrored upper and lower frequency spectrum", "Winamp-era visualisers"
    ),
    ViewSpec(
        "spectrum-radial", "spectrum", "Spoke-style circular frequency bars", "circular music visualisers"
    ),
    ViewSpec(
        "spectrum-ring",
        "spectrum",
        "Radial polygon that follows spectral levels",
        "circular music visualisers",
    ),
    ViewSpec(
        "spectrum-spiral",
        "spectrum",
        "Frequency bins along a logarithmic-looking spiral path",
        "creative spectrum art",
    ),
    ViewSpec("spectrum-horizon", "spectrum", "Overlaid temporal spectrum traces", "persistence displays"),
    ViewSpec(
        "spectrum-waterfall",
        "spectrum",
        "Perspective waterfall of successive band spectra",
        "RF spectrum analysers",
    ),
    ViewSpec("spectrum-ridgeline", "spectrum", "Offset stacked spectrum ridges", "joy plots / sonograms"),
    ViewSpec("spectrum-peaks", "spectrum", "Local-maximum spike display", "peak-pick spectrograms"),
    ViewSpec(
        "spectrum-log",
        "spectrum",
        "Current spectrum with a logarithmic frequency axis",
        "log-spectrum analysers",
    ),
    ViewSpec(
        "spectrum-mel",
        "spectrum",
        "Current recorded bands positioned on a mel frequency axis",
        "psychoacoustic frequency scales",
    ),
    ViewSpec(
        "spectrum-third-octave",
        "spectrum",
        "Current bands shown on a 1/3-octave frequency axis",
        "acoustic measurement analysers",
    ),
    ViewSpec(
        "spectrogram-linear",
        "spectrogram",
        "Time-frequency heatmap, linear Hz positioning",
        "sonograph / STFT display",
    ),
    ViewSpec(
        "spectrogram-log",
        "spectrogram",
        "Time-frequency heatmap, logarithmic Hz positioning",
        "musical spectrogram",
    ),
    ViewSpec(
        "spectrogram-mel",
        "spectrogram",
        "Recorded Fourier bands remapped to a mel axis",
        "mel frequency-axis display",
    ),
    ViewSpec(
        "spectrogram-bark",
        "spectrogram",
        "Recorded Fourier bands remapped to a Bark axis",
        "critical-band frequency scale",
    ),
    ViewSpec(
        "spectrogram-erb",
        "spectrogram",
        "Recorded Fourier bands remapped to ERB-rate axis",
        "auditory filterbank displays",
    ),
    ViewSpec(
        "spectrogram-peaks",
        "spectrogram",
        "Time-frequency heatmap retaining only local band maxima",
        "spectral peak maps",
    ),
    ViewSpec(
        "spectrogram-contours",
        "spectrogram",
        "Quantised amplitude contour strips across time",
        "contour sonograms",
    ),
    ViewSpec(
        "spectrogram-mirror", "spectrogram", "Mirrored time-frequency heatmap", "creative audio visualisers"
    ),
    ViewSpec(
        "spectrogram-transposed",
        "spectrogram",
        "Vertical-time spectrogram / scrolling frequency history",
        "classic sideways waterfall",
    ),
    ViewSpec(
        "spectrogram-polar",
        "spectrogram",
        "Time wrapped around angle, frequency along radius",
        "polar sonograms",
    ),
)
MOTION_VIEWS = (
    ViewSpec("waveform-area", "motion", "Filled multi-lane rudiment value histories", "DAW waveform style"),
    ViewSpec(
        "waveform-mirror", "motion", "Symmetric amplitude envelope of rudiment values", "mirrored waveforms"
    ),
    ViewSpec(
        "waveform-dots", "motion", "Sampled rudiment values drawn as points", "discrete sampled signals"
    ),
    ViewSpec("waveform-steps", "motion", "Zero-order-hold rudiment signal traces", "digital oscilloscopes"),
    ViewSpec(
        "waveform-oscilloscope",
        "motion",
        "Centred scrolling single-trace rudiment oscilloscope",
        "CRT oscilloscopes",
    ),
    ViewSpec(
        "waveform-envelope",
        "motion",
        "Windowed min/max envelope of observed rudiment values",
        "DAW waveform summaries",
    ),
    ViewSpec(
        "waveform-ribbon",
        "motion",
        "Coloured variation ribbon of paired rudiment channels",
        "creative data visualisation",
    ),
    ViewSpec(
        "waveform-polar", "motion", "Polar history of the first rudiment channel", "polar oscilloscopes"
    ),
    ViewSpec(
        "phase-portrait",
        "motion",
        "Two-dimensional history using first two motion axes",
        "XY oscilloscopes / Lissajous",
    ),
    ViewSpec("phase-points", "motion", "Scatter plot of paired rudiment axes", "phase-space plots"),
)
# Circular and annular modes: distinct polar geometries, not palette aliases.
_CIRCULAR_SPECTRUM = {
    "bars": "Continuous radial frequency bars rooted at the inner ring",
    "bars-inward": "Radial bars growing inward from the outer perimeter",
    "bars-bipolar": "Centred, bidirectional radial magnitude bars",
    "bars-mirror": "Bidirectional ring bars with endpoint markers",
    "split": "Alternating inward/outward frequency bars",
    "sunburst": "Alternating-length radial sunburst analyser",
    "fan": "Filled wedge-frequency fan analyser",
    "petals": "Thin sector petals sized by frequency-band strength",
    "led": "Segmented radial LED meter from inner to outer ring",
    "led-inward": "LED meter counting backwards from the outside",
    "ticks": "Isolated radial magnitude-position ticks",
    "arc": "Concentric miniature gauge arcs for each band",
    "dial": "Graduated frequency dial with dot-end needles",
    "dots": "Displaced radial frequency-bin dots",
    "lollipop": "Radial bar stems with circular end markers",
    "orbit": "Angularly perturbed frequency-bin orbit dots",
    "caterpillar": "Thick circular bead chain driven by spectral bins",
    "peaks": "Selected local maximum spectral spokes",
    "area": "Closed annular frequency response with filled band",
    "line": "Closed radial spectrum outline",
    "ribbon": "Nested concentric polygon ribbons",
    "halo": "Layered low-alpha spectral halo lines",
    "rose": "Alternating polar rose-style magnitude petals",
    "concentric": "Multilayered frequency rings",
    "sectors": "Coloured pie sector bars per frequency bin",
    "layers": "Interleaved bands partitioned over concentric rings",
    "gauge": "Single annular swept arc for mean visual band intensity",
    "spiral": "Frequency bins plotted on an amplitude-perturbed spiral",
    "helix": "Frequency bins along a multiple-turn polar trace",
}
_CIRCULAR_SPECTROGRAM = {
    "time": "Annular spectrogram with time as radius and frequency as angle",
    "rings": "Discrete concentric time-history rings coloured by band intensity",
    "heatmap": "Polar annulus time-frequency heatmap",
    "stripes": "Alternating tinted strips across the polar heatmap",
    "contour": "Quantised spectral power annulus, band-level contour regions",
    "frequency": "Time mapped to angle, frequency to radius",
    "sweep": "Radar-style time sweep with old measurements faded",
    "spiral": "Dominant-band spectral history along a time spiral",
}
_CIRCULAR_MOTION = {
    "line": "Signed native Rudiment history as a complete annular outline",
    "fill": "Signed annular waveform with fill to the centre ring",
    "area": "Filled area between motion trace and inner ring",
    "envelope": "Unsigned Rudiment channel amplitude around a circular baseline",
    "halo": "Translucent circular waveform aura",
    "rose": "Alternating-petal annular waveform",
    "pulse": "Per-point thickness pulse around a middle ring",
    "mirror": "Inside/outside mirrored radial waveform envelopes",
    "ribbon": "Annular ribbon between two native Rudiment axes",
    "rings": "Concentric independent motion channels",
    "bands": "Two-axis banded annular trace",
    "bars": "Bipolar motion-history spokes rooted at a centred circle",
    "spokes": "Circular discrete-time stem oscilloscope",
    "led": "Radial LED trail driven by absolute Rudiment channel value",
    "petals": "Sampled waveform sectors as radial petals",
    "sectors": "Sector length encodes absolute Rudiment motion magnitude",
    "arc": "Time-addressed arcs with per-sample radial position",
    "dots": "Circular sampler dots mapped to signed amplitude",
    "radar": "Radar-like radial motion traces with dot endpoints",
    "trails": "Chronologically fading dots along the annular waveform",
    "spiral": "Rudiment signal warped around a multi-turn time spiral",
    "helix": "Waveform on a spiralling annular helix",
}

CIRCULAR_VIEWS = (
    tuple(
        ViewSpec("spectrum-annular-" + name, "spectrum", description, "radial spectrum analyser")
        for name, description in _CIRCULAR_SPECTRUM.items()
    )
    + tuple(
        ViewSpec("spectrogram-annular-" + name, "spectrogram", description, "polar sonogram")
        for name, description in _CIRCULAR_SPECTROGRAM.items()
    )
    + tuple(
        ViewSpec("waveform-annular-" + name, "motion", description, "circular oscilloscope")
        for name, description in _CIRCULAR_MOTION.items()
    )
    + (
        ViewSpec(
            "phase-annular-portrait",
            "motion",
            "Two native Rudiment dimensions mapped into annular angle and radius",
            "polar XY oscilloscope",
        ),
    )
)
EFFECT_VIEWS = tuple(ViewSpec(*spec) for spec in EFFECT_SPECS)
SPRITE_VIEWS = tuple(ViewSpec(*spec) for spec in SPRITE_SPECS)
SPATIAL_VIEWS = tuple(
    ViewSpec(
        "spatial-" + shape,
        "motion",
        "Deterministic 3D projected wire geometry animated by native Rudiment channels",
        "CPU perspective mesh rendering",
    )
    for shape in ("torus", "sphere", "terrain")
)
SOLID_SPATIAL_VIEWS = tuple(
    ViewSpec(
        "spatial-solid-" + shape,
        "motion",
        "Depth-tested 3D solid mesh with directional lighting and bounded volume fog",
        "CPU depth-buffered 3D geometry",
    )
    for shape in ("torus", "sphere", "terrain")
)
MUSIC_SPATIAL_VIEWS = tuple(
    ViewSpec(
        "spatial-music-" + shape,
        "motion",
        "Native-motion-deformed 3D mesh with optional calibrated FFT terrain and atmospheric materials",
        "CPU/OpenGL music-reactive geometry",
    )
    for shape in ("torus", "sphere", "terrain")
)
ALL_VIEWS = (SPECTRAL_VIEWS + MOTION_VIEWS + CIRCULAR_VIEWS + EFFECT_VIEWS
             + SPRITE_VIEWS + SPATIAL_VIEWS + SOLID_SPATIAL_VIEWS + MUSIC_SPATIAL_VIEWS)
BY_NAME = {v.name: v for v in ALL_VIEWS}
assert len(BY_NAME) == len(ALL_VIEWS)
