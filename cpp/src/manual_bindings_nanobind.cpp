// Hand-written nanobind bindings, kept equivalent to the reflected module in
// python_module_entry_nanobind.cpp so that the two can be compared: the same
// copying property accessors, `lambda` exposed as `lambda_` plus its C++
// spelling, the same keyword names, no default arguments on methods, and the
// same constructor and __repr__. tests/test_manual_bindings.py checks that the
// two modules stay in step.

#include "merton_online_calibrator.hpp"

#include <nanobind/nanobind.h>
#include <nanobind/stl/string.h>

#include <string>

namespace nb = nanobind;
using namespace nb::literals;

namespace {

// Same shape as Accessor<T, M> in reflection_accessors.hpp. Register these as
// function pointers, not lambdas: nanobind calls the two differently, and the
// reflected module passes function pointers.
template <auto Member>
struct Field;

template <typename T, typename F, F T::*Member>
struct Field<Member> {
    static F get(const T& self) { return self.*Member; }
    static void set(T& self, F v) { self.*Member = v; }
};

template <typename F>
std::string field_repr(const F& value) {
    return nb::cast<std::string>(nb::repr(nb::cast(value)));
}

}  // namespace

NB_MODULE(merton_manual_bindings, m) {
    using merton::CalibratorConfig;
    using merton::MertonParams;
    using merton::OnlineMertonCalibrator;

    m.doc() = "Online Merton jump-diffusion calibrator (hand-written bindings, nanobind)";

    nb::class_<MertonParams> p(m, "MertonParams");
    p.def(nb::init<>());
    p.def_prop_rw("sigma", &Field<&MertonParams::sigma>::get, &Field<&MertonParams::sigma>::set);
    p.def_prop_rw("lambda_", &Field<&MertonParams::lambda>::get, &Field<&MertonParams::lambda>::set);
    p.def_prop_rw("lambda", &Field<&MertonParams::lambda>::get, &Field<&MertonParams::lambda>::set);
    p.def_prop_rw("mu_j", &Field<&MertonParams::mu_j>::get, &Field<&MertonParams::mu_j>::set);
    p.def_prop_rw("delta_j", &Field<&MertonParams::delta_j>::get, &Field<&MertonParams::delta_j>::set);
    p.def("__repr__", [](const MertonParams& self) {
        return "MertonParams(sigma=" + field_repr(self.sigma) + ", lambda=" + field_repr(self.lambda) +
               ", mu_j=" + field_repr(self.mu_j) + ", delta_j=" + field_repr(self.delta_j) + ")";
    });

    nb::class_<CalibratorConfig> cfg(m, "CalibratorConfig");
    cfg.def(nb::init<>());
    cfg.def_prop_rw("window_size", &Field<&CalibratorConfig::window_size>::get,
                    &Field<&CalibratorConfig::window_size>::set);
    cfg.def_prop_rw("min_points_for_update", &Field<&CalibratorConfig::min_points_for_update>::get,
                    &Field<&CalibratorConfig::min_points_for_update>::set);
    cfg.def_prop_rw("n_max", &Field<&CalibratorConfig::n_max>::get, &Field<&CalibratorConfig::n_max>::set);
    cfg.def_prop_rw("update_every_n_returns", &Field<&CalibratorConfig::update_every_n_returns>::get,
                    &Field<&CalibratorConfig::update_every_n_returns>::set);
    cfg.def_prop_rw("coordinate_steps", &Field<&CalibratorConfig::coordinate_steps>::get,
                    &Field<&CalibratorConfig::coordinate_steps>::set);
    cfg.def_prop_rw("improvement_tol", &Field<&CalibratorConfig::improvement_tol>::get,
                    &Field<&CalibratorConfig::improvement_tol>::set);
    cfg.def("__repr__", [](const CalibratorConfig& self) {
        return "CalibratorConfig(window_size=" + field_repr(self.window_size) +
               ", min_points_for_update=" + field_repr(self.min_points_for_update) +
               ", n_max=" + field_repr(self.n_max) +
               ", update_every_n_returns=" + field_repr(self.update_every_n_returns) +
               ", coordinate_steps=" + field_repr(self.coordinate_steps) +
               ", improvement_tol=" + field_repr(self.improvement_tol) + ")";
    });

    nb::class_<OnlineMertonCalibrator> cl(m, "OnlineMertonCalibrator");
    cl.def(nb::init<MertonParams, CalibratorConfig>(), "initial"_a, "config"_a = CalibratorConfig{});
    cl.def("update_tick", &OnlineMertonCalibrator::update_tick, "price"_a, "epoch_us"_a);
    cl.def("maybe_update_params", &OnlineMertonCalibrator::maybe_update_params);
    cl.def("fair_value", &OnlineMertonCalibrator::fair_value, "s0"_a, "q_annual"_a, "t_years"_a, "r"_a);
    cl.def("fair_value_quantlib", &OnlineMertonCalibrator::fair_value_quantlib, "s0"_a, "q_annual"_a,
           "t_years"_a, "r"_a);
    cl.def("params", &OnlineMertonCalibrator::params);
    cl.def("sample_count", &OnlineMertonCalibrator::sample_count);
    cl.def("calibration_count", &OnlineMertonCalibrator::calibration_count);
}
