#import "handoff-observation.h"
#import "handoff-failure.h"

@implementation GAHandoffObservation {
    NSString *_identifier;
    NSUInteger _generation;
}
- (void)rememberRuntimeStop:(NSString *)identifier {
    [self clear];
    _identifier = [identifier copy];
}
- (void)clear { _identifier = nil; ++_generation; }
- (void)readFailureIfIdle:(BOOL)idle read:(GAHandoffRead)read observed:(GAHandoffFailureObserved)observed {
    if (!idle || !_identifier) return;
    NSString *identifier = _identifier;
    NSUInteger generation = _generation;
    _identifier = nil;
    read([@"/_desktop/handoffs/" stringByAppendingString:identifier], ^(NSDictionary *receipt, NSString *error) {
        if (generation == self->_generation && !error && [GAHandoffFailure fromReceipt:receipt]) observed(identifier, receipt);
    });
}
@end
