import type { FC } from 'react';
import type {
  PlantingZoneAssignment,
  PlantingZonePreview,
} from '@green/api-client';
import { repeatedItemLabel } from '../model/plantingZoneLabels';
import { zoneGeometryPath, zoneGeometryViewBox } from '../model/geometry';

export interface ZoneReviewProps {
  zone: PlantingZoneAssignment;
  zones: PlantingZoneAssignment[];
  preview?: PlantingZonePreview;
}
export const ZoneReview: FC<ZoneReviewProps> = ({ zone, zones, preview }) => {
  const overlaps = preview?.overlaps ?? [];
  const nearby = zones.filter((item) =>
    overlaps.some((overlap) => overlap.zone_id === item.id),
  );
  return (
    <div className="grid min-w-0 gap-3 text-xs leading-5">
      <strong>{zone.label}</strong>
      <svg
        className="rounded-card h-60 w-full border border-solid border-neutral-200 bg-neutral-100"
        role="img"
        aria-label="Контур нового участка и области пересечения"
        viewBox={zoneGeometryViewBox(
          [zone, ...nearby].map((item) => item.geometry),
        )}
      >
        {nearby.map((item) => (
          <path
            key={item.id}
            d={zoneGeometryPath(item.geometry)}
            className="fill-neutral-200 stroke-neutral-500"
            vectorEffect="non-scaling-stroke"
            fillRule="evenodd"
          />
        ))}
        <path
          d={zoneGeometryPath(zone.geometry)}
          className="fill-blue-600/10 stroke-blue-600"
          strokeWidth="2"
          vectorEffect="non-scaling-stroke"
          fillRule="evenodd"
        />
        {overlaps.map((overlap) => (
          <path
            key={overlap.zone_id}
            d={zoneGeometryPath(overlap.geometry)}
            className="fill-error/40 stroke-error"
            vectorEffect="non-scaling-stroke"
            fillRule="evenodd"
          />
        ))}
      </svg>
      {preview ? (
        <>
          <span>Площадь: {preview.area_m2.toLocaleString('ru')} м²</span>
          {overlaps.length ? (
            <>
              <p className="m-0">
                Синий — новый контур. Красным выделены пересечения с рабочими
                участками:
              </p>
              <ul className="m-0 pl-4">
                {overlaps.map((overlap) => {
                  const item = zones.find(
                    (item) => item.id === overlap.zone_id,
                  );
                  return (
                    <li key={overlap.zone_id}>
                      {item ? repeatedItemLabel(zones, item) : overlap.label}:{' '}
                      {overlap.area_m2.toLocaleString('ru')} м²
                    </li>
                  );
                })}
              </ul>
            </>
          ) : preview.error ? (
            <p role="alert" className="text-error m-0">
              {preview.error}
            </p>
          ) : (
            <p className="m-0">
              Контур можно сохранить. Существующие посадки не изменяются этим
              подтверждением.
            </p>
          )}
        </>
      ) : (
        <p role="status" className="m-0 text-neutral-600">
          Проверяем границы участка…
        </p>
      )}
    </div>
  );
};
