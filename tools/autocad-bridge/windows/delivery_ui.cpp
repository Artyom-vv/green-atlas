#include "../native/delivery_ui.h"
#include "../native/file_io.h"
#include "../native/filesystem_utf8.h"
#include "progress_window.h"
#include "adslib.h"
#include "aced.h"
#include "acedads.h"
#include <windows.h>
#include <commctrl.h>
#include <memory>
#include <chrono>

namespace gaDelivery {
namespace {
HWND owner=nullptr;
HMENU root=nullptr, popup=nullptr;
void (*openCommand)()=nullptr;
std::string progressPhase;
auto progressUpdated=std::chrono::steady_clock::time_point{};
class GreenAtlasContextMenu final : public AcEdUIContext {
public:
    explicit GreenAtlasContextMenu(void (*command)()) : command_(command) {
        menu_=CreatePopupMenu();
        if(menu_ && !AppendMenuW(menu_,MF_STRING,1,L"Открыть в Green Atlas…")) {
            DestroyMenu(menu_); menu_=nullptr;
        }
    }
    ~GreenAtlasContextMenu() override { if(menu_) DestroyMenu(menu_); }
    bool ready() const { return menu_!=nullptr; }
    void* getMenuContext(const AcRxClass*,const AcDbObjectIdArray&) override {
        return &menu_;
    }
    void onCommand(Adesk::UInt32 id) override {
        if(id==1 && command_) command_();
    }
private:
    HMENU menu_=nullptr;
    void (*command_)()=nullptr;
};
std::unique_ptr<GreenAtlasContextMenu> contextMenu;
bool contextMenuRegistered=false;
std::wstring wide(const std::string& text) { return ga::bridge::nativePath(text).wstring(); }
LRESULT CALLBACK menuMessage(HWND window,UINT message,WPARAM w,LPARAM l,UINT_PTR id,DWORD_PTR) {
    if(message==WM_MENUCOMMAND && reinterpret_cast<HMENU>(l)==popup && w==0 && openCommand) {
        openCommand(); return 0;
    }
    if(message==WM_NCDESTROY) RemoveWindowSubclass(window,menuMessage,id);
    return DefSubclassProc(window,message,w,l);
}
std::wstring notices(const std::vector<SourceNotice>& items) {
    std::string text;
    for(const auto& item:items) text+=item.label+": "+item.value+"\n\n";
    return wide(text);
}
}
void installContextMenu(void (*command)(),const void* appId) {
    if(contextMenuRegistered || !appId) return;
    auto menu=std::make_unique<GreenAtlasContextMenu>(command);
    if(!menu->ready() || !acedAddDefaultContextMenu(menu.get(),appId,L"Green Atlas")) return;
    contextMenu=std::move(menu);
    contextMenuRegistered=true;
}
void removeContextMenu() {
    if(contextMenuRegistered) acedRemoveDefaultContextMenu(contextMenu.get());
    contextMenuRegistered=false;
    contextMenu.reset();
}
void installMenu(void (*command)()) {
    const HWND frame=adsw_acadMainWnd();
    if(!frame) return;
    const HMENU current=GetMenu(frame);
    if(!current) return;
    if(frame==owner && current==root && IsMenu(popup)) {
        for(int i=0;i<GetMenuItemCount(root);++i) if(GetSubMenu(root,i)==popup) return;
    }
    removeMenu();
    owner=frame; root=current; openCommand=command;
    popup=CreatePopupMenu();
    MENUINFO info{sizeof(info)}; info.fMask=MIM_STYLE; info.dwStyle=MNS_NOTIFYBYPOS;
    SetMenuInfo(popup,&info);
    AppendMenuW(popup,MF_STRING,1,L"Открыть в Green Atlas…");
    AppendMenuW(root,MF_POPUP,reinterpret_cast<UINT_PTR>(popup),L"Green Atlas");
    SetWindowSubclass(owner,menuMessage,reinterpret_cast<UINT_PTR>(&installMenu),0);
    DrawMenuBar(owner);
}
void removeMenu() {
    if(IsMenu(root)) for(int i=0;i<GetMenuItemCount(root);++i)
        if(GetSubMenu(root,i)==popup) { RemoveMenu(root,i,MF_BYPOSITION); break; }
    if(IsMenu(popup)) DestroyMenu(popup);
    if(IsWindow(owner)) {
        RemoveWindowSubclass(owner,menuMessage,reinterpret_cast<UINT_PTR>(&installMenu));
        DrawMenuBar(owner);
    }
    owner=nullptr; root=nullptr; popup=nullptr; openCommand=nullptr;
    endStage();
}
bool confirmPreparation(bool modified) {
    const auto message=notices({{"Источник",modified?"С несохранёнными изменениями":"Открытый чертёж"},
        {"Подосновы","Загруженные в AutoCAD"},{"Исходный файл","Без изменений"}});
    return MessageBoxW(adsw_acadMainWnd(),message.c_str(),L"Открыть в Green Atlas",
        MB_OKCANCEL|MB_ICONINFORMATION)==IDOK;
}
bool confirmLivePartial(const std::vector<SourceNotice>& issues) {
    const auto message=notices(issues)+L"Открыть доступные данные? Замечания сохранятся в проекте.";
    return MessageBoxW(adsw_acadMainWnd(),message.c_str(),L"Есть замечания к исходнику",
        MB_OKCANCEL|MB_ICONWARNING)==IDOK;
}
bool confirmPartial(const std::vector<std::string>& issues) {
    std::wstring text;
    for(const auto& issue:issues) text+=wide(issue)+L"\n\n";
    text+=L"Продолжить с доступной геометрией? Замечания сохранятся вместе с копией.";
    return MessageBoxW(adsw_acadMainWnd(),text.c_str(),L"Не все данные удалось подготовить",
        MB_OKCANCEL|MB_ICONWARNING)==IDOK;
}
ReferenceRecovery confirmReferenceRecovery(size_t lost,size_t available) {
    const auto text=L"Ссылок: "+std::to_wstring(lost)+L". Доступны для восстановления: "
        +std::to_wstring(available)+L".\nВосстановить доступные ссылки в копии?\n"
        L"Да — восстановить. Нет — продолжить без них. Отмена — вернуться в AutoCAD.";
    const auto result=MessageBoxW(adsw_acadMainWnd(),text.c_str(),L"Подосновы копии",
        MB_YESNOCANCEL|MB_ICONQUESTION);
    return result==IDYES?ReferenceRecovery::RestoreAvailable:
        result==IDNO?ReferenceRecovery::Skip:ReferenceRecovery::Cancel;
}
void error(const std::string& message) {
    MessageBoxW(adsw_acadMainWnd(),wide(message).c_str(),L"Передача не начата",MB_OK|MB_ICONERROR);
}
void stage(const std::string& message) {
    progressWindow::stage(adsw_acadMainWnd(),wide(message));
}
void progress(const std::string& phase, std::size_t processed) {
    const auto now = std::chrono::steady_clock::now();
    if(phase==progressPhase && now-progressUpdated<std::chrono::milliseconds(100)) return;
    progressPhase=phase;
    progressUpdated=now;
    const char* label = phase == "collecting" ? "Читаем объекты"
        : phase == "inventory" ? "Получаем свойства объектов"
        : phase == "serializing_coverage" ? "Сохраняем перечень объектов"
        : phase == "serializing_paths" ? "Сохраняем геометрию отображения"
        : phase == "archive_instances" ? "Сохраняем свойства экземпляров"
        : phase == "archive_copy" ? "Создаём копии чертежа и подоснов"
        : phase == "archive_files" ? "Сохраняем чертёж и подосновы"
        : "Сохраняем снимок чертежа";
    progressWindow::progress(adsw_acadMainWnd(),wide(label),processed);
}
void endStage() {
    progressWindow::close();
    progressPhase.clear();
    progressUpdated={};
}
}
