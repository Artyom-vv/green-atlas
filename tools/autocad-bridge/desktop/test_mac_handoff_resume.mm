#import <AppKit/AppKit.h>
#import <WebKit/WebKit.h>
#include <cassert>

static void (^chooseReceipt)(NSDictionary *);
static void (^confirmResume)(NSModalResponse);

@interface TestAlert : NSObject
@property(copy) NSString *messageText;
@property(copy) NSString *informativeText;
- (void)addButtonWithTitle:(NSString *)title;
- (void)beginSheetModalForWindow:(NSWindow *)window completionHandler:(void (^)(NSModalResponse))completion;
@end
@implementation TestAlert
- (void)addButtonWithTitle:(NSString *)title {}
- (void)beginSheetModalForWindow:(NSWindow *)window completionHandler:(void (^)(NSModalResponse))completion {
    confirmResume = completion;
}
@end

// Exercise the production controller and request-generation guard. Only modal
// choices and transport are held by doubles; no app, server or CAD is launched.
#define NSAlert TestAlert
#define GAChooseHandoff TestChooseHandoff
#define main GADesktopProgramMain
#include "mac-main.mm"
#undef main
#undef GAChooseHandoff
#undef NSAlert

void TestChooseHandoff(NSWindow *window, NSArray *receipts, void (^selected)(NSDictionary *)) {
    chooseReceipt = selected;
}

@interface TestField : NSObject
@property(copy) NSString *stringValue;
@end
@implementation TestField @end
@interface TestButton : NSObject
@property(getter=isHidden) BOOL hidden;
@end
@implementation TestButton @end
@interface TestPanel : NSObject
@property(getter=isHidden) BOOL hidden;
@property(getter=isLoading) BOOL loading;
@property NSRect frame;
@property(copy) NSString *error;
@property(strong) TestField *status;
@property(strong) TestButton *retry;
@property(strong) TestButton *cancel;
- (void)presentProgress:(NSString *)stage autocadRequired:(BOOL)required;
- (CGFloat)preferredHeight;
- (void)clearError;
- (void)presentError:(NSString *)message runtimeAvailable:(BOOL)available;
- (void)presentFailure:(GAHandoffFailure *)failure runtimeAvailable:(BOOL)available;
@end
@implementation TestPanel
- (CGFloat)preferredHeight { return 50; }
- (void)clearError { self.error = nil; self.loading = NO; }
- (void)presentProgress:(NSString *)stage autocadRequired:(BOOL)required {
    self.status.stringValue = stage; self.loading = YES; self.error = nil; self.retry.hidden = YES;
}
- (void)presentError:(NSString *)message runtimeAvailable:(BOOL)available {
    self.loading = NO; self.error = message; self.retry.hidden = NO; self.cancel.hidden = YES;
}
- (void)presentFailure:(GAHandoffFailure *)failure runtimeAvailable:(BOOL)available {
    [self presentError:failure.message runtimeAvailable:available];
}
@end
@interface TestEngine : NSObject
@property(getter=isRunning) BOOL running;
@end
@implementation TestEngine @end

@interface TestTask : NSObject
@property(strong) NSURLRequest *request;
@property BOOL cancelled;
@property(copy) void (^completed)(NSData *, NSURLResponse *, NSError *);
- (void)resume;
- (void)cancel;
- (void)reply:(NSDictionary *)value failed:(BOOL)failed;
- (void)reply:(NSDictionary *)value status:(NSInteger)status failed:(BOOL)failed;
@end
@implementation TestTask
- (void)resume {}
- (void)cancel { self.cancelled = YES; }
- (void)reply:(NSDictionary *)value failed:(BOOL)failed {
    [self reply:value status:200 failed:failed];
}
- (void)reply:(NSDictionary *)value status:(NSInteger)status failed:(BOOL)failed {
    NSData *data = value ? [NSJSONSerialization dataWithJSONObject:value options:0 error:nil] : nil;
    NSHTTPURLResponse *response = [[NSHTTPURLResponse alloc] initWithURL:self.request.URL
        statusCode:status HTTPVersion:@"HTTP/1.1" headerFields:@{}];
    self.completed(data, response, failed ? [NSError errorWithDomain:NSURLErrorDomain code:NSURLErrorNetworkConnectionLost userInfo:nil] : nil);
}
@end
@interface TestNetwork : NSObject
@property(strong) NSMutableArray<TestTask *> *tasks;
@property(copy) void (^sending)(NSURLRequest *);
- (void)invalidateAndCancel;
- (TestTask *)dataTaskWithRequest:(NSURLRequest *)request completionHandler:(void (^)(NSData *, NSURLResponse *, NSError *))completed;
@end
@implementation TestNetwork
- (void)invalidateAndCancel { for (TestTask *task in self.tasks) [task cancel]; }
- (TestTask *)dataTaskWithRequest:(NSURLRequest *)request completionHandler:(void (^)(NSData *, NSURLResponse *, NSError *))completed {
    if (self.sending) self.sending(request);
    TestTask *task = [[TestTask alloc] init]; task.request = request; task.completed = completed;
    [self.tasks addObject:task]; return task;
}
@end
@interface TestLeaveGuard : NSObject
@property(copy) void (^decision)(BOOL);
@property NSUInteger requests;
@property NSUInteger documents;
- (BOOL)request:(ga::desktop::Action)action target:(NSString *)target completion:(void (^)(BOOL))completion;
- (void)newDocument:(NSString *)origin;
- (void)runtimeAvailable:(BOOL)available;
@end
@implementation TestLeaveGuard
- (BOOL)request:(ga::desktop::Action)action target:(NSString *)target completion:(void (^)(BOOL))completion {
    self.requests++; self.decision = completion; return YES;
}
- (void)newDocument:(NSString *)origin { self.documents++; }
- (void)runtimeAvailable:(BOOL)available {}
@end
@interface TestApplication : NSObject
@property NSUInteger replies;
@property BOOL allowed;
- (void)replyToApplicationShouldTerminate:(BOOL)allowed;
@end
@implementation TestApplication
- (void)replyToApplicationShouldTerminate:(BOOL)allowed { self.replies++; self.allowed = allowed; }
@end
@interface TestQualification : GADesktopQualification
@property BOOL completed;
@end
@implementation TestQualification
- (BOOL)finished { return self.completed; }
@end
@interface TestDesktop : Desktop
- (void)show;
@end
@implementation TestDesktop
- (void)show {}
@end

