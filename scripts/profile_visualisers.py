"""Feed projects/audio to presets, capture intervals and invoke PrismMoves.

Usage: python scripts/profile_visualisers.py SUITE CATALOGUE --output NEW_DIRECTORY
All remaining options are the maintained `framerudiments presets-profile` options.
"""

import sys
from framerudiments.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["presets-profile", *sys.argv[1:]]))
