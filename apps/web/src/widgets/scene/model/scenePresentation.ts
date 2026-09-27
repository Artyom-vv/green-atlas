import type { SceneSnapshot } from '@green/api-client';
import { countLabel } from '@/shared/format/countLabel';
import {
  SCENE_TELEMETRY_ATTRIBUTES,
  type SceneRenderTelemetry,
} from '@/widgets/scene/model/sceneRenderContract';

export const SCENE_GROWTH_LABELS: Readonly<Record<string, string>> = {
  planting: 'Посадочный размер',
  young: 'Молодое растение',
  developing: 'Формирующаяся крона',
  mature: 'Зрелая форма',
};

export function sceneHorizonLabel(year: number) {
  return year === 0
    ? 'сейчас'
    : `через ${countLabel(year, 'год', 'года', 'лет')}`;
}

export function sceneSourceFacts(
  snapshot: SceneSnapshot | undefined,
  zoneCount: number,
) {
  return [
    [
      'Координаты',
      snapshot?.georeference_status === 'confirmed'
        ? 'Геопривязка подтверждена'
        : snapshot?.georeference_status === 'declared'
          ? 'Геопривязка заявлена'
          : 'Локальные координаты DXF',
    ],
    [
      'Высоты зданий',
      snapshot?.building_heights_status === 'confirmed'
        ? `${snapshot.building_height_confirmed_count} высот из DXF`
        : snapshot?.building_heights_status === 'estimated'
          ? 'Высоты OSM и оценки по этажности'
          : 'Высоты не заданы',
    ],
    [
      'Рельеф',
      snapshot?.terrain_status === 'confirmed'
        ? 'Подтверждён в DXF'
        : snapshot?.terrain_status === 'estimated'
          ? 'DEM, оценочные высоты'
          : 'Высоты не заданы',
    ],
    ['Рабочие участки', zoneCount],
    ['Посадки', snapshot?.objects.length ?? 0],
  ] as const;
}

export function sceneTelemetryAttributes(telemetry?: SceneRenderTelemetry) {
  return Object.fromEntries(
    Object.entries(SCENE_TELEMETRY_ATTRIBUTES).map(([key, attribute]) => [
      attribute,
      key === 'status'
        ? telemetry
          ? 'ready'
          : 'warming-up'
        : (telemetry?.[
            key as Exclude<keyof typeof SCENE_TELEMETRY_ATTRIBUTES, 'status'>
          ] ?? 0),
    ]),
  );
}
