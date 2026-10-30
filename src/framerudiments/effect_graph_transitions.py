"""Deterministic, alpha-correct two-source effect-graph transitions."""

from PIL import Image

from .effect_graph_transition_mattes import transition_matte_weight
from .effect_graph_transition_spectral import spectral_transition
from .effect_graph_transition_geometry import GEOMETRIC_STYLES, geometric_transition


def render_transition(from_image: Image.Image, to_image: Image.Image, config: dict,
                      *, matte_image: Image.Image | None = None) -> Image.Image:
    """Two-input source-over *interpolation* in premultiplied RGBA.

    Graph transitions are deterministic at arbitrary progress and preserve
    straight-alpha output for the existing Pillow compositing pipeline. The
    working colour representation is currently display-referred RGBA; true
    scene-linear/HDR interpolation is a separately tracked milestone.
    """
    import numpy as np

    if config.get('transition_style', 'dissolve') in GEOMETRIC_STYLES:
        if matte_image is not None:
            raise ValueError('geometric transition accepts exactly two inputs')
        return geometric_transition(from_image, to_image, config)
    if config.get('transition_style', 'dissolve') == 'spectral':
        if matte_image is not None:
            raise ValueError('spectral transition accepts exactly two inputs')
        return spectral_transition(from_image, to_image, config)
    progress = config.get('progress', 0.0)
    if progress <= 0:
        return from_image.copy()
    if progress >= 1:
        return to_image.copy()

    height, width = from_image.height, from_image.width
    style = config.get('transition_style', 'dissolve')
    if style == 'matte':
        if matte_image is None:
            raise ValueError('matte transition needs a third mask input')
        if matte_image.size != from_image.size:
            raise ValueError('matte transition inputs need equal dimensions')
        weight = transition_matte_weight(matte_image, config, progress)
    elif style == 'dissolve':
        weight = np.full((height, width), progress, dtype=np.float32)
    else:
        yy, xx = np.indices((height, width), dtype=np.float32)
        if style == 'wipe':
            direction = config.get('direction', 'right')
            if direction == 'right':
                coordinate = (xx + 0.5) / width
            elif direction == 'left':
                coordinate = 1 - (xx + 0.5) / width
            elif direction == 'down':
                coordinate = (yy + 0.5) / height
            else:
                coordinate = 1 - (yy + 0.5) / height
        else:
            cx, cy = config.get('center', [0.5, 0.5])
            x, y = (xx + 0.5) / width, (yy + 0.5) / height
            radius = max(
                ((corner_x - cx) ** 2 + (corner_y - cy) ** 2) ** 0.5
                for corner_x in (0, 1) for corner_y in (0, 1)
            )
            coordinate = np.sqrt((x - cx) ** 2 + (y - cy) ** 2) / max(radius, 1e-8)
        feather = config.get('feather', 0.0)
        if feather > 0:
            t = np.clip((progress - coordinate) / feather + 0.5, 0, 1)
            weight = t * t * (3 - 2 * t)
        else:
            weight = (coordinate <= progress).astype(np.float32)

    a = np.asarray(from_image, dtype=np.float32) / 255.0
    b = np.asarray(to_image, dtype=np.float32) / 255.0
    wa, wb = (1 - weight)[..., None], weight[..., None]
    alpha = wa * a[..., 3:4] + wb * b[..., 3:4]
    premul_rgb = wa * a[..., :3] * a[..., 3:4] + wb * b[..., :3] * b[..., 3:4]
    rgb = np.divide(premul_rgb, alpha, out=np.zeros_like(premul_rgb), where=alpha > 0)
    out = np.concatenate([rgb, alpha], axis=-1)
    return Image.fromarray(np.uint8(np.rint(np.clip(out, 0, 1) * 255)), 'RGBA')
