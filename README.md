![](/assets/images/merton-bot.png)

# Merton Market Maker: C++26 Reflection + Python Bindings
**Solving the Speed-to-Market vs Speed-to-Book Tension in Algorithmic Trading.**

---

⚠️ **The "No Alpha" Disclaimer**

This project is an engineering demonstration. The included Merton Jump Diffusion (MJD) algorithm is a standard, textbook model used to provide a real-world context.
* This is not a money-printing bot.
* It exposes no novel alpha.
* The value is in the infrastructure, not the strategy.

---
## A Practical Hybrid Workflow
The algorithmic trading world is often split into two camps:

1. **Speed-to-Market (Python)**: Rapid iteration, research, and backtesting.
2. **Speed-to-Book (C++)**: Low-latency execution and high-performance pricing math.

While it has always been possible to bridge these worlds using `pybind11`, `nanobind`, or CFFI, the manual boilerplate tax is usually too cumbersome for day-to-day use. Quants writing pricing models should not have to manually update a binding layer just to see the result in their Python strategy. A quant-dev with both C++ and Python should not have to be brought in for every binding change.

C++26 Reflection (P2996) changes this. It is *compiler-supported* compile-time programming. We can automate the bridge entirely and reliably. This project demonstrates how reflection makes the hybrid Python/C++ model practical for daily development and provides an end-to-end working example.

## Through the Mirror into a New C++ World
Instead of writing a manual binding stanza for every C++ function, this project uses a generic reflection loop. When you add or change a public method in your C++ math engine, the Python bindings update automatically at compile time.

### Shared reflection idea
```c++
template <typename T>
void bind_reflected_member_functions(/* backend class wrapper */& cl) {
    static constexpr auto members = std::define_static_array(
        std::meta::members_of(^^T, std::meta::access_context::current()));

    template for (constexpr auto m : members) {
        if constexpr (std::meta::is_public(m) && std::meta::is_function(m) &&
                      !std::meta::is_constructor(m)) {
            cl.def(std::meta::identifier_of(m).data(), &[:m:]);
        }
    }
}
```

### `pybind11` example
```c++
PYBIND11_MODULE(merton_online_calibrator, m) {
    py::class_<merton::OnlineMertonCalibrator> cl(m, "OnlineMertonCalibrator");
    cl.def(py::init<merton::MertonParams, merton::CalibratorConfig>(),
           py::arg("initial"), py::arg("config") = merton::CalibratorConfig{});
    bind_reflected_member_functions(cl);
}
```

### `nanobind` example
```c++
NB_MODULE(merton_online_calibrator, m) {
    nb::class_<merton::OnlineMertonCalibrator> cl(m, "OnlineMertonCalibrator");
    cl.def(nb::init<merton::MertonParams, merton::CalibratorConfig>(),
           "initial"_a, "config"_a = merton::CalibratorConfig{});
    bind_reflected_member_functions(cl);
}
```

Now, any public method added to the C++ engine is available in the Python execution environment without extra per-method binding boilerplate.

Note the new C++ syntax: the reflection operator `^^T` converts a type into a reflected meta-object, and the splicer `[:m:]` converts reflected members back into run-time expressions.

---
## Why Merton Jump Diffusion?
I chose the MJD model because its math is heavy. It requires infinite-series-style mixture summations that would be much slower in pure Python. It illustrates the "Speed-to-Book" need for compiled math, while the high-level trading logic still benefits from Python's "Speed-to-Market."

## Technical Stack
C++26 reflection is built with either released GCC 16.2 and libstdc++ or the pinned Bloomberg Clang P2996 fork and libc++. Both toolchains support the nanobind and pybind11 backends and run entirely in pinned `linux/amd64` Docker environments.

### Build and test from a fresh clone
Prerequisites: [Docker](https://www.docker.com/) and [`just`](https://github.com/casey/just).

```bash
cd cpp
just test
```

This builds and tests the default GCC/nanobind combination. Run `just test-matrix` for GCC and Clang with both binding backends. See [BUILD](cpp/BUILD.md) for compiler selection, output paths, clean builds, and explicit deployment copying.

## References & Further Reading
* Blog Post: [Stop Choosing: Get C++ Performance in Python Algos with C++26](https://profitview.net/blog/cpp26-reflection-python-algo-trading)
* [P2996 - Reflection for C++26](https://wg21.link/p2996)
* [Callum Piper](https://www.linkedin.com/in/callum-piper-3691373/)'s [talk](https://youtu.be/SJ0NFLpR9vE) from ACCU 2025.
* Reference deployment: [ProfitView](https://profitview.net/).
* See [THEORY](cpp/THEORY.md)
* [nanobind: tiny and efficient C++/Python bindings](https://github.com/wjakob/nanobind)

If you need more information or are interested in building a system using C++ reflection, feel free to contact me:
* [LinkedIn](https://www.linkedin.com/in/rthickling/)
* richard@profitview.net
* richard.t.hickling@gmail.com
