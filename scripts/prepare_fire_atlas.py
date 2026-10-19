"""Prepare explicitly supplied, rights-reviewed footage or a maintained Blender bake."""

import argparse
from framerudiments.fire_assets import prepare_footage, pack_mantaflow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="method", required=True)
    video = commands.add_parser("footage")
    video.add_argument("source")
    video.add_argument("--sha256", required=True)
    video.add_argument("--ffmpeg", required=True)
    video.add_argument("--ffprobe", required=True)
    video.add_argument("--source-url", required=True)
    video.add_argument("--start", type=float, default=0)
    video.add_argument("--duration", type=float, default=4)
    video.add_argument("--fps", type=int, default=60)
    video.add_argument("--crop", nargs=4, type=int)
    video.add_argument("--rotation", type=float, default=0)
    video.add_argument("--black-level", type=float, default=0.035)
    bake = commands.add_parser("mantaflow")
    bake.add_argument("source")
    for sub in (video, bake):
        sub.add_argument("output")
        sub.add_argument("--licence", required=True, help="Reviewed asset rights; no rights are inferred")
    args = vars(parser.parse_args())
    method, source, output = (args.pop(k) for k in ("method", "source", "output"))
    if method == "mantaflow":
        path = pack_mantaflow(source, output, **args)
    else:
        digest = args.pop("sha256")
        path = prepare_footage(dict(path=source, sha256=digest), output, **args)
    print(path)


if __name__ == "__main__":
    main()
