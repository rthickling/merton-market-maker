#!/usr/bin/env bash
set -euo pipefail

python_mm="${PYTHON_MM:-${PYTHON_VERSION%.*}}"
python_bin="${PYTHON_BIN:-/opt/python-${python_mm}/bin/python${python_mm}}"
python_actual="$("$python_bin" -c 'import platform; print(platform.python_version())')"
quantlib_actual="$(/opt/ql_install/bin/quantlib-config --version)"
just_actual="$(just --version | awk '{print $2}')"

[[ "$python_actual" == "${PYTHON_VERSION:?}" ]]
[[ "$quantlib_actual" == "${QUANTLIB_VERSION:?}" ]]
[[ "$just_actual" == "${JUST_VERSION:?}" ]]

printf '%s\n' \
  "base_image=${MERTON_BASE_IMAGE:?}" \
  "ubuntu_snapshot=${UBUNTU_SNAPSHOT:?}" \
  "python=${python_actual}" \
  "quantlib=${quantlib_actual}" \
  "just=${just_actual}"

if [[ -n "${CLANG_P2996_COMMIT:-}" ]]; then
  clang_version="$(clang++ --version)"
  grep -Fq "$CLANG_P2996_COMMIT" <<<"$clang_version"
  printf 'compiler=clang-p2996\nclang_commit=%s\n' "$CLANG_P2996_COMMIT"
  sed -n '1,2p' <<<"$clang_version"
elif [[ -n "${GCC_VERSION:-}" ]]; then
  gcc_actual="$(g++ -dumpfullversion)"
  [[ "$gcc_actual" == "$GCC_VERSION" ]]
  printf 'compiler=gcc\ngcc_version=%s\n' "$gcc_actual"
  g++ --version | sed -n '1p'
else
  echo 'compiler metadata missing' >&2
  exit 1
fi

"$python_bin" - <<'PY'
import os
from importlib.metadata import version
expected = {
    "pybind11": "3.0.2",
    "nanobind": "2.12.0",
    "pytest": "8.4.2",
    "websockets": "15.0.1",
    "python-dotenv": "1.2.1",
    "numba": "0.68.0",
    "numpy": "2.5.3",
    "cppyy-cling": "6.32.8",
    "setuptools": "84.0.0",
    "wheel": "0.48.0",
}
if os.environ.get("GCC_VERSION"):
    expected |= {"cppyy": "3.5.0", "CPyCppyy": "1.13.0", "cppyy-backend": "1.15.3"}
for package, wanted in expected.items():
    actual = version(package)
    if actual != wanted:
        raise SystemExit(f"{package}: expected {wanted}, got {actual}")
    print(f"{package}={actual}")
PY
