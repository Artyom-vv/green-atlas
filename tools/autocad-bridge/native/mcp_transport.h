#pragma once
#include <string>

namespace ga::bridge {

std::string mcpDirectory();
bool mcpRequestInProgress();
void processMcpRequestsOnIdle();
void publishMcpStatus(bool ready);

}  // namespace ga::bridge
