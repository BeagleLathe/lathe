#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLEANUP_DIRS=()

cleanup() {
    [[ ${#CLEANUP_DIRS[@]} -gt 0 ]] && rm -rf "${CLEANUP_DIRS[@]}"
}
trap cleanup EXIT

# Install `build` into a temp venv if it isn't already available
if ! python3 -c "import build" 2>/dev/null; then
    BUILD_VENV=$(mktemp -d)
    CLEANUP_DIRS+=("$BUILD_VENV")
    python3 -m venv "$BUILD_VENV"
    "$BUILD_VENV/bin/pip" install --quiet build
    BUILD_PYTHON="$BUILD_VENV/bin/python"
else
    BUILD_PYTHON="python3"
fi

BUILD_DIR=$(mktemp -d)
CLEANUP_DIRS+=("$BUILD_DIR")
cd "$REPO_ROOT"
"$BUILD_PYTHON" -m build --wheel --outdir "$BUILD_DIR"
WHEEL=$(ls "$BUILD_DIR/"*.whl)

TEST_VENV=$(mktemp -d)
CLEANUP_DIRS+=("$TEST_VENV")
python3 -m venv "$TEST_VENV"
"$TEST_VENV/bin/pip" install --quiet "$WHEEL[dev]"

TEST_DIR=$(mktemp -d)
CLEANUP_DIRS+=("$TEST_DIR")
cp "$REPO_ROOT/tests/test_resolver.py" "$TEST_DIR/"

env -i PATH=/usr/bin HOME="$HOME" \
    "$TEST_VENV/bin/pytest" "$TEST_DIR/test_resolver.py" -v
