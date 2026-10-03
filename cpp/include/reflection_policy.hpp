#pragma once

// Backend-neutral binding decisions shared by reflection_bind_nanobind.hpp and
// reflection_bind_pybind11.hpp: which members become Python methods, how
// reflected names are spelled in Python, and which parameter names are exposed.

#include <array>
#include <cstddef>
#include <meta>
#include <span>
#include <string>
#include <string_view>

namespace reflection_policy {

inline constexpr std::array<std::string_view, 35> python_keywords = {
    "False", "None",   "True",     "and",      "as",       "assert", "async",
    "await", "break",  "class",    "continue", "def",      "del",    "elif",
    "else",  "except", "finally",  "for",      "from",     "global", "if",
    "import", "in",    "is",       "lambda",   "nonlocal", "not",    "or",
    "pass",  "raise",  "return",   "try",      "while",    "with",   "yield",
};

consteval bool is_python_keyword(std::string_view name) {
    for (std::string_view keyword : python_keywords) {
        if (keyword == name) {
            return true;
        }
    }
    return false;
}

// Public, named, ordinary member functions. Constructors stay explicit in the
// module entry files; operators and conversions are not mapped.
consteval bool is_bindable_method(std::meta::info m) {
    return std::meta::is_public(m) &&
           std::meta::is_function(m) &&
           !std::meta::is_constructor(m) &&
           !std::meta::is_destructor(m) &&
           !std::meta::is_conversion_function(m) &&
           !std::meta::is_operator_function(m) &&
           std::meta::has_identifier(m);
}

consteval bool needs_python_alias(std::meta::info r) {
    return is_python_keyword(std::meta::identifier_of(r));
}

// Python spelling of a reflected identifier: a name that is a Python keyword
// gets a trailing underscore (PEP 8), e.g. `lambda` -> `lambda_`.
consteval const char* python_name(std::meta::info r) {
    std::string_view id = std::meta::identifier_of(r);
    if (!is_python_keyword(id)) {
        return id.data();
    }
    std::string aliased(id);
    aliased += '_';
    return std::define_static_string(aliased);
}

template <std::meta::info F>
inline constexpr std::span<const std::meta::info> parameters =
    std::define_static_array(std::meta::parameters_of(F));

// Parameter names are exposed only when every parameter has one; otherwise
// the function is bound positionally.
template <std::meta::info F>
consteval bool all_parameters_named() {
    for (std::meta::info p : parameters<F>) {
        if (!std::meta::has_identifier(p)) {
            return false;
        }
    }
    return true;
}

template <std::meta::info F>
inline constexpr std::size_t named_parameter_count =
    all_parameters_named<F>() ? parameters<F>.size() : 0;

template <std::meta::info F, std::size_t I>
inline constexpr const char* parameter_name = python_name(parameters<F>[I]);

}  // namespace reflection_policy
