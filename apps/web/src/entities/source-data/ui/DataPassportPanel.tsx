import type { DataPassport } from '@green/api-client';
import { Disclosure, Tabs, Text } from '@green/ui';
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
  const tabsId = useId();
  const attentionId = `${tabsId}-attention`;
  const includedId = `${tabsId}-included`;
  const blocked =
    passport.calculation_status === 'not_ready' ||
    passport.mass_placement_status === 'blocked';
  const limited = passport.mass_placement_status === 'limited' || blocked;
  const entries = passport.entries ?? [];
  const hasEntryReport = passport.entries !== undefined;
  const attention = entries.filter(
    (entry) => !entry.used_in_calculation || entry.status !== 'verified',
  );
  const included = entries.filter(
    (entry) => entry.used_in_calculation && entry.status === 'verified',
  );
  const hasRestrictions = Boolean(passport.gaps?.length);
  const [view, setView] = useState<'attention' | 'included'>(
    attention.length || hasRestrictions || !entries.length
      ? 'attention'
      : 'included',
  );
  return (
    <section
      className="grid min-w-0 text-sm leading-5 text-neutral-800"
      aria-labelledby={header === undefined ? titleId : undefined}
      aria-label={header !== undefined ? 'Паспорт исходных данных' : undefined}
    >
      {header === undefined ? (
        <Text
          as="h2"
          id={titleId}
          variant="body"
          className="px-4 py-4 font-semibold min-[720px]:px-6"
        >
          Паспорт исходных данных
        </Text>
      ) : (
        header
      )}
      {limited && (
        <Text
          as="p"
          role="status"
          className="text-warning-strong border-0 border-b border-solid border-neutral-200 px-4 py-3 min-[720px]:px-6"
        >
          {passport.summary ||
            (blocked
              ? 'Карта не готова к расчёту'
              : 'Проверка ограничена исходными данными')}
        </Text>
      )}
      <div className="border-0 border-b border-solid border-neutral-200 px-4 min-[720px]:px-6">
        <Tabs
          id={tabsId}
          label="Состав паспорта"
          value={view}
          onChange={setView}
          controlSize="large"
          className="max-w-full"
          options={[
            {
              value: 'attention',
              label:
                hasEntryReport && (attention.length || !hasRestrictions)
                  ? `Проверить (${attention.length})`
                  : 'Проверить',
              panelId: attentionId,
            },
            {
              value: 'included',
              label: hasEntryReport ? `Учтено (${included.length})` : 'Учтено',
              panelId: includedId,
            },
          ]}
        />
      </div>
      <div
        id={attentionId}
        role="tabpanel"
        aria-labelledby={`${tabsId}-tab-attention`}
        hidden={view !== 'attention'}
      >
        {attention.length ? (
          <PassportAttention entries={attention} passport={passport} />
        ) : (
          <p className="m-0 px-4 py-4 text-sm leading-5 text-neutral-600 min-[720px]:px-6">
            {entries.length
              ? 'Все представленные классы учтены в расчёте'
              : 'Состав исходных данных не представлен'}
          </p>
        )}
      </div>
      <div
        id={includedId}
        role="tabpanel"
        aria-labelledby={`${tabsId}-tab-included`}
        hidden={view !== 'included'}
      >
        {included.length ? (
          <PassportIncluded entries={included} passport={passport} />
        ) : (
          <p className="m-0 px-4 py-4 text-sm leading-5 text-neutral-600 min-[720px]:px-6">
            {entries.length
              ? 'Нет подтверждённых классов в расчёте'
              : 'Состав исходных данных не представлен'}
          </p>
        )}
      </div>
      {view === 'attention' && !!passport.gaps?.length && (
        <Disclosure
          variant="section"
          title={`Основания ограничений (${passport.gaps.length})`}
          defaultOpen={!attention.length}
        >
          <ul className="m-0 space-y-2 pl-5 text-sm leading-5">
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
              value:
                passport.coordinate_reference?.crs_id ??
                passport.coordinate_reference?.name ??
                'Не представлена',
            },
            ...(passport.coordinate_reference
              ? [
                  {
                    label: 'Статус координат',
                    value: {
                      unknown: 'Не подтверждена',
                      local: 'Локальная',
                      declared: 'Заявлена',
                      verified: 'Подтверждена',
                    }[passport.coordinate_reference.status],
                  },
                ]
              : []),
            {
              label: 'Контрольные точки',
              value:
                passport.coordinate_reference?.control_points_count ??
                'Нет данных',
            },
            ...(passport.coordinate_reference?.evidence
              ? [
                  {
                    label: 'Основание привязки',
                    value: passport.coordinate_reference.evidence,
                  },
                ]
              : []),
          ]}
        />
      </Disclosure>
    </section>
  );
};
