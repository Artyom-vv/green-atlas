#include "../native/delivery_ui.h"
#include "../native/file_io.h"
#include "../native/filesystem_utf8.h"
#include "../native/local_platform.h"
#include "delivery_contract.h"
#include <windows.h>
#include <fstream>
#include <filesystem>
#include <stdexcept>

namespace gaDelivery {
namespace {
using contract::Json;
namespace fs=std::filesystem;
using ga::bridge::nativePath;
using ga::bridge::utf8Path;
Json fileEntry(const fs::path& path,const char* kind) {
    const auto name=utf8Path(path);
    if(!ga::platform::directPath(name) || !fs::is_regular_file(path))
        throw std::runtime_error("Файл передачи недоступен или содержит ссылку");
    const auto size=fs::file_size(path);
    const auto hash=ga::bridge::sha256File(name);
    if(!size || hash.size()!=64 || size!=fs::file_size(path))
        throw std::runtime_error("Не удалось проверить файл передачи");
    return {{"name",utf8Path(path.filename())},{"kind",kind},{"bytes",size},{"sha256",hash}};
}
Json readProbe(const fs::path& path) {
    if(!ga::platform::directPath(utf8Path(path)) || !fs::is_regular_file(path)
        || fs::file_size(path)>contract::kMaxDisplay)
        throw std::runtime_error("Недопустимый файл геометрии");
    std::ifstream input(path,std::ios::binary);
    auto result=Json::parse(input);
    if(!result.is_object()) throw std::runtime_error("Недопустимый снимок геометрии");
    return result;
}
Json probe(const fs::path& path,const std::string& facts,const Json& entry) {
    if(facts.empty()) return readProbe(path);
    return contract::factsProbe(facts,entry.at("sha256").get<std::string>());
}
std::string saveTicket(const fs::path& path,const Json& ticket) {
    if(!ga::platform::ensurePrivateDirectory(utf8Path(path.parent_path()))) return {};
    const auto data=ticket.dump(2);
    if(data.size()>8*1024*1024) return {};
    const auto temporary=path.parent_path()/nativePath("ticket-"+ga::platform::randomToken()+".pending");
    HANDLE file=CreateFileW(temporary.c_str(),GENERIC_WRITE,0,nullptr,CREATE_NEW,
        FILE_ATTRIBUTE_NORMAL|FILE_FLAG_OPEN_REPARSE_POINT,nullptr);
    if(file==INVALID_HANDLE_VALUE) return {};
    DWORD written=0;
    const bool ok=WriteFile(file,data.data(),static_cast<DWORD>(data.size()),&written,nullptr)
        && written==data.size() && FlushFileBuffers(file);
    const bool closed=CloseHandle(file)!=FALSE;
    // Публикуем только закрытый билет, не заменяя уже опубликованный другим вызовом.
    if(!ok || !closed || !MoveFileExW(temporary.c_str(),path.c_str(),MOVEFILE_WRITE_THROUGH)) {
        std::error_code error;fs::remove(temporary,error);return {};
    }
    return utf8Path(path);
}
}
std::string createStaging() {
    const auto root=ga::platform::transferDirectory();
    if(!ga::platform::ensurePrivateDirectory(root)) return {};
    const auto directory=nativePath(root)/nativePath(ga::platform::randomToken());
    if(fs::exists(directory) || !ga::platform::ensurePrivateDirectory(utf8Path(directory))) return {};
    return utf8Path(directory);
}
std::vector<SourceNotice> liveProbeIssues(const std::string& directory,const std::string& facts) {
    try {
        const auto path=nativePath(directory)/L"Drawing.autocad.json";
        const auto entry=fileEntry(path,"live_capture");
        const auto value=probe(path,facts,entry);
        const auto& summary=value.at("summary");
        if(!summary.is_object() || !value.at("source").is_object())
            return {{"Проверка снимка","Нет результата"}};
        std::vector<SourceNotice> issues;
        for(const auto& item : std::vector<std::pair<std::string,std::string>>{
            {"unresolved_instances","Без геометрии отображения"},
            {"cyclic_block_references","Циклические ссылки"},
            {"unexpanded_minsert_blocks","Нераскрытые массивы"}}) {
            const auto count=summary.value(item.first,0LL);
            if(count>0) issues.push_back({item.second,std::to_string(count)});
        }
        std::size_t missing=0;
        for(const auto& dependency:value.value("xref_dependencies",Json::array()))
            if(dependency.is_object() && dependency.value("status",std::string{})=="unresolved") ++missing;
        if(missing) issues.push_back({"Недоступные подосновы",std::to_string(missing)});
        return issues;
    } catch(...) { return {{"Чтение снимка","Ошибка"}}; }
}
std::string writeLiveTicket(const std::string& directory,const std::string& version,
                            const std::string& autocadVersion,const std::string& liveSession,
                            const std::string& transferFacts) {
    try {
        const auto root=nativePath(directory),path=root/L"Drawing.autocad.json";
        if(fs::exists(root/L"transfer.gatransfer") || !ga::platform::ensurePrivateDirectory(directory)) return {};
        const auto capture=fileEntry(path,"live_capture");
        if(capture.at("bytes").get<std::size_t>()>contract::kMaxDisplay) return {};
        const auto value=probe(path,transferFacts,capture);
        const auto original=value.at("source").at("path").get<std::string>();
        const auto source=nativePath(original);
        const auto sourceName=utf8Path(source.filename());
        if(value.at("capture_mode")!="live_document" || value.at("plugin_version")!=version
            || original.empty() || sourceName.empty() || source.filename().wstring().size()>240
            || fileEntry(source,"source_context").at("sha256")!=value.at("source").at("sha256")
            || capture!=fileEntry(path,"live_capture")) return {};
        Json files=Json::array();std::size_t count=0;
        const auto native=root/L"Native";
        if(!ga::platform::directPath(utf8Path(native))) return {};
        for(const auto& item:fs::directory_iterator(native)) {
            if(++count>contract::kMaxNativeFiles) return {};
            auto entry=fileEntry(item.path(),"native_capture");
            entry.erase("kind");entry["name"]="Native/"+utf8Path(item.path().filename());
            files.push_back(std::move(entry));
        }
        if(count<3) return {};
        for(const char* name:{"Drawing.autocad.json","Drawing.autocad.json.inventory.json"}) {
            auto entry=std::string(name)=="Drawing.autocad.json"?capture:fileEntry(root/nativePath(name),"capture_input");
            entry.erase("kind");entry["name"]=name;files.push_back(std::move(entry));
        }
        const auto receiptPath=native/L"session.json";
        if(fs::file_size(receiptPath)>4*1024*1024) return {};
        const auto receipt=readProbe(receiptPath);
        if(receipt.value("schema",std::string{})!="green-atlas.native-session/3"
            || receipt.at("display_sha256")!=capture.at("sha256")
            || receipt.at("plugin_version")!=version) return {};
        const auto session=Json::parse(liveSession);
        return saveTicket(root/L"transfer.gatransfer",
            contract::nativeTicket(version,autocadVersion,sourceName,capture,session,files));
    } catch(...) { return {}; }
}
// Совместимые помощники не добавляют второго пункта входа в интерфейс.
std::string writeTicket(const std::string& directory,const std::string& version,
                        const std::string& autocadVersion,const std::vector<std::string>& issues) {
    try {
        const auto root=nativePath(directory),path=root/L"Drawing.dxf.green-atlas.geometry.json";
        if(fs::exists(root/L"transfer.gatransfer") || !ga::platform::ensurePrivateDirectory(directory)) return {};
        auto value=readProbe(path);
        const auto drawing=fileEntry(root/L"Drawing.dxf","drawing");
        if(value.at("source").at("sha256")!=drawing.at("sha256") || value.at("plugin_version")!=version) return {};
        value["preparation"]={{"mode","autocad-copy-bind"},{"notices",issues}};
        if(!ga::bridge::writeAtomicText(utf8Path(path),value.dump())) return {};
        const auto geometry=fileEntry(path,"native_probe");
        if(drawing.at("bytes").get<std::size_t>()+geometry.at("bytes").get<std::size_t>()>contract::kMaxDisplay) return {};
        return saveTicket(root/L"transfer.gatransfer",{{"schema","green-atlas.transfer/1"},{"plugin_version",version},
            {"producer",{{"autocad_version",autocadVersion},{"target","windows-x86_64"}}},
            {"manifest",{{"entry","Drawing.dxf"},{"files",Json::array({drawing,geometry})}}}});
    } catch(...) { return {}; }
}
std::vector<std::string> probeIssues(const std::string& directory) {
    try {
        const auto value=readProbe(nativePath(directory)/L"Drawing.dxf.green-atlas.geometry.json");
        const auto& summary=value.at("summary");std::vector<std::string> issues;
        if(summary.value("cyclic_block_references",0LL)>0) issues.push_back("В копии обнаружены циклические ссылки.");
        if(summary.value("unexpanded_minsert_blocks",0LL)>0) issues.push_back("Часть массивов объектов не удалось раскрыть в копии.");
        for(const char* key:{"unresolved_instances","unreadable_entities","unreadable_block_records"})
            if(summary.value(key,0LL)>0) { issues.push_back("Часть объектов недоступна для расчёта. Замечания сохранятся.");break; }
        return issues;
    } catch(...) { return {"Файл геометрии не прошёл проверку чтения."}; }
}
}
