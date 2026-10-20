#include "progress_window.h"
#include <memory>

namespace gaDelivery::progressWindow {
namespace {
constexpr wchar_t kClassName[] = L"GreenAtlasTransferProgress";
constexpr UINT kUpdate = WM_APP + 0x341;
constexpr UINT kStop = WM_APP + 0x342;
constexpr UINT_PTR kAnimation = 1;

struct Update {
    std::wstring message;
    std::size_t processed = 0;
    bool showCount = false;
};
struct ThreadStart {
    HWND owner = nullptr;
    HANDLE ready = nullptr;
};
struct View {
    std::wstring message = L"Подготавливаем чертёж";
    std::wstring detail;
    UINT dpi = 96;
    int animation = 0;
    bool reverse = false;
    HFONT title = nullptr;
    HFONT body = nullptr;
    HFONT smallFont = nullptr;
    ~View() {
        if(title) DeleteObject(title);
        if(body) DeleteObject(body);
        if(smallFont) DeleteObject(smallFont);
    }
};

// GAOPEN runs synchronously on AutoCAD's editor thread. This window owns its
// own message loop so its animation and dragging never pump CAD messages.
HANDLE worker = nullptr;
HWND window = nullptr;

int scaled(int value, UINT dpi) { return MulDiv(value, static_cast<int>(dpi), 96); }
COLORREF rgb(int red, int green, int blue) { return RGB(red, green, blue); }
HFONT font(int points, int weight, UINT dpi) {
    return CreateFontW(-MulDiv(points, static_cast<int>(dpi), 72), 0, 0, 0,
        weight, FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS,
        CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, DEFAULT_PITCH | FF_SWISS,
        L"Segoe UI");
}
void rectangle(HDC dc, RECT area, COLORREF color) {
    const auto brush = CreateSolidBrush(color);
    FillRect(dc, &area, brush);
    DeleteObject(brush);
}
void roundRect(HDC dc, RECT area, int radius, COLORREF color) {
    const auto brush = CreateSolidBrush(color);
    const auto oldBrush = SelectObject(dc, brush);
    const auto oldPen = SelectObject(dc, GetStockObject(NULL_PEN));
    RoundRect(dc, area.left, area.top, area.right, area.bottom, radius, radius);
    SelectObject(dc, oldPen);
    SelectObject(dc, oldBrush);
    DeleteObject(brush);
}
void drawText(HDC dc, const wchar_t* value, RECT area, HFONT fontValue,
              COLORREF color, UINT flags) {
    const auto old = SelectObject(dc, fontValue);
    SetBkMode(dc, TRANSPARENT);
    SetTextColor(dc, color);
    DrawTextW(dc, value, -1, &area, flags | DT_NOPREFIX);
    SelectObject(dc, old);
}
void paint(HWND hwnd, View& view) {
    PAINTSTRUCT paintInfo{};
    const auto target = BeginPaint(hwnd, &paintInfo);
    RECT bounds{};
    GetClientRect(hwnd, &bounds);
    const int width = bounds.right, height = bounds.bottom;
    const auto memory = CreateCompatibleDC(target);
    const auto bitmap = CreateCompatibleBitmap(target, width, height);
    const auto oldBitmap = SelectObject(memory, bitmap);
    const auto s = [&view](int value) { return scaled(value, view.dpi); };
    rectangle(memory, bounds, rgb(250, 252, 250));
    rectangle(memory, {0, 0, s(6), height}, rgb(29, 105, 76));
    roundRect(memory, {s(26), s(22), s(74), s(70)}, s(13), rgb(29, 105, 76));
    drawText(memory, L"GA", {s(26), s(31), s(74), s(61)}, view.body,
        RGB(255, 255, 255), DT_CENTER | DT_SINGLELINE | DT_VCENTER);
    drawText(memory, L"Передаём проект", {s(91), s(22), width-s(25), s(53)},
        view.title, rgb(26, 44, 36), DT_LEFT | DT_SINGLELINE | DT_VCENTER);
    drawText(memory, L"GREEN ATLAS / AUTOCAD", {s(91), s(53), width-s(25), s(75)},
        view.smallFont, rgb(95, 111, 102), DT_LEFT | DT_SINGLELINE | DT_VCENTER);
    rectangle(memory, {s(26), s(88), width-s(26), s(89)}, rgb(221, 230, 223));
    drawText(memory, view.message.c_str(), {s(26), s(108), width-s(26), s(154)},
        view.body, rgb(29, 50, 39), DT_LEFT | DT_WORDBREAK);
    const wchar_t* detail = view.detail.empty()
        ? L"Исходный чертёж остаётся без изменений" : view.detail.c_str();
    drawText(memory, detail, {s(26), s(153), width-s(26), s(174)},
        view.smallFont, rgb(96, 110, 101), DT_LEFT | DT_SINGLELINE | DT_END_ELLIPSIS);
    const RECT track{s(26), s(191), width-s(26), s(197)};
    roundRect(memory, track, s(6), rgb(223, 232, 225));
    const int trackWidth = track.right-track.left;
    const int segment = trackWidth*32/100;
    const int offset = (trackWidth-segment)*view.animation/100;
    roundRect(memory,
        {track.left+offset, track.top, track.left+offset+segment, track.bottom},
        s(6), rgb(29, 125, 87));
    BitBlt(target, 0, 0, width, height, memory, 0, 0, SRCCOPY);
    SelectObject(memory, oldBitmap);
    DeleteObject(bitmap);
    DeleteDC(memory);
    EndPaint(hwnd, &paintInfo);
}
LRESULT CALLBACK windowProcedure(HWND hwnd, UINT message, WPARAM wparam, LPARAM lparam) {
    auto* view = reinterpret_cast<View*>(GetWindowLongPtrW(hwnd, GWLP_USERDATA));
    switch(message) {
    case WM_NCCREATE: {
        auto* created = new View;
        created->dpi = GetDpiForWindow(hwnd);
        if(!created->dpi) created->dpi = 96;
        created->title = font(18, FW_SEMIBOLD, created->dpi);
        created->body = font(12, FW_MEDIUM, created->dpi);
        created->smallFont = font(9, FW_NORMAL, created->dpi);
        SetWindowLongPtrW(hwnd, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(created));
        return TRUE;
    }
    case WM_NCHITTEST: {
        const auto result = DefWindowProcW(hwnd, message, wparam, lparam);
        return result == HTCLIENT ? HTCAPTION : result;
    }
    case WM_ERASEBKGND: return 1;
    case WM_PAINT:
        if(view) paint(hwnd, *view);
        return 0;
    case WM_TIMER:
        if(view && wparam == kAnimation) {
            view->animation += view->reverse ? -3 : 3;
            if(view->animation >= 100) { view->animation = 100; view->reverse = true; }
            if(view->animation <= 0) { view->animation = 0; view->reverse = false; }
            InvalidateRect(hwnd, nullptr, FALSE);
        }
        return 0;
    case kUpdate: {
        std::unique_ptr<Update> update(reinterpret_cast<Update*>(lparam));
        if(view && update) {
            view->message = std::move(update->message);
            view->detail = update->showCount
                ? L"Обработано объектов: " + std::to_wstring(update->processed) : L"";
            InvalidateRect(hwnd, nullptr, FALSE);
            UpdateWindow(hwnd);
        }
        return 0;
    }
    case kStop:
        DestroyWindow(hwnd);
        return 0;
    case WM_CLOSE: return 0; // The CAD operation controls this window's lifetime.
    case WM_DESTROY:
        KillTimer(hwnd, kAnimation);
        PostQuitMessage(0);
        return 0;
    case WM_NCDESTROY:
        delete view;
        SetWindowLongPtrW(hwnd, GWLP_USERDATA, 0);
        break;
    }
    return DefWindowProcW(hwnd, message, wparam, lparam);
}
DWORD WINAPI windowThread(void* argument) {
    std::unique_ptr<ThreadStart> start(static_cast<ThreadStart*>(argument));
    WNDCLASSEXW klass{};
    klass.cbSize = sizeof(klass);
    klass.lpfnWndProc = windowProcedure;
    if(!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
        GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
        reinterpret_cast<LPCWSTR>(&windowProcedure), &klass.hInstance)) {
        SetEvent(start->ready);
        return 1;
    }
    klass.hCursor = LoadCursorW(nullptr, MAKEINTRESOURCEW(32512));
    klass.lpszClassName = kClassName;
    if(!RegisterClassExW(&klass)) {
        SetEvent(start->ready);
        return 1;
    }
    const UINT dpi = start->owner ? GetDpiForWindow(start->owner) : 96;
    const int width = scaled(580, dpi), height = scaled(220, dpi);
    RECT ownerBounds{};
    if(start->owner) GetWindowRect(start->owner, &ownerBounds);
    const int x = start->owner
        ? ownerBounds.left + (ownerBounds.right-ownerBounds.left-width)/2 : CW_USEDEFAULT;
    const int y = start->owner
        ? ownerBounds.top + (ownerBounds.bottom-ownerBounds.top-height)/2 : CW_USEDEFAULT;
    const HWND created = CreateWindowExW(WS_EX_TOOLWINDOW, kClassName,
        L"Green Atlas / передача чертежа", WS_POPUP | WS_BORDER,
        x, y, width, height, start->owner, nullptr, klass.hInstance, nullptr);
    window = created;
    SetEvent(start->ready);
    if(!created) {
        UnregisterClassW(kClassName, klass.hInstance);
        return 1;
    }
    SetTimer(created, kAnimation, 40, nullptr);
    ShowWindow(created, SW_SHOWNOACTIVATE);
    UpdateWindow(created);
    MSG message{};
    while(GetMessageW(&message, nullptr, 0, 0) > 0) {
        TranslateMessage(&message);
        DispatchMessageW(&message);
    }
    UnregisterClassW(kClassName, klass.hInstance);
    return 0;
}
void ensureWindow(HWND owner) {
    if(worker) return;
    HANDLE ready = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if(!ready) return;
    auto* start = new ThreadStart{owner, ready};
    worker = CreateThread(nullptr, 0, windowThread, start, 0, nullptr);
    if(!worker) {
        delete start;
        CloseHandle(ready);
        return;
    }
    WaitForSingleObject(ready, INFINITE);
    CloseHandle(ready);
}
void send(HWND owner, const std::wstring& message, std::size_t processed, bool showCount) {
    ensureWindow(owner);
    if(!window) return;
    auto* update = new Update{message, processed, showCount};
    if(!PostMessageW(window, kUpdate, 0, reinterpret_cast<LPARAM>(update))) delete update;
}
}

void stage(HWND owner, const std::wstring& message) { send(owner, message, 0, false); }
void progress(HWND owner, const std::wstring& message, std::size_t processed) {
    send(owner, message, processed, true);
}
void close() {
    if(!worker) return;
    if(window) PostMessageW(window, kStop, 0, 0);
    WaitForSingleObject(worker, INFINITE);
    CloseHandle(worker);
    worker = nullptr;
    window = nullptr;
}
}
