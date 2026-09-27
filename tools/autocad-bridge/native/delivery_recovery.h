#pragma once
#include "dbmain.h"
#include <string>
#include <vector>

namespace gaDelivery {
struct ReferenceEvidence {
    std::string name;
    std::string resolvedPath;
    bool available = false;
    bool overlay = false;
    std::vector<AcDbHandle> instances;
    std::string storedPath;
    bool unloaded = false;
};
// Read-only inventory of local INSERTs referencing XREF definitions. Descendants
// inside an XREF belong to that file and must not be recreated in its host.
std::vector<ReferenceEvidence> captureReferences(AcDbDatabase* source);
// Reconcile ONLY definitions omitted by AutoCAD serialization. Source IDs are
// never used for writes. Returns false when the user cancels the preparation.
bool recoverReferences(AcDbDatabase* copy, const std::vector<ReferenceEvidence>& source,
                       std::vector<std::string>& notices);
}
