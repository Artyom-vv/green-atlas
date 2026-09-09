import type { DataPassport, DataPassportEntry } from '@green/api-client';
import { ChevronRight } from 'lucide-react';
import { DataTable } from '@green/ui';
import './data-passport.css';

const statusLabels: Record<DataPassportEntry['status'], string> = {
  verified: 'Распознано', partial: 'Неполные данные', missing: 'Нет данных', excluded: 'Исключено',
};

function EntryDetails({ entry, passport }: { entry: DataPassportEntry; passport: DataPassport }) {
  return <details className="source-details passport-entry-details">
    <summary><ChevronRight size={14} aria-hidden="true" />Подробности<span className="sr-only">: {entry.label}</span></summary>
    <p>Объектов: {entry.object_count.toLocaleString('ru-RU')}. В расчёте: {entry.used_object_count ?? 0}.</p>
    {(entry.layer_names ?? []).length ? <ul aria-label={`Слои: ${entry.label}`}>{entry.layer_names!.map(name => <li key={name}><code>{name}</code></li>)}</ul> : <p>Слои этого класса не найдены</p>}
    {entry.note ? <p>{entry.note}</p> : null}
    {entry.source_file_name && entry.source_file_name !== passport.source_file_name ? <p>Источник: {entry.source_file_name}</p> : null}
    {entry.source_owner && entry.source_owner !== passport.source_owner ? <p>Владелец: {entry.source_owner}</p> : null}
    {entry.source_imported_at && entry.source_imported_at !== passport.source_imported_at ? <p>Импорт: {new Date(entry.source_imported_at).toLocaleDateString('ru-RU')}</p> : null}
  </details>;
}

export function DataPassportPanel({ passport }: { passport: DataPassport }) {
  const blocked = passport.mass_placement_status === 'blocked';
  const limited = passport.mass_placement_status === 'limited' || blocked;
  const entries = passport.entries ?? [];
  const attention = entries.filter(entry => !entry.used_in_calculation || entry.status !== 'verified');
  const included = entries.filter(entry => entry.used_in_calculation && entry.status === 'verified');
  return <section className="data-passport" aria-labelledby="data-passport-title">
    <header className="data-passport__header"><h2 id="data-passport-title">Паспорт исходных данных</h2></header>
    <dl className="passport-source">
      <div><dt>Файл</dt><dd>{passport.source_file_name ?? 'Не указан'}</dd></div>
      <div><dt>Импортирован</dt><dd>{passport.source_imported_at ? new Date(passport.source_imported_at).toLocaleDateString('ru-RU') : 'Дата не указана'}</dd></div>
    </dl>
    {limited ? <section className="passport-coverage" aria-label="Границы проверки">
      <h3>{blocked ? 'Карта не готова к расчёту' : 'Проверка ограничена исходными данными'}</h3>
      <p>{blocked ? 'Недостающие исходные данные перечислены ниже.' : 'Результат проверяется по загруженным ограничениям. Отсутствующие данные не означают отсутствие ограничений на территории.'}</p>
    </section> : <p className="passport-footnote">Проверка учитывает только загруженные слои</p>}
    {attention.length ? <section aria-label="Данные, требующие внимания">
      <h3>Что не учтено полностью</h3>
      <ul className="passport-attention">{attention.map(entry => <li key={entry.kind}>
        <div className="passport-attention__heading"><strong>{entry.label}</strong><span>{statusLabels[entry.status]}</span></div>
        <p>{entry.used_in_calculation ? 'Участвует в расчёте частично' : 'Не участвует в расчёте'}{entry.decision_level === 'stop' ? '. Блокирует расчёт' : ''}</p>
        <EntryDetails entry={entry} passport={passport} />
      </li>)}</ul>
    </section> : null}
    {(passport.gaps ?? []).length ? <details className="source-details passport-gaps"><summary><ChevronRight size={14} aria-hidden="true" />Основания ограничений ({passport.gaps!.length})</summary><ul>{passport.gaps!.map(gap => <li key={gap}>{gap}</li>)}</ul></details> : null}
    {included.length ? <section aria-label="Данные в расчёте"><h3>Учтено в расчёте</h3>
      <DataTable className="passport-table"><thead><tr><th>Данные</th><th>Объектов</th><th><span className="sr-only">Подробности</span></th></tr></thead>
        <tbody>{included.map(entry => <tr key={entry.kind}><td>{entry.label}</td><td>{entry.object_count.toLocaleString('ru-RU')}</td><td><EntryDetails entry={entry} passport={passport} /></td></tr>)}</tbody>
      </DataTable>
    </section> : null}
    <details className="source-details passport-provenance"><summary><ChevronRight size={14} aria-hidden="true" />Координаты и происхождение</summary>
      <dl className="passport-source"><div><dt>Владелец данных</dt><dd>{passport.source_owner ?? 'Не указан'}</dd></div><div><dt>Система координат</dt><dd>{passport.coordinate_reference?.crs_id ?? 'Не подтверждена'}</dd></div><div><dt>Контрольные точки</dt><dd>{passport.coordinate_reference?.control_points_count ?? 0}</dd></div></dl>
    </details>
  </section>;
}
