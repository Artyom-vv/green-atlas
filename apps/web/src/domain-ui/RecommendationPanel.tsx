import { useEffect, useMemo, useState } from 'react';
import type { PlantingZoneAssignment, RecommendationRequest } from '@green/api-client';
import { Button, Checkbox, FormField, InlineMessage, NumberStepper, Select } from '@green/ui';
import { InspectorHeader } from './InspectorHeader';
import { InspectorFooter, InspectorSettingRow } from './InspectorLayout';

type Draft = Omit<RecommendationRequest, 'base_plan_version'>;

export function RecommendationPanel({ zones, loading, error, onPreview, onCancel }: {
  zones: PlantingZoneAssignment[];
  loading?: boolean;
  error?: string;
  onPreview: (draft: Draft) => void;
  onCancel: () => void;
}) {
  const availableIds = (items: PlantingZoneAssignment[]) => items.flatMap((zone) => zone.id ? [zone.id] : []);
  const [zoneIds, setZoneIds] = useState<string[]>(() => availableIds(zones));
  const [profile, setProfile] = useState<RecommendationRequest['profile']>('balanced');
  const [maxSites, setMaxSites] = useState(40);

  useEffect(() => {
    const available = new Set(availableIds(zones));
    setZoneIds((current) => {
      const retained = current.filter((id) => available.has(id));
      return retained.length ? retained : availableIds(zones);
    });
  }, [zones]);

  const selected = useMemo(() => new Set(zoneIds), [zoneIds]);
  return <div className="project-inspector recommendation-panel">
    <InspectorHeader title="Предложить посадки" meta="Один проверяемый вариант" />
    <div className="recommendation-panel__content">
      <p className="recommendation-panel__intro">Сервис найдёт допустимые позиции. Решение останется за вами.</p>
      <fieldset className="recommendation-panel__zones">
        <legend>Участки</legend>
        {zones.flatMap((zone) => zone.id ? [<Checkbox key={zone.id} label={zone.label} checked={selected.has(zone.id)} onChange={(event) => setZoneIds((current) => event.target.checked ? [...new Set([...current, zone.id!])] : current.filter((id) => id !== zone.id))} />] : [])}
      </fieldset>
      <FormField label="Приоритет" hint="Он меняет рисунок и породу, но не отменяет проверку.">
        <Select aria-label="Приоритет" value={profile} onChange={(event) => setProfile(event.target.value as RecommendationRequest['profile'])}>
          <option value="balanced">Сбалансированный</option>
          <option value="shade">Потенциал тени</option>
          <option value="continuity">Связность посадок</option>
          <option value="low_future_conflict">Меньше будущих конфликтов</option>
        </Select>
      </FormField>
      <InspectorSettingRow label="Не больше"><NumberStepper label="Максимум посадок" value={maxSites} onChange={setMaxSites} min={1} max={500} step={5} /></InspectorSettingRow>
      <InlineMessage tone="info">Инсоляция, почва и вода не оцениваются без исходных данных.</InlineMessage>
      {error ? <InlineMessage tone="error">{error}</InlineMessage> : null}
    </div>
    <div className="inspector-spacer" />
    <InspectorFooter><Button variant="secondary" disabled={loading} onClick={onCancel}>Отмена</Button><Button variant="primary" loading={loading} disabled={!zoneIds.length} onClick={() => onPreview({ zone_ids: zoneIds, profile, max_sites: maxSites })}>Показать</Button></InspectorFooter>
  </div>;
}
