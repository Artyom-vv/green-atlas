#pragma once
#include "planar_faces.h"
#include <array>
#include <map>

namespace ga::faces {
inline constexpr double kEquality = ga::nativeQuery::kNativeJoinTolerance;
struct Input {
    std::unique_ptr<AcDbCurve> curve;
    std::set<std::string> routes;
    bool clipping = false, repair = false;
    AcDbExtents bounds;
    double first = 0, last = 0;
    std::vector<double> cuts;
};
struct Edge {
    Fragment fragment;
    int a = 0, b = 0;
    double length = 0, integral = 0;
};
struct Graph {
    std::multimap<double, int> nodeIndex;
    std::vector<AcGePoint3d> nodes;
    std::vector<std::vector<std::pair<double, int>>> outgoing;
    std::vector<Edge> edges;
    std::set<std::string> needsArea;
    int node(const AcGePoint3d& point);
    void add(Fragment fragment);
};
void require(Acad::ErrorStatus status, const char* operation);
void checkCancelled(const std::function<bool()>& cancelled);
Input input(std::unique_ptr<AcDbCurve> curve, const std::set<std::string>& routes,
            bool clipping, bool repair = false);
std::vector<Input> primitives(std::vector<Source>& sources, Result& result,
                              const std::function<bool()>& cancelled);
std::vector<Connector> connect(std::vector<Input>& inputs, double gap,
                               const std::function<bool()>& cancelled);
Graph intersectAndSplit(std::vector<Input>& inputs, Result& result,
                        const std::function<bool()>& cancelled);
void collectFaces(Graph& graph, const std::vector<Connector>& repairs,
                  Result& result, const std::function<bool()>& cancelled);
}
