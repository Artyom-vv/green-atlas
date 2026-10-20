#include "../native/delivery_ui.h"
#include "../native/filesystem_utf8.h"
#include "../native/local_platform.h"
#include "delivery_contract.h"
#include "installed_module.h"
#include <windows.h>
#include <filesystem>
#include <fstream>

namespace gaDelivery {
namespace {
std::wstring quote(const std::wstring& value) {
    std::wstring result=L"\"";std::size_t slashes=0;
    for(const auto ch:value) {
        if(ch==L'\\') { ++slashes;continue; }
        if(ch==L'"') { result.append(slashes*2+1,L'\\');result+=ch; }
        else { result.append(slashes,L'\\');result+=ch; }
        slashes=0;
    }
    result.append(slashes*2,L'\\');return result+L'"';
}
}
bool launchConnector(const std::string& ticket, std::string* failure) {
    const auto reject=[failure](const char* reason) {
        if(failure) *failure=reason;
        return false;
    };
    try {
        namespace fs=std::filesystem;
        using ga::bridge::nativePath;
        using ga::bridge::utf8Path;
        const auto path=nativePath(ticket);
        const auto id=utf8Path(path.parent_path().filename());
        const auto root=nativePath(ga::platform::transferDirectory());
        if(!contract::token(id) || path.filename()!=L"transfer.gatransfer"
            || path.parent_path().parent_path()!=root
            || !ga::platform::ensurePrivateDirectory(utf8Path(path.parent_path()))
            || !ga::platform::directPath(ticket) || !fs::is_regular_file(path))
            return reject("Файл передачи недоступен. Данные остаются в списке передач AutoCAD.");
        HMODULE module=nullptr;
        if(!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
            reinterpret_cast<LPCWSTR>(&launchConnector),&module))
            return reject("AutoCAD не смог определить загруженный модуль Green Atlas. Перезапустите AutoCAD.");
        wchar_t filename[32768];const auto length=GetModuleFileNameW(module,filename,32768);
        if(!length || length>=32768)
            return reject("AutoCAD не смог определить путь модуля Green Atlas. Перезапустите AutoCAD.");
        const auto binary=fs::path(filename);
        const auto base=installedWindowsDirectory();
        if(base.empty()) return reject("Не удалось найти каталог установки Green Atlas в профиле Windows.");
        if(!matchesInstalledModule(binary,base/L"GreenAtlasBridge.arx")) {
            if(failure) *failure="AutoCAD загрузил другую копию плагина: "
                +utf8Path(binary)+". Закройте AutoCAD и проверьте установленный комплект Green Atlas.";
            return false;
        }
        const auto config=base/L"green-atlas-launch.json";
        if(!ga::platform::directPath(utf8Path(config)) || !fs::is_regular_file(config)
            || fs::file_size(config)>16384)
            return reject("В установленном комплекте отсутствует конфигурация запуска Green Atlas.");
        std::ifstream input(config,std::ios::binary);
        if(!input) return reject("Не удалось прочитать конфигурацию запуска Green Atlas.");
        const auto metadata=contract::Json::parse(input);
        const contract::Json expected={{"schema","green-atlas.desktop-launch/1"},
            {"kind","windows_webview2"},{"host","GreenAtlasDesktop.exe"},
            {"runtime","Runtime/GreenAtlasRuntime.exe"},{"web","Web"},{"worker","Worker"}};
        if(metadata!=expected)
            return reject("Конфигурация запуска не соответствует версии плагина Green Atlas.");
        const auto executable=base/L"GreenAtlasDesktop.exe";
        if(!ga::platform::directPath(utf8Path(executable)) || !fs::is_regular_file(executable))
            return reject("В комплекте отсутствует приложение GreenAtlasDesktop.exe.");
        auto command=quote(executable.wstring())+L" --ticket-id "+nativePath(id).wstring();
        STARTUPINFOW startup{};startup.cb=sizeof(startup);
        PROCESS_INFORMATION child{};
        // Приложение живёт независимо от редактора и само владеет своим runtime.
        // Наследование handles отключено, shell/URL/команда из метаданных не используются.
        if(!CreateProcessW(executable.c_str(),command.data(),nullptr,nullptr,FALSE,
            CREATE_NO_WINDOW,nullptr,base.c_str(),&startup,&child)) {
            if(failure) *failure="Windows не смогла запустить GreenAtlasDesktop.exe (код "
                +std::to_string(GetLastError())+"). Файлы сохранены в списке передач AutoCAD.";
            return false;
        }
        const bool threadClosed=CloseHandle(child.hThread)!=FALSE;
        const bool processClosed=CloseHandle(child.hProcess)!=FALSE;
        if(!threadClosed || !processClosed)
            return reject("Не удалось завершить запуск приложения Green Atlas. Файлы передачи сохранены.");
        return true;
    } catch(const std::exception&) {
        return reject("Путь или файлы установленного плагина повреждены. Файлы передачи сохранены в списке передач AutoCAD.");
    } catch(...) { return reject("Не удалось запустить Green Atlas. Файлы передачи сохранены в списке передач AutoCAD."); }
}
}
