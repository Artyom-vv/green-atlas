// Use only the typed value-array overload. The edge-pointer overload entered
// AutoCAD's fatal handler on real elliptical loops and remains quarantined.
bool hatchPointOnSegment(const Point3& point, const Point3& left,
                         const Point3& right, const double tolerance) {
    const double cross = (right.x - left.x) * (point.y - left.y) -
                         (right.y - left.y) * (point.x - left.x);
    if (std::abs(cross) > tolerance * std::max(1.0, pointDistance(left, right)))
        return false;
    return point.x >= std::min(left.x, right.x) - tolerance &&
           point.x <= std::max(left.x, right.x) + tolerance &&
           point.y >= std::min(left.y, right.y) - tolerance &&
           point.y <= std::max(left.y, right.y) + tolerance;
}

bool hatchLoopContainsPoint(const std::vector<Point3>& loop,
                            const Point3& point, const double tolerance) {
    bool inside = false;
    for (std::size_t index = 1; index < loop.size(); ++index) {
        const Point3& left = loop[index - 1];
        const Point3& right = loop[index];
        if (hatchPointOnSegment(point, left, right, tolerance)) return true;
        const bool crosses = (left.y > point.y) != (right.y > point.y);
        if (crosses && point.x <
            (right.x - left.x) * (point.y - left.y) /
                (right.y - left.y) + left.x) inside = !inside;
    }
    return inside;
}

double hatchLoopArea(const std::vector<Point3>& loop) {
    double twiceArea = 0.0;
    for (std::size_t index = 1; index < loop.size(); ++index)
        twiceArea += loop[index - 1].x * loop[index].y -
                     loop[index].x * loop[index - 1].y;
    return std::abs(twiceArea) * 0.5;
}

double hatchLoopPerimeter(const std::vector<Point3>& loop) {
    double result = 0.0;
    for (std::size_t index = 1; index < loop.size(); ++index)
        result += pointDistance(loop[index - 1], loop[index]);
    return result;
}

