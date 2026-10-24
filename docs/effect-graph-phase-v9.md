# Effect graph v9 — alpha-weighted luminance track mattes

The matte node supports `matte_channel: alpha | luminance` (default `alpha`). In luminance mode the matte RGB luminance is multiplied by its own alpha before feather/inversion and source-alpha multiplication. This prevents fully transparent white pixels from revealing the subject. All branches remain immutable and source images remain untouched.

## Acceptance tests

`python -m pytest tests/test_effect_graph.py -q` tests white, black and transparent-white matte pixels, and rejects unsupported channels. Existing chroma-key, fork and displacement regression tests must continue passing.

## Remaining work

Native DanceRudiment control bindings, graph end-to-end render fixtures, arbitrary spectral keying, GPU parity and graph UI remain outstanding.
