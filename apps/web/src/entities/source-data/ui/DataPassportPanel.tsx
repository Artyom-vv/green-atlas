import type { DataPassport } from '@green/api-client';
import { Disclosure, InlineMessage, SegmentedControl, Text } from '@green/ui';
import { useId, useState, type FC, type ReactNode } from 'react';
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
  const [view, setView] = useState<'attention' | 'included'>(
    attention.length ? 'attention' : 'included',
  );
  return (
    <section
      className="grid min-w-0 gap-4 text-neutral-800"
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
      {limited ? (
        <InlineMessage tone={blocked ? 'warning' : 'info'}>
          {blocked
            ? 'Карта не готова к расчёту'
            : 'Проверка ограничена исходными данными'}
        </InlineMessage>
      ) : (
        <Text as="p" variant="caption">
          Проверка учитывает только загруженные слои
        </Text>
      )}
      <SegmentedControl
        label="Состав паспорта"
        value={view}
        onChange={setView}
        className="w-fit max-w-full"
        options={[
          { value: 'attention', label: `Проверить (${attention.length})` },
          { value: 'included', label: `Учтено (${included.length})` },
        ]}
      />
      <div hidden={view !== 'attention'}>
        {attention.length ? (
          <PassportAttention entries={attention} passport={passport} />
        ) : (
          <p className="m-0 text-xs text-neutral-600">
            Замечаний к составу слоёв нет
          </p>
        )}
      </div>
      <div hidden={view !== 'included'}>
        {included.length ? (
          <PassportIncluded entries={included} passport={passport} />
        ) : (
          <p className="m-0 text-xs text-neutral-600">
            Нет классов с полной проверкой
          </p>
        )}
      </div>
      {view === 'attention' && !!passport.gaps?.length && (
        <Disclosure
          variant="section"
          title={`Основания ограничений (${passport.gaps.length})`}
        >
          <ul className="m-0 space-y-1 pl-5 text-xs">
            {passport.gaps.map((gap) => (
              <li key={gap}>{gap}</li>
            ))}
          </ul>
        </Disclosure>
      )}
      <Disclosure variant="section" title="О файле и координатах">
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
