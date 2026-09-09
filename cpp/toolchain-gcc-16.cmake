# Toolchain for building reflection-based Python modules with released GCC 16
# inside the repo's GCC Docker image (libstdc++).

set(CMAKE_C_COMPILER "/opt/gcc-16/bin/gcc" CACHE FILEPATH "GCC 16")
set(CMAKE_CXX_COMPILER "/opt/gcc-16/bin/g++" CACHE FILEPATH "GCC 16")
set(REFLECT_PY_STRAT_GCC_PREFIX "/opt/gcc-16" CACHE PATH "GCC 16 install prefix")
set(REFLECT_PY_STRAT_QL_INSTALL_DIR "/opt/ql_install" CACHE PATH "QuantLib install prefix")

# Do not mix with libc++ artifacts. Use the GCC/libstdc++ toolchain only.
set(REFLECT_PY_STRAT_CXX_FLAGS
  -O3 -fPIC
  -std=c++26 -freflection
  -fconstexpr-ops-limit=67108864
  -fvisibility=hidden
  -DQL_USE_STD_SHARED_PTR
  -ffunction-sections -fdata-sections
)

set(REFLECT_PY_STRAT_LINK_FLAGS
  -Wl,--gc-sections -Wl,--as-needed
  -Wl,-rpath,${REFLECT_PY_STRAT_GCC_PREFIX}/lib64
)

set(REFLECT_PY_STRAT_INCLUDE_DIRS
  "${REFLECT_PY_STRAT_QL_INSTALL_DIR}/include"
)
