#pragma once
#include <iosfwd>
class AcBrBrep;
// Diagnostic metadata and fixture samples only. Never a product polygon export.
void emitTopologyProbe(std::ostream& out, const AcBrBrep& brep);
