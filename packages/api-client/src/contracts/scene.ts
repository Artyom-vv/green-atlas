import type { Schemas } from './wire';

export type ScenePlantObject = Schemas['ScenePlantObject'];

export type SceneSnapshot = Omit<
  Schemas['SceneSnapshot'],
  'objects' | 'data_gaps'
> & { objects: ScenePlantObject[]; data_gaps: string[] };
