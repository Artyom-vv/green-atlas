import { assistantApi } from './assistant';
import { cadApi } from './cad';
import { planningApi } from './planning';
import { projectsApi } from './projects';
import { releaseApi } from './release';
import { sceneApi } from './scene';

export const api = {
  ...projectsApi,
  ...planningApi,
  ...sceneApi,
  ...releaseApi,
  ...assistantApi,
  ...cadApi,
};
