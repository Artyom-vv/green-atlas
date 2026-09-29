import type { DataPassport, DataPassportEntry } from '@green/api-client';
import { Disclosure } from '@green/ui';
import { type FC } from 'react';
import { SourceFacts } from './SourceFacts';
interface EntryDetailsProps {
  entry: DataPassportEntry;
  passport: DataPassport;
}
const STATUS_LABELS: Record<DataPassportEntry['status'], string> = {
  verified: 'Распознано',
  partial: 'Неполные данные',
  missing: 'Нет данных',
  excluded: 'Исключено',
};
export const EntryDetails: FC<EntryDetailsProps> = ({ entry, passport }) => (
  <Disclosure
    variant="section"
    title={
      <span className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <span className="flex min-w-0 flex-1 flex-wrap items-baseline gap-x-4 gap-y-1">
          <span className="font-medium">{entry.label}</span>
          <span className="text-xs font-normal text-neutral-500">
            Объектов: {entry.object_count.toLocaleString('ru-RU')}, слоёв:{' '}
            {entry.layer_names?.length ?? 0}
          </span>
        </span>
        <span
          className={
            entry.status === 'partial' || entry.status === 'missing'
              ? 'text-xs font-normal text-amber-700'
              : 'text-xs font-normal text-neutral-600'
          }
        >
          {STATUS_LABELS[entry.status]}
        </span>
      </span>
    }
  >
    <div className="grid min-w-0 gap-4 text-xs wrap-anywhere text-neutral-700">
      <SourceFacts
        items={[
          {
            label: 'Исходных объектов',
            value: entry.object_count.toLocaleString('ru-RU'),
          },
          ...(entry.display_feature_count != null
            ? [
                {
                  label: 'Элементов карты',
                  value: entry.display_feature_count.toLocaleString('ru-RU'),
                },
              ]
            : []),
          ...(entry.geometry_coverage
            ? [
                {
                  label: 'Расчётных площадей',
                  value:
                    entry.geometry_coverage.area_count.toLocaleString('ru-RU'),
                },
                {
                  label: 'Линейных препятствий',
                  value:
                    entry.geometry_coverage.linear_count.toLocaleString(
                      'ru-RU',
                    ),
                },
                {
                  label: 'Точечных объектов',
                  value:
                    entry.geometry_coverage.point_count.toLocaleString('ru-RU'),
                },
                {
                  label: 'Пропусков подготовки',
                  value: (
                    entry.geometry_coverage.unresolved?.length ?? 0
                  ).toLocaleString('ru-RU'),
                },
              ]
            : [
                {
                  label: 'Расчётное представление',
                  value: 'Нет актуальных данных',
                },
              ]),
        ]}
      />
      {entry.layer_names?.length ? (
        <ul
          className="m-0 grid list-none gap-2 rounded-lg bg-neutral-50 p-3"
          aria-label={`Слои: ${entry.label}`}
        >
          {entry.layer_names.map((name) => (
            <li key={name}>
              <code>{name}</code>
            </li>
          ))}
        </ul>
      ) : (
        <p>Слои этого класса не найдены</p>
      )}
      {!!(
        entry.source_file_name &&
        entry.source_file_name !== passport.source_file_name
      ) && <p>Источник: {entry.source_file_name}</p>}
      {!!(
        entry.source_owner && entry.source_owner !== passport.source_owner
      ) && <p>Владелец: {entry.source_owner}</p>}
      {!!(
        entry.source_imported_at &&
        entry.source_imported_at !== passport.source_imported_at
      ) && (
        <p>
          Импорт:{' '}
          {new Date(entry.source_imported_at).toLocaleDateString('ru-RU')}
        </p>
      )}
    </div>
  </Disclosure>
);
