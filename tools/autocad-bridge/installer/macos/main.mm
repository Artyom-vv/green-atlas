#import <AppKit/AppKit.h>
#import "launcher-entry.h"
#import "install-transaction.h"
#import "runtime-guard.h"
#import "qualification-root.h"
#include <unistd.h>

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
    NSURL *expected = relative.length ? [package.URLByResolvingSymlinksInPath URLByAppendingPathComponent:relative] : package.URLByResolvingSymlinksInPath;
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

static BOOL signedBundle(NSURL *bundle, NSString *identifier, NSError **error) {
    NSDictionary *info = [NSDictionary dictionaryWithContentsOfURL:[bundle URLByAppendingPathComponent:@"Contents/Info.plist"]];
    if (![info[@"CFBundleIdentifier"] isEqual:identifier]) {
        if (error) *error = failure([@"Неверная идентичность программного компонента: " stringByAppendingString:bundle.path]);
        return NO;
    }
    NSTask *task = [[NSTask alloc] init];
    task.executableURL = [NSURL fileURLWithPath:@"/usr/bin/codesign"];
    task.arguments = @[@"--verify", @"--deep", @"--strict", bundle.path];
    task.standardOutput = NSFileHandle.fileHandleWithNullDevice;
    task.standardError = NSFileHandle.fileHandleWithNullDevice;
    if (![task launchAndReturnError:error]) return NO;
    [task waitUntilExit];
    if (task.terminationStatus != 0) {
        if (error) *error = failure([@"Подпись программного компонента повреждена: " stringByAppendingString:bundle.path]);
        return NO;
    }
    return YES;
}
static BOOL packageChild(NSURL *package, NSString *relative, NSString *type, NSError **error) {
    NSURL *file = [package URLByAppendingPathComponent:relative];
    NSDictionary *attributes = [NSFileManager.defaultManager attributesOfItemAtPath:file.path error:nil];
    if (![attributes[NSFileType] isEqual:type] || !isExpectedPackageChild(package, file, relative)) {
        if (error) *error = failure([@"Отсутствует безопасный программный компонент: " stringByAppendingString:file.path]);
        return NO;
    }
    return YES;
}
// В unsigned outer package допустимы только известные программные каталоги.
// Неизвестные файлы не переносятся в новый payload и не удаляются автоматически.
static BOOL disposablePackage(NSURL *package, NSError **error) {
    NSURL *runtime = [desktopURL(package) URLByAppendingPathComponent:@"Contents/Resources/Runtime/GreenAtlasRuntime"];
    if (GAExactExecutableIsRunning(runtime)) {
        if (error) *error = failure(@"Программные файлы ещё используются локальным сервисом. Очистка отложена.");
        return NO;
    }
    NSDictionary *directories = @{@"":@[@"PackageContents.xml",@"Contents"],
        @"Contents":@[@"MacOS",@"Applications",@"Workers",@"green_atlas_loader.lsp"],
        @"Contents/MacOS":@[@"GreenAtlasBridge.bundle"], @"Contents/Workers":@[@"GreenAtlasQuery.bundle"]};
    for (NSString *relative in directories) {
        NSURL *directory = relative.length ? [package URLByAppendingPathComponent:relative] : package;
        if (![NSFileManager.defaultManager fileExistsAtPath:directory.path]) continue;
        if (!packageChild(package, relative, NSFileTypeDirectory, error)) return NO;
        NSArray *names = [NSFileManager.defaultManager contentsOfDirectoryAtPath:directory.path error:error];
        if (!names) return NO;
        for (NSString *name in names) {
            if (![directories[relative] containsObject:name] && ![name isEqual:@".DS_Store"]) {
                if (error) *error = failure([@"В установленном пакете есть посторонний файл. Он сохранён, обновление остановлено: " stringByAppendingString:[directory URLByAppendingPathComponent:name].path]);
                return NO;
            }
            NSString *childRelative = relative.length ? [relative stringByAppendingPathComponent:name] : name;
            BOOL directoryExpected = [@[@"Contents",@"Contents/MacOS",@"Contents/Applications",@"Contents/Workers",@"Contents/MacOS/GreenAtlasBridge.bundle",@"Contents/Workers/GreenAtlasQuery.bundle"] containsObject:childRelative];
            if (!packageChild(package, childRelative, directoryExpected ? NSFileTypeDirectory : NSFileTypeRegular, error)) return NO;
        }
    }
    for (NSArray *component in @[@[@"Contents/MacOS/GreenAtlasBridge.bundle", BridgeID], @[@"Contents/Workers/GreenAtlasQuery.bundle", @"ru.green-atlas.native-query-worker"]]) {
        NSURL *bundle = [package URLByAppendingPathComponent:component[0]];
        if ([NSFileManager.defaultManager fileExistsAtPath:bundle.path] && !signedBundle(bundle, component[1], error)) return NO;
    }
    NSURL *applications = [package URLByAppendingPathComponent:@"Contents/Applications"];
    if ([NSFileManager.defaultManager fileExistsAtPath:applications.path]) {
        NSArray *names = [NSFileManager.defaultManager contentsOfDirectoryAtPath:applications.path error:error];
        if (!names) return NO;
        for (NSString *name in names) {
            if ([name isEqual:@".DS_Store"]) continue;
            BOOL known = [name isEqual:@"Green Atlas.app"] || [name rangeOfString:@"^\\.green-atlas-[A-Za-z0-9_-]+-desktop(?:-[0-9a-f]{32})?\\.app$" options:NSRegularExpressionSearch].location != NSNotFound;
            NSString *relative = [@"Contents/Applications" stringByAppendingPathComponent:name];
            if (!known || !packageChild(package, relative, NSFileTypeDirectory, error)
                || !signedBundle([package URLByAppendingPathComponent:relative], @"ru.green-atlas.desktop", error)) {
                if (error && !*error) *error = failure([@"Неизвестные файлы в программном каталоге: " stringByAppendingString:relative]);
                return NO;
            }
        }
    }
    return YES;
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
    for (NSString *relative in @[@"PackageContents.xml",@"Contents/green_atlas_loader.lsp",
        @"Contents/MacOS/GreenAtlasBridge.bundle/Contents/Info.plist",
        @"Contents/MacOS/GreenAtlasBridge.bundle/Contents/MacOS/GreenAtlasBridge",
        @"Contents/Workers/GreenAtlasQuery.bundle/Contents/Info.plist",
        @"Contents/Workers/GreenAtlasQuery.bundle/Contents/MacOS/GreenAtlasBridge",
        @"Contents/Applications/Green Atlas.app/Contents/Info.plist"])
        if (!packageChild(package, relative, NSFileTypeRegular, error)) return NO;
    NSURL *worker = [package URLByAppendingPathComponent:@"Contents/Workers/GreenAtlasQuery.bundle"];
    if (!packageChild(package,@"Contents/MacOS/GreenAtlasBridge.bundle",NSFileTypeDirectory,error)
        || !packageChild(package,@"Contents/Workers/GreenAtlasQuery.bundle",NSFileTypeDirectory,error)
        || !signedBundle(worker,@"ru.green-atlas.native-query-worker",error)) return NO;
    NSURL *xml = [package URLByAppendingPathComponent:@"PackageContents.xml"];
    NSNumber *xmlSize = [NSFileManager.defaultManager attributesOfItemAtPath:xml.path error:nil][NSFileSize];
    if (xmlSize.unsignedLongLongValue > 131072) { if (error) *error=failure(@"Описание пакета слишком велико."); return NO; }
    NSXMLDocument *document = [[NSXMLDocument alloc] initWithContentsOfURL:xml options:NSXMLNodeLoadExternalEntitiesNever error:error];
    NSXMLElement *root = document.rootElement;
    NSArray *entries = [root nodesForXPath:@"./Components/ComponentEntry" error:error];
    NSMutableDictionary *modules = [NSMutableDictionary dictionary];
    for (NSXMLElement *entry in entries) {
        NSString *module = [entry attributeForName:@"ModuleName"].stringValue;
        NSString *type = [entry attributeForName:@"AppType"].stringValue;
        if (module && type) modules[module] = type;
    }
    NSDictionary *expected = @{@"./Contents/MacOS/GreenAtlasBridge.bundle":@"Arx",@"./Contents/green_atlas_loader.lsp":@"Lisp"};
    if (!document || document.DTD || ![root.name isEqual:@"ApplicationPackage"] || entries.count != 2
        || ![modules isEqual:expected]
        || ![[root attributeForName:@"AppVersion"].stringValue isEqual:info[@"CFBundleShortVersionString"]]) {
        if (error) *error = failure(@"Описание CAD-пакета не соответствует приложению или ссылается на посторонние файлы.");
        return NO;
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

// Старый payload удаляется лишь после durable commit и проверки новой установки.
static BOOL installPackage(NSURL *source, NSURL *parent, NSURL **backup, NSError **error) {
    if (backup) *backup = nil;
    if (!supportedHost()) {
        if (error) *error = failure(@"Эта сборка требует macOS 14 или новее и Apple Silicon. Файлы не изменены.");
        return NO;
    }
    return GAInstallManagedItems({{source, parent, PackageName, @".green-atlas", ownedPackage, verifySignature, disposablePackage}}, error);
}

static NSURL *userAddins() {
    NSURL *support = [[NSFileManager defaultManager]
        URLsForDirectory:NSApplicationSupportDirectory inDomains:NSUserDomainMask].firstObject;
    return [support URLByAppendingPathComponent:@"Autodesk/ApplicationAddins"];
}
static NSURL *userApplications() {
    return [NSURL fileURLWithPath:[NSHomeDirectory() stringByAppendingPathComponent:@"Applications"]];
}
static NSURL *launcherSource() {
    return [NSBundle.mainBundle.resourceURL URLByAppendingPathComponent:@"Green Atlas Launcher.app"];
}
static BOOL installProduct(NSURL *source, NSURL *parent, NSURL *applications, NSURL **backup, NSError **error, BOOL *cleanupPending = nullptr) {
    if (backup) *backup = nil;
    if (!supportedHost()) { if (error) *error = failure(@"Эта сборка требует macOS 14 или новее и Apple Silicon."); return NO; }
    return GAInstallManagedItems({
        {source, parent, PackageName, @".green-atlas", ownedPackage, verifySignature, disposablePackage},
        {launcherSource(), applications, @"Green Atlas.app", @".green-atlas-launcher", GAOwnedLauncher, GAValidLauncher, GAValidLauncher}
    }, error, cleanupPending);
}

// Блокировка живого API удерживается также во время recovery и удаления previous.
struct RuntimeInstallGuard {
    int descriptor = -1;
    ~RuntimeInstallGuard() { if (descriptor >= 0) close(descriptor); }
    BOOL acquire(NSError **error) {
        NSURL *package = [userAddins() URLByAppendingPathComponent:PackageName];
        NSURL *runtime = [desktopURL(package) URLByAppendingPathComponent:@"Contents/Resources/Runtime/GreenAtlasRuntime"];
        if (GAExactExecutableIsRunning(runtime)) {
            if (error) *error = failure(@"Локальный сервис Green Atlas ещё работает. Закройте приложение и повторите обновление.");
            return NO;
        }
        NSURL *support = [NSFileManager.defaultManager URLsForDirectory:NSApplicationSupportDirectory inDomains:NSUserDomainMask].firstObject;
        return GAAcquireWorkspaceInstallLease([support URLByAppendingPathComponent:@"Green Atlas/Workspace"], &descriptor, error);
    }
};

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
    RuntimeInstallGuard runtimeGuard;
    if (!runtimeGuard.acquire(&error)) { [self alert:error.localizedDescription]; return; }
    NSURL *backup = nil;
    self.installButton.enabled = NO;
    BOOL cleanupPending = NO;
    BOOL success = installProduct(self.source, userAddins(), userApplications(), &backup, &error, &cleanupPending);
    self.installButton.enabled = YES;
    if (!success) { [self alert:error.localizedDescription]; return; }
    self.status.stringValue = @"Установлено. Откройте Green Atlas из Applications. Для нового чертежа используйте плагин в AutoCAD.";
    if (cleanupPending) [self alert:@"Новая версия установлена. Старые программные файлы пока не удалось убрать. Повторный запуск установщика завершит очистку."];
}
- (void)remove:(id)sender {
    if (![self readyToChange]) return;
    NSError *guardFailure = nil;
    RuntimeInstallGuard runtimeGuard;
    if (!runtimeGuard.acquire(&guardFailure)) { [self alert:guardFailure.localizedDescription]; return; }
    NSURL *target = [userAddins() URLByAppendingPathComponent:PackageName];
    NSURL *journal = [userAddins() URLByAppendingPathComponent:@".green-atlas-install-transaction.plist"];
    if ([NSFileManager.defaultManager attributesOfItemAtPath:journal.path error:nil]) {
        [self alert:@"Предыдущее обновление не завершено. Повторите установку, чтобы восстановить или завершить его, затем повторите удаление."]; return;
    }
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
    NSURL *entry = [userApplications() URLByAppendingPathComponent:@"Green Atlas.app"];
    if (GAOwnedLauncher(entry) && ![NSFileManager.defaultManager trashItemAtURL:entry resultingItemURL:nil error:&error]) {
        [self alert:[NSString stringWithFormat:@"Пакет удалён, но ярлык не удалось удалить: %@", error.localizedDescription]];
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
        [NSString stringWithFormat:@"Приложение %@, CAD-плагин %@. Закройте AutoCAD перед установкой.",
            [NSBundle.mainBundle objectForInfoDictionaryKey:@"CFBundleShortVersionString"] ?: @"не определено",
            bridgeInfo(self.source)[@"CFBundleShortVersionString"] ?: @"не определён"]];
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
    BOOL update = installPackage(source, root, &backup, &error) && backup == nil && verifySignature(target, nil);
    if (!update) fprintf(stderr,"update-check: %s\n",error.localizedDescription.UTF8String ?: "validation failed");
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
    // Все обязательные части нового пакета проверяются до замены старого.
    BOOL metadataRefused = YES;
    for (NSString *missing in @[@"PackageContents.xml",@"Contents/green_atlas_loader.lsp",@"Contents/Workers/GreenAtlasQuery.bundle"]) {
        NSURL *candidate = [root URLByAppendingPathComponent:NSUUID.UUID.UUIDString];
        [manager copyItemAtURL:source toURL:candidate error:nil];
        [manager removeItemAtURL:[candidate URLByAppendingPathComponent:missing] error:nil];
        metadataRefused = metadataRefused && !installPackage(candidate,root,nil,&error) && verifySignature(target,nil);
    }
    NSURL *wrongXML = [root URLByAppendingPathComponent:@"wrong-version-source"];
    [manager copyItemAtURL:source toURL:wrongXML error:nil];
    NSURL *wrongXMLFile = [wrongXML URLByAppendingPathComponent:@"PackageContents.xml"];
    NSXMLDocument *description = [[NSXMLDocument alloc] initWithContentsOfURL:wrongXMLFile options:NSXMLNodeLoadExternalEntitiesNever error:nil];
    [[description.rootElement attributeForName:@"AppVersion"] setStringValue:@"0.0.0"];
    [description.XMLData writeToURL:wrongXMLFile options:0 error:nil];
    metadataRefused = metadataRefused && !installPackage(wrongXML,root,nil,&error) && verifySignature(target,nil);
    NSURL *brokenWorker = [root URLByAppendingPathComponent:@"broken-worker-source"];
    [manager copyItemAtURL:source toURL:brokenWorker error:nil];
    [@"broken" writeToURL:[brokenWorker URLByAppendingPathComponent:@"Contents/Workers/GreenAtlasQuery.bundle/Contents/MacOS/GreenAtlasBridge"] atomically:YES encoding:NSUTF8StringEncoding error:nil];
    metadataRefused = metadataRefused && !installPackage(brokenWorker,root,nil,&error) && verifySignature(target,nil);
    NSURL *linkedLoader = [root URLByAppendingPathComponent:@"linked-loader-source"];
    [manager copyItemAtURL:source toURL:linkedLoader error:nil];
    NSURL *loaderPath = [linkedLoader URLByAppendingPathComponent:@"Contents/green_atlas_loader.lsp"];
    [manager removeItemAtURL:loaderPath error:nil];
    [manager createSymbolicLinkAtURL:loaderPath withDestinationURL:[source URLByAppendingPathComponent:@"Contents/green_atlas_loader.lsp"] error:nil];
    metadataRefused = metadataRefused && !installPackage(linkedLoader,root,nil,&error) && verifySignature(target,nil);
    BOOL processCheck = isAutoCADEditor(@"com.autodesk.AutoCAD2027", @"app")
        && !isAutoCADEditor(@"com.autodesk.AutoCAD2027.AcQuickLookThumbnailer", @"appex")
        && !isAutoCADEditor(@"com.autodesk.AutoCAD2027.Helper", @"app")
        && !isAutoCADEditor(nil, @"app")
        && requiresClosedApp(@"ru.green-atlas.desktop", @"app")
        && !requiresClosedApp(@"com.apple.finder", @"app");
    NSURL *applications = [root URLByAppendingPathComponent:@"Applications"];
    BOOL entryInstalled = GAInstallLauncher(launcherSource(), applications, nil, &error)
        && GAOwnedLauncher([applications URLByAppendingPathComponent:@"Green Atlas.app"]);
    NSURL *entryBackup = nil;
    BOOL entryUpdated = GAInstallLauncher(launcherSource(), applications, &entryBackup, &error)
        && entryBackup == nil && GAOwnedLauncher([applications URLByAppendingPathComponent:@"Green Atlas.app"]);
    NSURL *unrelatedApps = [root URLByAppendingPathComponent:@"other-applications"];
    NSURL *unrelatedEntry = [unrelatedApps URLByAppendingPathComponent:@"Green Atlas.app"];
    [manager createDirectoryAtURL:unrelatedEntry withIntermediateDirectories:YES attributes:nil error:nil];
    BOOL entryRefused = !GAInstallLauncher(launcherSource(), unrelatedApps, nil, &error)
        && [manager fileExistsAtPath:unrelatedEntry.path];
    NSURL *linkedApps = [root URLByAppendingPathComponent:@"linked-applications"];
    [manager createSymbolicLinkAtURL:linkedApps withDestinationURL:applications error:nil];
    BOOL entryLinkRefused = !GAInstallLauncher(launcherSource(), linkedApps, nil, &error);
    NSURL *productRoot = [root URLByAppendingPathComponent:@"product"];
    NSURL *productApps = [root URLByAppendingPathComponent:@"product-applications"];
    BOOL productInstalled = installProduct(source, productRoot, productApps, nil, &error);
    NSURL *productTarget = [productRoot URLByAppendingPathComponent:PackageName];
    NSURL *unknownManual = [productTarget URLByAppendingPathComponent:@"manual-plan.dwg"];
    NSData *manualBytes = [@"user drawing" dataUsingEncoding:NSUTF8StringEncoding];
    [manualBytes writeToURL:unknownManual options:0 error:nil];
    BOOL unknownPreserved = !installProduct(source,productRoot,productApps,nil,&error)
        && [[NSData dataWithContentsOfURL:unknownManual] isEqual:manualBytes] && verifySignature(productTarget,nil);
    [manager removeItemAtURL:unknownManual error:nil];
    NSURL *restoreMarker = [productTarget URLByAppendingPathComponent:@"Contents/green_atlas_loader.lsp"];
    [@"previous installation" writeToURL:restoreMarker atomically:YES encoding:NSUTF8StringEncoding error:nil];
    NSURL *workspace = [root URLByAppendingPathComponent:@"Workspace"];
    [manager createDirectoryAtURL:workspace withIntermediateDirectories:YES attributes:nil error:nil];
    NSURL *database = [workspace URLByAppendingPathComponent:@"project.sqlite3"];
    NSData *originalData = [@"opaque user data" dataUsingEncoding:NSUTF8StringEncoding];
    [originalData writeToURL:database options:0 error:nil];
    [manager setAttributes:@{NSFilePosixPermissions:@0500} ofItemAtPath:productApps.path error:nil];
    BOOL productRolledBack = !installProduct(source, productRoot, productApps, nil, &error)
        && verifySignature(productTarget, nil) && [[NSString stringWithContentsOfURL:restoreMarker encoding:NSUTF8StringEncoding error:nil] isEqual:@"previous installation"];
    [manager setAttributes:@{NSFilePosixPermissions:@0700} ofItemAtPath:productApps.path error:nil];
    BOOL dataPreserved = [[NSData dataWithContentsOfURL:database] isEqual:originalData];
    BOOL passed = metadataRefused && unknownPreserved && first && identity && linkedSource && update && untouched && refusal && symlink && corruptRefused && connectorRefused && missingRefused && qaRefused && processCheck && entryInstalled && entryUpdated && entryRefused && entryLinkRefused && productInstalled && productRolledBack && dataPreserved;
    printf("{\"required_components_refused\":%s,\"unknown_manual_file_preserved\":%s}\n", metadataRefused ? "true" : "false", unknownPreserved ? "true" : "false");
    printf("{\"product_install\":%s,\"launcher_failure_restores_package\":%s,\"workspace_preserved\":%s}\n", productInstalled ? "true" : "false", productRolledBack ? "true" : "false", dataPreserved ? "true" : "false");
    printf("{\"launcher_install\":%s,\"launcher_update\":%s,\"launcher_unrelated_refused\":%s,\"launcher_symlink_refused\":%s}\n", entryInstalled ? "true" : "false", entryUpdated ? "true" : "false", entryRefused ? "true" : "false", entryLinkRefused ? "true" : "false");
    printf("{\"missing_desktop_refused\":%s,\"development_profile_refused\":%s,\"supported_host\":%s}\n", missingRefused ? "true" : "false", qaRefused ? "true" : "false", supportedHost() ? "true" : "false");
    printf("{\"passed\":%s,\"install\":%s,\"identity\":%s,\"linked_source\":%s,\"update_old_removed\":%s,\"source_preserved\":%s,\"unrelated_refused\":%s,\"symlink_refused\":%s,\"corrupted_package_refused\":%s,\"corrupted_connector_refused\":%s,\"background_helper_ignored\":%s}\n",
        passed ? "true" : "false", first ? "true" : "false", identity ? "true" : "false",
        linkedSource ? "true" : "false", update ? "true" : "false", untouched ? "true" : "false", refusal ? "true" : "false", symlink ? "true" : "false", corruptRefused ? "true" : "false", connectorRefused ? "true" : "false", processCheck ? "true" : "false");
    [manager removeItemAtURL:root error:nil];
    return passed ? 0 : 1;
}

// Приёмка использует ту же транзакцию, но только внутри собственного QA-root.
static BOOL qualificationClean(NSURL *parent, NSURL *applications, NSError **error) {
    for (NSURL *directory in @[parent, applications]) {
        NSArray<NSString *> *names = [NSFileManager.defaultManager contentsOfDirectoryAtPath:directory.path error:error];
        if (!names) return NO;
        for (NSString *name in names) {
            if ([name hasPrefix:@".green-atlas-previous-"] || [name hasPrefix:@".green-atlas-incoming-"]
                || [name hasPrefix:@".green-atlas-launcher-previous-"]
                || [name hasPrefix:@".green-atlas-launcher-incoming-"]
                || [name isEqual:@".green-atlas-install-transaction.plist"]) {
                if (error) *error = failure(@"Приёмка не завершена: очистка программных копий ещё ожидается.");
                return NO;
            }
        }
    }
    return YES;
}

static BOOL qualificationRemove(NSURL *root, NSURL *parent, NSURL *applications, NSError **error) {
    NSFileManager *manager = NSFileManager.defaultManager;
    NSURL *target = [parent URLByAppendingPathComponent:PackageName];
    NSURL *entry = [applications URLByAppendingPathComponent:@"Green Atlas.app"];
    NSURL *removed = [root URLByAppendingPathComponent:@"Removed"];
    if (!GAQualificationChild(root, target) || !GAQualificationChild(root, entry)
        || !GAQualificationChild(root, removed) || [manager attributesOfItemAtPath:removed.path error:nil]
        || !qualificationClean(parent, applications, error)
        || !verifySignature(target, error) || !disposablePackage(target, error)
        || !GAValidLauncher(entry, error)) return NO;
    if (![manager createDirectoryAtURL:removed withIntermediateDirectories:NO
        attributes:@{NSFilePosixPermissions:@0700} error:error]) return NO;
    NSURL *retained = [removed URLByAppendingPathComponent:PackageName];
    if (![manager moveItemAtURL:target toURL:retained error:error]) return NO;
    if (![manager moveItemAtURL:entry toURL:[removed URLByAppendingPathComponent:@"Green Atlas.app"] error:error]) {
        if (![manager moveItemAtURL:retained toURL:target error:nil] && error)
            *error = failure(@"Ошибка удаления приёмочного ярлыка; пакет сохранён внутри Removed.");
        return NO;
    }
    return YES;
}

static BOOL qualificationPurge(NSURL *root, NSError **error) {
    NSFileManager *manager = NSFileManager.defaultManager;
    NSURL *removed = [root URLByAppendingPathComponent:@"Removed"];
    if (!GAQualificationChild(root, removed)) return NO;
    NSArray<NSURL *> *items = [manager contentsOfDirectoryAtURL:removed includingPropertiesForKeys:nil options:0 error:error];
    if (!items) return NO;
    for (NSURL *item in items) {
        BOOL package = [item.lastPathComponent isEqual:PackageName];
        BOOL launcher = [item.lastPathComponent isEqual:@"Green Atlas.app"];
        if (!GAQualificationChild(root, item)
            || !((package && verifySignature(item, error) && disposablePackage(item, error))
                || (launcher && GAValidLauncher(item, error)))) {
            if (error && !*error) *error = failure(@"В Removed найден посторонний или повреждённый объект; он сохранён.");
            return NO;
        }
    }
    for (NSURL *item in items) if (![manager removeItemAtURL:item error:error]) return NO;
    return YES;
}

static int qualifyInstaller(NSString *action, NSURL *root, NSURL *source) {
    NSError *error = nil;
    BOOL success = NO, cleanupPending = NO;
    NSMutableDictionary *state = nil;
    NSURL *parent = [root URLByAppendingPathComponent:@"ApplicationAddins"];
    NSURL *applications = [root URLByAppendingPathComponent:@"Applications"];
    NSURL *target = [parent URLByAppendingPathComponent:PackageName];
    if ([action isEqual:@"init"]) {
        success = GACreateQualificationRoot(root, &error);
        if (success) state = [GAReadQualificationRoot(root, &error) mutableCopy];
    } else {
        state = [GAReadQualificationRoot(root, &error) mutableCopy];
        if (state && (!GAQualificationChild(root, parent) || !GAQualificationChild(root, applications))) {
            error = failure(@"Пути приёмочной установки вышли из собственного профиля.");
            state = nil;
        }
        NSString *phase = state[@"phase"];
        int frozenLease = -1, desktopLease = -1;
        struct LeaseRelease {
            int &first, &second;
            ~LeaseRelease() { if (first >= 0) close(first); if (second >= 0) close(second); }
        } leases{frozenLease, desktopLease};
        if (state) {
            NSURL *runtime = [desktopURL(target) URLByAppendingPathComponent:@"Contents/Resources/Runtime/GreenAtlasRuntime"];
            if (GAExactExecutableIsRunning(runtime)
                || !GAAcquireWorkspaceInstallLease([root URLByAppendingPathComponent:@"FrozenProfile/Workspace"], &frozenLease, &error)
                || !GAAcquireWorkspaceInstallLease([root URLByAppendingPathComponent:@"RuntimeProfile/Workspace"], &desktopLease, &error)) {
                if (!error) error = failure(@"Завершите собственный приёмочный API перед изменением программных файлов.");
                state = nil;
            }
        }
        if (state && [action isEqual:@"rollback"]) {
            error = failure(@"После успешного обновления старые сборки удаляются. Откат до commit выполняет установочная транзакция при сбое; отдельной прежней версии нет.");
        } else if (state && (([action isEqual:@"install"] && [phase isEqual:@"init"])
            || ([action isEqual:@"update"] && [@[@"installed", @"updated"] containsObject:phase]))) {
            success = installProduct(source, parent, applications, nil, &error, &cleanupPending)
                && !cleanupPending && verifySignature(target, &error)
                && GAValidLauncher([applications URLByAppendingPathComponent:@"Green Atlas.app"], &error)
                && qualificationClean(parent, applications, &error);
            if (success) {
                state[@"phase"] = [action isEqual:@"install"] ? @"installed" : @"updated";
                state[@"backup"] = NSNull.null;
            }
        } else if (state && [action isEqual:@"uninstall"]
            && [@[@"installed", @"updated", @"rolled_back"] containsObject:phase]) {
            success = qualificationRemove(root, parent, applications, &error);
            if (success) state[@"phase"] = @"uninstalled";
        } else if (state && [action isEqual:@"purge"] && [phase isEqual:@"uninstalled"]) {
            success = qualificationPurge(root, &error);
            if (success) { state[@"phase"] = @"purged"; state[@"backup"] = NSNull.null; }
        }
        if (success) success = GAWriteQualificationRoot(root, state, &error);
    }
    if (!success || !state) {
        fprintf(stderr, "%s\n", (error.localizedDescription ?: @"Недопустимая операция или порядок приёмки.").UTF8String);
        return 1;
    }
    NSURL *desktop = desktopURL(target);
    NSDictionary *receipt = @{@"schema":@"green-atlas.macos-installer-qualification/1",
        @"passed":@YES, @"action":action, @"phase":state[@"phase"], @"root":root.path,
        @"session_uuid":state[@"session_uuid"], @"uid":state[@"uid"],
        @"installed_bundle":target.path, @"desktop_app":desktop.path,
        @"runtime":[desktop URLByAppendingPathComponent:@"Contents/Resources/Runtime/GreenAtlasRuntime"].path,
        @"web":[desktop URLByAppendingPathComponent:@"Contents/Resources/Web"].path,
        @"worker":[target URLByAppendingPathComponent:@"Contents/Workers/GreenAtlasQuery.bundle"].path,
        @"launcher":[applications URLByAppendingPathComponent:@"Green Atlas.app"].path,
        @"backup":NSNull.null, @"cleanup_pending":@(cleanupPending),
        @"old_program_copies_removed":@YES, @"retained_version_rollback_available":@NO,
        @"same_payload_lifecycle":@YES, @"different_version_rollback_tested":@NO,
        @"live_installation_used":@NO, @"product_qualified":@NO, @"autocad_launched":@NO,
        @"uninstall_retention":@"qualification-root/Removed; Finder Trash not used"};
    NSData *json = [NSJSONSerialization dataWithJSONObject:receipt options:NSJSONWritingSortedKeys error:&error];
    if (!json) { fprintf(stderr, "%s\n", error.localizedDescription.UTF8String); return 1; }
    fwrite(json.bytes, 1, json.length, stdout); printf("\n");
    return 0;
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc >= 2 && strcmp(argv[1], "--qualification") == 0) {
            if (argc != 5 || strcmp(argv[3], "--root") != 0 || ![@(argv[4]) isAbsolutePath]) {
                fprintf(stderr, "Usage: --qualification init|install|update|rollback|uninstall|purge --root ABSOLUTE_NEW_QA_ROOT\n");
                return 2;
            }
            NSSet *actions = [NSSet setWithArray:@[@"init", @"install", @"update", @"rollback", @"uninstall", @"purge"]];
            NSString *action = @(argv[2]);
            if (![actions containsObject:action]) { fprintf(stderr, "Unknown qualification action.\n"); return 2; }
            return qualifyInstaller(action, [NSURL fileURLWithPath:@(argv[4])],
                [NSBundle.mainBundle.resourceURL URLByAppendingPathComponent:PackageName]);
        }
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
            RuntimeInstallGuard runtimeGuard;
            if (!runtimeGuard.acquire(&error)) { fprintf(stderr, "%s\n", error.localizedDescription.UTF8String); return 2; }
            NSURL *backup = nil;
            NSURL *source = [NSBundle.mainBundle.resourceURL URLByAppendingPathComponent:PackageName];
            BOOL cleanupPending = NO;
            if (!installProduct(source, userAddins(), userApplications(), &backup, &error, &cleanupPending)) {
                fprintf(stderr, "%s\n", error.localizedDescription.UTF8String);
                return 1;
            }
            NSDictionary *receipt = @{@"installed": @YES,
                @"version": [NSBundle.mainBundle objectForInfoDictionaryKey:@"CFBundleShortVersionString"] ?: @"unknown",
                @"plugin_version": bridgeInfo(source)[@"CFBundleShortVersionString"] ?: @"unknown",
                @"target": [userAddins() URLByAppendingPathComponent:PackageName].path,
                @"backup": backup.path ?: [NSNull null],
                @"cleanup_pending": @(cleanupPending),
                @"cleanup_message": cleanupPending ? error.localizedDescription ?: @"Очистка будет повторена" : @""};
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
