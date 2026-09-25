#pragma once
#include <string>

namespace ga::liveQuery {
// Editor main-thread only. A session is invalidated by host/XREF edits or close;
// the token is NOT a claim that all source objects are calculation-ready.
std::string openSession(const std::string& token, const std::string& snapshotPath);
std::string inspectSession(const std::string& token);
void querySession(const std::string& token, const std::string& requestPath);
void prepareSessionFaces(const std::string& token,const std::string& requestPath,
                         const std::string& outputPath);
void closeSession();
}
