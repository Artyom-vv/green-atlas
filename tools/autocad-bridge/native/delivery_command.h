#pragma once
#include <string>

void gaOpenInService();
void gaQueueOpenInService();
bool gaDeliveryActive();
// Uses the existing native extractor, never an alternate DXF reader.
bool gaExportPreparedSnapshot(const std::string& path, std::string& error);
