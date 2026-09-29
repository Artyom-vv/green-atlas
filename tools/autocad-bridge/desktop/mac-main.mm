#import <AppKit/AppKit.h>
#import <WebKit/WebKit.h>
#import "project-route.h"

@interface Desktop : NSObject <NSApplicationDelegate, WKNavigationDelegate, WKDownloadDelegate, NSURLSessionTaskDelegate>
@property(strong) NSWindow *window;
@property(strong) WKWebView *web;
@property(strong) NSTextField *status;
@property(strong) NSButton *retry;
@property(strong) NSButton *cancel;
@property(strong) NSTask *engine;
@property(strong) NSPipe *pipe;
@property(strong) NSFileHandle *log;
@property(strong) NSURLSession *network;
@property(copy) NSString *origin;
@property(copy) NSString *secret;
@property(copy) NSString *identifier;
@property(copy) NSString *activeTicket;
@property(strong) NSMutableArray<NSString *> *tickets;
@property NSUInteger generation;
@property BOOL closing;
@property BOOL ready;
@property BOOL busy;
@end

@implementation Desktop
- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    if (!self.tickets) self.tickets = [NSMutableArray array];
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
    self.window.minSize = NSMakeSize(850, 600);
    self.status = [NSTextField labelWithString:@"Запускаем Green Atlas…"];
    self.status.frame = NSMakeRect(20, 766, 890, 22);
    self.status.autoresizingMask = NSViewWidthSizable | NSViewMinYMargin;
    [self.window.contentView addSubview:self.status];
    self.retry = [NSButton buttonWithTitle:@"Повторить" target:self action:@selector(retryClicked:)];
    self.retry.frame = NSMakeRect(1040, 762, 120, 30);
    self.retry.autoresizingMask = NSViewMinXMargin | NSViewMinYMargin;
    self.retry.hidden = YES; [self.window.contentView addSubview:self.retry];
    self.cancel = [NSButton buttonWithTitle:@"Отменить" target:self action:@selector(cancelClicked:)];
    self.cancel.frame = NSMakeRect(1040, 762, 120, 30);
    self.cancel.autoresizingMask = NSViewMinXMargin | NSViewMinYMargin;
    self.cancel.hidden = YES; [self.window.contentView addSubview:self.cancel];
    WKWebViewConfiguration *configuration = [[WKWebViewConfiguration alloc] init];
    configuration.websiteDataStore = WKWebsiteDataStore.nonPersistentDataStore;
    self.web = [[WKWebView alloc] initWithFrame:NSMakeRect(0, 0, 1180, 750) configuration:configuration];
    self.web.autoresizingMask = NSViewWidthSizable | NSViewHeightSizable;
    self.web.navigationDelegate = self;
    [self.window.contentView addSubview:self.web];
    [self.window center];
    NSURLSessionConfiguration *http = NSURLSessionConfiguration.ephemeralSessionConfiguration;
    http.timeoutIntervalForRequest = 60;
    http.HTTPShouldSetCookies = NO;
    http.URLCredentialStorage = nil;
    self.network = [NSURLSession sessionWithConfiguration:http delegate:self delegateQueue:nil];
    [self launchEngine];
}
- (void)show { [self.window makeKeyAndOrderFront:nil]; [NSApp activateIgnoringOtherApps:YES]; }
- (void)progressVisible:(BOOL)visible {
    self.status.hidden = !visible;
    NSRect bounds = self.window.contentView.bounds;
    bounds.size.height = MAX(0, bounds.size.height - (visible ? 50 : 0));
    self.web.frame = bounds;
}
- (void)openSnapshot:(id)sender {
    NSOpenPanel *panel = [NSOpenPanel openPanel];
    panel.canChooseDirectories = NO; panel.allowsMultipleSelection = NO;
    panel.message = @"Выберите transfer.gatransfer, подготовленный плагином AutoCAD.";
    [panel beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse response) {
        if (response == NSModalResponseOK && ![self application:NSApp openFile:panel.URL.path]) [self fail:@"Выберите подготовленный снимок AutoCAD."];
    }];
}
- (void)openProjects:(id)sender {
    if (self.ready) [self openProject:@"/projects"];
}
- (void)fail:(NSString *)message {
    [self progressVisible:YES];
    self.status.stringValue = message;
    self.retry.hidden = NO; self.cancel.hidden = YES;
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
- (void)applicationWillTerminate:(NSNotification *)notification {
    self.closing = YES;
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
- (void)webView:(WKWebView *)webView navigationAction:(WKNavigationAction *)navigationAction didBecomeDownload:(WKDownload *)download {
    download.delegate = self;
}
- (void)webView:(WKWebView *)webView navigationResponse:(WKNavigationResponse *)navigationResponse didBecomeDownload:(WKDownload *)download {
    download.delegate = self;
}
- (void)download:(WKDownload *)download decideDestinationUsingResponse:(NSURLResponse *)response suggestedFilename:(NSString *)suggestedFilename completionHandler:(void (^)(NSURL * _Nullable))completionHandler {
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
    [self progressVisible:YES];
    self.ready = NO; self.origin = nil; self.secret = nil;
    self.retry.hidden = YES;
    self.status.stringValue = @"Запускаем Green Atlas…";
    NSURL *resources = NSBundle.mainBundle.resourceURL;
    NSURL *executable = [resources URLByAppendingPathComponent:@"Runtime/GreenAtlasRuntime"];
    NSURL *web = [resources URLByAppendingPathComponent:@"Web"];
    NSURL *support = [NSFileManager.defaultManager URLsForDirectory:NSApplicationSupportDirectory inDomains:NSUserDomainMask].firstObject;
    NSURL *base = [support URLByAppendingPathComponent:@"Green Atlas"];
    NSString *testProfile = [NSBundle.mainBundle objectForInfoDictionaryKey:@"GADevelopmentProfile"];
    if (testProfile.length) { base = [NSURL fileURLWithPath:testProfile]; self.window.title = @"Green Atlas — проверка"; }
    NSURL *data = [base URLByAppendingPathComponent:@"Workspace"];
    NSURL *transfers = [base URLByAppendingPathComponent:@"Transfers"];
    NSError *error;
    if (![NSFileManager.defaultManager createDirectoryAtURL:data withIntermediateDirectories:YES attributes:@{NSFilePosixPermissions:@0700} error:&error]) { [self fail:@"Не удалось открыть локальное хранилище."]; return; }
    NSURL *log = [base URLByAppendingPathComponent:@"desktop-runtime.log"];
    [NSFileManager.defaultManager createFileAtPath:log.path contents:nil attributes:@{NSFilePosixPermissions:@0600}];
    self.log = [NSFileHandle fileHandleForWritingToURL:log error:&error];
    self.engine = [[NSTask alloc] init];
    self.engine.executableURL = executable;
    NSMutableArray *arguments = [NSMutableArray arrayWithArray:@[@"--data-dir", data.path, @"--web-dir", web.path, @"--ticket-root", transfers.path]];
    NSURL *packageContents = [NSBundle.mainBundle.bundleURL.URLByDeletingLastPathComponent URLByDeletingLastPathComponent];
    NSURL *cadWorker = [packageContents URLByAppendingPathComponent:@"Workers/GreenAtlasQuery.bundle"];
    if ([NSFileManager.defaultManager fileExistsAtPath:cadWorker.path]) {
        [arguments addObjectsFromArray:@[@"--cad-worker", cadWorker.path]];
    }
    self.engine.arguments = arguments;
    self.pipe = [NSPipe pipe]; self.engine.standardOutput = self.pipe; self.engine.standardError = self.log;
    __weak Desktop *weakSelf = self;
    NSTask *launched = self.engine;
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
        dispatch_async(dispatch_get_main_queue(), ^{
            if (!weakSelf.closing && weakSelf.engine == task) { weakSelf.ready = NO; [weakSelf fail:@"Локальный движок остановлен. Можно повторить запуск."]; }
        });
    };
    if (![self.engine launchAndReturnError:&error]) { [self fail:@"Не удалось запустить Green Atlas. Проверьте установку приложения."]; return; }
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 45 * NSEC_PER_SEC), dispatch_get_main_queue(), ^{
        if (weakSelf.engine == launched && !weakSelf.ready && launched.running) { [launched terminate]; [weakSelf fail:@"Запуск занял слишком много времени. Повторите."]; }
    });
}
- (void)acceptReady:(id)receipt {
    if (![receipt isKindOfClass:NSDictionary.class] || ![receipt[@"service"] isEqual:@"green-atlas-desktop"] || ![receipt[@"protocol"] isEqual:@1]) { [self.engine terminate]; return; }
    NSString *origin = receipt[@"origin"], *secret = receipt[@"session_token"];
    if (![origin isKindOfClass:NSString.class] || ![secret isKindOfClass:NSString.class]) { [self.engine terminate]; return; }
    NSURLComponents *url = [NSURLComponents componentsWithString:origin];
    NSCharacterSet *allowed = [NSCharacterSet characterSetWithCharactersInString:@"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"];
    if (![url.scheme isEqual:@"http"] || ![url.host isEqual:@"127.0.0.1"] || url.port.integerValue < 1024 || url.port.integerValue > 65535 || url.path.length || url.query || url.fragment || url.user || url.password || secret.length < 43 || secret.length > 128 || [secret rangeOfCharacterFromSet:allowed.invertedSet].location != NSNotFound) { [self.engine terminate]; return; }
    self.origin = origin; self.secret = secret;
    NSHTTPCookie *cookie = [NSHTTPCookie cookieWithProperties:@{NSHTTPCookieName:@"green_atlas_local_session", NSHTTPCookieValue:secret, NSHTTPCookieDomain:@"127.0.0.1", NSHTTPCookiePath:@"/", @"HttpOnly":@"TRUE", @"SameSite":@"Strict"}];
    [self.web.configuration.websiteDataStore.httpCookieStore setCookie:cookie completionHandler:^{
        dispatch_async(dispatch_get_main_queue(), ^{
            self.ready = YES;
            if (self.activeTicket) { self.busy = NO; [self.tickets insertObject:self.activeTicket atIndex:0]; self.activeTicket = nil; }
            if (self.tickets.count) [self nextTicket];
            else [self openProject:@"/projects"];
        });
    }];
}
- (void)request:(NSString *)method path:(NSString *)path body:(NSDictionary *)body done:(void (^)(NSDictionary *, NSString *))done {
    if (!self.ready || !self.secret || !self.origin) { done(nil, @"Локальный движок недоступен."); return; }
    NSUInteger generation = self.generation;
    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:[NSURL URLWithString:[self.origin stringByAppendingString:path]]];
    request.HTTPMethod = method; [request setValue:self.secret forHTTPHeaderField:@"X-Green-Atlas-Local-Session"];
    if (body) { request.HTTPBody = [NSJSONSerialization dataWithJSONObject:body options:0 error:nil]; [request setValue:@"application/json" forHTTPHeaderField:@"Content-Type"]; }
    [[self.network dataTaskWithRequest:request completionHandler:^(NSData *data, NSURLResponse *response, NSError *error) {
        id result = data.length > 0 && data.length < 32768 ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
        NSInteger status = [(NSHTTPURLResponse *)response statusCode];
        dispatch_async(dispatch_get_main_queue(), ^{
            if (self.closing || self.generation != generation) return;
            done([result isKindOfClass:NSDictionary.class] ? result : nil, error || status < 200 || status >= 300 ? @"Не удалось принять снимок. Повторите подготовку." : nil);
        });
    }] resume];
}
- (void)nextTicket {
    if (!self.ready || self.busy || !self.tickets.count) return;
    [self progressVisible:YES];
    self.busy = YES; self.retry.hidden = YES; self.cancel.hidden = YES;
    self.activeTicket = self.tickets.firstObject; [self.tickets removeObjectAtIndex:0];
    self.generation++;
    self.status.stringValue = @"Открываем снимок AutoCAD…";
    [self request:@"POST" path:@"/_desktop/handoffs" body:@{@"ticket":self.activeTicket} done:^(NSDictionary *receipt, NSString *error) {
        if (error) { [self fail:error]; return; }
        NSString *identifier = receipt[@"id"];
        if (![identifier isKindOfClass:NSString.class] || identifier.length != 64 || [identifier rangeOfCharacterFromSet:[NSCharacterSet characterSetWithCharactersInString:@"abcdef0123456789"].invertedSet].location != NSNotFound) { [self fail:@"Движок вернул неверное состояние передачи."]; return; }
        self.identifier = identifier;
        [self handleReceipt:receipt];
    }];
}
- (void)handleReceipt:(NSDictionary *)receipt {
    if ([receipt[@"status"] isEqual:@"needs_review"]) {
        NSString *path = GAHandoffProjectPath(receipt);
        if (!path) { [self fail:@"Проект сохранён, но приложение не смогло открыть его страницу. Адрес проекта несовместим с этой версией приложения."]; return; }
        self.busy = NO; self.activeTicket = nil; self.identifier = nil;
        [self openProject:path]; [self nextTicket]; return;
    }
    if ([@[@"failed", @"interrupted", @"cancelled"] containsObject:receipt[@"status"]]) { [self fail:@"Подготовка остановлена. Можно повторить."]; return; }
    self.status.stringValue = [receipt[@"stage"] isKindOfClass:NSString.class] ? receipt[@"stage"] : @"Подготавливаем проект…";
    self.cancel.hidden = NO;
    NSString *identifier = self.identifier;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC), dispatch_get_main_queue(), ^{
        if (![self.identifier isEqual:identifier] || !self.busy) return;
        [self request:@"GET" path:[@"/_desktop/handoffs/" stringByAppendingString:identifier] body:nil done:^(NSDictionary *value, NSString *error) { if (error) [self fail:error]; else [self handleReceipt:value]; }];
    });
}
- (void)openProject:(NSString *)path {
    self.status.stringValue = @""; self.retry.hidden = YES; self.cancel.hidden = YES;
    [self progressVisible:NO];
    [self.web loadRequest:[NSURLRequest requestWithURL:[NSURL URLWithString:[self.origin stringByAppendingString:path]]]];
    [self show];
}
- (void)retryClicked:(id)sender {
    self.retry.hidden = YES;
    if (!self.engine.running) { [self launchEngine]; return; }
    if (self.activeTicket) { [self.tickets insertObject:self.activeTicket atIndex:0]; self.activeTicket = nil; }
    self.busy = NO; self.identifier = nil; [self nextTicket];
}
- (void)cancelClicked:(id)sender {
    if (!self.identifier) return;
    self.generation++; // Invalidate pending polls before requesting cancellation.
    self.cancel.hidden = YES;
    [self request:@"POST" path:[NSString stringWithFormat:@"/_desktop/handoffs/%@/cancel", self.identifier] body:nil done:^(NSDictionary *receipt, NSString *error) {
        if (error) { [self fail:@"Отмена не подтверждена. Повторите."]; return; }
        if ([receipt[@"status"] isEqual:@"needs_review"]) { [self handleReceipt:receipt]; return; }
        self.busy = NO; self.activeTicket = nil; self.identifier = nil;
        self.cancel.hidden = YES; self.status.stringValue = @"Подготовка отменена";
        [self nextTicket];
    }];
}
@end

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        [NSApplication sharedApplication];
        [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
        Desktop *delegate = [[Desktop alloc] init];
        delegate.tickets = [NSMutableArray array];
        if (argc == 2) [delegate.tickets addObject:@(argv[1])];
        NSApp.delegate = delegate;
        [NSApp run];
    }
    return 0;
}
