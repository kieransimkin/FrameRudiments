"""Auditable visualisation presets; display axes are not a replacement for DSP."""
from dataclasses import dataclass


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
    ViewSpec("spectrum-mirror", "spectrum", "Mirrored upper and lower frequency spectrum", "Winamp-era visualisers"),
    ViewSpec("spectrum-radial", "spectrum", "Spoke-style circular frequency bars", "circular music visualisers"),
    ViewSpec("spectrum-ring", "spectrum", "Radial polygon that follows spectral levels", "circular music visualisers"),
    ViewSpec("spectrum-spiral", "spectrum", "Frequency bins along a logarithmic-looking spiral path", "creative spectrum art"),
    ViewSpec("spectrum-horizon", "spectrum", "Overlaid temporal spectrum traces", "persistence displays"),
    ViewSpec("spectrum-waterfall", "spectrum", "Perspective waterfall of successive band spectra", "RF spectrum analysers"),
    ViewSpec("spectrum-ridgeline", "spectrum", "Offset stacked spectrum ridges", "joy plots / sonograms"),
    ViewSpec("spectrum-peaks", "spectrum", "Local-maximum spike display", "peak-pick spectrograms"),
    ViewSpec("spectrum-log", "spectrum", "Current spectrum with a logarithmic frequency axis", "log-spectrum analysers"),
    ViewSpec("spectrum-mel", "spectrum", "Current recorded bands positioned on a mel frequency axis", "psychoacoustic frequency scales"),
    ViewSpec("spectrum-third-octave", "spectrum", "Current bands shown on a 1/3-octave frequency axis", "acoustic measurement analysers"),
    ViewSpec("spectrogram-linear", "spectrogram", "Time-frequency heatmap, linear Hz positioning", "sonograph / STFT display"),
    ViewSpec("spectrogram-log", "spectrogram", "Time-frequency heatmap, logarithmic Hz positioning", "musical spectrogram"),
    ViewSpec("spectrogram-mel", "spectrogram", "Recorded Fourier bands remapped to a mel axis", "mel frequency-axis display"),
    ViewSpec("spectrogram-bark", "spectrogram", "Recorded Fourier bands remapped to a Bark axis", "critical-band frequency scale"),
    ViewSpec("spectrogram-erb", "spectrogram", "Recorded Fourier bands remapped to ERB-rate axis", "auditory filterbank displays"),
    ViewSpec("spectrogram-peaks", "spectrogram", "Time-frequency heatmap retaining only local band maxima", "spectral peak maps"),
    ViewSpec("spectrogram-contours", "spectrogram", "Quantised amplitude contour strips across time", "contour sonograms"),
    ViewSpec("spectrogram-mirror", "spectrogram", "Mirrored time-frequency heatmap", "creative audio visualisers"),
    ViewSpec("spectrogram-transposed", "spectrogram", "Vertical-time spectrogram / scrolling frequency history", "classic sideways waterfall"),
    ViewSpec("spectrogram-polar", "spectrogram", "Time wrapped around angle, frequency along radius", "polar sonograms"),
)
MOTION_VIEWS = (
    ViewSpec("waveform-area", "motion", "Filled multi-lane rudiment value histories", "DAW waveform style"),
    ViewSpec("waveform-mirror", "motion", "Symmetric amplitude envelope of rudiment values", "mirrored waveforms"),
    ViewSpec("waveform-dots", "motion", "Sampled rudiment values drawn as points", "discrete sampled signals"),
    ViewSpec("waveform-steps", "motion", "Zero-order-hold rudiment signal traces", "digital oscilloscopes"),
    ViewSpec("waveform-oscilloscope", "motion", "Centred scrolling single-trace rudiment oscilloscope", "CRT oscilloscopes"),
    ViewSpec("waveform-envelope", "motion", "Windowed min/max envelope of observed rudiment values", "DAW waveform summaries"),
    ViewSpec("waveform-ribbon", "motion", "Coloured variation ribbon of paired rudiment channels", "creative data visualisation"),
    ViewSpec("waveform-polar", "motion", "Polar history of the first rudiment channel", "polar oscilloscopes"),
    ViewSpec("phase-portrait", "motion", "Two-dimensional history using first two motion axes", "XY oscilloscopes / Lissajous"),
    ViewSpec("phase-points", "motion", "Scatter plot of paired rudiment axes", "phase-space plots"),
)
ALL_VIEWS = SPECTRAL_VIEWS + MOTION_VIEWS
BY_NAME = {v.name: v for v in ALL_VIEWS}
assert len(BY_NAME) == len(ALL_VIEWS)
