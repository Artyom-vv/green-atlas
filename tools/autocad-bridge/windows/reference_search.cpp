#include "../native/reference_search.h"
#include "../native/file_io.h"
#include "../native/filesystem_utf8.h"
#include "../native/local_platform.h"
#include <windows.h>
#include <shlobj.h>
#include <algorithm>
#include <map>
#include <stdexcept>

namespace gaDelivery {
namespace {
namespace fs=std::filesystem;
using ga::bridge::nativePath;
using ga::bridge::utf8Path;
std::wstring canonical(const std::string& text) {
    const auto value=nativePath(text).wstring();
    if(value.empty()) return {};
    const int size=NormalizeString(NormalizationC,value.data(),static_cast<int>(value.size()),nullptr,0);
    if(size<=0) throw std::runtime_error("Не удалось проверить имя файла.");
    std::wstring normalized(size,L'\0');
    const int actual=NormalizeString(NormalizationC,value.data(),static_cast<int>(value.size()),normalized.data(),size);
    if(actual<=0) throw std::runtime_error("Не удалось проверить имя файла.");
    normalized.resize(actual);
    std::wstring result(normalized.size(),L'\0');
    if(!LCMapStringEx(LOCALE_NAME_INVARIANT,LCMAP_LOWERCASE,normalized.data(),actual,
        result.data(),actual,nullptr,nullptr,0)) throw std::runtime_error("Не удалось сравнить имена файлов.");
    return result;
}
}
std::vector<ReferencePathMatch> findReferencePaths(const std::string& rootPath,
    const std::vector<ReferencePathRequest>& requests) {
    const auto root=nativePath(rootPath);
    if(!ga::platform::directPath(rootPath) || !fs::is_directory(root))
        throw std::runtime_error("Папка комплекта недоступна или содержит ссылку.");
    std::map<std::wstring,std::vector<std::string>> byName;
    std::size_t count=0;
    for(fs::recursive_directory_iterator it(root),end;it!=end;++it) {
        if(++count>200000) throw std::runtime_error("Папка слишком большая. Выберите папку нужного чертежа.");
        const auto attributes=GetFileAttributesW(it->path().c_str());
        if(attributes==INVALID_FILE_ATTRIBUTES) throw std::runtime_error("Не все файлы доступны для поиска.");
        if(attributes&FILE_ATTRIBUTE_REPARSE_POINT) { it.disable_recursion_pending(); continue; }
        const auto extension=canonical(utf8Path(it->path().extension()));
        if(!it->is_regular_file() || (extension!=L".dwg" && extension!=L".dxf")) continue;
        byName[canonical(utf8Path(it->path().filename()))].push_back(utf8Path(it->path()));
    }
    std::map<std::string,std::string> hashes;
    std::vector<ReferencePathMatch> result;
    for(const auto& request:requests) {
        std::string stored=request.storedPath;
        std::replace(stored.begin(),stored.end(),'\\','/');
        while(stored.rfind("./",0)==0) stored.erase(0,2);
        const auto authored=nativePath(stored);
        ReferencePathMatch match{request.name,{},"missing",byName[canonical(utf8Path(authored.filename()))]};
        std::sort(match.candidates.begin(),match.candidates.end());
        bool relative=!authored.is_absolute() && stored.find(':')==std::string::npos
            && stored.find('/')!=std::string::npos;
        for(const auto& part:authored) if(part==L"..") relative=false;
        std::vector<std::string> suffix;
        const auto expected=canonical("/"+stored);
        for(const auto& path:match.candidates) {
            auto portable=path; std::replace(portable.begin(),portable.end(),'\\','/');
            const auto value=canonical(portable);
            if(relative && value.size()>=expected.size()
                && value.compare(value.size()-expected.size(),expected.size(),expected)==0) suffix.push_back(path);
        }
        const auto& pool=suffix.empty()?match.candidates:suffix;
        if(pool.size()==1) { match.path=pool.front(); match.status=suffix.empty()?"unique_name":"exact_suffix"; }
        else if(pool.size()>1) {
            std::string expectedHash; bool identical=true;
            for(const auto& path:pool) {
                if(!hashes.count(path)) hashes[path]=ga::bridge::sha256File(path);
                const auto& hash=hashes[path];
                if(hash.empty() || (!expectedHash.empty() && hash!=expectedHash)) identical=false;
                expectedHash=hash;
            }
            match.status=identical?"identical_copies":"ambiguous";
            if(identical) match.path=pool.front();
        }
        result.push_back(std::move(match));
    }
    return result;
}
bool chooseReferencePaths(const std::vector<ReferencePathRequest>& requests,
                          std::vector<ReferencePathMatch>& chosen) {
    if(requests.empty()) return true;
    const auto answer=MessageBoxW(GetActiveWindow(),L"Выбрать папку комплекта для поиска подоснов?\n"
        L"Да — выбрать папку. Нет — продолжить без них. Отмена — вернуться в AutoCAD.",
        L"Поиск подоснов",MB_YESNOCANCEL|MB_ICONQUESTION);
    if(answer==IDNO) return true;
    if(answer!=IDYES) return false;
    BROWSEINFOW options{}; options.hwndOwner=GetActiveWindow();
    options.lpszTitle=L"Папка комплекта чертежей"; options.ulFlags=BIF_RETURNONLYFSDIRS|BIF_NEWDIALOGSTYLE;
    PIDLIST_ABSOLUTE selected=SHBrowseForFolderW(&options);
    if(!selected) return false;
    wchar_t folder[32768];
    const bool valid=SHGetPathFromIDListEx(selected,folder,32768,GPFIDL_DEFAULT);
    CoTaskMemFree(selected);
    if(!valid) return false;
    const auto found=findReferencePaths(utf8Path(fs::path(folder)),requests);
    std::wstring text=L"Проверьте пути. Изменения коснутся только копии.\n\n";
    for(const auto& match:found) text+=nativePath(match.name+"\n"+(match.path.empty()
        ?(match.status=="ambiguous"?"Несколько разных файлов — не подставляем":"Не найдено")
        :match.path)).wstring()+L"\n\n";
    text+=L"Да — применить найденные пути. Нет — без замены. Отмена — вернуться в AutoCAD.";
    const auto decision=MessageBoxW(GetActiveWindow(),text.c_str(),L"Подосновы комплекта",MB_YESNOCANCEL|MB_ICONQUESTION);
    if(decision==IDCANCEL) return false;
    if(decision==IDYES) for(const auto& match:found) if(!match.path.empty()) chosen.push_back(match);
    return true;
}
}