static void flushMainQueue() {
    __block BOOL flushed = NO;
    dispatch_async(dispatch_get_main_queue(), ^{ flushed = YES; });
    NSDate *limit = [NSDate dateWithTimeIntervalSinceNow:1];
    while (!flushed && limit.timeIntervalSinceNow > 0) {
        [NSRunLoop.currentRunLoop runMode:NSDefaultRunLoopMode beforeDate:limit];
    }
    assert(flushed);
}
static void waitForPollDeadline() {
    NSDate *limit = [NSDate dateWithTimeIntervalSinceNow:1.1];
    while (limit.timeIntervalSinceNow > 0) {
        [NSRunLoop.currentRunLoop runMode:NSDefaultRunLoopMode beforeDate:limit];
    }
    flushMainQueue();
}
static NSString *identifier() { return [@"a" stringByPaddingToLength:64 withString:@"a" startingAtIndex:0]; }
static TestDesktop *desktop() {
    TestDesktop *value = [[TestDesktop alloc] init];
    TestPanel *panel = [[TestPanel alloc] init]; panel.hidden = YES;
    panel.status = [[TestField alloc] init]; panel.retry = [[TestButton alloc] init]; panel.cancel = [[TestButton alloc] init];
    panel.retry.hidden = YES; panel.cancel.hidden = YES;
    value.statusPanel = (GADesktopStatusPanel *)panel;
    value.status = (NSTextField *)panel.status; value.retry = (NSButton *)panel.retry; value.cancel = (NSButton *)panel.cancel;
    TestEngine *engine = [[TestEngine alloc] init]; engine.running = YES; value.engine = (NSTask *)engine;
    TestNetwork *network = [[TestNetwork alloc] init]; network.tasks = [NSMutableArray array]; value.network = (NSURLSession *)network;
    value.leaveGuard = (GADesktopLeaveGuard *)[[TestLeaveGuard alloc] init];
    value.handoffObservation = [[GAHandoffObservation alloc] init]; value.tickets = [NSMutableArray array];
    value.ready = YES; value.origin = @"http://127.0.0.1:8123"; value.secret = @"test-session";
    return value;
}
static void choose(TestDesktop *value) {
    chooseReceipt = nil; confirmResume = nil;
    [value openRecoverable:nil];
    TestNetwork *network = (TestNetwork *)value.network;
    [network.tasks.lastObject reply:@{@"handoffs":@[@{@"id":identifier(), @"status":@"interrupted"}]} failed:NO];
    flushMainQueue(); assert(chooseReceipt);
    chooseReceipt(@{@"id":identifier(), @"status":@"interrupted"}); assert(confirmResume);
}
static void confirm(TestDesktop *value) {
    choose(value); confirmResume(NSAlertFirstButtonReturn);
}

