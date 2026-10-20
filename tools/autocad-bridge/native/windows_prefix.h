#pragma once

#ifndef NOMINMAX
#define NOMINMAX
#endif

#include <windows.h>
#include <tchar.h>
#include "adesk.h"

#ifdef _WIN32
// SDK использует CPoint только в объявлениях core_rxmfcapi.h; определение MFC не нужно.
class CPoint;
#endif
