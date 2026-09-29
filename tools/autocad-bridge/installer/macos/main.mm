#import <AppKit/AppKit.h>

static NSString *const BridgeID = @"ru.green-atlas.autocad-bridge";
static NSString *const PackageName = @"GreenAtlasBridge.bundle";

static NSError *failure(NSString *message) {
    return [NSError errorWithDomain:BridgeID code:1
        userInfo:@{NSLocalizedDescriptionKey: message}];
}

static NSURL *nativeURL(NSURL *package) {
    return [package URLByAppendingPathComponent:@"Contents/MacOS/GreenAtlasBridge.bundle"];
}

static NSDictionary *bridgeInfo(NSURL *package) {
    return [NSDictionary dictionaryWithContentsOfURL:
        [nativeURL(package) URLByAppendingPathComponent:@"Contents/Info.plist"]];
}

static NSURL *desktopURL(NSURL *package) {
    return [package URLByAppendingPathComponent:@"Contents/Applications/Green Atlas.app"];
}

// Finder may launch a quarantined app from an App Translocation path whose
// parent resolves through a symlink. Compare children against the resolved
// package root, while still rejecting symlinks *inside* the package.
static BOOL isExpectedPackageChild(NSURL *package, NSURL *child, NSString *relative) {
    NSURL *expected = [package.URLByResolvingSymlinksInPath URLByAppendingPathComponent:relative];
    return [child.URLByResolvingSymlinksInPath.path isEqualToString:expected.path];
}

static BOOL supportedHost() {
#if defined(__arm64__)
    return [NSProcessInfo.processInfo isOperatingSystemAtLeastVersion:(NSOperatingSystemVersion){14, 0, 0}];
#else
    return NO;
#endif
}

// Do not overwrite an unrelated bundle, or follow a symlink out of the target.
static BOOL ownedPackage(NSURL *package) {
    NSDictionary *attributes = [[NSFileManager defaultManager]
        attributesOfItemAtPath:package.path error:nil];
    return [attributes[NSFileType] isEqualToString:NSFileTypeDirectory]
        && [bridgeInfo(package)[@"CFBundleIdentifier"] isEqualToString:BridgeID];
}

static BOOL verifySignature(NSURL *package, NSError **error) {
    if (!ownedPackage(package)) {
        if (error) *error = failure(@"Пакет Green Atlas повреждён или имеет другую идентичность.");
        return NO;
    }
    NSMutableArray<NSURL *> *targets = [NSMutableArray arrayWithObject:nativeURL(package)];
    NSURL *desktop = desktopURL(package);
    NSDictionary *info = [NSDictionary dictionaryWithContentsOfURL:[desktop URLByAppendingPathComponent:@"Contents/Info.plist"]];
    if (![info[@"CFBundleIdentifier"] isEqual:@"ru.green-atlas.desktop"]
        || ![info[@"LSMinimumSystemVersion"] isEqual:@"14.0"]
        || ![info[@"GASupportedArchitectures"] isEqual:@[@"arm64"]]
        || info[@"GADevelopmentProfile"] != nil
        || !isExpectedPackageChild(package, desktop, @"Contents/Applications/Green Atlas.app")) {
        if (error) *error = failure(@"В пакете нет совместимого локального приложения Green Atlas.");
        return NO;
    }
    for (NSString *relative in @[@"Contents/MacOS/GreenAtlasDesktop", @"Contents/Resources/Runtime/GreenAtlasRuntime", @"Contents/Resources/Web/index.html"]) {
        NSURL *file = [desktop URLByAppendingPathComponent:relative];
        NSDictionary *attributes = [NSFileManager.defaultManager attributesOfItemAtPath:file.path error:nil];
        NSString *packageRelative = [@"Contents/Applications/Green Atlas.app" stringByAppendingPathComponent:relative];
        if (![attributes[NSFileType] isEqual:NSFileTypeRegular]
            || !isExpectedPackageChild(package, file, packageRelative)) {
            if (error) *error = failure(@"Локальный движок или интерфейс отсутствует. Скачайте полный пакет.");
            return NO;
        }
    }
    [targets addObject:desktop];
    for (NSURL *target in targets) {
        NSTask *task = [[NSTask alloc] init];
        task.executableURL = [NSURL fileURLWithPath:@"/usr/bin/codesign"];
        task.arguments = @[@"--verify", @"--deep", @"--strict", target.path];
        task.standardOutput = [NSFileHandle fileHandleWithNullDevice];
        task.standardError = [NSFileHandle fileHandleWithNullDevice];
        if (![task launchAndReturnError:error]) return NO;
        [task waitUntilExit];
        if (task.terminationStatus != 0) {
            if (error) *error = failure(@"Подпись плагина не прошла проверку. Скачайте установщик заново.");
            return NO;
        }
    }
    return YES;
}

