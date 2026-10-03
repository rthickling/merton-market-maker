#!/usr/bin/env python3
"""Time the calibrator in readable Python and in C++ behind two kinds of bindings.

Variants:
  python     scripts/merton_reference.py, the line-by-line Python translation
  reflected  merton_online_calibrator, bindings generated with C++26 reflection
  manual     merton_manual_bindings, equivalent hand-written nanobind bindings

Validation comes first and stops the run on failure: the agreement and
equivalence tests, then the exact timed workloads compared across variants.
Calibration and replay time each variant in its own subprocess, in alternating
order across runs. The cheap-call comparison loads both C++ modules into one
subprocess per run and alternates between them in ABBA blocks, so drift in
machine state affects both equally. Timers cover only the calls being measured,
with the garbage collector paused, as timeit does.

Run inside the build image with `cd cpp && just bench`, which writes bench.json,
bench.md and calibration.svg. `--reference-only` times just the Python
reference, so other interpreters can be compared without the C++ modules.
"""

from __future__ import annotations

import argparse
import datetime as dt
import functools
import gc
import importlib
import itertools
import json
import math
import os
import platform
import shlex
import statistics
import subprocess
import sys
import sysconfig
import time
from html import escape
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts import merton_reference as ref  # noqa: E402
from scripts.merton_runtime import (  # noqa: E402
    CPP_COORDINATE_STEPS,
    CPP_MIN_POINTS_FOR_UPDATE,
    CPP_N_MAX,
    CPP_UPDATE_EVERY_N_RETURNS,
    CPP_WINDOW_SIZE,
    T_YEARS,
)
from scripts.synthetic_ticks import PathSpec, merton_ticks  # noqa: E402

VARIANTS = ("python", "reflected", "manual")
CPP_MODULES = {"reflected": "merton_online_calibrator", "manual": "merton_manual_bindings"}
LABELS = {
    "python": "Python reference",
    "reflected": "C++, reflected bindings",
    "manual": "C++, hand-written bindings",
}
CALLS = ("fair_value", "update_tick", "sample_count")
CALL_SIGNATURES = {
    "fair_value": "fair_value(s0, q_annual, t_years, r)",
    "update_tick": "update_tick(price, epoch_us)",
    "sample_count": "sample_count()",
}

# The live runtime's configuration (scripts/merton_runtime.py).
CONFIG = {
    "window_size": CPP_WINDOW_SIZE,
    "min_points_for_update": CPP_MIN_POINTS_FOR_UPDATE,
    "update_every_n_returns": CPP_UPDATE_EVERY_N_RETURNS,
    "n_max": CPP_N_MAX,
    "coordinate_steps": CPP_COORDINATE_STEPS,
}
SEED = 7
CALIBRATION_TICKS = CPP_WINDOW_SIZE + CPP_UPDATE_EVERY_N_RETURNS + 1
REPLAY_TICKS = CPP_WINDOW_SIZE + 1
FAIR_VALUE_ARGS = (68_000.0, 0.1, T_YEARS, 0.0)

# Same tolerances as tests/test_reference_agreement.py.
PARAM_REL_TOL, PARAM_ABS_TOL = 1e-12, 1e-15
FAIR_VALUE_REL_TOL = 1e-13

FULL_PLAN = {
    "runs": 3,
    "calibration": {"python": {"samples": 15, "warmup": 1}, "cpp": {"samples": 51, "warmup": 3}},
    "replay": {"python": {"samples": 1}, "cpp": {"samples": 5}},
    "calls": {"blocks": 16, "warmup_blocks": 1, "calls_per_sample": 100_000},
}
QUICK_PLAN = {
    "runs": 1,
    "calibration": {"python": {"samples": 2, "warmup": 0}, "cpp": {"samples": 11, "warmup": 1}},
    "replay": {"python": {"samples": 1}, "cpp": {"samples": 2}},
    "calls": {"blocks": 3, "warmup_blocks": 1, "calls_per_sample": 20_000},
}


# --- workloads -----------------------------------------------------------------


def make_calibrator(variant: str):
    if variant == "python":
        return ref.OnlineMertonCalibrator(ref.MertonParams(), ref.CalibratorConfig(**CONFIG))
    module = importlib.import_module(CPP_MODULES[variant])
    cfg = module.CalibratorConfig()
    for name, value in CONFIG.items():
        setattr(cfg, name, value)
    return module.OnlineMertonCalibrator(module.MertonParams(), cfg)


def snapshot(cal) -> dict:
    p = cal.params()
    return {
        "params": [p.sigma, p.lambda_, p.mu_j, p.delta_j],
        "calibrations": cal.calibration_count(),
        "samples": cal.sample_count(),
    }


