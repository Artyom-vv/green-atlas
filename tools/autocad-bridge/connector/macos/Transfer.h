#import <Foundation/Foundation.h>

@interface GATransfer : NSObject <NSURLSessionTaskDelegate>
@property(atomic) BOOL cancelled;
@property(atomic, strong) NSURLSessionDataTask *activeTask;
@property(copy) void (^progress)(NSString *stage, NSString *code);
@property(copy) void (^openBrowser)(NSURL *url);
- (instancetype)initWithTicket:(NSURL *)ticket;
- (NSURL *)runToOrigin:(NSString *)origin;
- (void)cancel;
- (void)close;
+ (BOOL)validOrigin:(NSString *)origin;
+ (int)selfTest;
@end
