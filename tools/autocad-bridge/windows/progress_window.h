#pragma once

#include <windows.h>
#include <cstddef>
#include <string>

namespace gaDelivery::progressWindow {

void stage(HWND owner, const std::wstring& message);
void progress(HWND owner, const std::wstring& message, std::size_t processed);
void close();

}
