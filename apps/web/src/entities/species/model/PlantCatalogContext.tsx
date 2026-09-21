import { createContext, useContext } from 'react';
import type { AssortmentInventory } from '@green/api-client';

export const PlantCatalogContext = createContext<{
  inventory?: AssortmentInventory;
}>({});
export const usePlantCatalog = () => useContext(PlantCatalogContext);
