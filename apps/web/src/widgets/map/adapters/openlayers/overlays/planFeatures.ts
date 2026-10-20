import type {
  ChangeSetPreview,
  PlanChangeSetDraft,
  PlanObject,
  ValidationIssue,
} from '@green/api-client';
import type { GrowthOverlayForecast } from '@/entities/planting-forecast/model/growthOverlayForecasts';
import { workingEnvelopeRadius } from '@/entities/planting-forecast/model/growthForecast';
import {
  isGeneralDataNotice,
  issueObjectIds,
} from '@/entities/validation/model/issueGroups';
import Feature from 'ol/Feature';
import Circle from 'ol/geom/Circle';
import LineString from 'ol/geom/LineString';
import Point from 'ol/geom/Point';
import VectorSource from 'ol/source/Vector';

/** Одна прогнозная крона и рабочая корневая зона выбранной посадки
 * используют те же границы диапазонов, что и локальный расчёт. */
export function growthEnvelopeFeatures(
  forecasts: readonly GrowthOverlayForecast[],
): Feature[] {
  const singleSelection =
    forecasts.filter((item) => item.selected).length === 1;
  return forecasts.flatMap(({ object, canopy, roots, selected }) => {
    const result: Feature[] = [];
    for (const [envelopeStyle, radius] of [
      ['canopy', workingEnvelopeRadius('canopy', canopy)],
      [
        'roots',
        singleSelection && selected
          ? workingEnvelopeRadius('roots', roots)
          : undefined,
      ],
    ] as const) {
      if (radius === undefined || radius <= 0) continue;
      const feature = new Feature({
        geometry: new Circle([object.x, object.y], radius),
        objectId: object.id,
        envelopeStyle,
      });
      feature.setId(`growth-${envelopeStyle}-${object.id}`);
      result.push(feature);
    }
    return result;
  });
}

export function syncPlanFeatures(
  source: VectorSource,
  objects: PlanObject[],
  issues?: readonly ValidationIssue[],
): void {
  const issuesByObjectId = new globalThis.Map<string, ValidationIssue[]>();
  for (const issue of issues ?? []) {
    for (const id of issueObjectIds(issue)) {
      const associated = issuesByObjectId.get(id) ?? [];
      associated.push(issue);
      issuesByObjectId.set(id, associated);
    }
  }
  const incoming = new Set(
    objects.flatMap((object) => (object.id ? [object.id] : [])),
  );
  for (const feature of source.getFeatures()) {
    const id = String(feature.getId() ?? '');
    if (!incoming.has(id)) source.removeFeature(feature);
  }
  for (const object of objects) {
    if (!object.id) continue;
    const objectIssues = issuesByObjectId.get(object.id) ?? [];
    const radius = object.layout_radius_m ?? object.radius;
    let feature = source.getFeatureById(object.id) as Feature | null;
    if (!feature) {
      feature = new Feature();
      feature.setId(object.id);
      source.addFeature(feature);
    }
    const geometry = feature.getGeometry();
    if (
      !(geometry instanceof Circle) ||
      geometry.getRadius() !== radius ||
      geometry.getCenter()[0] !== object.x ||
      geometry.getCenter()[1] !== object.y
    ) {
      feature.setGeometry(new Circle([object.x, object.y], radius));
    }
    const marker = feature.get('markerGeometry');
    if (
      !(marker instanceof Point) ||
      marker.getCoordinates()[0] !== object.x ||
      marker.getCoordinates()[1] !== object.y
    ) {
      feature.set('markerGeometry', new Point([object.x, object.y]), true);
    }
    feature.setProperties(
      {
        kind: object.kind,
        status: object.status,
        objectId: object.id,
        plantingZoneId: object.planting_zone_id,
        locked: object.locked,
        // Общие сведения не окрашивают каждую посадку. Для адресных замечаний,
        // ошибок и статуса без основания сохраняем исходное отображение.
        _generalNoticeOnly:
          object.status === 'warning' &&
          objectIssues.length > 0 &&
          objectIssues.every(
            (issue) =>
              issue.severity !== 'error' && isGeneralDataNotice(issue.code),
          ),
      },
      true,
    );
    feature.changed();
  }
}