def timed(fn):
    """Run fn once with the garbage collector paused; return (ns, result)."""
    gc.collect()
    gc.disable()
    try:
        start = time.perf_counter_ns()
        result = fn()
        return time.perf_counter_ns() - start, result
    finally:
        gc.enable()


def fed_calibrator(variant: str, ticks):
    cal = make_calibrator(variant)
    for price, ts in ticks:
        cal.update_tick(price, ts)
    return cal


def replay(cal, ticks) -> int:
    update, maybe = cal.update_tick, cal.maybe_update_params
    changed = 0
    for price, ts in ticks:
        update(price, ts)
        changed += maybe()
    return changed


def time_calibration(variant: str, samples: int, warmup: int) -> dict:
    ticks = merton_ticks(PathSpec(seed=SEED, n_ticks=CALIBRATION_TICKS))
    times, changed, states = [], [], []
    for i in range(warmup + samples):
        cal = fed_calibrator(variant, ticks)
        before = cal.calibration_count()
        elapsed, did_change = timed(cal.maybe_update_params)
        if cal.calibration_count() != before + 1:
            raise RuntimeError(f"{variant}: the timed call did not run exactly one calibration")
        if i >= warmup:
            times.append(elapsed)
            changed.append(did_change)
            states.append(snapshot(cal))
    if any(state != states[0] for state in states) or len(set(changed)) != 1:
        raise RuntimeError(f"{variant}: identical calibrations gave different results")
    return {"ns": times, "state": {**states[0], "changed": changed[0]}}


def time_replay(variant: str, samples: int) -> dict:
    ticks = merton_ticks(PathSpec(seed=SEED, n_ticks=REPLAY_TICKS))
    times, states = [], []
    for _ in range(samples):
        cal = make_calibrator(variant)
        elapsed, changed = timed(lambda: replay(cal, ticks))
        times.append(elapsed)
        states.append({**snapshot(cal), "changed": changed})
    if any(state != states[0] for state in states):
        raise RuntimeError(f"{variant}: identical replays gave different results")
    return {"ns": times, "state": states[0]}


def _loop_fair_value(f, n, s0, q_annual, t_years, r):
    for _ in itertools.repeat(None, n):
        f(s0, q_annual, t_years, r)


def _loop_update_tick(f, price, stamps):
    for ts in stamps:
        f(price, ts)


def _loop_sample_count(f, n):
    for _ in itertools.repeat(None, n):
        f()


def _loop_empty(n):
    for _ in itertools.repeat(None, n):
        pass


def time_calls(first: str, blocks: int, warmup_blocks: int, calls: int) -> dict:
    """Time both C++ modules in this process, alternating in ABBA blocks for each call."""
    ticks = merton_ticks(PathSpec(seed=SEED, n_ticks=CALIBRATION_TICKS))
    cals = {variant: fed_calibrator(variant, ticks) for variant in CPP_MODULES}
    second = next(variant for variant in CPP_MODULES if variant != first)
    step_us = ticks[1][1] - ticks[0][1]
    price = ticks[-1][0]
    next_ts = dict.fromkeys(CPP_MODULES, ticks[-1][1] + step_us)

    def sample(name: str, variant: str) -> float:
        cal = cals[variant]
        if name == "fair_value":
            loop = functools.partial(_loop_fair_value, cal.fair_value, calls, *FAIR_VALUE_ARGS)
        elif name == "update_tick":
            # Rising timestamps keep update_tick on its normal path (accept, push, pop).
            start = next_ts[variant]
            next_ts[variant] = start + step_us * calls
            stamps = list(range(start, next_ts[variant], step_us))
            loop = functools.partial(_loop_update_tick, cal.update_tick, price, stamps)
        else:
            loop = functools.partial(_loop_sample_count, cal.sample_count, calls)
        return timed(loop)[0] / calls

    per_call = {variant: {name: [] for name in CALLS} for variant in CPP_MODULES}
    differences = {name: [] for name in CALLS}
    empty = []
    for block in range(warmup_blocks + blocks):
        block_ns = {}
        for name in CALLS:
            block_ns[name] = {variant: [] for variant in CPP_MODULES}
            for variant in (first, second, second, first):
                block_ns[name][variant].append(sample(name, variant))
        empty_ns = timed(functools.partial(_loop_empty, calls))[0] / calls
        if block < warmup_blocks:
            continue
        empty.append(empty_ns)
        for name, got in block_ns.items():
            for variant in CPP_MODULES:
                per_call[variant][name] += got[variant]
            differences[name].append(statistics.fmean(got["reflected"]) - statistics.fmean(got["manual"]))
    state = {
        variant: {**snapshot(cal), "fair_value": cal.fair_value(*FAIR_VALUE_ARGS)} for variant, cal in cals.items()
    }
    return {
        "first": first,
        "ns_per_call": per_call,
        "block_differences": differences,
        "empty_loop": empty,
        "state": state,
    }


