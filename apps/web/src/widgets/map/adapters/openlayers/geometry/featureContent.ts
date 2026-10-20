import type { RawViewportFeature } from './readViewportFeature';
import type Geometry from 'ol/geom/Geometry';
import { geometryCoordinatesMatch } from './geometryCoordinatesMatch';

interface GeometryContent {
  attributes: string;
  geometries?: GeometryContent[];
}
export interface FeatureContent {
  attributes: string;
  geometry: GeometryContent;
}

function captureGeometry(geometry: Record<string, unknown>): GeometryContent {
  const { coordinates, geometries, ...attributes } = geometry;
  void coordinates;
  return {
    attributes: JSON.stringify(attributes),
    geometries: Array.isArray(geometries)
      ? geometries.map((child) =>
          captureGeometry(child as Record<string, unknown>),
        )
      : undefined,
  };
}

function geometryMatches(
  content: GeometryContent,
  geometry: Record<string, unknown>,
): boolean {
  if (!geometry || typeof geometry !== 'object') return false;
  const { coordinates, geometries, ...attributes } = geometry;
  void coordinates;
  if (content.attributes !== JSON.stringify(attributes)) return false;
  if (content.geometries) {
    return (
      Array.isArray(geometries) &&
      geometries.length === content.geometries.length &&
      content.geometries.every((child, index) =>
        geometryMatches(child, geometries[index] as Record<string, unknown>),
      )
    );
  }
  return !Array.isArray(geometries);
}

export function captureFeatureContent(raw: RawViewportFeature): FeatureContent {
  const { geometry, ...attributes } = raw;
  return {
    attributes: JSON.stringify(attributes),
    geometry: captureGeometry(geometry!),
  };
}

export function featureContentMatches(
  content: FeatureContent,
  raw: RawViewportFeature,
  shape: Geometry | undefined,
): boolean {
  const { geometry, ...attributes } = raw;
  return (
    content.attributes === JSON.stringify(attributes) &&
    Boolean(
      geometry &&
      shape &&
      geometryMatches(content.geometry, geometry) &&
      geometryCoordinatesMatch(geometry, shape),
    )
  );
}
