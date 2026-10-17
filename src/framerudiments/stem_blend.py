"""Mix separately pinned native DanceRudiments recording streams, not PCM.

The container delegates source association, sample clocks and file provenance to
Motion. It is a presentation-domain weighted channel combination, not stem
separation, loudness estimation, FFT or spectral aggregation.
"""

import math

from .motion import Motion


class StemBlend:
    def __init__(self, sources, project):
        if not isinstance(sources, list) or not 2 <= len(sources) <= 8:
            raise ValueError("stem_blend requires 2 to 8 named stem inputs")
        self.entries = []
        self.files = []
        seen = set()
        for item in sources:
            if not isinstance(item, dict) or set(item) - {"stem_source", "weight"}:
                raise ValueError("stem_blend entries need stem_source and optional weight")
            name = item.get("stem_source")
            if not isinstance(name, str) or name not in project.stem_sources or name in seen:
                raise ValueError("stem_blend requires distinct declared stem_source names")
            seen.add(name)
            weight = item.get("weight", 1.0)
            if type(weight) not in (int, float) or not math.isfinite(weight) or weight <= 0 or weight > 100:
                raise ValueError("stem_blend weights must be finite and positive (<=100)")
            motion = Motion(project.stem_sources[name], project.base, project.clock)
            if self.entries and motion.value_dimensions != self.value_dimensions:
                raise ValueError("stem_blend recordings must have identical named dimensions")
            self.entries.append((name, float(weight), motion))
            self.files.extend(motion.files)
            if not hasattr(self, "value_dimensions"):
                self.value_dimensions = motion.value_dimensions
        self.evidence = {
            "kind": "stem-blend",
            "domain": "native normalized motion values",
            "inputs": [{"stem_source": n, "weight": w, "motion": m.evidence} for n, w, m in self.entries],
        }

    def values(self, seconds, *, continuous_beat=None):
        total = sum(weight for _, weight, _ in self.entries)
        arrays = [motion.values(seconds, continuous_beat=continuous_beat) for _, _, motion in self.entries]
        if any(len(values) != len(self.value_dimensions) for values in arrays):
            raise ValueError("stem_blend source channel count changed")
        return tuple(
            max(
                -1.0,
                min(
                    1.0,
                    sum(values[i] * weight for values, (_, weight, _) in zip(arrays, self.entries)) / total,
                ),
            )
            for i in range(len(self.value_dimensions))
        )
