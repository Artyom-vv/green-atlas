import { useQuery } from '@tanstack/react-query';
import { api } from '@green/api-client';

export function useAssortmentInventory() {
  return useQuery({
    queryKey: ['species-assortment'],
    queryFn: api.getAssortment,
    staleTime: Infinity,
  });
}
