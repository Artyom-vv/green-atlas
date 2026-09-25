#include "live_query_transport.h"
#include "live_query_session.h"
#include "delivery_command.h"
#include "mcp_transport.h"
#include "file_io.h"
#include "aced.h"
#include "adslib.h"
#include "AcString.h"
#include <chrono>
#include <ctime>
#include <dirent.h>
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <sys/stat.h>
#include <unistd.h>
#include <mach-o/dyld.h>
#include <thread>
#include <signal.h>

namespace ga::liveQuery {
namespace {
constexpr const char* kProtocol="green-atlas.live-query/1";
bool busy=false;
bool hexId(const std::string& value) {
    return value.size()==32 && value.find_first_not_of("0123456789abcdef")==std::string::npos;
}
std::string directory() {
    // Two AutoCAD processes must never consume each other's open/query request.
    return "/tmp/green-atlas-live-query-"+std::to_string(getuid())+"-"+std::to_string(getpid());
}
bool ensureDirectory() {
    const auto path=directory();
    struct stat st{};
    if(lstat(path.c_str(),&st)!=0) {
        if(mkdir(path.c_str(),0700)!=0) return false;
        if(lstat(path.c_str(),&st)!=0) return false;
    }
    return S_ISDIR(st.st_mode) && st.st_uid==getuid() && (st.st_mode&077)==0;
}
std::string q(const std::string& s) { return "\""+ga::bridge::jsonEscape(s)+"\""; }
void process(const std::string& id) {
    if(!hexId(id) || !ensureDirectory() || busy || gaDeliveryActive()
        || ga::bridge::mcpRequestInProgress()) return;
    const auto base=directory()+"/";
    const auto input=base+"request-"+id+".txt", working=base+"processing-"+id+".txt";
    if(rename(input.c_str(),working.c_str())!=0) return;
    busy=true;
    struct Done { ~Done(){busy=false;} } done;
    std::string payload, error;
    try {
        if(ga::bridge::fileSize(working)>512) throw std::runtime_error("live_request_too_large");
        std::ifstream in(working);
        std::string schema,op,token,deadlineText,extra;
        if(!std::getline(in,schema)||!std::getline(in,op)||!std::getline(in,token)
            ||!std::getline(in,deadlineText)||std::getline(in,extra)||schema!=kProtocol
            ||!hexId(token)) throw std::runtime_error("live_invalid_request");
        std::size_t used=0;
        const auto deadline=std::stoll(deadlineText,&used);
        const auto now=std::time(nullptr);
        if(used!=deadlineText.size() || deadline<now || deadline>now+900)
            throw std::runtime_error("live_request_expired");
        if(access((base+"cancel-"+id).c_str(),F_OK)==0)
            throw std::runtime_error("live_request_cancelled");
        if(op=="open") {
            if(token!=id) throw std::runtime_error("live_invalid_open_identity");
            const auto snapshot=base+"map-"+id+".json";
            if(access(snapshot.c_str(),F_OK)==0) throw std::runtime_error("live_output_exists");
            payload=openSession(token,snapshot);
        } else if(op=="inspect") payload=inspectSession(token);
        else if(op=="prepare_faces") {
            prepareSessionFaces(token,base+"layers-"+id+".txt",base+"faces-"+id+".json");
            payload=inspectSession(token);
        }
        else if(op=="query") {
            querySession(token,base+"objects-"+id+".txt");
            payload=inspectSession(token);
        } else throw std::runtime_error("live_invalid_operation");
    } catch(const std::exception& failure) { error=failure.what(); }
    const auto reply=std::string("{\"schema\":")+q(kProtocol)+",\"request_id\":"+q(id)
        +",\"ok\":"+(error.empty()?"true":"false")+",\"error\":"+q(error)
        +",\"session\":"+(error.empty()?payload:"null")+"}";
    ga::bridge::writeAtomicText(base+"reply-"+id+".json",reply);
    std::remove(working.c_str());
}
}
void pollRequests() {
    static auto next=std::chrono::steady_clock::now();
    const auto now=std::chrono::steady_clock::now();
    if(busy || now<next || gaDeliveryActive() || ga::bridge::mcpRequestInProgress()) return;
    next=now+std::chrono::milliseconds(100);
    if(!ensureDirectory()) return;
    DIR* dir=opendir(directory().c_str());
    if(!dir) return;
    std::string id;
    while(const auto* entry=readdir(dir)) {
        const std::string name=entry->d_name;
        if(name.size()==44 && name.substr(0,8)=="request-" && name.substr(40)==".txt"
            && hexId(name.substr(8,32))) { id=name.substr(8,32); break; }
    }
    closedir(dir);
    if(!id.empty()) process(id);
}
void requestCommand() {
    ACHAR id[128]={};
    if(acedGetString(0,_T("\nLive request id: "),id)==RTNORM)
        process(AcString(id).utf8Str());
}
void consoleDemoLoop() {
    // Test runner only: never occupy the user's GUI document/message loop.
    char executable[4096]; uint32_t length=sizeof(executable);
    if(_NSGetExecutablePath(executable,&length)!=0
        ||std::string(executable).find("/AcCoreConsole.app/")==std::string::npos) {
        acutPrintf(_T("\nGreen Atlas: this command is only for the isolated Core demo runner."));
        return;
    }
    if(!ensureDirectory()) return;
    const auto stop=directory()+"/stop-live-demo";
    // The supervising runner owns lifetime, not an invisible user deadline.
    // An orphan must still release AutoCAD when that runner exits/crashes.
    const auto supervisor=getppid();
    while(access(stop.c_str(),F_OK)!=0 && supervisor>1
        && getppid()==supervisor && kill(supervisor,0)==0) {
        pollRequests();
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
    closeSession();
}
}
