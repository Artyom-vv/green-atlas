import type VectorImageLayer from 'ol/layer/VectorImage';

export interface CadGeometryLayers {
  base: VectorImageLayer;
  zones: VectorImageLayer;
  constraints: VectorImageLayer;
}

/** Only visual ownership changes; all vector sources keep picking/snap data. */
export function cadGeometryDisplay(layers: CadGeometryLayers | null) {
  const original = layers
    ? Object.values(layers).map((layer) => ({
        layer,
        visible: layer.getVisible(),
      }))
    : [];
  const constraintStyle = layers?.constraints.getStyle();
  const style = layers?.constraints.getStyleFunction();
  return {
    ready(ownership: 'source' | 'all') {
      if (!layers) return;
      if (ownership === 'all') {
        for (const { layer } of original) layer.setVisible(false);
        return;
      }
      layers.base.setVisible(false);
      // Utility/water/restricted are raw DXF. Computed forbidden constraints
      // retain their existing style; hovered constraints use a separate layer.
      layers.constraints.setStyle((feature, resolution) =>
        feature.get('kind') === 'forbidden'
          ? style?.(feature, resolution)
          : undefined,
      );
    },
    restore() {
      for (const { layer, visible } of original) layer.setVisible(visible);
      if (layers) layers.constraints.setStyle(constraintStyle);
    },
  };
}
