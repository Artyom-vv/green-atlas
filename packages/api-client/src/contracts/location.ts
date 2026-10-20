import type { WireSchema } from './wire';

export type LocationPoint = WireSchema<'LocationControlPair'>['cad_xy'];
export type LocationBounds = NonNullable<
  WireSchema<'LocationDraft'>['cad_bounds']
>;
export interface LocationGeometry {
  type: string;
  coordinates?: unknown;
  geometries?: LocationGeometry[];
}
export interface LocationFeatureCollection {
  type: 'FeatureCollection';
  features: Array<{
    type: 'Feature';
    id?: string | number;
    geometry: LocationGeometry | null;
    properties?: Record<string, unknown> | null;
  }>;
}
export type LocationSuggestion = WireSchema<'LocationSuggestion'>;
export type LocationSuggestions = WireSchema<'LocationSuggestions'>;
export type LocationSuggestionsRequest =
  WireSchema<'LocationSuggestionsRequest'>;
export type LocationServiceSettings = WireSchema<'LocationServiceSettings'>;
export type LocationServiceSettingsUpdate =
  WireSchema<'LocationServiceSettingsUpdate'>;
export type LocationControlPair = WireSchema<'LocationControlPair'>;
export type AppliedLocation = WireSchema<'AppliedLocation'>;
export type LocationResolveRequest = Omit<
  WireSchema<'LocationResolveRequest'>,
  'buffer_m'
> & { buffer_m?: number };
export type LocationAlignmentRequest = WireSchema<'LocationAlignmentRequest'>;
export type LocationApplyRequest = WireSchema<'LocationApplyRequest'>;

// GeoJSON dictionaries are narrowed at the client boundary; domain fields use the wire schema.
export type LocationDraft = Omit<
  WireSchema<'LocationDraft'>,
  'street_candidates' | 'search_geometry' | 'map_buildings' | 'cad_geometry'
> & {
  street_candidates: Array<
    Omit<WireSchema<'StreetCandidate'>, 'geometry'> & {
      geometry: LocationGeometry;
    }
  >;
  search_geometry?: LocationGeometry | null;
  map_buildings: LocationFeatureCollection;
  cad_geometry: LocationFeatureCollection;
};
export type LocationAlignment = Omit<
  WireSchema<'LocationAlignment'>,
  'preview_buildings'
> & {
  preview_buildings: LocationFeatureCollection;
};
export type LocationState = Omit<
  WireSchema<'LocationState'>,
  'draft' | 'alignment'
> & {
  draft: LocationDraft | null;
  alignment: LocationAlignment | null;
};
