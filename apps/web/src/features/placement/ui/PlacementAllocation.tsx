import type { FC } from 'react';
import type { PatternPreview, PlantingZoneAssignment } from '@green/api-client';
import { repeatedItemLabel } from '@/entities/planting-zone';

export interface PlacementAllocationProps {
  zones: PlantingZoneAssignment[];
  selectedIds: string[];
  preview: PatternPreview;
}

export const PlacementAllocation: FC<PlacementAllocationProps> = ({
  zones,
  selectedIds,
  preview,
}) => {
  const selected = zones.filter(
    (zone) => zone.id && selectedIds.includes(zone.id),
  );
  const quotas = preview.zone_allocations ?? [];
  const hasTargets = quotas.some((zone) => zone.requested_count != null);
  return (
    <section
      className="grid min-w-0 gap-2 overflow-x-auto"
      aria-label="Посадки по участкам"
    >
      <table className="w-full border-collapse text-left text-xs [&_td]:py-2 [&_td]:text-right [&_th]:py-2 [&_th]:font-medium [&_tr]:border-0 [&_tr]:border-b [&_tr]:border-solid [&_tr]:border-neutral-200">
        <caption className="py-2 text-left font-semibold">
          Распределение по участкам
        </caption>
        <thead>
          <tr>
            <th scope="col">Участок</th>
            {hasTargets && (
              <th scope="col" className="text-right">
                Нужно
              </th>
            )}
            <th scope="col" className="text-right">
              Найдено
            </th>
          </tr>
        </thead>
        <tbody>
          {selected.map((zone) => {
            const allocation = quotas.find((item) => item.zone_id === zone.id);
            const accepted =
              allocation?.accepted_count ??
              preview.change_set?.additions?.filter(
                (item) => item.planting_zone_id === zone.id,
              ).length ??
              0;
            return (
              <tr key={zone.id}>
                <th scope="row">{repeatedItemLabel(zones, zone)}</th>
                {hasTargets && <td>{allocation?.requested_count ?? 0}</td>}
                <td>{accepted}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {hasTargets && preview.accepted_count < preview.requested_count && (
        <p className="m-0 text-xs leading-4 text-neutral-600">
          Недостающие посадки не перенесены на другие участки.
        </p>
      )}
    </section>
  );
};
