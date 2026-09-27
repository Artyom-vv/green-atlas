#pragma once
#include <string>
#include <functional>
namespace ga::nativeQuery {
// Internal object-measurement command. The caller must separately establish
// capture/XREF completeness; this command never certifies planting safety.
void queryObjectsCommand();
// Same request/identity/kernel path for a worker-owned side database. Throws
// on failure so its lifecycle owner cannot publish a completion receipt.
void queryObjectsFile(const std::string& requestPath,
                      const std::function<void()>& checkBasis = {});
}
