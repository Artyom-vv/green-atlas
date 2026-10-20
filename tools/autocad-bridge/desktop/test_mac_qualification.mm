#import "mac-qualification.h"
#include <cassert>
#include <sys/stat.h>
#include <unistd.h>

static NSString *newRoot(NSString *parent, NSString *phase = @"installed") {
    NSString *session = NSUUID.UUID.UUIDString;
    NSString *root = [parent stringByAppendingPathComponent:[@"GA-Mac-Qualification-" stringByAppendingString:session]];
    assert(mkdir(root.fileSystemRepresentation, 0700) == 0);
    NSDictionary *value = @{@"schema":@"green-atlas.macos-installer-qa-root/1", @"uid":@(getuid()),
        @"session_uuid":session, @"phase":phase, @"backup":NSNull.null};
    NSData *bytes = [NSJSONSerialization dataWithJSONObject:value options:0 error:nil];
    NSString *marker = [root stringByAppendingPathComponent:@".green-atlas-installer-qualification.json"];
    assert([bytes writeToFile:marker atomically:NO]);
    assert(chmod(marker.fileSystemRepresentation, 0600) == 0);
    return root;
}

static GADesktopQualification *parse(NSString *root, NSError **error) {
    NSString *profile = [root stringByAppendingPathComponent:@"RuntimeProfile"];
    NSString *receipt = [profile stringByAppendingPathComponent:@"desktop-qualification.json"];
    const char *args[] = {"Desktop", "--qualify-profile", profile.UTF8String, "--qualify-receipt", receipt.UTF8String};
    return GAParseDesktopQualification(5, args, error);
}

