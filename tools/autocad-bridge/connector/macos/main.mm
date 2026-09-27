#import <AppKit/AppKit.h>
#import "Transfer.h"
#import "BrowserActions.h"

@interface Connector : NSObject <NSApplicationDelegate, NSWindowDelegate>
@property(strong) NSWindow *window;
@property(strong) NSTextField *origin;
@property(strong) NSTextField *status;
@property(strong) NSTextField *code;
@property(strong) NSButton *start;
@property(strong) NSButton *cancel;
@property(strong) NSProgressIndicator *spinner;
@property(strong) GABrowserActions *browserActions;
@property(strong) GATransfer *transfer;
@property(strong) NSURL *ticket;
@property BOOL busy;
@end

@implementation Connector
- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    self.window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 520, 390)
        styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
        backing:NSBackingStoreBuffered defer:NO];
    self.window.title = @"Green Atlas — передача чертежа";
    self.window.delegate = self;
    NSView *view = self.window.contentView;
    NSTextField *heading = [NSTextField labelWithString:@"Открыть в Green Atlas"];
    heading.font = [NSFont systemFontOfSize:22 weight:NSFontWeightSemibold];
    heading.frame = NSMakeRect(28, 332, 464, 30); [view addSubview:heading];
    NSTextField *label = [NSTextField labelWithString:@"Адрес сервиса"];
    label.frame = NSMakeRect(28, 290, 464, 20); [view addSubview:label];
    self.origin = [[NSTextField alloc] initWithFrame:NSMakeRect(28, 257, 464, 28)];
    self.origin.placeholderString = @"https://адрес-вашего-сервиса";
    self.origin.stringValue = [NSUserDefaults.standardUserDefaults stringForKey:@"service-origin"] ?: @"";
    [view addSubview:self.origin];
    self.status = [NSTextField wrappingLabelWithString:@"Чертёж передаётся только после вашего подтверждения."];
    self.status.frame = NSMakeRect(28, 177, 464, 60); [view addSubview:self.status];
    self.code = [NSTextField labelWithString:@""];
    self.code.font = [NSFont monospacedSystemFontOfSize:22 weight:NSFontWeightMedium];
    self.code.selectable = YES;
    self.code.frame = NSMakeRect(28, 135, 464, 32); [view addSubview:self.code];
    self.browserActions = [[GABrowserActions alloc] initWithFrame:NSMakeRect(28, 86, 464, 34)];
    [view addSubview:self.browserActions];
    self.spinner = [[NSProgressIndicator alloc] initWithFrame:NSMakeRect(28, 35, 20, 20)];
    self.spinner.style = NSProgressIndicatorStyleSpinning;
    self.spinner.displayedWhenStopped = NO; [view addSubview:self.spinner];
    self.cancel = [NSButton buttonWithTitle:@"Отменить" target:self action:@selector(cancelClicked:)];
    self.cancel.frame = NSMakeRect(248, 26, 110, 34); self.cancel.enabled = NO; [view addSubview:self.cancel];
    self.start = [NSButton buttonWithTitle:@"Передать" target:self action:@selector(startClicked:)];
    self.start.frame = NSMakeRect(364, 26, 128, 34); self.start.keyEquivalent = @"\r"; [view addSubview:self.start];
    if (!self.ticket) {
        self.status.stringValue = @"Откройте чертёж в AutoCAD и выберите «Открыть в Green Atlas».";
        self.start.enabled = NO;
    }
    [self.window center]; [self.window makeKeyAndOrderFront:nil];
    [NSApp activateIgnoringOtherApps:YES];
}
- (BOOL)application:(NSApplication *)application openFile:(NSString *)filename {
    if (self.busy) return NO;
    [self.browserActions clear];
    self.ticket = [NSURL fileURLWithPath:filename]; self.start.enabled = YES;
    return YES;
}
- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)application { return YES; }
- (BOOL)windowShouldClose:(NSWindow *)window {
    if (!self.busy) return YES;
    NSAlert *alert = [[NSAlert alloc] init];
    alert.messageText = @"Отменить передачу?";
    alert.informativeText = @"Исходный чертёж в AutoCAD не изменится.";
    [alert addButtonWithTitle:@"Продолжить передачу"];
    [alert addButtonWithTitle:@"Отменить"];
    if ([alert runModal] == NSAlertSecondButtonReturn) [self cancelClicked:nil];
    return NO;
}
- (void)cancelClicked:(id)sender {
    [self.browserActions clear];
    [self.transfer cancel]; self.status.stringValue = @"Отменяем передачу…";
    self.cancel.enabled = NO;
}
- (void)startClicked:(id)sender {
    if (self.busy || !self.ticket) return;
    NSString *origin = [self.origin.stringValue stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
    @try {
        if (![GATransfer validOrigin:origin]) @throw [NSException exceptionWithName:@"Origin" reason:@"Укажите адрес HTTPS без пути, например https://green.example" userInfo:nil];
        self.transfer = [[GATransfer alloc] initWithTicket:self.ticket];
    } @catch (NSException *error) { self.status.stringValue = error.reason; return; }
    self.busy = YES; self.start.enabled = NO; self.cancel.enabled = YES; self.origin.enabled = NO;
    [self.browserActions clear];
    self.code.stringValue = @""; [self.spinner startAnimation:nil];
    [NSUserDefaults.standardUserDefaults setObject:origin forKey:@"service-origin"];
    __weak Connector *weakSelf = self;
    self.transfer.progress = ^(NSString *stage, NSString *code) {
        dispatch_async(dispatch_get_main_queue(), ^{ weakSelf.status.stringValue = stage; weakSelf.code.stringValue = code; });
    };
    self.transfer.openBrowser = ^(NSURL *url) {
        dispatch_async(dispatch_get_main_queue(), ^{
            if (!weakSelf.transfer.cancelled) [weakSelf.browserActions presentURL:url project:NO];
        });
    };
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        NSURL *project = nil; NSString *error = nil;
        @try { project = [self.transfer runToOrigin:origin]; }
        @catch (NSException *exception) { error = exception.reason; }
        @finally { [self.transfer close]; }
        dispatch_async(dispatch_get_main_queue(), ^{
            self.busy = NO; self.cancel.enabled = NO; self.origin.enabled = YES;
            [self.spinner stopAnimation:nil]; self.code.stringValue = @"";
            self.start.title = @"Повторить";
            self.start.enabled = !project && !self.transfer.cancelled;
            self.status.stringValue = project ? @"Проект создан. Откройте его для проверки исходных данных." : error;
            if (project) [self.browserActions presentURL:project project:YES];
            else [self.browserActions clear];
        });
    });
}
@end

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc == 2 && strcmp(argv[1], "--self-test") == 0) return [GATransfer selfTest];
        if (argc == 2 && strcmp(argv[1], "--ui-self-test") == 0) {
            [NSApplication sharedApplication];
            return [GABrowserActions selfTest];
        }
        if (argc == 4 && strcmp(argv[1], "--headless") == 0) {
            @try {
                GATransfer *transfer = [[GATransfer alloc] initWithTicket:[NSURL fileURLWithPath:@(argv[2])]];
                transfer.progress = ^(NSString *stage, NSString *code) { fprintf(stderr, "%s\n", stage.UTF8String); };
                NSURL *project = [transfer runToOrigin:@(argv[3])];
                puts(project.absoluteString.UTF8String); return 0;
            } @catch (NSException *error) { fprintf(stderr, "%s\n", error.reason.UTF8String); return 1; }
        }
        [NSApplication sharedApplication];
        [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
        Connector *delegate = [[Connector alloc] init];
        if (argc == 2) delegate.ticket = [NSURL fileURLWithPath:@(argv[1])];
        NSApp.delegate = delegate;
        [NSApp run];
    }
    return 0;
}
