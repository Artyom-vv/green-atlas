#pragma once
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include "windows-policies.h"
#include <atomic>
#include <filesystem>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>

namespace ga::desktop::windows {
namespace fs = std::filesystem;
std::string utf8(std::wstring_view value);
std::wstring utf16(std::string_view value);
std::wstring quote(std::wstring_view value);
std::string digest(std::string_view value);
bool directPath(const fs::path& path, bool directory = false);
void privateDirectory(const fs::path& path);
struct ProfilePreparationError : std::runtime_error {
    std::string code, role;
    DWORD win32Error;
    ProfilePreparationError(std::string value, std::string part, DWORD error = 0)
        : std::runtime_error(value), code(std::move(value)), role(std::move(part)), win32Error(error) {}
};
// Внутренний вход для изолированных native-тестов. CLI не принимает путь.
void prepareProfileDirectories(const fs::path& profile);
Json prepareProfile();
std::wstring userSid();
std::wstring ipcName();
struct Settings {
    fs::path root, runtime, web, worker, profile, data, tickets, browser, logs;
};
Settings installedSettings(bool profile);
struct HttpResult {
    DWORD status = 0;
    Json value;
    std::string error;
};
HttpResult request(std::string origin, std::string token, std::string method,
    std::string route, Json body = nullptr);
struct DownloadResult {
    bool ok = false;
    uint64_t bytes = 0;
    std::string sha256, error, route, mime;
    fs::path path;
};
DownloadResult download(std::string origin, std::string token, std::string route,
    std::string mime, fs::path destination, const std::shared_ptr<std::atomic_bool>& cancel);
void writeDownloadReceipt(const Settings& settings, const DownloadResult& result);
class Runtime {
public:
    ~Runtime();
    void start(const Settings& settings, std::optional<uint16_t> port);
    bool running() const;
    bool stopped() const;
    std::optional<std::string> ready();
    void terminate();
    void release();
private:
    HANDLE job_ = nullptr, process_ = nullptr, output_ = nullptr, errors_ = nullptr, logFile_ = nullptr;
    std::string startup_;
    uint64_t logBytes_ = 0;
    bool gotReady_ = false;
};
class TicketPipe {
public:
    ~TicketPipe();
    void start(HWND window, UINT message);
    void stop();
    static bool forward(std::string_view identifier);
private:
    HANDLE stop_ = nullptr;
    std::thread thread_;
};
}
#endif
