import type { FeatureLike } from 'ol/Feature';
import type Geometry from 'ol/geom/Geometry';
import GeoJSON from 'ol/format/GeoJSON';
import LineString from 'ol/geom/LineString';
import MultiPolygon from 'ol/geom/MultiPolygon';
import Polygon from 'ol/geom/Polygon';
import type { MapAreaTarget } from '../../model/mapContracts';
import { projection } from './projection';

interface PolygonParts {
  revision: number;
  polygons: Polygon[];
}

interface PolygonSnapshot {
  revision: number;
  geometry: NonNullable<MapAreaTarget['geometry']>;
}

const parts = new WeakMap<Geometry, PolygonParts>();
const closedLineAreas = new WeakMap<LineString, PolygonParts>();
const snapshots = new WeakMap<Polygon, PolygonSnapshot>();
const format = new GeoJSON();

function polygonOfClosedLine(geometry: LineString) {
  const revision = geometry.getRevision();
  const cached = closedLineAreas.get(geometry);
  if (cached?.revision === revision) return cached.polygons;
  const coordinates = geometry
    .getCoordinates()
    .map((coordinate) => [coordinate[0], coordinate[1]]);
  const first = coordinates[0];
  const last = coordinates.at(-1);
  const closed =
    coordinates.length >= 4 &&
    first &&
    last &&
    Number.isFinite(first[0]) &&
    Number.isFinite(first[1]) &&
    Math.hypot(first[0] - last[0], first[1] - last[1]) <= 1e-7;
  const polygon = closed ? new Polygon([coordinates]) : undefined;
  const polygons =
    polygon && Math.abs(polygon.getArea()) > 1e-8 ? [polygon] : [];
  closedLineAreas.set(geometry, { revision, polygons });
  return polygons;
}

/** Copies of multipart rings live only as long as their current OL geometry. */
function polygonsOf(geometry: ReturnType<FeatureLike['getGeometry']>) {
  if (geometry instanceof Polygon) return [geometry];
  // AutoCAD 3D polylines preserve their line identity even when closed. For
  // interaction only, expose their enclosed area without mutating source CAD.
  if (geometry instanceof LineString) return polygonOfClosedLine(geometry);
  if (!(geometry instanceof MultiPolygon)) return [];
  const revision = geometry.getRevision();
  const cached = parts.get(geometry);
  if (cached?.revision === revision) return cached.polygons;
  const polygons = geometry.getPolygons();
  parts.set(geometry, { revision, polygons });
  return polygons;
}

/** Preserve the existing first-contained / nearest-boundary component choice. */
export function polygonAtCoordinate(
  geometry: ReturnType<FeatureLike['getGeometry']>,
  coordinate?: [number, number],
  nearest = true,
): Polygon | undefined {
  const polygons = polygonsOf(geometry);
  if (!coordinate) return polygons[0];
  // The only component wins both containment and nearest fallback.
  if (nearest && polygons.length === 1) return polygons[0];
  const contained = polygons.find((polygon) =>
    polygon.intersectsCoordinate(coordinate),
  );
  if (contained || !nearest) return contained;
  return polygons
    .map((polygon) => {
      const closest = polygon.getClosestPoint(coordinate);
      return {
        polygon,
        distance: Math.hypot(
          closest[0] - coordinate[0],
          closest[1] - coordinate[1],
        ),
      };
    })
    .sort((left, right) => left.distance - right.distance)[0]?.polygon;
}

/** Shared immutable snapshot; consumers construct new geometry when editing. */
export function polygonSnapshot(polygon: Polygon) {
  const revision = polygon.getRevision();
  const cached = snapshots.get(polygon);
  if (cached?.revision === revision) return cached.geometry;
  const geometry = format.writeGeometryObject(polygon, {
    featureProjection: projection,
    dataProjection: projection,
  }) as NonNullable<MapAreaTarget['geometry']>;
  snapshots.set(polygon, { revision, geometry });
  return geometry;
}
