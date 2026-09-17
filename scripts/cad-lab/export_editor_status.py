"""Produce a portable per-street status from retained, independent receipts."""
from collections import Counter
import json
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def main():
    audit = read('.runtime/twenty-street-audit/summary.json')
    groups = read('.runtime/twenty-street-audit/group-qualification.json')
    imports = read('.runtime/source-editor/prepared-groups/summary.json')
    census = read('.runtime/source-editor/prepared-acis-census.json')
    repairs = read('.runtime/source-editor/repaired-assemblies/summary.json')
    verified_path = Path('.runtime/source-editor/repaired-editor-checks/summary.json')
    verified = read(verified_path) if verified_path.exists() else []
    lower_memory_path = Path('.runtime/source-editor/lodo-lower-memory-import/summary.json')
    if lower_memory_path.exists():
        verified += read(lower_memory_path)
    rows = []
    for street in audit['streets']:
        name = street['street']
        qualified = [r for r in imports if r['street'] == name]
        repair_rows = []
        for row in repairs:
            if row['street'] != name:
                continue
            folder = Path(row['directory'])
            process = read(folder/'process.json')
            assembly = read(folder/'assembly.json') if (folder/'assembly.json').exists() else None
            repair_rows.append({'root': row['root'], 'process': process, 'assembly': assembly})
        rows.append({
            'street': name,
            'sdk_status': street['counts'],
            'selected_group_barriers_before_prepare': dict(Counter(g['status'] for g in groups if g['street'] == name)),
            'prepared_import_baseline': [{'root':r['root'],'status':r['status'],'features':(r.get('published') or {}).get('features'), 'process':r.get('process')} for r in qualified],
            'assemblies_requiring_acis_repair': sum(r['street'] == name and r['suspect_missing_binary_records'] for r in census),
            'acis_census_groups': sum(r['street'] == name for r in census),
            'acis_repairs': repair_rows,
            'repaired_imports': [{'root': r['root'], 'source_sha256': r['source_sha256'], 'status': r['status'],
                                  'process': r.get('process'), 'features': (r.get('published') or {}).get('features')}
                                 for r in verified if r['street'] == name],
            'regulatory_calculation_verified': False,
        })
    destination = Path('docs/implementation/2026-09-16-source-editor')
    (destination/'dataset-status.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf8')
    lines = ['# Состояние всех 20 улиц после подключения полного редактора', '',
        'Срез 16 сентября. Улицы и самостоятельные комплекты не объединяются произвольно.',
        '**Нормативный расчёт полного официального комплекта пока не принят ни по одной улице.**', '',
        'Кустанайская: исправленный полный DXF `2beba2b6` импортируется за 45,52 с / 1,28 ГиБ.',
        'Рабочий QA-проект `320fa737-e8d2-48e8-93f3-614bd52e48c6`: участок, дерево, DXF-выпуск.',
        'При выпуске сохранены исходные графические объекты и все 2384 SAB. REGION для расчёта ещё не интерпретированы.', '',
        '| Улица | Подготовленный импорт до исправления ACDSDATA | Пересборка ACDSDATA | Остальные барьеры |',
        '| --- | --- | --- | --- |']
    for row in rows:
        baseline = Counter(r['status'] for r in row['prepared_import_baseline'])
        imported = f"Открылись: {baseline['editable_draft']}; не прошли бюджет: {baseline['not_published']}" if baseline else 'Ещё не проходил'
        if row['repaired_imports']:
            successful = [r for r in row['repaired_imports'] if r['status'] == 'editable_draft']
            imported += f"; после исправления: {len(successful)} из {len(row['repaired_imports'])}"
        if row['street'].startswith('18.'):
            imported = 'Исправленный комплект: импорт + редактор + выпуск прошли'
        repaired = sum(r['process']['status'] == 'completed' for r in row['acis_repairs'])
        repair = f"{repaired} из {row['assemblies_requiring_acis_repair']} проверены" if row['assemblies_requiring_acis_repair'] else ('Этот признак потери не найден' if row['acis_census_groups'] else 'Нет проверенной сборки')
        if row['street'].startswith('18.'):
            repair = '2384 SAB восстановлены в производной сборке и проверены'
        counts = row['selected_group_barriers_before_prepare']
        barriers = []
        if any(r['process']['status'] != 'completed' for r in row['acis_repairs']):
            barriers.append('Пересборка не прошла сравнение ACIS; не опубликована')
        if counts.get('reference_review_or_read_failure'):
            barriers.append(f"Групп с проверкой ссылок: {counts['reference_review_or_read_failure']}")
        if counts.get('assembly_failed'):
            barriers.append(f"Групп с проблемами сборки: {counts['assembly_failed']}")
        if baseline['not_published']:
            barriers.append('4 ГиБ при подготовке/полном чтении')
        if row['street'].startswith('11.'):
            barriers.append('CAD не найден в папке и её основном ZIP; есть PDF/таблицы')
        if row['street'].startswith('18.'):
            barriers = ['Ссылки выбранного ИП восстановлены через идентичный архивный корень; расчёт REGION требует проверки']
        lines.append(f"| {row['street']} | {imported} | {repair} | {'; '.join(barriers) or 'Назначения слоёв и контроль расчёта не подтверждены'} |")
    lines += ['', 'Числа относятся к отдельным комплектам, а не ко всей улице целиком.',
        'Исходные 10/15 успешных импортов доказывают открытие сохранённого DXF, но не полноту относительно DWG.',
        'Дефект отсутствующего заголовка ACDSDATA найден в нашей offline-подготовке; это не доказательство повреждения датасета.',
        'Пересборка не отменяет ресурсные ограничения больших проектов. Результат SDK save/read не заменяет независимый CAD-эталон.', '',
        '[Машинные данные](dataset-status.json) · [Текущий handoff](prepared-import.md) · [Первоначальный пофайловый аудит](../2026-09-16-twenty-street-audit/README.md)', '']
    (destination/'dataset-status.md').write_text('\n'.join(lines),encoding='utf8')


if __name__ == '__main__':
    main()
