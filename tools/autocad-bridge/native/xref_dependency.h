#pragma once
#include "geometry_types.h"

class AcDbBlockTableRecord;

namespace ga::bridge {
// Inspect native XREF status and the authored file's provenance. This does not
// open a side database, load an XREF or replace an editor's loaded database.
XrefDependency inspectXrefDependency(AcDbBlockTableRecord* record);
}
