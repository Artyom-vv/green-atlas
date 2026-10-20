import type {
  LocationAlignment,
  LocationDraft,
  LocationFeatureCollection,
  LocationState,
  LocationSuggestion,
} from '@green/api-client';

export const locationSuggestion: LocationSuggestion = {
  id: 'street',
  label: 'Москва, Парковая улица',
  kind: 'street',
  city: 'Москва',
  street: 'Парковая улица',
  city_fias_id: 'moscow',
  longitude: 37.6,
  latitude: 55.7,
};
const collection: LocationFeatureCollection = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      geometry: {
        type: 'Polygon',
        coordinates: [
          [
            [0, 0],
            [20, 0],
            [20, 15],
            [0, 15],
            [0, 0],
          ],
        ],
      },
    },
  ],
};
export const locationDraft: LocationDraft = {
  id: 'draft-one',
  suggestion: locationSuggestion,
  source_sha256: 'a'.repeat(64),
  geometry_version: 3,
  status: 'ready',
  message: 'Готово',
  street_candidates: [
    {
      id: 'way/1',
      label: 'Парковая улица',
      geometry: {
        type: 'LineString',
        coordinates: [
          [37.6, 55.7],
          [37.602, 55.702],
        ],
      },
    },
  ],
  selected_street_id: 'way/1',
  map_buildings: {
    type: 'FeatureCollection',
    features: [
      {
        type: 'Feature',
        geometry: {
          type: 'Polygon',
          coordinates: [
            [
              [37.6, 55.7],
              [37.601, 55.7],
              [37.601, 55.701],
              [37.6, 55.701],
              [37.6, 55.7],
            ],
          ],
        },
      },
    ],
  },
  cad_geometry: collection,
  cad_bounds: [0, 0, 100, 100],
  provenance: {},
};
export const locationAlignment: LocationAlignment = {
  id: 'alignment-one',
  draft_id: locationDraft.id,
  status: 'candidate',
  method: 'manual',
  control_pairs: [],
  rmse_m: 0.3,
  max_residual_m: 0.4,
  holdout_rmse_m: 0.6,
  inlier_count: 3,
  preview_buildings: collection,
  message: 'Проверьте контуры',
};
export const locationState: LocationState = {
  configured: true,
  applied: null,
  draft: null,
  alignment: null,
  message: null,
};