static BOOL safeParent(NSURL *parent, NSError **error) {
    NSURL *absolute = parent.URLByStandardizingPath;
    if (![absolute.path isEqualToString:absolute.URLByResolvingSymlinksInPath.path]) {
        if (error) *error = failure(@"Каталог установки содержит символическую ссылку. Установка остановлена.");
        return NO;
    }
    return [[NSFileManager defaultManager] createDirectoryAtURL:parent
        withIntermediateDirectories:YES attributes:nil error:error];
}

// Stage and validate first. Preserve the old package until the replacement is ready.
static BOOL installPackage(NSURL *source, NSURL *parent, NSURL **backup, NSError **error) {
    NSFileManager *manager = [NSFileManager defaultManager];
    if (!supportedHost()) {
        if (error) *error = failure(@"Эта сборка требует macOS 14 или новее и Apple Silicon. Файлы не изменены.");
        return NO;
    }
    if (!verifySignature(source, error) || !safeParent(parent, error)) return NO;
    NSURL *target = [parent URLByAppendingPathComponent:PackageName];
    BOOL exists = [manager attributesOfItemAtPath:target.path error:nil] != nil;
    if (exists && !ownedPackage(target)) {
        if (error) *error = failure(@"На месте плагина находится посторонний файл. Он не изменён.");
        return NO;
    }
    NSURL *staging = [parent URLByAppendingPathComponent:
        [@".green-atlas-incoming-" stringByAppendingString:NSUUID.UUID.UUIDString]];
    if (![manager copyItemAtURL:source toURL:staging error:error]) return NO;
    if (!verifySignature(staging, error)) {
        [manager removeItemAtURL:staging error:nil];
        return NO;
    }
    NSURL *previous = nil;
    if (exists) {
        previous = [parent URLByAppendingPathComponent:
            [@".green-atlas-previous-" stringByAppendingString:NSUUID.UUID.UUIDString]];
        if (![manager moveItemAtURL:target toURL:previous error:error]) {
            [manager removeItemAtURL:staging error:nil];
            return NO;
        }
    }
    if (![manager moveItemAtURL:staging toURL:target error:error]) {
        if (previous && ![manager moveItemAtURL:previous toURL:target error:nil]) {
            if (error) *error = failure([NSString stringWithFormat:
                @"Замена не завершена. Предыдущий плагин сохранён здесь: %@", previous.path]);
        }
        [manager removeItemAtURL:staging error:nil];
        return NO;
    }
    if (backup) *backup = previous;
    return YES;
}

static NSURL *userAddins() {
    NSURL *support = [[NSFileManager defaultManager]
        URLsForDirectory:NSApplicationSupportDirectory inDomains:NSUserDomainMask].firstObject;
    return [support URLByAppendingPathComponent:@"Autodesk/ApplicationAddins"];
}

static BOOL isAutoCADEditor(NSString *identifier, NSString *extension) {
    // Quick Look retains com.autodesk.AutoCAD2027.AcQuickLookThumbnailer after
    // the editor has quit. It does not load this ARX package and must not block updates.
    return [extension isEqualToString:@"app"] && identifier != nil
        && [identifier rangeOfString:@"^com\\.autodesk\\.AutoCAD[0-9]{4}$"
                            options:NSRegularExpressionSearch].location != NSNotFound;
}

static BOOL requiresClosedApp(NSString *identifier, NSString *extension) {
    return isAutoCADEditor(identifier, extension)
        || ([extension isEqual:@"app"] && [identifier isEqual:@"ru.green-atlas.desktop"]);
}

