import type { DataPassport, DataPassportEntry } from '@green/api-client';
import { AlertTriangle, Check, CircleHelp } from 'lucide-react';
import { DataTable, Icon, InlineMessage, StatusIndicator } from '@green/ui';

const statusLabels: Record<DataPassportEntry['status'], string> = {
  verified: 'Подтверждено',
  partial: 'Неполно',
  missing: 'Не найдено',
  excluded: 'Исключено',
};

const statusTone: Record<DataPassportEntry['status'], 'success' | 'warning' | 'error' | 'neutral'> = {
  verified: 'success',
  partial: 'warning',
  missing: 'error',
  excluded: 'neutral',
};

function PassportEntry({ entry }: { entry: DataPassportEntry }) {
  const status = entry.status;
  const layerNames = entry.layer_names ?? [];
  return (
    <tr data-status={status}>
      <td>
        <span className="data-passport__class">
          <StatusIndicator tone={statusTone[status]} label={entry.label} />
          <small>{layerNames.length ? layerNames.join(', ') : 'Слой не найден'}</small>
        </span>
      </td>
      <td className="mono-cell">{entry.object_count}</td>
      <td>
        <span className={`data-passport__usage data-passport__usage--${entry.used_in_calculation ? 'used' : 'not-used'}`}>
          {entry.used_in_calculation ? <Icon icon={Check} size={14} /> : <Icon icon={CircleHelp} size={14} />}
          {entry.used_in_calculation ? 'Участвует' : 'Не участвует'}
        </span>
      </td>
      <td><span className="data-passport__status">{statusLabels[status]}</span></td>
    </tr>
  );
}

export function DataPassportPanel({ passport }: { passport: DataPassport }) {
  const isLimited = passport.mass_placement_status === 'limited';
  const isBlocked = passport.mass_placement_status === 'blocked';
  const messageTone = isBlocked ? 'info' : 'warning';
  return (
    <section className="data-passport" aria-labelledby="data-passport-title">
      <header className="data-passport__header">
        <div>
          <h2 id="data-passport-title">Паспорт исходных данных</h2>
          <p>{passport.summary}</p>
        </div>
        <StatusIndicator
          tone={isBlocked ? 'neutral' : isLimited ? 'warning' : 'success'}
          label={isBlocked ? 'Не готово' : isLimited ? 'Ограниченная проверка' : 'Проверено'}
        />
      </header>
      <DataTable className="data-passport__table">
        <thead><tr><th>Класс</th><th>Объектов</th><th>В расчёте</th><th>Состояние</th></tr></thead>
        <tbody>{(passport.entries ?? []).map((entry) => <PassportEntry key={entry.kind} entry={entry} />)}</tbody>
      </DataTable>
      {(passport.gaps ?? []).length ? (
        <InlineMessage tone={messageTone} title={isBlocked ? 'Подготовьте карту' : 'Массовая посадка требует проверки'}>
          <span className="data-passport__gaps">{(passport.gaps ?? []).join('; ')}</span>
        </InlineMessage>
      ) : null}
      {!(passport.gaps ?? []).length && (passport.used_in_calculation ?? []).length ? (
        <p className="data-passport__footnote"><AlertTriangle size={14} /> Использованы только подтверждённые слои</p>
      ) : null}
    </section>
  );
}
