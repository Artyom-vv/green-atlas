// Только синтетические дочерние процессы и явно переданный каталог сборочного теста.
#include "windows-platform.h"
#ifdef _WIN32
#include <aclapi.h>
#include <sddl.h>
#include <fstream>
#include <iostream>
#include <stdexcept>
using namespace ga::desktop::windows;
namespace {
void check(bool value, const char* text) { if (!value) throw std::runtime_error(text); }
void waitStopped(Runtime& runtime) {
    const auto deadline = GetTickCount64() + 5000;
    while (!runtime.stopped() && GetTickCount64() < deadline) Sleep(5);
    check(runtime.stopped(), "Собственное тестовое дерево не остановлено");
}
std::wstring securityText(const fs::path& path,
    SECURITY_INFORMATION information = OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION) {
    PSECURITY_DESCRIPTOR descriptor = nullptr;
    check(GetNamedSecurityInfoW(path.c_str(), SE_FILE_OBJECT,
        information, nullptr, nullptr, nullptr, nullptr,
        &descriptor) == ERROR_SUCCESS, "Не прочитан SD тестового объекта");
    LPWSTR text = nullptr;
    const bool converted = ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor,
        SDDL_REVISION_1, information, &text, nullptr) != 0;
    LocalFree(descriptor); check(converted, "Не сериализован SD тестового объекта");
    std::wstring result(text); LocalFree(text); return result;
}
void createSecurityDirectory(const fs::path& path, const std::wstring& sddl) {
    PSECURITY_DESCRIPTOR descriptor = nullptr;
    check(ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl.c_str(), SDDL_REVISION_1,
        &descriptor, nullptr) != 0, "Не создан SD тестового каталога");
    SECURITY_ATTRIBUTES attributes{sizeof(attributes), descriptor, FALSE};
    const bool created = CreateDirectoryW(path.c_str(), &attributes) != 0;
    LocalFree(descriptor); check(created, "Не создан отдельный тестовый каталог");
}
void createOwnedDirectory(const fs::path& path) { createSecurityDirectory(path, L"O:" + userSid()); }
void createInheritedUnsafe(const fs::path& parent, const fs::path& path,
    const wchar_t* grant = L"0x001200a9") {
    createSecurityDirectory(parent, L"O:" + userSid() + L"D:P(A;OICI;FA;;;" + userSid()
        + L")(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;" + grant + L";;;BU)");
    createOwnedDirectory(path);
    PSECURITY_DESCRIPTOR descriptor = nullptr; PACL acl = nullptr;
    check(GetNamedSecurityInfoW(path.c_str(), SE_FILE_OBJECT, DACL_SECURITY_INFORMATION,
        nullptr, nullptr, &acl, nullptr, &descriptor) == ERROR_SUCCESS, "Не прочитано inherited ACL");
    bool inherited = false;
    for (DWORD index = 0; acl && index < acl->AceCount; ++index) {
        void* ace = nullptr; check(GetAce(acl, index, &ace) != 0, "Не прочитан inherited ACE");
        inherited = inherited || (static_cast<ACE_HEADER*>(ace)->AceFlags & INHERITED_ACE);
    }
    LocalFree(descriptor); check(inherited, "Fixture не имеет inherited ACE");
}
std::string fileText(const fs::path& path) {
    std::ifstream stream(path, std::ios::binary);
    return {std::istreambuf_iterator<char>(stream), std::istreambuf_iterator<char>()};
}
void expectProfileRefusal(const fs::path& path, const std::string& code) {
    bool refused = false;
    try { prepareProfileDirectories(path); }
    catch (const ProfilePreparationError& error) { refused = error.code == code; }
    check(refused, "Bootstrap не отказал ожидаемым безопасным кодом");
}
Json profileTests(const fs::path& base) {
    check(!fs::exists(base), "Native profile fixtures требуют новый отдельный каталог");
    privateDirectory(base);
    const auto fresh = base / L"fresh";
    prepareProfileDirectories(fresh);
    for (const auto* name : {L"Workspace", L"Transfers", L"WebView2", L"Logs"}) privateDirectory(fresh / name);
    const auto before = securityText(fresh);
    prepareProfileDirectories(fresh); check(before == securityText(fresh), "Повтор изменил private SD");

    const auto unsafeParent = base / L"inherited-parent", bootstrap = unsafeParent / L"bootstrap";
    createInheritedUnsafe(unsafeParent, bootstrap);
    createOwnedDirectory(bootstrap / L"Installer");
    const auto program = bootstrap / L"Installer/setup.exe";
    // Явный текущий владелец и наследуемый чужой RX, как в полученном отчёте.
    PSECURITY_DESCRIPTOR fileDescriptor = nullptr;
    check(ConvertStringSecurityDescriptorToSecurityDescriptorW((L"O:" + userSid()).c_str(),
        SDDL_REVISION_1, &fileDescriptor, nullptr), "Не создан SD fixture файла");
    SECURITY_ATTRIBUTES fileSecurity{sizeof(fileSecurity), fileDescriptor, FALSE};
    HANDLE file = CreateFileW(program.c_str(), GENERIC_WRITE, 0, &fileSecurity, CREATE_NEW, 0, nullptr);
    LocalFree(fileDescriptor); check(file != INVALID_HANDLE_VALUE, "Не создан fixture программы");
    const std::string programBytes = "owned installer fixture"; DWORD written = 0;
    check(WriteFile(file, programBytes.data(), static_cast<DWORD>(programBytes.size()), &written, nullptr)
        && written == programBytes.size(), "Не записан fixture программы"); CloseHandle(file);
    const auto parentSecurity = securityText(unsafeParent), programSecurity = securityText(program);
    const auto programOwner = securityText(program, OWNER_SECURITY_INFORMATION);
    bool strictRejected = false;
    try { privateDirectory(bootstrap); } catch (const std::exception&) { strictRejected = true; }
    check(strictRejected, "Strict launch принял inherited foreign RX до подготовки");
    prepareProfileDirectories(bootstrap); privateDirectory(bootstrap);
    check(fileText(program) == programBytes, "Repair изменил байты программы");
    check(securityText(program, OWNER_SECURITY_INFORMATION) == programOwner, "Repair изменил владельца программы");
    check(securityText(unsafeParent) == parentSecurity, "Repair изменил ACL предка");
    check(securityText(program) != programSecurity, "Validated Installer ACL не приватизирован");
    check(securityText(program).find(L"BU") == std::wstring::npos, "Installer сохранил чужой RX");

    const auto dataParent = base / L"data-parent", withData = dataParent / L"bootstrap";
    createInheritedUnsafe(dataParent, withData); createOwnedDirectory(withData / L"Workspace");
    const auto sentinel = withData / L"Workspace/sentinel.txt";
    { std::ofstream stream(sentinel); stream << "manual data must survive"; }
    const auto dataRootSecurity = securityText(withData), dataSecurity = securityText(sentinel);
    expectProfileRefusal(withData, "PROFILE_BOOTSTRAP_HAS_DATA");
    check(securityText(withData) == dataRootSecurity && securityText(sentinel) == dataSecurity
        && fileText(sentinel) == "manual data must survive", "Refusal изменил данные или ACL");
    check(!fs::exists(withData / L"Transfers"), "Refusal создал недостающий runtime каталог");

    const auto writeParent = base / L"write-parent", writable = writeParent / L"bootstrap";
    createInheritedUnsafe(writeParent, writable, L"FA");
    const auto writableSecurity = securityText(writable);
    expectProfileRefusal(writable, "PROFILE_UNSAFE_WRITE_ACL");
    check(securityText(writable) == writableSecurity && !fs::exists(writable / L"Workspace"),
        "Refusal чужих write прав изменил ACL или создал каталог");

    const auto unknownParent = base / L"unknown-parent", unknown = unknownParent / L"bootstrap";
    createInheritedUnsafe(unknownParent, unknown);
    { std::ofstream stream(unknown / L"unknown.txt"); stream << "unknown data"; }
    const auto unknownSecurity = securityText(unknown);
    expectProfileRefusal(unknown, "PROFILE_BOOTSTRAP_HAS_DATA");
    check(securityText(unknown) == unknownSecurity && fileText(unknown / L"unknown.txt") == "unknown data",
        "Unknown entry refusal изменил данные или ACL");

    const auto privateData = fresh / L"Workspace/private.txt";
    { std::ofstream stream(privateData); stream << "saved project fixture"; }
    const auto privateRootSecurity = securityText(fresh), privateFileSecurity = securityText(privateData);
    prepareProfileDirectories(fresh);
    check(securityText(fresh) == privateRootSecurity && securityText(privateData) == privateFileSecurity
        && fileText(privateData) == "saved project fixture", "Private existing data было изменено");

    bool linksExecuted = false;
    const auto target = base / L"link-target"; privateDirectory(target);
    const auto targetSecurity = securityText(target);
    const auto leafLink = base / L"leaf-link";
    if (CreateSymbolicLinkW(leafLink.c_str(), target.c_str(), SYMBOLIC_LINK_FLAG_DIRECTORY | 0x2)) {
        linksExecuted = true; expectProfileRefusal(leafLink, "PROFILE_REPARSE_POINT");
        expectProfileRefusal(leafLink / L"child", "PROFILE_REPARSE_POINT");
        check(securityText(target) == targetSecurity, "Refusal изменил link target ACL");
        check(!fs::exists(target / L"child"), "Refusal создал каталог в link target");
        fs::remove(leafLink);
    }
    bool foreignOwnerExecuted = false;
    const auto foreign = base / L"foreign-owner"; privateDirectory(foreign);
    PSID administrators = nullptr;
    check(ConvertStringSidToSidW(L"S-1-5-32-544", &administrators), "Нет SID fixture группы");
    const auto ownerResult = SetNamedSecurityInfoW(const_cast<LPWSTR>(foreign.c_str()), SE_FILE_OBJECT,
        OWNER_SECURITY_INFORMATION, administrators, nullptr, nullptr, nullptr);
    LocalFree(administrators);
    if (ownerResult == ERROR_SUCCESS) {
        foreignOwnerExecuted = true;
        const auto foreignSecurity = securityText(foreign);
        expectProfileRefusal(foreign, "PROFILE_OWNER_MISMATCH");
        check(securityText(foreign) == foreignSecurity, "Refusal изменил чужое владение/ACL");
    }
    return {{"fresh_and_repeat", true}, {"inherited_foreign_rx_installer_repaired", true},
        {"installer_bytes_and_ancestor_acl_preserved", true}, {"unsafe_userdata_refused_unchanged", true},
        {"private_userdata_unchanged", true}, {"foreign_write_refused_unchanged", true},
        {"unknown_entry_refused_unchanged", true}, {"link_refusal_executed", linksExecuted},
        {"foreign_owner_refusal_executed", foreignOwnerExecuted}};
}
int child(int argc, wchar_t** argv) {
    if (argc == 2 && std::wstring_view(argv[1]) == L"--descendant") { Sleep(60000); return 0; }
    if (argc < 3 || std::wstring_view(argv[1]) != L"--data-dir") return -1;
    const fs::path mode(argv[2]);
    if (mode.filename() == L"silent") { Sleep(60000); return 0; }
    if (mode.filename() == L"oversized") { std::cout << std::string(17000, 'x') << '\n' << std::flush; Sleep(60000); return 0; }
    wchar_t module[32768]{}; check(GetModuleFileNameW(nullptr, module, 32768) > 0, "Нет пути тестового EXE");
    auto command = quote(module) + L" --descendant";
    STARTUPINFOW startup{}; startup.cb = sizeof(startup); PROCESS_INFORMATION process{};
    check(CreateProcessW(module, command.data(), nullptr, nullptr, FALSE, CREATE_NO_WINDOW, nullptr, nullptr, &startup, &process), "Нет тестового потомка");
    CloseHandle(process.hThread); CloseHandle(process.hProcess);
    std::cout << Json{{"service", "green-atlas-desktop"}, {"protocol", 1}, {"origin", "http://127.0.0.1:8123"},
        {"session_token", std::string(43, 'x')}}.dump() << '\n' << std::flush;
    return 0;
}
}
int wmain(int argc, wchar_t** argv) {
    try {
        const auto childResult = child(argc, argv); if (childResult >= 0) return childResult;
        check(argc == 2, "Передайте отдельный каталог синтетического теста");
        const auto root = fs::absolute(argv[1]); fs::create_directories(root);
        const auto profileChecks = profileTests(root / L"profile-security-fixtures");
        wchar_t module[32768]{}; check(GetModuleFileNameW(nullptr, module, 32768) > 0, "Нет пути тестового EXE");
        Settings settings; settings.runtime = module; settings.root = root; settings.logs = root;
        settings.web = root / L"Web"; settings.worker = root / L"Worker"; settings.tickets = root / L"Transfers";
        DownloadResult saved; saved.ok = true; saved.bytes = 42; saved.sha256 = std::string(64, 'a');
        saved.route = "/synthetic"; saved.mime = "application/zip"; saved.path = root / L"результат.zip";
        writeDownloadReceipt(settings, saved); writeDownloadReceipt(settings, saved);
        unsigned receiptCount = 0;
        for (const auto& entry : fs::directory_iterator(root)) {
            if (entry.path().filename().wstring().find(L"download-") != 0) continue;
            std::ifstream input(entry.path()); const auto receipt = Json::parse(input);
            check(receipt.value("bytes", 0) == 42 && receipt.value("filename", "") == utf8(L"результат.zip"), "Квитанция потеряла имя или размер");
            check(!receipt.contains("session_token") && !receipt.contains("path"), "Квитанция содержит приватные сведения");
            ++receiptCount;
        }
        check(receiptCount == 2, "Независимые квитанции перезаписали друг друга");
        Runtime runtime;
        settings.data = root / L"silent";
        runtime.start(settings, std::nullopt);
        const auto before = GetTickCount64(); check(!runtime.ready(), "Silent worker выдал ready");
        check(GetTickCount64() - before < 1000, "Ready pipe блокирует вызывающий поток");
        runtime.terminate(); waitStopped(runtime); runtime.release();
        settings.data = root / L"oversized"; runtime.start(settings, std::nullopt);
        bool rejected = false; const auto limit = GetTickCount64() + 5000;
        while (!rejected && GetTickCount64() < limit) {
            try { runtime.ready(); } catch (const std::exception&) { rejected = true; }
            Sleep(5);
        }
        check(rejected, "Ready byte limit не применён"); runtime.terminate(); waitStopped(runtime); runtime.release();
        settings.data = root / L"owned-tree"; runtime.start(settings, 8123);
        std::optional<std::string> record; const auto readyDeadline = GetTickCount64() + 5000;
        while (!record && GetTickCount64() < readyDeadline) { record = runtime.ready(); Sleep(5); }
        check(record.has_value(), "Не получена синтетическая готовность");
        Bridge bridge; check(bridge.acceptReady(Json::parse(*record)), "Некорректная готовность");
        const auto leaderDeadline = GetTickCount64() + 5000;
        while (runtime.running() && GetTickCount64() < leaderDeadline) Sleep(5);
        check(!runtime.running() && !runtime.stopped(), "Завершение лидера потеряло принадлежащего потомка");
        runtime.terminate(); waitStopped(runtime); runtime.release();
        std::cout << Json{{"schema", "green-atlas.desktop-runtime-smoke/1"}, {"passed", true},
            {"synthetic_only", true}, {"webview_launched", false}, {"profile_checks", profileChecks}}.dump() << '\n';
        return 0;
    } catch (const std::exception& error) {
        std::cout << Json{{"schema", "green-atlas.desktop-runtime-smoke/1"}, {"passed", false}, {"error", error.what()}}.dump() << '\n'; return 1;
    }
}
#endif
