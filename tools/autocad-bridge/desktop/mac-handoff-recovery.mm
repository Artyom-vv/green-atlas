#import "mac-handoff-recovery.h"
#import "handoff-failure.h"

@interface GAHandoffChoice : NSObject
@property(strong) NSArray<NSDictionary *> *receipts;
@property(weak) NSAlert *alert;
- (void)changed:(NSPopUpButton *)choice;
@end
@implementation GAHandoffChoice
- (void)changed:(NSPopUpButton *)choice {
    GAHandoffFailure *failure = [GAHandoffFailure fromReceipt:self.receipts[choice.indexOfSelectedItem]];
    self.alert.informativeText = failure
        ? [NSString stringWithFormat:@"%@\n\n%@\n\nПодготовка продолжится только после отдельного подтверждения.", failure.title, failure.message]
        : @"Выберите передачу. Подготовка продолжится только после подтверждения.";
    [self.alert layout];
}
@end

void GAChooseHandoff(NSWindow *window, NSArray *receipts, void (^selected)(NSDictionary *)) {
    NSMutableArray<NSDictionary *> *valid = [NSMutableArray array];
    for (id item in receipts) {
        if (![item isKindOfClass:NSDictionary.class]) continue;
        NSString *identifier = item[@"id"];
        if (![identifier isKindOfClass:NSString.class] || identifier.length != 64
            || [identifier rangeOfCharacterFromSet:[NSCharacterSet characterSetWithCharactersInString:@"abcdef0123456789"].invertedSet].location != NSNotFound) continue;
        if ([@[@"failed", @"interrupted", @"cancelled", @"needs_review", @"queued", @"processing"] containsObject:item[@"status"]]) [valid addObject:item];
    }
    NSAlert *alert = [[NSAlert alloc] init];
    alert.messageText = @"Передачи AutoCAD";
    if (!valid.count) {
        alert.informativeText = @"Сохранённых передач пока нет.";
        [alert beginSheetModalForWindow:window completionHandler:nil]; return;
    }
    NSPopUpButton *choice = [[NSPopUpButton alloc] initWithFrame:NSMakeRect(0, 0, 380, 26) pullsDown:NO];
    for (NSDictionary *item in valid) {
        NSDictionary *labels = @{@"failed":@"Ошибка", @"interrupted":@"Прервана", @"cancelled":@"Отменена", @"needs_review":@"Проект сохранён", @"queued":@"В очереди", @"processing":@"Подготовка"};
        [choice addItemWithTitle:[NSString stringWithFormat:@"%@, %@", labels[item[@"status"]], [item[@"id"] substringToIndex:8]]];
    }
    alert.accessoryView = choice;
    GAHandoffChoice *presentation = [[GAHandoffChoice alloc] init];
    presentation.receipts = valid; presentation.alert = alert;
    choice.target = presentation; choice.action = @selector(changed:);
    [presentation changed:choice];
    [alert addButtonWithTitle:@"Выбрать"]; [alert addButtonWithTitle:@"Отмена"];
    [alert beginSheetModalForWindow:window completionHandler:^(NSModalResponse response) {
        if (response == NSAlertFirstButtonReturn) selected(presentation.receipts[choice.indexOfSelectedItem]);
    }];
}