def worker(spec: dict) -> dict:
    task, variant = spec["task"], spec.get("variant")
    if task == "calibration":
        return time_calibration(variant, spec["samples"], spec["warmup"])
    if task == "replay":
        return time_replay(variant, spec["samples"])
    if task == "calls":
        return time_calls(spec["first"], spec["blocks"], spec["warmup_blocks"], spec["calls_per_sample"])
    raise ValueError(f"unknown task {task!r}")


def run_worker(spec: dict) -> dict:
    cmd = [sys.executable, str(Path(__file__).resolve()), "--worker", json.dumps(spec)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"worker failed: {spec}")
    return json.loads(proc.stdout)


# --- validation ----------------------------------------------------------------


def ulps(a: float, b: float) -> float:
    return 0.0 if a == b else abs(a - b) / math.ulp(max(abs(a), abs(b)))


def params_agree(python_params, cpp_params) -> bool:
    return all(
        math.isclose(a, b, rel_tol=PARAM_REL_TOL, abs_tol=PARAM_ABS_TOL)
        for a, b in zip(python_params, cpp_params)
    )


def check_python_matches(python_state: dict, cpp_state: dict, what: str) -> float:
    exact = [k for k in python_state if k != "params"]
    if any(python_state[k] != cpp_state[k] for k in exact):
        raise SystemExit(f"validation failed: {what}: counters differ {python_state} vs {cpp_state}")
    if not params_agree(python_state["params"], cpp_state["params"]):
        raise SystemExit(f"validation failed: {what}: parameters differ {python_state} vs {cpp_state}")
    return max(ulps(a, b) for a, b in zip(python_state["params"], cpp_state["params"]))


def validate(tests_dir: Path) -> dict:
    progress("validating: agreement and equivalence tests")
    tests = [tests_dir / "test_reference_agreement.py", tests_dir / "test_manual_bindings.py"]
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *map(str, tests)],
        capture_output=True,
        text=True,
    )
    summary = next((line for line in reversed(proc.stdout.splitlines()) if " passed" in line), "")
    if proc.returncode != 0 or "skipped" in summary:
        sys.stderr.write(proc.stdout + proc.stderr)
        raise SystemExit(f"validation failed: {summary or 'pytest did not run'}")

    progress("validating: the timed workloads give the same results in every variant")
    ticks = merton_ticks(PathSpec(seed=SEED, n_ticks=CALIBRATION_TICKS))
    outcome = {}
    for variant in VARIANTS:
        cal = fed_calibrator(variant, ticks)
        changed = cal.maybe_update_params()
        outcome[variant] = {**snapshot(cal), "changed": changed}
        outcome[variant]["fair_value"] = cal.fair_value(*FAIR_VALUE_ARGS)
    if outcome["reflected"] != outcome["manual"]:
        raise SystemExit("validation failed: reflected and hand-written modules differ")
    py, cpp = dict(outcome["python"]), dict(outcome["reflected"])
    py_fv, cpp_fv = py.pop("fair_value"), cpp.pop("fair_value")
    worst = check_python_matches(py, cpp, "calibration workload")
    if not math.isclose(py_fv, cpp_fv, rel_tol=FAIR_VALUE_REL_TOL, abs_tol=0.0):
        raise SystemExit(f"validation failed: fair_value {py_fv!r} vs {cpp_fv!r}")

    replay_ticks = merton_ticks(PathSpec(seed=SEED, n_ticks=REPLAY_TICKS))
    replays = {}
    for variant in CPP_MODULES:
        cal = make_calibrator(variant)
        changed = replay(cal, replay_ticks)
        replays[variant] = {**snapshot(cal), "changed": changed}
    if replays["reflected"] != replays["manual"]:
        raise SystemExit("validation failed: replay differs between reflected and hand-written modules")

    return {
        "tests": summary,
        "calibration": cpp,
        "replay": replays["reflected"],
        "python_vs_cpp_max_ulps": worst,
        "fair_value_ulps": ulps(py_fv, cpp_fv),
    }


# --- statistics ----------------------------------------------------------------


def describe(values: list[float]) -> dict:
    values = sorted(values)
    if len(values) == 1:
        q1 = q3 = values[0]
    else:
        q1, _, q3 = statistics.quantiles(values, n=4, method="inclusive")
    return {"median": statistics.median(values), "q1": q1, "q3": q3, "min": values[0], "n": len(values)}


