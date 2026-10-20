// Compile the real delivery flow, with distinct command/query names. This
// diagnostic does not maintain a second handwritten recipe for GAOPEN.
#define gaOpenInService gaParityOpenInService
#define gaQueueOpenInService gaParityQueueOpenInService
#define gaDeliveryActive gaParityDeliveryActive
#define ga gaParity
#include "../native/delivery_command.cpp"
#undef ga
#undef gaDeliveryActive
#undef gaQueueOpenInService
#undef gaOpenInService
#include "live_delivery_probe.h"
#include <dlfcn.h>
#include <fstream>

namespace parityFs = std::filesystem;
namespace {
using ParityText = std::string (*)(const std::string&);
using ParityOpen = std::string (*)(const std::string&, const std::string&);
using ParityStage = void (*)(const std::string&);
using ParityConfirm = bool (*)(bool);
using ParityIssues = std::vector<gaDelivery::SourceNotice> (*)(const std::string&);
using ParityReview = bool (*)(const std::vector<gaDelivery::SourceNotice>&);
using ParityTicket = std::string (*)(const std::string&, const std::string&,
                                    const std::string&, const std::string&);
struct ParityBindings {
    parityFs::path root, source;
    ParityText hash = nullptr;
    ParityOpen open = nullptr, archive = nullptr;
    ParityStage stage = nullptr;
    void (*endStage)() = nullptr;
    void (*close)() = nullptr;
    bool (*busy)() = nullptr;
    ParityConfirm confirm = nullptr;
    ParityIssues issues = nullptr;
    ParityReview review = nullptr;
    ParityTicket ticket = nullptr;
    std::string failure, ticketPath, snapshotPath;
    std::string progressViolation;
    std::vector<std::string> progressSequence;
    unsigned stages = 0, reviews = 0;
    bool confirmed = false, launchIntercepted = false, progressVisible = false;
};
ParityBindings* parity = nullptr;

template<class T> T paritySymbol(void* library, const char* name) {
    auto result = reinterpret_cast<T>(dlsym(library, name));
    if (!result) throw std::runtime_error(std::string("private delivery symbol unavailable: ") + name);
    return result;
}
ParityBindings& parityCurrent() {
    if (!parity) throw std::runtime_error("private delivery context unavailable");
    return *parity;
}
void parityProgress(bool visible, const char* operation) {
    // Проверяем исполняемый production flow на его границах, а не тексты стадий.
    auto& state = parityCurrent();
    state.progressSequence.emplace_back(operation);
    if (state.progressVisible != visible) {
        state.progressViolation = std::string("private delivery progress visibility invalid: ") + operation;
        throw std::runtime_error(state.progressViolation);
    }
}
std::string parityQuote(const std::string& value) {
    constexpr char hex[] = "0123456789abcdef";
    std::string result = "\"";
    for (unsigned char c : value) {
        if (c == '"' || c == '\\') { result += '\\'; result += char(c); }
        else if (c < 0x20) {
            result += "\\u00";
            result += hex[c >> 4]; result += hex[c & 15];
        } else result += char(c);
    }
    return result + '"';
}
void parityNoPending(const parityFs::path& directory) {
    if (!parityFs::exists(directory)) return;
    for (const auto& entry : parityFs::directory_iterator(directory)) {
        const auto name = entry.path().filename().string();
        if (name.rfind("request-", 0) == 0 || name.rfind("processing-", 0) == 0)
            throw std::runtime_error("private delivery refuses concurrent editor requests");
    }
}
}