export function changePreviewFeatures(
  objects: readonly PlanObject[],
  preview: ChangeSetPreview,
  draft?: PlanChangeSetDraft,
): Feature[] {
  const currentById = new globalThis.Map(
    objects.flatMap((object) =>
      object.id ? [[object.id, object] as const] : [],
    ),
  );
  const resultByObjectId = new globalThis.Map(
    (preview.candidate_results ?? []).flatMap((result) =>
      result.object_id ? [[result.object_id, result] as const] : [],
    ),
  );
  const moves: Array<{
    from: [number, number];
    to: [number, number];
    kind: string;
    status: string;
    code?: string;
  }> = [];
  const candidates = [
    ...(preview.additions ?? []),
    ...(preview.updates ?? []),
  ].flatMap((object) => {
    if (!object.id) return [] as Feature[];
    const radius = object.layout_radius_m ?? object.radius;
    const result = resultByObjectId.get(object.id);
    const status = result?.status ?? 'allowed';
    const current = currentById.get(object.id);
    const moved = current && (current.x !== object.x || current.y !== object.y);
    if (moved)
      moves.push({
        from: [current.x, current.y],
        to: [object.x, object.y],
        kind: object.kind,
        status,
        code: result?.code,
      });
    const candidate = new Feature({
      geometry: new Circle([object.x, object.y], radius),
      markerGeometry: new Point([object.x, object.y]),
      objectId: object.id,
      kind: object.kind,
      candidateStatus: status,
      candidateCode: result?.code ?? 'POSITION_ACCEPTED',
      candidateReason: result?.reason ?? 'Позиция проходит текущую проверку',
      candidateSuggestedAction: result?.suggested_action,
      previewRole: 'candidate',
    });
    candidate.setId(`change-preview-${object.id}`);
    return [candidate];
  });

  // Rejected additions are deliberately absent from the server's additions.
  // Draw their attempted positions separately; never add them to the saved
  // preview or change can_apply. Match only a draft tied to this preview.
  for (const result of preview.candidate_results ?? []) {
    if (result.type !== 'add' || result.status !== 'blocked') continue;
    if (
      result.object_id &&
      candidates.some((feature) => feature.get('objectId') === result.object_id)
    )
      continue;
    const operation = draft?.operations[result.operation_index];
    if (operation?.type !== 'add') continue;
    const object = operation.object;
    const radius = object.layout_radius_m ?? object.radius;
    if (!radius) continue;
    const objectId =
      result.object_id ?? `rejected-${preview.id}-${result.operation_index}`;
    const candidate = new Feature({
      geometry: new Circle([object.x, object.y], radius),
      markerGeometry: new Point([object.x, object.y]),
      objectId,
      kind: object.kind,
      candidateStatus: result.status,
      candidateCode: result.code,
      candidateReason: result.reason,
      candidateSuggestedAction: result.suggested_action,
      previewRole: 'candidate',
    });
    candidate.setId(`change-preview-${objectId}`);
    candidates.push(candidate);
  }
  for (const id of preview.deletion_ids ?? []) {
    const object = currentById.get(id);
    if (!object) continue;
    const marker = new Feature({
      geometry: new Point([object.x, object.y]),
      objectId: id,
      kind: object.kind,
      previewRole: 'delete',
    });
    marker.setId(`change-delete-${id}`);
    candidates.push(marker);
  }
  if (!moves.length) return candidates;

  // A group is translated rigidly. One centroid trail communicates the
  // before/after relationship without turning a 70-object selection into a
  // thicket of overlapping guide lines. Per-object candidates still retain
  // their own validation colour at the destination.
  const centroid = (coordinates: Array<[number, number]>): [number, number] => [
    coordinates.reduce((sum, point) => sum + point[0], 0) / coordinates.length,
    coordinates.reduce((sum, point) => sum + point[1], 0) / coordinates.length,
  ];
  const from = centroid(moves.map((move) => move.from));
  const to = centroid(moves.map((move) => move.to));
  const groupStatus = moves.some((move) => move.status === 'blocked')
    ? 'blocked'
    : moves.some(
          (move) =>
            move.status === 'soft_conflict' || move.status === 'unknown',
        )
      ? 'soft_conflict'
      : 'allowed';
  const groupNoticeCode = moves.every(
    (move) =>
      move.status === 'allowed' ||
      (move.status !== 'blocked' && isGeneralDataNotice(move.code)),
  )
    ? moves.find((move) => isGeneralDataNotice(move.code))?.code
    : undefined;
  const path = new Feature({
    geometry: new LineString([from, to]),
    kind: moves[0].kind,
    candidateStatus: groupStatus,
    candidateCode: groupNoticeCode,
    previewRole: 'move-path',
  });
  const origin = new Feature({
    geometry: new Point(from),
    kind: moves[0].kind,
    candidateStatus: groupStatus,
    candidateCode: groupNoticeCode,
    previewRole: 'move-origin',
  });
  path.setId('change-preview-group-path');
  origin.setId('change-preview-group-origin');
  return [path, origin, ...candidates];
}
