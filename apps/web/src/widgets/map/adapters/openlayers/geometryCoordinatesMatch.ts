import type Geometry from 'ol/geom/Geometry';
import type SimpleGeometry from 'ol/geom/SimpleGeometry';
import type Polygon from 'ol/geom/Polygon';
import type MultiLineString from 'ol/geom/MultiLineString';
import type MultiPolygon from 'ol/geom/MultiPolygon';
import type GeometryCollection from 'ol/geom/GeometryCollection';

/** Compare the incoming local coordinates with OL's existing numeric storage. */
export function geometryCoordinatesMatch(
  raw: Record<string, unknown>,
  geometry: Geometry,
): boolean {
  if (!raw || typeof raw !== 'object') return false;
  if (raw.type !== geometry.getType()) return false;
  if (raw.type === 'GeometryCollection') {
    const children = (geometry as GeometryCollection).getGeometriesArray();
    const incoming = raw.geometries;
    return (
      Array.isArray(incoming) &&
      incoming.length === children.length &&
      children.every((child, index) =>
        geometryCoordinatesMatch(incoming[index], child),
      )
    );
  }
  const simple = geometry as SimpleGeometry;
  const flat = simple.getFlatCoordinates();
  const stride = simple.getStride();
  let offset = 0;
  const position = (value: unknown): boolean => {
    if (!Array.isArray(value) || value.length !== stride) return false;
    for (let index = 0; index < stride; index += 1)
      if (value[index] !== flat[offset++]) return false;
    return true;
  };
  const sequence = (value: unknown, end: number): boolean => {
    if (!Array.isArray(value) || value.length * stride !== end - offset)
      return false;
    for (const point of value) if (!position(point)) return false;
    return true;
  };
  const rings = (value: unknown, ends: number[]): boolean => {
    if (!Array.isArray(value) || value.length !== ends.length) return false;
    for (let index = 0; index < ends.length; index += 1)
      if (!sequence(value[index], ends[index])) return false;
    return true;
  };
  switch (raw.type) {
    case 'Point':
      return (
        Array.isArray(raw.coordinates) &&
        (flat.length === 0
          ? raw.coordinates.length === 0
          : position(raw.coordinates)) &&
        offset === flat.length
      );
    case 'LineString':
    case 'MultiPoint':
      return sequence(raw.coordinates, flat.length);
    case 'Polygon':
    case 'MultiLineString':
      return (
        rings(
          raw.coordinates,
          (simple as Polygon | MultiLineString).getEnds(),
        ) && offset === flat.length
      );
    case 'MultiPolygon': {
      const endss = (simple as MultiPolygon).getEndss();
      if (
        !Array.isArray(raw.coordinates) ||
        raw.coordinates.length !== endss.length
      )
        return false;
      for (let index = 0; index < endss.length; index += 1)
        if (!rings(raw.coordinates[index], endss[index])) return false;
      return offset === flat.length;
    }
    default:
      return false;
  }
}
