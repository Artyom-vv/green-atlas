#import <AppKit/AppKit.h>
#import <CommonCrypto/CommonDigest.h>
#include <dlfcn.h>
#include "delivery_ui.h"
#import "../connector/macos/LocalPath.h"

static NSString* text(const std::string& value) { return [NSString stringWithUTF8String:value.c_str()] ?: @""; }
static void (*openCommand)() = nullptr;
@interface GAOpenMenuTarget : NSObject
- (void)open:(id)sender;
@end
@implementation GAOpenMenuTarget
- (void)open:(id)sender { if (openCommand) openCommand(); }
@end
static GAOpenMenuTarget* menuTarget;
static NSMenuItem* rootMenuItem;
static NSPanel* progressPanel;
static NSTextField* progressLabel;

namespace gaDelivery {
static NSView* sourceFacts(const std::vector<SourceNotice>& facts) {
    NSMutableArray* rows = [NSMutableArray new];
    for (const auto& fact : facts) {
        NSTextField* label = [NSTextField wrappingLabelWithString:text(fact.label)];
        label.textColor = NSColor.secondaryLabelColor;
        NSTextField* value = [NSTextField wrappingLabelWithString:text(fact.value)];
        value.font = [NSFont systemFontOfSize:NSFont.systemFontSize weight:NSFontWeightMedium];
        [rows addObject:@[label, value]];
    }
    NSGridView* grid = [NSGridView gridViewWithViews:rows];
    grid.rowSpacing = 10;
    grid.columnSpacing = 20;
    grid.xPlacement = NSGridCellPlacementLeading;
    [grid.widthAnchor constraintEqualToConstant:380].active = YES;
    return grid;
}
void installMenu(void (*command)()) {
    if (!NSApp.mainMenu) return;
    if (rootMenuItem && rootMenuItem.menu == NSApp.mainMenu) return;
    // AutoCAD rebuilds/replaces its menu when the first document opens. An
    // allocated item is not evidence it is still attached to the visible menu.
    [rootMenuItem.menu removeItem:rootMenuItem];
    openCommand = command;
    menuTarget = [GAOpenMenuTarget new];
    rootMenuItem = [[NSMenuItem alloc] initWithTitle:@"Green Atlas" action:nil keyEquivalent:@""];
    rootMenuItem.submenu = [[NSMenu alloc] initWithTitle:@"Green Atlas"];
    NSMenuItem* item = [[NSMenuItem alloc] initWithTitle:@"Открыть в Green Atlas…" action:@selector(open:) keyEquivalent:@""];
    item.target = menuTarget;
    [rootMenuItem.submenu addItem:item];
    [NSApp.mainMenu addItem:rootMenuItem];
}
void removeMenu() {
    [rootMenuItem.menu removeItem:rootMenuItem];
    rootMenuItem = nil; menuTarget = nil; openCommand = nullptr;
    endStage();
}
bool confirmPreparation(bool modified) {
    NSAlert* alert = [NSAlert new];
    alert.messageText = @"Открыть в Green Atlas";
    alert.informativeText = @"";
    alert.accessoryView = sourceFacts({
        {"Источник", modified ? "С несохранёнными изменениями" : "Открытый чертёж"},
        {"Подосновы", "Загруженные в AutoCAD"},
        {"Исходный файл", "Без изменений"},
    });
    [alert addButtonWithTitle:@"Открыть"];
    [alert addButtonWithTitle:@"Отмена"];
    return [alert runModal] == NSAlertFirstButtonReturn;
}
bool confirmLivePartial(const std::vector<SourceNotice>& issues) {
    NSAlert* alert = [NSAlert new];
    alert.alertStyle = NSAlertStyleWarning;
    alert.messageText = @"Есть замечания к исходнику";
    alert.informativeText = @"";
    alert.accessoryView = sourceFacts(issues);
    [alert addButtonWithTitle:@"Открыть доступные данные"];
    [alert addButtonWithTitle:@"Вернуться в AutoCAD"];
    return [alert runModal] == NSAlertFirstButtonReturn;
}
bool confirmPartial(const std::vector<std::string>& issues) {
    NSAlert* alert = [NSAlert new];
    alert.messageText = @"Не все данные удалось подготовить";
    alert.informativeText = @"Можно продолжить с доступной геометрией или вернуться в AutoCAD. Эти замечания сохранятся вместе с копией.";
    NSScrollView* scroll = [[NSScrollView alloc] initWithFrame:NSMakeRect(0, 0, 430, 120)];
    scroll.hasVerticalScroller = YES;
    NSTextView* details = [[NSTextView alloc] initWithFrame:NSMakeRect(0, 0, 410, 120)];
    details.editable = NO; details.font = [NSFont systemFontOfSize:12];
    NSMutableArray* lines = [NSMutableArray new];
    for (const auto& issue : issues) [lines addObject:text(issue)];
    details.string = [lines componentsJoinedByString:@"\n\n"];
    scroll.documentView = details; alert.accessoryView = scroll;
    [alert addButtonWithTitle:@"Продолжить"];
    [alert addButtonWithTitle:@"Вернуться в AutoCAD"];
    return [alert runModal] == NSAlertFirstButtonReturn;
}
ReferenceRecovery confirmReferenceRecovery(size_t lost, size_t available) {
    NSAlert* alert = [NSAlert new];
    alert.messageText = @"Часть подоснов не попала в копию";
    alert.informativeText = [NSString stringWithFormat:
        @"Ссылок: %lu. Доступны для восстановления: %lu.\nВосстановим доступные ссылки только в копии, остальные будут пропущены. Исходный чертёж не меняется.",
        (unsigned long)lost, (unsigned long)available];
    if (available) [alert addButtonWithTitle:@"Восстановить доступные"];
    [alert addButtonWithTitle:@"Продолжить без них"];
    [alert addButtonWithTitle:@"Вернуться в AutoCAD"];
    const NSModalResponse response = [alert runModal];
    if (available && response == NSAlertFirstButtonReturn) return ReferenceRecovery::RestoreAvailable;
    if (response == (available ? NSAlertSecondButtonReturn : NSAlertFirstButtonReturn)) return ReferenceRecovery::Skip;
    return ReferenceRecovery::Cancel;
}
void error(const std::string& message) {
    NSAlert* alert = [NSAlert new];
    alert.messageText = @"Передача не начата";
    alert.informativeText = text(message);
    [alert addButtonWithTitle:@"Понятно"];
    [alert runModal];
}
void stage(const std::string& message) {
    if (!progressPanel) {
        progressPanel = [[NSPanel alloc] initWithContentRect:NSMakeRect(0, 0, 450, 92)
            styleMask:NSWindowStyleMaskTitled backing:NSBackingStoreBuffered defer:NO];
        progressPanel.title = @"Green Atlas";
        progressLabel = [NSTextField wrappingLabelWithString:@""];
        progressLabel.frame = NSMakeRect(20, 20, 410, 50);
        [progressPanel.contentView addSubview:progressLabel];
        [progressPanel center]; [progressPanel orderFront:nil];
    }
    progressLabel.stringValue = text(message);
    // Do not pump an event loop: AutoCAD database calls must not be re-entered.
    [progressPanel display];
}
void endStage() { [progressPanel orderOut:nil]; progressPanel = nil; progressLabel = nil; }

std::string createStaging() {
    NSFileManager* files = NSFileManager.defaultManager;
    NSURL* support = [files URLForDirectory:NSApplicationSupportDirectory inDomain:NSUserDomainMask
                         appropriateForURL:nil create:YES error:nil];
    NSURL* root = [support URLByAppendingPathComponent:@"Green Atlas/Transfers" isDirectory:YES];
    if (!root) return {};
    if (![files createDirectoryAtURL:root withIntermediateDirectories:YES
                         attributes:@{NSFilePosixPermissions:@0700} error:nil]) return {};
    if (!GALocalDirectPath(root)) return {};
    NSURL* directory = [root URLByAppendingPathComponent:NSUUID.UUID.UUIDString isDirectory:YES];
    if (![files createDirectoryAtURL:directory withIntermediateDirectories:NO
                         attributes:@{NSFilePosixPermissions:@0700} error:nil]) return {};
    return directory.path.UTF8String;
}

static NSDictionary* fileEntry(NSURL* url, NSString* kind) {
    NSDictionary* attributes = [NSFileManager.defaultManager attributesOfItemAtPath:url.path error:nil];
    if (![attributes[NSFileType] isEqual:NSFileTypeRegular]
        || !GALocalDirectPath(url)) return nil;
    NSInputStream* input = [NSInputStream inputStreamWithURL:url];
    [input open];
    CC_SHA256_CTX context; CC_SHA256_Init(&context);
    uint8_t bytes[65536]; NSInteger count; unsigned long long total = 0;
    while ((count = [input read:bytes maxLength:sizeof(bytes)]) > 0) {
        CC_SHA256_Update(&context, bytes, (CC_LONG)count); total += count;
    }
    [input close];
    if (count < 0 || total != [attributes[NSFileSize] unsignedLongLongValue] || total == 0) return nil;
    unsigned char digest[CC_SHA256_DIGEST_LENGTH]; CC_SHA256_Final(digest, &context);
    NSMutableString* hash = [NSMutableString new];
    for (int i = 0; i < CC_SHA256_DIGEST_LENGTH; ++i) [hash appendFormat:@"%02x", digest[i]];
    return @{@"name":url.lastPathComponent, @"kind":kind, @"bytes":@(total), @"sha256":hash};
}

static NSMutableDictionary* readProbe(NSURL* probe) {
    NSDictionary* attributes = [NSFileManager.defaultManager attributesOfItemAtPath:probe.path error:nil];
    if (![attributes[NSFileType] isEqual:NSFileTypeRegular]
        || [attributes[NSFileSize] unsignedLongLongValue] > 768ULL*1024*1024
        || !GALocalDirectPath(probe)) return nil;
    NSData* raw = [NSData dataWithContentsOfURL:probe options:NSDataReadingMappedIfSafe error:nil];
    id object = raw ? [NSJSONSerialization JSONObjectWithData:raw options:NSJSONReadingMutableContainers error:nil] : nil;
    return [object isKindOfClass:NSMutableDictionary.class] ? object : nil;
}

std::vector<std::string> probeIssues(const std::string& directory) {
    NSURL* root = [NSURL fileURLWithPath:text(directory) isDirectory:YES];
    NSDictionary* probe = readProbe([root URLByAppendingPathComponent:@"Drawing.dxf.green-atlas.geometry.json"]);
    if (!probe) return {"Файл геометрии не прошёл проверку чтения."};
    NSDictionary* summary = probe[@"summary"];
    if (![summary isKindOfClass:NSDictionary.class]) return {"AutoCAD не предоставил итог проверки геометрии."};
    std::vector<std::string> issues;
    if ([summary[@"cyclic_block_references"] isKindOfClass:NSNumber.class]
        && [summary[@"cyclic_block_references"] unsignedLongLongValue] > 0)
        issues.push_back("В копии обнаружены циклические ссылки. Их содержимое не подготовлено для расчёта.");
    if ([summary[@"unexpanded_minsert_blocks"] isKindOfClass:NSNumber.class]
        && [summary[@"unexpanded_minsert_blocks"] unsignedLongLongValue] > 0)
        issues.push_back("Часть массивов объектов не удалось раскрыть в копии.");
    for (NSString* key in @[@"unresolved_instances", @"unreadable_entities", @"unreadable_block_records"])
        if ([summary[key] isKindOfClass:NSNumber.class] && [summary[key] unsignedLongLongValue] > 0) {
            issues.push_back("Часть объектов недоступна для расчёта. Сервис покажет подробности при открытии.");
            break;
        }
    return issues;
}

std::vector<SourceNotice> liveProbeIssues(const std::string& directory) {
    NSURL* root = [NSURL fileURLWithPath:text(directory) isDirectory:YES];
    NSDictionary* probe = readProbe([root URLByAppendingPathComponent:@"Drawing.autocad.json"]);
    if (!probe) return {{"Чтение снимка", "Ошибка"}};
    NSDictionary* summary = probe[@"summary"];
    NSDictionary* source = probe[@"source"];
    if (![summary isKindOfClass:NSDictionary.class] || ![source isKindOfClass:NSDictionary.class])
        return {{"Проверка снимка", "Нет результата"}};
    std::vector<SourceNotice> issues;
    auto countNotice = [&](NSString* key, const char* label) {
        NSNumber* value = summary[key];
        if ([value isKindOfClass:NSNumber.class] && value.longLongValue > 0)
            issues.push_back({label, std::to_string(value.unsignedLongLongValue)});
    };
    // This is display-extraction coverage, not native calculation coverage.
    // Unsaved state was already shown before capture and is not a defect.
    countNotice(@"unresolved_instances", "Без геометрии отображения");
    countNotice(@"cyclic_block_references", "Циклические ссылки");
    countNotice(@"unexpanded_minsert_blocks", "Нераскрытые массивы");
    size_t missingReferences = 0;
    for (NSDictionary* dependency in probe[@"xref_dependencies"] ?: @[]) {
        if (![dependency isKindOfClass:NSDictionary.class]) continue;
        if ([dependency[@"status"] isEqual:@"unresolved"]) {
            ++missingReferences;
        }
    }
    if (missingReferences) issues.push_back({"Недоступные подосновы", std::to_string(missingReferences)});
    return issues;
}

std::string writeLiveTicket(const std::string& directory, const std::string& version,
                            const std::string& autocadVersion, const std::string& liveSession) {
    NSURL* root = [NSURL fileURLWithPath:text(directory) isDirectory:YES];
    NSURL* capture = [root URLByAppendingPathComponent:@"Drawing.autocad.json"];
    NSURL* destination = [root URLByAppendingPathComponent:@"transfer.gatransfer"];
    if ([NSFileManager.defaultManager fileExistsAtPath:destination.path]) return {};
    NSDictionary* probe = readProbe(capture);
    NSDictionary* source = probe[@"source"];
    NSString* original = source[@"path"];
    if (![probe[@"capture_mode"] isEqual:@"live_document"]
        || ![probe[@"plugin_version"] isEqual:text(version)]
        || ![original isKindOfClass:NSString.class] || !original.length
        || ![source[@"sha256"] isKindOfClass:NSString.class]) return {};
    NSDictionary* captureEntry = fileEntry(capture, @"live_capture");
    if (!captureEntry || [captureEntry[@"bytes"] unsignedLongLongValue] > 768ULL*1024*1024)
        return {};
    // The path is context, not the live geometry source, but a stale disk hash
    // would make its provenance misleading. Never silently substitute disk data.
    NSDictionary* diskEntry = fileEntry([NSURL fileURLWithPath:original], @"source_context");
    if (!diskEntry || ![diskEntry[@"sha256"] isEqual:source[@"sha256"]]) return {};
    NSString* sourceName = original.lastPathComponent;
    if (!sourceName.length || sourceName.length > 240) return {};
#if defined(__arm64__)
    NSString* target = @"macos-arm64";
#else
    NSString* target = @"macos-x86_64";
#endif
    NSMutableDictionary* ticket = [@{@"schema":@"green-atlas.transfer/2", @"plugin_version":text(version),
        @"producer":@{@"autocad_version":text(autocadVersion), @"target":target},
        @"manifest":@{@"entry":@"Drawing.autocad.json", @"files":@[captureEntry]},
        @"source_name":sourceName} mutableCopy];
    if (!liveSession.empty()) {
        NSData* sessionData = [text(liveSession) dataUsingEncoding:NSUTF8StringEncoding];
        NSDictionary* session = [NSJSONSerialization JSONObjectWithData:sessionData options:0 error:nil];
        if (![session isKindOfClass:NSDictionary.class]
            || ![session[@"snapshot_sha256"] isEqual:captureEntry[@"sha256"]]) return {};
        ticket[@"schema"] = @"green-atlas.transfer/4";
        ticket[@"live_session"] = session;
    }
    NSData* data = [NSJSONSerialization dataWithJSONObject:ticket options:NSJSONWritingPrettyPrinted error:nil];
    if (!data) return {};
    if (![NSFileManager.defaultManager createFileAtPath:destination.path contents:data
                                            attributes:@{NSFilePosixPermissions:@0600}]) return {};
    return destination.path.UTF8String;
}

std::string writeTicket(const std::string& directory, const std::string& version,
                        const std::string& autocadVersion, const std::vector<std::string>& issues) {
    NSURL* root = [NSURL fileURLWithPath:text(directory) isDirectory:YES];
    NSURL* drawing = [root URLByAppendingPathComponent:@"Drawing.dxf"];
    NSURL* probe = [root URLByAppendingPathComponent:@"Drawing.dxf.green-atlas.geometry.json"];
    NSURL* destination = [root URLByAppendingPathComponent:@"transfer.gatransfer"];
    if ([NSFileManager.defaultManager fileExistsAtPath:destination.path]) return {};
    // Preparation diagnostics travel inside the hashed native evidence, not just a local alert.
    NSMutableDictionary* object = readProbe(probe);
    NSDictionary* dxfEntry = fileEntry(drawing, @"drawing");
    if (!object || !dxfEntry || ![object[@"source"] isKindOfClass:NSDictionary.class]
        || ![object[@"source"][@"sha256"] isEqual:dxfEntry[@"sha256"]]
        || ![object[@"plugin_version"] isEqual:text(version)]) return {};
    NSMutableArray* notices = [NSMutableArray new];
    for (const auto& issue : issues) [notices addObject:text(issue)];
    object[@"preparation"] = @{@"mode":@"autocad-copy-bind", @"notices":notices};
    NSData* prepared = [NSJSONSerialization dataWithJSONObject:object options:0 error:nil];
    if (!prepared || ![prepared writeToURL:probe options:NSDataWritingAtomic error:nil]) return {};
    NSDictionary* probeEntry = fileEntry(probe, @"native_probe");
    if (!dxfEntry || !probeEntry) return {};
    if ([dxfEntry[@"bytes"] unsignedLongLongValue] + [probeEntry[@"bytes"] unsignedLongLongValue] > 2ULL*1024*1024*1024) return {};
#if defined(__arm64__)
    NSString* target = @"macos-arm64";
#else
    NSString* target = @"macos-x86_64";
#endif
    NSDictionary* ticket = @{@"schema":@"green-atlas.transfer/1", @"plugin_version":text(version),
        @"producer":@{@"autocad_version":text(autocadVersion), @"target":target},
        @"manifest":@{@"entry":@"Drawing.dxf", @"files":@[dxfEntry, probeEntry]}};
    NSData* data = [NSJSONSerialization dataWithJSONObject:ticket options:NSJSONWritingPrettyPrinted error:nil];
    if (!data) return {};
    if (![NSFileManager.defaultManager createFileAtPath:destination.path contents:data
                                            attributes:@{NSFilePosixPermissions:@0600}]) return {};
    return destination.path.UTF8String;
}

bool launchConnector(const std::string& ticket) {
    Dl_info image{};
    if (!dladdr((void*)&launchConnector, &image) || !image.dli_fname) return false;
    // ...package/Contents/MacOS/GreenAtlasBridge.bundle/Contents/MacOS/binary
    NSURL* directory = [NSURL fileURLWithPath:[NSString stringWithUTF8String:image.dli_fname]];
    for (int i = 0; i < 5; ++i) directory = directory.URLByDeletingLastPathComponent;
    NSURL* app = [directory URLByAppendingPathComponent:@"Applications/Green Atlas.app"];
    if (!GALocalDirectPath(app) || ![[NSBundle bundleWithURL:app].bundleIdentifier isEqual:@"ru.green-atlas.desktop"]) return false;
    NSWorkspaceOpenConfiguration* config = [NSWorkspaceOpenConfiguration configuration];
    config.activates = NO; // The app raises its own window once the project is ready.
    config.createsNewApplicationInstance = NO; // The local app owns the queue and one workspace runtime.
    [NSWorkspace.sharedWorkspace openURLs:@[[NSURL fileURLWithPath:text(ticket)]]
         withApplicationAtURL:app configuration:config completionHandler:^(NSRunningApplication* application, NSError* failure) {
        if (failure) dispatch_async(dispatch_get_main_queue(), ^{ gaDelivery::error("macOS не смогла открыть приложение передачи. Проверьте установку Green Atlas."); });
    }];
    return true;
}
}