int main() {
    @autoreleasepool {
        char cwd[4096]; assert(getcwd(cwd, sizeof(cwd)));
        NSString *parent = [[NSString stringWithUTF8String:cwd] stringByAppendingPathComponent:
            [@".runtime/autocad-bridge/desktop-qualification-tests/" stringByAppendingString:NSUUID.UUID.UUIDString]];
        assert([NSFileManager.defaultManager createDirectoryAtPath:parent withIntermediateDirectories:YES
            attributes:@{NSFilePosixPermissions:@0700} error:nil]);
        const char *normal[] = {"Desktop", "/prepared/transfer.gatransfer"};
        NSError *error = nil;
        assert(!GAParseDesktopQualification(2, normal, &error) && !error);
        const char *bad[] = {"Desktop", "--qualify-profile"};
        assert(!GAParseDesktopQualification(2, bad, &error) && error);
        const char *misplaced[] = {"Desktop", "ticket", "--qualify-profile"};
        error = nil; assert(!GAParseDesktopQualification(3, misplaced, &error) && error);
        const char *relative[] = {"Desktop", "--qualify-profile", "relative/RuntimeProfile",
            "--qualify-receipt", "relative/RuntimeProfile/desktop-qualification.json"};
        error = nil; assert(!GAParseDesktopQualification(5, relative, &error) && error);

        for (NSString *phase in @[@"installed", @"updated", @"rolled_back"]) {
            NSString *root = newRoot(parent, phase); error = nil;
            GADesktopQualification *profile = parse(root, &error);
            assert(profile && !error);
            assert(!profile.finished && !profile.passed);
            NSDictionary *environment = [profile runtimeEnvironment];
            assert([environment[@"PATH"] isEqual:@"/usr/bin:/bin"] && !environment[@"HOME"]
                && !environment[@"CFFIXED_USER_HOME"] && !environment[@"PYTHONPATH"]);
            assert([environment[@"TMPDIR"] hasPrefix:profile.profile.path]);
            assert(!parse(root, &error) && error);
        }
        for (NSString *phase in @[@"initialized", @"uninstalled", @"purged"]) {
            NSString *root = newRoot(parent, phase); error = nil;
            assert(!parse(root, &error) && error);
            assert(![NSFileManager.defaultManager fileExistsAtPath:[root stringByAppendingPathComponent:@"RuntimeProfile"]]);
        }
        NSString *publicRoot = newRoot(parent);
        assert(chmod(publicRoot.fileSystemRepresentation, 0755) == 0); error = nil;
        assert(!parse(publicRoot, &error) && error);
        assert(chmod(publicRoot.fileSystemRepresentation, 0700) == 0);

        NSString *occupied = newRoot(parent);
        NSString *occupiedProfile = [occupied stringByAppendingPathComponent:@"RuntimeProfile"];
        assert(mkdir(occupiedProfile.fileSystemRepresentation, 0700) == 0);
        NSString *sentinel = [occupiedProfile stringByAppendingPathComponent:@"do-not-change"];
        assert([@"preserve" writeToFile:sentinel atomically:NO encoding:NSUTF8StringEncoding error:nil]);
        error = nil; assert(!parse(occupied, &error) && error);
        assert([[NSString stringWithContentsOfFile:sentinel encoding:NSUTF8StringEncoding error:nil] isEqual:@"preserve"]);

        NSString *linked = newRoot(parent);
        NSString *marker = [linked stringByAppendingPathComponent:@".green-atlas-installer-qualification.json"];
        NSString *saved = [linked stringByAppendingPathComponent:@"saved-marker"];
        assert([NSFileManager.defaultManager moveItemAtPath:marker toPath:saved error:nil]);
        assert(symlink(saved.fileSystemRepresentation, marker.fileSystemRepresentation) == 0);
        error = nil; assert(!parse(linked, &error) && error);

        NSString *hardlinked = newRoot(parent);
        NSString *hardMarker = [hardlinked stringByAppendingPathComponent:@".green-atlas-installer-qualification.json"];
        assert(link(hardMarker.fileSystemRepresentation, [hardlinked stringByAppendingPathComponent:@"second-link"].fileSystemRepresentation) == 0);
        error = nil; assert(!parse(hardlinked, &error) && error);

        NSString *target = newRoot(parent);
        NSString *alias = [parent stringByAppendingPathComponent:[@"GA-Mac-Qualification-" stringByAppendingString:NSUUID.UUID.UUIDString]];
        assert(symlink(target.fileSystemRepresentation, alias.fileSystemRepresentation) == 0);
        error = nil; assert(!parse(alias, &error) && error);

        NSString *accepted = newRoot(parent); error = nil;
        GADesktopQualification *profile = parse(accepted, &error); assert(profile && !error);
        NSMutableDictionary *proof = [@{@"document_complete":@YES, @"compiled_module_present":@YES,
            @"rendered_project_library":@YES, @"private_health":@YES, @"empty_projects":@YES,
            @"shutdown_202":@YES} mutableCopy];
        assert(GADesktopQualificationWebProof(proof));
        proof[@"private_health"] = @1; assert(!GADesktopQualificationWebProof(proof));
        proof[@"private_health"] = @YES;
        assert([profile finish:proof runtimePID:123 runtimeExit:0 error:nil]);
        NSDictionary *receipt = [NSJSONSerialization JSONObjectWithData:
            [NSData dataWithContentsOfURL:profile.receipt] options:0 error:nil];
        assert([receipt[@"passed"] isEqual:@YES] && [receipt[@"product_qualified"] isEqual:@NO]);
        assert(!receipt[@"session_token"] && [receipt[@"profile"] isEqual:profile.profile.path]);
        struct stat info; assert(lstat(profile.receipt.path.fileSystemRepresentation, &info) == 0);
        assert((info.st_mode & 0777) == 0600 && info.st_uid == getuid());
        assert([profile finish:@{} runtimePID:123 runtimeExit:9 error:@"Не заменять готовую квитанцию"]);
        GADesktopQualification *failed = parse(newRoot(parent), &error); assert(failed);
        assert(![failed finish:proof runtimePID:123 runtimeExit:9 error:nil]);
        NSDictionary *failureReceipt = [NSJSONSerialization JSONObjectWithData:
            [NSData dataWithContentsOfURL:failed.receipt] options:0 error:nil];
        assert([failureReceipt[@"passed"] isEqual:@NO] && [failureReceipt[@"runtime_exit_code"] isEqual:@9]);

        NSUserDefaults *defaults = NSUserDefaults.standardUserDefaults;
        NSDictionary *previousArguments = [defaults volatileDomainForName:NSArgumentDomain];
        [defaults setVolatileDomain:@{@"unrelated":@"preserve"} forName:NSArgumentDomain];
        GAConfigureDesktopQualificationPreferences();
        NSDictionary *arguments = [defaults volatileDomainForName:NSArgumentDomain];
        assert([arguments[@"unrelated"] isEqual:@"preserve"]);
        assert([arguments[@"NSQuitAlwaysKeepsWindows"] isEqual:@NO] && [arguments[@"ApplePersistenceIgnoreState"] isEqual:@YES]);
        [defaults setVolatileDomain:previousArguments ?: @{} forName:NSArgumentDomain];
        assert([NSFileManager.defaultManager removeItemAtPath:parent error:nil]);
        puts("Mac qualification guards passed");
    }
    return 0;
}
