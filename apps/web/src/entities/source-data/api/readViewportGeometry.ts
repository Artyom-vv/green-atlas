import { api } from '@green/api-client';

/** The source-data boundary owns transport; the viewport owns query timing. */
export const readViewportGeometry = api.getMapFeatures;
