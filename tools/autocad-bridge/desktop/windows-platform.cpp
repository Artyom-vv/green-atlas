#include "windows-platform.h"
#ifdef _WIN32
#include <bcrypt.h>
#include <sddl.h>
#include <aclapi.h>
#include <shlobj.h>
#include <winhttp.h>
#include <algorithm>
#include <array>
#include <fstream>
#include <set>
#include <stdexcept>
#include <vector>

namespace ga::desktop::windows {
namespace {
void require(bool value, const char* text) { if (!value) throw std::runtime_error(text); }
struct Handle {
    HANDLE value = nullptr;
    Handle() = default;
    explicit Handle(HANDLE handle): value(handle) {}
    ~Handle() { if (value && value != INVALID_HANDLE_VALUE) CloseHandle(value); }
    Handle(const Handle&) = delete;
    Handle& operator=(const Handle&) = delete;
    HANDLE take() { const auto result = value; value = nullptr; return result; }
};
struct Internet {
    HINTERNET value = nullptr;
    explicit Internet(HINTERNET handle): value(handle) {}
    ~Internet() { if (value) WinHttpCloseHandle(value); }
};
struct Security {
    PSECURITY_DESCRIPTOR descriptor = nullptr;
    SECURITY_ATTRIBUTES attributes{sizeof(SECURITY_ATTRIBUTES), nullptr, FALSE};
    explicit Security(bool directory = false) {
        const auto user = userSid();
        const auto sddl = directory
            ? L"O:" + user + L"D:P(A;OICI;FA;;;" + user + L")(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"
            : L"O:" + user + L"D:P(A;;GA;;;" + user + L")(A;;GA;;;SY)";
        require(ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl.c_str(), SDDL_REVISION_1,
            &descriptor, nullptr) != 0, "Не удалось создать приватные права");
        attributes.lpSecurityDescriptor = descriptor;
    }
    ~Security() { if (descriptor) LocalFree(descriptor); }
};
std::wstring sidText(PSID sid) {
    LPWSTR raw = nullptr; if (!sid || !IsValidSid(sid) || !ConvertSidToStringSidW(sid, &raw)) return {};
    std::wstring result(raw); LocalFree(raw); return result;
}
bool privateDescriptor(PSID owner, PACL acl) {
    if (!owner || !IsValidSid(owner) || !acl || !acl->AceCount) return false;
    const auto user = userSid();
    if (sidText(owner) != user) return false;
    for (DWORD index = 0; index < acl->AceCount; ++index) {
        void* rawAce = nullptr; if (!GetAce(acl, index, &rawAce) || !rawAce) return false;
        const auto* header = static_cast<ACE_HEADER*>(rawAce);
        if (header->AceType == ACCESS_DENIED_ACE_TYPE) continue;
        if (header->AceType != ACCESS_ALLOWED_ACE_TYPE || header->AceSize < sizeof(ACCESS_ALLOWED_ACE)) return false;
        const auto sid = sidText(&static_cast<ACCESS_ALLOWED_ACE*>(rawAce)->SidStart);
        if (sid != user && sid != L"S-1-3-4" && sid != L"S-1-5-18" && sid != L"S-1-5-32-544") return false;
    }
    return true;
}
bool nonMutatingForeignAcl(PACL acl, bool parent = false) {
    if (!acl) return false;
    const auto user = userSid();
    for (DWORD index = 0; index < acl->AceCount; ++index) {
        void* raw = nullptr; if (!GetAce(acl, index, &raw) || !raw) return false;
        const auto* header = static_cast<ACE_HEADER*>(raw);
        if (header->AceType == ACCESS_DENIED_ACE_TYPE) continue;
        if (header->AceType != ACCESS_ALLOWED_ACE_TYPE || header->AceSize < sizeof(ACCESS_ALLOWED_ACE)) return false;
        if (parent && (header->AceFlags & INHERIT_ONLY_ACE)) continue;
        const auto* ace = static_cast<ACCESS_ALLOWED_ACE*>(raw);
        const auto sid = sidText(const_cast<DWORD*>(&ace->SidStart));
        if (sid.empty()) return false;
        if (sid == user || sid == L"S-1-3-4" || sid == L"S-1-5-18" || sid == L"S-1-5-32-544") continue;
        DWORD mask = ace->Mask;
        GENERIC_MAPPING mapping{FILE_GENERIC_READ, FILE_GENERIC_WRITE, FILE_GENERIC_EXECUTE, FILE_ALL_ACCESS};
        MapGenericMask(&mask, &mapping);
        if (mask & ~(FILE_GENERIC_READ | FILE_GENERIC_EXECUTE)) return false;
    }
    return true;
}
bool privateAcl(const fs::path& path) {
    PSID owner = nullptr; PACL acl = nullptr; PSECURITY_DESCRIPTOR raw = nullptr;
    const auto status = GetNamedSecurityInfoW(path.c_str(), SE_FILE_OBJECT,
        OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION, &owner, nullptr, &acl, nullptr, &raw);
    struct Local { PSECURITY_DESCRIPTOR value; ~Local() { if (value) LocalFree(value); } } memory{raw};
    return status == ERROR_SUCCESS && privateDescriptor(owner, acl);
}
fs::path profilePath() {
    PWSTR local = nullptr;
    if (FAILED(SHGetKnownFolderPath(FOLDERID_LocalAppData, 0, nullptr, &local)))
        throw ProfilePreparationError("PROFILE_KNOWN_FOLDER_FAILED", "profile");
    fs::path path;
    try { path = fs::path(local) / L"GreenAtlas"; } catch (...) { CoTaskMemFree(local); throw; }
    CoTaskMemFree(local); return path;
}
constexpr std::array<const wchar_t*, 4> profileDirectories{L"Workspace", L"Transfers", L"WebView2", L"Logs"};
struct ProfilePin {
    fs::path path;
    std::string role;
    Handle handle, writer;
    BY_HANDLE_FILE_INFORMATION info{};
    bool directory = false, owned = false, privateAcl = false, repairableAcl = false, parentRepairableAcl = false;
};
[[noreturn]] void profileFailure(const std::string& role, const char* code, DWORD error = 0) {
    throw ProfilePreparationError(code, role, error);
}
bool sameFile(const BY_HANDLE_FILE_INFORMATION& first, const BY_HANDLE_FILE_INFORMATION& second) {
    return first.dwVolumeSerialNumber == second.dwVolumeSerialNumber
        && first.nFileIndexHigh == second.nFileIndexHigh && first.nFileIndexLow == second.nFileIndexLow;
}
void profileOwner(ProfilePin& entry, HANDLE handle) {
    PSID owner = nullptr; PACL acl = nullptr; PSECURITY_DESCRIPTOR raw = nullptr;
    const auto result = GetSecurityInfo(handle, SE_FILE_OBJECT,
        OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION, &owner, nullptr, &acl, nullptr, &raw);
    struct Local { PSECURITY_DESCRIPTOR value; ~Local() { if (value) LocalFree(value); } } memory{raw};
    if (result != ERROR_SUCCESS) profileFailure(entry.role, "PROFILE_SECURITY_READ_FAILED", result);
    entry.owned = sidText(owner) == userSid(); entry.privateAcl = privateDescriptor(owner, acl);
    entry.repairableAcl = nonMutatingForeignAcl(acl); entry.parentRepairableAcl = nonMutatingForeignAcl(acl, true);
}
std::unique_ptr<ProfilePin> pinProfile(const fs::path& path, std::string role,
    bool directory, bool required = true, bool security = true) {
    auto entry = std::make_unique<ProfilePin>(); entry->path = path; entry->role = std::move(role); entry->directory = directory;
    entry->handle.value = CreateFileW(path.c_str(), FILE_READ_ATTRIBUTES | (security ? READ_CONTROL : 0),
        FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_EXISTING,
        FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS, nullptr);
    if (entry->handle.value == INVALID_HANDLE_VALUE) {
        const auto error = GetLastError();
        if (!required && (error == ERROR_FILE_NOT_FOUND || error == ERROR_PATH_NOT_FOUND)) return nullptr;
        profileFailure(entry->role, "PROFILE_OPEN_FAILED", error);
    }
    if (!GetFileInformationByHandle(entry->handle.value, &entry->info))
        profileFailure(entry->role, "PROFILE_INFORMATION_FAILED", GetLastError());
    if (entry->info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT)
        profileFailure(entry->role, "PROFILE_REPARSE_POINT");
    if (bool(entry->info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != directory)
        profileFailure(entry->role, "PROFILE_WRONG_TYPE");
    if (!directory && entry->info.nNumberOfLinks != 1)
        profileFailure(entry->role, "PROFILE_LINKED_FILE");
    if (security) profileOwner(*entry, entry->handle.value);
    return entry;
}
std::vector<fs::path> profileChildren(const fs::path& directory, const std::string& role) {
    std::error_code error;
    std::vector<fs::path> children;
    fs::directory_iterator iterator(directory, error), end;
    if (error) profileFailure(role, "PROFILE_ENUMERATION_FAILED", static_cast<DWORD>(error.value()));
    while (iterator != end) {
        if (children.size() >= 4096) profileFailure(role, "PROFILE_OBJECT_LIMIT");
        children.push_back(iterator->path()); iterator.increment(error);
        if (error) profileFailure(role, "PROFILE_ENUMERATION_FAILED", static_cast<DWORD>(error.value()));
    }
    return children;
}
std::wstring profileName(const fs::path& path) {
    auto name = path.filename().wstring();
    for (auto& ch : name) if (ch >= L'A' && ch <= L'Z') ch += L'a' - L'A';
    return name;
}
void openProfileWriter(ProfilePin& entry) {
    entry.writer.value = CreateFileW(entry.path.c_str(), READ_CONTROL | WRITE_DAC | FILE_READ_ATTRIBUTES,
        FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_EXISTING,
        FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS, nullptr);
    if (entry.writer.value == INVALID_HANDLE_VALUE) profileFailure(entry.role, "PROFILE_WRITE_ACCESS_FAILED", GetLastError());
    BY_HANDLE_FILE_INFORMATION info{};
    if (!GetFileInformationByHandle(entry.writer.value, &info) || !sameFile(entry.info, info)
        || (info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT)) profileFailure(entry.role, "PROFILE_IDENTITY_CHANGED");
    profileOwner(entry, entry.writer.value);
    if (!entry.owned) profileFailure(entry.role, "PROFILE_OWNER_MISMATCH");
    if (!entry.repairableAcl) profileFailure(entry.role, "PROFILE_UNSAFE_WRITE_ACL");
}
void setProfileAcl(ProfilePin& entry) {
    // SetSecurityInfo распространяет наследуемые ACE на детей. Это разрешено
    // только после проверки пустого профиля и всех собственных файлов Installer.
    const auto user = userSid();
    const auto flags = entry.directory ? L"OICI" : L"";
    const auto sddl = L"D:P(A;" + std::wstring(flags) + L";FA;;;" + user
        + L")(A;" + flags + L";FA;;;SY)(A;" + flags + L";FA;;;BA)";
    PSECURITY_DESCRIPTOR raw = nullptr;
    if (!ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl.c_str(), SDDL_REVISION_1, &raw, nullptr))
        profileFailure(entry.role, "PROFILE_SECURITY_BUILD_FAILED", GetLastError());
    struct Local { PSECURITY_DESCRIPTOR value; ~Local() { if (value) LocalFree(value); } } memory{raw};
    PACL acl = nullptr; BOOL present = FALSE, defaulted = FALSE;
    if (!GetSecurityDescriptorDacl(raw, &present, &acl, &defaulted) || !present || !acl)
        profileFailure(entry.role, "PROFILE_SECURITY_BUILD_FAILED", GetLastError());
    const auto result = SetSecurityInfo(entry.writer.value, SE_FILE_OBJECT,
        DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION, nullptr, nullptr, acl, nullptr);
    if (result != ERROR_SUCCESS) profileFailure(entry.role, "PROFILE_SECURITY_WRITE_FAILED", result);
    profileOwner(entry, entry.writer.value);
    if (!entry.privateAcl) profileFailure(entry.role, "PROFILE_SECURITY_VERIFY_FAILED");
}
class Hash {
public:
    Hash() {
        require(BCryptOpenAlgorithmProvider(&algorithm_, BCRYPT_SHA256_ALGORITHM, nullptr, 0) == 0, "SHA provider недоступен");
        if (BCryptCreateHash(algorithm_, &hash_, nullptr, 0, nullptr, 0, 0) != 0) {
            BCryptCloseAlgorithmProvider(algorithm_, 0); algorithm_ = nullptr;
            throw std::runtime_error("SHA state недоступен");
        }
    }
    ~Hash() { if (hash_) BCryptDestroyHash(hash_); if (algorithm_) BCryptCloseAlgorithmProvider(algorithm_, 0); }
    void add(const char* data, size_t size) {
        require(size <= MAXDWORD && BCryptHashData(hash_, reinterpret_cast<PUCHAR>(const_cast<char*>(data)),
            static_cast<ULONG>(size), 0) == 0, "Не удалось вычислить SHA");
    }
    std::string finish() {
        std::array<unsigned char, 32> bytes{};
        require(BCryptFinishHash(hash_, bytes.data(), static_cast<ULONG>(bytes.size()), 0) == 0, "Не удалось завершить SHA");
        constexpr char hex[] = "0123456789abcdef";
        std::string value;
        for (const auto byte : bytes) { value += hex[byte >> 4]; value += hex[byte & 15]; }
        return value;
    }
private:
    BCRYPT_ALG_HANDLE algorithm_ = nullptr;
    BCRYPT_HASH_HANDLE hash_ = nullptr;
};
std::string readBounded(const fs::path& path, size_t limit) {
    require(directPath(path), "Файл поставки недоступен или содержит ссылку");
    std::ifstream input(path, std::ios::binary);
    std::string value(limit + 1, '\0');
    input.read(value.data(), static_cast<std::streamsize>(value.size()));
    value.resize(static_cast<size_t>(input.gcount()));
    require(value.size() <= limit && !input.bad(), "Файл поставки превышает лимит");
    return value;
}
std::string readPipe(HANDLE pipe, size_t limit) {
    DWORD available = 0;
    if (!PeekNamedPipe(pipe, nullptr, 0, nullptr, &available, nullptr)) {
        const auto error = GetLastError();
        if (error == ERROR_BROKEN_PIPE || error == ERROR_NO_DATA || error == ERROR_PIPE_NOT_CONNECTED) return {};
        throw std::runtime_error("Канал API недоступен");
    }
    const auto amount = static_cast<DWORD>(std::min<size_t>(available, limit));
    if (!amount) return {};
    std::string value(amount, '\0'); DWORD count = 0;
    require(ReadFile(pipe, value.data(), amount, &count, nullptr) != 0, "Не удалось прочитать канал API");
    value.resize(count); return value;
}
struct Http {
    Internet session, connection, operation;
    Http(std::string_view origin, std::string_view token, std::string_view method, std::string_view route):
        session(WinHttpOpen(L"GreenAtlasDesktop/1", WINHTTP_ACCESS_TYPE_NO_PROXY, WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0)),
        connection(session.value && loopbackPort(origin) ? WinHttpConnect(session.value, L"127.0.0.1", *loopbackPort(origin), 0) : nullptr),
        operation(connection.value ? WinHttpOpenRequest(connection.value, utf16(method).c_str(), utf16(route).c_str(), nullptr,
            WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, 0) : nullptr) {
        require(operation.value != nullptr, "Не удалось открыть локальный запрос");
        // Проверка большого ticket может занимать время; ожидание идёт вне UI thread.
        const int receiveTimeout = route == "/_desktop/shutdown" ? 5000 : 60000;
        require(WinHttpSetTimeouts(operation.value, 2000, 2000, 5000, receiveTimeout) != 0, "Не удалось ограничить запрос");
        DWORD disabled = WINHTTP_DISABLE_REDIRECTS | WINHTTP_DISABLE_COOKIES | WINHTTP_DISABLE_AUTHENTICATION;
        require(WinHttpSetOption(operation.value, WINHTTP_OPTION_DISABLE_FEATURE, &disabled, sizeof(disabled)) != 0,
            "Не удалось запретить перенаправление приватного запроса");
        headers = L"X-Green-Atlas-Local-Session: " + utf16(token) + L"\r\nContent-Type: application/json\r\n";
    }
    DWORD send(const std::string& body) {
        require(WinHttpSendRequest(operation.value, headers.c_str(), static_cast<DWORD>(-1), body.empty() ? WINHTTP_NO_REQUEST_DATA
            : const_cast<char*>(body.data()), static_cast<DWORD>(body.size()), static_cast<DWORD>(body.size()), 0) != 0
            && WinHttpReceiveResponse(operation.value, nullptr) != 0, "Локальный сервис не ответил");
        DWORD code = 0, size = sizeof(code);
        require(WinHttpQueryHeaders(operation.value, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
            WINHTTP_HEADER_NAME_BY_INDEX, &code, &size, WINHTTP_NO_HEADER_INDEX) != 0, "Нет статуса локального ответа");
        return code;
    }
    std::string contentType() {
        wchar_t buffer[256]{}; DWORD size = sizeof(buffer);
        require(WinHttpQueryHeaders(operation.value, WINHTTP_QUERY_CONTENT_TYPE, WINHTTP_HEADER_NAME_BY_INDEX,
            buffer, &size, WINHTTP_NO_HEADER_INDEX) != 0, "Тип загрузки не указан");
        return utf8(buffer);
    }
    std::wstring headers;
};
}
std::string utf8(std::wstring_view value) {
    if (value.empty()) return {};
    const auto count = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value.data(), static_cast<int>(value.size()), nullptr, 0, nullptr, nullptr);
    require(count > 0, "Некорректная строка Windows");
    std::string output(static_cast<size_t>(count), '\0');
    require(WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value.data(), static_cast<int>(value.size()), output.data(), count, nullptr, nullptr) == count, "Некорректная строка Windows");
    return output;
}
std::wstring utf16(std::string_view value) {
    if (value.empty()) return {};
    const auto count = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, value.data(), static_cast<int>(value.size()), nullptr, 0);
    require(count > 0, "Некорректный UTF-8");
    std::wstring output(static_cast<size_t>(count), L'\0');
    require(MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, value.data(), static_cast<int>(value.size()), output.data(), count) == count, "Некорректный UTF-8");
    return output;
}
std::wstring quote(std::wstring_view value) {
    std::wstring output = L"\""; size_t slashes = 0;
    for (const auto ch : value) {
        if (ch == L'\\') { ++slashes; continue; }
        output.append(slashes * (ch == L'"' ? 2 : 1) + (ch == L'"' ? 1 : 0), L'\\');
        output += ch; slashes = 0;
    }
    output.append(slashes * 2, L'\\'); return output + L'"';
}
std::string digest(std::string_view value) { Hash hash; hash.add(value.data(), value.size()); return hash.finish(); }
bool directPath(const fs::path& path, bool directory) {
    if (!path.is_absolute()) return false;
    for (auto current = path; !current.empty(); current = current.parent_path()) {
        Handle handle(CreateFileW(current.c_str(), 0, FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr,
            OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS, nullptr));
        BY_HANDLE_FILE_INFORMATION info{};
        if (handle.value == INVALID_HANDLE_VALUE || !GetFileInformationByHandle(handle.value, &info)
            || (info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT)
            || (current == path && bool(info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != directory)) return false;
        if (current == current.parent_path()) break;
    }
    return true;
}
std::wstring userSid() {
    Handle token;
    require(OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token.value) != 0, "Нет token пользователя");
    DWORD length = 0; GetTokenInformation(token.value, TokenUser, nullptr, 0, &length);
    std::vector<char> storage(length);
    require(length && GetTokenInformation(token.value, TokenUser, storage.data(), length, &length), "Нет SID пользователя");
    LPWSTR raw = nullptr;
    require(ConvertSidToStringSidW(reinterpret_cast<TOKEN_USER*>(storage.data())->User.Sid, &raw) != 0, "Некорректный SID");
    std::wstring value(raw); LocalFree(raw); return value;
}
std::wstring ipcName() { return L"GreenAtlasDesktop-" + utf16(digest(utf8(userSid())).substr(0, 32)); }
void privateDirectory(const fs::path& path) {
    if (!fs::exists(path)) {
        require(directPath(path.parent_path(), true), "Родитель профиля содержит ссылку");
        Security security(true);
        require(CreateDirectoryW(path.c_str(), &security.attributes) != 0 || GetLastError() == ERROR_ALREADY_EXISTS, "Не удалось создать приватный профиль");
    }
    // Существующее владение и ACL проверяются, а не исправляются скрыто.
    require(directPath(path, true) && privateAcl(path), "Каталог профиля содержит ссылку или открыт чужому пользователю");
}
void prepareProfileDirectories(const fs::path& profile) {
    if (!profile.is_absolute()) profileFailure("profile", "PROFILE_INVALID_PATH");
    std::vector<std::unique_ptr<ProfilePin>> parents, entries;
    // До завершения удерживаем всех существующих предков без FILE_SHARE_DELETE.
    // Владение и ACL предков никогда не меняются.
    for (auto path = profile.parent_path(); !path.empty(); path = path.parent_path()) {
        parents.push_back(pinProfile(path, "ancestor", true, true, false));
        if (path == path.parent_path()) break;
    }
    auto root = pinProfile(profile, "profile", true, false);
    if (root) {
        entries.push_back(std::move(root));
        for (const auto* name : profileDirectories) {
            auto child = pinProfile(profile / name, utf8(name), true, false);
            if (child) entries.push_back(std::move(child));
        }
    }
    const bool safe = std::all_of(entries.begin(), entries.end(),
        [](const auto& entry) { return entry->privateAcl; });
    if (!safe) {
        for (const auto& entry : entries)
            if (!entry->owned) profileFailure(entry->role, "PROFILE_OWNER_MISMATCH");
        for (const auto& entry : entries)
            if (!entry->repairableAcl) profileFailure(entry->role, "PROFILE_UNSAFE_WRITE_ACL");
        auto parentSecurity = pinProfile(profile.parent_path(), "ancestor", true);
        if (!parentSecurity->parentRepairableAcl) profileFailure("ancestor", "PROFILE_UNSAFE_WRITE_ACL");
        parents.push_back(std::move(parentSecurity));
        // Рабочие данные исключают автоматическую правку ACL. Единственное
        // разрешённое непустое поддерево принадлежит установщику.
        std::set<std::wstring> names;
        fs::path installer;
        for (const auto& child : profileChildren(profile, "profile")) {
            const auto name = profileName(child);
            if (!names.insert(name).second) profileFailure("profile", "PROFILE_BOOTSTRAP_HAS_DATA");
            if (name == L"installer") { installer = child; continue; }
            bool fixed = false; std::string fixedRole;
            for (const auto* expected : profileDirectories)
                if (name == profileName(fs::path(expected))) { fixed = true; fixedRole = utf8(expected); }
            if (!fixed) profileFailure("profile", "PROFILE_BOOTSTRAP_HAS_DATA");
            if (!profileChildren(child, fixedRole).empty())
                profileFailure("profile", "PROFILE_BOOTSTRAP_HAS_DATA");
        }
        if (!installer.empty()) {
            entries.push_back(pinProfile(installer, "Installer", true));
            if (!entries.back()->owned) profileFailure("Installer", "PROFILE_OWNER_MISMATCH");
            if (!entries.back()->repairableAcl) profileFailure("Installer", "PROFILE_UNSAFE_WRITE_ACL");
            for (size_t index = entries.size() - 1; index < entries.size(); ++index) {
                if (!entries[index]->directory || entries[index]->role != "Installer") continue;
                for (const auto& child : profileChildren(entries[index]->path, "Installer")) {
                    if (entries.size() >= 4096) profileFailure("Installer", "PROFILE_OBJECT_LIMIT");
                    const auto attributes = GetFileAttributesW(child.c_str());
                    if (attributes == INVALID_FILE_ATTRIBUTES) profileFailure("Installer", "PROFILE_INFORMATION_FAILED", GetLastError());
                    auto entry = pinProfile(child, "Installer", bool(attributes & FILE_ATTRIBUTE_DIRECTORY));
                    if (!entry->owned) profileFailure("Installer", "PROFILE_OWNER_MISMATCH");
                    if (!entry->repairableAcl) profileFailure("Installer", "PROFILE_UNSAFE_WRITE_ACL");
                    entries.push_back(std::move(entry));
                }
            }
        }
        for (const auto& entry : entries)
            if (!entry->owned) profileFailure(entry->role, "PROFILE_OWNER_MISMATCH");
        // Все write-дескрипторы, идентичности и владельцы проверяются до первой
        // правки ACL. Владение не присваивается, рекурсивного reset нет.
        for (auto& entry : entries) openProfileWriter(*entry);
        for (auto& entry : entries) setProfileAcl(*entry);
    }
    // В уже приватном профиле могут быть рабочие данные. Их дескрипторы и байты
    // не меняем, только безопасно создаём недостающие фиксированные каталоги.
    privateDirectory(profile);
    for (const auto* name : profileDirectories) privateDirectory(profile / name);
}
Json prepareProfile() {
    Handle token;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token.value))
        profileFailure("token", "PROFILE_TOKEN_FAILED", GetLastError());
    TOKEN_ELEVATION elevation{}; DWORD size = 0;
    if (!GetTokenInformation(token.value, TokenElevation, &elevation, sizeof(elevation), &size))
        profileFailure("token", "PROFILE_TOKEN_FAILED", GetLastError());
    if (elevation.TokenIsElevated) profileFailure("token", "PROFILE_ELEVATED_TOKEN");
    const auto path = profilePath();
    std::wstring environment(32768, L'\0');
    const auto length = GetEnvironmentVariableW(L"LOCALAPPDATA", environment.data(), static_cast<DWORD>(environment.size()));
    if (!length || length >= environment.size()) profileFailure("profile", "PROFILE_ENV_MISMATCH");
    environment.resize(length);
    const auto normalized = [](fs::path value) {
        value = value.lexically_normal(); value.make_preferred();
        if (value != value.root_path() && value.filename().empty()) value = value.parent_path();
        return value.wstring();
    };
    const auto expected = normalized(path.parent_path());
    const fs::path observed(environment);
    const auto actual = normalized(observed);
    if (!observed.is_absolute() || CompareStringOrdinal(expected.c_str(), -1, actual.c_str(), -1, TRUE) != CSTR_EQUAL)
        profileFailure("profile", "PROFILE_ENV_MISMATCH");
    const Json receipt{{"schema", "green-atlas.desktop-profile/1"}, {"prepared", true}, {"profile", utf8(path.wstring())}};
    if (receipt.dump().size() > 8192) profileFailure("profile", "PROFILE_PATH_TOO_LONG");
    prepareProfileDirectories(path); return receipt;
}
Settings installedSettings(bool profile) {
    std::wstring buffer(32768, L'\0');
    const auto length = GetModuleFileNameW(nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
    require(length && length < buffer.size(), "Нет пути приложения"); buffer.resize(length);
    Settings value; value.root = fs::path(buffer).parent_path();
    require(launchMetadata(Json::parse(readBounded(value.root / L"green-atlas-launch.json", 16384))), "Некорректная поставка Windows");
    value.runtime = value.root / L"Runtime/GreenAtlasRuntime.exe";
    value.web = value.root / L"Web"; value.worker = value.root / L"Worker";
    require(directPath(value.runtime) && directPath(value.web / L"index.html")
        && directPath(value.worker / L"GreenAtlasQuery.crx") && directPath(value.worker / L"worker.json"), "Поставка приложения неполна");
    if (!profile) return value;
    value.profile = profilePath();
    value.data = value.profile / L"Workspace"; value.tickets = value.profile / L"Transfers";
    value.browser = value.profile / L"WebView2"; value.logs = value.profile / L"Logs";
    for (const auto& path : {value.profile, value.data, value.tickets, value.browser, value.logs}) privateDirectory(path);
    return value;
}
HttpResult request(std::string origin, std::string token, std::string method, std::string route, Json body) {
    HttpResult result;
    try {
        require(privateApiRoute(method, route), "Запрещён приватный маршрут");
        Http http(origin, token, method, route); result.status = http.send(body.is_null() ? "" : body.dump());
        const auto deadline = GetTickCount64() + 75000;
        std::string bytes; char buffer[4096]; DWORD count = 0;
        do {
            require(WinHttpReadData(http.operation.value, buffer, sizeof(buffer), &count) != 0, "Локальный ответ прерван");
            require(GetTickCount64() <= deadline, "Истекло время чтения локального ответа");
            require(bytes.size() + count <= 32768, "Локальный ответ превышает лимит"); bytes.append(buffer, count);
        } while (count);
        result.value = Json::parse(bytes);
        require(result.value.is_object(), "Ожидается локальная квитанция");
        if (result.status < 200 || result.status >= 300) result.error = result.value.value("message", "Сервис не подтвердил запрос");
    } catch (const std::exception& error) { result.error = error.what(); }
    return result;
}
DownloadResult download(std::string origin, std::string token, std::string route, std::string mime,
    fs::path destination, const std::shared_ptr<std::atomic_bool>& cancel) {
    DownloadResult result; result.path = destination; result.route = route; result.mime = mime;
    fs::path temporary;
    try {
        require(downloadType(route, mime, utf8(destination.filename().wstring())), "Тип загрузки не соответствует файлу");
        require(directPath(destination.parent_path(), true), "Каталог загрузки содержит ссылку");
        if (fs::exists(destination)) require(directPath(destination), "Файл назначения содержит ссылку");
        Http http(origin, token, "GET", route); require(http.send("") == 200, "Загрузка не подтверждена сервисом");
        require(downloadType(route, http.contentType(), utf8(destination.filename().wstring())), "Тип ответа не соответствует загрузке");
        GUID id{}; require(SUCCEEDED(CoCreateGuid(&id)), "Не удалось создать имя загрузки");
        wchar_t suffix[40]{}; StringFromGUID2(id, suffix, 40);
        temporary = destination.parent_path() / (L".green-atlas-" + std::wstring(suffix) + L".tmp");
        Handle output(CreateFileW(temporary.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW, FILE_FLAG_OPEN_REPARSE_POINT, nullptr));
        require(output.value != INVALID_HANDLE_VALUE, "Не удалось создать файл загрузки");
        Hash hash; std::array<char, 65536> buffer{}; DWORD count = 0; std::string prefix;
        do {
            require(!cancel->load(), "Загрузка отменена");
            require(WinHttpReadData(http.operation.value, buffer.data(), static_cast<DWORD>(buffer.size()), &count) != 0, "Загрузка прервана");
            result.bytes += count;
            prefix.append(buffer.data(), std::min<size_t>(count, 8 - prefix.size()));
            require(result.bytes <= (renderImageRoute(route) ? 256ULL * 1024 * 1024 : 8ULL * 1024 * 1024 * 1024), "Загрузка превышает лимит");
            DWORD written = 0;
            require(WriteFile(output.value, buffer.data(), count, &written, nullptr) && written == count, "Не удалось сохранить загрузку");
            hash.add(buffer.data(), count);
        } while (count);
        if (mime.substr(0, mime.find(';')) == "image/png")
            require(prefix == std::string("\x89PNG\r\n\x1a\n", 8), "Ответ не является PNG");
        if (mime.substr(0, mime.find(';')) == "application/zip")
            require(prefix.size() >= 4 && prefix.substr(0, 2) == "PK"
                && (prefix.substr(2, 2) == std::string("\x03\x04", 2)
                    || prefix.substr(2, 2) == std::string("\x05\x06", 2)
                    || prefix.substr(2, 2) == std::string("\x07\x08", 2)), "Ответ не является ZIP");
        require(result.bytes > 0 && FlushFileBuffers(output.value), "Загрузка пуста или не сохранена");
        result.sha256 = hash.finish(); CloseHandle(output.take());
        require(!cancel->load() && MoveFileExW(temporary.c_str(), destination.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH), "Не удалось опубликовать загрузку");
        temporary.clear(); result.ok = true;
    } catch (const std::exception& error) { result.error = error.what(); }
    if (!temporary.empty()) DeleteFileW(temporary.c_str());
    return result;
}
void writeDownloadReceipt(const Settings& settings, const DownloadResult& result) {
    require(directPath(settings.logs, true), "Каталог квитанций содержит ссылку");
    const Json receipt{{"schema", "green-atlas.desktop-download/1"}, {"ok", result.ok}, {"bytes", result.bytes},
        {"sha256", result.sha256}, {"route", result.route}, {"filename", utf8(result.path.filename().wstring())},
        {"mime", result.mime}, {"error", result.error}};
    GUID id{}; require(SUCCEEDED(CoCreateGuid(&id)), "Нет имени квитанции загрузки");
    wchar_t suffix[40]{}; StringFromGUID2(id, suffix, 40);
    const auto path = settings.logs / (L"download-" + std::wstring(suffix) + L".json");
    Handle output(CreateFileW(path.c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr,
        CREATE_NEW, FILE_FLAG_OPEN_REPARSE_POINT, nullptr));
    require(output.value != INVALID_HANDLE_VALUE, "Не удалось создать независимую квитанцию загрузки");
    const auto bytes = receipt.dump() + "\n"; DWORD written = 0;
    require(bytes.size() <= MAXDWORD && WriteFile(output.value, bytes.data(), static_cast<DWORD>(bytes.size()), &written, nullptr)
        && written == bytes.size() && FlushFileBuffers(output.value), "Не удалось сохранить квитанцию загрузки");
}
Runtime::~Runtime() { release(); }
bool Runtime::running() const { return process_ && WaitForSingleObject(process_, 0) == WAIT_TIMEOUT; }
bool Runtime::stopped() const {
    if (!process_) return true;
    JOBOBJECT_BASIC_ACCOUNTING_INFORMATION accounting{};
    return WaitForSingleObject(process_, 0) == WAIT_OBJECT_0 && job_
        && QueryInformationJobObject(job_, JobObjectBasicAccountingInformation, &accounting, sizeof(accounting), nullptr)
        && accounting.ActiveProcesses == 0;
}
void Runtime::terminate() { if (job_) require(TerminateJobObject(job_, 1) != 0, "Не удалось остановить собственный API job"); }
void Runtime::release() {
    // Закрытие последнего собственного job обеспечивает аварийную остановку дерева.
    for (auto* handle : {&output_, &errors_, &logFile_, &process_, &job_}) {
        if (*handle) { CloseHandle(*handle); *handle = nullptr; }
    }
}
void Runtime::start(const Settings& settings, std::optional<uint16_t> port) {
    require(!process_, "Предыдущий API ещё принадлежит host");
    Handle job(CreateJobObjectW(nullptr, nullptr));
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{}; limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    require(job.value && SetInformationJobObject(job.value, JobObjectExtendedLimitInformation, &limits, sizeof(limits)), "Не удалось создать API job");
    SECURITY_ATTRIBUTES security{sizeof(security), nullptr, TRUE};
    Handle reader, writer, errorReader, errorWriter;
    require(CreatePipe(&reader.value, &writer.value, &security, 0) && SetHandleInformation(reader.value, HANDLE_FLAG_INHERIT, 0), "Нет приватного ready pipe");
    require(CreatePipe(&errorReader.value, &errorWriter.value, &security, 0) && SetHandleInformation(errorReader.value, HANDLE_FLAG_INHERIT, 0), "Нет канала журнала");
    Handle input(CreateFileW(L"NUL", GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, &security, OPEN_EXISTING, 0, nullptr));
    require(input.value != INVALID_HANDLE_VALUE, "Нет stdin API");
    GUID id{}; require(SUCCEEDED(CoCreateGuid(&id)), "Нет имени локального журнала");
    wchar_t suffix[40]{}; StringFromGUID2(id, suffix, 40);
    const auto logPath = settings.logs / (L"runtime-" + std::wstring(suffix) + L".log");
    Handle log(CreateFileW(logPath.c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr,
        CREATE_NEW, FILE_FLAG_OPEN_REPARSE_POINT, nullptr));
    require(log.value != INVALID_HANDLE_VALUE, "Не удалось создать независимый журнал API");
    SIZE_T size = 0; InitializeProcThreadAttributeList(nullptr, 2, 0, &size);
    require(GetLastError() == ERROR_INSUFFICIENT_BUFFER && size > 0, "Нет размера API STARTUPINFOEX");
    std::vector<unsigned char> storage(size); auto* attributes = reinterpret_cast<LPPROC_THREAD_ATTRIBUTE_LIST>(storage.data());
    require(InitializeProcThreadAttributeList(attributes, 2, 0, &size) != 0, "Нет списка API STARTUPINFOEX");
    PROCESS_INFORMATION child{};
    try {
        HANDLE inherited[]{input.value, writer.value, errorWriter.value}; HANDLE jobs[]{job.value};
        require(UpdateProcThreadAttribute(attributes, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST, inherited, sizeof(inherited), nullptr, nullptr)
            && UpdateProcThreadAttribute(attributes, 0, PROC_THREAD_ATTRIBUTE_JOB_LIST, jobs, sizeof(jobs), nullptr, nullptr), "API job не привязан атомарно");
        STARTUPINFOEXW startup{}; startup.StartupInfo.cb = sizeof(startup); startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
        startup.StartupInfo.hStdInput = input.value; startup.StartupInfo.hStdOutput = writer.value; startup.StartupInfo.hStdError = errorWriter.value;
        startup.lpAttributeList = attributes;
        auto command = quote(settings.runtime.wstring()) + L" --data-dir " + quote(settings.data.wstring())
            + L" --web-dir " + quote(settings.web.wstring()) + L" --ticket-root " + quote(settings.tickets.wstring())
            + L" --cad-worker " + quote(settings.worker.wstring());
        if (port) command += L" --listen-port " + std::to_wstring(*port);
        require(CreateProcessW(settings.runtime.c_str(), command.data(), nullptr, nullptr, TRUE,
            CREATE_SUSPENDED | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT | EXTENDED_STARTUPINFO_PRESENT,
            nullptr, settings.root.c_str(), &startup.StartupInfo, &child) != 0, "Не удалось создать локальный API");
        BOOL belongs = FALSE;
        require(IsProcessInJob(child.hProcess, job.value, &belongs) && belongs && ResumeThread(child.hThread) == 1, "Не удалось запустить принадлежащий API");
        CloseHandle(child.hThread); child.hThread = nullptr;
        process_ = child.hProcess; child.hProcess = nullptr; job_ = job.take(); output_ = reader.take(); errors_ = errorReader.take(); logFile_ = log.take();
        startup_.clear(); gotReady_ = false; logBytes_ = 0;
    } catch (...) {
        if (child.hProcess) { TerminateJobObject(job.value, 1); WaitForSingleObject(child.hProcess, 2000); CloseHandle(child.hProcess); }
        if (child.hThread) CloseHandle(child.hThread);
        DeleteProcThreadAttributeList(attributes); throw;
    }
    DeleteProcThreadAttributeList(attributes);
}
std::optional<std::string> Runtime::ready() {
    if (!process_) return std::nullopt;
    auto errors = readPipe(errors_, 65536);
    if (logBytes_ < 4 * 1024 * 1024 && !errors.empty()) {
        errors.resize(std::min<size_t>(errors.size(), 4 * 1024 * 1024 - logBytes_));
        DWORD written = 0;
        require(WriteFile(logFile_, errors.data(), static_cast<DWORD>(errors.size()), &written, nullptr)
            && written == errors.size(), "Не удалось записать локальный журнал API");
        logBytes_ += errors.size();
    }
    const auto bytes = readPipe(output_, 16385);
    if (gotReady_) return std::nullopt;
    startup_ += bytes;
    require(startup_.size() <= 16384, "Ready API превышает лимит");
    const auto newline = startup_.find('\n');
    if (newline == std::string::npos) return std::nullopt;
    auto record = startup_.substr(0, newline); startup_.clear(); gotReady_ = true; return record;
}
TicketPipe::~TicketPipe() { stop(); }
void TicketPipe::start(HWND window, UINT message) {
    Handle stop(CreateEventW(nullptr, TRUE, FALSE, nullptr)); require(stop.value != nullptr, "Нет IPC stop event");
    const auto name = L"\\\\.\\pipe\\" + ipcName();
    Security security;
    Handle pipe(CreateNamedPipeW(name.c_str(), PIPE_ACCESS_INBOUND | FILE_FLAG_OVERLAPPED | FILE_FLAG_FIRST_PIPE_INSTANCE,
        PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS, 1, 64, 64, 0, &security.attributes));
    require(pipe.value != INVALID_HANDLE_VALUE, "Приватный IPC занят");
    auto ownedPipe = std::make_shared<Handle>(pipe.take());
    stop_ = stop.take();
    try { thread_ = std::thread([this, window, message, ownedPipe] {
        auto& pipe = *ownedPipe;
        Handle event(CreateEventW(nullptr, TRUE, FALSE, nullptr));
        if (!event.value) return;
        while (WaitForSingleObject(stop_, 0) == WAIT_TIMEOUT) {
            ResetEvent(event.value); OVERLAPPED pending{}; pending.hEvent = event.value;
            BOOL connected = ConnectNamedPipe(pipe.value, &pending);
            auto error = connected ? ERROR_SUCCESS : GetLastError();
            if (error == ERROR_PIPE_CONNECTED) SetEvent(event.value);
            else if (error != ERROR_IO_PENDING && error != ERROR_SUCCESS) break;
            HANDLE waits[]{stop_, event.value};
            if (!connected && WaitForMultipleObjects(2, waits, FALSE, INFINITE) != WAIT_OBJECT_0 + 1) {
                CancelIoEx(pipe.value, &pending);
                DWORD ignored = 0; GetOverlappedResult(pipe.value, &pending, &ignored, TRUE);
                break;
            }
            ResetEvent(event.value); pending = {}; pending.hEvent = event.value;
            std::array<char, 33> data{}; DWORD read = 0;
            BOOL received = ReadFile(pipe.value, data.data(), static_cast<DWORD>(data.size()), &read, &pending);
            if (!received && GetLastError() == ERROR_IO_PENDING) {
                if (WaitForMultipleObjects(2, waits, FALSE, 2000) == WAIT_OBJECT_0 + 1) received = GetOverlappedResult(pipe.value, &pending, &read, FALSE);
                else { CancelIoEx(pipe.value, &pending); GetOverlappedResult(pipe.value, &pending, &read, TRUE); }
            }
            if (received && read == 32 && ticketDirectoryId(std::string_view(data.data(), read))) {
                auto id = std::make_unique<std::string>(data.data(), read);
                if (PostMessageW(window, message, 0, reinterpret_cast<LPARAM>(id.get()))) id.release();
            }
            DisconnectNamedPipe(pipe.value);
        }
    }); } catch (...) { CloseHandle(stop_); stop_ = nullptr; throw; }
}
void TicketPipe::stop() {
    if (stop_) SetEvent(stop_);
    if (thread_.joinable()) thread_.join();
    if (stop_) { CloseHandle(stop_); stop_ = nullptr; }
}
bool TicketPipe::forward(std::string_view identifier) {
    if (!ticketDirectoryId(identifier)) return false;
    const auto name = L"\\\\.\\pipe\\" + ipcName();
    if (!WaitNamedPipeW(name.c_str(), 2000)) return false;
    Handle pipe(CreateFileW(name.c_str(), GENERIC_WRITE, 0, nullptr, OPEN_EXISTING, 0, nullptr));
    if (pipe.value == INVALID_HANDLE_VALUE) return false;
    const std::string value(identifier);
    DWORD written = 0; return WriteFile(pipe.value, value.data(), static_cast<DWORD>(value.size()), &written, nullptr) && written == value.size();
}
}
#endif
