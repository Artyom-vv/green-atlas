// Тестовый запуск настоящего формирователя передачи без AutoCAD.
#import <AppKit/AppKit.h>
#include "delivery_ui.h"
#include <cstdio>

namespace {
// Проверяем непустую видимую область текста NSAlert без AutoCAD; снимок проверяется отдельно.
static NSArray<NSString*>* visibleTexts(NSView* view, NSView* content) {
    NSMutableArray<NSString*>* values = [NSMutableArray new];
    if ([view isKindOfClass:NSTextField.class] || [view isKindOfClass:NSButton.class]) {
        BOOL visible = !view.isHiddenOrHasHiddenAncestor;
        for (NSView* ancestor = view; ancestor; ancestor = ancestor.superview) {
            const NSRect bounds = [view convertRect:view.bounds toView:ancestor];
            if (NSIsEmptyRect(NSIntersectionRect(bounds, ancestor.bounds))) visible = NO;
            if (ancestor == content) break;
        }
        if (visible) [values addObject:[view isKindOfClass:NSButton.class]
            ? ((NSButton*)view).title : ((NSTextField*)view).stringValue];
    }
    for (NSView* child in view.subviews) [values addObjectsFromArray:visibleTexts(child, content)];
    return values;
}

static std::vector<gaDelivery::SourceNotice> noticeFacts(bool single) {
    if (single) return {{"Недоступные подосновы", "2"}};
    return {
        {"Без геометрии отображения", "17"},
        {"Циклические ссылки", "3"},
        {"Нераскрытые массивы", "4"},
        {"Недоступные подосновы", "2"},
    };
}

static int checkNotice(bool preparation, bool single, bool accept) {
    [NSApplication sharedApplication];
    __block BOOL passed = NO;
    __block NSDictionary* observed;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 300 * NSEC_PER_MSEC), dispatch_get_main_queue(), ^{
        NSWindow* modal = NSApp.modalWindow;
        NSString* contents = [visibleTexts(modal.contentView, modal.contentView) componentsJoinedByString:@"\n"];
        NSArray<NSString*>* expected = preparation ? @[
            @"Источник", @"С несохранёнными изменениями", @"Подосновы", @"Загруженные в AutoCAD",
            @"Исходный файл", @"Без изменений", @"Состояние AutoCAD",
            @"После создания копии чертёж может отмечаться изменённым",
        ] : (single ? @[@"Недоступные подосновы", @"2"] : @[
            @"Без геометрии отображения", @"17", @"Циклические ссылки", @"3",
            @"Нераскрытые массивы", @"4", @"Недоступные подосновы", @"2",
        ]);
        passed = modal.isVisible && contents.length > 0;
        for (NSString* value in expected) passed = passed && [contents containsString:value];
        for (NSString* button in preparation ? @[@"Открыть", @"Отмена"]
            : @[@"Открыть доступные данные", @"Вернуться в AutoCAD"])
            passed = passed && [contents containsString:button];
        observed = @{
            @"passed":@(passed), @"visible_text":contents ?: @"", @"expected_text":expected,
            @"scenario":preparation ? @"preparation" : (single ? @"live-single" : @"live-four"),
            @"accept":@(accept),
        };
        [NSApp stopModalWithCode:accept ? NSAlertFirstButtonReturn : NSAlertSecondButtonReturn];
    });
    const bool result = preparation ? gaDelivery::confirmPreparation(true)
        : gaDelivery::confirmLivePartial(noticeFacts(single));
    NSMutableDictionary* receipt = [observed mutableCopy] ?: [NSMutableDictionary new];
    receipt[@"confirmation_return"] = @(result);
    NSData* data = [NSJSONSerialization dataWithJSONObject:receipt options:0 error:nil];
    std::puts([[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding].UTF8String);
    return passed && result == accept ? 0 : 1;
}

static int previewNotice(bool preparation, bool single) {
    [NSApplication sharedApplication];
    [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
    // Штатный цикл завершает запуск приложения до показа модалки для проверки кадром.
    dispatch_async(dispatch_get_main_queue(), ^{
        [NSApp activate];
        const bool accepted = preparation ? gaDelivery::confirmPreparation(true)
            : gaDelivery::confirmLivePartial(noticeFacts(single));
        std::puts(accepted ? "accepted" : "cancelled");
        [NSApp terminate:nil];
    });
    [NSApp run];
    return 0;
}
}

