#include "delivery_command.h"
#include "delivery_ui.h"
#include "capture_commands.h"
#include "bridge_config.h"
#include "live_query_session.h"
#include "file_io.h"
#ifdef _WIN32
#include "local_platform.h"
#include "filesystem_utf8.h"
#endif
#include "operation_control.h"
#include "aced.h"
#include "adslib.h"
#include "acdocman.h"
#include "dbapserv.h"
#include "AcString.h"

#include <stdexcept>
#include <filesystem>
#ifndef _WIN32
#include <sys/stat.h>
#include <unistd.h>
#include <uuid/uuid.h>
#endif

namespace {
bool preparing = false;
struct Preparing {
    Preparing() { preparing = true; }
    ~Preparing() { preparing = false; }
};
struct WorkingDatabase {
    AcDbDatabase* previous = acdbHostApplicationServices()->workingDatabase();
    explicit WorkingDatabase(AcDbDatabase* db) { acdbHostApplicationServices()->setWorkingDatabase(db); }
    ~WorkingDatabase() { acdbHostApplicationServices()->setWorkingDatabase(previous); }
};

void checkCancel() {
    if (acedUsrBrk()) throw std::runtime_error("Подготовка отменена.");
}

void checkSourceState(AcApDocument* expectedDocument, AcDbDatabase* expectedDatabase,
                      const char* stage) {
    // Guard the same active editor/database across the synchronous native
    // capture. DBMOD is recorded in the snapshot, not treated as a disk match.
    auto* current = acDocManager ? acDocManager->curDocument() : nullptr;
    if (current != expectedDocument || !current || current->database() != expectedDatabase)
        throw std::runtime_error(std::string("Подготовка остановлена: активный чертёж сменился на этапе ")
            + stage + ". Файлы передачи не отправлены.");
}
}

bool gaDeliveryActive() { return preparing; }

void gaQueueOpenInService() {
    if (preparing) return;
    if (acDocManager && acDocManager->curDocument())
        acDocManager->sendStringToExecute(acDocManager->curDocument(), _T("GAOPEN\n"), true, false, false);
    else gaDelivery::error("Откройте чертёж в AutoCAD.");
}

void gaOpenInService() {
    if (preparing) return;
    Preparing active;
    try {
        auto* document = acDocManager ? acDocManager->curDocument() : nullptr;
        AcDbDatabase* live = document ? document->database() : nullptr;
        if (!live) { gaDelivery::error("Откройте чертёж в AutoCAD."); return; }
        WorkingDatabase sourceContext(live);
        resbuf modification{};
        if (acedGetVar(_T("DBMOD"), &modification) != RTNORM)
            throw std::runtime_error("Не удалось проверить состояние исходного чертежа.");
        const bool modified = modification.resval.rint != 0;
        if (!gaDelivery::confirmPreparation(modified)) return;
        ga::bridge::ScopedOperationControl progress({
            [] { return acedUsrBrk() != 0; }, gaDelivery::progress});
        gaDelivery::stage("Считываем открытый чертёж средствами AutoCAD");
        checkCancel();
        const std::string directory = gaDelivery::createStaging();
        if (directory.empty()) throw std::runtime_error("Не удалось создать папку передачи.");
        const std::string capture = directory + "/Drawing.autocad.json";
#ifdef _WIN32
        const auto token = ga::platform::randomToken();
        const auto queue = ga::platform::liveQueueDirectory();
        if(!ga::platform::ensurePrivateDirectory(queue))
            throw std::runtime_error("Недопустимый каталог расчётного сеанса");
#else
        uuid_t uuid; uuid_generate_random(uuid);
        char formatted[37]; uuid_unparse_lower(uuid,formatted);
        std::string token; for(char ch:std::string(formatted)) if(ch!='-') token+=ch;
        const auto queue="/tmp/green-atlas-live-query-"+std::to_string(getuid())+"-"+std::to_string(getpid());
        if(mkdir(queue.c_str(),0700)!=0 && errno!=EEXIST)
            throw std::runtime_error("Не удалось создать расчётный сеанс AutoCAD");
        struct stat queueInfo{};
        if(lstat(queue.c_str(),&queueInfo)!=0||!S_ISDIR(queueInfo.st_mode)
            ||queueInfo.st_uid!=getuid()||(queueInfo.st_mode&077))
            throw std::runtime_error("Недопустимый каталог расчётного сеанса");
#endif
        const auto snapshot=queue+"/map-"+token+".json";
        std::string transferFacts;
        const auto liveSession=ga::liveQuery::openSession(token,snapshot,&transferFacts);
        #ifdef _WIN32
        std::filesystem::copy_file(ga::bridge::nativePath(snapshot),ga::bridge::nativePath(capture),
            std::filesystem::copy_options::none);
#else
        std::filesystem::copy_file(snapshot,capture,std::filesystem::copy_options::none);
#endif
        if(ga::bridge::sha256File(capture)!=ga::bridge::sha256File(snapshot))
            throw std::runtime_error("Снимок изменился во время передачи");
        checkSourceState(document, live, "проверка геометрии");
        checkCancel();
        gaDelivery::stage("Сохраняем чертёж и загруженные подосновы проекта");
        ga::liveQuery::archiveSession(token, directory + "/Native");
#ifdef _WIN32
        std::filesystem::copy_file(ga::bridge::nativePath(snapshot + ".inventory.json"),
            ga::bridge::nativePath(capture + ".inventory.json"), std::filesystem::copy_options::none);
#else
        std::filesystem::copy_file(snapshot + ".inventory.json",
            capture + ".inventory.json", std::filesystem::copy_options::none);
#endif
        checkSourceState(document, live, "архивирование чертежа");
        checkCancel();
        gaDelivery::stage("Проверяем данные и замечания к исходнику");
        const auto issues = gaDelivery::liveProbeIssues(directory, transferFacts);
        checkSourceState(document, live, "проверка данных передачи");
        checkCancel();
        if (!issues.empty()) {
            gaDelivery::endStage();
            if (!gaDelivery::confirmLivePartial(issues)) return;
            checkSourceState(document, live, "подтверждение замечаний");
            checkCancel();
        }
        gaDelivery::stage("Проверяем файлы и передаём проект в Green Atlas");
        const auto version = acdbHostApplicationServices()->releaseMarketVersion();
        const std::string ticket = gaDelivery::writeLiveTicket(
            directory, ga::bridge::kPluginVersion,
            version ? AcString(version).utf8Str() : "2027", liveSession, transferFacts);
        if (ticket.empty()) throw std::runtime_error("Не удалось проверить файлы передачи.");
        checkSourceState(document, live, "передача проекта");
        checkCancel();
        std::string launchFailure;
        if (!gaDelivery::launchConnector(ticket, &launchFailure))
            throw std::runtime_error(launchFailure.empty()
                ? "Не удалось открыть приложение передачи. Файлы сохранены в списке передач AutoCAD."
                : launchFailure);
        gaDelivery::endStage();
        acutPrintf(_T("\nGreen Atlas: файлы передачи готовы, запрос на открытие отправлен"));
    } catch (const std::exception& failure) {
        gaDelivery::endStage();
        gaDelivery::error(failure.what());
    }
}
