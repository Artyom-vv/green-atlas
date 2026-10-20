#pragma once
#include <string>

// Calls the production delivery command with the chosen native library.
// Only staging/launch/error boundaries are adapted to private qualification.
void runPrivateDeliveryProbe(void* library, const std::string& root,
                             const std::string& source, const std::string& libraryPath);
