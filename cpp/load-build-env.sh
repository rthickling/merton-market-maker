#!/usr/bin/env bash

# Load saved build defaults without overriding values exported by the caller.
# This preserves command-line selections such as:
#   COMPILER=gcc MERTON_PYTHON_BINDING=pybind11 just test

_merton_cfg="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/.merton-build.env"
_merton_names=(
  COMPILER
  MERTON_PYTHON_BINDING
  DOCKER_NETWORK
  IMAGE_NAME
  MODULE_DEST_DIR
)
declare -A _merton_overrides=()

for _merton_name in "${_merton_names[@]}"; do
  if [[ -v "$_merton_name" ]]; then
    _merton_overrides["$_merton_name"]="${!_merton_name}"
  fi
done

if [[ -f "$_merton_cfg" ]]; then
  # shellcheck disable=SC1090
  source "$_merton_cfg"
fi

for _merton_name in "${!_merton_overrides[@]}"; do
  printf -v "$_merton_name" '%s' "${_merton_overrides[$_merton_name]}"
  export "$_merton_name"
done

unset _merton_cfg _merton_names _merton_overrides _merton_name
