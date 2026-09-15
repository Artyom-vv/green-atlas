import { repeatedItemLabel } from '@/entities/planting-zone/model/plantingZoneLabels';
import { countLabel, plantingCount } from '@/shared/format/countLabel';
import type {
  AgentRun,
  AgentSelectionContext,
  Project,
} from '@green/api-client';

type SelectionIds = Pick<AgentSelectionContext, 'zone_ids' | 'object_ids'>;
const record = (value: unknown): Record<string, unknown> | undefined =>
  value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
const ids = (value: unknown) =>
  Array.isArray(value) && value.every((id) => typeof id === 'string')
    ? (value as string[])
    : [];

/** Copy at submission so later map selections cannot alter the request or its label. */
export function snapshotSelection(
  projectId: string,
  context: AgentSelectionContext | undefined,
): AgentSelectionContext | undefined {
  return context?.project_id === projectId
    ? {
        ...context,
        object_ids: [...context.object_ids],
        zone_ids: [...context.zone_ids],
      }
    : undefined;
}

export function selectionLabel(
  selection: SelectionIds | undefined,
  project: Project | undefined,
) {
  if (!selection) return undefined;
  const { object_ids: objects, zone_ids: zones } = selection;
  if (objects.length && zones.length)
    return `${plantingCount(objects.length)} · ${countLabel(zones.length, 'участок', 'участка', 'участков')}`;
  if (objects.length) return plantingCount(objects.length);
  if (zones.length === 1) {
    const zone = project?.planting_zones?.find((item) => item.id === zones[0]);
    return zone
      ? repeatedItemLabel(project?.planting_zones ?? [], zone)
      : '1 участок — название недоступно';
  }
  return zones.length
    ? countLabel(zones.length, 'участок', 'участка', 'участков')
    : undefined;
}

/** Raw map context is merely available evidence. Only the accepted scope names the task. */
export function acceptedSelection(
  run: AgentRun | undefined,
): SelectionIds | undefined {
  const scope = run?.state.resolved_scope;
  const context = record(run?.state.intent.selection_context);
  if (
    run?.state.intent.scope_mode !== 'selection' ||
    scope?.basis !== 'selection' ||
    scope.project_id !== run.state.project_id ||
    context?.project_id !== run.state.project_id ||
    run.state.intent.selection_issue
  )
    return undefined;
  const selection = {
    zone_ids: ids(scope.zone_ids),
    object_ids: ids(scope.object_ids),
  };
  return Boolean(selection.zone_ids.length) !==
    Boolean(selection.object_ids.length)
    ? selection
    : undefined;
}

export function selectionRemedies(context: AgentSelectionContext | undefined) {
  if (!context || (!context.object_ids.length && !context.zone_ids.length))
    return [];
  if (context.object_ids.length && context.zone_ids.length)
    return [
      {
        label: 'Выделенные посадки',
        draft: 'Используй текущее выделение посадок',
      },
      {
        label: 'Выделенные участки',
        draft: 'Используй текущее выделение участков',
      },
    ];
  return [
    {
      label: 'Использовать текущее выделение',
      draft: 'Используй текущее выделение',
    },
  ];
}
