import { useCallback, useEffect, useState, type FC, type Ref } from 'react';
import type { CadRenderState } from '../model/cadSource';
import { CadMapActivity } from './CadMapActivity';
import { useOpenLayersViewport } from '../adapters/openlayers/useOpenLayersViewport';
import type { MapViewportOptions } from '../model/mapViewportOptions';
import type { MapViewportHandle } from '../model/mapContracts';
import { mapViewport } from './mapViewportVariants';
import { preventMapLookup } from '../lib/preventMapLookup';
import '../adapters/openlayers/openlayers.css';

export interface MapViewportProps extends MapViewportOptions {
  ref?: Ref<MapViewportHandle>;
}

export const MapViewport: FC<MapViewportProps> = ({ ref, ...options }) => {
  const [cadState, setCadState] = useState<CadRenderState>();
  const onCadRenderState = options.onCadRenderState;
  const handleCadState = useCallback(
    (state: CadRenderState) => {
      setCadState(state);
      onCadRenderState?.(state);
    },
    [onCadRenderState],
  );
  const { targetRef, helpId, growthOverlaySummary } = useOpenLayersViewport(
    { ...options, onCadRenderState: handleCadState },
    ref,
  );
  useEffect(() => {
    const target = targetRef.current;
    if (target) return preventMapLookup(target);
  }, [targetRef]);
  const { tool, growthHorizon, brushStrokes } = options;
  return (
    <>
      <div
        ref={targetRef}
        data-map-view
        data-tool={tool}
        className={mapViewport({ tool })}
        role="region"
        tabIndex={0}
        aria-label="Карта проекта озеленения"
        aria-describedby={helpId}
        data-geometry-ready="false"
        data-growth-horizon={growthHorizon ?? ''}
        data-growth-overlay={growthOverlaySummary}
        data-brush-stroke-count={brushStrokes?.length ?? 0}
      >
        <span
          role="status"
          className="sr-only"
          aria-live="polite"
          aria-label="Прогнозный слой карты"
        >
          {growthOverlaySummary
            ? `Прогноз посадок: ${growthHorizon ? `через ${growthHorizon} лет` : 'сейчас'}`
            : 'Прогнозный слой недоступен'}
        </span>
      </div>
      {options.cadSource && <CadMapActivity state={cadState} />}
      <span id={helpId} className="sr-only">
        Стрелки перемещают карту, плюс и минус меняют масштаб
      </span>
    </>
  );
};