bool extractPolylineHatchTopology(const AcDbHatch* hatch,
                                  const AcGeMatrix3d& transform,
                                  const double tolerance,
                                  RegionTopology& result,
                                  std::string& reason) {
    const int loopCount = hatch->numLoops();
    if (loopCount < 1 || static_cast<std::size_t>(loopCount) >
            kMaximumTopologyElementsPerEntity) {
        reason = "HATCH has an invalid or oversized loop count";
        return false;
    }
    bool containsEdgeLoop = false;
    for (int loopIndex = 0; loopIndex < loopCount; ++loopIndex) {
        if (!(hatch->loopTypeAt(loopIndex) & AcDbHatch::kPolyline)) {
            containsEdgeLoop = true;
            break;
        }
    }
    if (containsEdgeLoop) {
        // Ask AutoCAD's own geometry kernel for the evaluated hatch area. This
        // is a typed, owned Region result and safely covers non-associative
        // line/arc/ellipse/spline edge loops. Do not use the legacy
        // untyped edge-pointer overload: malformed real-world elliptic loops
        // can enter AutoCAD's fatal handler before callers can validate them.
        AcDbRegion* areaRegion = hatch->getRegionArea();
        if (areaRegion != nullptr) {
            RegionTopology areaTopology;
            areaTopology.measurement.handle = result.measurement.handle;
            areaTopology.measurement.layer = result.measurement.layer;
            areaTopology.sourceLayer = result.sourceLayer;
            areaTopology.instanceChain = result.instanceChain;
            const bool extracted = extractRegionTopology(
                areaRegion, transform, tolerance, areaTopology);
            delete areaRegion;
            if (extracted && areaTopology.resolved &&
                !areaTopology.loops.empty()) {
                result = std::move(areaTopology);
                reason.clear();
                return true;
            }
        }
    }
    std::vector<double> areas;
    double perimeter = 0.0;
    for (int loopIndex = 0; loopIndex < loopCount; ++loopIndex) {
        if (mcpCancellationRequested()) return false;
        const auto type = hatch->loopTypeAt(loopIndex);
        NativePath sampled;
        if (!(type & AcDbHatch::kPolyline)) {
            AcDbObjectIdArray associatedObjects;
            const Acad::ErrorStatus associationStatus =
                hatch->getAssocObjIdsAt(loopIndex, associatedObjects);
            if (associationStatus != Acad::eOk ||
                associatedObjects.length() != 1) {
                reason = "HATCH edge-curve loop has no single associative "
                         "boundary object (loop=" +
                         std::to_string(loopIndex) + ", type=" +
                         std::to_string(static_cast<long long>(type)) +
                         ", associative=" +
                         std::string(hatch->associative() ? "true" : "false") +
                         ", boundary_objects=" +
                         std::to_string(
                             associationStatus == Acad::eOk
                                 ? associatedObjects.length()
                                 : 0) +
                         ", association_status=" +
                         std::to_string(static_cast<int>(associationStatus)) + ")";
                return false;
            }
            AcDbEntity* boundaryEntity = nullptr;
            const Acad::ErrorStatus openStatus = acdbOpenObject(
                boundaryEntity, associatedObjects[0], AcDb::kForRead);
            if (openStatus == Acad::eOk && boundaryEntity != nullptr) {
                if (AcDbRegion* boundaryRegion =
                        AcDbRegion::cast(boundaryEntity)) {
                    RegionTopology boundaryTopology;
                    const bool extracted = extractRegionTopology(
                        boundaryRegion, transform, tolerance, boundaryTopology);
                    boundaryEntity->close();
                    if (!extracted || boundaryTopology.loops.empty()) {
                        reason = "HATCH associative AcDbRegion boundary could "
                                 "not be traversed safely";
                        return false;
                    }
                    for (RegionLoop& regionLoop : boundaryTopology.loops) {
                        const double area = hatchLoopArea(regionLoop.coordinates);
                        if (!std::isfinite(area) ||
                            area <= tolerance * tolerance) {
                            reason = "HATCH associative AcDbRegion boundary has "
                                     "no usable projected area";
                            return false;
                        }
                        perimeter += hatchLoopPerimeter(regionLoop.coordinates);
                        areas.push_back(area);
                        result.loops.push_back(std::move(regionLoop));
                    }
                    continue;
                }
            }
            const AcDbCurve* boundaryCurve =
                openStatus == Acad::eOk && boundaryEntity != nullptr
                    ? AcDbCurve::cast(boundaryEntity)
                    : nullptr;
            const bool extracted = boundaryCurve != nullptr &&
                extractDatabaseCurve(
                    boundaryCurve, transform, tolerance, sampled) &&
                sampled.resolved && sampled.closed;
            const std::string boundaryType = boundaryEntity == nullptr
                ? "unavailable"
                : utf8(boundaryEntity->isA()->name());
            if (boundaryEntity != nullptr) boundaryEntity->close();
            if (!extracted) {
                reason = "HATCH associative boundary could not be extracted "
                         "as one closed curve (type=" + boundaryType +
                         ", open_status=" +
                         std::to_string(static_cast<int>(openStatus)) +
                         ", detail=" + sampled.reason + ")";
                return false;
            }
        } else {
            if (type & (AcDbHatch::kNotClosed | AcDbHatch::kSelfIntersecting)) {
                reason = "AutoCAD marks the HATCH boundary open or self-intersecting";
                return false;
            }
            Adesk::Int32 returnedType = 0;
            AcGePoint2dArray vertices;
            AcGeDoubleArray bulges;
            const auto status = hatch->getLoopAt(
                loopIndex, returnedType, vertices, bulges);
            if (status != Acad::eOk || vertices.length() < 2 ||
                vertices.length() > kMaximumTopologyElementsPerEntity ||
                (!bulges.isEmpty() && bulges.length() != vertices.length())) {
                result.errorStatus = static_cast<int>(
                    status == Acad::eOk ? Acad::eInvalidInput : status);
                reason = "AutoCAD returned an invalid or oversized HATCH polyline boundary";
                return false;
            }
            AcDbPolyline boundary(vertices.length());
            if (boundary.setNormal(hatch->normal()) != Acad::eOk ||
                !std::isfinite(hatch->elevation())) {
                reason = "AutoCAD could not reproduce the HATCH boundary plane";
                return false;
            }
            boundary.setElevation(hatch->elevation());
            for (unsigned int index = 0; index < vertices.length(); ++index) {
                const double bulge = bulges.isEmpty() ? 0 : bulges[index];
                if (!std::isfinite(vertices[index].x) ||
                    !std::isfinite(vertices[index].y) || !std::isfinite(bulge) ||
                    boundary.addVertexAt(index, vertices[index], bulge) != Acad::eOk) {
                    reason = "HATCH polyline has invalid vertex values";
                    return false;
                }
            }
            boundary.setClosed(true);
            if (!extractLightweightPolyline(
                    &boundary, transform, tolerance, sampled) || !sampled.closed) {
                result.errorStatus = sampled.errorStatus;
                reason = "HATCH polyline could not be sampled as a closed boundary: " +
                         sampled.reason;
                return false;
            }
        }
        const double area = hatchLoopArea(sampled.coordinates);
        if (!std::isfinite(area) || area <= tolerance * tolerance) {
            reason = "HATCH polyline boundary has no usable projected area";
            return false;
        }
        RegionLoop loop;
        loop.coordinates = std::move(sampled.coordinates);
        loop.sampledMaximumDeviation = sampled.sampledMaximumDeviation;
        perimeter += hatchLoopPerimeter(loop.coordinates);
        areas.push_back(area);
        result.loops.push_back(std::move(loop));
    }
    const auto largest = std::max_element(areas.begin(), areas.end());
    const std::size_t outerIndex = static_cast<std::size_t>(largest - areas.begin());
    result.loops[outerIndex].role = "outer";
    double area = areas[outerIndex];
    for (std::size_t index = 0; index < result.loops.size(); ++index) {
        if (index == outerIndex) continue;
        const auto& candidate = result.loops[index].coordinates;
        const bool contained = std::all_of(
            candidate.begin(), candidate.end() - 1,
            [&](const Point3& point) {
                return hatchLoopContainsPoint(
                    result.loops[outerIndex].coordinates, point, tolerance);
            });
        if (!contained) {
            reason = "HATCH contains multiple disjoint outer boundaries";
            result.loops.clear();
            return false;
        }
        result.loops[index].role = "hole";
        area -= areas[index];
    }
    if (!std::isfinite(area) || area <= 0.0 ||
        !std::isfinite(perimeter) || perimeter <= 0.0) {
        reason = "HATCH loops do not form one positive surface";
        result.loops.clear();
        return false;
    }
    result.measurement.area = area;
    result.measurement.perimeter = perimeter;
    result.measurement.areaValid = true;
    result.measurement.perimeterValid = true;
    result.resolved = true;
    result.errorStatus = 0;
    return true;
}
