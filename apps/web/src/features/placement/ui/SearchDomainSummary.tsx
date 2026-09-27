import type { PatternPreview } from '@green/api-client';
import { Disclosure } from '@green/ui';

const reasonLabels: Record<string, string> = {
  utility_context: 'Условия посадки у сетей',
  utility_context_missing: 'Не заданы характеристики сетей',
  utility_type_unknown: 'Не определён тип сетей',
  utility_installation_unknown: 'Не указан способ прокладки',
  utility_installation_unsupported: 'Нет правила для надземных сетей',
  utility_reference_unknown: 'Не определён смысл линий сетей',
  utility_axis_extent_unknown: 'Нет наружных размеров сетей',
  utility_reference_unsupported: 'Геометрия сети не соответствует правилу',
  utility_context_unconfirmed: 'Не подтверждены характеристики сетей',
  utility_rule_missing: 'Нет правила для типа сети',
  utility_no_numeric_setback: 'В таблице не указан отступ для растения',
  source_object: 'Назначение или чтение объектов',
  object_interior: 'Внутренние области контуров',
  obstacle_edge: 'Участки у границ препятствий',
  site_membership: 'Граница территории',
  projection_missing: 'Не передана геометрия объекта',
  projection_invalid: 'Ошибка передачи контура',
  projection_area_mismatch: 'Расхождение площади с AutoCAD',
  projection_accuracy_missing: 'В захвате не указана точность контура',
};

export function SearchDomainSummary({
  domains,
  showPendingNotice = true,
}: {
  domains: PatternPreview['search_domains'];
  showPendingNotice?: boolean;
}) {
  if (!domains?.length) return null;
  const sum = (
    key:
      | 'available_area_m2'
      | 'excluded_area_m2'
      | 'unresolved_area_m2'
      | 'pending_area_m2',
  ) =>
    domains
      .reduce((total, domain) => total + (domain[key] ?? 0), 0)
      .toLocaleString('ru-RU', { maximumFractionDigits: 1 });
  const reasons = new Map<string, number>();
  for (const domain of domains) {
    for (const [code, area] of Object.entries(
      domain.unresolved_reason_areas_m2 ?? {},
    )) {
      const label = reasonLabels[code] ?? 'Другие причины';
      reasons.set(label, (reasons.get(label) ?? 0) + area);
    }
  }
  return (
    <section
      aria-label="Область поиска"
      className="grid gap-2 rounded border border-neutral-300 p-3"
    >
      <h4 className="m-0 text-xs font-semibold">Область поиска</h4>
      {domains.some((domain) => domain.method === 'hybrid') && (
        <p className="m-0 text-xs text-neutral-600">
          {domains.every((domain) => domain.final_check === 'prepared_geometry')
            ? 'Расчёт по сохранённой геометрии AutoCAD. Подбор и проверка посадок выполняются локально.'
            : 'По подготовленным ограничениям. Найденные посадки дополнительно проверяет AutoCAD.'}
        </p>
      )}
      <dl className="m-0 grid gap-2 text-xs">
        <div className="flex justify-between gap-3">
          <dt className="text-emerald-800">Для поиска</dt>
          <dd className="m-0 tabular-nums">{sum('available_area_m2')} м²</dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt>Исключено ограничениями</dt>
          <dd className="m-0 tabular-nums">{sum('excluded_area_m2')} м²</dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt className="text-amber-800">Требует уточнения</dt>
          <dd className="m-0 tabular-nums">{sum('unresolved_area_m2')} м²</dd>
        </div>
        {domains.some((domain) => (domain.pending_area_m2 ?? 0) > 0) && (
          <div className="flex justify-between gap-3">
            <dt className="text-neutral-600">Осталось проверить</dt>
            <dd className="m-0 tabular-nums">{sum('pending_area_m2')} м²</dd>
          </div>
        )}
      </dl>
      {reasons.size > 0 && (
        <Disclosure title="Что требует уточнения" variant="plain">
          <dl
            className="m-0 grid gap-2 text-xs"
            aria-label="Основные причины по площади"
          >
            {[...reasons]
              .sort((a, b) => b[1] - a[1])
              .map(([label, area]) => (
                <div key={label} className="flex justify-between gap-3">
                  <dt>{label}</dt>
                  <dd className="m-0 whitespace-nowrap tabular-nums">
                    {area.toLocaleString('ru-RU', { maximumFractionDigits: 1 })}{' '}
                    м²
                  </dd>
                </div>
              ))}
          </dl>
        </Disclosure>
      )}
      {showPendingNotice &&
        domains.some((domain) => domain.stop_reason !== 'resolution') && (
          <p className="mb-0 text-xs" role="status">
            Предварительная проверка не завершена
          </p>
        )}
    </section>
  );
}