static BOOL productRunning() {
    for (NSRunningApplication *app in NSWorkspace.sharedWorkspace.runningApplications) {
        if (!app.terminated && requiresClosedApp(app.bundleIdentifier, app.bundleURL.pathExtension)) return YES;
    }
    return NO;
}

@interface Installer : NSObject <NSApplicationDelegate>
@property(strong) NSWindow *window;
@property(strong) NSTextField *status;
@property(strong) NSButton *installButton;
@end

@implementation Installer
- (NSURL *)source {
    return [NSBundle.mainBundle.resourceURL URLByAppendingPathComponent:PackageName];
}
- (void)alert:(NSString *)message {
    NSAlert *alert = [[NSAlert alloc] init];
    alert.messageText = @"Green Atlas";
    alert.informativeText = message;
    [alert addButtonWithTitle:@"Понятно"];
    [alert runModal];
}
- (BOOL)readyToChange {
    if (!productRunning()) return YES;
    [self alert:@"Сохраните работу и закройте AutoCAD и Green Atlas перед обновлением или удалением. Установщик не закрывает их автоматически."];
    return NO;
}
- (void)install:(id)sender {
    if (![self readyToChange]) return;
    NSError *error = nil;
    NSURL *backup = nil;
    self.installButton.enabled = NO;
    BOOL success = installPackage(self.source, userAddins(), &backup, &error);
    self.installButton.enabled = YES;
    if (!success) { [self alert:error.localizedDescription]; return; }
    self.status.stringValue = @"Установлено. В AutoCAD выберите Green Atlas → Открыть в Green Atlas. Проекты и расчёты останутся на компьютере.";
    if (backup) [self alert:[NSString stringWithFormat:
        @"Плагин обновлён. Предыдущая версия сохранена для восстановления:\n%@", backup.path]];
}
- (void)remove:(id)sender {
    if (![self readyToChange]) return;
    NSURL *target = [userAddins() URLByAppendingPathComponent:PackageName];
    if (!ownedPackage(target)) { [self alert:@"Установленный пакет Green Atlas не найден. Файлы не изменены."]; return; }
    NSAlert *confirmation = [[NSAlert alloc] init];
    confirmation.messageText = @"Удалить плагин Green Atlas?";
    confirmation.informativeText = @"Пакет переместится в Корзину. Чертежи и проекты останутся на месте.";
    [confirmation addButtonWithTitle:@"Отмена"];
    [confirmation addButtonWithTitle:@"Удалить"];
    if ([confirmation runModal] != NSAlertSecondButtonReturn) return;
    NSError *error = nil;
    if (!safeParent(userAddins(), &error) || ![[NSFileManager defaultManager]
            trashItemAtURL:target resultingItemURL:nil error:&error]) {
        [self alert:error.localizedDescription]; return;
    }
    self.status.stringValue = @"Плагин перемещён в Корзину. Его можно восстановить.";
}
- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
    NSMenu *menu = [[NSMenu alloc] init];
    NSMenuItem *appItem = [[NSMenuItem alloc] init];
    [menu addItem:appItem];
    NSMenu *appMenu = [[NSMenu alloc] init];
    [appMenu addItemWithTitle:@"Завершить установщик" action:@selector(terminate:) keyEquivalent:@"q"];
    appItem.submenu = appMenu;
    NSApp.mainMenu = menu;
    self.window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 520, 300)
        styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
        backing:NSBackingStoreBuffered defer:NO];
    self.window.title = @"Green Atlas для AutoCAD";
    self.window.releasedWhenClosed = NO;
    NSTextField *title = [NSTextField labelWithString:@"Green Atlas для AutoCAD"];
    title.font = [NSFont systemFontOfSize:23 weight:NSFontWeightSemibold];
    title.frame = NSMakeRect(28, 236, 470, 32);
    NSTextField *subtitle = [NSTextField wrappingLabelWithString:
        @"AutoCAD 2027, macOS 14 или новее, Apple Silicon\nЛокальное приложение и плагин. Чертежи не изменяются."];
    subtitle.frame = NSMakeRect(28, 165, 460, 58);
    self.status = [NSTextField wrappingLabelWithString:
        [NSString stringWithFormat:@"Версия %@. Закройте AutoCAD перед установкой.",
            bridgeInfo(self.source)[@"CFBundleShortVersionString"] ?: @"не определена"]];
    self.status.frame = NSMakeRect(28, 92, 460, 58);
    self.status.textColor = NSColor.secondaryLabelColor;
    self.installButton = [NSButton buttonWithTitle:@"Установить / обновить"
        target:self action:@selector(install:)];
    self.installButton.frame = NSMakeRect(292, 28, 200, 34);
    self.installButton.keyEquivalent = @"\r";
    NSButton *remove = [NSButton buttonWithTitle:@"Удалить…" target:self action:@selector(remove:)];
    remove.frame = NSMakeRect(28, 28, 110, 34);
    for (NSView *view in @[title, subtitle, self.status, self.installButton, remove])
        [self.window.contentView addSubview:view];
    [self.window center];
    [self.window makeKeyAndOrderFront:nil];
    [NSApp activateIgnoringOtherApps:YES];
}
- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)sender { return YES; }
@end

