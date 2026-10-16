"""Connected alpha-component cleaning extracted from the sprite preparation tools."""
from collections import deque
from PIL import Image

def components(alpha: Image.Image, threshold: int = 18) -> list[list[tuple[int, int]]]:
    width, height = alpha.size
    pixels = alpha.load()
    seen: set[tuple[int, int]] = set()
    result: list[list[tuple[int, int]]] = []
    for y in range(height):
        for x in range(width):
            if (x, y) in seen or pixels[x, y] < threshold:
                continue
            queue = deque([(x, y)])
            seen.add((x, y))
            group: list[tuple[int, int]] = []
            while queue:
                px, py = queue.popleft()
                group.append((px, py))
                for ny in range(max(0, py - 1), min(height, py + 2)):
                    for nx in range(max(0, px - 1), min(width, px + 2)):
                        if (nx, ny) not in seen and pixels[nx, ny] >= threshold:
                            seen.add((nx, ny))
                            queue.append((nx, ny))
            result.append(group)
    return sorted(result, key=len, reverse=True)

def isolate_one_object(cell: Image.Image) -> tuple[Image.Image, int, int]:
    rgba = cell.convert("RGBA")
    groups = components(rgba.getchannel("A"))
    if not groups:
        raise ValueError("atlas cell contains no opaque object")
    keep = set(groups[0])
    removed = sum(len(group) for group in groups[1:])
    src = rgba.load()
    for y in range(rgba.height):
        for x in range(rgba.width):
            if (x, y) not in keep:
                r, g, b, _ = src[x, y]
                src[x, y] = (r, g, b, 0)
    post = [group for group in components(rgba.getchannel("A")) if len(group) >= 8]
    if len(post) != 1:
        raise ValueError(f"cleaned cell has {len(post)} meaningful objects")
    return rgba, len(groups), removed