def across_runs(per_run: list[list[float]]) -> dict:
    medians = [statistics.median(run) for run in per_run]
    return {
        "median": statistics.median(medians),
        "run_medians": medians,
        "spread": max(medians) - min(medians),
        "pooled": describe([v for run in per_run for v in run]),
    }


def summarize(raw: dict, runs: int) -> dict:
    summary = {"calibration": {}, "replay": {}, "calls": {}}
    for task in ("calibration", "replay"):
        for variant in VARIANTS:
            summary[task][variant] = across_runs([r["ns"] for r in raw[task][variant]])
        for variant in CPP_MODULES:
            ratios = [
                p / c
                for p, c in zip(summary[task]["python"]["run_medians"], summary[task][variant]["run_medians"])
            ]
            summary[task][variant]["speedup"] = summary[task]["python"]["median"] / summary[task][variant]["median"]
            summary[task][variant]["speedup_runs"] = ratios
    for name in CALLS:
        per_run = [statistics.median(r["block_differences"][name]) for r in raw["calls"]]
        difference = statistics.median(per_run)
        spread = max(per_run) - min(per_run)
        summary["calls"][name] = {
            **{v: across_runs([r["ns_per_call"][v][name] for r in raw["calls"]]) for v in CPP_MODULES},
            "both_modules": describe([ns for r in raw["calls"] for v in CPP_MODULES for ns in r["ns_per_call"][v][name]]),
            "runs": [
                {
                    **{v: describe(r["ns_per_call"][v][name]) for v in CPP_MODULES},
                    "difference": describe(r["block_differences"][name]),
                }
                for r in raw["calls"]
            ],
            "difference": difference,
            "difference_runs": per_run,
            "spread": spread,
            "within_spread": runs >= 2 and abs(difference) <= spread,
        }
    summary["calls"]["empty_loop"] = across_runs([r["empty_loop"] for r in raw["calls"]])
    return summary


# --- environment ---------------------------------------------------------------


def read(path) -> str | None:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def conditions() -> dict:
    cpu = "/sys/devices/system/cpu/cpu0/cpufreq/"
    supplies = []
    for supply in sorted(Path("/sys/class/power_supply").glob("*")):
        kind = read(supply / "type")
        if kind in ("Mains", "Battery"):
            supplies.append(
                {
                    "name": supply.name,
                    "type": kind,
                    "online": read(supply / "online"),
                    "status": read(supply / "status"),
                    "capacity": read(supply / "capacity"),
                }
            )
    return {
        "load_average": [round(x, 2) for x in os.getloadavg()],
        "governor": read(cpu + "scaling_governor"),
        "energy_performance_preference": read(cpu + "energy_performance_preference"),
        "cpufreq_driver": read(cpu + "scaling_driver"),
        "boost": read("/sys/devices/system/cpu/cpufreq/boost"),
        "platform_profile": read("/sys/firmware/acpi/platform_profile"),
        "on_mains": any(s["type"] == "Mains" and s["online"] == "1" for s in supplies) if supplies else None,
        "power_supplies": supplies,
    }


def _compile_flags(args: list[str]) -> list[str]:
    flags, skip = [], False
    for arg in args:
        if skip:
            skip = False
        elif arg in ("-o", "-c", "-MF", "-MT", "-isystem"):
            skip = True
        elif arg.startswith("-") and arg != "-MD" and not arg.startswith("-I"):
            flags.append(arg)
    return flags


def environment(cpp: bool) -> dict:
    config_args = sysconfig.get_config_var("CONFIG_ARGS") or ""
    jit = getattr(sys, "_jit", None)
    cpu_model = next(
        (line.split(":", 1)[1].strip() for line in (read("/proc/cpuinfo") or "").splitlines()
         if line.startswith("model name")),
        platform.processor() or None,
    )
    env = {
        "commit": os.environ.get("BENCH_COMMIT", "unknown"),
        "cpu": cpu_model,
        "logical_cpus": os.cpu_count(),
        "kernel": platform.release(),
        "python": sys.version.split()[0],
        "python_configure": config_args,
        "python_pgo": "--enable-optimizations" in config_args,
        "python_lto": "--with-lto" in config_args,
        "python_jit": bool(jit and jit.is_enabled()),
        "python_gil": sys._is_gil_enabled() if hasattr(sys, "_is_gil_enabled") else True,
    }
    if not cpp:
        return env
    import importlib.metadata

    import merton_online_calibrator

    build_dir = Path(merton_online_calibrator.__file__).resolve().parent
    flags, compiler = {}, None
    commands = build_dir / "compile_commands.json"
    if commands.exists():
        for entry in json.loads(commands.read_text()):
            name = Path(entry["file"]).name
            if name in ("merton_online_calibrator.cpp", "python_module_entry_nanobind.cpp",
                        "manual_bindings_nanobind.cpp"):
                args = shlex.split(entry["command"])
                compiler = compiler or args[0]
                flags[name] = _compile_flags(args[1:])
    version = None
    if compiler:
        proc = subprocess.run([compiler, "--version"], capture_output=True, text=True)
        version = proc.stdout.splitlines()[0] if proc.stdout else None
    env.update(
        {
            "build_dir": str(build_dir),
            "compiler": version,
            "nanobind": importlib.metadata.version("nanobind"),
            "compile_flags": flags,
        }
    )
    return env


