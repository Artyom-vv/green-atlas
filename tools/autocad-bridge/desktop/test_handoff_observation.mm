#import "handoff-observation.h"
#import "handoff-failure.h"
#include <cassert>

int main() {
    @autoreleasepool {
        NSString *identifier = [@"a" stringByPaddingToLength:64 withString:@"a" startingAtIndex:0];
        NSString *cause = @"CAD-архив и перечень имеют разные исходные объекты";
        GAHandoffObservation *observation = [[GAHandoffObservation alloc] init];
        __block NSUInteger reads = 0, presentations = 0;
        __block void (^reply)(NSDictionary *, NSString *) = nil;
        GAHandoffRead read = ^(NSString *path, void (^done)(NSDictionary *, NSString *)) {
            ++reads;
            assert([path isEqual:[@"/_desktop/handoffs/" stringByAppendingString:identifier]]);
            reply = done;
        };
        GAHandoffFailureObserved observed = ^(NSString *returnedIdentifier, NSDictionary *receipt) {
            ++presentations;
            assert([returnedIdentifier isEqual:identifier]);
            assert([[GAHandoffFailure fromReceipt:receipt].message isEqual:cause]);
        };
        [observation rememberRuntimeStop:identifier];
        [observation readFailureIfIdle:NO read:read observed:observed];
        assert(reads == 0); // Explicit new work owns the screen.
        [observation readFailureIfIdle:YES read:read observed:observed];
        assert(reads == 1 && presentations == 0);
        reply(@{@"status": @"failed", @"message": cause}, nil);
        assert(presentations == 1);
        [observation readFailureIfIdle:YES read:read observed:observed];
        assert(reads == 1); // One recovery read, no hidden polling or write.

        [observation rememberRuntimeStop:identifier];
        [observation readFailureIfIdle:YES read:read observed:observed];
        reply(@{@"status": @"needs_review"}, nil);
        assert(reads == 2 && presentations == 1); // Never reopen a document implicitly.
        [observation rememberRuntimeStop:identifier];
        [observation readFailureIfIdle:YES read:read observed:observed];
        reply(nil, @"Нет ответа");
        assert(reads == 3 && presentations == 1); // Missing response proves no outcome.

        [observation rememberRuntimeStop:identifier];
        [observation readFailureIfIdle:YES read:read observed:observed];
        [observation clear]; // Library navigation/new transfer invalidates old observations.
        reply(@{@"status": @"failed", @"message": cause}, nil);
        assert(reads == 4 && presentations == 1);
        [observation rememberRuntimeStop:nil];
        [observation readFailureIfIdle:YES read:read observed:observed];
        assert(reads == 4);
    }
}
