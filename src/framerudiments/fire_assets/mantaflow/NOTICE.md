# Original simulated fire

Original FrameRudiments Blender/Mantaflow gas-domain simulation, MIT licensed.
Blender 5.0.1 (build a3db93c5b259), 96 domain resolution with noise scale2,
60-frame warm-up, 120 rendered frames at60fps. Exact source blend and frame
SHA-256 hashes, render settings and stable crop are in atlas.json. RGB emission
is keyed over black; raw volume extinction alpha is not used for flame opacity.

The included asset is an actual bake, not a filmed flame or a claim of
scientific combustion accuracy. Large simulation caches and source scenes are
not included in the package. Reproduce with scripts/bake_mantaflow_fire.py
and scripts/prepare_fire_atlas.py as described in docs/natural-materials.md.