# --- reporting -----------------------------------------------------------------


def progress(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def fmt_time(ns: float) -> str:
    ms = ns / 1e6
    return f"{ms:.3g} ms" if ms < 1000 else f"{ms / 1000:.3g} s"


def fmt_range(values: list[float], fmt) -> str:
    return fmt(min(values)) if len(values) == 1 else f"{fmt(min(values))} – {fmt(max(values))}"


def axis_label(ms: float) -> str:
    return f"{ms:g} ms" if ms < 1000 else f"{ms / 1000:g} s"


def calibration_svg(rows: list[tuple[str, float, float, float, str, str]], title: str, subtitle: str) -> str:
    """Horizontal bars on a log scale. Rows: (label, median, low, high, colour, note), in ms."""
    width, left, right, top, bar, gap = 1000, 330, 160, 120, 54, 30
    axis_bottom = top + len(rows) * (bar + gap) - gap + 12
    height = axis_bottom + 50
    lo = math.floor(math.log10(min(r[2] for r in rows)))
    hi = max(math.ceil(math.log10(max(r[3] for r in rows))), lo + 1)

    def x(ms: float) -> float:
        return left + (math.log10(ms) - lo) / (hi - lo) * (width - left - right)

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="Helvetica, Arial, sans-serif">',
        f'<rect width="{width}" height="{height}" fill="white"/>',
        f'<text x="40" y="46" font-size="26" font-weight="bold" fill="#222">{escape(title)}</text>',
        f'<text x="40" y="80" font-size="18" fill="#555">{escape(subtitle)}</text>',
    ]
    for decade in range(lo, hi + 1):
        xv = x(10.0**decade)
        out.append(f'<line x1="{xv:.1f}" y1="{top - 12}" x2="{xv:.1f}" y2="{axis_bottom}" stroke="#ddd"/>')
        out.append(
            f'<text x="{xv:.1f}" y="{axis_bottom + 30}" font-size="16" fill="#555" '
            f'text-anchor="middle">{axis_label(10.0**decade)}</text>'
        )
    for i, (label, median, low, high, colour, note) in enumerate(rows):
        y = top + i * (bar + gap)
        cy = y + bar / 2
        out.append(
            f'<text x="{left - 16}" y="{cy + 7:.1f}" font-size="20" fill="#222" text-anchor="end">{escape(label)}</text>'
        )
        out.append(f'<rect x="{left}" y="{y}" width="{x(median) - left:.1f}" height="{bar}" fill="{colour}"/>')
        out.append(
            f'<line x1="{x(low):.1f}" y1="{cy}" x2="{x(high):.1f}" y2="{cy}" stroke="#222" stroke-width="2"/>'
        )
        for xv in (x(low), x(high)):
            out.append(
                f'<line x1="{xv:.1f}" y1="{cy - 10}" x2="{xv:.1f}" y2="{cy + 10}" stroke="#222" stroke-width="2"/>'
            )
        out.append(
            f'<text x="{max(x(median), x(high)) + 12:.1f}" y="{cy + 7:.1f}" font-size="20" '
            f'fill="#222">{escape(fmt_time(median * 1e6) + note)}</text>'
        )
    out.append("</svg>")
    return "\n".join(out) + "\n"


def chart(report: dict) -> str:
    cal = report["summary"]["calibration"]
    colours = {"python": "#3776AB", "reflected": "#D9822B", "manual": "#E8B07A"}
    rows = []
    for variant in VARIANTS:
        s = cal[variant]
        note = f"  ({s['speedup']:.0f}× faster)" if variant in CPP_MODULES else ""
        medians = [m / 1e6 for m in s["run_medians"]]
        rows.append((LABELS[variant], s["median"] / 1e6, min(medians), max(medians), colours[variant], note))
    runs = report["plan"]["runs"]
    return calibration_svg(
        rows,
        f"One calibration: {CPP_WINDOW_SIZE:,} returns, n_max {CPP_N_MAX}, {CPP_COORDINATE_STEPS} rounds",
        f"Median of {runs} run{'s' if runs != 1 else ''} (whiskers: run-to-run range). "
        "Log scale. Same algorithm, this machine.",
    )


