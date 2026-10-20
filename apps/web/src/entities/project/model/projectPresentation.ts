import type { ProjectSummary } from '@green/api-client';
import { countLabel } from '@/shared/format/countLabel';
import { projectResumeRoute, projectPreparationNotice } from './projectResume';

export function projectState(project: ProjectSummary) {
  if (!project.source_name)
    return { title: 'Нужен исходник', detail: 'Откройте чертёж через AutoCAD' };
  const preparationIssue = projectPreparationNotice(project);
  if (preparationIssue)
    return { title: 'Проверьте источник', detail: preparationIssue };
  if (!project.has_geometry)
    return { title: 'Проверьте источник', detail: 'Геометрия ещё не открыта' };
  if (!project.planting_zone_count)
    return { title: 'Выберите участки', detail: 'Геометрия открыта' };
  const detail = `${countLabel(project.plan_object_count, 'посадка', 'посадки', 'посадок')}, ${countLabel(project.planting_zone_count, 'участок', 'участка', 'участков')}`;
  return {
    // Display geometry and zones do not prove calculation readiness. The
    // workspace owns that status; this summary only describes saved content.
    title: project.plan_object_count ? 'Редактируется' : 'Участки созданы',
    detail,
  };
}

export const projectRoute = projectResumeRoute;
