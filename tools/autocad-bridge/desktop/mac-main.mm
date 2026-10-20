#import <AppKit/AppKit.h>
#import <WebKit/WebKit.h>
#import "project-route.h"
#import "mac-leave-guard.h"
#import "mac-handoff-recovery.h"
#import "mac-status-panel.h"
#import "handoff-observation.h"
#import "mac-runtime-lifecycle.h"
#import "mac-download-response.h"
#import "mac-qualification.h"
#include <signal.h>

// Cold start may publish a WAL-consistent backup of a large existing workspace.
// This I/O happens before the ready receipt and is not an API request timeout.
static constexpr int64_t kColdStartTimeoutSeconds = 10 * 60;
// Сервер ждёт изменение до 25 секунд; транспорт оставляет запас для ответа JSON
static constexpr NSTimeInterval kHandoffWaitRequestTimeoutSeconds = 35;
static constexpr NSTimeInterval kRuntimeShutdownTimeoutSeconds = 10;

@interface Desktop : NSObject <NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKDownloadDelegate, NSURLSessionTaskDelegate>
@property(strong) NSWindow *window;
@property(strong) WKWebView *web;
@property(strong) NSTextField *status;
@property(strong) NSButton *retry;
@property(strong) NSButton *cancel;
@property(strong) GADesktopStatusPanel *statusPanel;
@property(strong) GAHandoffFailure *handoffFailure;
@property(strong) GAHandoffObservation *handoffObservation;
@property(strong) NSTask *engine;
@property(strong) NSPipe *pipe;
@property(strong) NSFileHandle *log;
@property(strong) NSURLSession *network;
@property(strong) NSURLSessionDataTask *handoffWait;
@property(copy) NSString *origin;
@property(copy) NSString *secret;
@property(copy) NSString *identifier;
@property(copy) NSString *activeTicket;
@property(strong) NSMutableArray<NSString *> *tickets;
@property NSUInteger generation;
@property BOOL closing;
@property BOOL ready;
@property BOOL busy;
@property(strong) GADesktopLeaveGuard *leaveGuard;
@property BOOL exitApproved;
@property BOOL quitPending;
@property BOOL startupTimedOut;
@property BOOL runtimeStopping;
@property(strong) GADesktopQualification *qualification;
@property BOOL qualificationStopping;
@property BOOL qualificationWebStarted;
@property(strong) NSMutableDictionary *qualificationProof;
@end

