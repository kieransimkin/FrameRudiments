"""Reusable bounded raster operations extracted from the release compositors."""
from PIL import Image, ImageChops, ImageFilter


def alpha_scaled(image, opacity):
    opacity = max(0.0, min(1.0, opacity))
    result = image.copy()
    result.putalpha(result.getchannel("A").point(lambda v: round(v * opacity)))
    return result


def transformed_bounds(image, x, y):
    bounds = image.getchannel("A").getbbox()
    return None if bounds is None else (x + bounds[0], y + bounds[1], x + bounds[2], y + bounds[3])


def within(bounds, allowed):
    return bounds is None or (bounds[0] >= allowed[0] and bounds[1] >= allowed[1]
                              and bounds[2] <= allowed[2] and bounds[3] <= allowed[3])


def directional_trail(image, *, offset=(0, 0), opacity=.2, radius=2):
    """Same-pose trail avoids detached anatomy from a previous sprite pose."""
    dx, dy = map(int, offset)
    margin = int(radius * 3 + 2)
    result = Image.new("RGBA", (image.width + abs(dx) + margin * 2,
                                image.height + abs(dy) + margin * 2))
    origin = (margin + max(0, -dx), margin + max(0, -dy))
    trail = Image.new("RGBA", result.size)
    trail.alpha_composite(alpha_scaled(image, opacity), (origin[0] + dx, origin[1] + dy))
    result.alpha_composite(trail.filter(ImageFilter.GaussianBlur(radius)))
    result.alpha_composite(image, origin)
    return result


def masked_overlay(image, mask, opacity=1.0):
    result = alpha_scaled(image, opacity)
    if mask.size != result.size:
        raise ValueError("Overlay and mask dimensions must match")
    result.putalpha(ImageChops.multiply(result.getchannel("A"), mask.convert("L")))
    return result


def atlas_cell(image, *, columns, rows, index):
    if image.width % columns or image.height % rows:
        raise ValueError("Atlas cells must divide the exact sheet dimensions")
    if not 0 <= index < columns * rows:
        raise ValueError("Atlas index outside sheet")
    width, height = image.width // columns, image.height // rows
    x, y = index % columns * width, index // columns * height
    return image.crop((x, y, x + width, y + height))
