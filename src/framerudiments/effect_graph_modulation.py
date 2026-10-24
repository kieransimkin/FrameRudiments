"""Native DanceRudiment-driven effect-graph controls.

Reuses the existing motion clock, smoothing, curves and source provenance.
No new oscillator or mutable per-frame state is introduced.
"""
from .modulation import _Modulator, _combine


class EffectGraphControls:
    def __init__(self, project, graph, configs, context_at):
        if not isinstance(configs, list) or len(configs) > 64:
            raise ValueError("effect_graph_controls must be a list of at most 64 bindings")
        if configs and graph is None:
            raise ValueError("effect_graph_controls requires effect_graph")
        # Validate the whole binding table before constructing *any* native
        # Motion. Invalid targets and colliding replacement bindings must fail
        # before loading external recording files or accessing project state.
        validated = []
        seen = set()
        allowed_fields = {
            "id", "node", "property", "motion", "axis", "range", "curve", "steps",
            "power", "invert", "combine", "weight", "lag_frames",
            "smoothing_frames", "deadzone", "sample_stride",
        }
        for row in configs:
            if not isinstance(row, dict) or set(row) - allowed_fields:
                raise ValueError("invalid effect graph control fields")
            node, prop = row.get("node"), row.get("property")
            if not isinstance(node, str) or not isinstance(prop, str):
                raise ValueError("graph control needs node and property")
            if (node, prop) in seen and row.get("combine", "set") == "set":
                raise ValueError("duplicate replacement graph control target")
            seen.add((node, prop))
            graph.validate_control_target(node, prop)
            validated.append((row, node, prop))

        self.bindings = []
        self.files = []
        for row, node, prop in validated:
            proxy = {k: v for k, v in row.items() if k not in {"node", "property"}}
            proxy["target"] = "style.value"
            driver = _Modulator(proxy, project, context_at)
            self.bindings.append((node, prop, driver))
            self.files.extend(driver.motion.files)

    def values(self, ctx, graph):
        result = {}
        for node, prop, driver in self.bindings:
            sample = driver.output(ctx)
            if sample is None:
                continue
            value, _ = sample
            key = (node, prop)
            start = result.get(key, graph.base_control_value(node, prop))
            result[key] = _combine(start, value, driver.combine, driver.weight)
        # The graph validates the final numeric range at evaluation time.
        return result
