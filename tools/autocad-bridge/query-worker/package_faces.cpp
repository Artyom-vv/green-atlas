#include "package_faces.h"
#include "native_face_catalog.h"
#include "xref_instance_access.h"
#include "file_io.h"
#include "filesystem_utf8.h"
#include "AcString.h"
#include "dbhatch.h"
#include <fstream>
#include <filesystem>
#include <iomanip>
#include <locale>
#include <sstream>
#include <stdexcept>

namespace ga::queryWorker {
namespace {
constexpr std::size_t kMaxFaceRequestBytes=96*1024*1024;
constexpr double kCaptureMatrixTolerance=1e-8; // Same identity check as native instance access.
std::string line(std::istream& input) {
    std::string value;
    if(!std::getline(input,value)||value.empty()||value.size()>4096
        ||value.find('\r')!=std::string::npos)
        throw std::runtime_error("invalid captured face request line");
    return value;
}
template<class... T> void numbers(const std::string& value,T&... fields) {
    std::istringstream input(value);input.imbue(std::locale::classic());
    if(!(input>>...>>fields)) throw std::runtime_error("invalid captured face request values");
    input>>std::ws;if(!input.eof()) throw std::runtime_error("extra captured face request values");
}
std::string q(const std::string& value) {return "\""+ga::bridge::jsonEscape(value)+"\"";}
}
void prepareArchivedFaces(AcDbDatabase& database,const std::string& path,bool retainCatalogue) {
    if(ga::bridge::fileSize(path)>kMaxFaceRequestBytes)
        throw std::runtime_error("captured face request exceeds limit");
    std::ifstream input(ga::bridge::nativePath(path));
    const auto protocol=line(input);
    const bool typedTopology=protocol=="green-atlas.archive-face-preparation/3";
    const bool separateLayers=typedTopology||protocol=="green-atlas.archive-face-preparation/2";
    if(!separateLayers&&protocol!="green-atlas.archive-face-preparation/1")
        throw std::runtime_error("captured face protocol mismatch");
    const auto session=line(input), sourceSha=line(input);
    if(session.size()!=32||session.find_first_not_of("0123456789abcdef")!=std::string::npos
        ||sourceSha.size()!=64||sourceSha.find_first_not_of("0123456789abcdef")!=std::string::npos)
        throw std::runtime_error("captured face identity invalid");
    const ACHAR* filename=nullptr;
    if(database.getFilename(filename)!=Acad::eOk||!filename
        ||ga::bridge::sha256File(AcString(filename).utf8Str())!=sourceSha)
        throw std::runtime_error("captured face DWG identity changed");
    int units;std::size_t layerCount,rowCount,companionCount=0;
    if(typedTopology) numbers(line(input),units,layerCount,companionCount,rowCount);
    else numbers(line(input),units,layerCount,rowCount);
    if(units!=int(database.insunits())||layerCount>4096||companionCount>4096||rowCount>1000000)
        throw std::runtime_error("captured face units or count invalid");
    std::set<std::string> layers;
    for(std::size_t i=0;i<layerCount;++i)
        if(!layers.insert(line(input)).second) throw std::runtime_error("duplicate captured face layer");
    std::set<std::string> companions;
    for(std::size_t i=0;i<companionCount;++i) {
        const auto name=line(input);
        if(layers.count(name)||!companions.insert(name).second)
            throw std::runtime_error("ambiguous captured face companion");
    }
    ga::bridge::DrawingGeometry drawing;
    std::map<std::string,std::string> aliases;
    std::set<std::string> targets;
    for(std::size_t i=0;i<rowCount;++i) {
        const auto original=line(input), archive=line(input), type=line(input), authoredLayer=line(input);
        const auto effectiveLayer=separateLayers?line(input):authoredLayer;
        if(typedTopology&&!layers.count(effectiveLayer)&&!companions.count(effectiveLayer))
            throw std::runtime_error("captured face has an unrequested effective layer");
        int authored; numbers(line(input),authored);
        if((authored!=0&&authored!=1)||!aliases.emplace(original,archive).second
            ||!targets.insert(archive).second) throw std::runtime_error("ambiguous captured face identity");
        ga::xref::Instance instance;ga::xref::resolve(database,archive,instance);
        if(type!=AcString(instance.entity->isA()->name()).utf8Str())
            throw std::runtime_error("captured face class changed");
        if(typedTopology&&!AcDbCurve::cast(instance.entity)
            &&!(companions.count(effectiveLayer)
                &&(AcDbRegion::cast(instance.entity)||AcDbHatch::cast(instance.entity))))
            throw std::runtime_error("captured face has no supported native shape");
        AcString actualLayer;
        if(instance.entity->layer(actualLayer)!=Acad::eOk||authoredLayer!=actualLayer.utf8Str())
            throw std::runtime_error("captured face layer changed");
        for(int r=0;r<4;++r) {
            double values[4];numbers(line(input),values[0],values[1],values[2],values[3]);
            for(int c=0;c<4;++c)
                if(!std::isfinite(values[c])||std::abs(values[c]-instance.transform(r,c))>kCaptureMatrixTolerance)
                    throw std::runtime_error("captured face transform changed");
        }
        const auto slash=original.find_last_of('/');
        ga::bridge::EntityCoverage row;
        row.handle=slash==std::string::npos?original:original.substr(slash+1);
        std::istringstream parents(slash==std::string::npos?"":original.substr(0,slash));
        std::string parent;
        while(std::getline(parents,parent,'/')) row.instanceChain.push_back(parent);
        row.sourceLayer=authoredLayer;row.layer=effectiveLayer;row.entityType=type;
        drawing.coverage.push_back(row);
        if(authored) {
            ga::bridge::RegionTopology region;
            region.measurement.handle=row.handle;region.instanceChain=row.instanceChain;
            region.resolved=true;drawing.regions.push_back(std::move(region));
        }
    }
    input>>std::ws;if(!input.eof()) throw std::runtime_error("extra captured face request content");
    struct Clear {bool active=true;~Clear(){if(active) ga::faces::clearCatalog();}} clear;
    ga::faces::rememberFaceSources(database,drawing,aliases);
    ga::faces::prepareFaceLayers(database,layers,{},typedTopology?&companions:nullptr);
    std::ostringstream out;out<<std::setprecision(17)<<"{\"session_id\":"<<q(session)
        <<",\"request_sha256\":"<<q(ga::bridge::sha256File(path))<<",\"layers\":[";
    bool first=true;for(const auto& layer:layers){out<<(first?"":",")<<q(layer);first=false;}
    out<<']';ga::faces::emitCatalog(out,layers);out<<'}';
    const auto output=ga::bridge::utf8Path(ga::bridge::nativePath(path).parent_path()
        /(retainCatalogue?"faces.json":"native.json"));
    if(!ga::bridge::writeAtomicText(output,out.str()))
        throw std::runtime_error("captured face publication failed");
    clear.active=!retainCatalogue;
}
}