// Реальный дочерний процесс удерживает отдельную flock-блокировку. Проверяем
// завершение до повторного запуска, не открывая приложение или рабочую базу.
static NSTask *leaseProcess(NSURL *lock, BOOL ignoresTermination) {
    NSTask *task = [[NSTask alloc] init];
    task.executableURL = [NSURL fileURLWithPath:@"/usr/bin/python3"];
    NSString *program = @"import fcntl,os,signal,sys,time\n"
         "fd=os.open(sys.argv[1],os.O_CREAT|os.O_RDWR,0o600)\n"
         "fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)\n"
         "stopping=False\n"
         "def stop(signum,frame):\n"
         " global stopping\n"
         " stopping=True\n"
         "signal.signal(signal.SIGTERM,signal.SIG_IGN if sys.argv[2]=='ignore' else stop)\n"
         "print('ready',flush=True)\n"
         "while not stopping: time.sleep(.01)\n"
         "time.sleep(.15)\n"
         "os.close(fd)\n";
    task.arguments = @[@"-c", program,
        lock.path, ignoresTermination ? @"ignore" : @"stop"];
    NSPipe *output = [NSPipe pipe]; task.standardOutput = output;
    task.standardError = NSFileHandle.fileHandleWithNullDevice;
    assert([task launchAndReturnError:nil]);
    NSData *ready = [output.fileHandleForReading readDataOfLength:6];
    assert([ready isEqual:[@"ready\n" dataUsingEncoding:NSUTF8StringEncoding]]);
    return task;
}
static void waitForStop(BOOL (^finished)(void)) {
    NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:2];
    while (!finished() && deadline.timeIntervalSinceNow > 0)
        [NSRunLoop.currentRunLoop runMode:NSDefaultRunLoopMode beforeDate:deadline];
    assert(finished());
}
static void checkRuntimeExitDiagnostics() {
    NSURL *root = [NSURL fileURLWithPath:[NSTemporaryDirectory() stringByAppendingPathComponent:NSUUID.UUID.UUIDString]
        isDirectory:YES];
    assert([NSFileManager.defaultManager createDirectoryAtURL:root withIntermediateDirectories:YES attributes:nil error:nil]);
    NSFileHandle *first = GAOpenRuntimeLog(root, nil);
    NSFileHandle *exitLog = GARuntimeExitLog(first); assert(exitLog);
    __block BOOL recorded = NO;
    NSTask *clean = [[NSTask alloc] init];
    clean.executableURL = [NSURL fileURLWithPath:@"/bin/sh"];
    clean.arguments = @[@"-c", @"exit 7", @"private-session-fixture"];
    clean.terminationHandler = ^(NSTask *task) {
        GARecordRuntimeExit(exitLog, task, YES, NO);
        dispatch_async(dispatch_get_main_queue(), ^{ recorded = YES; });
    };
    assert([clean launchAndReturnError:nil]);
    waitForStop(^BOOL { return recorded; });

    // Ответ прежнего процесса после ротации должен остаться в прежнем журнале.
    NSTask *signal = [[NSTask alloc] init];
    signal.executableURL = [NSURL fileURLWithPath:@"/bin/sleep"]; signal.arguments = @[@"30"];
    signal.terminationHandler = ^(NSTask *task) {
        GARecordRuntimeExit(exitLog, task, NO, YES);
        dispatch_async(dispatch_get_main_queue(), ^{ recorded = YES; });
    };
    assert([signal launchAndReturnError:nil]);
    [first closeFile];
    NSFileHandle *next = GAOpenRuntimeLog(root, nil); assert(next);
    recorded = NO; [signal terminate];
    waitForStop(^BOOL { return recorded; });
    [exitLog closeFile]; [next closeFile];
    NSData *current = [NSData dataWithContentsOfURL:[root URLByAppendingPathComponent:@"desktop-runtime.log"]];
    assert(current.length == 0);
    NSURL *archive = [root URLByAppendingPathComponent:@"RuntimeLogs"];
    NSArray<NSURL *> *logs = [NSFileManager.defaultManager contentsOfDirectoryAtURL:archive
        includingPropertiesForKeys:nil options:0 error:nil];
    assert(logs.count == 1);
    NSString *text = [[NSString alloc] initWithData:[NSData dataWithContentsOfURL:logs.firstObject] encoding:NSUTF8StringEncoding];
    NSArray<NSString *> *lines = [text componentsSeparatedByString:@"\n"]; assert(lines.count == 3);
    NSMutableArray<NSDictionary *> *events = [NSMutableArray new];
    NSSet *fields = [NSSet setWithArray:@[@"event", @"desktop_pid", @"runtime_pid", @"termination_reason",
        @"termination_status", @"expected_closing", @"startup_timeout"]];
    for (NSUInteger index = 0; index < 2; index++) {
        NSDictionary *event = [NSJSONSerialization JSONObjectWithData:[lines[index] dataUsingEncoding:NSUTF8StringEncoding]
            options:0 error:nil];
        assert([[NSSet setWithArray:event.allKeys] isEqual:fields]);
        assert([event[@"event"] isEqual:@"runtime_exit"]);
        assert([event[@"desktop_pid"] intValue] == NSProcessInfo.processInfo.processIdentifier);
        [events addObject:event];
    }
    assert([events[0][@"runtime_pid"] intValue] == clean.processIdentifier);
    assert([events[0][@"termination_reason"] isEqual:@"exit"] && [events[0][@"termination_status"] intValue] == 7);
    assert([events[0][@"expected_closing"] isEqual:@YES] && [events[0][@"startup_timeout"] isEqual:@NO]);
    assert([events[1][@"runtime_pid"] intValue] == signal.processIdentifier);
    assert([events[1][@"termination_reason"] isEqual:@"signal"] && [events[1][@"termination_status"] intValue] == SIGTERM);
    assert([events[1][@"expected_closing"] isEqual:@NO] && [events[1][@"startup_timeout"] isEqual:@YES]);
    assert(![text containsString:@"private-session-fixture"]);
    assert([NSFileManager.defaultManager removeItemAtURL:root error:nil]);
}
static void checkQualifiedDesktopExit() {
    NSURL *root = [NSURL fileURLWithPath:[NSTemporaryDirectory() stringByAppendingPathComponent:NSUUID.UUID.UUIDString]
        isDirectory:YES];
    assert([NSFileManager.defaultManager createDirectoryAtURL:root withIntermediateDirectories:YES attributes:nil error:nil]);
    NSTask *runtime = leaseProcess([root URLByAppendingPathComponent:@"qualified.lock"], NO);
    __block BOOL stopped = NO;
    GAStopRuntime(runtime, 1, ^(BOOL value) { stopped = value; });
    waitForStop(^BOOL { return stopped; }); assert(!runtime.running);

    TestDesktop *controller = desktop(); controller.engine = runtime;
    TestQualification *qualification = [[TestQualification alloc] init]; qualification.completed = YES;
    controller.qualification = qualification; controller.exitApproved = YES;
    TestApplication *app = [[TestApplication alloc] init];
    __block NSApplicationTerminateReply reply = NSTerminateCancel;
    __block BOOL returned = NO;
    dispatch_async(dispatch_get_main_queue(), ^{
        reply = [controller applicationShouldTerminate:(NSApplication *)app];
        returned = YES;
    });
    waitForStop(^BOOL { return returned; });
    assert(reply == NSTerminateNow && app.replies == 0 && !controller.runtimeStopping);
    flushMainQueue(); assert(app.replies == 0);
    assert(((TestLeaveGuard *)controller.leaveGuard).requests == 0);

    // Незавершённая приёмка не обходит существующее ожидание закрытия.
    controller = desktop(); controller.engine = runtime; controller.qualification = qualification;
    qualification.completed = NO; controller.exitApproved = YES;
    app = [[TestApplication alloc] init];
    assert([controller applicationShouldTerminate:(NSApplication *)app] == NSTerminateLater);
    waitForStop(^BOOL { return app.replies == 1; }); assert(app.allowed);

    // Даже готовая квитанция не разрешает выход с ещё работающим собственным API.
    controller = desktop(); controller.engine = leaseProcess([root URLByAppendingPathComponent:@"still-running.lock"], NO);
    controller.qualification = qualification; qualification.completed = YES; controller.exitApproved = YES;
    app = [[TestApplication alloc] init];
    assert([controller applicationShouldTerminate:(NSApplication *)app] == NSTerminateLater);
    assert(controller.runtimeStopping && controller.engine.running && app.replies == 0);
    waitForStop(^BOOL { return app.replies == 1; }); assert(app.allowed && !controller.engine.running);

    // Разрешение пользователя/сценария выхода остаётся обязательным.
    controller = desktop(); controller.engine = runtime; controller.qualification = qualification;
    app = [[TestApplication alloc] init];
    assert([controller applicationShouldTerminate:(NSApplication *)app] == NSTerminateLater);
    assert(((TestLeaveGuard *)controller.leaveGuard).requests == 1 && app.replies == 0);
    assert([NSFileManager.defaultManager removeItemAtURL:root error:nil]);
}
static void checkRuntimeShutdownAndLogs() {
    NSURL *root = [NSURL fileURLWithPath:[NSTemporaryDirectory() stringByAppendingPathComponent:NSUUID.UUID.UUIDString]
        isDirectory:YES];
    assert([NSFileManager.defaultManager createDirectoryAtURL:root withIntermediateDirectories:YES attributes:nil error:nil]);
    NSURL *lease = [root URLByAppendingPathComponent:@"isolated-runtime.lock"];
    NSTask *owner = leaseProcess(lease, NO);
    __block BOOL finished = NO, stopped = NO;
    GAStopRuntime(owner, 1, ^(BOOL value) { stopped = value; finished = YES; });
    assert(!finished); // Quit нельзя подтверждать сразу после SIGTERM.
    waitForStop(^BOOL { return finished; }); assert(stopped && !owner.running);
    NSTask *next = leaseProcess(lease, NO); // Новый запуск получает тот же профиль без lease conflict.
    finished = NO; GAStopRuntime(next, 1, ^(BOOL value) { stopped = value; finished = YES; });
    waitForStop(^BOOL { return finished; }); assert(stopped);

    TestDesktop *controller = desktop();
    controller.engine = leaseProcess(lease, NO); controller.exitApproved = YES;
    TestApplication *app = [[TestApplication alloc] init];
    assert([controller applicationShouldTerminate:(NSApplication *)app] == NSTerminateLater);
    assert(controller.closing && controller.runtimeStopping && app.replies == 0);
    waitForStop(^BOOL { return app.replies == 1; });
    assert(app.allowed && !controller.engine.running && !controller.runtimeStopping);

    NSTask *busy = leaseProcess([root URLByAppendingPathComponent:@"busy.lock"], YES);
    NSTask *foreign = leaseProcess([root URLByAppendingPathComponent:@"other-profile.lock"], NO);
    finished = NO; GAStopRuntime(busy, .1, ^(BOOL value) { stopped = value; finished = YES; });
    waitForStop(^BOOL { return finished; }); assert(!stopped && busy.running && foreign.running);
    [busy interrupt]; [busy waitUntilExit]; // Только тест завершает свой специально удержанный процесс.
    finished = NO; GAStopRuntime(foreign, 1, ^(BOOL value) { stopped = value; finished = YES; });
    waitForStop(^BOOL { return finished; }); assert(stopped);

    NSData *previous = [@"Предыдущая ошибка удаления\n" dataUsingEncoding:NSUTF8StringEncoding];
    NSFileHandle *first = GAOpenRuntimeLog(root, nil); assert(first);
    [first writeData:previous]; [first closeFile];
    NSFileHandle *second = GAOpenRuntimeLog(root, nil); assert(second); [second closeFile];
    NSURL *archive = [root URLByAppendingPathComponent:@"RuntimeLogs"];
    NSArray<NSURL *> *logs = [NSFileManager.defaultManager contentsOfDirectoryAtURL:archive
        includingPropertiesForKeys:nil options:0 error:nil];
    assert(logs.count == 1 && [[NSData dataWithContentsOfURL:logs.firstObject] isEqual:previous]);
    NSURL *unowned = [archive URLByAppendingPathComponent:@"notes.log"];
    assert([previous writeToURL:unowned atomically:YES]);
    for (NSUInteger index = 0; index < 7; index++) {
        NSFileHandle *log = GAOpenRuntimeLog(root, nil); assert(log); [log closeFile];
    }
    logs = [NSFileManager.defaultManager contentsOfDirectoryAtURL:archive includingPropertiesForKeys:nil options:0 error:nil];
    assert(logs.count == 6 && [[NSData dataWithContentsOfURL:unowned] isEqual:previous]);
    assert([NSFileManager.defaultManager removeItemAtURL:root error:nil]);
}

