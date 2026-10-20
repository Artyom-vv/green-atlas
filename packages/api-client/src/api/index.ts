import { cadApi } from './cad';
import { planningApi } from './planning';
import { projectsApi } from './projects';
import { releaseApi } from './release';
import { sceneApi } from './scene';
import { locationApi } from './location';

export const api = {
  ...projectsApi,
  ...planningApi,
  ...sceneApi,
  ...locationApi,
  ...releaseApi,
  ...cadApi,
};
