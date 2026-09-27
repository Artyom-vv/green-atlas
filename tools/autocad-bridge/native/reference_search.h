#pragma once
#include <string>
#include <vector>

namespace gaDelivery {
struct ReferencePathRequest { std::string name; std::string storedPath; };
struct ReferencePathMatch {
    std::string name;
    std::string path;
    std::string status; // exact_suffix, unique_name, identical_copies, ambiguous, missing
    std::vector<std::string> candidates;
};
// Read-only, bounded to the explicit root. Never follows symlinks or chooses
// between different-content duplicates. No dataset paths/names in this module.
std::vector<ReferencePathMatch> findReferencePaths(
    const std::string& root, const std::vector<ReferencePathRequest>& requests);
// The folder and proposed substitutions are approved in native dialogs.
// Cancel means stop GAOPEN; Skip returns true with no chosen substitutions.
bool chooseReferencePaths(const std::vector<ReferencePathRequest>& requests,
                          std::vector<ReferencePathMatch>& chosen);
}