int main() {
    @autoreleasepool {
        checkRuntimeExitDiagnostics();
        checkQualifiedDesktopExit();
        checkRuntimeShutdownAndLogs();
        TestDesktop *value = desktop(); TestNetwork *network = (TestNetwork *)value.network;
        choose(value); confirmResume(NSAlertSecondButtonReturn);
        assert(!value.busy && network.tasks.count == 1 && value.statusPanel.hidden);

        value = desktop(); network = (TestNetwork *)value.network;
        network.sending = ^(NSURLRequest *request) {
            if ([request.HTTPMethod isEqual:@"POST"]) {
                assert(value.busy && !value.statusPanel.hidden); // Fails before the fix: held resume had no feedback.
                assert([value.identifier isEqual:identifier()]);
            }
        };
        confirm(value);
        assert(value.busy && !value.statusPanel.hidden && value.retry.hidden);
        assert([value.status.stringValue isEqual:@"Продолжаем передачу AutoCAD"]);
        NSUInteger sent = network.tasks.count;
        [value retryClicked:nil]; assert(network.tasks.count == sent); // Hidden retry cannot submit twice.
        [value application:(NSApplication *)[[NSObject alloc] init] openFile:@"/tmp/queued-transfer/transfer.gatransfer"];
        assert(value.tickets.count == 1 && network.tasks.count == sent); // Pending resume owns the queue.
        [network.tasks.lastObject reply:@{@"id":identifier(), @"status":@"processing", @"revision":@1, @"stage":@"Читаем объекты"} failed:NO];
        flushMainQueue(); assert(value.busy && !value.statusPanel.hidden && !value.cancel.hidden);
        assert([value.status.stringValue isEqual:@"Читаем объекты"]);
        value.generation++; value.busy = NO; value.identifier = nil;

        value = desktop(); network = (TestNetwork *)value.network;
        confirm(value); [network.tasks.lastObject reply:nil failed:YES]; flushMainQueue();
        assert(!value.busy && !value.statusPanel.hidden && !value.retry.hidden);
        assert([value.identifier isEqual:identifier()] && ((TestPanel *)value.statusPanel).error.length);
        [value retryClicked:nil]; assert(value.busy && network.tasks.count == 3);
        assert([network.tasks.lastObject.request.HTTPMethod isEqual:@"GET"]);
        assert([network.tasks.lastObject.request.URL.path hasSuffix:identifier()]);
        value.generation++; value.busy = NO; value.identifier = nil;

        value = desktop(); network = (TestNetwork *)value.network;
        choose(value); value.generation++; confirmResume(NSAlertFirstButtonReturn);
        assert(network.tasks.count == 1 && !value.busy); // A stale confirmation cannot claim another owner.

        value = desktop(); network = (TestNetwork *)value.network; confirm(value);
        TestTask *pending = network.tasks.lastObject;
        [value openProjects:nil]; TestLeaveGuard *guard = (TestLeaveGuard *)value.leaveGuard;
        assert(guard.requests == 1 && value.busy); guard.decision(NO);
        assert(value.busy); // Shared leave policy owns permission to leave.
        [value openProjects:nil]; guard.decision(YES);
        assert(!value.busy && !value.identifier && value.statusPanel.hidden);
        [pending reply:@{@"id":identifier(), @"status":@"processing", @"revision":@1} failed:NO]; flushMainQueue();
        assert(!value.busy && !value.identifier && value.statusPanel.hidden);

        value = desktop(); network = (TestNetwork *)value.network; confirm(value); pending = network.tasks.lastObject;
        value.engine = (NSTask *)[[TestEngine alloc] init]; value.status.stringValue = @"Другой движок";
        [pending reply:@{@"id":identifier(), @"status":@"processing", @"revision":@1, @"stage":@"Старый ответ"} failed:NO]; flushMainQueue();
        assert([value.status.stringValue isEqual:@"Другой движок"]);

        value = desktop(); network = (TestNetwork *)value.network; confirm(value); pending = network.tasks.lastObject;
        [value cancelClicked:nil]; assert(network.tasks.count == 3);
        [pending reply:@{@"id":identifier(), @"status":@"processing", @"revision":@1} failed:NO]; flushMainQueue();
        [network.tasks.lastObject reply:@{@"id":identifier(), @"status":@"cancelled"} failed:NO]; flushMainQueue();
        assert(!value.busy && !value.identifier && [value.status.stringValue isEqual:@"Подготовка отменена"]);

        value = desktop(); network = (TestNetwork *)value.network; confirm(value);
        [value cancelClicked:nil]; TestTask *cancellation = network.tasks.lastObject;
        [cancellation reply:nil failed:YES]; flushMainQueue();
        assert(!value.retry.hidden && [value.identifier isEqual:identifier()]);
        [value retryClicked:nil]; assert(value.busy && network.tasks.count == 4);
        assert([network.tasks.lastObject.request.HTTPMethod isEqual:@"GET"]);
        [cancellation reply:@{@"id":identifier(), @"status":@"cancelled"} failed:NO]; flushMainQueue();
        assert(value.busy && [value.identifier isEqual:identifier()]); // Old cancellation cannot dismiss an explicit retry.
        value.generation++; value.busy = NO; value.identifier = nil;

        value = desktop(); network = (TestNetwork *)value.network; confirm(value);
        [network.tasks.lastObject reply:@{@"id":identifier(), @"status":@"failed", @"message":@"Снимок повреждён"} failed:NO];
        flushMainQueue(); assert(!value.busy && value.handoffFailure && !value.statusPanel.hidden);
        assert(!value.retry.hidden && [value.identifier isEqual:identifier()]);

        value = desktop(); network = (TestNetwork *)value.network; confirm(value);
        [network.tasks.lastObject reply:@{@"id":identifier(), @"status":@"needs_review", @"entry":@"Drawing.autocad.json",
            @"project_path":@"/projects/122a1ecd-4e1f-4a4e-bf88-13b93299ae1f/setup"} failed:NO];
        flushMainQueue(); guard = (TestLeaveGuard *)value.leaveGuard;
        assert(value.busy && !value.identifier && guard.requests == 1 && guard.documents == 0);
        guard.decision(NO); assert(guard.documents == 0 && !value.statusPanel.hidden);
        assert([value.status.stringValue isEqual:@"Новый проект сохранён, откройте его в списке проектов"]);

        value = desktop(); network = (TestNetwork *)value.network;
        [value application:(NSApplication *)[[NSObject alloc] init] openFile:@"/tmp/fresh-transfer/transfer.gatransfer"];
        NSString *expected = GAHandoffIdentifier(@"/tmp/fresh-transfer/transfer.gatransfer");
        assert([value.identifier isEqual:expected] && value.busy && value.statusPanel.loading);
        [network.tasks.lastObject reply:nil failed:YES]; flushMainQueue();
        assert(!value.busy && !value.statusPanel.loading && !value.retry.hidden);
        NSUInteger requests = network.tasks.count;
        [value retryClicked:nil];
        assert(network.tasks.count == requests + 1);
        assert([network.tasks.lastObject.request.HTTPMethod isEqual:@"GET"]);
        assert([network.tasks.lastObject.request.URL.path hasSuffix:expected]);
        [network.tasks.lastObject reply:@{@"id":expected, @"status":@"processing", @"revision":@1, @"stage":@"Сохраняем проект"} failed:NO];
        flushMainQueue(); assert(value.busy && value.statusPanel.loading);
        value.generation++; value.busy = NO;

        // Потерянный POST мог не дойти до сервера. Только подтверждённый GET404
        // разрешает следующее явное нажатие; ответ сам не запускает повтор записи.
        value = desktop(); network = (TestNetwork *)value.network;
        NSString *ticket = @"/tmp/missing-first-post/transfer.gatransfer";
        [value application:(NSApplication *)[[NSObject alloc] init] openFile:ticket];
        expected = GAHandoffIdentifier(ticket);
        [network.tasks.lastObject reply:nil failed:YES]; flushMainQueue();
        [value retryClicked:nil];
        assert([network.tasks.lastObject.request.HTTPMethod isEqual:@"GET"]);
        requests = network.tasks.count;
        [network.tasks.lastObject reply:@{@"code":@"NOT_FOUND", @"message":@"Передача или проект не найдены"} status:404 failed:NO];
        flushMainQueue();
        assert(network.tasks.count == requests && !value.busy && !value.identifier && !value.retry.hidden);
        assert([value.activeTicket isEqual:ticket]);
        [value retryClicked:nil];
        assert(network.tasks.count == requests + 1 && value.busy);
        assert([network.tasks.lastObject.request.HTTPMethod isEqual:@"POST"]);
        NSDictionary *repeatedBody = [NSJSONSerialization JSONObjectWithData:network.tasks.lastObject.request.HTTPBody options:0 error:nil];
        assert([repeatedBody[@"ticket"] isEqual:ticket] && [value.identifier isEqual:expected]);
        value.generation++; value.busy = NO;

        // Структурированный отказ POST не доказывает отсутствие регистрации.
        // HTTP400/500, повторное чтение500 и потерянный GET сохраняют адрес.
        for (NSNumber *postStatus in @[@400, @500]) {
            value = desktop(); network = (TestNetwork *)value.network;
            [value application:(NSApplication *)[[NSObject alloc] init] openFile:ticket];
            [network.tasks.lastObject reply:@{@"code":@"TICKET_NOT_ACCEPTED", @"message":@"Ошибка после регистрации"} status:postStatus.integerValue failed:NO];
            flushMainQueue(); assert([value.identifier isEqual:expected] && !value.busy);
            [value retryClicked:nil]; assert([network.tasks.lastObject.request.HTTPMethod isEqual:@"GET"]);
            [network.tasks.lastObject reply:@{@"code":@"NOT_FOUND", @"message":@"Нельзя трактовать 500 как 404", @"_ga_confirmed_not_found":@YES} status:500 failed:NO];
            flushMainQueue(); assert([value.identifier isEqual:expected] && !value.busy);
            [value retryClicked:nil]; assert([network.tasks.lastObject.request.HTTPMethod isEqual:@"GET"]);
            [network.tasks.lastObject reply:nil failed:YES];
            flushMainQueue(); assert([value.identifier isEqual:expected] && !value.busy);
            [value retryClicked:nil]; assert([network.tasks.lastObject.request.HTTPMethod isEqual:@"GET"]);
            [network.tasks.lastObject reply:@{@"code":@"NOT_FOUND", @"_ga_confirmed_not_found":@YES} status:404 failed:YES];
            flushMainQueue(); assert([value.identifier isEqual:expected] && !value.busy);
            [value retryClicked:nil]; assert([network.tasks.lastObject.request.HTTPMethod isEqual:@"GET"]);
            value.generation++; value.busy = NO;
        }

        // Даже тело подтверждённой передачи вместе с transport error ведёт к GET,
        // а не к повторному POST. Сервер уже мог сохранить проект.
        value = desktop(); network = (TestNetwork *)value.network;
        [value application:(NSApplication *)[[NSObject alloc] init] openFile:ticket];
        [network.tasks.lastObject reply:@{@"id":expected, @"status":@"processing", @"revision":@1} failed:YES];
        flushMainQueue(); assert([value.identifier isEqual:expected]);
        [value retryClicked:nil]; assert([network.tasks.lastObject.request.HTTPMethod isEqual:@"GET"]);
        value.generation++; value.busy = NO;

        // Native-меню не меняет владельца и индикатор выполняемой передачи.
        // Сначала проверяем список: прежний код отправлял GET, и assertion
        // останавливал тест до возможного создания настоящего NSOpenPanel.
        value = desktop(); network = (TestNetwork *)value.network; confirm(value);
        [network.tasks.lastObject reply:@{@"id":identifier(), @"status":@"processing", @"revision":@1, @"stage":@"Читаем объекты"} failed:NO];
        flushMainQueue(); requests = network.tasks.count;
        [value openRecoverable:nil];
        assert(network.tasks.count == requests && value.busy && [value.identifier isEqual:identifier()]);
        assert(value.statusPanel.loading && [value.status.stringValue isEqual:@"Читаем объекты"]);
        [value openSnapshot:nil];
        assert(network.tasks.count == requests && value.busy && [value.identifier isEqual:identifier()]);
        assert(value.statusPanel.loading && !((TestPanel *)value.statusPanel).error);
        assert([value.status.stringValue isEqual:@"Читаем объекты"]);
        value.generation++; value.busy = NO;

        // Отмена освобождает long polling; поздний ответ не запускает новый GET.
        value = desktop(); network = (TestNetwork *)value.network; confirm(value);
        [network.tasks.lastObject reply:@{@"id":identifier(), @"status":@"processing", @"revision":@1} failed:NO];
        flushMainQueue();
        TestTask *cancelledWait = network.tasks.lastObject;
        [value cancelClicked:nil]; assert(cancelledWait.cancelled && !value.handoffWait);
        cancellation = network.tasks.lastObject;
        requests = network.tasks.count;
        waitForPollDeadline(); assert(network.tasks.count == requests);
        [cancellation reply:@{@"id":identifier(), @"status":@"cancelled"} failed:NO];
        flushMainQueue(); assert(!value.identifier && !value.busy && !((TestPanel *)value.statusPanel).error);

        // Успешный HTTP-ответ отмены обязан принадлежать запросу и быть terminal.
        for (NSDictionary *invalidCancel in @[
            @{@"id":[@"b" stringByPaddingToLength:64 withString:@"b" startingAtIndex:0], @"status":@"cancelled"},
            @{@"id":identifier(), @"status":@"processing", @"revision":@1}
        ]) {
            value = desktop(); network = (TestNetwork *)value.network; confirm(value);
            [network.tasks.lastObject reply:@{@"id":identifier(), @"status":@"processing", @"revision":@1} failed:NO];
            flushMainQueue(); [value cancelClicked:nil];
            [network.tasks.lastObject reply:invalidCancel failed:NO]; flushMainQueue();
            assert([value.identifier isEqual:identifier()] && !value.busy && !value.retry.hidden);
            assert(((TestPanel *)value.statusPanel).error.length);
            value.generation++;
        }

        // Второй ticket ждёт решения общей leave policy о первом готовом проекте.
        // За время подтверждения новая передача не меняет текущего владельца.
        for (NSNumber *allowed in @[@YES, @NO]) {
            value = desktop(); network = (TestNetwork *)value.network;
            NSString *firstTicket = @"/tmp/queue-first/transfer.gatransfer";
            NSString *secondTicket = @"/tmp/queue-second/transfer.gatransfer";
            [value application:(NSApplication *)[[NSObject alloc] init] openFile:firstTicket];
            [value application:(NSApplication *)[[NSObject alloc] init] openFile:secondTicket];
            assert(network.tasks.count == 1 && value.tickets.count == 1);
            [network.tasks.firstObject reply:@{
                @"id":GAHandoffIdentifier(firstTicket), @"status":@"needs_review",
                @"entry":@"Drawing.autocad.json", @"project_path":@"/projects/122a1ecd-4e1f-4a4e-bf88-13b93299ae1f/setup"
            } failed:NO];
            flushMainQueue(); guard = (TestLeaveGuard *)value.leaveGuard;
            assert(guard.requests == 1 && guard.documents == 0 && network.tasks.count == 1);
            assert(value.busy && value.tickets.count == 1);
            guard.decision(allowed.boolValue);
            assert(network.tasks.count == 2 && value.busy && value.tickets.count == 0);
            assert([value.activeTicket isEqual:secondTicket]);
            assert([network.tasks.lastObject.request.HTTPMethod isEqual:@"POST"]);
            assert(guard.documents == (allowed.boolValue ? 1 : 0));
            value.generation++; value.busy = NO;
        }

        value = desktop(); network = (TestNetwork *)value.network; confirm(value);
        [network.tasks.lastObject reply:@{@"id":identifier(), @"status":@"not-a-state"} failed:NO];
        flushMainQueue(); assert(!value.busy && !value.statusPanel.loading && !value.retry.hidden);

        value = desktop(); network = (TestNetwork *)value.network; confirm(value); pending = network.tasks.lastObject;
        value.closing = YES; value.status.stringValue = @"Закрываем окно";
        [pending reply:@{@"id":identifier(), @"status":@"processing", @"revision":@1, @"stage":@"Старый ответ"} failed:NO]; flushMainQueue();
        assert([value.status.stringValue isEqual:@"Закрываем окно"]);

        // Весь активный этап наблюдается одним последовательным held GET.
        value = desktop(); network = (TestNetwork *)value.network; confirm(value);
        [network.tasks.lastObject reply:@{@"id":identifier(), @"status":@"processing", @"revision":@7,
            @"stage":@"Читаем объекты"} failed:NO];
        flushMainQueue(); assert(network.tasks.count == 3);
        TestTask *held = network.tasks.lastObject;
        assert([held.request.HTTPMethod isEqual:@"GET"] && [held.request.URL.query isEqual:@"after=7"]);
        assert(held.request.timeoutInterval == kHandoffWaitRequestTimeoutSeconds && value.handoffWait == (id)held);
        waitForPollDeadline(); assert(network.tasks.count == 3); // Нет секундного запроса поверх held GET.
        [held reply:@{@"id":identifier(), @"status":@"processing", @"revision":@8,
            @"stage":@"Сохраняем проект"} failed:NO];
        flushMainQueue(); assert(network.tasks.count == 4 && [value.status.stringValue isEqual:@"Сохраняем проект"]);
        held = network.tasks.lastObject;
        assert([held.request.URL.query isEqual:@"after=8"]);
        [value openProjects:nil]; guard = (TestLeaveGuard *)value.leaveGuard; guard.decision(NO);
        assert(!held.cancelled && value.busy); // Отказ leave policy сохраняет наблюдение.
        [value openProjects:nil]; guard.decision(YES);
        assert(held.cancelled && !value.handoffWait && !value.busy);
        requests = network.tasks.count;
        [held reply:@{@"id":identifier(), @"status":@"processing", @"revision":@9,
            @"stage":@"Поздний ответ"} failed:NO];
        flushMainQueue(); assert(network.tasks.count == requests && !value.busy && !value.identifier);

        // Завершение прекращает ожидание и проходит через общую leave policy.
        value = desktop(); network = (TestNetwork *)value.network; confirm(value);
        [network.tasks.lastObject reply:@{@"id":identifier(), @"status":@"processing", @"revision":@1} failed:NO];
        flushMainQueue(); held = network.tasks.lastObject; requests = network.tasks.count;
        [held reply:@{@"id":identifier(), @"status":@"needs_review", @"entry":@"Drawing.autocad.json",
            @"project_path":@"/projects/122a1ecd-4e1f-4a4e-bf88-13b93299ae1f/setup", @"revision":@2} failed:NO];
        flushMainQueue(); assert(!value.handoffWait && !value.identifier && network.tasks.count == requests);
        guard = (TestLeaveGuard *)value.leaveGuard; assert(guard.requests == 1); guard.decision(NO);

        // Обрыв held GET допускает только явное чтение результата, без POST.
        value = desktop(); network = (TestNetwork *)value.network; confirm(value);
        [network.tasks.lastObject reply:@{@"id":identifier(), @"status":@"processing", @"revision":@1} failed:NO];
        flushMainQueue(); held = network.tasks.lastObject; requests = network.tasks.count;
        [held reply:nil failed:YES]; flushMainQueue();
        assert(!value.busy && !value.handoffWait && !value.statusPanel.loading && !value.retry.hidden);
        assert(network.tasks.count == requests);
        [value retryClicked:nil]; assert(network.tasks.count == requests + 1);
        assert([network.tasks.lastObject.request.HTTPMethod isEqual:@"GET"] && !network.tasks.lastObject.request.URL.query);
        value.generation++; value.busy = NO;

        // При смене процесса, ticket или закрытии старый ответ не меняет экран.
        for (NSString *ownerChange in @[@"engine", @"ticket", @"secret", @"origin", @"quit"]) {
            value = desktop(); network = (TestNetwork *)value.network; confirm(value);
            [network.tasks.lastObject reply:@{@"id":identifier(), @"status":@"processing", @"revision":@1} failed:NO];
            flushMainQueue(); held = network.tasks.lastObject; requests = network.tasks.count;
            value.status.stringValue = @"Другой документ";
            if ([ownerChange isEqual:@"engine"]) value.engine = (NSTask *)[[TestEngine alloc] init];
            else if ([ownerChange isEqual:@"ticket"]) value.activeTicket = @"/tmp/other/transfer.gatransfer";
            else if ([ownerChange isEqual:@"secret"]) value.secret = @"new-session";
            else if ([ownerChange isEqual:@"origin"]) value.origin = @"http://127.0.0.1:8124";
            else {
                ((TestEngine *)value.engine).running = NO;
                [value applicationWillTerminate:[NSNotification notificationWithName:@"quit" object:nil]];
                assert(held.cancelled && !value.handoffWait);
            }
            [held reply:@{@"id":identifier(), @"status":@"processing", @"revision":@2,
                @"stage":@"Старый ответ"} failed:NO];
            flushMainQueue(); assert(network.tasks.count == requests && [value.status.stringValue isEqual:@"Другой документ"]);
            [value stopHandoffWait];
        }

        // Неверная ревизия не превращается в бесконечный цикл быстрых GET.
        for (id invalidRevision in @[NSNull.null, @YES, @"1", @(-1), @1.5, @(UINT64_MAX)]) {
            value = desktop(); network = (TestNetwork *)value.network; value.identifier = identifier();
            [value handleReceipt:@{@"id":identifier(), @"status":@"processing", @"revision":invalidRevision}];
            assert(!value.busy && !value.handoffWait && !value.statusPanel.loading && network.tasks.count == 0);
        }
    }
}
