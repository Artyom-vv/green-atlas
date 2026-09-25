#pragma once
#include <cstddef>
#include <string>

namespace ga::bridge {

std::string jsonEscape(const std::string& value);
std::string sha256File(const std::string& path);
std::size_t fileSize(const std::string& path);
std::string parentDirectory(const std::string& path);
bool writeAtomicText(const std::string& path, const std::string& payload);

}  // namespace ga::bridge
