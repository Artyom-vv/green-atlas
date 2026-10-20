import type { Schemas } from './wire';

export type ScenePlantObject = Schemas['ScenePlantObject'];
export type RenderCamera = Schemas['RenderCamera'];
export type SceneRenderRequest = Schemas['SceneRenderRequest'];
export type SceneRenderJob = Schemas['SceneRenderJob'];
export type SceneRenderPreparation = Schemas['SceneRenderPreparation'];
export type SceneRenderPreparationRequest =
  Schemas['SceneRenderPreparationRequest'];

export type SceneSnapshot = Omit<
  Schemas['SceneSnapshot'],
  'objects' | 'data_gaps'
> & { objects: ScenePlantObject[]; data_gaps: string[] };
