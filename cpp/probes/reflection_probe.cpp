// Minimal probe of reflection constructs used by include/reflection_bind_*.hpp,
// include/reflection_accessors.hpp and include/reflection_policy.hpp.
// Compile with Clang P2996 or GCC 16+.
#include <meta>
#include <cstdio>
#include <string_view>

#include "../include/reflection_policy.hpp"

struct ProbeParams {
    double sigma = 0.44;
    double lambda = 20.0;
};

// Mirrors Accessor<T,M> in reflection_accessors.hpp
template <typename T, std::meta::info M>
struct Accessor {
    using FieldType = [: std::meta::type_of(M) :];
    static FieldType get(const T& self) { return self.[:M:]; }
    static void set(T& self, FieldType v) { self.[:M:] = v; }
};

struct ProbeCalibrator {
    double value = 0.0;
    double scale() const { return value * 2.0; }
    double shifted(double offset, double lambda = 0.5) const { return value + offset + lambda; }
    static int version() { return 1; }
};

// parameters_of + parameter names + defaults + keyword aliases (reflection_policy.hpp)
constexpr auto probe_shifted = ^^ProbeCalibrator::shifted;
static_assert(reflection_policy::named_parameter_count<probe_shifted> == 2);
static_assert(std::string_view(reflection_policy::parameter_name<probe_shifted, 0>) == "offset");
static_assert(std::string_view(reflection_policy::parameter_name<probe_shifted, 1>) == "lambda_");
static_assert(!std::meta::has_default_argument(reflection_policy::parameters<probe_shifted>[0]));
static_assert(std::meta::has_default_argument(reflection_policy::parameters<probe_shifted>[1]));
static_assert(std::string_view(reflection_policy::python_name(^^ProbeParams::lambda)) == "lambda_");
static_assert(std::string_view(reflection_policy::python_name(^^ProbeParams::sigma)) == "sigma");

int main() {
    // define_static_array + nonstatic_data_members_of + template for + splices
    static constexpr auto members = std::define_static_array(
        std::meta::nonstatic_data_members_of(^^ProbeParams, std::meta::access_context::current()));

    ProbeParams p{};
    double sum = 0.0;
    template for (constexpr auto m : members) {
        if constexpr (std::meta::has_identifier(m)) {
            sum += Accessor<ProbeParams, m>::get(p);
            Accessor<ProbeParams, m>::set(p, Accessor<ProbeParams, m>::get(p) + 1.0);
            std::printf("field %s\n", std::meta::identifier_of(m).data());
        }
    }

    // members_of + method filters + function splice (bind_reflected_member_functions pattern)
    static constexpr auto fns = std::define_static_array(
        std::meta::members_of(^^ProbeCalibrator, std::meta::access_context::current()));

    ProbeCalibrator cal{};
    cal.value = 3.0;
    template for (constexpr auto m : fns) {
        if constexpr (reflection_policy::is_bindable_method(m)) {
            constexpr auto name = std::meta::identifier_of(m);
            if constexpr (std::meta::is_static_member(m)) {
                std::printf("static method %s -> %d\n", name.data(), ([:m:])());
            } else if constexpr (std::meta::identifier_of(m) == std::string_view{"scale"}) {
                std::printf("method %s -> %f\n", name.data(), (cal.[:m:])());
            }
        }
    }

    // A call through a splice applies the C++ default argument.
    const double shifted = cal.[:probe_shifted:](1.0);
    std::printf("parameters %s, %s; shifted(1.0) -> %f\n",
                reflection_policy::parameter_name<probe_shifted, 0>,
                reflection_policy::parameter_name<probe_shifted, 1>, shifted);

    std::printf("sum0=%f sigma=%f lambda=%f type=%s\n",
                sum, p.sigma, p.lambda, std::meta::identifier_of(^^ProbeParams).data());
    return (sum > 0.0 && p.sigma == 1.44 && p.lambda == 21.0 && shifted == 4.5) ? 0 : 1;
}
