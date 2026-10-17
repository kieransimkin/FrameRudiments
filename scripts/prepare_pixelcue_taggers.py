"""Prepare selected PixelCue keyword models in a project-local models/ tree.

Called during FrameRudiments full-catalogue setup, before preset processing.
This module is an installer/validator only: PixelCue owns all downloads,
model manifests, inference, and checkpoint selection.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

TAGGERS = (
    "smolvlm-256m",
    "smolvlm-500m",
    "smolvlm2-256m",
    "smolvlm2-500m",
    "joycaption",
)


def prepare_taggers(models_dir: Path, names: list[str], *, factory=None) -> list[Path]:
    """Fetch and verify selected taggers, refusing paths outside models_dir.

    The factory parameter permits fast offline regression tests; normally it is
    the published ``pixelcue.model.create_image_tagger`` factory.
    """
    if not names or set(names) - set(TAGGERS):
        raise ValueError("Choose at least one supported PixelCue tagger")
    root = Path(models_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)

    # An old user setting may otherwise direct downloads to an unrelated folder.
    os.environ.pop("PIXELCUE_MODEL_DIR", None)
    os.environ["PIXELCUE_MODELS_DIR"] = str(root)
    if factory is None:
        from pixelcue.model import create_image_tagger

        factory = create_image_tagger

    ready = []
    for name in dict.fromkeys(names):
        tagger = factory(name)
        declared = Path(tagger.model_dir).expanduser().resolve()
        if declared == root or not declared.is_relative_to(root):
            raise ValueError(f"PixelCue tried to use a model path outside its own folder: {declared}")
        print(f"Preparing PixelCue {name} at {declared} (cached files will be reused)", flush=True)
        downloaded = Path(tagger.ensure_downloaded()).expanduser().resolve()
        if downloaded != declared:
            raise ValueError("PixelCue downloaded a tagger outside its declared project-local path")
        print(f"PixelCue {name}: model ready at {downloaded}", flush=True)
        ready.append(downloaded)
    return ready


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-dir", type=Path, required=True)
    parser.add_argument("--taggers", nargs="+", choices=TAGGERS, required=True)
    args = parser.parse_args(argv)
    prepare_taggers(args.models_dir, args.taggers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
