#pragma once
#include <cstddef>
#include <map>
#include <string>
#include <vector>

namespace ga::bridge {

struct RegionMeasurement {
    std::string handle;
    std::string layer;
    double area = 0.0;
    double perimeter = 0.0;
    bool areaValid = false;
    bool perimeterValid = false;
};

struct Point3 {
    double x = 0.0;
    double y = 0.0;
    double z = 0.0;
};

struct RegionLoop {
    std::string role;
    std::vector<Point3> coordinates;
    double sampledMaximumDeviation = 0.0;
    // Native BRep ownership, used by boolean-domain display export. Existing
    // snapshot serialization remains unchanged.
    std::size_t faceIndex = 0;
};

struct RegionTopology {
    RegionMeasurement measurement;
    std::string sourceLayer;
    std::vector<std::string> instanceChain;
    // The coverage method records the actual native HATCH extraction route.
    std::string extractionMethod;
    // Present only for a native region derived from multiple original curves.
    std::vector<std::string> sourceHandles;
    std::vector<RegionLoop> loops;
    bool resolved = false;
    int errorStatus = 0;
};

// A region measured on a transient AutoCAD clone. It is evidence for a
// user-facing decision, never an authored or immediately active surface.
struct NativeAreaProposal {
    RegionTopology preview;
    double closureGapWcsXyUnits = 0.0;
};

struct NativePath {
    std::string handle;
    std::string sourceLayer;
    std::string layer;
    std::vector<std::string> instanceChain;
    std::vector<Point3> coordinates;
    bool closed = false;
    bool resolved = false;
    bool calculationContext = false;
    int errorStatus = 0;
    double sampledMaximumDeviation = 0.0;
    std::string method;
    std::string reason;
};

struct NativePoint {
    std::string handle;
    std::string sourceLayer;
    std::string layer;
    std::vector<std::string> instanceChain;
    Point3 coordinates;
};

struct EntityCoverage {
    std::string handle;
    std::string entityType;
    std::string sourceLayer;
    std::string layer;
    std::vector<std::string> instanceChain;
    std::string status;
    std::string method;
    std::string reason;
    std::string xrefDependencyId;
};

struct XrefDependency {
    std::string recordHandle;
    std::string blockName;
    std::string storedPath;
    std::string resolvedPath;
    std::string sha256;
    std::size_t bytes = 0;
    std::string status;
};

struct AreaProposalRejection {
    std::string handle;
    std::vector<std::string> instanceChain;
    std::string layer;
    double gapWcsXyUnits = 0.0;
    std::string stage;
    int nativeStatus = 0;
};

struct RegionTraversalDiagnostics {
    std::size_t visitedEntities = 0;
    std::size_t blockReferences = 0;
    std::size_t traversedBlockReferences = 0;
    std::size_t cyclicBlockReferences = 0;
    std::size_t xrefBlockReferences = 0;
    std::size_t unloadedXrefBlockReferences = 0;
    std::size_t unresolvedXrefBlockReferences = 0;
    std::size_t expandedMInsertCells = 0;
    std::size_t unexpandedMInsertBlocks = 0;
    std::size_t unreadableBlockRecords = 0;
    std::size_t unreadableEntities = 0;
    std::size_t areaProposalCandidates = 0;
    std::size_t areaProposalRejected = 0;
    std::vector<AreaProposalRejection> areaProposalRejections;
    std::map<std::string, XrefDependency> xrefDependencies;
};

struct DrawingGeometry {
    std::vector<RegionTopology> regions;
    std::vector<NativeAreaProposal> areaProposals;
    std::vector<NativePath> paths;
    std::vector<NativePoint> points;
    std::vector<EntityCoverage> coverage;
    RegionTraversalDiagnostics traversal;
};

}  // namespace ga::bridge
