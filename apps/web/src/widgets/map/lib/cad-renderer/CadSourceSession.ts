import { assertAsciiDxf } from './assertAsciiDxf';
import { CadFrameCache } from './CadFrameCache';
import { getReadyInfo, renderCadFrame, type CadFrame } from './camera';
import { cadFontUrl, createCadWorker, loadCadViewer } from './loadCadViewer';
import type { CadViewer } from './sdkTypes';
import type { CadSourceLayerOptions, CadSourceReadyInfo } from './types';
import { attachCadGpuTiming } from './performance/attachCadGpuTiming';
import { CadAppearance } from './appearance/CadAppearance';
import type { CadAppearanceMode, CadLayerRoles } from '../../model/cadSource';
import { prepareCadFrustumCulling } from './culling/prepareCadFrustumCulling';

export class CadSourceSession {
  private viewer: CadViewer | null = null;
  private worker: Worker | null = null;
  private loaded = false;
  private disposed = false;
  private hidden = new Set<string>();
  private appliedHidden = new Set<string>();
  private dimensions = [1, 1];
  private frames = new CadFrameCache();
  private appearance: CadAppearance | undefined;
  private appearanceMode: CadAppearanceMode = 'cad';
  private layerRoles: CadLayerRoles = {};
  private culling: ReturnType<typeof prepareCadFrustumCulling> | undefined;
  private abort = new AbortController();
  private stopLoad: () => void = () => undefined;
  private stopGpuTiming: (() => void) | undefined;

  constructor(
    private host: HTMLElement,
    private options: CadSourceLayerOptions,
    private changed: () => void,
  ) {}

  load(): Promise<CadSourceReadyInfo | null> {
    if (this.disposed) return Promise.resolve(null);
    const stopped = new Promise<null>((resolve) => {
      this.stopLoad = () => resolve(null);
    });
    return Promise.race([this.initialize(), stopped]);
  }

  private async initialize(): Promise<CadSourceReadyInfo | null> {
    try {
      await assertAsciiDxf(this.options.assetUrl, this.abort.signal);
      if (this.disposed) return null;
      const viewer = await loadCadViewer(this.host, this.options.fileEncoding);
      if (this.disposed) {
        viewer.Destroy();
        viewer.GetCanvas().remove();
        return null;
      }
      this.viewer = viewer;
      if (!viewer.HasRenderer())
        throw new Error('Не удалось создать WebGL-карту CAD.');
      const canvas = viewer.GetCanvas();
      canvas.style.pointerEvents = 'none';
      canvas.tabIndex = -1;
      canvas.addEventListener('webglcontextlost', this.onContextLost);
      let rejectWorker: (error: Error) => void = () => undefined;
      const workerFailure = new Promise<never>((_, reject) => {
        rejectWorker = reject;
      });
      const complete = await Promise.race([
        viewer
          .Load({
            url: this.options.assetUrl,
            fonts: [cadFontUrl()],
            workerFactory: () => {
              const worker = createCadWorker();
              this.worker = worker;
              worker.addEventListener('error', (event) =>
                rejectWorker(new Error(event.message || 'Ошибка CAD worker.')),
              );
              worker.addEventListener('messageerror', () =>
                rejectWorker(
                  new Error('Не удалось получить геометрию CAD worker.'),
                ),
              );
              return worker;
            },
          })
          .then(() => true),
        workerFailure,
      ]);
      if (!complete || this.disposed) return null;
      this.worker = null; // Load awaits the SDK's worker destruction.
      if (
        import.meta.env.DEV &&
        new URLSearchParams(window.location.search).get('mapDiagnostics') ===
          '1'
      ) {
        this.stopGpuTiming = attachCadGpuTiming(viewer, this.host);
      }
      const info = getReadyInfo(viewer, this.options.unitScaleToM);
      this.appearance = new CadAppearance(viewer.GetScene().children);
      this.loaded = true;
      this.applyVisibility();
      this.applyAppearance();
      this.culling = prepareCadFrustumCulling(viewer.GetScene());
      if (this.stopGpuTiming) {
        this.host.dataset.cadCullingSummary = JSON.stringify(
          this.culling.summary,
        );
      }
      this.changed();
      this.options.onReady?.(info);
      return info;
    } catch (error) {
      if (!this.disposed) {
        this.dispose();
        this.options.onError(
          error instanceof Error ? error : new Error(String(error)),
        );
      }
      return null;
    }
  }

  render(frame: CadFrame): void {
    const viewer = this.viewer;
    if (!this.loaded || !viewer || this.disposed) return;
    this.frames.render(frame, () => {
      this.culling?.updateForResolution(
        frame.viewState.resolution / this.options.unitScaleToM,
      );
      renderCadFrame(viewer, frame, this.options.unitScaleToM, this.dimensions);
    });
  }

  setHiddenLayers(names: ReadonlySet<string>): void {
    this.hidden = new Set(names);
    this.applyVisibility();
  }

  setAppearance(mode: CadAppearanceMode, roles: CadLayerRoles): void {
    this.appearanceMode = mode;
    this.layerRoles = roles;
    this.applyAppearance();
  }

  private applyAppearance(): void {
    if (!this.loaded || this.disposed) return;
    if (this.appearance?.apply(this.appearanceMode, this.layerRoles)) {
      this.frames.invalidate();
      this.changed();
    }
  }

  private applyVisibility(): void {
    if (!this.loaded || !this.viewer || this.disposed) return;
    const changed: Record<string, boolean> = Object.create(null);
    for (const name of new Set([...this.hidden, ...this.appliedHidden])) {
      if (this.hidden.has(name) !== this.appliedHidden.has(name)) {
        changed[name] = !this.hidden.has(name);
      }
    }
    this.appliedHidden = new Set(this.hidden);
    if (Object.keys(changed).length) {
      this.frames.invalidate();
      this.viewer.ShowLayers(changed);
    }
  }

  private onContextLost = (): void => {
    if (this.disposed) return;
    this.dispose();
    this.options.onError(new Error('Контекст WebGL карты CAD потерян.'));
  };

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.loaded = false;
    this.frames.invalidate();
    this.abort.abort();
    this.stopLoad();
    this.worker?.terminate();
    this.worker = null;
    this.stopGpuTiming?.();
    this.stopGpuTiming = undefined;
    this.appearance?.dispose();
    this.appearance = undefined;
    this.culling = undefined;
    if (this.viewer) {
      const canvas = this.viewer.GetCanvas();
      canvas?.removeEventListener('webglcontextlost', this.onContextLost);
      this.viewer.Destroy();
      canvas?.remove();
      this.viewer = null;
    }
    this.host.remove();
    this.changed();
  }
}
