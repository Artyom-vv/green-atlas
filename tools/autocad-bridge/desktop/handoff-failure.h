#import <Foundation/Foundation.h>

// Only the authenticated native channel supplies these messages. Keep their
// presentation separate from transfer retry, journal mutation and navigation.
@interface GAHandoffFailure : NSObject
@property(copy, readonly) NSString *title;
@property(copy, readonly) NSString *message;
+ (instancetype)fromReceipt:(NSDictionary *)receipt;
@end

NSString *GADesktopRequestError(NSDictionary *response, NSInteger status, BOOL transportFailed);
