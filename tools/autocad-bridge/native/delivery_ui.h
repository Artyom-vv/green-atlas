#pragma once

#include <string>
#include <vector>

// This boundary has no AutoCAD/AppKit types. Compile AppKit without WinStubs.
namespace gaDelivery {
void installMenu(void (*command)());
void removeMenu();
#ifdef _WIN32
void installContextMenu(void (*command)(), const void* appId);
void removeContextMenu();
#endif
bool confirmPreparation(bool modified);
bool confirmPartial(const std::vector<std::string>& issues);
struct SourceNotice { std::string label; std::string value; };
bool confirmLivePartial(const std::vector<SourceNotice>& issues);
enum class ReferenceRecovery { RestoreAvailable, Skip, Cancel };
ReferenceRecovery confirmReferenceRecovery(size_t lost, size_t available);
void error(const std::string& message);
void stage(const std::string& message);
void progress(const std::string& phase, std::size_t processed);
void endStage();
std::string createStaging();
std::vector<std::string> probeIssues(const std::string& directory);
std::vector<SourceNotice> liveProbeIssues(const std::string& directory,
                                        const std::string& transferFacts = {});
std::string writeTicket(const std::string& directory, const std::string& version,
                        const std::string& autocadVersion,
                        const std::vector<std::string>& issues);
std::string writeLiveTicket(const std::string& directory, const std::string& version,
                            const std::string& autocadVersion,
                            const std::string& liveSession = {},
                            const std::string& transferFacts = {});
bool launchConnector(const std::string& ticket, std::string* failure = nullptr);
}
