"""The C++ calibrator called through cppyy instead of compiled bindings.

cppyy reads the declarations in cpp/include/merton_online_calibrator.hpp and
calls into libmerton_core_shared.so: the core compiled from the same source,
with the same flags, as the static library inside the nanobind modules (CMake
option MERTON_BUILD_SHARED_CORE=ON, GCC only). The library exports just the
five out-of-line public methods. Everything else a call needs, cppyy's
interpreter compiles on first use: its call wrappers, the header's inline
accessors and the implicit destructor. warm_up() does that up front.

The library is found on sys.path, like the extension modules. MertonParams
gains a `lambda_` alias for its `lambda` member, as in the nanobind modules, so
callers can use the three the same way. One difference stays: params() returns
a reference to the calibrator's own parameters, not a copy.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cppyy

LIBRARY = "libmerton_core_shared.so"
INCLUDE_DIR = Path(__file__).resolve().parents[1] / "cpp" / "include"


def _find_library() -> Path | None:
    for entry in sys.path:
        candidate = Path(entry or ".") / LIBRARY
        if candidate.is_file():
            return candidate
    return None


_library = _find_library()
if _library is None:
    raise ModuleNotFoundError(f"{LIBRARY} is not on sys.path; build it with -DMERTON_BUILD_SHARED_CORE=ON", name=LIBRARY)

cppyy.add_include_path(str(INCLUDE_DIR))
cppyy.include("merton_online_calibrator.hpp")
cppyy.load_library(str(_library))
cppyy.cppdef('extern "C" std::size_t merton_calibrator_size();')

MertonParams = cppyy.gbl.merton.MertonParams
CalibratorConfig = cppyy.gbl.merton.CalibratorConfig
OnlineMertonCalibrator = cppyy.gbl.merton.OnlineMertonCalibrator

_sizes = cppyy.sizeof(OnlineMertonCalibrator), cppyy.gbl.merton_calibrator_size()
if _sizes[0] != _sizes[1]:
    raise ImportError(
        f"cppyy lays out merton::OnlineMertonCalibrator in {_sizes[0]} bytes and {LIBRARY} in {_sizes[1]}: "
        "the interpreter is reading incompatible standard library headers (see EXTRA_CLING_ARGS)"
    )

MertonParams.lambda_ = property(lambda p: getattr(p, "lambda"), lambda p, value: setattr(p, "lambda", value))


def warm_up() -> None:
    """Call each method the benchmark times once, so cppyy has compiled its wrappers."""
    cal = OnlineMertonCalibrator(MertonParams(), CalibratorConfig())
    cal.update_tick(100.0, 1)
    cal.update_tick(100.0, 2)
    cal.maybe_update_params()
    cal.fair_value(100.0, 0.0, 1e-3, 0.0)
    cal.params()
    cal.sample_count()
    cal.calibration_count()
    del cal
