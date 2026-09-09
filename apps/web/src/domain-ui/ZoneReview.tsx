import type { PlantingZoneAssignment, PlantingZonePreview } from '@green/api-client';
import { repeatedItemLabel } from './plantingZoneLabels';
import './workspace-modules.css';

export function ZoneReview({ zone, zones, preview }: { zone: PlantingZoneAssignment; zones: PlantingZoneAssignment[]; preview?: PlantingZonePreview }) {
  const polygons = (geometry: Record<string, unknown>): number[][][][] => geometry.type === 'Polygon' ? [geometry.coordinates as number[][][]] : geometry.type === 'MultiPolygon' ? geometry.coordinates as number[][][][] : [];
  const overlaps = preview?.overlaps ?? [];
  const nearby = zones.filter(item => overlaps.some(overlap => overlap.zone_id === item.id));
  const points = [zone, ...nearby].flatMap(item => polygons(item.geometry).flat(2));
  const xs = points.map(p => p[0]), ys = points.map(p => p[1]);
  const x = Math.min(...xs), y = Math.min(...ys), width = Math.max(...xs) - x || 1, height = Math.max(...ys) - y || 1, pad = Math.max(width, height) * .08;
  const path = (geometry: Record<string, unknown>) => polygons(geometry).map(polygon => polygon.map(ring => `M${ring.map(p => `${p[0]},${-p[1]}`).join('L')}Z`).join('')).join('');
  return <div className="zone-review"><strong>{zone.label}</strong><svg role="img" aria-label="Контур нового участка и области пересечения" viewBox={`${x - pad} ${-y - height - pad} ${width + pad * 2} ${height + pad * 2}`}>
    {nearby.map(item => <path key={item.id} d={path(item.geometry)} fill="#e8ecef" stroke="#697687" vectorEffect="non-scaling-stroke" fillRule="evenodd" />)}<path d={path(zone.geometry)} fill="#225cff20" stroke="#225cff" strokeWidth="2" vectorEffect="non-scaling-stroke" fillRule="evenodd" />{overlaps.map(overlap => <path key={overlap.zone_id} d={path(overlap.geometry)} fill="#d92d2070" stroke="#b42318" vectorEffect="non-scaling-stroke" fillRule="evenodd" />)}
  </svg>{preview ? <><span>Площадь: {preview.area_m2.toLocaleString('ru')} м²</span>{overlaps.length ? <><p>Синий — новый контур. Красным выделены пересечения с рабочими участками:</p><ul>{overlaps.map(overlap => { const item = zones.find(item => item.id === overlap.zone_id); return <li key={overlap.zone_id}>{item ? repeatedItemLabel(zones, item) : overlap.label}: {overlap.area_m2.toLocaleString('ru')} м²</li>; })}</ul></> : preview.error ? <p role="alert">{preview.error}</p> : <p>Контур можно сохранить. Существующие посадки не изменяются этим подтверждением.</p>}</> : <p role="status">Проверяем границы участка…</p>}</div>;
}
