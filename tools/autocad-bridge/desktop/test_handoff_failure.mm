#import "handoff-failure.h"
#include <cassert>

static void GAAssert(bool condition) { assert(condition); }

int main() {
    @autoreleasepool {
        NSString *cause = @"CAD-архив и перечень имеют разные исходные объекты";
        GAHandoffFailure *failure = [GAHandoffFailure fromReceipt:@{
            @"status": @"failed", @"message": cause, @"stage": @"Подготовка остановлена"
        }];
        GAAssert([failure.title isEqual:@"Не удалось открыть проект"]);
        GAAssert([failure.message isEqual:cause]);
        GAAssert([[GAHandoffFailure fromReceipt:@{
            @"status": @"interrupted", @"message": @"Приложение закрыто"
        }].title isEqual:@"Передача прервана"]);
        GAAssert([[GAHandoffFailure fromReceipt:@{
            @"status": @"cancelled", @"message": @"Отменено пользователем"
        }].title isEqual:@"Передача отменена"]);
        for (id message in @[@42, NSNull.null, @"  \n\t"]) {
            GAAssert([[GAHandoffFailure fromReceipt:@{
                @"status": @"failed", @"message": message
            }].message isEqual:@"Сервис не сообщил причину остановки."]);
        }
        for (id status in @[@42, NSNull.null, @"needs_review", @"processing", @"queued", @"unknown"]) {
            GAAssert(![GAHandoffFailure fromReceipt:@{@"status": status, @"message": cause}]);
        }
        GAAssert(![GAHandoffFailure fromReceipt:@{}]);
        NSString *plainText = @"<details>Слой 0\nПодоснова XREF</details>";
        GAAssert([[GAHandoffFailure fromReceipt:@{
            @"status": @"failed", @"message": plainText
        }].message isEqual:plainText]);
        GAAssert(!GADesktopRequestError(@{@"message": cause}, 202, NO));
        GAAssert([GADesktopRequestError(@{@"message": cause}, 400, NO) isEqual:cause]);
        GAAssert([GADesktopRequestError(@{@"message": @"Передача или проект не найдены"}, 404, NO)
            isEqual:@"Передача или проект не найдены"]);
        GAAssert([GADesktopRequestError(@{@"message": @42}, 500, NO)
            containsString:@"не подтвердил запрос"]);
        GAAssert([GADesktopRequestError(nil, 0, YES) containsString:@"Не удалось связаться"]);
        // A failed transport cannot promote an unrelated/body-only message to a
        // confirmed journal outcome. It must offer an explicit state read.
        GAAssert(![GADesktopRequestError(@{@"message": cause}, 202, YES) containsString:cause]);
    }
}
