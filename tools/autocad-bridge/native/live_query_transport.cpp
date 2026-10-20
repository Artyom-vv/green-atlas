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
#ifdef _WIN32
#include "local_platform.h"
#include "filesystem_utf8.h"
#include <windows.h>
#include <tlhelp32.h>
#else
#include <dirent.h>
#include <sys/stat.h>
#include <unistd.h>
#include <mach-o/dyld.h>
#endif
#include <thread>
#include <signal.h>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <stdexcept>

namespace ga::liveQuery {
namespace {
constexpr const char* kProtocol="green-atlas.live-query/1";
bool busy=false;
bool hexId(const std::string& value) {
    return value.size()==32 && value.find_first_not_of("0123456789abcdef")==std::string::npos;
}
std::string directory() {
#ifdef _WIN32
    return ga::platform::liveQueueDirectory();
#else
    // Two AutoCAD processes must never consume each other's open/query request.
    return "/tmp/green-atlas-live-query-"+std::to_string(getuid())+"-"+std::to_string(getpid());
#endif
}
bool ensureDirectory() {
#ifdef _WIN32
    return ga::platform::ensurePrivateDirectory(directory());
#else
    const auto path=directory();
    struct stat st{};
    if(lstat(path.c_str(),&st)!=0) {
        if(mkdir(path.c_str(),0700)!=0) return false;
        if(lstat(path.c_str(),&st)!=0) return false;
    }
    return S_ISDIR(st.st_mode) && st.st_uid==getuid() && (st.st_mode&077)==0;
#endif
}
std::string q(const std::string& s) { return "\""+ga::bridge::jsonEscape(s)+"\""; }
void process(const std::string& id) {
    if(!hexId(id) || !ensureDirectory() || busy || gaDeliveryActive()
        || ga::bridge::mcpRequestInProgress()) return;
    const auto base=directory()+"/";
    const auto input=base+"request-"+id+".txt", working=base+"processing-"+id+".txt";
#ifdef _WIN32
    if(!ga::platform::renameFile(input,working)) return;
#else
    if(rename(input.c_str(),working.c_str())!=0) return;
#endif
    busy=true;
    struct Done { ~Done(){busy=false;} } done;
    std::string payload, error;
    try {
        if(ga::bridge::fileSize(working)>512) throw std::runtime_error("live_request_too_large");
#ifdef _WIN32
        if(!ga::platform::directPath(working)) throw std::runtime_error("live_invalid_path");
        std::istringstream in(ga::platform::readBoundedFile(working,512));
#else
        std::ifstream in(working);
#endif
        std::string schema,op,token,deadlineText,extra;
        if(!std::getline(in,schema)||!std::getline(in,op)||!std::getline(in,token)
            ||!std::getline(in,deadlineText)||std::getline(in,extra)||schema!=kProtocol
            ||!hexId(token)) throw std::runtime_error("live_invalid_request");
        std::size_t used=0;
        const auto deadline=std::stoll(deadlineText,&used);
        const auto now=std::time(nullptr);
        if(used!=deadlineText.size() || deadline<now || deadline>now+900)
            throw std::runtime_error("live_request_expired");
#ifdef _WIN32
        if(ga::platform::fileExists(base+"cancel-"+id))
#else
        if(access((base+"cancel-"+id).c_str(),F_OK)==0)
#endif
            throw std::runtime_error("live_request_cancelled");
        if(op=="open") {
            if(token!=id) throw std::runtime_error("live_invalid_open_identity");
            const auto snapshot=base+"map-"+id+".json";
#ifdef _WIN32
            if(ga::platform::fileExists(snapshot)) throw std::runtime_error("live_output_exists");
#else
            if(access(snapshot.c_str(),F_OK)==0) throw std::runtime_error("live_output_exists");
#endif
            payload=openSession(token,snapshot);
        } else if(op=="inspect") payload=inspectSession(token);
        else if(op=="archive") {
            const auto root=base+"archive-"+id;
#ifdef _WIN32
            if(ga::platform::fileExists(root) || !ga::platform::ensurePrivateDirectory(root))
#else
            if(!std::filesystem::create_directory(root))
#endif
                throw std::runtime_error("live_output_exists");
#ifndef _WIN32
            std::filesystem::permissions(root,std::filesystem::perms::owner_all,
                std::filesystem::perm_options::replace);
#endif
            payload=archiveSession(token,root+"/Native");
        }
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
#ifdef _WIN32
    std::error_code cleanup;std::filesystem::remove(ga::bridge::nativePath(working),cleanup);
#else
    std::remove(working.c_str());
#endif
}
}
void pollRequests() {
    static auto next=std::chrono::steady_clock::now();
    const auto now=std::chrono::steady_clock::now();
    if(busy || now<next || gaDeliveryActive() || ga::bridge::mcpRequestInProgress()) return;
    next=now+std::chrono::milliseconds(100);
    if(!ensureDirectory()) return;
#ifdef _WIN32
    std::string id;
    try {
        for(const auto& name : ga::platform::directoryNames(directory()))
            if(name.size()==44 && name.substr(0,8)=="request-" && name.substr(40)==".txt"
                && hexId(name.substr(8,32))) { id=name.substr(8,32); break; }
    } catch(...) { return; }
#else
    DIR* dir=opendir(directory().c_str());
    if(!dir) return;
    std::string id;
    while(const auto* entry=readdir(dir)) {
        const std::string name=entry->d_name;
        if(name.size()==44 && name.substr(0,8)=="request-" && name.substr(40)==".txt"
            && hexId(name.substr(8,32))) { id=name.substr(8,32); break; }
    }
    closedir(dir);
#endif
    if(!id.empty()) process(id);
}
void requestCommand() {
    ACHAR id[128]={};
    if(acedGetString(0,_T("\nLive request id: "),id)==RTNORM)
        process(AcString(id).utf8Str());
}
void consoleDemoLoop() {
#ifdef _WIN32
    wchar_t executable[32768];
    const auto length=GetModuleFileNameW(nullptr,executable,32768);
    if(!length || length>=32768
        || _wcsicmp(std::filesystem::path(executable).filename().c_str(),L"accoreconsole.exe")!=0)
        return;
    if(!ensureDirectory()) return;
    HANDLE snapshot=CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS,0);
    if(snapshot==INVALID_HANDLE_VALUE) return;
    PROCESSENTRY32W entry{}; entry.dwSize=sizeof(entry); DWORD parent=0;
    for(BOOL ok=Process32FirstW(snapshot,&entry);ok;ok=Process32NextW(snapshot,&entry))
        if(entry.th32ProcessID==GetCurrentProcessId()) { parent=entry.th32ParentProcessID;break; }
    CloseHandle(snapshot);
    HANDLE supervisor=OpenProcess(SYNCHRONIZE|PROCESS_QUERY_LIMITED_INFORMATION,FALSE,parent);
    if(!supervisor) return;
    FILETIME created{},ended{},kernel{},user{},ownCreated{},ownEnded{},ownKernel{},ownUser{};
    if(!GetProcessTimes(supervisor,&created,&ended,&kernel,&user)
        || !GetProcessTimes(GetCurrentProcess(),&ownCreated,&ownEnded,&ownKernel,&ownUser)
        || CompareFileTime(&created,&ownCreated)>0) { CloseHandle(supervisor);return; }
    const auto stop=directory()+"/stop-live-demo";
    while(!ga::platform::fileExists(stop) && WaitForSingleObject(supervisor,0)==WAIT_TIMEOUT) {
        pollRequests();
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
    CloseHandle(supervisor);
#else
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
#endif
    closeSession();
}
}
