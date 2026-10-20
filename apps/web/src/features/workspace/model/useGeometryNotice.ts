/** Уведомление о пропусках подготовленной геометрии: паспорт принадлежит Query,
 * факт просмотра — локальной настройке проекта, без изменения его проверок. */
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { preparationApi } from '@/features/source-preparation/api/preparationApi';

const noticeKey = (projectId: string) =>
  `green-atlas:geometry-notice:v1:${projectId}`;

export function useGeometryNotice(projectId: string, prepared: boolean) {
  const [viewed, setViewed] = useState(() => {
    try {
      return localStorage.getItem(noticeKey(projectId)) === 'viewed';
    } catch {
      return false;
    }
  });
  const passport = useQuery({
    queryKey: ['data-passport', projectId],
    queryFn: () => preparationApi.getDataPassport(projectId),
    enabled: prepared && !viewed,
    networkMode: 'always',
    retry: false,
  });
  const layers = passport.data?.incomplete_layers ?? [];
  return {
    open: prepared && !viewed && layers.length > 0,
    layers,
    dismiss: () => {
      setViewed(true);
      try {
        localStorage.setItem(noticeKey(projectId), 'viewed');
      } catch {
        // Недоступная настройка не препятствует закрытию и работе в текущем окне.
      }
    },
  };
}
