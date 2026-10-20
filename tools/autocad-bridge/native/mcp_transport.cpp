#include "mcp_transport.h"
#include "capture_commands.h"
#include "operation_control.h"
#include "file_io.h"
#include "bridge_config.h"
#include "delivery_command.h"
#include "delivery_ui.h"
#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cstdio>
#ifdef _WIN32
#include "local_platform.h"
#include "filesystem_utf8.h"
#else
#include <dirent.h>
#include <sys/stat.h>
#include <unistd.h>
#endif
#include <fstream>
#include <filesystem>
#include <iomanip>
#include <sstream>

namespace ga::bridge {

namespace {
bool gMcpRequestInProgress = false;
std::string gActiveMcpRequestId;
bool gMcpCancellationObserved = false;
std::chrono::steady_clock::time_point gMcpRequestStarted;
std::chrono::steady_clock::time_point gNextMcpControlCheck;

bool mcpCancellationRequested() {
    if (gActiveMcpRequestId.empty()) return false;
    if (gMcpCancellationObserved) return true;
    const auto now = std::chrono::steady_clock::now();
    if (now < gNextMcpControlCheck) return false;
    gNextMcpControlCheck = now + std::chrono::milliseconds(200);
    const std::string cancellationPath =
        mcpDirectory() + "/cancel-" + gActiveMcpRequestId + ".txt";
#ifdef _WIN32
    if (ga::platform::fileExists(cancellationPath)) {
#else
    if (access(cancellationPath.c_str(), F_OK) == 0) {
#endif
        gMcpCancellationObserved = true;
    }
    return gMcpCancellationObserved;
}

}  // namespace

std::string mcpDirectory() {
#ifdef _WIN32
    return ga::platform::mcpQueueDirectory(kMcpQueueVersion);
#else
    return "/tmp/green-atlas-autocad-mcp-" +
        std::to_string(static_cast<unsigned long>(getuid())) + "-" +
        kMcpQueueVersion;
#endif
}

bool ensureMcpDirectory() {
#ifdef _WIN32
    return ga::platform::ensurePrivateDirectory(mcpDirectory());
#else
    const std::string directory = mcpDirectory();
    if (mkdir(directory.c_str(), 0700) != 0 && errno != EEXIST) {
        return false;
    }
    return chmod(directory.c_str(), 0700) == 0;
#endif
}

void publishMcpProgress(const std::string& phase,
                        const std::size_t processedEntities) {
    if (gActiveMcpRequestId.empty()) return;
    const double elapsedSeconds = std::chrono::duration<double>(
        std::chrono::steady_clock::now() - gMcpRequestStarted).count();
    std::ostringstream payload;
    payload << std::setprecision(6)
            << "{\"schema\":\"green-atlas.autocad-mcp-progress/1\","
            << "\"request_id\":\"" << gActiveMcpRequestId << "\","
            << "\"plugin_version\":\"" << kPluginVersion << "\","
            << "\"phase\":\"" << jsonEscape(phase) << "\","
            << "\"processed_entities\":" << processedEntities << ','
            << "\"elapsed_seconds\":" << elapsedSeconds << "}\n";
    writeAtomicText(
        mcpDirectory() + "/progress-" + gActiveMcpRequestId + ".json",
        payload.str());
}

void publishMcpStatus(const bool ready) {
    if (!ensureMcpDirectory()) return;
    const std::string payload =
        std::string("{\"schema\":\"green-atlas.autocad-mcp-status/1\",") +
        "\"ready\":" + (ready ? "true" : "false") +
        ",\"plugin_version\":\"" + kPluginVersion +
        "\",\"pid\":" + std::to_string(
#ifdef _WIN32
            ga::platform::processId()
#else
            static_cast<long>(getpid())
#endif
        ) + "}\n";
    writeAtomicText(mcpDirectory() + "/status.json", payload);
}

bool isMcpRequestName(const std::string& name) {
    constexpr const char* prefix = "request-";
    constexpr const char* suffix = ".txt";
    if (name.size() != 8 + 32 + 4 || name.compare(0, 8, prefix) != 0 ||
        name.compare(name.size() - 4, 4, suffix) != 0) {
        return false;
    }
    return std::all_of(
        name.begin() + 8, name.begin() + 40,
        [](const unsigned char value) {
            return (value >= '0' && value <= '9') ||
                   (value >= 'a' && value <= 'f');
        });
}

bool mcpRequestInProgress() { return gMcpRequestInProgress; }

void processMcpRequestsOnIdle() {
    static auto nextPoll = std::chrono::steady_clock::now();
    const auto now = std::chrono::steady_clock::now();
    if (gMcpRequestInProgress || gaDeliveryActive() || now < nextPoll) return;
    nextPoll = now + std::chrono::milliseconds(200);
    gaDelivery::installMenu(gaQueueOpenInService); // Main menu may not exist at module startup.
    if (!ensureMcpDirectory()) return;

#ifdef _WIN32
    std::string requestName;
    try {
        for(const auto& candidate : ga::platform::directoryNames(mcpDirectory()))
            if(isMcpRequestName(candidate) && (requestName.empty() || candidate<requestName))
                requestName=candidate;
    } catch(...) { return; }
#else
    DIR* directory = opendir(mcpDirectory().c_str());
    if (directory == nullptr) return;
    std::string requestName;
    while (const dirent* entry = readdir(directory)) {
        const std::string candidate = entry->d_name;
        if (isMcpRequestName(candidate) &&
            (requestName.empty() || candidate < requestName)) {
            requestName = candidate;
        }
    }
    closedir(directory);
#endif
    if (requestName.empty()) return;

    const std::string requestId = requestName.substr(8, 32);
    const std::string requestPath = mcpDirectory() + "/" + requestName;
    const std::string processingPath =
        mcpDirectory() + "/processing-" + requestId + ".txt";
#ifdef _WIN32
    if(!ga::platform::renameFile(requestPath,processingPath)) return;
#else
    if (std::rename(requestPath.c_str(), processingPath.c_str()) != 0) return;
#endif

    gMcpRequestInProgress = true;
    gActiveMcpRequestId = requestId;
    gMcpCancellationObserved = false;
    gMcpRequestStarted = std::chrono::steady_clock::now();
    gNextMcpControlCheck = gMcpRequestStarted;
    ScopedOperationControl control({
        mcpCancellationRequested,
        publishMcpProgress
    });
    publishMcpProgress("accepted", 0);
#ifdef _WIN32
    std::string boundedRequest;
    try { boundedRequest=ga::platform::readBoundedFile(processingPath,16384); }
    catch(...) {
        std::error_code cleanup;std::filesystem::remove(ga::bridge::nativePath(processingPath),cleanup);
        gActiveMcpRequestId.clear();gMcpRequestInProgress=false;return;
    }
    std::istringstream request(boundedRequest);
#else
    std::ifstream request(processingPath, std::ios::binary);
#endif
    std::ostringstream requestBuffer;
    requestBuffer << request.rdbuf();
    const bool requestReadable = request.good() || request.eof();
#ifndef _WIN32
    request.close();
#endif
    std::string sourcePath = requestBuffer.str();
    if (!sourcePath.empty() && sourcePath.back() == '\n') sourcePath.pop_back();
    if (!sourcePath.empty() && sourcePath.back() == '\r') sourcePath.pop_back();

    ExportResult result;
    bool succeeded = false;
#ifdef _WIN32
    const bool absolute=ga::platform::directPath(sourcePath);
#else
    const bool absolute=!sourcePath.empty() && sourcePath.front()=='/';
#endif
    if (!requestReadable || sourcePath.empty() || !absolute || sourcePath.find('\0')!=std::string::npos ||
        sourcePath.find('\n') != std::string::npos ||
        sourcePath.find('\r') != std::string::npos) {
        result.error = "invalid MCP request payload";
    } else {
        result = exportRegionTopologyFromPath(sourcePath);
        succeeded = result.success;
    }

    std::ostringstream response;
    response << "{\"schema\":\"green-atlas.autocad-mcp-response/1\","
             << "\"request_id\":\"" << requestId << "\","
             << "\"success\":" << (succeeded ? "true" : "false") << ","
             << "\"source_path\":\"" << jsonEscape(sourcePath) << "\","
             << "\"output_path\":";
    if (succeeded) response << "\"" << jsonEscape(result.path) << "\"";
    else response << "null";
    response << ",\"error\":";
    if (succeeded) response << "null";
    else response << "\"" << jsonEscape(result.error) << "\"";
    response << "}\n";
    writeAtomicText(
        mcpDirectory() + "/response-" + requestId + ".json",
        response.str());
#ifdef _WIN32
    std::error_code cleanup;
    std::filesystem::remove(ga::bridge::nativePath(processingPath),cleanup);
    std::filesystem::remove(ga::bridge::nativePath(mcpDirectory()+"/cancel-"+requestId+".txt"),cleanup);
    std::filesystem::remove(ga::bridge::nativePath(mcpDirectory()+"/progress-"+requestId+".json"),cleanup);
#else
    std::remove(processingPath.c_str());
    std::remove((mcpDirectory() + "/cancel-" + requestId + ".txt").c_str());
    std::remove((mcpDirectory() + "/progress-" + requestId + ".json").c_str());
#endif
    publishMcpStatus(true);
    gActiveMcpRequestId.clear();
    gMcpCancellationObserved = false;
    gMcpRequestInProgress = false;
}

}  // namespace ga::bridge