def markdown(report: dict) -> str:
    env, val, plan, s = report["environment"], report["validation"], report["plan"], report["summary"]
    cond = report["conditions"]["before"]
    runs = plan["runs"]
    cal_plan, calls_plan = plan["calibration"], plan["calls"]
    lines = ["# Calibration benchmark", ""]
    if report["quick"]:
        lines += ["> Quick run with reduced samples, for checking the harness. Not for reporting.", ""]
    lines += [
        f"Commit `{env['commit']}`, finished {report['finished_utc']}. {runs} complete run"
        f"{'s' if runs != 1 else ''}, each in fresh processes.",
        "",
        "## Validation",
        "",
        f"- Agreement and equivalence tests: {val['tests']}.",
        "- The timed workloads gave the same results in every variant: counters and outcomes exactly; "
        f"Python and C++ parameters within relative {PARAM_REL_TOL:g} (largest difference observed: "
        f"{val['python_vs_cpp_max_ulps']:g} ulps); reflected and hand-written modules identical.",
        "- This shows the implementations are consistent with each other. It does not validate the model.",
        "",
        "## One calibration",
        "",
        f"One `maybe_update_params()` call on {CPP_WINDOW_SIZE:,} returns with `n_max` {CPP_N_MAX} and "
        f"{CPP_COORDINATE_STEPS} coordinate rounds ({1 + 8 * CPP_COORDINATE_STEPS} likelihood evaluations), "
        "the live runtime's configuration. Each sample starts from a fresh calibrator fed the same "
        f"{CALIBRATION_TICKS:,} synthetic ticks outside the timer. Samples per run: Python "
        f"{cal_plan['python']['samples']}, each C++ variant {cal_plan['cpp']['samples']}, after warm-up. "
        "Each variant runs in its own process, in alternating order between runs.",
        "",
        "| Variant | Median | Run medians | IQR, all samples | Min | Samples |",
        "|---|---|---|---|---|---|",
    ]
    for variant in VARIANTS:
        c = s["calibration"][variant]
        pooled = c["pooled"]
        lines.append(
            f"| {LABELS[variant]} | {fmt_time(c['median'])} | {fmt_range(c['run_medians'], fmt_time)} | "
            f"{fmt_time(pooled['q1'])} – {fmt_time(pooled['q3'])} | {fmt_time(pooled['min'])} | {pooled['n']} |"
        )
    refl, man = s["calibration"]["reflected"], s["calibration"]["manual"]
    lines += [
        "",
        f"Speedup, same algorithm on this machine: **{refl['speedup']:.0f}×** with reflected bindings "
        f"(per run {fmt_range(refl['speedup_runs'], lambda v: f'{v:.1f}×')}) and {man['speedup']:.0f}× "
        "with hand-written bindings.",
        "",
        "![Calibration time](calibration.svg)",
        "",
        "## Full replay",
        "",
        f"{REPLAY_TICKS:,} synthetic hourly ticks into a fresh calibrator, calling `maybe_update_params()` "
        f"after every tick as the live runtime does: {val['replay']['calibrations']} calibrations, "
        f"{val['replay']['changed']} of which changed the parameters.",
        "",
        "| Variant | Median | Run medians | Samples |",
        "|---|---|---|---|",
    ]
    for variant in VARIANTS:
        r = s["replay"][variant]
        lines.append(
            f"| {LABELS[variant]} | {fmt_time(r['median'])} | {fmt_range(r['run_medians'], fmt_time)} | "
            f"{r['pooled']['n']} |"
        )
    lines += [
        "",
        f"Speedup: {s['replay']['reflected']['speedup']:.0f}× (reflected), "
        f"{s['replay']['manual']['speedup']:.0f}× (hand-written).",
        "",
        "## Cheap calls: reflected versus hand-written bindings",
        "",
        "Both modules are loaded into the same process and timed alternately in blocks of four samples "
        "(A, B, B, A), so drift in machine state affects both equally; each block gives one paired difference. "
        f"Each sample times {calls_plan['calls_per_sample']:,} calls in a Python loop; "
        f"{2 * calls_plan['blocks']} samples per module per run after warm-up, with a new process for each "
        "run. Both modules carry identical argument names. Times are per call and include the loop, argument "
        "conversion, dispatch, the C++ body and the return conversion; they are whole-call timings, not an "
        "isolated measurement of the cost of crossing the boundary. An empty loop costs "
        f"{s['calls']['empty_loop']['median']:.1f} ns per iteration.",
        "",
        "| Call | Time per call | Paired difference, reflected − hand-written | Per run | Run-to-run spread "
        "| Assessment |",
        "|---|---|---|---|---|---|",
    ]
    slower, faster = [], []
    for name in CALLS:
        c = s["calls"][name]
        d, base = c["difference"], c["both_modules"]["median"]
        if runs < 2:
            verdict = "needs at least two runs"
        elif c["within_spread"]:
            verdict = "within spread"
        else:
            verdict = f"reflected {'slower' if d > 0 else 'faster'} by {abs(d):.2f} ns ({abs(d) / base:.1%})"
            (slower if d > 0 else faster).append(f"`{name}` (by {abs(d):.2f} ns, {abs(d) / base:.1%})")
        per_run = ", ".join(f"{v:+.2f}" for v in c["difference_runs"])
        spread = f"{c['spread']:.2f} ns" if runs >= 2 else "n/a"
        lines.append(
            f"| `{CALL_SIGNATURES[name]}` | {base:.1f} ns | {d:+.2f} ns ({d / base:+.1%}) | {per_run} ns "
            f"| {spread} | {verdict} |"
        )
    lines.append("")
    if runs < 2:
        lines.append("One run gives no run-to-run spread, so no assessment is made.")
    else:
        if slower:
            text = "The reflected module was slower by more than the run-to-run spread for " + "; ".join(slower) + "."
        else:
            text = (
                "We measured no additional call overhead versus equivalent handwritten bindings: for no call was "
                "the reflected module slower by more than the run-to-run spread of the paired difference (the "
                f"range of the per-run median differences over {runs} runs)."
            )
        if faster:
            text += " Where a difference exceeded the spread, the reflected module was faster: " + "; ".join(faster) + "."
        if slower or faster:
            text += (
                " The two modules run the same C++ through the same nanobind calls, so differences in how the "
                "compiler inlined or laid out each shared library are a more likely cause than the binding "
                "approach; this benchmark does not establish the cause."
            )
        lines.append(text)
    lines += [
        "",
        f"Per run: median (interquartile range; minimum) of the {2 * calls_plan['blocks']} samples per module, "
        f"and median (interquartile range) of the {calls_plan['blocks']} paired differences.",
        "",
        "| Call | Run | Reflected | Hand-written | Paired difference |",
        "|---|---|---|---|---|",
    ]
    for name in CALLS:
        for i, run in enumerate(s["calls"][name]["runs"], start=1):
            cells = [
                f"{p['median']:.1f} ns ({p['q1']:.1f}–{p['q3']:.1f}; min {p['min']:.1f})"
                for p in (run["reflected"], run["manual"])
            ]
            diff = run["difference"]
            lines.append(
                f"| `{name}` | {i} | {cells[0]} | {cells[1]} "
                f"| {diff['median']:+.2f} ns ({diff['q1']:+.2f} to {diff['q3']:+.2f}) |"
            )
    lines += [
        "",
        "## Conditions",
        "",
        f"- CPU: {env['cpu']}, {env['logical_cpus']} logical CPUs; kernel {env['kernel']}.",
        f"- Power: {'mains' if cond['on_mains'] else 'not on mains' if cond['on_mains'] is False else 'unknown'}; "
        f"governor {cond['governor']}, energy preference {cond['energy_performance_preference']}, "
        f"boost {cond['boost']}; load average before {cond['load_average']}, after "
        f"{report['conditions']['after']['load_average']}.",
        f"- Python {env['python']} (configure: {env['python_configure'] or 'n/a'}; PGO "
        f"{'yes' if env['python_pgo'] else 'no'}, LTO {'yes' if env['python_lto'] else 'no'}, "
        f"JIT {'on' if env['python_jit'] else 'off'}). Python timings depend on how CPython was built; "
        "`--reference-only` times the Python reference in any interpreter for comparison.",
        f"- {env.get('compiler')}; nanobind {env.get('nanobind')}.",
        f"- Compile flags (`merton_online_calibrator.cpp`): `{' '.join(env.get('compile_flags', {}).get('merton_online_calibrator.cpp', []))}`.",
        "- Garbage collector paused inside timed regions, as timeit does.",
        "",
        "## Scope",
        "",
        "Speedups compare the same algorithm, in readable Python and in C++, on this machine. There is no "
        "comparison with NumPy, Numba or other ways of speeding up Python.",
        "",
    ]
    return "\n".join(lines)