// The original command's calls bind to these diagnostic boundary adapters;
// the selected library still implements the actual capture/archive/UI/ticket.
namespace gaParity::bridge {
std::string sha256File(const std::string& path) { return parityCurrent().hash(path); }
}
namespace gaParity::liveQuery {
std::string openSession(const std::string& token, const std::string& snapshot) {
    parityProgress(true, "capture");
    auto& state = parityCurrent();
    state.snapshotPath = snapshot;
    return state.open(token, snapshot);
}
std::string archiveSession(const std::string& token, const std::string& destination) {
    parityProgress(true, "archive");
    return parityCurrent().archive(token, destination);
}
}
namespace gaDelivery {
bool confirmPreparation(bool modified) {
    parityProgress(false, "confirm_preparation");
    auto& state = parityCurrent();
    state.confirmed = state.confirm(modified);
    return state.confirmed;
}
void stage(const std::string& message) {
    auto& state = parityCurrent();
    ++state.stages;
    state.stage(message);
    state.progressVisible = true;
    state.progressSequence.emplace_back("stage");
}
void endStage() {
    auto& state = parityCurrent();
    state.endStage();
    state.progressVisible = false;
    state.progressSequence.emplace_back("hide");
}
std::string createStaging() {
    const auto directory = parityCurrent().root / "staging";
    if (!parityFs::create_directory(directory)) throw std::runtime_error("fresh private staging required");
    parityFs::permissions(directory, parityFs::perms::owner_all, parityFs::perm_options::replace);
    return directory.string();
}
std::vector<SourceNotice> liveProbeIssues(const std::string& directory) {
    parityProgress(true, "inspect_source");
    return parityCurrent().issues(directory);
}
bool confirmLivePartial(const std::vector<SourceNotice>& issues) {
    parityProgress(false, "confirm_partial");
    auto& state = parityCurrent();
    ++state.reviews;
    return state.review(issues);
}
std::string writeLiveTicket(const std::string& directory, const std::string& version,
                            const std::string& autocadVersion, const std::string& session) {
    parityProgress(true, "write_ticket");
    return parityCurrent().ticket(directory, version, autocadVersion, session);
}
bool launchConnector(const std::string& ticket) {
    parityProgress(true, "launch");
    auto& state = parityCurrent();
    const auto expected = state.root / "staging/transfer.gatransfer";
    if (!parityFs::is_regular_file(ticket) || parityFs::canonical(ticket) != expected ||
        state.hash(ticket).empty())
        throw std::runtime_error("private delivery ticket validation failed");
    state.ticketPath = ticket;
    state.launchIntercepted = true;
    // The verified production ticket exists. Stop before starting desktop or
    // publishing anything in the user's library; this is explicitly recorded.
    return true;
}
void error(const std::string& message) {
    auto& state = parityCurrent();
    if (state.progressVisible)
        state.progressViolation = "private delivery progress visibility invalid: error";
    state.progressSequence.emplace_back("error");
    state.failure = message;
    std::ofstream(state.root / "delivery-error.txt") << message << '\n';
    acutPrintf(L"\nPRIVATE_DELIVERY_ERROR: %s", AcString(message.c_str()).kwszPtr());
}
}

