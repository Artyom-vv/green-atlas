#pragma once
#include <string>

class AcDbDatabase;

namespace ga::capture {
// Copy the loaded native databases, never saved disk substitutes. The caller
// must hold the editor command context and disclose the authorised DBMOD side
// effect. No save/reset/rollback is performed on a source database.
// The destination must not exist. A failed capture has no completed receipt.
void capturePackage(AcDbDatabase& source, const std::string& destination,
                    const std::string& displaySha256 = {});
}
