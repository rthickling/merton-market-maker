# C++ Core For Real-Time Adaptive Merton Updates

This folder contains the heavy runtime path in C++ and a reflection-based Python extension module with interchangeable `pybind11` and `nanobind` backends.

## What Is Implemented

- `OnlineMertonCalibrator` in `include/merton_online_calibrator.hpp` / `src/merton_online_calibrator.cpp`
- Python binding entry points `src/python_module_entry_pybind11.cpp` and `src/python_module_entry_nanobind.cpp`
- Reflection-based backend adapters in `include/reflection_bind_pybind11.hpp` and `include/reflection_bind_nanobind.hpp`
- Shared reflected field accessors in `include/reflection_accessors.hpp`
- Heavy computation path:
  - rolling return ingestion from ticks
  - Merton jump-diffusion negative log-likelihood over a rolling window
  - periodic online parameter improvement (`sigma`, `lambda`, `mu_j`, `delta_j`)
- No-jump conditional mean, `no_jump_conditional_mean` (see section 4); `fair_value` is its former name, kept as an alias:
  - `S_0 * exp((r - q - lambda * k) * T)` with `k = exp(mu_j + 0.5 * delta_j^2) - 1`
  - This is `E[S_T | no jumps in (0, T]]`. Under the compensated Merton SDE,
    the unconditional expectation is `S_0 * exp((r - q) * T)`.

The heavy likelihood path uses an internal standard-normal density implementation to keep the core portable in minimal build environments.

## Binding Backends

Backend selection is controlled by the CMake option `MERTON_PYTHON_BINDING`.

### `pybind11`

```c++
PYBIND11_MODULE(merton_online_calibrator, m) {
    py::class_<merton::OnlineMertonCalibrator> cl(m, "OnlineMertonCalibrator");
    cl.def(py::init<merton::MertonParams, merton::CalibratorConfig>(),
           py::arg("initial"), py::arg("config") = merton::CalibratorConfig{});
    bind_reflected_member_functions(cl);
}
```

### `nanobind`

```c++
NB_MODULE(merton_online_calibrator, m) {
    nb::class_<merton::OnlineMertonCalibrator> cl(m, "OnlineMertonCalibrator");
    cl.def(nb::init<merton::MertonParams, merton::CalibratorConfig>(),
           "initial"_a, "config"_a = merton::CalibratorConfig{});
    bind_reflected_member_functions(cl);
}
```

Both backends expose the same Python-facing API and the same reflected public members and methods.

## Binding Surface

Both binding backends emit the plain extension filename `merton_online_calibrator.so`. Classes and constructors are registered by hand. Public data members and public instance methods of those classes are bound by the reflection headers, including parameter names. C++ default arguments are not exposed as Python defaults.

Python signatures, as actually exposed:

- `OnlineMertonCalibrator(initial, config=CalibratorConfig())`
- `update_tick(price, epoch_us)`
- `maybe_update_params()`
- `no_jump_conditional_mean(s0, q_annual, t_years, r)` — `r` is required; the C++ default `r = 0.0` is not a Python default
- `no_jump_conditional_mean_quantlib(s0, q_annual, t_years, r)` — same
- `fair_value(s0, q_annual, t_years, r)` and `fair_value_quantlib(s0, q_annual, t_years, r)` — the former names: inline aliases in the header with the same arguments and results, bound by reflection like any other public method
- `params()` — returns a copy of the current `MertonParams` (a snapshot; changing the copy does not change the calibrator)
- `sample_count()`
- `calibration_count()`

`MertonParams` fields are `sigma`, `lambda_`, `mu_j` and `delta_j`. `lambda_` is the Python name for the C++ member `lambda`. A second property, `lambda`, is an alias reachable with `getattr` and `setattr`, because `lambda` is a Python keyword. Getters return copies of the scalar values; setters write the scalar back.

Each call holds the GIL. The calibrator object is mutable and is not safe to share across threads without external synchronisation.

