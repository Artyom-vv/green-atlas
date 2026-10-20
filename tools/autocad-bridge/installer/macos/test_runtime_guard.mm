#import "runtime-guard.h"
#include <cassert>
#include <unistd.h>

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSURL *root=[[NSURL fileURLWithPath:NSTemporaryDirectory()].URLByResolvingSymlinksInPath
            URLByAppendingPathComponent:[@"ga-runtime-install-guard-" stringByAppendingString:NSUUID.UUID.UUIDString]];
        NSFileManager *fm=NSFileManager.defaultManager;
        assert([fm createDirectoryAtURL:root withIntermediateDirectories:YES attributes:nil error:nil]);
        NSURL *workspace=[root URLByAppendingPathComponent:@"Workspace"]; int first=-1,second=-1;
        // Отсутствующий профиль не создаётся ради установки.
        assert(GAAcquireWorkspaceInstallLease(workspace,&first,nil) && first==-1);
        assert(![fm fileExistsAtPath:workspace.path]);
        assert([fm createDirectoryAtURL:workspace withIntermediateDirectories:YES attributes:nil error:nil]);
        assert(GAAcquireWorkspaceInstallLease(workspace,&first,nil) && first>=0);
        assert(!GAAcquireWorkspaceInstallLease(workspace,&second,nil) && second==-1);
        close(first); assert(GAAcquireWorkspaceInstallLease(workspace,&second,nil) && second>=0); close(second);
        NSURL *lock=[workspace URLByAppendingPathComponent:@".desktop-runtime.lock"];
        NSURL *external=[root URLByAppendingPathComponent:@"user-data"];
        NSData *original=[@"unchanged" dataUsingEncoding:NSUTF8StringEncoding]; assert([original writeToURL:external options:0 error:nil]);
        assert([fm removeItemAtURL:lock error:nil]);
        assert([fm createSymbolicLinkAtURL:lock withDestinationURL:external error:nil]);
        assert(!GAAcquireWorkspaceInstallLease(workspace,&first,nil));
        assert([[NSData dataWithContentsOfURL:external] isEqual:original]);
        NSURL *link=[root URLByAppendingPathComponent:@"linked-profile"];
        assert([fm createSymbolicLinkAtURL:link withDestinationURL:workspace error:nil]);
        assert(!GAAcquireWorkspaceInstallLease(link,&first,nil));
        assert(GAExactExecutableIsRunning([NSURL fileURLWithPath:[NSString stringWithUTF8String:argv[0]]]));
        assert(!GAExactExecutableIsRunning([root URLByAppendingPathComponent:@"not-a-runtime"]));
        assert([fm removeItemAtURL:root error:nil]);
        printf("{\"status\":\"PASS\",\"scenarios\":5,\"real_workspace_modified\":false}\n");
    }
}
