#pragma once
#include "native_clearance_domain.h"
#include <string>

namespace ga::clearance {
void clearJobs();
// Only called within the live session's identity/revision guards.
void advanceFile(AcDbDatabase& host,const std::string& sessionId,
                 const std::string& request,const std::string& output,
                 const std::function<void()>& checkBasis);
}
