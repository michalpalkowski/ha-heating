#!/usr/bin/env bash
set -euo pipefail
integration_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
image=ghcr.io/home-assistant/home-assistant:2026.10.2@sha256:33afb0659d786213264c2211f8d91c5fe87461c56eba6a3a546eb05fc71e89f4
docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges \
  --read-only --tmpfs /tmp:rw,nosuid,nodev,size=256m \
  --user "$(id -u):$(id -g)" --entrypoint python \
  --env PYTHONDONTWRITEBYTECODE=1 --volume "$integration_root:/src:ro" --workdir /src \
  "$image" -m unittest discover -s tests -v
