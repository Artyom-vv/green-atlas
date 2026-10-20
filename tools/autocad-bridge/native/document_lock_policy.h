#pragma once
#include "AcApDocLockmode.h"

namespace ga::liveQuery {
// Test the caller's myLockMode(), never lockMode() from another context.
// AutoWrite is not protected against nested locks during CAD archival.
inline constexpr bool canBorrowDocumentWrite(AcAp::DocLockMode mode) noexcept {
    return mode == AcAp::kWrite || mode == AcAp::kProtectedAutoWrite ||
           mode == AcAp::kXWrite;
}
}
