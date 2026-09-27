import {
  ApiClientError,
  type PlacementCheck,
  type PlacementCheckRequest,
  type PlanObjectCreate,
} from '@green/api-client';

export type Coordinate = [number, number];
export interface SinglePlacementContext {
  projectId: string;
  planVersion?: number;
  geometryVersion: number;
  stateVersion: number;
  active: boolean;
  kind: PlanObjectCreate['kind'] | undefined;
  speciesRevisionId?: string;
  sizeClass?: Exclude<PlanObjectCreate['size_class'], 'unspecified'>;
}
export interface PlacementCandidate {
  projectId: string;
  expectedStateVersion: number;
  object: PlanObjectCreate;
  check: PlacementCheckRequest;
}

/** The hover and explicit click use exactly the same frozen candidate fields. */
export function placementCandidate(
  context: SinglePlacementContext,
  coordinate: Coordinate,
): PlacementCandidate | undefined {
  if (!context.kind || context.planVersion === undefined) return undefined;
  const object: PlanObjectCreate = {
    kind: context.kind,
    x: coordinate[0],
    y: coordinate[1],
    species_revision_id: context.speciesRevisionId,
    size_class: context.sizeClass ?? 'standard',
  };
  return {
    projectId: context.projectId,
    expectedStateVersion: context.stateVersion,
    object,
    check: {
      ...object,
      base_plan_version: context.planVersion,
      geometry_version: context.geometryVersion,
      state_version: context.stateVersion,
    },
  };
}

export const sameCoordinate = (a: Coordinate | undefined, b: Coordinate) =>
  a?.[0] === b[0] && a?.[1] === b[1];
// A successful own commit changes versions but preserves the user's intent.
export const placementIntentKey = (o: SinglePlacementContext) =>
  JSON.stringify([
    o.projectId,
    o.active,
    o.kind,
    o.speciesRevisionId,
    o.sizeClass ?? 'standard',
  ]);
export const placementContextKey = (o: SinglePlacementContext) =>
  JSON.stringify([
    o.projectId,
    o.planVersion,
    o.geometryVersion,
    o.stateVersion,
    o.active,
    o.kind,
    o.speciesRevisionId,
    o.sizeClass ?? 'standard',
  ]);
export const stalePlacementBasis = (
  check: PlacementCheck,
  context: SinglePlacementContext,
) =>
  check.plan_version !== context.planVersion ||
  check.geometry_version !== context.geometryVersion ||
  check.state_version !== context.stateVersion;
export const placementSavedNotice =
  'Посадка сохранена. Выберите следующее место.';
export const placementRefreshFailure = (error: Error) =>
  new ApiClientError(
    'PLACEMENT_SAVED_REFRESH_FAILED',
    'Посадка сохранена, но не удалось обновить карту.',
    {},
    { cause: error.message },
  );
export const stalePlacementError = () =>
  new ApiClientError(
    'STALE_PLACEMENT_BASIS',
    'Проект изменился. Обновите данные перед проверкой посадки.',
  );
export const placementVersionError = (error: Error) =>
  error instanceof ApiClientError &&
  [
    'STALE_PLACEMENT_BASIS',
    'PROJECT_VERSION_CONFLICT',
    'PLAN_VERSION_CONFLICT',
  ].includes(error.code);
export const placementErrorFrom = (error: unknown) =>
  error instanceof Error ? error : new Error(String(error));
