import type { PropsWithChildren } from 'react';
import { PlantCatalogContext } from '@/entities/species/model/PlantCatalogContext';
import { useAssortmentInventory } from '@/entities/species/model/useAssortmentInventory';

export function WorkspacePlantCatalog({ children }: PropsWithChildren) {
  const inventory = useAssortmentInventory();
  return (
    <PlantCatalogContext.Provider value={{ inventory: inventory.data }}>
      {children}
    </PlantCatalogContext.Provider>
  );
}
