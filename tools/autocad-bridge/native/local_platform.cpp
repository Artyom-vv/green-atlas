#include "local_platform.h"
#include "file_io.h"
#include "filesystem_utf8.h"
#include <filesystem>
#include <stdexcept>
#include <fstream>
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <aclapi.h>
#include <sddl.h>
#include <bcrypt.h>
#include <shlobj.h>
#else
#include <sys/stat.h>
#include <unistd.h>
#include <uuid/uuid.h>
#endif

namespace ga::platform {
namespace fs = std::filesystem;
using ga::bridge::nativePath;
using ga::bridge::utf8Path;
#ifdef _WIN32
namespace {
struct LocalMemory {
    void* value = nullptr;
    ~LocalMemory() { if(value) LocalFree(value); }
};
std::wstring sidText(PSID sid) {
    if(!sid || !IsValidSid(sid)) throw std::runtime_error("invalid Windows SID");
    LPWSTR text = nullptr;
    if(!ConvertSidToStringSidW(sid, &text)) throw std::runtime_error("SID conversion failed");
    LocalMemory memory{text};
    return text;
}
std::wstring currentUser() {
    HANDLE token = nullptr;
    if(!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token))
        throw std::runtime_error("process token unavailable");
    struct Close { HANDLE value; ~Close(){ CloseHandle(value); } } close{token};
    DWORD size = 0;
    GetTokenInformation(token, TokenUser, nullptr, 0, &size);
    if(!size) throw std::runtime_error("token user unavailable");
    std::vector<unsigned char> data(size);
    if(!GetTokenInformation(token, TokenUser, data.data(), size, &size))
        throw std::runtime_error("token user unavailable");
    return sidText(reinterpret_cast<TOKEN_USER*>(data.data())->User.Sid);
}
bool directAncestors(const fs::path& path) {
    for(auto part = path; !part.empty();) {
        const auto attributes = GetFileAttributesW(part.c_str());
        if(attributes == INVALID_FILE_ATTRIBUTES) {
            const auto error = GetLastError();
            if(error != ERROR_FILE_NOT_FOUND && error != ERROR_PATH_NOT_FOUND) return false;
        } else if(attributes & FILE_ATTRIBUTE_REPARSE_POINT) return false;
        const auto parent = part.parent_path();
        if(parent == part) break;
        part = parent;
    }
    return true;
}
bool privateAcl(const fs::path& path, const std::wstring& user) {
    PSID owner = nullptr; PACL acl = nullptr;
    PSECURITY_DESCRIPTOR raw = nullptr;
    const auto error = GetNamedSecurityInfoW(path.c_str(), SE_FILE_OBJECT,
        OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
        &owner, nullptr, &acl, nullptr, &raw);
    LocalMemory memory{raw};
    if(error != ERROR_SUCCESS || !acl || sidText(owner) != user || !acl->AceCount) return false;
    for(DWORD index = 0; index < acl->AceCount; ++index) {
        void* data = nullptr;
        if(!GetAce(acl, index, &data) || !data) return false;
        const auto* header = static_cast<ACE_HEADER*>(data);
        if(header->AceType == ACCESS_DENIED_ACE_TYPE) continue;
        if(header->AceType != ACCESS_ALLOWED_ACE_TYPE || header->AceSize < sizeof(ACCESS_ALLOWED_ACE))
            return false;
        const auto sid = sidText(&static_cast<ACCESS_ALLOWED_ACE*>(data)->SidStart);
        if(sid != user && sid != L"S-1-3-4" && sid != L"S-1-5-18" && sid != L"S-1-5-32-544")
            return false;
    }
    return true;
}
fs::path localRoot() {
    PWSTR raw = nullptr;
    if(FAILED(SHGetKnownFolderPath(FOLDERID_LocalAppData, KF_FLAG_DEFAULT, nullptr, &raw)))
        return {};
    const fs::path root(raw); CoTaskMemFree(raw);
    if(!root.is_absolute()) return {};
    return root / L"GreenAtlas";
}
}
#endif

