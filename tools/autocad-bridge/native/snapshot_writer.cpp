#include "snapshot_writer.h"
#include "bridge_config.h"
#include "operation_control.h"
#include "file_io.h"
#include <cstdio>
#include <fstream>
#include <functional>
#include <iomanip>

namespace ga::bridge {

namespace {
bool writeRegionRecord(std::ostream& output, const RegionTopology& region,
                       const NativeAreaProposal* proposal,
                       const std::function<bool()>& cancelled) {
    output << "{\"handle\": \"" << jsonEscape(region.measurement.handle)
           << "\", \"source_layer\": \"" << jsonEscape(region.sourceLayer)
           << "\", \"layer\": \"" << jsonEscape(region.measurement.layer)
           << "\", \"instance_chain\": [";
    for (std::size_t chainIndex = 0;
         chainIndex < region.instanceChain.size(); ++chainIndex) {
        output << (chainIndex == 0 ? "" : ",")
               << "\"" << jsonEscape(region.instanceChain[chainIndex]) << "\"";
    }
    output << "], \"source_handles\": [";
    for (std::size_t memberIndex = 0;
         memberIndex < region.sourceHandles.size(); ++memberIndex) {
        output << (memberIndex == 0 ? "" : ",") << "\""
               << jsonEscape(region.sourceHandles[memberIndex]) << "\"";
    }
    output << ']';
    if (proposal) {
        output << ", \"proposal_method\": \"explicit-chord-closure\""
               << ", \"closure_gap_wcs_xy_units\": "
               << proposal->closureGapWcsXyUnits;
    }
    output << ", \"status\": \"" << (region.resolved ? "native" : "unresolved")
           << "\", \"error_status\": ";
    if (region.resolved) output << "null"; else output << region.errorStatus;
    output << ", \"native_area_units2\": ";
    if (region.measurement.areaValid) output << region.measurement.area;
    else output << "null";
    output << ", \"native_perimeter_units\": ";
    if (region.measurement.perimeterValid) output << region.measurement.perimeter;
    else output << "null";
    output << ", \"loops\": [";
    for (std::size_t loopIndex = 0; loopIndex < region.loops.size(); ++loopIndex) {
        const RegionLoop& loop = region.loops[loopIndex];
        output << (loopIndex == 0 ? "" : ",")
               << "{\"role\": \"" << loop.role
               << "\", \"sampled_max_deviation_units\": "
               << loop.sampledMaximumDeviation << ", \"coordinates\": [";
        for (std::size_t pointIndex = 0;
             pointIndex < loop.coordinates.size(); ++pointIndex) {
            if (pointIndex % 4096 == 0 && cancelled()) return false;
            const Point3& point = loop.coordinates[pointIndex];
            output << (pointIndex == 0 ? "" : ",")
                   << '[' << point.x << ',' << point.y << ',' << point.z << ']';
        }
        output << "]}";
    }
    output << "]}";
    return true;
}
}  // namespace

ExportResult writeTopologySnapshot(const SnapshotMetadata& metadata,
                                   const DrawingGeometry& geometry,
                                   const std::string& destination) {
    ExportResult result;
    const auto& sourcePath = metadata.sourcePath;
    const auto& sourceHash = metadata.sourceHash;
    const auto& regions = geometry.regions;
    const auto& areaProposals = geometry.areaProposals;
    const auto& paths = geometry.paths;
    const auto& points = geometry.points;
    const auto& coverage = geometry.coverage;
    const auto& traversal = geometry.traversal;
    std::size_t resolvedCount = 0;
    std::size_t loopCount = 0;
    std::size_t pointCount = 0;
    for (const RegionTopology& region : regions) {
        if (region.resolved) ++resolvedCount;
        loopCount += region.loops.size();
        for (const RegionLoop& loop : region.loops) pointCount += loop.coordinates.size();
    }
    std::map<std::string, std::size_t> coverageStatusCounts;
    for (const EntityCoverage& record : coverage) {
        ++coverageStatusCounts[record.status];
    }

    // GAOPEN publishes into its private transfer directory. Diagnostic probes
    // still use the historical adjacent sidecar when no destination is given.
    const std::string outputPath = destination.empty()
        ? sourcePath + ".green-atlas.geometry.json" : destination;
    const std::string temporaryPath = outputPath + ".tmp";
    std::ofstream output(temporaryPath, std::ios::binary | std::ios::trunc);
    if (!output) {
        result.error = "topology sidecar cannot be created";
        return result;
    }
    const auto abortSerializationIfCancelled = [&]() {
        if (!operationCancelled()) return false;
        output.close();
        std::remove(temporaryPath.c_str());
        result.error = "native topology export cancelled";
        return true;
    };

    output << std::setprecision(17);
    output << "{\n"
           << "  \"schema\": \"green-atlas.autocad-region-topology-probe/1\",\n"
           << "  \"complete\": false,\n"
           << "  \"plugin_version\": \"" << kPluginVersion << "\",\n"
           << "  \"capture_mode\": \""
           << (metadata.sideDatabaseCapture ? "side_database_dxf" : "live_document")
           << "\",\n"
           << "  \"requested_tolerance_m\": " << kRequestedToleranceMetres << ",\n"
           << "  \"source\": {\n"
           << "    \"path\": \"" << jsonEscape(sourcePath) << "\",\n"
           << "    \"sha256\": \"" << sourceHash << "\",\n"
           << "    \"units_code\": " << static_cast<int>(metadata.unitsCode) << ",\n"
           << "    \"metres_per_unit\": " << metadata.metresPerUnit << ",\n"
           << "    \"document_revision\": \"" << jsonEscape(metadata.revision) << "\",\n"
           << "    \"database_modified_flags\": " << metadata.databaseModificationFlags << ",\n"
           << "    \"live_database_matches_disk\": "
           << (metadata.databaseModificationFlags == 0 ? "true" : "false") << "\n"
           << "  },\n"
           << "  \"xref_dependencies\": [";
    std::size_t dependencyIndex = 0;
    for (const auto& [recordHandle, dependency] : traversal.xrefDependencies) {
        output << (dependencyIndex++ == 0 ? "\n" : ",\n")
               << "    {\"record_handle\": \"" << jsonEscape(recordHandle)
               << "\", \"block_name\": \"" << jsonEscape(dependency.blockName)
               << "\", \"stored_path\": \"" << jsonEscape(dependency.storedPath)
               << "\", \"resolved_path\": \"" << jsonEscape(dependency.resolvedPath)
               << "\", \"sha256\": \"" << jsonEscape(dependency.sha256)
               << "\", \"bytes\": " << dependency.bytes
               << ", \"status\": \"" << dependency.status << "\"}";
    }
    if (!traversal.xrefDependencies.empty()) output << '\n';
    output << "  ],\n"
           << "  \"coverage\": [";

    for (std::size_t coverageIndex = 0;
         coverageIndex < coverage.size(); ++coverageIndex) {
        if (coverageIndex % 10000 == 0 && abortSerializationIfCancelled()) return result;
        const EntityCoverage& record = coverage[coverageIndex];
        output << (coverageIndex == 0 ? "\n" : ",\n")
               << "    {\"handle\": \"" << jsonEscape(record.handle)
               << "\", \"entity_type\": \"" << jsonEscape(record.entityType)
               << "\", \"source_layer\": \"" << jsonEscape(record.sourceLayer)
               << "\", \"layer\": \"" << jsonEscape(record.layer)
               << "\", \"instance_chain\": [";
        for (std::size_t chainIndex = 0;
             chainIndex < record.instanceChain.size(); ++chainIndex) {
            output << (chainIndex == 0 ? "" : ",")
                   << "\"" << jsonEscape(record.instanceChain[chainIndex]) << "\"";
        }
        output << "], \"status\": \"" << record.status
               << "\", \"method\": \"" << record.method
               << "\", \"reason\": ";
        if (record.reason.empty()) output << "null";
        else output << "\"" << jsonEscape(record.reason) << "\"";
        output << ", \"xref_dependency_id\": ";
        if (record.xrefDependencyId.empty()) output << "null";
        else output << "\"" << jsonEscape(record.xrefDependencyId) << "\"";
        output << '}';
    }
    if (!coverage.empty()) output << '\n';
    output << "  ],\n"
           << "  \"regions\": [";

    for (std::size_t regionIndex = 0; regionIndex < regions.size(); ++regionIndex) {
        if (regionIndex % 1000 == 0 && abortSerializationIfCancelled()) return result;
        const RegionTopology& region = regions[regionIndex];
        output << (regionIndex == 0 ? "\n    " : ",\n    ");
        if (!writeRegionRecord(output, region, nullptr,
                               abortSerializationIfCancelled)) return result;
    }
    if (!regions.empty()) output << '\n';
    output << "  ],\n"
           << "  \"area_proposals\": [";
    for (std::size_t index = 0; index < areaProposals.size(); ++index) {
        if (index % 1000 == 0 && abortSerializationIfCancelled()) return result;
        output << (index == 0 ? "\n    " : ",\n    ");
        if (!writeRegionRecord(output, areaProposals[index].preview,
                               &areaProposals[index],
                               abortSerializationIfCancelled)) return result;
    }
    if (!areaProposals.empty()) output << '\n';
    output << "  ],\n"
           << "  \"area_proposal_rejections\": [";
    for (std::size_t index = 0;
         index < traversal.areaProposalRejections.size(); ++index) {
        const auto& rejection = traversal.areaProposalRejections[index];
        output << (index == 0 ? "\n    " : ",\n    ")
               << "{\"handle\": \"" << jsonEscape(rejection.handle)
               << "\", \"layer\": \"" << jsonEscape(rejection.layer)
               << "\", \"instance_chain\": [";
        for (std::size_t chainIndex = 0;
             chainIndex < rejection.instanceChain.size(); ++chainIndex) {
            output << (chainIndex == 0 ? "" : ",") << "\""
                   << jsonEscape(rejection.instanceChain[chainIndex]) << "\"";
        }
        output << "], \"closure_gap_wcs_xy_units\": "
               << rejection.gapWcsXyUnits << ", \"stage\": \""
               << jsonEscape(rejection.stage) << "\", \"native_status\": "
               << rejection.nativeStatus << '}';
    }
    if (!traversal.areaProposalRejections.empty()) output << '\n';
    output << "  ],\n"
           << "  \"paths\": [";
    for (std::size_t pathIndex = 0; pathIndex < paths.size(); ++pathIndex) {
        if (pathIndex % 1000 == 0 && abortSerializationIfCancelled()) return result;
        const NativePath& path = paths[pathIndex];
        output << (pathIndex == 0 ? "\n" : ",\n")
               << "    {\"handle\": \"" << jsonEscape(path.handle)
               << "\", \"source_layer\": \"" << jsonEscape(path.sourceLayer)
               << "\", \"layer\": \"" << jsonEscape(path.layer)
               << "\", \"instance_chain\": [";
        for (std::size_t chainIndex = 0;
             chainIndex < path.instanceChain.size(); ++chainIndex) {
            output << (chainIndex == 0 ? "" : ",")
                   << "\"" << jsonEscape(path.instanceChain[chainIndex]) << "\"";
        }
        output << "], \"status\": \"native\", \"error_status\": null"
               << ", \"closed\": " << (path.closed ? "true" : "false")
               << ", \"sampled_max_deviation_units\": "
               << path.sampledMaximumDeviation
               << ", \"coordinates\": [";
        for (std::size_t pointIndex = 0;
             pointIndex < path.coordinates.size(); ++pointIndex) {
            if (pointIndex % 4096 == 0 && abortSerializationIfCancelled()) return result;
            const Point3& point = path.coordinates[pointIndex];
            output << (pointIndex == 0 ? "" : ",")
                   << '[' << point.x << ',' << point.y << ',' << point.z << ']';
        }
        output << "]}";
    }
    if (!paths.empty()) output << '\n';
    output << "  ],\n"
           << "  \"points\": [";
    for (std::size_t pointIndex = 0; pointIndex < points.size(); ++pointIndex) {
        if (pointIndex % 10000 == 0 && abortSerializationIfCancelled()) return result;
        const NativePoint& point = points[pointIndex];
        output << (pointIndex == 0 ? "\n" : ",\n")
               << "    {\"handle\": \"" << jsonEscape(point.handle)
               << "\", \"source_layer\": \"" << jsonEscape(point.sourceLayer)
               << "\", \"layer\": \"" << jsonEscape(point.layer)
               << "\", \"instance_chain\": [";
        for (std::size_t chainIndex = 0;
             chainIndex < point.instanceChain.size(); ++chainIndex) {
            output << (chainIndex == 0 ? "" : ",")
                   << "\"" << jsonEscape(point.instanceChain[chainIndex]) << "\"";
        }
        output << "], \"status\": \"native\", \"error_status\": null"
               << ", \"coordinates\": ["
               << point.coordinates.x << ',' << point.coordinates.y << ','
               << point.coordinates.z << "]}";
    }
    if (!points.empty()) output << '\n';
    output << "  ],\n"
           << "  \"summary\": {\"regions\": " << regions.size()
           << ", \"area_proposals\": " << areaProposals.size()
           << ", \"area_proposal_candidates\": "
           << traversal.areaProposalCandidates
           << ", \"area_proposal_rejected\": "
           << traversal.areaProposalRejected
           << ", \"paths\": " << paths.size()
           << ", \"points\": " << points.size()
           << ", \"resolved\": " << resolvedCount
           << ", \"unresolved\": " << (regions.size() - resolvedCount)
           << ", \"loops\": " << loopCount
           << ", \"region_sampled_points\": " << pointCount
           << ", \"block_references\": " << traversal.blockReferences
           << ", \"traversed_block_references\": " << traversal.traversedBlockReferences
           << ", \"cyclic_block_references\": " << traversal.cyclicBlockReferences
           << ", \"xref_block_references\": " << traversal.xrefBlockReferences
           << ", \"unloaded_xref_block_references\": "
           << traversal.unloadedXrefBlockReferences
           << ", \"unresolved_xref_block_references\": "
           << traversal.unresolvedXrefBlockReferences
           << ", \"xref_dependency_records\": "
           << traversal.xrefDependencies.size()
           << ", \"expanded_minsert_cells\": "
           << traversal.expandedMInsertCells
           << ", \"unexpanded_minsert_blocks\": "
           << traversal.unexpandedMInsertBlocks
           << ", \"unreadable_block_records\": " << traversal.unreadableBlockRecords
           << ", \"unreadable_entities\": " << traversal.unreadableEntities
           << ", \"visited_entities\": " << traversal.visitedEntities
           << ", \"source_instances\": " << coverage.size()
           << ", \"native\": " << coverageStatusCounts["native"]
           << ", \"context\": " << coverageStatusCounts["context"]
           << ", \"unresolved_instances\": " << coverageStatusCounts["unresolved"]
           << "},\n"
           << "  \"limitations\": ["
              "\"REGION, HATCH area, finite curves and points are emitted; other non-context entities remain explicit unresolved coverage\", "
              "\"resolved XREF files are hashed and unresolved references fail admission\", "
              "\"nonzero DBMOD means the saved source differs from the live capture\", "
              "\"adaptive chord checks require independent admission verification\"]\n"
           << "}\n";
    output.close();

    if (!output || std::rename(temporaryPath.c_str(), outputPath.c_str()) != 0) {
        std::remove(temporaryPath.c_str());
        result.error = "topology sidecar could not be published atomically";
        return result;
    }

    result.success = true;
    result.path = outputPath;
    result.error.clear();


    return result;
}

}  // namespace ga::bridge
