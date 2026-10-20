#pragma once

#include <filesystem>

namespace gaDelivery {

// AutoCAD may load the installed ARX through a path alias or from an identical
// private copy. Compare the file itself before launching the installed host.
bool matchesInstalledModule(const std::filesystem::path& loaded,
                            const std::filesystem::path& installed);
std::filesystem::path installedWindowsDirectory();

}
