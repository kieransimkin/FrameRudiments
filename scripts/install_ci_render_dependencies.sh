#!/usr/bin/env bash
set -euo pipefail

packages=(ffmpeg fonts-dejavu-core)
if [[ "${1:-}" == "--gpu" ]]; then
  packages+=(libgl1-mesa-dev libgl1-mesa-dri libegl1 libglfw3 xvfb xauth ninja-build)
elif [[ $# -ne 0 ]]; then
  echo "usage: $0 [--gpu]" >&2
  exit 2
fi

# FFmpeg is in Ubuntu's official universe component. Some hosted images omit
# that component; a successful index refresh alone does not make it available.
if [[ -r /etc/os-release ]]; then
  . /etc/os-release
  if [[ "${ID:-}" == "ubuntu" ]]; then
    sudo timeout --signal=TERM --kill-after=15s 120s add-apt-repository --yes --no-update universe
  fi
fi

setup_started_seconds=$SECONDS
for attempt in 1 2 3; do
  echo "Installing CI render dependencies (attempt $attempt/3)"
  if sudo timeout --signal=TERM --kill-after=15s 300s env DEBIAN_FRONTEND=noninteractive apt-get update -qq -o APT::Update::Error-Mode=any \
    && sudo timeout --signal=TERM --kill-after=15s 600s env DEBIAN_FRONTEND=noninteractive \
      apt-get install -y "${packages[@]}"; then
    echo "CI render dependencies installed on attempt $attempt in $((SECONDS - setup_started_seconds))s"
    exit 0
  fi
  if [[ $attempt -lt 3 ]]; then
    sleep $((attempt * 10))
  fi
done

echo "Unable to install CI render dependencies after three bounded attempts" >&2
exit 1
