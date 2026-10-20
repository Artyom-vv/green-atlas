#include "document_lock_policy.h"
#include <cassert>

int main() {
    using ga::liveQuery::canBorrowDocumentWrite;
    // A registered modal command already owns write permission. Reacquiring
    // these modes emits lock notifications; the archive must borrow them.
    assert(canBorrowDocumentWrite(AcAp::kWrite));
    assert(canBorrowDocumentWrite(AcAp::kProtectedAutoWrite));
    assert(canBorrowDocumentWrite(AcAp::kXWrite));
    // Neither another context's lock nor numeric enum ordering grants this
    // caller write permission. Exclusive read is numerically above Write.
    assert(!canBorrowDocumentWrite(AcAp::kRead));
    assert(!canBorrowDocumentWrite(AcAp::kNone));
    assert(!canBorrowDocumentWrite(AcAp::kNotLocked));
    assert(!canBorrowDocumentWrite(AcAp::kAutoWrite));
    assert(!canBorrowDocumentWrite(static_cast<AcAp::DocLockMode>(0x80)));
}
