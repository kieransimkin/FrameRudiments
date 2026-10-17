"""Explicit opt-in sprite counterparts; all historic vector names are retained."""


def _views(prefix, family, origin, rows):
    return tuple((f"sprite-{prefix}-{slug}", family, description, origin) for slug, description in rows)


SPRITE_SPECS = (
    _views(
        "spectrum",
        "spectrum",
        "sprite-based band analyser",
        (
            ("lcd", "Segmented LCD level columns with lit and unlit image cells"),
            ("lcd-horizontal", "Horizontal LCD meters with independent band rows"),
            ("lcd-mirrored", "Mirrored LCD columns growing about the centre line"),
            ("led", "LED dot matrix with individually illuminated PNG discs"),
            ("led-radial", "Circular LED dot analyzers built from repeated PNG stamps"),
            ("vfd", "Vacuum fluorescent tube bars assembled from sprite cells"),
            ("retro", "8-bit arcade frequency blocks"),
            ("neon", "Glossy neon tube columns with sprite reflections"),
            ("crystal", "Stacked refractive crystal sprite frequency columns"),
            ("fire", "Flame sprite stacks driven by calibrated band magnitudes"),
            ("ice", "Translucent frosted sprite frequency peaks"),
            ("icons", "Icon-stack frequency columns"),
            ("petals", "Sprite petals arranged around a circular frequency plot"),
            ("ring", "Concentric sprite level rings"),
            ("halo", "Soft-glow image particles around polar band positions"),
            ("mosaic", "Frequency-response mosaics made from image tiles"),
            ("city", "Skyline of tile-built frequency towers"),
            ("gauge", "Single radial sprite meter of current aggregate band level"),
            ("spokes", "Radial discrete frequency spokes stamped from source tiles"),
            ("orbits", "Orbiting glyphs at data-derived radius for each Fourier band"),
        ),
    )
    + _views(
        "spectrogram",
        "spectrogram",
        "sprite time-frequency plot",
        (
            ("lcd", "LCD segment-cell time-frequency history"),
            ("led", "Discrete LED dot sonogram"),
            ("pixels", "Indexed pixel tiles across observed spectrum history"),
            ("heatmap", "Intensity-coded bitmap cell sonogram"),
            ("icons", "Discrete icon heatmap over observed time-frequency bins"),
            ("neon", "Neon time-frequency mosaic"),
            ("crystal", "Crystal glyphs arranged by band and observed time"),
            ("fire", "Flame sprites along energetic time-frequency regions"),
            ("radial", "Radial annulus with frequency and time sprite positions"),
            ("spiral", "Time-wrapped sprite spiral with frequency-driven radius"),
            ("rain", "Rain-like descending trails of detected Fourier energy"),
            ("blocks", "Block-cell bitmap sonogram with visible grid"),
        ),
    )
    + _views(
        "waveform",
        "motion",
        "sampled Rudiment waveform sprites",
        (
            ("lcd", "Discrete LCD cells tracing native Rudiment history"),
            ("led", "LED particles following the native waveform"),
            ("dots", "Individual sprite points for every observed sample"),
            ("icons", "Icon stamps in chronological waveform order"),
            ("neon", "Luminous neon nodes along the native motion trace"),
            ("ribbon", "Thick image-tile waveform ribbon"),
            ("bars", "Sprite columns from the zero line to Rudiment samples"),
            ("mirrored", "Inside/outside mirrored sprite waveform"),
            ("annular", "Circular waveform built from rotating sprites"),
            ("spiral", "Chronological spiral sprite trail"),
            ("sparks", "Phosphor photon sprites at sampled motion positions"),
            ("crystal", "Crystal and gem glyphs following the waveform"),
        ),
    )
    + _views(
        "motion",
        "motion",
        "native DanceRudiment sprite field",
        (
            ("path", "XY native Rudiment path drawn with sprite stamps"),
            ("orbit", "Orbiting sprite glyphs driven by two Rudiment axes"),
            ("cloud", "Deterministic sprite cloud around native movement"),
            ("constellation", "Sparse star / node sprite constellation"),
            ("trails", "Chronological decaying motion sprite trails"),
            ("particles", "Multi-particle native motion sprite field"),
            ("xy", "Sprite scatter for the first two native dimensions"),
            ("icon-grid", "Grid of sprites responding to independent Rudiment values"),
        ),
    )
)

assert len(SPRITE_SPECS) == 52
assert len({row[0] for row in SPRITE_SPECS}) == len(SPRITE_SPECS)