# --- orchestration -------------------------------------------------------------


def collect(plan: dict, validation: dict) -> dict:
    raw = {"calibration": {v: [] for v in VARIANTS}, "replay": {v: [] for v in VARIANTS}, "calls": [], "order": []}

    def run(label: str, spec: dict, what: str) -> dict:
        start = time.perf_counter()
        result = run_worker(spec)
        check_worker(spec, result, validation)
        progress(f"{label}: {spec['task']:<11} {what:<9} {time.perf_counter() - start:6.1f} s")
        return result

    for i in range(plan["runs"]):
        label = f"run {i + 1}/{plan['runs']}"
        order = VARIANTS if i % 2 == 0 else VARIANTS[::-1]
        raw["order"].append(list(order))
        for task in ("calibration", "calls", "replay"):
            if task == "calls":
                first = next(v for v in order if v in CPP_MODULES)
                raw["calls"].append(run(label, {"task": task, "first": first, **plan["calls"]}, "both"))
                continue
            for variant in order:
                kind = "python" if variant == "python" else "cpp"
                raw[task][variant].append(run(label, {"task": task, "variant": variant, **plan[task][kind]}, variant))
    return raw


def check_worker(spec: dict, result: dict, validation: dict) -> None:
    task, variant = spec["task"], spec.get("variant")
    if task == "calls":
        if result["state"]["reflected"] != result["state"]["manual"]:
            raise SystemExit(f"validation failed: the cheap-call loops left the modules in different states: "
                             f"{result['state']}")
        return
    expected = validation[task]
    if variant == "python":
        check_python_matches(result["state"], expected, f"timed {task}")
    elif result["state"] != expected:
        raise SystemExit(f"validation failed: timed {task} for {variant} gave {result['state']}, expected {expected}")


