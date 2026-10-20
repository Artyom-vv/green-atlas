import { useEffect, useRef, type RefObject } from 'react';
import type Map from 'ol/Map';
import type { CadGeometryLayers } from './cadGeometryDisplay';
import { readSourceOverview, visibleOverviewSvg } from './sourceOverview';
import { createSourceOverviewDisplay } from './sourceOverviewDisplay';
import type { CadAppearanceMode } from '../../../model/cadSource';
import { viewportGeometryIdentity } from '../../../model/viewportGeometryContext';

/** Обзор хранит полное полученное отображение, а выбор использует точные объекты */
export function useSourceOverviewLayer({
  mapRef,
  targetRef,
  layersRef,
  geometry,
  hiddenNames,
  disabled,
  renderMode = 'design',
}: {
  mapRef: RefObject<Map | null>;
  targetRef: RefObject<HTMLDivElement | null>;
  layersRef: RefObject<CadGeometryLayers | null>;
  geometry?: Record<string, unknown>;
  hiddenNames?: string[];
  disabled: boolean;
  renderMode?: CadAppearanceMode;
}) {
  const overview = readSourceOverview(geometry);
  const overviewSvg = overview?.svg;
  const overviewFeatures = overview?.rendered_features;
  const hiddenNamesKey = JSON.stringify(hiddenNames ?? []);
  const context = geometry?.viewportContext as
    Record<string, unknown> | undefined;
  const knownSnapshot =
    typeof context?.projectId === 'string' &&
    context.projectId.length > 0 &&
    typeof context.sourceKey === 'string' &&
    context.sourceKey.length > 0 &&
    typeof context.geometryVersion === 'number' &&
    Number.isInteger(context.geometryVersion) &&
    context.geometryVersion >= 0;
  const scope =
    knownSnapshot && geometry
      ? viewportGeometryIdentity(geometry).scope
      : overviewSvg;
  const owner = useRef<
    | {
        scope: string | undefined;
        display: ReturnType<typeof createSourceOverviewDisplay>;
      }
    | undefined
  >(undefined);
  useEffect(() => {
    const map = mapRef.current;
    const target = targetRef.current;
    if (!map || !overviewSvg || disabled) {
      owner.current?.display.dispose();
      owner.current = undefined;
      return;
    }
    if (owner.current?.scope !== scope || owner.current?.display.map !== map) {
      owner.current?.display.dispose();
      owner.current = {
        scope,
        display: createSourceOverviewDisplay(map, target, layersRef.current),
      };
    }
    owner.current.display.update(
      visibleOverviewSvg(overviewSvg, JSON.parse(hiddenNamesKey) as string[]),
      renderMode,
      JSON.stringify([hiddenNamesKey, renderMode]),
      overviewFeatures ?? 0,
    );
  }, [
    mapRef,
    targetRef,
    layersRef,
    overviewSvg,
    overviewFeatures,
    hiddenNamesKey,
    disabled,
    renderMode,
    scope,
  ]);
  useEffect(
    () => () => {
      owner.current?.display.dispose();
      owner.current = undefined;
    },
    [],
  );
}