unsigned long processId() {
#ifdef _WIN32
    return static_cast<unsigned long>(GetCurrentProcessId());
#else
    return static_cast<unsigned long>(getpid());
#endif
}
std::string liveQueueDirectory() {
#ifdef _WIN32
    const auto root=localRoot();
    return root.empty()?std::string{}:utf8Path(root / L"live-query" / std::to_wstring(processId()));
#else
    return "/tmp/green-atlas-live-query-" + std::to_string(getuid()) + "-" + std::to_string(processId());
#endif
}
std::string mcpQueueDirectory(const std::string& version) {
#ifdef _WIN32
    const auto root=localRoot();
    return root.empty()?std::string{}:utf8Path(root / L"autocad-mcp" / nativePath(version));
#else
    return "/tmp/green-atlas-autocad-mcp-" + std::to_string(getuid()) + "-" + version;
#endif
}
bool ensurePrivateDirectory(const std::string& value) {
    try {
        const auto path = nativePath(value);
        if(!path.is_absolute()) return false;
#ifdef _WIN32
        if(path.root_name().wstring().size() != 2 || !directAncestors(path)) return false;
        const auto user = currentUser();
        const auto ownedRoot=localRoot();
        const auto relative=path.lexically_relative(ownedRoot);
        if(relative.empty() && path!=ownedRoot) return false;
        for(const auto& part:relative) if(part==L"..") return false;
        for(auto part=path;;part=part.parent_path()) {
            if(fs::exists(part) && !privateAcl(part,user)) return false;
            if(part==ownedRoot) break;
            if(part.empty() || part==part.parent_path()) return false;
        }
        std::vector<fs::path> missing;
        for(auto part = path; !fs::exists(part); part = part.parent_path()) missing.push_back(part);
        const auto sddl = L"O:" + user + L"D:P(A;OICI;FA;;;" + user + L")(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)";
        PSECURITY_DESCRIPTOR raw = nullptr;
        if(!ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl.c_str(), SDDL_REVISION_1, &raw, nullptr))
            return false;
        LocalMemory memory{raw};
        SECURITY_ATTRIBUTES attributes{sizeof(SECURITY_ATTRIBUTES), raw, FALSE};
        for(auto it = missing.rbegin(); it != missing.rend(); ++it) {
            if(!directAncestors(*it)) return false;
            if(!CreateDirectoryW(it->c_str(), &attributes) && GetLastError() != ERROR_ALREADY_EXISTS)
                return false;
            if(!privateAcl(*it,user)) return false;
        }
        for(auto part=path;;part=part.parent_path()) {
            if(!privateAcl(part,user)) return false;
            if(part==ownedRoot) break;
            if(part.empty() || part==part.parent_path()) return false;
        }
        return directAncestors(path) && fs::is_directory(path);
#else
        struct stat info{};
        if(lstat(value.c_str(), &info) != 0) {
            if(mkdir(value.c_str(), 0700) != 0 || lstat(value.c_str(), &info) != 0) return false;
        }
        return S_ISDIR(info.st_mode) && info.st_uid == getuid() && !(info.st_mode & 077);
#endif
    } catch(...) { return false; }
}
std::string transferDirectory() {
#ifdef _WIN32
    const auto root=localRoot();
    return root.empty()?std::string{}:utf8Path(root / L"Transfers");
#else
    throw std::runtime_error("Каталог передачи Mac принадлежит AppKit адаптеру");
#endif
}
std::string randomToken() {
    unsigned char bytes[16];
#ifdef _WIN32
    if(BCryptGenRandom(nullptr, bytes, sizeof(bytes), BCRYPT_USE_SYSTEM_PREFERRED_RNG) < 0)
        throw std::runtime_error("secure random source unavailable");
#else
    uuid_generate_random(bytes);
#endif
    constexpr const char* hex = "0123456789abcdef";
    std::string token;
    for(const auto byte : bytes) { token += hex[byte >> 4]; token += hex[byte & 15]; }
    return token;
}
std::string readBoundedFile(const std::string& path,std::size_t maximumBytes) {
    if(maximumBytes>16*1024*1024 || !directPath(path) || !fs::is_regular_file(nativePath(path)))
        throw std::runtime_error("Недопустимый файл очереди");
    std::ifstream input(nativePath(path),std::ios::binary);
    if(!input) throw std::runtime_error("Файл очереди недоступен");
    std::string data(maximumBytes+1,'\0');
    input.read(data.data(),static_cast<std::streamsize>(data.size()));
    const auto read=input.gcount();
    if(read<0 || static_cast<std::size_t>(read)>maximumBytes || (!input && !input.eof()))
        throw std::runtime_error("Превышен лимит файла очереди");
    data.resize(static_cast<std::size_t>(read));return data;
}
bool fileExists(const std::string& path) {
    std::error_code error;
    return fs::exists(nativePath(path), error);
}
bool directPath(const std::string& value) {
    try {
        const auto path = nativePath(value);
        if(!path.is_absolute()) return false;
#ifdef _WIN32
        return path.root_name().wstring().size() == 2 && directAncestors(path);
#else
        for(auto part = path; !part.empty();) {
            if(fs::is_symlink(part)) return false;
            const auto parent = part.parent_path();
            if(parent == part) break;
            part = parent;
        }
        return true;
#endif
    } catch(...) { return false; }
}
bool renameFile(const std::string& from, const std::string& to) {
#ifdef _WIN32
    if(!directPath(from) || !directPath(to) || !fs::is_regular_file(nativePath(from))) return false;
    return MoveFileExW(nativePath(from).c_str(),nativePath(to).c_str(),MOVEFILE_WRITE_THROUGH)!=FALSE;
#else
    std::error_code error;
    fs::rename(nativePath(from), nativePath(to), error);
    return !error;
#endif
}
std::vector<std::string> directoryNames(const std::string& path) {
    std::vector<std::string> names;
    std::error_code error;
    fs::directory_iterator it(nativePath(path), error), end;
    for(; !error && it != end; it.increment(error)) {
        if(names.size() >= 4096) throw std::runtime_error("Очередь превысила лимит файлов");
        names.push_back(utf8Path(it->path().filename()));
    }
    if(error) throw std::runtime_error("Каталог очереди недоступен");
    return names;
}
}
