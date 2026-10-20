import type { FeatureLike } from 'ol/Feature';
import Feature from 'ol/Feature';
import type { Extent } from 'ol/extent';
import LineString from 'ol/geom/LineString';
import MultiLineString from 'ol/geom/MultiLineString';
import MultiPolygon from 'ol/geom/MultiPolygon';
import Polygon from 'ol/geom/Polygon';
import VectorSource from 'ol/source/Vector';

/**
 * Find a row axis only inside the pointer tolerance.
 *
 * VectorSource keeps an R-tree for its features. Querying the small pointer
 * extent is important on city-scale DXFs: materialising every feature and
 * calling getClosestPoint() on all of them blocks the browser event loop.
 */
export function nearestLineFeature(
  sources: readonly VectorSource[],
  coordinate: [number, number],
  maxDistance: number,
): Feature | undefined {
  const searchExtent: Extent = [
    coordinate[0] - maxDistance,
    coordinate[1] - maxDistance,
    coordinate[0] + maxDistance,
    coordinate[1] + maxDistance,
  ];
  let nearest: { feature: Feature; distance: number } | undefined;
  for (const source of sources) {
    source.forEachFeatureInExtent(searchExtent, (candidate) => {
      const axis = closestAxisPart(candidate, coordinate);
      if (!axis) return;
      const distance = axis.distance;
      if (distance <= maxDistance && (!nearest || distance < nearest.distance))
        nearest = { feature: candidate, distance };
    });
  }
  return nearest?.feature;
}

export function closestAxisPart(
  feature: FeatureLike,
  coordinate: [number, number],
): { coordinates: number[][]; distance: number } | undefined {
  const geometry = feature.getGeometry();
  const lines =
    geometry instanceof LineString
      ? [geometry]
      : geometry instanceof MultiLineString
        ? geometry.getLineStrings()
        : geometry instanceof Polygon
          ? geometry
              .getLinearRings()
              .map((ring) => new LineString(ring.getCoordinates()))
          : geometry instanceof MultiPolygon
            ? geometry
                .getPolygons()
                .flatMap((polygon) =>
                  polygon
                    .getLinearRings()
                    .map((ring) => new LineString(ring.getCoordinates())),
                )
            : [];
  return lines
    .map((line) => {
      const point = line.getClosestPoint(coordinate);
      return {
        coordinates: line.getCoordinates(),
        distance: Math.hypot(
          point[0] - coordinate[0],
          point[1] - coordinate[1],
        ),
      };
    })
    .sort((left, right) => left.distance - right.distance)[0];
}

export function axisCoordinatesFromFeature(
  feature: FeatureLike,
  coordinate: [number, number],
): number[][] | undefined {
  return closestAxisPart(feature, coordinate)?.coordinates;
}
