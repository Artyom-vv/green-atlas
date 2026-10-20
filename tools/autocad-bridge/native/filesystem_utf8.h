#pragma once

#include <filesystem>
#include <string>

namespace ga::bridge {

// Внешние протоколы передают UTF-8; Windows filesystem использует UTF-16.
inline std::filesystem::path nativePath(const std::string& value) {
#ifdef _WIN32
  return std::filesystem::u8path(value);
#else
  return std::filesystem::path(value);
#endif
}

inline std::string utf8Path(const std::filesystem::path& value) {
#ifdef _WIN32
  const auto encoded = value.u8string();
  return std::string(reinterpret_cast<const char*>(encoded.data()), encoded.size());
#else
  return value.string();
#endif
}

inline std::string genericUtf8Path(const std::filesystem::path& value) {
#ifdef _WIN32
  const auto encoded = value.generic_u8string();
  return std::string(reinterpret_cast<const char*>(encoded.data()), encoded.size());
#else
  return value.generic_string();
#endif
}

}  // namespace ga::bridge
