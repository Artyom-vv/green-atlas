#include "installed_module.h"
#include <cassert>
#include <filesystem>
#include <fstream>
#include <iostream>

int main(int argc, char** argv) {
    assert(argc == 2);
    namespace fs = std::filesystem;
    const fs::path root = fs::u8path(argv[1]);
    const auto installed = root / L"GreenAtlas.bundle" / L"Contents" / L"Windows"
        / L"GreenAtlasBridge.arx";
    const auto copy = root / L"AutoCAD cache" / L"GREENATLASBRIDGE.ARX";
    fs::create_directories(installed.parent_path());
    fs::create_directories(copy.parent_path());
    {
        std::ofstream output(installed, std::ios::binary);
        output << "current bridge";
    }
    fs::copy_file(installed, copy, fs::copy_options::overwrite_existing);
    assert(gaDelivery::matchesInstalledModule(installed, installed));
    assert(gaDelivery::matchesInstalledModule(
        root / L"greenatlas.bundle" / L"contents" / L"windows"
            / L"greenatlasbridge.arx", installed));
    assert(gaDelivery::matchesInstalledModule(copy, installed));
    {
        std::ofstream output(copy, std::ios::binary | std::ios::trunc);
        output << "altered bridge";
    }
    assert(!gaDelivery::matchesInstalledModule(copy, installed));
    assert(!gaDelivery::matchesInstalledModule(root / L"missing.arx", installed));
    std::cout << "installed-module-ok\n";
}
