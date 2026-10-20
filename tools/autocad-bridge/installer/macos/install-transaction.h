#import <Foundation/Foundation.h>
#include <vector>

// Только программные каталоги. Данные профиля не являются частью транзакции.
struct GAInstallItem {
    NSURL *__strong source;
    NSURL *__strong parent;
    NSString *__strong name;
    NSString *__strong prefix;
    BOOL (*owned)(NSURL *);
    BOOL (*validate)(NSURL *, NSError **);
    BOOL (*disposable)(NSURL *, NSError **) = nullptr;
};
BOOL GAInstallManagedItems(const std::vector<GAInstallItem> &items, NSError **error, BOOL *cleanupPending = nullptr);
#ifdef GA_INSTALL_TRANSACTION_TESTING
extern void (*GAInstallTransactionHook)(NSString *event);
#endif
