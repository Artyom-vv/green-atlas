import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@green/api-client';

export function useCadDirectory() {
  const [location, setLocation] = useState({ rootId: '', path: '.' });
  const [selected, select] = useState<string>();
  const roots = useQuery({
    queryKey: ['cad-roots'],
    queryFn: api.listCadRoots,
    retry: false,
  });
  const rootId = location.rootId || roots.data?.[0]?.id || '';
  const directory = useQuery({
    queryKey: ['cad-directory', rootId, location.path],
    queryFn: ({ signal }) =>
      api.listCadDirectory(rootId, location.path, signal),
    enabled: Boolean(rootId),
    retry: false,
  });
  const navigate = (path: string, nextRoot = rootId) => {
    select(undefined);
    setLocation({ rootId: nextRoot, path });
  };
  return {
    roots,
    directory,
    rootId,
    path: location.path,
    selected,
    select,
    navigate,
  };
}
