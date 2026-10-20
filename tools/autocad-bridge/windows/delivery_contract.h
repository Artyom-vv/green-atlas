#pragma once
#include "../vendor/nlohmann/json.hpp"
#include <stdexcept>
#include <string>
#include <set>

// Общая проверяемая политика билета не обращается к Win32 или AutoCAD.
namespace gaDelivery::contract {
using Json = nlohmann::json;
constexpr std::size_t kMaxDisplay = 2ULL * 1024 * 1024 * 1024;
constexpr std::size_t kMaxFacts = 1024 * 1024;
constexpr std::size_t kMaxNativeFiles = 4096 + 3;
inline bool token(const std::string& value) {
    return value.size()==32 && value.find_first_not_of("0123456789abcdef")==std::string::npos;
}
inline Json factsProbe(const std::string& facts, const std::string& sha) {
    if(facts.empty() || facts.size()>kMaxFacts) throw std::runtime_error("Недопустимая сводка захвата");
    const auto envelope=Json::parse(facts);
    if(!envelope.is_object() || !envelope.contains("probe") || !envelope["probe"].is_object()
        || envelope.value("snapshot_sha256",std::string{})!=sha)
        throw std::runtime_error("Сводка не соответствует SHA снимка");
    return envelope["probe"];
}
inline Json nativeTicket(const std::string& version, const std::string& autocadVersion,
                         const std::string& sourceName, const Json& capture,
                         const Json& session, const Json& nativeFiles) {
    if(version!="0.1.48" || !session.is_object()
        || session.at("snapshot_sha256")!=capture.at("sha256")
        || !nativeFiles.is_array() || nativeFiles.size()<6
        || nativeFiles.size()>kMaxNativeFiles+2 || sourceName.empty())
        throw std::runtime_error("Неполный комплект текущего захвата");
    std::set<std::string> names;
    std::uint64_t total=0;
    for(const auto& file:nativeFiles) {
        const auto name=file.at("name").get<std::string>();
        const auto sha=file.at("sha256").get<std::string>();
        const auto bytes=file.at("bytes").get<std::uint64_t>();
        if(!names.insert(name).second || !bytes || sha.size()!=64
            || sha.find_first_not_of("0123456789abcdef")!=std::string::npos)
            throw std::runtime_error("Недопустимая идентичность файла захвата");
        const auto limit=name=="Native/session.json"?4ULL*1024*1024:
            name=="Native/semantics.json"?32ULL*1024*1024:
            name=="Native/instances.json" || name=="Drawing.autocad.json.inventory.json"?1024ULL*1024*1024:
            name=="Drawing.autocad.json"?kMaxDisplay:8ULL*1024*1024*1024;
        if(bytes>limit || total>8ULL*1024*1024*1024-bytes)
            throw std::runtime_error("Превышен лимит файлов CAD-захвата");
        total+=bytes;
    }
    for(const char* required:{"Native/session.json","Native/instances.json","Native/semantics.json",
                              "Native/host.dwg","Drawing.autocad.json","Drawing.autocad.json.inventory.json"})
        if(!names.count(required)) throw std::runtime_error("Не хватает обязательного файла захвата");
    return {{"schema","green-atlas.transfer/5"},{"plugin_version",version},
        {"producer",{{"autocad_version",autocadVersion},{"target","windows-x86_64"}}},
        {"source_name",sourceName},
        {"manifest",{{"entry","Drawing.autocad.json"},{"files",Json::array({capture})}}},
        {"live_session",session},
        {"native_session",{{"entry","Native/session.json"},{"files",nativeFiles}}}};
}
}
