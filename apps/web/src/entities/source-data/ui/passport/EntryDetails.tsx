import type { DataPassport, DataPassportEntry } from '@green/api-client';
import { Disclosure } from '@green/ui';
import { type FC } from 'react';
import { SourceFacts } from './SourceFacts';
interface EntryDetailsProps {
  entry: DataPassportEntry;
  passport: DataPassport;
}
export const EntryDetails: FC<EntryDetailsProps> = ({ entry, passport }) => (
  <Disclosure
    variant="plain"
    title={
      <>
        Подробности<span className="sr-only">: {entry.label}</span>
      </>
    }
  >
    <div className="grid min-w-0 gap-2 text-xs wrap-anywhere text-neutral-700">
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
        <ul className="m-0 pl-5" aria-label={`Слои: ${entry.label}`}>
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