void runPrivateDeliveryProbe(void* library, const std::string& root,
                             const std::string& source, const std::string& libraryPath) {
    if (parity) throw std::runtime_error("private delivery already active");
    ParityBindings state;
    state.root = parityFs::canonical(root); state.source = parityFs::canonical(source);
    state.hash = paritySymbol<ParityText>(library,
        "_ZN2ga6bridge10sha256FileERKNSt3__112basic_stringIcNS1_11char_traitsIcEENS1_9allocatorIcEEEE");
    state.open = paritySymbol<ParityOpen>(library,
        "_ZN2ga9liveQuery11openSessionERKNSt3__112basic_stringIcNS1_11char_traitsIcEENS1_9allocatorIcEEEES9_");
    state.archive = paritySymbol<ParityOpen>(library,
        "_ZN2ga9liveQuery14archiveSessionERKNSt3__112basic_stringIcNS1_11char_traitsIcEENS1_9allocatorIcEEEES9_");
    state.close = paritySymbol<void (*)()>(library, "_ZN2ga9liveQuery12closeSessionEv");
    state.busy = paritySymbol<bool (*)()>(library, "_ZN2ga6bridge20mcpRequestInProgressEv");
    state.stage = paritySymbol<ParityStage>(library,
        "_ZN10gaDelivery5stageERKNSt3__112basic_stringIcNS0_11char_traitsIcEENS0_9allocatorIcEEEE");
    state.endStage = paritySymbol<void (*)()>(library, "_ZN10gaDelivery8endStageEv");
    state.confirm = paritySymbol<ParityConfirm>(library, "_ZN10gaDelivery18confirmPreparationEb");
    state.issues = paritySymbol<ParityIssues>(library,
        "_ZN10gaDelivery15liveProbeIssuesERKNSt3__112basic_stringIcNS0_11char_traitsIcEENS0_9allocatorIcEEEE");
    state.review = paritySymbol<ParityReview>(library,
        "_ZN10gaDelivery18confirmLivePartialERKNSt3__16vectorINS_12SourceNoticeENS0_9allocatorIS2_EEEE");
    state.ticket = paritySymbol<ParityTicket>(library,
        "_ZN10gaDelivery15writeLiveTicketERKNSt3__112basic_stringIcNS0_11char_traitsIcEENS0_9allocatorIcEEEES8_S8_S8_");
    if (state.busy()) throw std::runtime_error("private library has an active MCP operation");
    parityNoPending("/tmp/green-atlas-live-query-" + std::to_string(getuid()) + "-" + std::to_string(getpid()));
    parityNoPending("/tmp/green-atlas-autocad-mcp-" + std::to_string(getuid()) + "-" + gaParity::bridge::kMcpQueueVersion);
    if (parityFs::exists(state.root / "staging") || parityFs::exists(state.root / "delivery-receipt.json"))
        throw std::runtime_error("fresh private delivery output required");
    const auto sourceBefore = state.hash(source);
    const auto libraryHash = state.hash(libraryPath);
    if (sourceBefore.empty() || libraryHash.empty()) throw std::runtime_error("private input/library hash unavailable");
    parity = &state;
    struct Reset {
        ParityBindings& state;
        ~Reset() { state.close(); state.endStage(); parity = nullptr; }
    } reset{state};
    gaParityOpenInService(); // The real source owns ordering, guards and catches.
    const bool unchanged = state.hash(source) == sourceBefore;
    const bool progressValid = state.progressViolation.empty() && !state.progressVisible;
    const bool complete = state.failure.empty() && state.launchIntercepted && unchanged && progressValid;
    std::ofstream receipt(state.root / "delivery-receipt.json");
    receipt << "{\"schema\":\"green-atlas.private-delivery-qualification/1\",\"native_pipeline_completed\":"
        << (complete ? "true" : "false") << ",\"preparation_confirmed\":" << (state.confirmed ? "true" : "false")
        << ",\"source_disk_unchanged\":" << (unchanged ? "true" : "false")
        << ",\"source_sha256\":" << parityQuote(sourceBefore)
        << ",\"library_sha256\":" << parityQuote(libraryHash)
        << ",\"library_path\":" << parityQuote(libraryPath)
        << ",\"ticket\":" << parityQuote(state.ticketPath)
        << ",\"snapshot\":" << parityQuote(state.snapshotPath)
        << ",\"session_diagnostics\":" << parityQuote(state.snapshotPath.empty() ? "" :
            state.snapshotPath + ".session-diagnostics.json")
        << ",\"production_stage_calls\":" << state.stages
        << ",\"production_partial_review_calls\":" << state.reviews
        << ",\"progress_visibility_checked\":" << (progressValid ? "true" : "false")
        << ",\"progress_violation\":" << parityQuote(state.progressViolation)
        << ",\"progress_sequence\":[";
    for (size_t index = 0; index < state.progressSequence.size(); ++index) {
        if (index) receipt << ',';
        receipt << parityQuote(state.progressSequence[index]);
    }
    receipt << ']'
        << ",\"launch_intercepted\":" << (state.launchIntercepted ? "true" : "false")
        << ",\"user_library_publication\":false,\"error\":" << parityQuote(state.failure)
        << ",\"flow_source\":\"native/delivery_command.cpp\","
        << "\"scope\":\"producer capture/archive/ticket qualification; desktop/API handoff and old installed idle callbacks are not qualified\"}\n";
    if (!receipt) throw std::runtime_error("private delivery receipt unavailable");
    acutPrintf(complete ? L"\nPRIVATE_DELIVERY_COMPLETE: desktop launch intercepted." :
                        L"\nPRIVATE_DELIVERY_INCOMPLETE: inspect delivery-receipt.json.");
}
