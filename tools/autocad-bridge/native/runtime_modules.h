#pragma once

namespace ga::bridge {

#ifdef _WIN32
inline constexpr const wchar_t* kBrepModule = L"acbr26.dbx";
#else
inline constexpr const wchar_t* kBrepModule = L"AcBr.dbx";
#endif
inline constexpr const wchar_t* kGeometryModule = L"AcGeomentObj.dbx";

}
