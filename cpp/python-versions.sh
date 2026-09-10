#!/usr/bin/env bash
# Pin table for selectable CPython source builds. Sourced after load-build-env.sh.

_merton_normalize_python_version() {
  local requested="${PYTHON_VERSION:-3.12.11}"
  case "$requested" in
    3.9|3.9.*) PYTHON_VERSION=3.9.25 ;;
    3.12|3.12.*) PYTHON_VERSION=3.12.11 ;;
    3.13|3.13.*) PYTHON_VERSION=3.13.7 ;;
    *)
      echo "Unsupported PYTHON_VERSION='$requested' (use 3.9.25, 3.12.11, or 3.13.7)." >&2
      return 1
      ;;
  esac
  PYTHON_MM="${PYTHON_VERSION%.*}"
  case "$PYTHON_VERSION" in
    3.9.25)
      PYTHON_SHA256=00e07d7c0f2f0cc002432d1ee84d2a40dae404a99303e3f97701c10966c91834
      ;;
    3.12.11)
      PYTHON_SHA256=c30bb24b7f1e9a19b11b55a546434f74e739bb4c271a3e3a80ff4380d49f7adb
      ;;
    3.13.7)
      PYTHON_SHA256=5462f9099dfd30e238def83c71d91897d8caa5ff6ebc7a50f14d4802cdaaa79a
      ;;
  esac
  PYTHON_BIN="/opt/python-${PYTHON_MM}/bin/python${PYTHON_MM}"
  export PYTHON_VERSION PYTHON_MM PYTHON_SHA256 PYTHON_BIN
}

_merton_normalize_python_version
unset -f _merton_normalize_python_version
