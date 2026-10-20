// Только синтетические дочерние процессы и явно переданный каталог сборочного теста.
#include "windows-platform.h"
#ifdef _WIN32
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
            {"synthetic_only", true}, {"webview_launched", false}}.dump() << '\n';
        return 0;
    } catch (const std::exception& error) {
        std::cout << Json{{"schema", "green-atlas.desktop-runtime-smoke/1"}, {"passed", false}, {"error", error.what()}}.dump() << '\n'; return 1;
    }
}
#endif
