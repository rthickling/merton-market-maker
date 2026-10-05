// Built only into libmerton_core_shared.so. cppyy lays the class out from the header using its own
// (older) libstdc++ headers; scripts/merton_cppyy.py refuses to load if the sizes disagree.
#include "merton_online_calibrator.hpp"

extern "C" MERTON_API std::size_t merton_calibrator_size() {
    return sizeof(merton::OnlineMertonCalibrator);
}
