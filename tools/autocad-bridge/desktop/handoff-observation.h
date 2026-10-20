#import <Foundation/Foundation.h>

typedef void (^GAHandoffRead)(NSString *path, void (^done)(NSDictionary *, NSString *));
typedef void (^GAHandoffFailureObserved)(NSString *identifier, NSDictionary *receipt);

// A stopped runtime needs a journal read, never a replay of the transfer write.
// The read port deliberately has no submit, cancel or resume capability.
@interface GAHandoffObservation : NSObject
- (void)rememberRuntimeStop:(NSString *)identifier;
- (void)readFailureIfIdle:(BOOL)idle read:(GAHandoffRead)read observed:(GAHandoffFailureObserved)observed;
- (void)clear;
@end
