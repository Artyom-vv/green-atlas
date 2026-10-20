import * as sdk from '../sdk.generated';
import type { WireSchema } from '../contracts/wire';
import type {
  LocationAlignment,
  LocationAlignmentRequest,
  LocationApplyRequest,
  LocationDraft,
  LocationFeatureCollection,
  LocationGeometry,
  LocationResolveRequest,
  LocationServiceSettingsUpdate,
  LocationState,
  LocationSuggestionsRequest,
} from '../contracts/location';
import { ApiClientError } from '../transport/errors';

function geometry(value: Record<string, unknown>): LocationGeometry {
  if (typeof value.type !== 'string')
    throw new ApiClientError(
      'LOCATION_GEOMETRY_INVALID',
      'Геометрия карты не распознана. Повторите загрузку.',
    );
  return value as unknown as LocationGeometry;
}
function collection(
  value?: Record<string, unknown>,
): LocationFeatureCollection {
  if (!value) return { type: 'FeatureCollection', features: [] };
  if (value.type !== 'FeatureCollection' || !Array.isArray(value.features)) {
    throw new ApiClientError(
      'LOCATION_GEOMETRY_INVALID',
      'Контуры карты не распознаны. Повторите загрузку.',
    );
  }
  return value as unknown as LocationFeatureCollection;
}
function draft(value: WireSchema<'LocationDraft'>): LocationDraft {
  return {
    ...value,
    street_candidates: (value.street_candidates ?? []).map((candidate) => ({
      ...candidate,
      geometry: geometry(candidate.geometry),
    })),
    search_geometry: value.search_geometry
      ? geometry(value.search_geometry)
      : null,
    map_buildings: collection(value.map_buildings),
    cad_geometry: collection(value.cad_geometry),
  };
}
function alignment(value: WireSchema<'LocationAlignment'>): LocationAlignment {
  return { ...value, preview_buildings: collection(value.preview_buildings) };
}
function state(value: WireSchema<'LocationState'>): LocationState {
  return {
    ...value,
    draft: value.draft ? draft(value.draft) : null,
    alignment: value.alignment ? alignment(value.alignment) : null,
  };
}
export const locationApi = {
  getLocationServiceSettings: (signal?: AbortSignal) =>
    sdk.getLocationServiceSettings({ signal }),
  updateLocationServiceSettings: (
    body: LocationServiceSettingsUpdate,
    signal?: AbortSignal,
  ) => sdk.updateLocationServiceSettings(body, { signal }),
  resetLocationServiceSettings: (signal?: AbortSignal) =>
    sdk.resetLocationServiceSettings({ signal }),
  getProjectLocation: (id: string, signal?: AbortSignal) =>
    sdk.getProjectLocation(id, { signal }).then(state),
  suggestProjectLocation: (
    id: string,
    body: LocationSuggestionsRequest,
    signal?: AbortSignal,
  ) => sdk.suggestProjectLocation(id, body, { signal }),
  resolveProjectLocation: (
    id: string,
    body: LocationResolveRequest,
    signal?: AbortSignal,
  ) =>
    sdk
      .resolveProjectLocation(id, { buffer_m: 120, ...body }, { signal })
      .then(draft),
  alignProjectLocation: (
    id: string,
    body: LocationAlignmentRequest,
    signal?: AbortSignal,
  ) => sdk.alignProjectLocation(id, body, { signal }).then(alignment),
  applyProjectLocation: (
    id: string,
    body: LocationApplyRequest,
    signal?: AbortSignal,
  ) => sdk.applyProjectLocation(id, body, { signal }).then(state),
};
