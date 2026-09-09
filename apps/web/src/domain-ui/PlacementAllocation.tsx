import type { PatternPreview, PlantingZoneAssignment } from '@green/api-client';
import { repeatedItemLabel } from './plantingZoneLabels';

export function PlacementAllocation({ zones, selectedIds, preview }: {
  zones: PlantingZoneAssignment[];
  selectedIds: string[];
  preview: PatternPreview;
}) {
  const selected = zones.filter(zone => zone.id && selectedIds.includes(zone.id));
  const quotas = preview.zone_allocations ?? [];
  const hasTargets = quotas.some(zone => zone.requested_count != null);
  return <section className="placement-allocation" aria-label="Посадки по участкам">
    <table>
      <caption>Распределение по участкам</caption>
      <thead><tr><th scope="col">Участок</th>{hasTargets ? <th scope="col">Нужно</th> : null}<th scope="col">Найдено</th></tr></thead>
      <tbody>{selected.map(zone => {
        const allocation = quotas.find(item => item.zone_id === zone.id);
        const accepted = allocation?.accepted_count ?? preview.change_set?.additions?.filter(item => item.planting_zone_id === zone.id).length ?? 0;
        return <tr key={zone.id}><th scope="row">{repeatedItemLabel(zones, zone)}</th>{hasTargets ? <td>{allocation?.requested_count ?? 0}</td> : null}<td>{accepted}</td></tr>;
      })}</tbody>
    </table>
    {hasTargets && preview.accepted_count < preview.requested_count ? <p className="editor-panel__hint">Недостающие посадки не перенесены на другие участки.</p> : null}
  </section>;
}
