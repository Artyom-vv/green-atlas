#include "local_platform.h"
#include <cassert>
#include <filesystem>
#include <fstream>
#include <iostream>
int main(int argc,char** argv) {
    assert(argc==2);namespace fs=std::filesystem;
    const auto root=fs::absolute(argv[1]);fs::create_directory(root);
    fs::permissions(root,fs::perms::owner_all,fs::perm_options::replace);
    assert(ga::platform::ensurePrivateDirectory(root.string()));
    const auto token=ga::platform::randomToken();
    assert(token.size()==32 && token.find_first_not_of("0123456789abcdef")==std::string::npos);
    assert(token!=ga::platform::randomToken());
    std::ofstream(root/"request.txt")<<"request";
    assert(ga::platform::renameFile((root/"request.txt").string(),(root/"processing.txt").string()));
    assert(ga::platform::directoryNames(root.string()).size()==1);
    assert(ga::platform::readBoundedFile((root/"processing.txt").string(),7)=="request");
    bool oversized=false;
    try { ga::platform::readBoundedFile((root/"processing.txt").string(),6); }
    catch(const std::exception&) { oversized=true; }
    assert(oversized);
    fs::create_directory_symlink(root,root.parent_path()/"link");
    assert(!ga::platform::directPath((root.parent_path()/"link/processing.txt").string()));
    fs::permissions(root,fs::perms::others_read,fs::perm_options::add);
    assert(!ga::platform::ensurePrivateDirectory(root.string()));
    std::cout<<"local-platform-ok\n";
}
