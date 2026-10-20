// Windows-оболочка текущего desktop-протокола; CAD и draft остаются у своих владельцев.
#include "windows-policies.h"
#ifdef _WIN32
#include "windows-platform.h"
// MIDL forward declarations требуют COM типов даже при WIN32_LEAN_AND_MEAN.
#include <objbase.h>
#include <wrl.h>
#include <WebView2.h>
#include <commdlg.h>
#include <commctrl.h>
#include <shellapi.h>
#include <shobjidl.h>
#include <winhttp.h>
#include <chrono>
#include <deque>
#include <functional>
#include <future>
#include <iostream>
#include <set>
#include <stdexcept>
#include <vector>

using Microsoft::WRL::Callback;
using Microsoft::WRL::ComPtr;
using namespace ga::desktop;
using namespace ga::desktop::windows;
namespace {
constexpr wchar_t kClass[] = L"GreenAtlasDesktopCurrent";
constexpr UINT kTicket = WM_APP + 1;
constexpr UINT kLibrary = 41001, kTransfers = 41002, kCancel = 41003, kRestart = 41004;
struct PendingHttp {
    uint64_t generation;
    std::string token;
    std::future<HttpResult> future;
    std::function<void(const HttpResult&)> done;
};
struct PendingDownload {
    std::future<DownloadResult> future;
    std::shared_ptr<std::atomic_bool> cancelled;
};
struct Desktop : std::enable_shared_from_this<Desktop> {
    Settings settings;
    HWND window = nullptr;
    HWND splash = nullptr;
    HFONT splashFont = nullptr;
    ComPtr<ICoreWebView2Controller> controller;
    ComPtr<ICoreWebView2> web;
    Runtime engine;
    TicketPipe ipc;
    Bridge bridge;
    std::vector<PendingHttp> requests;
    std::vector<PendingDownload> downloads;
    std::deque<std::string> tickets;
    std::deque<DownloadOffer> offeredDownloads;
    std::deque<std::string> externalLinks;
    std::set<std::string> seen;
    std::string activeId, stoppedId;
    uint64_t generation = 0, launchedAt = 0, stopDeadline = 0, nextPoll = 0, submission = 0;
    bool viewReady = false, document = false, handoffBusy = false, polling = false;
    bool stopping = false, closing = false, restarting = false, terminated = false;
    bool protectedDocument = false, engineStarted = false, exitObserved = false;
    bool showingDownloadDialog = false;
    bool showingDependencyDialog = false;
    std::optional<Json> dependencyChoice;

    void chooseDependency() {
        const auto choice = *dependencyChoice;
        dependencyChoice.reset();
        if (!dependencyPickerRequest(choice, bridge.origin, bridge) || stopping || closing) return;
        auto reply = bridge.event("dependencyChosen");
        reply["requestId"] = choice["requestId"];
        reply["field"] = choice["field"];
        reply["path"] = nullptr;
        const auto field = choice["field"].get<std::string>();
        showingDependencyDialog = true;
        ComPtr<IFileOpenDialog> dialog;
        if (SUCCEEDED(CoCreateInstance(CLSID_FileOpenDialog, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&dialog)))) {
            DWORD options = 0; dialog->GetOptions(&options);
            const bool file = field == "blender" || field == "autocad_template";
            dialog->SetOptions(options | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST | (file ? FOS_FILEMUSTEXIST : FOS_PICKFOLDERS));
            const COMDLG_FILTERSPEC program{L"Blender (blender.exe)", L"blender.exe"};
            const COMDLG_FILTERSPEC drawing{L"Шаблоны AutoCAD (*.dwt)", L"*.dwt"};
            if (file) dialog->SetFileTypes(1, field == "blender" ? &program : &drawing);
            dialog->SetTitle(file ? L"Выберите файл компонента" : L"Выберите папку компонента");
            if (SUCCEEDED(dialog->Show(window))) {
                ComPtr<IShellItem> item;
                PWSTR path = nullptr;
                if (SUCCEEDED(dialog->GetResult(&item)) && SUCCEEDED(item->GetDisplayName(SIGDN_FILESYSPATH, &path))) {
                    reply["path"] = utf8(path); CoTaskMemFree(path);
                }
            }
        }
        showingDependencyDialog = false;
        send(reply);
    }

