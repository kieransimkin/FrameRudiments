#!/usr/bin/env bash
set -euo pipefail

packages=(ffmpeg fonts-dejavu-core)
if [[ "${1:-}" == "--gpu" ]]; then
  packages+=(libgl1-mesa-dev libgl1-mesa-dri libegl1 libglfw3 xvfb xauth ninja-build)
elif [[ $# -ne 0 ]]; then
  echo "usage: $0 [--gpu]" >&2
  exit 2
fi

for attempt in 1 2 3; do
  echo "Installing CI render dependencies (attempt $attempt/3)"
  if sudo timeout --signal=TERM 300s env DEBIAN_FRONTEND=noninteractive apt-get update -qq \
    && sudo timeout --signal=TERM 600s env DEBIAN_FRONTEND=noninteractive \
      apt-get install -y "${packages[@]}"; then
    exit 0
  fi
  if [[ $attempt -lt 3 ]]; then
    sleep $((attempt * 10))
  fi
done

echo "Unable to install CI render dependencies after three bounded attempts" >&2
exit 1
