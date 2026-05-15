#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

docker buildx build --platform linux/amd64 -f scripts/test-install.dockerfile "$REPO_ROOT"
docker buildx build --platform linux/arm64 -f scripts/test-install.dockerfile "$REPO_ROOT"