    ~Desktop() { if (splashFont) DeleteObject(splashFont); }
    void status(const std::wstring& text) {
        SetWindowTextW(window, (L"Green Atlas — " + text).c_str());
        if (splash) SetWindowTextW(splash, text.c_str());
    }
    void showSplash() {
        RECT bounds{}; GetClientRect(window, &bounds);
        splash = CreateWindowExW(0, L"STATIC", L"Подготавливаем локальное приложение…",
            WS_CHILD | WS_VISIBLE | SS_CENTER | SS_CENTERIMAGE | SS_NOPREFIX,
            0, 0, bounds.right, bounds.bottom, window, nullptr, GetModuleHandleW(nullptr), nullptr);
        const UINT dpi = GetDpiForWindow(window);
        splashFont = CreateFontW(-MulDiv(15, static_cast<int>(dpi), 72), 0, 0, 0, FW_SEMIBOLD,
            FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
            CLEARTYPE_QUALITY, DEFAULT_PITCH | FF_SWISS, L"Segoe UI");
        if (splash && splashFont) SendMessageW(splash, WM_SETFONT, reinterpret_cast<WPARAM>(splashFont), TRUE);
    }
    void hideSplash() {
        if (splash) DestroyWindow(splash);
        splash = nullptr;
        if (splashFont) DeleteObject(splashFont);
        splashFont = nullptr;
    }
    void error(std::string_view text) { status(utf16(text)); }
    bool send(const Json& value) {
        return web && document && SUCCEEDED(web->PostWebMessageAsJson(utf16(value.dump()).c_str()));
    }
    void available(bool value) {
        if (!value) bridge.unavailable();
        send(bridge.event(value ? "runtimeRestored" : "runtimeUnavailable"));
    }
    void asyncRequest(std::string method, std::string route, Json body,
        std::function<void(const HttpResult&)> done) {
        const auto origin = bridge.origin, token = bridge.token;
        requests.push_back({generation, token,
            std::async(std::launch::async, [origin, token, method, route, body] {
                return request(origin, token, method, route, body);
            }), std::move(done)});
    }
    void navigate(const std::string& route) {
        if (!web || !bridge.runtime || !projectRoute(route)) return;
        protectedDocument = false; bridge.newDocument();
        approved = bridge.origin + route;
        if (FAILED(web->Navigate(utf16(approved).c_str()))) error("Не удалось открыть локальную страницу");
        else document = true;
    }
    std::string approved;
    void failDocument() {
        bridge.failedDocument();
        send(bridge.event("runtimeUnavailable"));
        error("Интерфейс остановлен. Закройте и заново откройте Green Atlas");
    }
    void leave(Action action, std::string target = {}) {
        if (bridge.documentFailed) {
            if (action != Action::close && action != Action::quit) { error("Интерфейс остановлен. Закройте и заново откройте Green Atlas"); return; }
            if (!protectedDocument || MessageBoxW(window,
                L"Интерфейс больше не отвечает. Подтверждение сохранения недоступно.\n\n"
                L"Последние изменения могли не сохраниться. Закрыть Green Atlas?",
                L"Закрытие Green Atlas", MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2) == IDYES) stop(true);
            return;
        }
        if (!protectedDocument) { if (action == Action::close || action == Action::quit) stop(true); else navigate(target); return; }
        const auto pending = bridge.leave.begin(action, target);
        if (!pending) { error("Дождитесь готовности документа и завершения действия"); return; }
        if (!send(bridge.navigation(*pending))) failDocument();
    }
    void message(const Json& value, const std::string& source) {
        if (!dependencyChoice && !showingDependencyDialog && dependencyPickerRequest(value, source, bridge)) {
            dependencyChoice = value;
            return;
        }
        const bool pending = bridge.leave.pending();
        const auto response = bridge.receive(value, source);
        if (response && response->value("type", "") == "capabilities") {
            protectedDocument = true;
            send(*response); send(bridge.event(bridge.runtime ? "runtimeRestored" : "runtimeUnavailable"));
            nextTicket(); return;
        }
        if (response && response->value("type", "") == "accepted") {
            const auto action = response->value("action", "");
            if (action == "close" || action == "quit") stop(true);
            else {
                if (action == "library") {
                    ++generation; ++submission; activeId.clear(); stoppedId.clear(); handoffBusy = polling = false;
                }
                navigate(response->value("target", ""));
            }
        }
        if (pending && !bridge.leave.pending() && handoffBusy && activeId.empty()) {
            handoffBusy = false;
            if (!response) error("Новый проект сохранён. Его можно открыть из списка проектов");
            nextTicket();
        }
    }
    void failRuntime(std::string_view message) {
        if (!stopping) {
            if (bridge.runtime) asyncRequest("POST", "/_desktop/shutdown", Json::object(), [](const HttpResult&) {});
            stopping = true; closing = restarting = terminated = false; stopDeadline = GetTickCount64() + 10000;
        }
        available(false); error(message);
    }
    void attachSession() {
        if (!web || !viewReady || !bridge.runtime) return;
        ComPtr<ICoreWebView2_2> version;
        ComPtr<ICoreWebView2CookieManager> cookies;
        ComPtr<ICoreWebView2Cookie> cookie;
        if (FAILED(web.As(&version)) || FAILED(version->get_CookieManager(&cookies))
            || FAILED(cookies->CreateCookie(L"green_atlas_local_session", utf16(bridge.token).c_str(), L"127.0.0.1", L"/", &cookie))
            || FAILED(cookie->put_IsHttpOnly(TRUE))
            || FAILED(cookie->put_SameSite(COREWEBVIEW2_COOKIE_SAME_SITE_KIND_STRICT))
            || FAILED(cookies->AddOrUpdateCookie(cookie.Get()))) {
            failRuntime("Не удалось открыть приватную сессию WebView2. Можно повторить запуск из меню"); return;
        }
        if (bridge.documentFailed) { send(bridge.event("runtimeUnavailable")); error("Интерфейс остановлен. Закройте и заново откройте Green Atlas"); return; }
        if (document && bridge.leave.isReady()) available(true);
        else if (!document) navigate("/projects");
        status(L"Готово");
        // После перезапуска только читаем журнал: POST прежнего ticket не повторяется.
        if (!stoppedId.empty()) {
            const auto id = stoppedId; stoppedId.clear();
            asyncRequest("GET", "/_desktop/handoffs/" + id, nullptr,
                [this, id](const HttpResult& value) { if (value.error.empty()) { activeId = id; receipt(value.value); } });
        }
        nextTicket();
    }
    void launch() {
        if (engine.running() || !engine.stopped()) return;
        engine.release(); ++generation; ++submission; polling = false;
        if (!activeId.empty()) stoppedId = activeId;
        activeId.clear(); handoffBusy = false; stopping = restarting = terminated = false;
        available(false); status(L"Запускаем локальный сервис");
        engine.start(settings, bridge.origin.empty() ? std::nullopt : loopbackPort(bridge.origin));
        launchedAt = GetTickCount64(); engineStarted = true; exitObserved = false;
    }
    void stop(bool quit) {
        if (stopping) { if (quit) { closing = true; restarting = false; } return; }
        closing = quit; restarting = !quit; stopping = true; terminated = false;
        stopDeadline = GetTickCount64() + 10000;
        for (auto& download : downloads) download.cancelled->store(true);
        if (bridge.runtime) asyncRequest("POST", "/_desktop/shutdown", Json::object(), [](const HttpResult&) {});
        available(false); status(quit ? L"Закрываем приложение" : L"Перезапускаем локальный сервис");
    }
    void queue(std::string id) {
        ShowWindow(window, SW_RESTORE); SetForegroundWindow(window);
        if (!ticketDirectoryId(id) || !seen.insert(id).second) return;
        const auto root = settings.tickets / utf16(id);
        if (!directPath(root, true) || !directPath(root / L"transfer.gatransfer")) {
            error("Снимок AutoCAD недоступен. Подготовьте его заново"); return;
        }
        tickets.push_back(std::move(id)); nextTicket();
    }
    void nextTicket() {
        if (!bridge.runtime || !viewReady || bridge.documentFailed || !bridge.leave.isReady() || bridge.leave.pending()
            || stopping || handoffBusy || tickets.empty()) return;
        const auto id = tickets.front(); tickets.pop_front(); handoffBusy = true;
        const auto path = settings.tickets / utf16(id) / L"transfer.gatransfer";
        const auto encoded = utf8(path.wstring()); activeId.clear(); stoppedId.clear();
        const auto expected = ++submission;
        status(L"Принимаем чертёж AutoCAD");
        // Текущий /5 принимает только ticket; решение о resume всегда отдельное.
        asyncRequest("POST", "/_desktop/handoffs", {{"ticket", encoded}}, [this, expected](const HttpResult& response) {
            if (submission != expected) return;
            if (!response.error.empty()) { handoffBusy = false; error(response.error); return; }
            const auto id = response.value.value("id", "");
            if (!handoffId(id)) { handoffBusy = false; error("Неизвестный идентификатор квитанции"); return; }
            activeId = id;
            receipt(response.value);
        });
    }
    void openExternal(std::string_view uri, bool userInitiated) {
        if (!userInitiated || !externalHttpUrl(uri) || sameOrigin(bridge.origin, uri)) return;
        URL_COMPONENTS parts{}; parts.dwStructSize = sizeof(parts);
        parts.dwSchemeLength = parts.dwHostNameLength = parts.dwUserNameLength = parts.dwPasswordLength = static_cast<DWORD>(-1);
        const auto wide = utf16(uri);
        if (!WinHttpCrackUrl(wide.c_str(), static_cast<DWORD>(wide.size()), 0, &parts)
            || (parts.nScheme != INTERNET_SCHEME_HTTP && parts.nScheme != INTERNET_SCHEME_HTTPS)
            || !parts.dwHostNameLength || parts.dwUserNameLength || parts.dwPasswordLength) return;
        if (reinterpret_cast<INT_PTR>(ShellExecuteW(window, L"open", wide.c_str(), nullptr, nullptr, SW_SHOWNORMAL)) <= 32)
            error("Не удалось открыть ссылку в системном браузере");
    }
    void offerExternal(std::string uri, bool userInitiated) {
        if (userInitiated && externalHttpUrl(uri) && !sameOrigin(bridge.origin, uri)
            && externalLinks.size() < 16) externalLinks.push_back(std::move(uri));
    }
    void receipt(const Json& value) {
        if (!value.is_object() || value.value("id", "") != activeId || !handoffId(activeId)) {
            error("Квитанция относится к другой передаче"); handoffBusy = false; return;
        }
        const auto state = value.value("status", "");
        if (state == "needs_review") {
            const auto route = value.value("project_path", ""), entry = value.value("entry", "");
            if (!handoffRoute(entry, route)) { error("Несовместимый адрес сохранённого проекта"); handoffBusy = false; return; }
            activeId.clear(); handoffBusy = true; leave(Action::handoff, route); return;
        }
        if (state == "queued" || state == "processing") {
            handoffBusy = true; nextPoll = GetTickCount64() + 500;
            status(utf16(value.value("stage", "Подготавливаем проект"))); return;
        }
        handoffBusy = false;
        if (state == "cancelled") { activeId.clear(); status(L"Подготовка отменена"); nextTicket(); return; }
        error(value.value("message", Json()).is_string() ? value["message"].get<std::string>() : "Передача остановлена. Откройте список передач AutoCAD");
    }
    void transfers() {
        if (!bridge.runtime || bridge.documentFailed || stopping) return;
        if (handoffBusy || bridge.leave.pending()) { error("Дождитесь текущего действия или отмените передачу"); return; }
        asyncRequest("GET", "/_desktop/handoffs", nullptr, [this](const HttpResult& answer) {
            if (handoffBusy || bridge.leave.pending() || stopping) return;
            if (!answer.error.empty()) { error(answer.error); return; }
            const auto values = answer.value.value("handoffs", Json());
            if (!values.is_array()) { error("Некорректный список передач"); return; }
            std::vector<Json> rows; std::vector<std::wstring> labels;
            for (const auto& row : values) {
                if (!row.is_object() || !handoffId(row.value("id", ""))) continue;
                const auto state = row.value("status", "");
                if (state != "failed" && state != "interrupted" && state != "cancelled" && state != "needs_review"
                    && state != "queued" && state != "processing") continue;
                rows.push_back(row); labels.push_back(utf16(state + ", " + row["id"].get<std::string>().substr(0, 8)));
                if (rows.size() == 64) break;
            }
            if (rows.empty()) { MessageBoxW(window, L"Сохранённых передач пока нет", L"Передачи AutoCAD", MB_OK); return; }
            std::vector<TASKDIALOG_BUTTON> choices;
            for (size_t index = 0; index < labels.size(); ++index) choices.push_back({1000 + static_cast<int>(index), labels[index].c_str()});
            TASKDIALOGCONFIG dialog{}; dialog.cbSize = sizeof(dialog); dialog.hwndParent = window;
            dialog.pszWindowTitle = L"Передачи AutoCAD"; dialog.pszMainInstruction = L"Выберите передачу";
            dialog.pszContent = L"Подготовка продолжится только после отдельного подтверждения.";
            dialog.dwCommonButtons = TDCBF_OK_BUTTON | TDCBF_CANCEL_BUTTON;
            dialog.cRadioButtons = static_cast<UINT>(choices.size()); dialog.pRadioButtons = choices.data();
            dialog.nDefaultRadioButton = choices.front().nButtonID;
            int button = 0, radio = 0;
            // Some supported SDK import libraries omit this v6 entry point.
            // Load only the signed system DLL under the app's v6 activation context.
            const auto controls = LoadLibraryExW(L"comctl32.dll", nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
            if (!controls) { error("Диалог передач Windows недоступен"); return; }
            using TaskDialogCall = HRESULT (WINAPI*)(const TASKDIALOGCONFIG*, int*, int*, BOOL*);
            const auto taskDialog = reinterpret_cast<TaskDialogCall>(GetProcAddress(controls, "TaskDialogIndirect"));
            const auto result = taskDialog ? taskDialog(&dialog, &button, &radio, nullptr) : E_NOTIMPL;
            FreeLibrary(controls);
            if (FAILED(result) || button != IDOK || radio < 1000
                || static_cast<size_t>(radio - 1000) >= rows.size()) return;
            if (handoffBusy || bridge.leave.pending() || stopping || !bridge.runtime) return;
            const auto selected = rows[radio - 1000];
            if (selected.value("status", "") == "needs_review") {
                const auto path = selected.value("project_path", "");
                if (handoffRoute(selected.value("entry", ""), path)) leave(Action::handoff, path);
                return;
            }
            const auto message = selected.value("message", Json()).is_string() ? utf16(selected["message"].get<std::string>()) + L"\n\nПродолжить подготовку?" : L"Продолжить подготовку выбранной передачи?";
            if (MessageBoxW(window, message.c_str(), L"Передачи AutoCAD", MB_YESNO | MB_ICONQUESTION) != IDYES) return;
            if (handoffBusy || bridge.leave.pending() || stopping || !bridge.runtime) return;
            ++generation; ++submission; polling = false; stoppedId.clear();
            activeId = selected["id"].get<std::string>(); handoffBusy = true;
            const auto id = activeId;
            asyncRequest("POST", "/_desktop/handoffs/" + id + "/resume", Json::object(),
                [this, id](const HttpResult& result) { if (activeId == id) { if (result.error.empty()) receipt(result.value); else { handoffBusy = false; error(result.error); } } });
        });
    }
    void offerDownload(ICoreWebView2DownloadStartingEventArgs* args) {
        args->put_Cancel(TRUE); args->put_Handled(TRUE);
        if (!bridge.runtime || stopping) return;
        ComPtr<ICoreWebView2DownloadOperation> operation;
        if (FAILED(args->get_DownloadOperation(&operation))) return;
        LPWSTR rawUri = nullptr, rawMime = nullptr, rawFile = nullptr;
        operation->get_Uri(&rawUri); operation->get_MimeType(&rawMime); args->get_ResultFilePath(&rawFile);
        const std::wstring uri = rawUri ? rawUri : L"", mime = rawMime ? rawMime : L"", file = rawFile ? rawFile : L"";
        CoTaskMemFree(rawUri); CoTaskMemFree(rawMime); CoTaskMemFree(rawFile);
        const auto encoded = utf8(uri);
        if (!sameOrigin(bridge.origin, encoded)) return;
        const auto route = encoded.substr(bridge.origin.size());
        const auto filename = fs::path(file).filename().wstring();
        if (!downloadType(route, utf8(mime), utf8(filename))) { error("Этот адрес не является файлом текущего проекта"); return; }
        // Модальный диалог открывается после возврата WebView2 callback, без COM args.
        if (offeredDownloads.size() < 16)
            offeredDownloads.push_back({generation, bridge.origin, bridge.token, route, utf8(mime), utf8(filename)});
    }
    void saveDownload(const DownloadOffer& offered) {
        const auto current = [&] { return offered.current(generation, bridge, stopping || closing); };
        if (!current()) return;
        showingDownloadDialog = true;
        struct Reset { bool& value; ~Reset() { value = false; } } reset{showingDownloadDialog};
        const auto filename = utf16(offered.filename);
        std::wstring selected(32768, L'\0'); selected.replace(0, filename.size(), filename);
        OPENFILENAMEW save{}; save.lStructSize = sizeof(save); save.hwndOwner = window;
        save.lpstrFile = selected.data(); save.nMaxFile = static_cast<DWORD>(selected.size());
        save.lpstrTitle = L"Сохранить файл Green Atlas";
        save.Flags = OFN_OVERWRITEPROMPT | OFN_PATHMUSTEXIST | OFN_NOCHANGEDIR;
        const auto extension = fs::path(filename).extension().wstring();
        save.lpstrDefExt = extension.empty() ? nullptr : extension.c_str() + 1;
        if (!GetSaveFileNameW(&save) || !current()) return;
        const fs::path destination(selected.c_str());
        if (!downloadType(offered.route, offered.mime, utf8(destination.filename().wstring()))) { error("Сохраните файл с исходным расширением"); return; }
        const auto origin = offered.origin, token = offered.token, type = offered.mime, route = offered.route;
        auto cancelled = std::make_shared<std::atomic_bool>(false);
        downloads.push_back({std::async(std::launch::async, [origin, token, route, type, destination, cancelled] {
            return download(origin, token, route, type, destination, cancelled);
        }), cancelled});
        status(L"Сохраняем файл");
    }
    void startWebView() {
        const auto keep = shared_from_this();
        const auto hr = CreateCoreWebView2EnvironmentWithOptions(nullptr, settings.browser.c_str(), nullptr,
            Callback<ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler>([this, keep](HRESULT result, ICoreWebView2Environment* environment) -> HRESULT {
                if (closing) return S_OK;
                if (FAILED(result) || !environment) { failRuntime("Установите WebView2 Runtime из комплекта установки"); return S_OK; }
                const auto created = environment->CreateCoreWebView2Controller(window,
                    Callback<ICoreWebView2CreateCoreWebView2ControllerCompletedHandler>([this, keep](HRESULT code, ICoreWebView2Controller* value) -> HRESULT {
                        if (closing) return S_OK;
                        if (FAILED(code) || !value) { failRuntime("Не удалось создать окно WebView2"); return S_OK; }
                        controller = value; controller->get_CoreWebView2(&web);
                        if (!web) { failRuntime("Не удалось получить WebView2 document"); return S_OK; }
                        controller->put_IsVisible(FALSE);
                        RECT bounds{}; GetClientRect(window, &bounds); controller->put_Bounds(bounds);
                        ComPtr<ICoreWebView2Settings> preferences; web->get_Settings(&preferences);
                        if (!preferences) { failRuntime("Не удалось настроить WebView2"); return S_OK; }
                        preferences->put_AreDevToolsEnabled(FALSE); preferences->put_AreDefaultContextMenusEnabled(FALSE);
                        EventRegistrationToken token{};
                        HRESULT registration = S_OK;
                        const auto checked = [&registration](HRESULT code) { if (FAILED(code)) registration = code; };
                        checked(web->add_NavigationStarting(Callback<ICoreWebView2NavigationStartingEventHandler>([this](ICoreWebView2*, ICoreWebView2NavigationStartingEventArgs* args) -> HRESULT {
                            LPWSTR raw = nullptr; args->get_Uri(&raw); const auto uri = raw ? utf8(raw) : ""; CoTaskMemFree(raw);
                            if (uri == "about:blank" && !document) return S_OK;
                            if (uri == approved && sameOrigin(bridge.origin, uri)) { approved.clear(); return S_OK; }
                            if (sameOrigin(bridge.origin, uri) && downloadRoute(uri.substr(bridge.origin.size()))) return S_OK;
                            BOOL user = FALSE; args->get_IsUserInitiated(&user);
                            args->put_Cancel(TRUE); offerExternal(uri, user != FALSE); return S_OK;
                        }).Get(), &token));
                        checked(web->add_FrameNavigationStarting(Callback<ICoreWebView2NavigationStartingEventHandler>([this](ICoreWebView2*, ICoreWebView2NavigationStartingEventArgs* args) -> HRESULT {
                            LPWSTR raw = nullptr; args->get_Uri(&raw); const auto uri = raw ? utf8(raw) : ""; CoTaskMemFree(raw);
                            if (!sameOrigin(bridge.origin, uri)) args->put_Cancel(TRUE); return S_OK;
                        }).Get(), &token));
                        checked(web->add_NewWindowRequested(Callback<ICoreWebView2NewWindowRequestedEventHandler>([this](ICoreWebView2*, ICoreWebView2NewWindowRequestedEventArgs* args) -> HRESULT {
                            LPWSTR raw = nullptr; BOOL user = FALSE; args->get_Uri(&raw); args->get_IsUserInitiated(&user);
                            const auto uri = raw ? utf8(raw) : ""; CoTaskMemFree(raw);
                            args->put_Handled(TRUE); offerExternal(uri, user != FALSE); return S_OK;
                        }).Get(), &token));
                        checked(web->add_NavigationCompleted(Callback<ICoreWebView2NavigationCompletedEventHandler>([this](ICoreWebView2*, ICoreWebView2NavigationCompletedEventArgs* args) -> HRESULT {
                            BOOL success = FALSE; args->get_IsSuccess(&success);
                            if (!success) error("Локальная страница не загрузилась. Откройте список проектов из меню");
                            else if (document && web) {
                                LPWSTR raw = nullptr; web->get_Source(&raw);
                                const auto source = raw ? utf8(raw) : ""; CoTaskMemFree(raw);
                                if (sameOrigin(bridge.origin, source)) {
                                    controller->put_IsVisible(TRUE);
                                    hideSplash();
                                }
                            }
                            return S_OK;
                        }).Get(), &token));
                        checked(web->add_ProcessFailed(Callback<ICoreWebView2ProcessFailedEventHandler>([this](ICoreWebView2*, ICoreWebView2ProcessFailedEventArgs* args) -> HRESULT {
                            if (closing) return S_OK;
                            COREWEBVIEW2_PROCESS_FAILED_KIND kind{};
                            if (FAILED(args->get_ProcessFailedKind(&kind))
                                || kind == COREWEBVIEW2_PROCESS_FAILED_KIND_BROWSER_PROCESS_EXITED
                                || kind == COREWEBVIEW2_PROCESS_FAILED_KIND_RENDER_PROCESS_EXITED
                                || kind == COREWEBVIEW2_PROCESS_FAILED_KIND_RENDER_PROCESS_UNRESPONSIVE) failDocument();
                            return S_OK;
                        }).Get(), &token));
                        // Это событие WebView относится к top document; frame API отдельно не подключается.
                        checked(web->add_WebMessageReceived(Callback<ICoreWebView2WebMessageReceivedEventHandler>([this](ICoreWebView2*, ICoreWebView2WebMessageReceivedEventArgs* args) -> HRESULT {
                            LPWSTR rawSource = nullptr, rawMessage = nullptr, current = nullptr;
                            args->get_Source(&rawSource); args->get_WebMessageAsJson(&rawMessage); web->get_Source(&current);
                            const auto source = rawSource ? utf8(rawSource) : "", top = current ? utf8(current) : "";
                            const auto body = rawMessage ? utf8(rawMessage) : "";
                            CoTaskMemFree(rawSource); CoTaskMemFree(rawMessage); CoTaskMemFree(current);
                            try { if (source == top && body.size() <= 16384) message(Json::parse(body), source); } catch (...) { }
                            return S_OK;
                        }).Get(), &token));
                        ComPtr<ICoreWebView2_4> downloads;
                        if (FAILED(web.As(&downloads))) { failRuntime("WebView2 Runtime слишком старый"); return S_OK; }
                        checked(downloads->add_DownloadStarting(Callback<ICoreWebView2DownloadStartingEventHandler>([this](ICoreWebView2*, ICoreWebView2DownloadStartingEventArgs* args) -> HRESULT {
                            try { offerDownload(args); } catch (const std::exception& errorValue) { error(errorValue.what()); } return S_OK;
                        }).Get(), &token));
                        if (FAILED(registration)) { failRuntime("Не удалось подключить callbacks WebView2"); return S_OK; }
                        viewReady = true; attachSession(); return S_OK;
                    }).Get());
                if (FAILED(created)) failRuntime("Не удалось начать создание WebView2 controller");
                return S_OK;
            }).Get());
        if (FAILED(hr)) failRuntime("Не удалось загрузить WebView2 Runtime");
    }
    void tick() {
        if (dependencyChoice && IsWindowEnabled(window) && !showingDownloadDialog && !showingDependencyDialog) chooseDependency();
        if (!externalLinks.empty() && !stopping && !closing) {
            const auto uri = std::move(externalLinks.front()); externalLinks.pop_front(); openExternal(uri, true);
        }
        if (!showingDownloadDialog && IsWindowEnabled(window) && !offeredDownloads.empty()) {
            const auto offered = std::move(offeredDownloads.front()); offeredDownloads.pop_front(); saveDownload(offered);
        }
        for (size_t index = 0; index < requests.size();) {
            if (requests[index].future.wait_for(std::chrono::seconds(0)) != std::future_status::ready) { ++index; continue; }
            auto item = std::move(requests[index]); requests.erase(requests.begin() + index);
            auto answer = item.future.get();
            if (item.generation == generation && item.token == bridge.token && !closing) item.done(answer);
        }
        for (size_t index = 0; index < downloads.size();) {
            if (downloads[index].future.wait_for(std::chrono::seconds(0)) != std::future_status::ready) { ++index; continue; }
            auto result = downloads[index].future.get(); downloads.erase(downloads.begin() + index);
            writeDownloadReceipt(settings, result);
            if (!closing) { if (result.ok) status(L"Файл сохранён"); else error(result.error); }
        }
        try {
            if (auto line = engine.ready()) {
                if (!bridge.acceptReady(Json::parse(*line))) throw std::runtime_error("Некорректная готовность локального сервиса");
                if (stopping) {
                    asyncRequest("POST", "/_desktop/shutdown", Json::object(), [](const HttpResult&) {});
                    available(false);
                } else attachSession();
            }
        } catch (const std::exception&) { failRuntime("Некорректная готовность локального сервиса"); }
        const auto now = GetTickCount64();
        if (engine.running() && !bridge.runtime && !stopping && now - launchedAt > 30 * 60 * 1000) {
            engine.terminate(); error("Подготовка хранилища заняла слишком много времени");
        }
        if (stopping) {
            if (!engine.stopped() && now >= stopDeadline && !terminated) { engine.terminate(); terminated = true; }
            if (engine.stopped() && requests.empty() && downloads.empty()) {
                engine.release(); stopping = false;
                if (closing) { ipc.stop(); if (controller) controller->Close(); web.Reset(); controller.Reset(); DestroyWindow(window); return; }
                if (restarting) launch();
            }
            return;
        }
        if (engineStarted && !engine.running() && !exitObserved) {
            exitObserved = true;
            stoppedId = activeId; activeId.clear(); handoffBusy = polling = false;
            ++generation; available(false); error("Локальный сервис остановлен. Его можно перезапустить из меню");
            if (!engine.stopped()) engine.terminate();
        }
        if (bridge.runtime && handoffBusy && !activeId.empty() && !polling && now >= nextPoll) {
            polling = true; const auto id = activeId;
            asyncRequest("GET", "/_desktop/handoffs/" + id, nullptr, [this, id](const HttpResult& value) {
                polling = false;
                if (activeId != id) return;
                if (!value.error.empty()) { handoffBusy = false; error(value.error); return; }
                receipt(value.value);
            });
        }
    }
};
LRESULT CALLBACK windowProc(HWND window, UINT message, WPARAM wparam, LPARAM lparam) {
    auto* app = reinterpret_cast<Desktop*>(GetWindowLongPtrW(window, GWLP_USERDATA));
    try {
        if (message == WM_NCCREATE) {
            SetWindowLongPtrW(window, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(reinterpret_cast<CREATESTRUCTW*>(lparam)->lpCreateParams)); return TRUE;
        }
        if (!app) return DefWindowProcW(window, message, wparam, lparam);
        switch (message) {
        case kTicket: { std::unique_ptr<std::string> id(reinterpret_cast<std::string*>(lparam)); app->queue(*id); return 0; }
        case WM_SIZE: {
            RECT bounds{}; GetClientRect(window, &bounds);
            if (app->controller) app->controller->put_Bounds(bounds);
            if (app->splash) MoveWindow(app->splash, 0, 0, bounds.right, bounds.bottom, TRUE);
            return 0;
        }
        case WM_TIMER: app->tick(); return 0;
        case WM_CLOSE: app->leave(Action::close); return 0;
        case WM_QUERYENDSESSION: return app->closing ? TRUE : FALSE;
        case WM_COMMAND:
            if (LOWORD(wparam) == kLibrary) app->leave(Action::library, "/projects");
            if (LOWORD(wparam) == kTransfers) app->transfers();
            if (LOWORD(wparam) == kCancel && handoffId(app->activeId) && app->bridge.runtime) {
                const auto id = app->activeId;
                app->asyncRequest("POST", "/_desktop/handoffs/" + id + "/cancel", Json::object(),
                    [app, id](const HttpResult& value) { if (app->activeId == id) { if (value.error.empty()) app->receipt(value.value); else app->error(value.error); } });
            }
            if (LOWORD(wparam) == kRestart && !app->engine.running() && app->engine.stopped()) app->stop(false);
            return 0;
        case WM_DESTROY: KillTimer(window, 1); PostQuitMessage(0); return 0;
        }
    } catch (const std::exception& error) { if (app) app->error(error.what()); }
    return DefWindowProcW(window, message, wparam, lparam);
}
void printJson(const Json& value) {
    const auto bytes = value.dump() + "\n";
    const auto output = GetStdHandle(STD_OUTPUT_HANDLE); DWORD written = 0;
    if (output && output != INVALID_HANDLE_VALUE) WriteFile(output, bytes.data(), static_cast<DWORD>(bytes.size()), &written, nullptr);
}
}
int WINAPI wWinMain(HINSTANCE instance, HINSTANCE, PWSTR, int show) {
    int argc = 0; auto** argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (!argv) return 2;
    std::string ticket, mode; bool ticketArgument = false;
    if (argc == 2) mode = utf8(argv[1]);
    else if (argc == 3 && std::wstring_view(argv[1]) == L"--ticket-id") { ticketArgument = true; ticket = utf8(argv[2]); }
    else if (argc != 1) { LocalFree(argv); return 2; }
    LocalFree(argv);
    if (mode == "--self-test") { const bool passed = selfTest(); printJson({{"schema", "green-atlas.desktop-self-test/1"}, {"passed", passed}, {"real_windows", true}, {"webview_launched", false}}); return passed ? 0 : 1; }
    if (mode == "--build-info") { auto value = buildInfo(); try { installedSettings(false); value["layout_verified"] = true; } catch (...) { value["layout_verified"] = false; } printJson(value); return 0; }
    if (mode == "--status-json") {
        Json status{{"schema", "green-atlas.desktop-status/1"}, {"qualification", "not_claimed"}};
        try { installedSettings(false); status["layout_verified"] = true; } catch (const std::exception& error) { status["layout_verified"] = false; status["error"] = error.what(); }
        LPWSTR version = nullptr; const auto hr = GetAvailableCoreWebView2BrowserVersionString(nullptr, &version);
        status["webview2_available"] = SUCCEEDED(hr) && version;
        status["webview2_supported"] = false;
        if (version) { status["webview2_version"] = utf8(version); status["webview2_supported"] = supportedWebViewVersion(utf8(version)); CoTaskMemFree(version); }
        printJson(status); return status["layout_verified"] == true && status["webview2_supported"] == true ? 0 : 2;
    }
    if (!mode.empty() || (ticketArgument && !ticketDirectoryId(ticket))) return 2;
    if (FAILED(CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED))) return 3;
    int result = 1;
    try {
        const auto mutexName = L"Local\\" + ipcName();
        // Mutex не передаёт файловые пути или сессионные сведения.
        const auto mutex = CreateMutexW(nullptr, TRUE, mutexName.c_str());
        if (!mutex) throw std::runtime_error("Не удалось открыть single-instance mutex");
        const auto existingMutex = GetLastError() == ERROR_ALREADY_EXISTS;
        const std::unique_ptr<void, decltype(&CloseHandle)> ownedMutex(mutex, CloseHandle);
        if (existingMutex) {
            const auto existing = FindWindowW(kClass, nullptr); if (existing) { ShowWindow(existing, SW_RESTORE); SetForegroundWindow(existing); }
            const bool delivered = ticket.empty() || TicketPipe::forward(ticket);
            CoUninitialize(); return delivered ? 0 : 4;
        }
        LPWSTR browserVersion = nullptr;
        const auto available = GetAvailableCoreWebView2BrowserVersionString(nullptr, &browserVersion);
        const bool supported = SUCCEEDED(available) && browserVersion && supportedWebViewVersion(utf8(browserVersion));
        CoTaskMemFree(browserVersion);
        if (!supported) throw std::runtime_error("Повторите установку Green Atlas, чтобы обновить WebView2 Runtime");
        auto app = std::make_shared<Desktop>();
        app->settings = installedSettings(true);
        WNDCLASSW descriptor{}; descriptor.lpfnWndProc = windowProc; descriptor.hInstance = instance;
        descriptor.lpszClassName = kClass; descriptor.hCursor = LoadCursorW(nullptr, IDC_ARROW);
        descriptor.hbrBackground = reinterpret_cast<HBRUSH>(COLOR_WINDOW + 1);
        if (!RegisterClassW(&descriptor)) throw std::runtime_error("Не удалось зарегистрировать окно");
        app->window = CreateWindowExW(0, kClass, L"Green Atlas", WS_OVERLAPPEDWINDOW, CW_USEDEFAULT, CW_USEDEFAULT,
            1180, 800, nullptr, nullptr, instance, app.get());
        if (!app->window) throw std::runtime_error("Не удалось открыть окно");
        app->showSplash();
        const auto menu = CreateMenu(), project = CreatePopupMenu();
        AppendMenuW(project, MF_STRING, kLibrary, L"Список проектов"); AppendMenuW(project, MF_STRING, kTransfers, L"Передачи AutoCAD");
        AppendMenuW(project, MF_STRING, kCancel, L"Отменить текущую передачу"); AppendMenuW(project, MF_SEPARATOR, 0, nullptr);
        AppendMenuW(project, MF_STRING, kRestart, L"Перезапустить остановленный сервис");
        AppendMenuW(menu, MF_POPUP, reinterpret_cast<UINT_PTR>(project), L"Green Atlas"); SetMenu(app->window, menu);
        app->ipc.start(app->window, kTicket); SetTimer(app->window, 1, 100, nullptr);
        ShowWindow(app->window, show); UpdateWindow(app->window);
        app->launch(); app->startWebView(); if (!ticket.empty()) app->queue(ticket);
        MSG message{};
        while (GetMessageW(&message, nullptr, 0, 0) > 0) { TranslateMessage(&message); DispatchMessageW(&message); }
        result = static_cast<int>(message.wParam);
    } catch (const std::exception& error) { MessageBoxW(nullptr, utf16(error.what()).c_str(), L"Green Atlas", MB_OK | MB_ICONERROR); }
    CoUninitialize(); return result;
}
#else
#include <iostream>
int main() {
    const bool passed = ga::desktop::windows::selfTest();
    std::cout << ga::desktop::windows::Json{{"schema", "green-atlas.desktop-self-test/1"}, {"passed", passed}, {"real_windows", false}}.dump() << '\n';
    return passed ? 0 : 1;
}
#endif
