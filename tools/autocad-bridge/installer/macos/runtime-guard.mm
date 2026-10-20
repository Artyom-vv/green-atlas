#import "runtime-guard.h"
#include <fcntl.h>
#include <libproc.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

static BOOL guardError(NSError **error) {
    if(error) *error=[NSError errorWithDomain:@"ru.green-atlas.installer-runtime" code:1
        userInfo:@{NSLocalizedDescriptionKey:@"Локальный сервис Green Atlas ещё работает или хранилище недоступно. Закройте приложение и повторите обновление. Файлы установки не изменены."}];
    return NO;
}
BOOL GAAcquireWorkspaceInstallLease(NSURL *workspace, int *descriptor, NSError **error) {
    *descriptor=-1;
    if(![workspace.path isEqual:workspace.URLByStandardizingPath.path]
        || ![workspace.path isEqual:workspace.URLByResolvingSymlinksInPath.path]) return guardError(error);
    struct stat directory;
    if(lstat(workspace.fileSystemRepresentation,&directory)!=0) return errno==ENOENT || guardError(error);
    if(!S_ISDIR(directory.st_mode) || directory.st_uid!=getuid()) return guardError(error);
    NSURL *path=[workspace URLByAppendingPathComponent:@".desktop-runtime.lock"];
    int fd=open(path.fileSystemRepresentation,O_RDWR|O_CREAT|O_NOFOLLOW|O_CLOEXEC,0600);
    struct stat s;
    if(fd<0) return guardError(error);
    if(fstat(fd,&s)!=0 || !S_ISREG(s.st_mode) || s.st_uid!=getuid() || flock(fd,LOCK_EX|LOCK_NB)!=0) {
        close(fd); return guardError(error);
    }
    *descriptor=fd; return YES;
}
BOOL GAExactExecutableIsRunning(NSURL *executable) {
    // Только executable paths: аргументы, окружение и содержимое профиля не читаются.
    int bytes=proc_listpids(PROC_ALL_PIDS,0,nullptr,0);
    if(bytes<=0) return YES;
    std::vector<pid_t> pids(static_cast<size_t>(bytes)/sizeof(pid_t)+64);
    bytes=proc_listpids(PROC_ALL_PIDS,0,pids.data(),static_cast<int>(pids.size()*sizeof(pid_t)));
    if(bytes<=0 || static_cast<size_t>(bytes)>=pids.size()*sizeof(pid_t)) return YES;
    NSString *expected=executable.URLByResolvingSymlinksInPath.path;
    struct stat expectedInfo; BOOL hasIdentity=stat(executable.fileSystemRepresentation,&expectedInfo)==0;
    for(size_t i=0;i<static_cast<size_t>(bytes)/sizeof(pid_t);++i) {
        if(pids[i]<=0) continue;
        char path[PROC_PIDPATHINFO_MAXSIZE]; int length=proc_pidpath(pids[i],path,sizeof(path));
        if(length>0) {
            NSURL *observed=[NSURL fileURLWithPath:[NSString stringWithUTF8String:path]];
            if([observed.URLByResolvingSymlinksInPath.path isEqual:expected]) return YES;
            struct stat actual;
            if(hasIdentity && stat(path,&actual)==0 && actual.st_dev==expectedInfo.st_dev && actual.st_ino==expectedInfo.st_ino) return YES;
        }
    }
    return NO;
}
