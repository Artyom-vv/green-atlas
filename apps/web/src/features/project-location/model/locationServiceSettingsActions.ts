import { api } from '@green/api-client';

export const locationServiceSettingsActions = {
  read: api.getLocationServiceSettings,
  save: api.updateLocationServiceSettings,
  reset: api.resetLocationServiceSettings,
};
