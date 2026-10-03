#!/usr/bin/env bash
# Pin table for selectable CPython source builds. Sourced after load-build-env.sh.

_merton_normalize_python_version() {
  local requested="${PYTHON_VERSION:-3.14.8}"
  case "$requested" in
    3.14|3.14.*) PYTHON_VERSION=3.14.8 ;;
    3.13|3.13.*) PYTHON_VERSION=3.13.16 ;;
    *)
      echo "Unsupported PYTHON_VERSION='$requested' (use 3.14.8 or 3.13.16)." >&2
      return 1
      ;;
  esac
  PYTHON_MM="${PYTHON_VERSION%.*}"
  case "$PYTHON_VERSION" in
    3.14.8)
      PYTHON_SHA256=c2215904f02b175596dc49351585104f4bc20341e1c47378b26a2c274360ce73
      ;;
    3.13.16)
      PYTHON_SHA256=f4b1bfb3c79b5bb11b8d228a12504163b4c0dab4d679828d8f5f26b6cb6ab35d
      ;;
  esac
  PYTHON_BIN="/opt/python-${PYTHON_MM}/bin/python${PYTHON_MM}"
  export PYTHON_VERSION PYTHON_MM PYTHON_SHA256 PYTHON_BIN
}

_merton_normalize_python_version
unset -f _merton_normalize_python_version
