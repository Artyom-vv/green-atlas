#pragma once
#include <string>
#include <cstddef>
#include <vector>

namespace ga::platform {
unsigned long processId();
std::string liveQueueDirectory();
std::string mcpQueueDirectory(const std::string& version);
std::string randomToken();
std::string transferDirectory();
// Чужое владение и существующие разрешения не исправляются скрыто.
bool ensurePrivateDirectory(const std::string& path);
bool directPath(const std::string& path);
bool fileExists(const std::string& path);
std::string readBoundedFile(const std::string& path,std::size_t maximumBytes);
bool renameFile(const std::string& from, const std::string& to);
std::vector<std::string> directoryNames(const std::string& path);
}
