#!/usr/bin/env bash
set -euo pipefail

python_actual="$(python3.9 -c 'import platform; print(platform.python_version())')"
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

python3.9 - <<'PY'
from importlib.metadata import version
expected = {
    "pybind11": "3.0.2",
    "nanobind": "2.12.0",
    "pytest": "8.4.2",
}
for package, wanted in expected.items():
    actual = version(package)
    if actual != wanted:
        raise SystemExit(f"{package}: expected {wanted}, got {actual}")
    print(f"{package}={actual}")
PY
