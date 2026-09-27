#pragma once
#include "AcString.h"
#include "dbents.h"
#include "dbsymtb.h"
#include "file_io.h"
#include <filesystem>
#include <map>
#include <memory>
#include <stdexcept>
#include <vector>

namespace ga::capture {
namespace fs = std::filesystem;
inline void checked(Acad::ErrorStatus status, const char* stage) {
    if (status != Acad::eOk)
        throw std::runtime_error(std::string(stage) + ": " + std::to_string(int(status)));
}
template<class T> struct Close { void operator()(T* p) const { if (p) p->close(); } };
template<class T> using Open = std::unique_ptr<T, Close<T>>;
template<class T> Open<T> open(AcDbObjectId id, AcDb::OpenMode mode = AcDb::kForRead) {
    T* raw = nullptr;
    checked(acdbOpenObject(raw, id, mode), "capture object access");
    if (!raw) throw std::runtime_error("capture object absent");
    return Open<T>(raw);
}
inline std::string handle(AcDbObjectId id) {
    ACHAR buffer[32] = {};
    id.handle().getIntoAsciiBuffer(buffer);
    return AcString(buffer).utf8Str();
}
inline std::string quote(const std::string& text) {
    return "\"" + ga::bridge::jsonEscape(text) + "\"";
}
inline void publish(const fs::path& path, const std::string& text) {
    if (fs::exists(path) || !ga::bridge::writeAtomicText(path.string(), text))
        throw std::runtime_error("capture publication failed: " + path.filename().string());
}
struct Link {
    AcDbObjectId sourceId;
    AcDbDatabase* target = nullptr;
    std::string name, authored;
    int status = 0;
    bool unloaded = false;
};
struct Unit {
    AcDbDatabase* source = nullptr;
    std::string file;
    std::unique_ptr<AcDbDatabase> copy;
    std::map<AcDbObjectId, std::string> handles;
    std::vector<Link> links;
};
struct Instance {
    std::string route, type, layer, unavailable;
    std::vector<AcDbObjectId> chain;
    AcGeMatrix3d transform;
};
std::vector<Instance> captureInstances(AcDbDatabase& source);
std::string instanceReceipt(const std::vector<Instance>& instances,
                            const std::vector<Unit>& units);
}
