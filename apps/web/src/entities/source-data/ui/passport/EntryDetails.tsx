import type { DataPassport, DataPassportEntry } from '@green/api-client';
import { Disclosure } from '@green/ui';
import { type FC } from 'react';
import { SourceFacts } from './SourceFacts';
interface EntryDetailsProps {
  entry: DataPassportEntry;
  passport: DataPassport;
}
const STATUS_LABELS: Record<DataPassportEntry['status'], string> = {
  verified: 'Учтено',
  partial: 'Неполные данные',
  missing: 'Нет данных',
  excluded: 'Исключено',
};
export const EntryDetails: FC<EntryDetailsProps> = ({ entry, passport }) => (
  <Disclosure
    variant="section"
    title={
      <span className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm leading-5">
        <span className="grid min-w-0 flex-1">
          <span className="font-medium">{entry.label}</span>
          <span className="text-xs leading-4 font-normal text-neutral-500">
            Объектов: {entry.object_count.toLocaleString('ru-RU')}
            {entry.layer_names !== undefined &&
              `, слоёв: ${entry.layer_names.length}`}
          </span>
        </span>
        <span
          className={
            entry.status === 'partial' || entry.status === 'missing'
              ? 'text-warning-strong text-sm leading-5 font-normal'
              : 'text-sm leading-5 font-normal text-neutral-600'
          }
        >
          {entry.status === 'verified' && !entry.used_in_calculation
            ? 'Не в расчёте'
            : STATUS_LABELS[entry.status]}
        </span>
      </span>
    }
  >
    <div className="grid min-w-0 gap-4 text-sm leading-5 wrap-anywhere text-neutral-700">
      {entry.note && <p className="m-0">{entry.note}</p>}
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
                  value:
                    entry.geometry_coverage.unresolved === undefined
                      ? 'Нет данных'
                      : entry.geometry_coverage.unresolved.length.toLocaleString(
                          'ru-RU',
                        ),
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
      {!!entry.geometry_coverage?.unresolved?.length && (
        <Disclosure title="Пропуски подготовленной геометрии" variant="plain">
          <ul
            className="m-0 grid gap-3 pl-5"
            aria-label={`Пропуски: ${entry.label}`}
          >
            {entry.geometry_coverage.unresolved.map((issue, index) => (
              <li key={index} className="grid gap-1">
                <span>{issue.detail}</span>
                {!!issue.routes?.length && (
                  <code>{issue.routes.join(', ')}</code>
                )}
                <span className="text-neutral-500">{issue.reason}</span>
              </li>
            ))}
          </ul>
        </Disclosure>
      )}
      {entry.layer_names?.length ? (
        <ul
          className="m-0 grid list-none gap-2 p-0"
          aria-label={`Слои: ${entry.label}`}
        >
          {entry.layer_names.map((name) => (
            <li key={name}>
              <code>{name}</code>
            </li>
          ))}
        </ul>
      ) : (
        <p className="m-0">
          {entry.layer_names === undefined
            ? 'Имена слоёв не представлены'
            : 'Слои этого класса не найдены'}
        </p>
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