Python usage pattern:

1. On each tick: `update_tick(price, ts_us)`
2. Every N returns: `maybe_update_params()`
3. Optionally, as a diagnostic: `no_jump_conditional_mean(mid, q_annual, T_years, r)` with `r` passed explicitly (the demos' paper quotes do not use it; they are centred on the market midpoint)
4. Periodically pull `params()` for logging / persistence

QuantLib integration (for illustration purposes):

- `no_jump_conditional_mean_quantlib(...)` (formerly `fair_value_quantlib`) builds flat `r`/`q` curves with QuantLib
- computes forward from discount factors (`S0 * Dq / Dr`)
- applies the same `-λκ` adjustment as `no_jump_conditional_mean`, after rounding the horizon to whole days, at least one: an 8-hour horizon is evaluated over one day

## How `OnlineMertonCalibrator` Works

### 1) Tick ingestion (`update_tick`)

For each incoming `(price, epoch_us)`:

- validates price and timestamp ordering
- computes `log(price / last_price)`
- appends return and `dt_us` to rolling buffers
- trims buffers to `window_size`
- increments the update counter

This keeps per-tick work light while maintaining a fresh rolling sample.

### 2) Gated online recalibration (`maybe_update_params`)

Recalibration only runs when both are true:

- `sample_count >= min_points_for_update`
- enough new returns since last update (`update_every_n_returns`)

When triggered, it:

- estimates `dt` (years) from the median of observed `dt_us`
- evaluates current negative log-likelihood (NLL)
- runs a small coordinate-search around current parameters:
  - try plus/minus perturbations for each parameter
  - accept improvements greater than `improvement_tol`
  - shrink step sizes if no improvement
- clamps results to configured/safe bounds

This is a local, incremental update strategy designed for high-frequency runtime use.

### 3) Likelihood objective

The calibrator optimizes Merton jump-diffusion NLL over the rolling returns. The log-return PDF is:

$$
f(x \mid \sigma, \lambda, \mu_J, \delta_J, \Delta t)
  = \sum_{n=0}^{n_{\max}-1}
    \frac{e^{-\lambda \Delta t}(\lambda \Delta t)^n}{n!}
    \cdot \frac{\phi\bigl((x - \mu_n)/\sigma_n\bigr)}{\sigma_n}
$$

where $\phi$ is the standard normal density, $\kappa = e^{\mu_J + \delta_J^2/2} - 1$, and

$$
\mu_n = \left(-\lambda\kappa - \frac{\sigma^2}{2}\right)\Delta t + n\mu_J,
\quad
\sigma_n^2 = \sigma^2 \Delta t + n \delta_J^2
$$

The objective is:

$$
\text{NLL}(\theta) = -\sum_i \log f(x_i \mid \theta, \widehat{\Delta t})
$$

with $\widehat{\Delta t}$ taken as the median of observed inter-tick intervals (in years).

### 4) No-jump conditional mean from current online parameters

At any point, `no_jump_conditional_mean` (formerly `fair_value`, which remains as an alias) uses the current online parameters and returns

$$
S_0 \, \exp\bigl((r - q - \lambda\kappa)\,T\bigr),
\quad
\kappa = e^{\mu_J + \delta_J^2/2} - 1.
$$

The documented process is the compensated Merton SDE,
`dS/S = (r - q - λκ)dt + σ dW + (J-1)dN`. Under that SDE the unconditional
mean is `S_0 exp((r - q)T)`, because the jump contribution cancels the
compensator in expectation. The implemented formula keeps `-λκ` in the drift,
so it equals the mean along the no-jump path
`E[S_T | N_T = 0]` (diffusion only). The difference is about 0.56 bp over an
8-hour horizon at the default parameters, and can reach a few percent at the
parameter clamps. The method's name says what the formula computes; the
formula itself is unchanged.

It is not a fair value to quote around. Its ratio to the price it is given is
`exp((r - q - λκ)T)`, which depends only on the parameters, the carry and the
horizon, not on the market, so its difference from the midpoint is not a
measure of mispricing and carries no trading signal. The demos print it as a
labelled diagnostic. In the demos, `T` is the full length of one funding
interval (8h, 4h or 1h). It does not count down to the next payment, and
funding payments do not reset the price: funding enters only as the carry `q`.

`no_jump_conditional_mean_quantlib` (formerly `fair_value_quantlib`) aims at
the same quantity via flat QuantLib curves, but rounds the horizon to a whole
number of days (minimum one), which creates a small systematic gap on sub-day
horizons: an 8-hour horizon is evaluated over one day.

So the runtime loop is:

- `update_tick` (every tick)
- `maybe_update_params` (periodically): the substantial work, the likelihood over the rolling window
- `no_jump_conditional_mean` (optional diagnostic): a single exponential

The default local demonstration (`just demo` / `scripts/run_binance_demo.py`) runs this loop on Binance public bookTicker data. It prints illustrative paper quotes centred on the market midpoint, `mid ± max(mid × minimum half-spread, half the market spread)`, alongside the calibrated parameters and the diagnostic, which are analytics and do not move the quotes. It places no orders.

`profitview_merton_signal.py` is an optional legacy ProfitView strategy wrapper for venue-specific live deployment. It quotes around the midpoint in the same way. Its `merton_theo` topic keeps the earlier `theo` and `diff_bps` fields, as aliases of `no_jump_conditional_mean` and `no_jump_mean_vs_mid_bps`. The supported paths are the Binance demo (`just demo`) or offline `just replay`.

The agreement tests show that the C++, pure-Python, Numba and cppyy paths compute the same numbers. They do not validate the model, the diagnostic or the quotes.

### Runtime pseudocode

```text
init calibrator(params0, config)

for each market tick (bid, ask, ts_us):
    mid = (bid + ask) / 2
    accepted = calibrator.update_tick(mid, ts_us)

    if accepted:
        updated = calibrator.maybe_update_params()
        if updated:
            params = calibrator.params()
            # optional: log/store params (analytics)

    # Paper quotes: centred on the midpoint; the fitted parameters do not move them.
    half_spread = max(mid * min_half_spread_bps / 10_000, (ask - bid) / 2)
    quote_bid, quote_ask = mid - half_spread, mid + half_spread

    # Optional diagnostic over one full funding interval (a fixed length, not a countdown).
    q_annual = funding_to_annual(funding_rate, interval_hours)  # Binance: live 8h/4h/1h
    T_years = interval_hours / (365.25 * 24)
    no_jump_mean = calibrator.no_jump_conditional_mean(mid, q_annual, T_years, 0.0)  # r is required in Python
```

## References

- Merton, R.C. (1976). Option pricing when underlying stock returns are discontinuous. *Journal of Financial Economics*, 3(1-2), 125-144.
- QuantLib [Merton76Process](https://rkapl123.github.io/QLAnnotatedSource/df/d83/class_quant_lib_1_1_merton76_process.html), [JumpDiffusionEngine](https://rkapl123.github.io/QLAnnotatedSource/dd/d6b/class_quant_lib_1_1_jump_diffusion_engine.html)
- QuantLib test suite: [jumpdiffusion.cpp](https://github.com/lballabio/QuantLib/blob/master/test-suite/jumpdiffusion.cpp)
- [Merton-Jump-Diffusion-CPP](https://github.com/QGoGithub/Merton-Jump-Diffusion-CPP) (standalone MIT implementation)
- [QuantStart: Jump-diffusion models for European options in C++](https://www.quantstart.com/articles/Jump-Diffusion-Models-for-European-Options-Pricing-in-C/)
- [nanobind](https://github.com/wjakob/nanobind) and [pybind11](https://github.com/pybind/pybind11)