@implementation Desktop
- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    if (!self.tickets) self.tickets = [NSMutableArray array];
    self.handoffObservation = [[GAHandoffObservation alloc] init];
    NSMenu *menu = [[NSMenu alloc] init];
    NSMenuItem *application = [[NSMenuItem alloc] init];
    application.submenu = [[NSMenu alloc] initWithTitle:@"Green Atlas"];
    [application.submenu addItemWithTitle:@"Завершить Green Atlas" action:@selector(terminate:) keyEquivalent:@"q"];
    [menu addItem:application];
    NSMenuItem *file = [[NSMenuItem alloc] init];
    file.submenu = [[NSMenu alloc] initWithTitle:@"Файл"];
    NSMenuItem *open = [file.submenu addItemWithTitle:@"Открыть подготовленный снимок…" action:@selector(openSnapshot:) keyEquivalent:@"o"];
    open.target = self;
    NSMenuItem *projects = [file.submenu addItemWithTitle:@"К проектам" action:@selector(openProjects:) keyEquivalent:@""];
    projects.target = self;
    NSMenuItem *recover = [file.submenu addItemWithTitle:@"Передачи AutoCAD…" action:@selector(openRecoverable:) keyEquivalent:@""];
    recover.target = self;
    [menu addItem:file];
    NSMenuItem *edit = [[NSMenuItem alloc] init];
    edit.submenu = [[NSMenu alloc] initWithTitle:@"Правка"];
    [edit.submenu addItemWithTitle:@"Вырезать" action:@selector(cut:) keyEquivalent:@"x"];
    [edit.submenu addItemWithTitle:@"Копировать" action:@selector(copy:) keyEquivalent:@"c"];
    [edit.submenu addItemWithTitle:@"Вставить" action:@selector(paste:) keyEquivalent:@"v"];
    [edit.submenu addItemWithTitle:@"Выбрать всё" action:@selector(selectAll:) keyEquivalent:@"a"];
    [menu addItem:edit];
    NSApp.mainMenu = menu;
    self.window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 1180, 800)
        styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable
        backing:NSBackingStoreBuffered defer:NO];
    self.window.title = @"Green Atlas";
    self.window.delegate = self;
    if (self.qualification) self.window.restorable = NO;
    self.window.minSize = NSMakeSize(850, 600);
    self.statusPanel = [[GADesktopStatusPanel alloc] initWithTarget:self retry:@selector(retryClicked:)
        cancel:@selector(cancelClicked:) library:@selector(openProjects:)];
    self.status = self.statusPanel.status;
    self.retry = self.statusPanel.retry;
    self.cancel = self.statusPanel.cancel;
    WKWebViewConfiguration *configuration = [[WKWebViewConfiguration alloc] init];
    configuration.websiteDataStore = WKWebsiteDataStore.nonPersistentDataStore;
    self.leaveGuard = [[GADesktopLeaveGuard alloc] init];
    [configuration.userContentController addScriptMessageHandler:self.leaveGuard name:@"greenAtlasDesktop"];
    self.web = [[WKWebView alloc] initWithFrame:NSMakeRect(0, 0, 1180, 750) configuration:configuration];
    self.web.autoresizingMask = NSViewWidthSizable | NSViewHeightSizable;
    self.web.navigationDelegate = self;
    self.leaveGuard.web = self.web;
    __weak Desktop *weakDesktop = self;
    self.leaveGuard.unavailable = ^{
        NSAlert *alert = [[NSAlert alloc] init];
        alert.messageText = @"Дождитесь завершения действия";
        alert.informativeText = @"Приложение ещё проверяет несохранённую работу. Текущая страница остаётся открытой.";
        [alert beginSheetModalForWindow:weakDesktop.window completionHandler:nil];
    };
    [self.window.contentView addSubview:self.web];
    [self.window.contentView addSubview:self.statusPanel];
    [self progressVisible:YES];
    [self.window center];
    [self createNetwork];
    [self launchEngine];
    if (self.qualification) {
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 90 * NSEC_PER_SEC), dispatch_get_main_queue(), ^{
            if (!self.qualification.finished) [self finishQualification:nil error:@"Истёк срок приёмки production Desktop"];
        });
    }
}
- (void)createNetwork {
    NSURLSessionConfiguration *http = NSURLSessionConfiguration.ephemeralSessionConfiguration;
    http.timeoutIntervalForRequest = 60;
    http.HTTPShouldSetCookies = NO;
    http.URLCredentialStorage = nil;
    self.network = [NSURLSession sessionWithConfiguration:http delegate:self delegateQueue:nil];
}
- (void)show { [self.window makeKeyAndOrderFront:nil]; [NSApp activateIgnoringOtherApps:YES]; }
- (void)progressVisible:(BOOL)visible {
    if (!visible) [self.statusPanel clearError];
    self.statusPanel.hidden = !visible;
    NSRect bounds = self.window.contentView.bounds;
    CGFloat height = visible ? (self.statusPanel.loading ? NSHeight(bounds) : self.statusPanel.preferredHeight) : 0;
    self.statusPanel.frame = NSMakeRect(0, NSHeight(bounds) - height, NSWidth(bounds), height);
    if (!self.statusPanel.loading) bounds.size.height = MAX(0, bounds.size.height - height);
    self.web.frame = bounds;
}
- (void)windowDidResize:(NSNotification *)notification { [self progressVisible:!self.statusPanel.hidden]; }
- (BOOL)validateMenuItem:(NSMenuItem *)item {
    if (item.action == @selector(openSnapshot:) || item.action == @selector(openRecoverable:)) return self.ready && !self.busy;
    if (item.action == @selector(openProjects:)) return self.ready;
    return YES;
}
- (void)openSnapshot:(id)sender {
    if (self.busy) return;
    // The factory creates a fresh panel with the documented default configuration.
    // The native handoff and API still validate the selected file and its root.
    NSOpenPanel *panel = [NSOpenPanel openPanel];
    panel.canChooseFiles = YES;
    panel.canChooseDirectories = NO; panel.allowsMultipleSelection = NO;
    panel.allowedContentTypes = @[];
    panel.allowsOtherFileTypes = YES;
    panel.message = @"Выберите transfer.gatransfer, подготовленный плагином AutoCAD.";
    [panel beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse response) {
        if (response == NSModalResponseOK && ![self application:NSApp openFile:panel.URL.path]) [self fail:@"Выберите подготовленный снимок AutoCAD."];
    }];
}
- (void)openProjects:(id)sender {
    if (self.ready) [self guardedProject:@"/projects" action:ga::desktop::Action::library];
}
- (void)openRecoverable:(id)sender {
    if (self.busy) return;
    if (!self.ready) { [self fail:@"Сначала запустите локальный сервис."]; return; }
    NSUInteger generation = self.generation;
    NSTask *engine = self.engine;
    [self request:@"GET" path:@"/_desktop/handoffs" body:nil done:^(NSDictionary *value, NSString *error) {
        if (self.closing || self.generation != generation || self.engine != engine) return;
        if (error || ![value[@"handoffs"] isKindOfClass:NSArray.class]) { [self fail:error ?: @"Не удалось прочитать список передач."]; return; }
        GAChooseHandoff(self.window, value[@"handoffs"], ^(NSDictionary *receipt) {
            if (self.closing || self.generation != generation || self.engine != engine || !self.ready) return;
            if ([receipt[@"status"] isEqual:@"needs_review"]) {
                NSString *path = GAHandoffProjectPath(receipt);
                if (path) [self guardedProject:path action:ga::desktop::Action::handoff];
                return;
            }
            if (self.busy) { [self fail:@"Дождитесь текущей передачи или отмените её."]; return; }
            NSAlert *confirm = [[NSAlert alloc] init]; confirm.messageText = @"Продолжить выбранную передачу?";
            confirm.informativeText = @"Локальный сервис проверит сохранённый снимок и продолжит подготовку проекта.";
            [confirm addButtonWithTitle:@"Продолжить"]; [confirm addButtonWithTitle:@"Отмена"];
            [confirm beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse response) {
                if (response != NSAlertFirstButtonReturn || self.closing || self.generation != generation
                    || self.engine != engine || !self.ready || self.busy) return;
                [self resumeHandoff:receipt[@"id"]];
            }];
        });
    }];
}
- (void)stopHandoffWait {
    [self.handoffWait cancel]; self.handoffWait = nil;
}
- (void)fail:(NSString *)message {
    if (self.qualification) { [self finishQualification:nil error:message]; return; }
    [self stopHandoffWait];
    self.busy = NO;
    [self.statusPanel presentError:message runtimeAvailable:self.ready];
    [self progressVisible:YES];
    [self show];
}
- (BOOL)application:(NSApplication *)application openFile:(NSString *)filename {
    if (!self.tickets) self.tickets = [NSMutableArray array];
    if (![filename.lastPathComponent isEqual:@"transfer.gatransfer"]) return NO;
    if (![self.tickets containsObject:filename] && ![filename isEqual:self.activeTicket]) [self.tickets addObject:filename];
    [self nextTicket];
    return YES;
}
- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)app { return YES; }
- (BOOL)applicationShouldHandleReopen:(NSApplication *)app hasVisibleWindows:(BOOL)visible { [self show]; return YES; }
- (BOOL)windowShouldClose:(NSWindow *)window {
    if (self.exitApproved) return YES;
    [self.leaveGuard request:ga::desktop::Action::close target:nil completion:^(BOOL allowed) {
        if (allowed) { self.exitApproved = YES; [self.window performClose:nil]; }
    }];
    return NO;
}
- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)app {
    // Приёмка уже дождалась своего API и записала результат. NSTerminateLater
    // из main dispatch handler заблокировал бы поставленный туда же reply.
    if (self.qualification.finished && self.exitApproved && !self.engine.running) return NSTerminateNow;
    if (self.quitPending) return NSTerminateLater;
    self.quitPending = YES;
    if (self.exitApproved) {
        [self finishQuit:app];
        return NSTerminateLater;
    }
    BOOL requested = [self.leaveGuard request:ga::desktop::Action::quit target:nil completion:^(BOOL allowed) {
        // AppKit expects its reply after applicationShouldTerminate returns.
        dispatch_async(dispatch_get_main_queue(), ^{
            if (!self.quitPending) return;
            self.exitApproved = allowed;
            if (allowed) [self finishQuit:app];
            else { self.quitPending = NO; [app replyToApplicationShouldTerminate:NO]; }
        });
    }];
    if (!requested) { self.quitPending = NO; return NSTerminateCancel; }
    return NSTerminateLater;
}
- (void)finishQuit:(NSApplication *)app {
    if (self.runtimeStopping) return;
    self.runtimeStopping = YES; self.closing = YES;
    self.ready = NO; self.generation++;
    [self.leaveGuard runtimeAvailable:NO];
    [self stopHandoffWait];
    [self.network invalidateAndCancel]; self.network = nil;
    self.pipe.fileHandleForReading.readabilityHandler = nil;
    GAStopRuntime(self.engine, kRuntimeShutdownTimeoutSeconds, ^(BOOL stopped) {
        self.runtimeStopping = NO; self.quitPending = NO;
        if (stopped) [app replyToApplicationShouldTerminate:YES];
        else {
            self.closing = NO; self.exitApproved = NO;
            [app replyToApplicationShouldTerminate:NO];
            [self fail:@"Локальный сервис ещё завершает работу. Повторите выход"];
        }
    });
}
- (void)applicationWillTerminate:(NSNotification *)notification {
    self.closing = YES;
    [self stopHandoffWait];
    [self.network invalidateAndCancel];
    self.pipe.fileHandleForReading.readabilityHandler = nil;
    if (self.engine.running) [self.engine terminate];
    [self.log closeFile];
}
- (void)URLSession:(NSURLSession *)session task:(NSURLSessionTask *)task willPerformHTTPRedirection:(NSHTTPURLResponse *)response newRequest:(NSURLRequest *)request completionHandler:(void (^)(NSURLRequest *))completionHandler {
    completionHandler(nil); // Never forward the private local session to a redirect.
}
- (void)webView:(WKWebView *)webView decidePolicyForNavigationAction:(WKNavigationAction *)action decisionHandler:(void (^)(WKNavigationActionPolicy))decisionHandler {
    NSURL *url = action.request.URL;
    NSURL *origin = [NSURL URLWithString:self.origin];
    BOOL local = [url.scheme isEqual:origin.scheme] && [url.host isEqual:origin.host] && [url.port isEqual:origin.port] && !url.user && !url.password;
    if (local && GAIsReleaseArtifactPath(url.path) && !url.query && !url.fragment) {
        decisionHandler(WKNavigationActionPolicyDownload);
        return;
    }
    decisionHandler(local || [url.absoluteString isEqual:@"about:blank"] ? WKNavigationActionPolicyAllow : WKNavigationActionPolicyCancel);
}
- (void)webView:(WKWebView *)webView didFinishNavigation:(WKNavigation *)navigation {
    if (!self.qualification || self.qualificationWebStarted || self.qualificationStopping || !self.ready) return;
    self.qualificationWebStarted = YES;
    [webView callAsyncJavaScript:GADesktopQualificationJavaScript() arguments:@{} inFrame:nil
        inContentWorld:WKContentWorld.pageWorld completionHandler:^(id result, NSError *error) {
            if (self.qualificationStopping) return;
            if (error || !GADesktopQualificationWebProof(result)) {
                [self finishQualification:nil error:@"Настоящий WebView не подтвердил DOM, health и пустую библиотеку"];
                return;
            }
            [self finishQualification:result error:nil];
        }];
}
- (void)webView:(WKWebView *)webView didFailProvisionalNavigation:(WKNavigation *)navigation withError:(NSError *)error {
    if (self.qualification) [self finishQualification:nil error:@"Не удалось загрузить production Web в WKWebView"];
}
- (void)webView:(WKWebView *)webView didFailNavigation:(WKNavigation *)navigation withError:(NSError *)error {
    if (self.qualification) [self finishQualification:nil error:@"Загрузка production Web в WKWebView завершилась ошибкой"];
}
- (void)finishQualification:(NSDictionary *)proof error:(NSString *)failure {
    if (!self.qualification || self.qualificationStopping || self.qualification.finished) return;
    self.qualificationStopping = YES;
    self.closing = YES;
    self.qualificationProof = proof ? [proof mutableCopy] : [NSMutableDictionary dictionary];
    [self stopHandoffWait];
    void (^waitForExit)(NSString *) = ^(NSString *reason) {
        self.closing = YES;
        const NSTimeInterval deadline = NSProcessInfo.processInfo.systemUptime + 15;
        dispatch_source_t timer = dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER, 0, 0, dispatch_get_main_queue());
        dispatch_source_set_timer(timer, DISPATCH_TIME_NOW, 50 * NSEC_PER_MSEC, 0);
        dispatch_source_set_event_handler(timer, ^{
            if (self.engine.running && NSProcessInfo.processInfo.systemUptime < deadline) return;
            if (self.engine.running) {
                // Только NSTask этого запуска; чужие PID и группы не затрагиваются.
                kill(self.engine.processIdentifier, SIGKILL);
                [self.engine waitUntilExit];
            }
            dispatch_source_cancel(timer);
            const int status = self.engine.processIdentifier > 0 ? self.engine.terminationStatus : -1;
            [self.qualification finish:self.qualificationProof runtimePID:self.engine.processIdentifier
                runtimeExit:status error:reason ?: (status == 0 ? nil : @"Собственный API завершился с ошибкой")];
            self.exitApproved = YES;
            [NSApp terminate:nil];
        });
        dispatch_source_set_cancel_handler(timer, ^{ dispatch_source_set_event_handler(timer, nil); });
        dispatch_resume(timer);
    };
    if (failure || !self.ready || !self.engine.running) {
        if (self.engine.running) [self.engine terminate];
        waitForExit(failure ?: @"API не объявил готовность");
        return;
    }
    // Обычный request отбрасывает ответ уже завершившегося runtime. Здесь такой
    // выход ожидается: квитанцию shutdown проверяем независимо от running.
    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:
        [NSURL URLWithString:[self.origin stringByAppendingString:@"/_desktop/shutdown"]]];
    request.HTTPMethod = @"POST"; request.timeoutInterval = 10;
    request.HTTPBody = [@"{}" dataUsingEncoding:NSUTF8StringEncoding];
    [request setValue:@"application/json" forHTTPHeaderField:@"Content-Type"];
    [request setValue:self.secret forHTTPHeaderField:@"X-Green-Atlas-Local-Session"];
    [[self.network dataTaskWithRequest:request completionHandler:^(NSData *data, NSURLResponse *response, NSError *error) {
        id value = data.length > 0 && data.length < 4096 ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
        BOOL accepted = !error && [response isKindOfClass:NSHTTPURLResponse.class]
            && [(NSHTTPURLResponse *)response statusCode] == 202
            && [value isKindOfClass:NSDictionary.class] && [value[@"state"] isEqual:@"stopping"];
        dispatch_async(dispatch_get_main_queue(), ^{
            self.qualificationProof[@"shutdown_202"] = @(accepted);
            if (!accepted && self.engine.running) [self.engine terminate];
            waitForExit(accepted ? nil : @"API не подтвердил graceful shutdown");
        });
    }] resume];
}
- (void)webView:(WKWebView *)webView navigationAction:(WKNavigationAction *)navigationAction didBecomeDownload:(WKDownload *)download {
    download.delegate = self;
}
- (void)webView:(WKWebView *)webView navigationResponse:(WKNavigationResponse *)navigationResponse didBecomeDownload:(WKDownload *)download {
    download.delegate = self;
}
- (void)download:(WKDownload *)download decideDestinationUsingResponse:(NSURLResponse *)response suggestedFilename:(NSString *)suggestedFilename completionHandler:(void (^)(NSURL * _Nullable))completionHandler {
    if (GARejectReleaseDownloadResponse(response, completionHandler, ^(NSString *explanation) {
        NSAlert *alert = [[NSAlert alloc] init];
        alert.messageText = @"Не удалось скачать файл";
        alert.informativeText = explanation;
        [alert beginSheetModalForWindow:self.window completionHandler:nil];
    })) return;
    // A fresh panel is important here: WebKit can offer ZIP, CSV and DWG
    // downloads in one session, and a reused panel may keep the prior type filter.
    NSSavePanel *panel = [[NSSavePanel alloc] init];
    panel.allowedContentTypes = @[];
    panel.allowsOtherFileTypes = YES;
    panel.canCreateDirectories = YES;
    panel.nameFieldStringValue = suggestedFilename.length ? suggestedFilename : @"green-atlas-artifact";
    panel.directoryURL = [[NSFileManager defaultManager] URLsForDirectory:NSDownloadsDirectory inDomains:NSUserDomainMask].firstObject;
    [panel beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse result) {
        completionHandler(result == NSModalResponseOK ? panel.URL : nil);
    }];
}
- (void)download:(WKDownload *)download didFailWithError:(NSError *)error resumeData:(NSData *)resumeData {
    if ([error.domain isEqualToString:NSURLErrorDomain] && error.code == NSURLErrorCancelled) return;
    NSAlert *alert = [[NSAlert alloc] init];
    alert.messageText = @"Не удалось скачать файл";
    alert.informativeText = error.localizedDescription ?: @"Повторите загрузку из проекта.";
    [alert beginSheetModalForWindow:self.window completionHandler:nil];
}
- (void)launchEngine {
    if (self.engine.running) return;
    self.generation++;
    [self stopHandoffWait];
    [self.statusPanel clearError];
    [self.statusPanel presentProgress:@"Запускаем Green Atlas" autocadRequired:YES];
    [self progressVisible:YES];
    self.ready = NO;
    self.startupTimedOut = NO;
    [self.leaveGuard runtimeAvailable:NO];
    if (!self.network) [self createNetwork];
    self.retry.hidden = YES;
    self.status.stringValue = @"Запускаем Green Atlas";
    NSURL *resources = NSBundle.mainBundle.resourceURL;
    NSURL *executable = [resources URLByAppendingPathComponent:@"Runtime/GreenAtlasRuntime"];
    NSURL *web = [resources URLByAppendingPathComponent:@"Web"];
    NSURL *base = self.qualification.profile;
    if (!base) {
        NSURL *support = [NSFileManager.defaultManager URLsForDirectory:NSApplicationSupportDirectory inDomains:NSUserDomainMask].firstObject;
        base = [support URLByAppendingPathComponent:@"Green Atlas"];
        NSString *testProfile = [NSBundle.mainBundle objectForInfoDictionaryKey:@"GADevelopmentProfile"];
        if (testProfile.length) { base = [NSURL fileURLWithPath:testProfile]; self.window.title = @"Green Atlas — проверка"; }
    }
    NSURL *data = [base URLByAppendingPathComponent:@"Workspace"];
    NSURL *transfers = [base URLByAppendingPathComponent:@"Transfers"];
    NSError *error;
    if (![NSFileManager.defaultManager createDirectoryAtURL:data withIntermediateDirectories:YES attributes:@{NSFilePosixPermissions:@0700} error:&error]) { [self fail:@"Не удалось открыть локальное хранилище."]; return; }
    if (self.qualification) {
        for (NSString *name in @[@"Transfers", @"Temp"]) {
            if (![NSFileManager.defaultManager createDirectoryAtURL:[base URLByAppendingPathComponent:name]
                withIntermediateDirectories:NO attributes:@{NSFilePosixPermissions:@0700} error:&error]) {
                [self fail:@"Не удалось создать каталоги одноразового профиля"]; return;
            }
        }
    }
    [self.log closeFile];
    self.log = GAOpenRuntimeLog(base, &error);
    if (!self.log) { [self fail:@"Не удалось открыть локальный журнал запуска"]; return; }
    self.engine = [[NSTask alloc] init];
    self.engine.executableURL = executable;
    if (self.qualification) self.engine.environment = [self.qualification runtimeEnvironment];
    NSMutableArray *arguments = [NSMutableArray arrayWithArray:@[@"--data-dir", data.path, @"--web-dir", web.path, @"--ticket-root", transfers.path]];
    if (self.origin.length) {
        const auto port = ga::desktop::loopbackPort(self.origin.UTF8String);
        if (!port) { [self fail:@"Адрес локального сервиса несовместим. Закройте приложение и откройте его заново."]; return; }
        [arguments addObjectsFromArray:@[@"--listen-port", [NSString stringWithFormat:@"%u", *port]]];
    }
    NSURL *packageContents = [NSBundle.mainBundle.bundleURL.URLByDeletingLastPathComponent URLByDeletingLastPathComponent];
    NSURL *cadWorker = [packageContents URLByAppendingPathComponent:@"Workers/GreenAtlasQuery.bundle"];
    if ([NSFileManager.defaultManager fileExistsAtPath:cadWorker.path]) {
        [arguments addObjectsFromArray:@[@"--cad-worker", cadWorker.path]];
    }
    self.engine.arguments = arguments;
    self.pipe = [NSPipe pipe]; self.engine.standardOutput = self.pipe; self.engine.standardError = self.log;
    __weak Desktop *weakSelf = self;
    NSTask *launched = self.engine;
    NSFileHandle *exitLog = GARuntimeExitLog(self.log);
    NSMutableData *buffer = [NSMutableData data];
    self.pipe.fileHandleForReading.readabilityHandler = ^(NSFileHandle *handle) {
        NSData *data = handle.availableData;
        if (!data.length) { handle.readabilityHandler = nil; return; }
        [buffer appendData:data];
        if (buffer.length > 16384) { handle.readabilityHandler = nil; [launched terminate]; return; }
        NSRange end = [buffer rangeOfData:[@"\n" dataUsingEncoding:NSUTF8StringEncoding] options:0 range:NSMakeRange(0, buffer.length)];
        if (end.location == NSNotFound) return;
        handle.readabilityHandler = nil;
        id receipt = [NSJSONSerialization JSONObjectWithData:[buffer subdataWithRange:NSMakeRange(0, end.location)] options:0 error:nil];
        dispatch_async(dispatch_get_main_queue(), ^{ if (weakSelf.engine == launched) [weakSelf acceptReady:receipt]; });
    };
    self.engine.terminationHandler = ^(NSTask *task) {
        GARecordRuntimeExit(exitLog, task, weakSelf.closing,
            weakSelf.engine == task && weakSelf.startupTimedOut);
        dispatch_async(dispatch_get_main_queue(), ^{
            if (!weakSelf.closing && weakSelf.engine == task) {
                weakSelf.ready = NO; weakSelf.generation++;
                [weakSelf stopHandoffWait];
                [weakSelf.handoffObservation rememberRuntimeStop:weakSelf.identifier];
                weakSelf.busy = NO; weakSelf.activeTicket = nil; weakSelf.identifier = nil;
                [weakSelf.leaveGuard runtimeAvailable:NO];
                [weakSelf.network invalidateAndCancel]; weakSelf.network = nil;
                NSString *message = weakSelf.startupTimedOut
                    ? @"Подготовка локального хранилища заняла слишком много времени. Можно повторить запуск."
                    : @"Локальный движок остановлен. Можно повторить запуск.";
                if (weakSelf.handoffFailure) message = [message stringByAppendingFormat:
                    @"\n\nПоследняя ошибка передачи: %@", weakSelf.handoffFailure.message];
                [weakSelf fail:message];
            }
        });
    };
    if (![self.engine launchAndReturnError:&error]) { [self fail:@"Не удалось запустить Green Atlas. Проверьте установку приложения."]; return; }
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, kColdStartTimeoutSeconds * NSEC_PER_SEC), dispatch_get_main_queue(), ^{
        if (weakSelf.engine == launched && !weakSelf.ready && launched.running) {
            weakSelf.startupTimedOut = YES;
            [launched terminate];
        }
    });
}
- (void)acceptReady:(id)receipt {
    if (![receipt isKindOfClass:NSDictionary.class] || ![receipt[@"service"] isEqual:@"green-atlas-desktop"] || ![receipt[@"protocol"] isEqual:@1]) { [self.engine terminate]; return; }
    NSString *origin = receipt[@"origin"], *secret = receipt[@"session_token"];
    if (![origin isKindOfClass:NSString.class] || ![secret isKindOfClass:NSString.class]) { [self.engine terminate]; return; }
    if (!ga::desktop::runtimeSessionReady(self.origin ? self.origin.UTF8String : "", self.secret ? self.secret.UTF8String : "", origin.UTF8String, secret.UTF8String)) { [self.engine terminate]; return; }
    BOOL preservingDocument = self.origin.length && [self.leaveGuard documentReady];
    NSTask *acceptedEngine = self.engine;
    self.origin = origin; self.secret = secret;
    NSHTTPCookie *cookie = [NSHTTPCookie cookieWithProperties:@{NSHTTPCookieName:@"green_atlas_local_session", NSHTTPCookieValue:secret, NSHTTPCookieDomain:@"127.0.0.1", NSHTTPCookiePath:@"/", @"HttpOnly":@"TRUE", @"SameSite":@"Strict"}];
    [self.web.configuration.websiteDataStore.httpCookieStore setCookie:cookie completionHandler:^{
        dispatch_async(dispatch_get_main_queue(), ^{
            if (self.engine != acceptedEngine || !acceptedEngine.running || ![self.secret isEqual:secret] || self.closing) return;
            self.ready = YES;
            [self.leaveGuard runtimeAvailable:YES];
            if (preservingDocument) {
                self.busy = NO; self.activeTicket = nil; self.identifier = nil;
                self.retry.hidden = YES; self.cancel.hidden = YES;
                [self progressVisible:NO];
                // Interrupted handoffs require explicit journal resume. Keep the
                // current document and its write proof; never retry its POST.
                [self readStoppedHandoff]; [self nextTicket]; return;
            }
            if (self.tickets.count) [self nextTicket];
            else { [self openProject:@"/projects"]; [self readStoppedHandoff]; }
        });
    }];
}
- (void)readStoppedHandoff {
    [self.handoffObservation readFailureIfIdle:!self.busy && !self.tickets.count
        read:^(NSString *path, void (^done)(NSDictionary *, NSString *)) {
            [self request:@"GET" path:path body:nil done:done];
        } observed:^(NSString *identifier, NSDictionary *receipt) {
            self.identifier = identifier; [self handleReceipt:receipt];
    }];
}
- (NSURLSessionDataTask *)request:(NSString *)method path:(NSString *)path body:(NSDictionary *)body done:(void (^)(NSDictionary *, NSString *))done {
    if (!self.ready || !self.secret || !self.origin) { done(nil, @"Локальный движок недоступен."); return nil; }
    NSUInteger generation = self.generation;
    NSTask *engine = self.engine;
    NSString *secret = self.secret;
    NSString *origin = self.origin;
    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:[NSURL URLWithString:[self.origin stringByAppendingString:path]]];
    if ([path containsString:@"?after="]) request.timeoutInterval = kHandoffWaitRequestTimeoutSeconds;
    request.HTTPMethod = method; [request setValue:self.secret forHTTPHeaderField:@"X-Green-Atlas-Local-Session"];
    if (body) { request.HTTPBody = [NSJSONSerialization dataWithJSONObject:body options:0 error:nil]; [request setValue:@"application/json" forHTTPHeaderField:@"Content-Type"]; }
    NSURLSessionDataTask *task = [self.network dataTaskWithRequest:request completionHandler:^(NSData *data, NSURLResponse *response, NSError *error) {
        id result = data.length > 0 && data.length < 32768 ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
        NSInteger status = [response isKindOfClass:NSHTTPURLResponse.class] ? [(NSHTTPURLResponse *)response statusCode] : 0;
        dispatch_async(dispatch_get_main_queue(), ^{
            if (self.closing || self.generation != generation || self.engine != engine
                || !engine.running || !self.ready || ![self.secret isEqual:secret]
                || ![self.origin isEqual:origin]) return;
            NSMutableDictionary *value = [result isKindOfClass:NSDictionary.class] ? [result mutableCopy] : nil;
            // Признак принадлежит транспорту: тело ответа не может подменить HTTP404.
            value[@"_ga_confirmed_not_found"] = @(!error && status == 404 && [value[@"code"] isEqual:@"NOT_FOUND"]);
            done(value, GADesktopRequestError(value, status, error != nil));
        });
    }];
    [task resume];
    return task;
}
- (void)nextTicket {
    if (!self.ready || self.busy || !self.tickets.count) return;
    self.handoffFailure = nil;
    [self.handoffObservation clear];
    [self.statusPanel clearError];
    [self.statusPanel presentProgress:@"Принимаем чертёж AutoCAD" autocadRequired:YES];
    [self progressVisible:YES];
    self.busy = YES; self.retry.hidden = YES; self.cancel.hidden = YES;
    self.activeTicket = self.tickets.firstObject; [self.tickets removeObjectAtIndex:0];
    self.generation++;
    [self stopHandoffWait];
    self.identifier = GAHandoffIdentifier(self.activeTicket);
    if (!self.identifier) { [self fail:@"Не удалось определить адрес передачи"]; return; }
    self.status.stringValue = @"Принимаем чертёж AutoCAD";
    [self show];
    [self request:@"POST" path:@"/_desktop/handoffs" body:@{@"ticket":self.activeTicket} done:^(NSDictionary *receipt, NSString *error) {
        if (error) { [self fail:error]; return; }
        NSString *identifier = receipt[@"id"];
        if (![identifier isKindOfClass:NSString.class] || ![identifier isEqual:self.identifier]) { [self fail:@"Движок вернул неверное состояние передачи."]; return; }
        self.identifier = identifier;
        [self handleReceipt:receipt];
    }];
}
- (void)handleReceipt:(NSDictionary *)receipt {
    [self stopHandoffWait];
    if (![receipt[@"id"] isEqual:self.identifier]) {
        [self fail:@"Ответ относится к другой передаче"]; return;
    }
    if ([receipt[@"status"] isEqual:@"needs_review"]) {
        NSString *path = GAHandoffProjectPath(receipt);
        if (!path) { [self fail:@"Проект сохранён, но приложение не смогло открыть его страницу. Адрес проекта несовместим с этой версией приложения."]; return; }
        self.busy = YES; self.activeTicket = nil; self.identifier = nil;
        [self progressVisible:NO];
        [self guardedProject:path action:ga::desktop::Action::handoff]; return;
    }
    GAHandoffFailure *failure = [GAHandoffFailure fromReceipt:receipt];
    if (failure) {
        self.busy = NO;
        self.handoffFailure = failure;
        [self.statusPanel presentFailure:failure runtimeAvailable:self.ready];
        [self progressVisible:YES]; [self show]; return;
    }
    if (![@[@"queued", @"processing"] containsObject:receipt[@"status"]]) {
        [self fail:@"Движок вернул неизвестное состояние передачи"]; return;
    }
    NSString *stage = [receipt[@"stage"] isKindOfClass:NSString.class] ? receipt[@"stage"] : @"Подготавливаем проект";
    BOOL autocadRequired = ![receipt[@"autocad_required"] isEqual:@NO];
    [self.statusPanel presentProgress:stage autocadRequired:autocadRequired];
    [self progressVisible:YES];
    self.busy = YES; self.cancel.hidden = NO;
    NSNumber *revision = receipt[@"revision"];
    if (![revision isKindOfClass:NSNumber.class]
        || CFGetTypeID((__bridge CFTypeRef)revision) == CFBooleanGetTypeID()
        || [revision.stringValue rangeOfCharacterFromSet:NSCharacterSet.decimalDigitCharacterSet.invertedSet].location != NSNotFound
        || revision.longLongValue < 0 || revision.unsignedLongLongValue > INT64_MAX) {
        [self fail:@"Движок вернул неверную ревизию передачи"]; return;
    }
    NSString *identifier = self.identifier;
    NSString *ticket = self.activeTicket;
    NSUInteger generation = self.generation;
    NSTask *engine = self.engine;
    self.handoffWait = [self request:@"GET"
        path:[NSString stringWithFormat:@"/_desktop/handoffs/%@?after=%@", identifier, revision]
        body:nil done:^(NSDictionary *value, NSString *error) {
            if (self.closing || self.generation != generation || self.engine != engine || !engine.running
                || !self.ready || !self.busy || ![self.identifier isEqual:identifier]
                || (self.activeTicket != ticket && ![self.activeTicket isEqual:ticket])) return;
            self.handoffWait = nil;
            if (error) [self fail:error]; else [self handleReceipt:value];
        }];
}
- (void)openProject:(NSString *)path {
    if (!ga::desktop::projectRoute(path.UTF8String)) return;
    self.status.stringValue = @""; self.retry.hidden = YES; self.cancel.hidden = YES;
    [self progressVisible:NO];
    [self.leaveGuard newDocument:self.origin];
    [self.web loadRequest:[NSURLRequest requestWithURL:[NSURL URLWithString:[self.origin stringByAppendingString:path]]]];
    [self show];
}
- (void)guardedProject:(NSString *)path action:(ga::desktop::Action)action {
    NSUInteger generation = self.generation;
    NSTask *engine = self.engine;
    BOOL finishingHandoff = action == ga::desktop::Action::handoff && self.busy && !self.identifier && !self.activeTicket;
    BOOL requested = [self.leaveGuard request:action target:path completion:^(BOOL allowed) {
        if (finishingHandoff) {
            if (self.closing || self.generation != generation || self.engine != engine) return;
            self.busy = NO;
        }
        if (allowed) {
            if (action == ga::desktop::Action::library) {
                // Stop observing this handoff only after the shared leave policy
                // permits navigation. Its journal remains available for recovery.
                self.generation++;
                [self stopHandoffWait];
                self.busy = NO; self.activeTicket = nil; self.identifier = nil;
                self.handoffFailure = nil;
                [self.handoffObservation clear];
            }
            [self openProject:path];
        }
        else if (action == ga::desktop::Action::handoff) {
            [self progressVisible:YES];
            self.status.stringValue = @"Новый проект сохранён, откройте его в списке проектов";
            NSString *notice = self.status.stringValue;
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 6 * NSEC_PER_SEC), dispatch_get_main_queue(), ^{
                if (!self.busy && [self.status.stringValue isEqual:notice]) [self progressVisible:NO];
            });
        }
        if (finishingHandoff) [self nextTicket];
    }];
    if (!requested && finishingHandoff) self.busy = NO;
}
- (void)retryClicked:(id)sender {
    if (self.retry.hidden) return;
    self.retry.hidden = YES;
    if (!self.engine.running) { [self launchEngine]; return; }
    if (self.identifier) {
        if (self.handoffFailure) { [self resumeHandoff:self.identifier]; return; }
        self.generation++;
        [self stopHandoffWait];
        NSString *identifier = self.identifier;
        self.busy = YES;
        [self.statusPanel presentProgress:@"Проверяем результат передачи" autocadRequired:YES];
        [self progressVisible:YES];
        [self request:@"GET" path:[@"/_desktop/handoffs/" stringByAppendingString:identifier] body:nil
            done:^(NSDictionary *receipt, NSString *error) {
                if (error) {
                    if (self.activeTicket && [receipt[@"_ga_confirmed_not_found"] isEqual:@YES]) {
                        // Следующее явное нажатие повторит тот же идемпотентный ticket.
                        // Ответ 404 сам по себе не запускает новую запись.
                        self.identifier = nil;
                        [self fail:@"Передача не найдена"]; return;
                    }
                    [self fail:error];
                } else [self handleReceipt:receipt];
        }];
        return;
    }
    if (self.activeTicket) { [self.tickets insertObject:self.activeTicket atIndex:0]; self.activeTicket = nil; }
    self.busy = NO; self.identifier = nil; [self nextTicket];
}
- (void)resumeHandoff:(NSString * _Nonnull)identifier {
    if (self.closing) return;
    if (!self.ready || !self.engine.running) { self.busy = NO; [self fail:@"Локальный движок недоступен."]; return; }
    self.generation++;
    [self stopHandoffWait];
    NSUInteger generation = self.generation;
    NSTask *engine = self.engine;
    self.identifier = identifier; self.activeTicket = nil; self.busy = YES;
    self.handoffFailure = nil;
    [self.handoffObservation clear];
    [self.statusPanel clearError];
    self.retry.hidden = YES; self.cancel.hidden = YES;
    [self.statusPanel presentProgress:@"Продолжаем передачу AutoCAD" autocadRequired:YES];
    [self progressVisible:YES]; [self show];
    [self request:@"POST" path:[NSString stringWithFormat:@"/_desktop/handoffs/%@/resume", identifier] body:@{} done:^(NSDictionary *receipt, NSString *error) {
        if (self.closing || self.generation != generation || self.engine != engine
            || !engine.running || !self.ready || ![self.identifier isEqual:identifier]) return;
        if (error) { self.busy = NO; [self fail:error]; return; }
        [self handleReceipt:receipt];
    }];
}
- (void)cancelClicked:(id)sender {
    if (!self.identifier) return;
    self.generation++; // Поздняя квитанция не меняет результат явной отмены
    [self stopHandoffWait];
    self.cancel.hidden = YES;
    [self request:@"POST" path:[NSString stringWithFormat:@"/_desktop/handoffs/%@/cancel", self.identifier] body:nil done:^(NSDictionary *receipt, NSString *error) {
        if (error) { [self fail:error]; return; }
        if (![receipt[@"id"] isEqual:self.identifier]) { [self fail:@"Ответ относится к другой передаче"]; return; }
        if ([receipt[@"status"] isEqual:@"needs_review"]) { [self handleReceipt:receipt]; return; }
        if (![receipt[@"status"] isEqual:@"cancelled"]) { [self fail:@"Движок не подтвердил отмену передачи"]; return; }
        self.busy = NO; self.activeTicket = nil; self.identifier = nil;
        self.handoffFailure = nil; [self.statusPanel clearError]; [self progressVisible:YES];
        self.cancel.hidden = YES; self.status.stringValue = @"Подготовка отменена";
        [self nextTicket];
    }];
}
@end

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSError *qualificationError;
        GADesktopQualification *qualification = GAParseDesktopQualification(argc, argv, &qualificationError);
        if (qualificationError) { fprintf(stderr, "%s\n", qualificationError.localizedDescription.UTF8String); return 2; }
        if (qualification) GAConfigureDesktopQualificationPreferences();
        [NSApplication sharedApplication];
        [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
        Desktop *delegate = [[Desktop alloc] init];
        delegate.qualification = qualification;
        delegate.tickets = [NSMutableArray array];
        if (!qualification && argc == 2) [delegate.tickets addObject:@(argv[1])];
        NSApp.delegate = delegate;
        [NSApp run];
        if (qualification) return qualification.finished && qualification.passed ? 0 : 1;
    }
    return 0;
}
