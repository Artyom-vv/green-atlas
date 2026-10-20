#include "installed_module.h"
#include "../native/file_io.h"
#include "../native/filesystem_utf8.h"
#include "../native/local_platform.h"
#include <windows.h>
#include <shlobj.h>
#include <memory>
#include <system_error>

namespace gaDelivery {
namespace fs = std::filesystem;

std::filesystem::path installedWindowsDirectory() {
    PWSTR raw = nullptr;
    if(FAILED(SHGetKnownFolderPath(FOLDERID_RoamingAppData, 0, nullptr, &raw)) || !raw)
        return {};
    const std::unique_ptr<wchar_t, decltype(&CoTaskMemFree)> roaming(raw, &CoTaskMemFree);
    return fs::path(roaming.get()) / L"Autodesk" / L"ApplicationPlugins"
        / L"GreenAtlas.bundle" / L"Contents" / L"Windows";
}

bool matchesInstalledModule(const fs::path& loaded, const fs::path& installed) {
    using ga::bridge::utf8Path;
    if(!ga::platform::directPath(utf8Path(loaded))
        || !ga::platform::directPath(utf8Path(installed))) return false;
    std::error_code error;
    if(!fs::is_regular_file(loaded,error) || error) return false;
    error.clear();
    if(!fs::is_regular_file(installed,error) || error) return false;
    error.clear();
    if(fs::equivalent(loaded,installed,error) && !error) return true;
    error.clear();
    const auto loadedSize=fs::file_size(loaded,error);
    if(error || !loadedSize) return false;
    error.clear();
    if(loadedSize!=fs::file_size(installed,error) || error) return false;
    const auto loadedHash=ga::bridge::sha256File(utf8Path(loaded));
    return !loadedHash.empty() && loadedHash==ga::bridge::sha256File(utf8Path(installed));
}

}
