#import "handoff-failure.h"

static NSString *GAResponseMessage(NSDictionary *response) {
    id value = response[@"message"];
    if (![value isKindOfClass:NSString.class]) return nil;
    NSString *message = [value stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
    return message.length ? message : nil;
}

@implementation GAHandoffFailure
+ (instancetype)fromReceipt:(NSDictionary *)receipt {
    NSDictionary *titles = @{
        @"failed": @"Не удалось открыть проект",
        @"interrupted": @"Передача прервана",
        @"cancelled": @"Передача отменена"
    };
    NSString *status = receipt[@"status"];
    if (![status isKindOfClass:NSString.class] || !titles[status]) return nil;
    GAHandoffFailure *failure = [[self alloc] init];
    failure->_title = titles[status];
    failure->_message = GAResponseMessage(receipt) ?: @"Сервис не сообщил причину остановки.";
    return failure;
}
@end

NSString *GADesktopRequestError(NSDictionary *response, NSInteger status, BOOL transportFailed) {
    if (transportFailed) return @"Не удалось связаться с локальным сервисом. Состояние передачи можно проверить в списке передач AutoCAD после восстановления связи.";
    if (status >= 200 && status < 300) return nil;
    return GAResponseMessage(response) ?: @"Локальный сервис не подтвердил запрос. Проверьте состояние передачи в списке передач AutoCAD.";
}
