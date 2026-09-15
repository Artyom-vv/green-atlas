import type { DataPassport } from '@green/api-client';
import { Disclosure, InlineMessage, Text } from '@green/ui';
import { useId, type FC, type ReactNode } from 'react';
import { PassportAttention } from './passport/PassportAttention';
import { PassportIncluded } from './passport/PassportIncluded';
import { SourceFacts } from './passport/SourceFacts';

export interface DataPassportPanelProps {
  passport: DataPassport;
  header?: ReactNode;
}
export const DataPassportPanel: FC<DataPassportPanelProps> = ({
  passport,
  header,
}) => {
  const titleId = useId();
  const blocked = passport.mass_placement_status === 'blocked';
  const limited = passport.mass_placement_status === 'limited' || blocked;
  const entries = passport.entries ?? [];
  const attention = entries.filter(
    (entry) => !entry.used_in_calculation || entry.status !== 'verified',
  );
  const included = entries.filter(
    (entry) => entry.used_in_calculation && entry.status === 'verified',
  );
  return (
    <section
      className="grid gap-5 text-neutral-800"
      aria-labelledby={header === undefined ? titleId : undefined}
      aria-label={header !== undefined ? 'Паспорт исходных данных' : undefined}
    >
      {header === undefined ? (
        <Text as="h2" id={titleId} variant="heading">
          Паспорт исходных данных
        </Text>
      ) : (
        header
      )}
      <SourceFacts
        items={[
          { label: 'Файл', value: passport.source_file_name ?? 'Не указан' },
          {
            label: 'Импортирован',
            value: passport.source_imported_at
              ? new Date(passport.source_imported_at).toLocaleDateString(
                  'ru-RU',
                )
              : 'Дата не указана',
          },
        ]}
      />
      {limited ? (
        <InlineMessage
          tone={blocked ? 'warning' : 'info'}
          title={
            blocked
              ? 'Карта не готова к расчёту'
              : 'Проверка ограничена исходными данными'
          }
        >
          {blocked
            ? 'Недостающие исходные данные перечислены ниже.'
            : 'Результат проверяется по загруженным ограничениям. Отсутствующие данные не означают отсутствие ограничений на территории.'}
        </InlineMessage>
      ) : (
        <Text as="p" variant="caption">
          Проверка учитывает только загруженные слои
        </Text>
      )}
      <PassportAttention entries={attention} passport={passport} />
      {!!passport.gaps?.length && (
        <Disclosure
          variant="plain"
          title={`Основания ограничений (${passport.gaps.length})`}
        >
          <ul className="m-0 space-y-1 pl-5 text-xs">
            {passport.gaps.map((gap) => (
              <li key={gap}>{gap}</li>
            ))}
          </ul>
        </Disclosure>
      )}
      <PassportIncluded entries={included} passport={passport} />
      <Disclosure variant="plain" title="Координаты и происхождение">
        <SourceFacts
          items={[
            {
              label: 'Владелец данных',
              value: passport.source_owner ?? 'Не указан',
            },
            {
              label: 'Система координат',
              value: passport.coordinate_reference?.crs_id ?? 'Не подтверждена',
            },
            {
              label: 'Контрольные точки',
              value: passport.coordinate_reference?.control_points_count ?? 0,
            },
          ]}
        />
      </Disclosure>
    </section>
  );
};
