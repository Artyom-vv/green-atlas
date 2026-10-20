export const MAX_CACHED_GEOMETRY_FEATURES = 25_000;
export const MAX_SNAP_TARGET_FEATURES = 3_500;
export const GEOMETRY_CHUNK_SIZE = 350;

/** Camera framing and animation policy; all distances here are CSS pixels. */
export const VIEWPORT_CAMERA_POLICY = {
  maxZoom: 24,
  overviewPadding: [28, 28, 28, 28],
  geometryPadding: [72, 72, 72, 72],
  selectionPadding: [96, 96, 96, 96],
  focusDurationMs: 180,
  planFocusDurationMs: 220,
  zoomDurationMs: 140,
};
