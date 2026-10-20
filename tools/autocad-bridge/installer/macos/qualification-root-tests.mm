#import "qualification-root.h"
#include <sys/stat.h>
#include <unistd.h>

// Контрактные проверки не копируют payload и не обращаются к рабочему профилю.
int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc != 2) return 2;
        NSFileManager *manager = NSFileManager.defaultManager;
        NSURL *parent = [NSURL fileURLWithPath:@(argv[1])].URLByResolvingSymlinksInPath;
        NSURL *base = [parent URLByAppendingPathComponent:[@"native-qualification-tests-" stringByAppendingString:NSUUID.UUID.UUIDString]];
        NSError *error = nil;
        if (![manager createDirectoryAtURL:base withIntermediateDirectories:NO attributes:@{NSFilePosixPermissions:@0700} error:&error]) return 2;
        int checks = 0;
        int failures = 0;
        auto check = [&](BOOL value, const char *name) {
            checks++;
            if (!value) { failures++; fprintf(stderr, "FAIL %s\n", name); }
        };
        NSURL *root = [base URLByAppendingPathComponent:[@"GA-Mac-Qualification-" stringByAppendingString:NSUUID.UUID.UUIDString]];
        check(GACreateQualificationRoot(root, &error), "fresh-root");
        NSDictionary *original = GAReadQualificationRoot(root, &error);
        check(original != nil && [original[@"phase"] isEqual:@"init"], "read-owned-marker");
        check(!GACreateQualificationRoot(root, &error), "existing-root-refused");
        check(!GACreateQualificationRoot([base URLByAppendingPathComponent:@"Workspace"], &error), "ordinary-name-refused");
        check(!GACreateQualificationRoot([base URLByAppendingPathComponent:@"GA-Mac-Qualification-invalid"], &error), "invalid-uuid-refused");
        NSURL *inside = [root URLByAppendingPathComponent:@"Applications"];
        check(GAQualificationChild(root, inside), "fixed-child");
        check(!GAQualificationChild(root, root) && !GAQualificationChild(root, base), "root-and-parent-not-children");
        NSURL *link = [root URLByAppendingPathComponent:@"linked"];
        [manager createSymbolicLinkAtURL:link withDestinationURL:base error:&error];
        check(!GAQualificationChild(root, [link URLByAppendingPathComponent:@"Apps"]), "linked-child-refused");
        NSURL *linkedParent = [base URLByAppendingPathComponent:@"linked-parent"];
        [manager createSymbolicLinkAtURL:linkedParent withDestinationURL:base error:&error];
        check(!GACreateQualificationRoot([linkedParent URLByAppendingPathComponent:root.lastPathComponent], &error), "linked-parent-refused");
        chmod(root.fileSystemRepresentation, 0755);
        check(GAReadQualificationRoot(root, &error) == nil, "public-root-refused");
        chmod(root.fileSystemRepresentation, 0700);
        NSURL *marker = [root URLByAppendingPathComponent:@".green-atlas-installer-qualification.json"];
        chmod(marker.fileSystemRepresentation, 0644);
        check(GAReadQualificationRoot(root, &error) == nil, "public-marker-refused");
        chmod(marker.fileSystemRepresentation, 0600);
        NSURL *hardlink = [root URLByAppendingPathComponent:@"marker-hardlink"];
        check(::link(marker.fileSystemRepresentation, hardlink.fileSystemRepresentation) == 0
            && GAReadQualificationRoot(root, &error) == nil, "hardlinked-marker-refused");
        [manager removeItemAtURL:hardlink error:nil];
        for (NSString *field in @[@"uid", @"session_uuid", @"schema", @"phase", @"backup"]) {
            NSMutableDictionary *changed = [original mutableCopy];
            changed[field] = [field isEqual:@"backup"] ? @42 : @"invalid";
            check(GAWriteQualificationRoot(root, changed, &error)
                && GAReadQualificationRoot(root, &error) == nil, field.UTF8String);
        }
        check(GAWriteQualificationRoot(root, original, &error), "restore-marker");
        [manager removeItemAtURL:marker error:&error];
        [manager createSymbolicLinkAtURL:marker withDestinationURL:root error:&error];
        check(GAReadQualificationRoot(root, &error) == nil, "linked-marker-refused");
        [manager removeItemAtURL:base error:nil];
        printf("{\"schema\":\"green-atlas.macos-installer-root-tests/1\",\"checks\":%d,\"failures\":%d,\"passed\":%s}\n", checks, failures, failures ? "false" : "true");
        return failures ? 1 : 0;
    }
}