// Exercise exactly the installer implementation in a disposable directory.
// No user's AutoCAD profile or installed package is touched by this mode.
static int selfTest(NSURL *source) {
    NSFileManager *manager = [NSFileManager defaultManager];
    NSURL *root = [[NSURL fileURLWithPath:NSTemporaryDirectory()].URLByResolvingSymlinksInPath
        URLByAppendingPathComponent:[@"green-atlas-installer-test-" stringByAppendingString:NSUUID.UUID.UUIDString]];
    NSError *error = nil;
    NSURL *backup = nil;
    BOOL first = installPackage(source, root, &backup, &error) && backup == nil;
    NSURL *target = [root URLByAppendingPathComponent:PackageName];
    BOOL identity = ownedPackage(target);
    NSURL *sourceParentLink = [root URLByAppendingPathComponent:@"source-parent-link"];
    [manager createSymbolicLinkAtURL:sourceParentLink
        withDestinationURL:source.URLByDeletingLastPathComponent error:nil];
    NSURL *sourceThroughLink = [sourceParentLink URLByAppendingPathComponent:PackageName];
    NSURL *linkedSourceRoot = [root URLByAppendingPathComponent:@"linked-source-install"];
    BOOL linkedSource = installPackage(sourceThroughLink, linkedSourceRoot, nil, &error);
    BOOL update = installPackage(source, root, &backup, &error) && ownedPackage(backup);
    BOOL untouched = ownedPackage(source);
    NSURL *otherRoot = [root URLByAppendingPathComponent:@"unrelated"];
    NSURL *other = [otherRoot URLByAppendingPathComponent:PackageName];
    [manager createDirectoryAtURL:other withIntermediateDirectories:YES attributes:nil error:nil];
    BOOL refusal = !installPackage(source, otherRoot, nil, &error)
        && [manager fileExistsAtPath:other.path];
    NSURL *link = [root URLByAppendingPathComponent:@"linked"];
    [manager createSymbolicLinkAtURL:link withDestinationURL:otherRoot error:nil];
    BOOL symlink = !installPackage(source, link, nil, &error);
    NSURL *corrupted = [root URLByAppendingPathComponent:@"corrupted-source"];
    [manager copyItemAtURL:source toURL:corrupted error:nil];
    NSURL *executable = [nativeURL(corrupted) URLByAppendingPathComponent:@"Contents/MacOS/GreenAtlasBridge"];
    [@"invalid binary" writeToURL:executable atomically:YES encoding:NSUTF8StringEncoding error:nil];
    BOOL corruptRefused = !installPackage(corrupted, root, nil, &error)
        && verifySignature(target, nil);
    NSURL *badConnector = [root URLByAppendingPathComponent:@"corrupted-connector-source"];
    [manager copyItemAtURL:source toURL:badConnector error:nil];
    NSURL *connectorBinary = [desktopURL(badConnector) URLByAppendingPathComponent:@"Contents/Resources/Runtime/GreenAtlasRuntime"];
    BOOL connectorRefused = [manager fileExistsAtPath:connectorBinary.path];
    [@"invalid runtime" writeToURL:connectorBinary atomically:YES encoding:NSUTF8StringEncoding error:nil];
    connectorRefused = connectorRefused && !installPackage(badConnector, root, nil, &error) && verifySignature(target, nil);
    NSURL *noDesktop = [root URLByAppendingPathComponent:@"missing-desktop-source"];
    [manager copyItemAtURL:source toURL:noDesktop error:nil];
    [manager removeItemAtURL:desktopURL(noDesktop) error:nil];
    BOOL missingRefused = !installPackage(noDesktop, root, nil, &error) && verifySignature(target, nil);
    NSURL *qaDesktop = [root URLByAppendingPathComponent:@"qa-desktop-source"];
    [manager copyItemAtURL:source toURL:qaDesktop error:nil];
    NSURL *qaInfo = [desktopURL(qaDesktop) URLByAppendingPathComponent:@"Contents/Info.plist"];
    NSMutableDictionary *qa = [[NSDictionary dictionaryWithContentsOfURL:qaInfo] mutableCopy];
    qa[@"GADevelopmentProfile"] = @"/tmp/qa";
    [qa writeToURL:qaInfo error:nil];
    BOOL qaRefused = !installPackage(qaDesktop, root, nil, &error) && verifySignature(target, nil);
    BOOL processCheck = isAutoCADEditor(@"com.autodesk.AutoCAD2027", @"app")
        && !isAutoCADEditor(@"com.autodesk.AutoCAD2027.AcQuickLookThumbnailer", @"appex")
        && !isAutoCADEditor(@"com.autodesk.AutoCAD2027.Helper", @"app")
        && !isAutoCADEditor(nil, @"app")
        && requiresClosedApp(@"ru.green-atlas.desktop", @"app")
        && !requiresClosedApp(@"com.apple.finder", @"app");
    BOOL passed = first && identity && linkedSource && update && untouched && refusal && symlink && corruptRefused && connectorRefused && missingRefused && qaRefused && processCheck;
    printf("{\"missing_desktop_refused\":%s,\"development_profile_refused\":%s,\"supported_host\":%s}\n", missingRefused ? "true" : "false", qaRefused ? "true" : "false", supportedHost() ? "true" : "false");
    printf("{\"passed\":%s,\"install\":%s,\"identity\":%s,\"linked_source\":%s,\"update_backup\":%s,\"source_preserved\":%s,\"unrelated_refused\":%s,\"symlink_refused\":%s,\"corrupted_package_refused\":%s,\"corrupted_connector_refused\":%s,\"background_helper_ignored\":%s}\n",
        passed ? "true" : "false", first ? "true" : "false", identity ? "true" : "false",
        linkedSource ? "true" : "false", update ? "true" : "false", untouched ? "true" : "false", refusal ? "true" : "false", symlink ? "true" : "false", corruptRefused ? "true" : "false", connectorRefused ? "true" : "false", processCheck ? "true" : "false");
    [manager removeItemAtURL:root error:nil];
    return passed ? 0 : 1;
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc == 2 && strcmp(argv[1], "--self-test") == 0)
            return selfTest([NSBundle.mainBundle.resourceURL URLByAppendingPathComponent:PackageName]);
        // The CLI uses exactly the GUI's ownership, signature and running-app
        // guards; a failed update never discards the previous installation.
        if (argc == 2 && strcmp(argv[1], "--install") == 0) {
            if (productRunning()) {
                fprintf(stderr, "Close AutoCAD and Green Atlas before installing. No files changed.\n");
                return 2;
            }
            NSError *error = nil;
            NSURL *backup = nil;
            NSURL *source = [NSBundle.mainBundle.resourceURL URLByAppendingPathComponent:PackageName];
            if (!installPackage(source, userAddins(), &backup, &error)) {
                fprintf(stderr, "%s\n", error.localizedDescription.UTF8String);
                return 1;
            }
            NSDictionary *receipt = @{@"installed": @YES,
                @"version": bridgeInfo(source)[@"CFBundleShortVersionString"] ?: @"unknown",
                @"target": [userAddins() URLByAppendingPathComponent:PackageName].path,
                @"backup": backup.path ?: [NSNull null]};
            NSData *json = [NSJSONSerialization dataWithJSONObject:receipt options:0 error:nil];
            fwrite(json.bytes, 1, json.length, stdout);
            printf("\n");
            return 0;
        }
        [NSApplication sharedApplication];
        Installer *delegate = [[Installer alloc] init];
        NSApp.delegate = delegate;
        [NSApp run];
    }
    return 0;
}
