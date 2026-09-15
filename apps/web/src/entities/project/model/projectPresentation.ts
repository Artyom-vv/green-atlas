import type { ProjectSummary } from '@green/api-client';
import { countLabel } from '@/shared/format/countLabel';

export function projectState(project: ProjectSummary) {
  if (!project.source_name)
    return { title: 'Нужен DXF', detail: 'Исходник не загружен' };
  if (!project.has_geometry)
    return { title: 'Подготовьте карту', detail: 'Проверьте слои DXF' };
  if (!project.planting_zone_count)
    return { title: 'Выберите участки', detail: 'Карта подготовлена' };
  const detail = `${countLabel(project.plan_object_count, 'посадка', 'посадки', 'посадок')}, ${countLabel(project.planting_zone_count, 'участок', 'участка', 'участков')}`;
  return {
    title: project.plan_object_count ? 'Редактируется' : 'Готов к размещению',
    detail,
  };
}

export function projectRoute(project: ProjectSummary) {
  if (!project.source_name) return `/projects/${project.id}/import`;
  if (!project.has_geometry && ['imported', 'mapped'].includes(project.status))
    return `/projects/${project.id}/setup`;
  return `/projects/${project.id}/workspace`;
}
