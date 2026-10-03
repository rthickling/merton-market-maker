#pragma once

#include "reflection_accessors.hpp"
#include "reflection_policy.hpp"
#include <pybind11/pybind11.h>
#include <string>
#include <string_view>
#include <utility>

namespace py = pybind11;

template <typename T>
void stringify_members(const T& self, std::string& s) {
    static constexpr auto members = std::define_static_array(
        std::meta::nonstatic_data_members_of(^^T, std::meta::access_context::current()));

    bool first = true;
    template for (constexpr auto m : members) {
        if constexpr (std::meta::has_identifier(m)) {
            if (!first) {
                s += ", ";
            }
            s += std::meta::identifier_of(m).data();
            s += "=" + py::repr(py::cast(self.[:m:])).template cast<std::string>();
            first = false;
        }
    }
}

template <typename T>
void bind_reflected_struct(py::class_<T>& cl) {
    static constexpr auto members = std::define_static_array(
        std::meta::nonstatic_data_members_of(^^T, std::meta::access_context::current()));

    template for (constexpr auto m : members) {
        if constexpr (std::meta::has_identifier(m)) {
            constexpr const char* py_name = reflection_policy::python_name(m);
            cl.def_property(py_name, &Accessor<T, m>::get, &Accessor<T, m>::set);
            if constexpr (reflection_policy::needs_python_alias(m)) {
                // The C++ spelling stays reachable through getattr/setattr.
                constexpr const char* cpp_name = std::meta::identifier_of(m).data();
                cl.def_property(cpp_name, &Accessor<T, m>::get, &Accessor<T, m>::set);
            }
        }
    }

    cl.def("__repr__", [](const T& self) {
        std::string s = std::string(std::meta::identifier_of(^^T).data()) + "(";
        stringify_members(self, s);
        return s + ")";
    });
}

// Named helper rather than a lambda inside `template for` (see Accessor).
template <std::meta::info M, typename T, std::size_t... I>
void def_reflected_method(py::class_<T>& cl, std::index_sequence<I...>) {
    constexpr const char* name = reflection_policy::python_name(M);
    if constexpr (std::meta::is_static_member(M)) {
        cl.def_static(name, &[:M:], py::arg(reflection_policy::parameter_name<M, I>)...);
    } else {
        cl.def(name, &[:M:], py::arg(reflection_policy::parameter_name<M, I>)...);
    }
}

template <typename T>
void bind_reflected_member_functions(py::class_<T>& cl) {
    static constexpr auto members = std::define_static_array(
        std::meta::members_of(^^T, std::meta::access_context::current()));

    template for (constexpr auto m : members) {
        if constexpr (reflection_policy::is_bindable_method(m)) {
            def_reflected_method<m>(
                cl, std::make_index_sequence<reflection_policy::named_parameter_count<m>>{});
        }
    }
}
