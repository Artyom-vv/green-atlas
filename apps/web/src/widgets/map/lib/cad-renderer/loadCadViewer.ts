import type { CadViewer } from './sdkTypes';

export const CAD_FONT_PATH = 'assets/fonts/NotoSans-Regular.ttf';

export async function loadCadViewer(
  host: HTMLElement,
  fileEncoding = 'utf-8',
): Promise<CadViewer> {
  const { DxfViewer } = await import('dxf-viewer');
  return new DxfViewer(host, {
    autoResize: false,
    canvasWidth: 1,
    canvasHeight: 1,
    canvasAlpha: true,
    clearAlpha: 0,
    clearColor: DxfViewer.DefaultOptions.clearColor.clone().setHex(0xf7f8f9),
    retainParsedDxf: false,
    fileEncoding,
  });
}

export function createCadWorker(): Worker {
  return new Worker(new URL('./cad.worker.ts', import.meta.url), {
    type: 'module',
  });
}

export function cadFontUrl(): string {
  return new URL(
    `${import.meta.env.BASE_URL}${CAD_FONT_PATH}`,
    window.location.href,
  ).href;
}
