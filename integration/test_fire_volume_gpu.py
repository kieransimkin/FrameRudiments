"""Actual GL fire acceptance; missing GPU support fails rather than skips."""

import numpy as np
from framerudiments import FireRenderer


def test_volume_rgba_fullscreen_seek_motion_and_cleanup():
    f = FireRenderer((128, 96), "volume", resolution=1, glow=0)
    try:
        a = f.render(0.7, front=1)
        assert f.render(1.2, front=1).tobytes() != a.tobytes()
        assert f.render(0.7, front=1).tobytes() == a.tobytes()
        alpha = np.asarray(a)[:, :, 3]
        # Both bottom corners are painted: catches incorrect fullscreen triangle coverage.
        assert alpha[-10:, :15].max() > 0 and alpha[-10:, -15:].max() > 0
        assert alpha[:12].mean() < alpha[-12:].mean()
        assert f.evidence["backend_details"]["renderer"]
    finally:
        f.close()
    assert f.evidence["backend_details"]["cleanup_verified"]
