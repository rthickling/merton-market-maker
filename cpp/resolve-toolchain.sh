#!/usr/bin/env bash
# Resolve compiler, binding, Python, image name, and build dir.
# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/load-build-env.sh"
# shellcheck disable=SC1091
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/python-versions.sh"

COMPILER="${COMPILER:-gcc}"
MERTON_PYTHON_BINDING="${MERTON_PYTHON_BINDING:-nanobind}"
DOCKER_NETWORK="${DOCKER_NETWORK:-host}"
MODULE_DEST_DIR="${MODULE_DEST_DIR:-}"

case "$COMPILER" in
  clang)
    compiler_tag="clang-p2996"
    dockerfile="Dockerfile"
    toolchain="toolchain-p2996.cmake"
    default_image="merton-refl-build-py${PYTHON_MM}"
    ;;
  gcc)
    compiler_tag="gcc-16"
    dockerfile="Dockerfile.gcc"
    toolchain="toolchain-gcc-16.cmake"
    default_image="merton-gcc-16-py${PYTHON_MM}"
    ;;
  *)
    echo "COMPILER must be clang or gcc (got '$COMPILER')" >&2
    return 1 2>/dev/null || exit 1
    ;;
esac

image_name="${IMAGE_NAME:-$default_image}"
build_dir="build/${compiler_tag}-${MERTON_PYTHON_BINDING}-py${PYTHON_MM}"
lockfile="requirements-docker.lock.${PYTHON_MM}"
export COMPILER MERTON_PYTHON_BINDING DOCKER_NETWORK MODULE_DEST_DIR
export compiler_tag dockerfile toolchain image_name build_dir lockfile
