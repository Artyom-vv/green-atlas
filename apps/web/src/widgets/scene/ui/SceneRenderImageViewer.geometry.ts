export interface ImageSize {
  width: number;
  height: number;
}

export interface ImagePoint {
  x: number;
  y: number;
}

export interface ImageView extends ImagePoint {
  scale: number;
  mode: 'fit' | 'custom';
}

export const MAX_IMAGE_SCALE = 8;

export function fitImageScale(image: ImageSize, viewport: ImageSize) {
  if (viewport.width <= 0 || viewport.height <= 0) return 1;
  return Math.min(
    1,
    viewport.width / image.width,
    viewport.height / image.height,
  );
}

export function imagePanLimits(
  image: ImageSize,
  viewport: ImageSize,
  scale: number,
) {
  return {
    x: Math.max(0, (image.width * scale - viewport.width) / 2),
    y: Math.max(0, (image.height * scale - viewport.height) / 2),
  };
}

export function clampImageView(
  view: ImageView,
  image: ImageSize,
  viewport: ImageSize,
): ImageView {
  if (view.mode === 'fit') {
    return { mode: 'fit', scale: fitImageScale(image, viewport), x: 0, y: 0 };
  }
  const scale = Math.max(
    Math.min(0.1, fitImageScale(image, viewport)),
    Math.min(MAX_IMAGE_SCALE, view.scale),
  );
  const limits = imagePanLimits(image, viewport, scale);
  return {
    ...view,
    scale,
    x: limits.x === 0 ? 0 : Math.max(-limits.x, Math.min(limits.x, view.x)),
    y: limits.y === 0 ? 0 : Math.max(-limits.y, Math.min(limits.y, view.y)),
  };
}

/** Anchor coordinates are measured from the viewport centre, like the image offset. */
export function zoomImageView(
  view: ImageView,
  nextScale: number,
  anchor: ImagePoint,
  image: ImageSize,
  viewport: ImageSize,
): ImageView {
  const scale = Math.max(
    Math.min(0.1, fitImageScale(image, viewport)),
    Math.min(MAX_IMAGE_SCALE, nextScale),
  );
  const ratio = scale / view.scale;
  return clampImageView(
    {
      mode: 'custom',
      scale,
      x: anchor.x - (anchor.x - view.x) * ratio,
      y: anchor.y - (anchor.y - view.y) * ratio,
    },
    image,
    viewport,
  );
}
