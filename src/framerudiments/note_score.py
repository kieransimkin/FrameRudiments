"""Follow a reviewed, pinned score image using authored note-occurrence boxes."""

from bisect import bisect_right
from PIL import Image, ImageDraw
from .io import fields, integer, number, sha256
from .note_view_support import ids, label, text


class ScoreView:
    def __init__(self, layer, settings):
        fields(
            settings,
            {"notes_sha256", "reviewed", "provenance", "pages", "glyphs", "allow_upscale", "highlight"},
            "reviewed score",
        )
        if settings.get("notes_sha256") != layer.timeline.evidence["sha256"]:
            raise ValueError("Score layout must bind the exact admitted note-file SHA-256")
        if settings.get("reviewed") is not True:
            raise ValueError("Score images and note mappings must be explicitly reviewed")
        self.layer = layer
        self.provenance = text(settings.get("provenance"), "Score provenance", maximum=240)
        self.allow_upscale = settings.get("allow_upscale", False)
        if type(self.allow_upscale) is not bool:
            raise ValueError("Score allow_upscale must be boolean")
        from .note_view_support import colour

        self.highlight = colour(settings.get("highlight", "#52d6cf"), "Score highlight")
        pages = settings.get("pages")
        if not isinstance(pages, list) or not 1 <= len(pages) <= 32:
            raise ValueError("Reviewed score needs 1..32 pinned pages")
        self.pages = []
        self.starts = []
        total_pixels = 0
        previous_end = 0
        for page in pages:
            fields(page, {"image", "start_seconds", "end_seconds", "label"}, "score page")
            start = number(
                page.get("start_seconds"), "Page start", minimum=0, maximum=float(layer.project.duration)
            )
            end = number(
                page.get("end_seconds"), "Page end", minimum=start, maximum=float(layer.project.duration)
            )
            if end <= start or start < previous_end:
                raise ValueError("Score page windows must increase without overlap")
            previous_end = end
            ref = layer.project.ref(page["image"])
            with Image.open(ref["path"]) as image:
                width, height = image.size
            if min(width, height) < 1 or max(width, height) > 8192 or width * height > 16 * 1024**2:
                raise ValueError("Score page exceeds its 16-megapixel raster budget")
            total_pixels += width * height
            if total_pixels > 64 * 1024**2:
                raise ValueError("Score pages exceed their combined pixel budget")
            self.pages.append(
                dict(
                    image=ref,
                    width=width,
                    height=height,
                    start=start,
                    end=end,
                    label=text(page.get("label", f"Page {len(self.pages) + 1}"), "Page label"),
                )
            )
            self.starts.append(start)
        glyphs = settings.get("glyphs")
        if not isinstance(glyphs, list) or len(glyphs) > 65536:
            raise ValueError("Score needs a bounded authored glyph list")
        self.glyphs = []
        self.mapping = {}
        counts = [0] * len(pages)
        references = 0
        covered = set()
        for row in glyphs:
            fields(row, {"page", "note_indices", "bounds"}, "score glyph")
            page = integer(row.get("page"), "Score glyph page", maximum=len(pages) - 1)
            counts[page] += 1
            if counts[page] > 4096:
                raise ValueError("A score page exceeds 4096 glyphs")
            indices = ids(row.get("note_indices"), "Score note indices", high=len(layer.timeline.notes) - 1)
            if len(indices) > 128:
                raise ValueError("One score glyph has too many notes")
            references += len(indices)
            if references > 262144:
                raise ValueError("Score glyph reference budget exceeded")
            box = row.get("bounds")
            if not isinstance(box, list) or len(box) != 4:
                raise ValueError("Score glyph needs a pixel rectangle")
            p = self.pages[page]
            box = tuple(
                number(
                    v, "Score glyph coordinate", minimum=0, maximum=p["width"] if i % 2 == 0 else p["height"]
                )
                for i, v in enumerate(box)
            )
            if box[0] >= box[2] or box[1] >= box[3]:
                raise ValueError("Score glyph bounds must be increasing")
            for index in indices:
                n = layer.timeline.notes[index]
                if not (
                    n.start_seconds < p["end"]
                    and (
                        n.end_seconds > p["start"]
                        or n.start_seconds == n.end_seconds
                        and n.start_seconds >= p["start"]
                    )
                ):
                    raise ValueError("A glyph's note does not intersect its declared page")
                self.mapping.setdefault((page, index), []).append(len(self.glyphs))
            self.glyphs.append((page, box, indices))
            covered.update(indices)
        if any(i not in covered for i, n in enumerate(layer.timeline.notes) if layer._selected(n)):
            raise ValueError("Every selected note needs a reviewed score mapping")
        self.lookup = {id(n): i for i, n in enumerate(layer.timeline.notes)}
        self._loaded = None
        self._page_image = None
        self.frame_report = {}
        self.evidence = dict(
            notes_sha256=settings["notes_sha256"],
            reviewed=True,
            provenance=self.provenance,
            glyphs=len(glyphs),
            pages=[
                dict(
                    image=p["image"],
                    width=p["width"],
                    height=p["height"],
                    window_seconds=[p["start"], p["end"]],
                )
                for p in self.pages
            ],
            interpretation="authored raster/glyph mapping; no notation inference or transcription",
        )

    def query_window(self, now):
        return now, now

    def close(self):
        if self._page_image is not None:
            self._page_image.close()
            self._page_image = None

    def _image(self, index):
        if self._loaded != index:
            self.close()
            ref = self.pages[index]["image"]
            self.layer.project.ref({k: ref[k] for k in ("path", "sha256")})
            with Image.open(ref["path"]) as image:
                self._page_image = image.convert("RGBA")
            if sha256(ref["path"]) != ref["sha256"]:
                self.close()
                raise ValueError("Score page changed during admission")
            self._loaded = index
        return self._page_image

    def draw(self, canvas, now, notes, active, sounding):
        index = bisect_right(self.starts, now) - 1
        if index < 0 or now >= self.pages[index]["end"]:
            self.frame_report = dict(page=None, highlighted_glyphs=[])
            return
        page = self.pages[index]
        glyphs = sorted({g for n in sounding for g in self.mapping.get((index, self.lookup[id(n)]), ())})
        self.frame_report = dict(
            page=index,
            page_label=page["label"],
            highlighted_glyphs=glyphs,
            score_notes_sha256=self.evidence["notes_sha256"],
        )
        if min(canvas.size) < 12:
            return
        w, h = canvas.size
        supersampling = self.layer.project.output_quality.vector_scale
        scale = min(w * 0.94 / page["width"], h * 0.8 / page["height"])
        if not self.allow_upscale:
            scale = min(scale, supersampling)
        size = (max(1, round(page["width"] * scale)), max(1, round(page["height"] * scale)))
        image = self._image(index).resize(size, Image.Resampling.LANCZOS)
        ox, oy = round((w - size[0]) / 2), round((h - size[1]) / 2)
        canvas.alpha_composite(image, (ox, oy))
        draw = ImageDraw.Draw(canvas)
        for glyph in glyphs:
            _p, box, _indices = self.glyphs[glyph]
            draw.rounded_rectangle(
                (ox + box[0] * scale, oy + box[1] * scale, ox + box[2] * scale, oy + box[3] * scale),
                radius=max(1, scale * 3),
                outline=self.highlight,
                width=max(1, round(w / 240)),
            )
        progress = (now - page["start"]) / (page["end"] - page["start"])
        y = min(h - 2, oy + size[1] + h * 0.035)
        draw.line((ox, y, ox + size[0], y), fill="#354f63", width=max(1, round(h / 150)))
        draw.line((ox, y, ox + size[0] * progress, y), fill=self.highlight, width=max(1, round(h / 150)))
        label(draw, (w * 0.03, h * 0.025), page["label"], self.layer, w * 0.03)
