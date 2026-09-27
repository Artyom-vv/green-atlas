#pragma once
#include "brvtx.h"
#include <iosfwd>
#include <vector>
class AcBrLoopEdgeTraverser;
class AcGeCurve3d;
void emitEdgeProbe(std::ostream&, const AcBrLoopEdgeTraverser&,
                   const AcGeCurve3d*, std::vector<AcBrVertex>&);
