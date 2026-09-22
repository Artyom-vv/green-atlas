#pragma once

#include <string>
#include <vector>

// This boundary has no AutoCAD/AppKit types. Compile AppKit without WinStubs.
namespace gaDelivery {
void installMenu(void (*command)());
void removeMenu();
bool confirmPreparation(bool modified);
bool confirmPartial(const std::vector<std::string>& issues);
enum class ReferenceRecovery { RestoreAvailable, Skip, Cancel };
ReferenceRecovery confirmReferenceRecovery(size_t lost, size_t available);
void error(const std::string& message);
void stage(const std::string& message);
void endStage();
std::string createStaging();
std::vector<std::string> probeIssues(const std::string& directory);
std::string writeTicket(const std::string& directory, const std::string& version,
                        const std::string& autocadVersion,
                        const std::vector<std::string>& issues);
bool launchConnector(const std::string& ticket);
}