int main(int argc, const char* argv[]) {
    @autoreleasepool {
        if (argc == 1) {
            NSString* scenario = [NSBundle.mainBundle objectForInfoDictionaryKey:@"GAQANotice"];
            if ([scenario isEqual:@"preparation"]) return previewNotice(true, false);
            if ([scenario isEqual:@"live-single"]) return previewNotice(false, true);
            if ([scenario isEqual:@"live-four"]) return previewNotice(false, false);
        }
        if (argc != 3) return 2;
        const std::string command = argv[1];
        if (command == "progress-check") {
            [NSApplication sharedApplication];
            gaDelivery::stage("Считываем открытый чертёж средствами AutoCAD");
            gaDelivery::progress("collecting", 10000);
            NSWindow* panel = nil;
            for (NSWindow* window in NSApp.windows)
                if ([window.title isEqual:@"Green Atlas"] && window.isVisible) panel = window;
            if (!panel) return 1;
            const auto before = [visibleTexts(panel.contentView, panel.contentView) componentsJoinedByString:@"\n"];
            gaDelivery::progress("inventory", 20000);
            const auto after = [visibleTexts(panel.contentView, panel.contentView) componentsJoinedByString:@"\n"];
            BOOL barVisible = NO;
            for (NSView* child in panel.contentView.subviews) {
                if ([child isKindOfClass:NSProgressIndicator.class]) {
                    NSProgressIndicator* bar = (NSProgressIndicator*)child;
                    barVisible = bar.indeterminate && bar.usesThreadedAnimation
                        && NSContainsRect(panel.contentView.bounds, bar.frame);
                }
            }
            const bool passed = [before containsString:@"Читаем объекты"]
                && [before containsString:@"Обработано: 10000"]
                && [after containsString:@"Получаем свойства объектов"]
                && [after containsString:@"Обработано: 20000"] && barVisible;
            gaDelivery::endStage();
            return passed && !panel.isVisible ? 0 : 1;
        }
        if (command == "notice-check") {
            const std::string scenario = argv[2];
            if (scenario == "preparation") return checkNotice(true, false, true);
            if (scenario == "live-single") return checkNotice(false, true, true);
            if (scenario == "live-four") return checkNotice(false, false, true);
            if (scenario == "live-cancel") return checkNotice(false, false, false);
            return 2;
        }
        if (command == "menu") {
            [NSApplication sharedApplication];
            NSApp.mainMenu = [[NSMenu alloc] initWithTitle:@"First"];
            gaDelivery::installMenu(+[]{});
            gaDelivery::installMenu(+[]{});
            if (NSApp.mainMenu.numberOfItems != 1) return 1;
            [NSApp.mainMenu removeItemAtIndex:0];
            gaDelivery::installMenu(+[]{});
            if (NSApp.mainMenu.numberOfItems != 1) return 2;
            NSApp.mainMenu = [[NSMenu alloc] initWithTitle:@"Document"];
            gaDelivery::installMenu(+[]{});
            if (NSApp.mainMenu.numberOfItems != 1) return 3;
            gaDelivery::removeMenu();
            return NSApp.mainMenu.numberOfItems == 0 ? 0 : 4;
        }
        if (command == "inspect") {
            NSURL* url = [[NSURL fileURLWithPath:[NSString stringWithUTF8String:argv[2]] isDirectory:YES]
                URLByAppendingPathComponent:@"Drawing.dxf.green-atlas.geometry.json"];
            NSError* error = nil;
            NSData* data = [NSData dataWithContentsOfURL:url options:0 error:&error];
            id object = data ? [NSJSONSerialization JSONObjectWithData:data options:NSJSONReadingMutableContainers error:&error] : nil;
            NSLog(@"path=%@ resolved=%@ attributes=%@ class=%@ error=%@", url.path,
                url.URLByResolvingSymlinksInPath.path,
                [NSFileManager.defaultManager attributesOfItemAtPath:url.path error:nil], [object class], error);
            return 0;
        }
        if (command == "ticket") {
            const auto result = gaDelivery::writeTicket(argv[2], "0.1.28", "2027",
                {"Missing reference: survey.dwg"});
            if (result.empty()) return 1;
            std::puts(result.c_str());
            return 0;
        }
        if (command == "live-ticket") {
            const auto result = gaDelivery::writeLiveTicket(argv[2], "0.1.38", "2027");
            if (result.empty()) return 1;
            std::puts(result.c_str());
            return 0;
        }
        // Проверяем оба ограничения размера настоящими чтением и созданием передачи.
        if (command == "live-size-accepted" || command == "live-size-rejected") {
            const auto issues = gaDelivery::liveProbeIssues(argv[2]);
            const bool readable = issues.size() == 1
                && issues.front().label == "Без геометрии отображения"
                && issues.front().value == "7";
            const auto result = gaDelivery::writeLiveTicket(argv[2], "0.1.38", "2027");
            const bool accepted = command == "live-size-accepted";
            const bool rejected = issues.size() == 1
                && issues.front().label == "Чтение снимка" && issues.front().value == "Ошибка";
            return accepted ? (readable && !result.empty() ? 0 : 1)
                : (rejected && result.empty() ? 0 : 1);
        }
        if (command == "archived-live-ticket" || command == "archived-compact-ticket") {
            NSURL* root = [NSURL fileURLWithPath:[NSString stringWithUTF8String:argv[2]] isDirectory:YES];
            NSString* session = [NSString stringWithContentsOfURL:
                [root URLByAppendingPathComponent:@"live-session.json"]
                encoding:NSUTF8StringEncoding error:nil];
            if (!session) return 2;
            NSString* facts = command == "archived-compact-ticket"
                ? [NSString stringWithContentsOfURL:[root URLByAppendingPathComponent:@"transfer-facts.json"]
                    encoding:NSUTF8StringEncoding error:nil] : @"";
            if (!facts) return 2;
            const auto result = gaDelivery::writeLiveTicket(argv[2], "0.1.48", "2027",
                session.UTF8String, facts.UTF8String);
            if (result.empty()) return 1;
            std::puts(result.c_str());
            return 0;
        }
        if (command == "live-issues" || command == "compact-issues") {
            NSString* facts = command == "compact-issues" ? [NSString stringWithContentsOfURL:
                [[NSURL fileURLWithPath:[NSString stringWithUTF8String:argv[2]] isDirectory:YES]
                    URLByAppendingPathComponent:@"transfer-facts.json"]
                encoding:NSUTF8StringEncoding error:nil] : @"";
            if (!facts) return 2;
            for (const auto& issue : gaDelivery::liveProbeIssues(argv[2], facts.UTF8String))
                std::printf("%s\t%s\n", issue.label.c_str(), issue.value.c_str());
            return 0;
        }
        if (command == "issues") {
            for (const auto& issue : gaDelivery::probeIssues(argv[2])) std::puts(issue.c_str());
            return 0;
        }
        return 2;
    }
}