def reference_only(samples: int, out: Path | None) -> None:
    result = time_calibration("python", samples, warmup=1)
    stats = describe(result["ns"])
    env = environment(cpp=False)
    print(
        f"Python reference, one calibration ({CPP_WINDOW_SIZE:,} returns, n_max {CPP_N_MAX}, "
        f"{CPP_COORDINATE_STEPS} rounds) on Python {env['python']}: median {fmt_time(stats['median'])}, "
        f"IQR {fmt_time(stats['q1'])} – {fmt_time(stats['q3'])}, min {fmt_time(stats['min'])}, n={stats['n']}. "
        f"Configure: {env['python_configure'] or 'n/a'}"
    )
    if out:
        out.mkdir(parents=True, exist_ok=True)
        (out / "reference.json").write_text(json.dumps({"environment": env, "ns": result["ns"], "stats": stats}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, help="output directory (default cpp/build/bench)")
    parser.add_argument("--tests-dir", type=Path, default=REPO / "cpp" / "tests")
    parser.add_argument("--quick", action="store_true", help="reduced samples, to check the harness")
    parser.add_argument("--runs", type=int, help="override the number of complete runs")
    parser.add_argument("--reference-only", action="store_true", help="time only the Python reference")
    parser.add_argument("--samples", type=int, default=15, help="samples for --reference-only")
    parser.add_argument(
        "--from-json", type=Path, metavar="BENCH_JSON",
        help="rewrite bench.md and calibration.svg from an earlier bench.json without timing anything",
    )
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.worker:
        print(json.dumps(worker(json.loads(args.worker))))
        return
    if args.reference_only:
        reference_only(args.samples, args.out)
        return
    if args.from_json:
        report = json.loads(args.from_json.read_text())
        report["summary"] = summarize(report["raw"], report["plan"]["runs"])
        write_report(report, args.out or args.from_json.parent)
        return

    plan = json.loads(json.dumps(QUICK_PLAN if args.quick else FULL_PLAN))
    if args.runs:
        plan["runs"] = args.runs
    before = conditions()
    if before["on_mains"] is False:
        progress("warning: not on mains power; timings may be throttled")
    validation = validate(args.tests_dir)
    raw = collect(plan, validation)
    report = {
        "quick": args.quick,
        "finished_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "plan": plan,
        "workload": {"config": CONFIG, "seed": SEED, "calibration_ticks": CALIBRATION_TICKS,
                     "replay_ticks": REPLAY_TICKS, "fair_value_args": FAIR_VALUE_ARGS},
        "validation": validation,
        "environment": environment(cpp=True),
        "conditions": {"before": before, "after": conditions()},
        "summary": summarize(raw, plan["runs"]),
        "raw": raw,
    }
    write_report(report, args.out or REPO / "cpp" / "build" / "bench")


def write_report(report: dict, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "bench.json").write_text(json.dumps(report, indent=2) + "\n")
    (out / "calibration.svg").write_text(chart(report))
    (out / "bench.md").write_text(markdown(report))
    progress(f"wrote {out}/bench.md, bench.json and calibration.svg")


if __name__ == "__main__":
    main()
