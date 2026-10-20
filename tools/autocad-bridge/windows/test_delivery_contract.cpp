#include "delivery_contract.h"
#include <cassert>
#include <iostream>
using namespace gaDelivery::contract;
int main() {
    const std::string hash(64,'a');
    const Json capture={{"name","Drawing.autocad.json"},{"kind","live_capture"},{"bytes",100},{"sha256",hash}};
    const Json session={{"session_id",std::string(32,'b')},{"snapshot_sha256",hash}};
    Json files=Json::array();
    for(const char* name:{"Native/session.json","Native/instances.json","Native/semantics.json",
                        "Native/host.dwg","Drawing.autocad.json","Drawing.autocad.json.inventory.json"})
        files.push_back({{"name",name},{"bytes",100},{"sha256",hash}});
    const auto ticket=nativeTicket("0.1.48","2027","Участок.dwg",capture,session,files);
    assert(ticket["schema"]=="green-atlas.transfer/5");
    assert(ticket["native_session"]["files"].size()==6);
    assert(ticket["producer"]["target"]=="windows-x86_64");
    const Json facts={{"snapshot_sha256",hash},{"probe",{{"capture_mode","live_document"}}}};
    assert(factsProbe(facts.dump(),hash)["capture_mode"]=="live_document");
    auto fails=[](auto action) { bool rejected=false;try {action();}catch(const std::exception&){rejected=true;}assert(rejected); };
    fails([&]{factsProbe(facts.dump(),std::string(64,'c'));});
    fails([&]{factsProbe(std::string(kMaxFacts+1,' '),hash);});
    fails([&]{nativeTicket("0.1.47","2027","Участок.dwg",capture,session,files);});
    auto missing=files;missing.erase(2);
    fails([&]{nativeTicket("0.1.48","2027","Участок.dwg",capture,session,missing);});
    auto duplicate=files;duplicate.push_back(files[0]);
    fails([&]{nativeTicket("0.1.48","2027","Участок.dwg",capture,session,duplicate);});
    auto oversized=files;oversized[2]["bytes"]=32ULL*1024*1024+1;
    fails([&]{nativeTicket("0.1.48","2027","Участок.dwg",capture,session,oversized);});
    assert(token(std::string(32,'a')) && !token(std::string(32,'A')) && !token("../a"));
    std::cout<<"delivery-contract-ok\n";
}
