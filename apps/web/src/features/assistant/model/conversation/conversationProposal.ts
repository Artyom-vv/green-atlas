import { repeatedItemLabel } from '@/entities/planting-zone/model/plantingZoneLabels';
import type {
  AgentTrace,
  Proposal,
} from '@/features/assistant/model/assistantContext';
import type {
  ChangeSetPreview,
  Conversation,
  Project,
  ProjectChatReply,
} from '@green/api-client';
type PreparedContent = {
  event?: string;
  preparation?: {
    change_set?: ChangeSetPreview;
    events?: { state_version: number }[];
    task?: Conversation['task'];
    resolved_zone_ids?: string[];
    requested?: number;
    shortfall_explanation?: string;
    condition_check?: { full_compliance_verified?: boolean; coverage?: string };
    agent_trace?: AgentTrace;
  };
};
const content = (record: Conversation['records'][number]) =>
  record.payload.content as PreparedContent | undefined;

export function latestProposal(
  conversation: Conversation,
  project: Project,
): Proposal | undefined {
  const record = [...conversation.records]
    .reverse()
    .find(
      (item) =>
        item.kind === 'tool_event' &&
        ['task_prepared', 'placement_prepared'].includes(
          content(item)?.event ?? '',
        ),
    );
  const prepared = record ? content(record)?.preparation : undefined;
  const preview = prepared?.change_set as ChangeSetPreview | undefined;
  if (
    !record ||
    !preview ||
    typeof preview.id !== 'string' ||
    typeof preview.digest !== 'string' ||
    typeof preview.can_apply !== 'boolean'
  )
    return;
  const stateVersion = prepared?.events?.[0]?.state_version;
  if (typeof stateVersion !== 'number') return;
  const values = prepared?.task?.values;
  const operation = values?.operation;
  const count =
    operation === 'delete'
      ? (preview.deletion_ids?.length ?? 0)
      : operation === 'edit'
        ? (preview.updates?.length ?? 0)
        : (preview.additions?.length ?? 0);
  const zones: string[] = prepared?.resolved_zone_ids?.length
    ? prepared.resolved_zone_ids
    : (values?.zone_ids ?? []);
  const scope: ProjectChatReply['scope'] =
    values?.scope === 'objects'
      ? 'selection'
      : values?.scope === 'project'
        ? 'project'
        : 'zone';
  const title =
    operation === 'delete'
      ? `Удалить посадки: ${count}`
      : operation === 'edit'
        ? `Изменить посадки: ${count}`
        : `Найдено ${count}${prepared?.requested ? ` из ${prepared.requested}` : ''}`;
  const zone = project.planting_zones?.find((item) => item.id === zones[0]);
  const scopeLabel =
    scope === 'selection'
      ? 'Выбранные посадки'
      : scope === 'project'
        ? 'Весь проект'
        : zones.length > 1
          ? `Участков: ${zones.length}`
          : zone
            ? repeatedItemLabel(project.planting_zones ?? [], zone)
            : 'Участок задания';
  return {
    recordId: record.record_id,
    messageId: `proposal:${record.record_id}`,
    preview,
    stateVersion,
    title,
    scopeLabel,
    postAction: values?.post_action === 'focus_map' ? 'focus_map' : undefined,
    agentTrace: prepared?.agent_trace,
    shortfallExplanation:
      operation === 'place' &&
      typeof prepared?.requested === 'number' &&
      count < prepared.requested &&
      typeof prepared.shortfall_explanation === 'string'
        ? prepared.shortfall_explanation
        : undefined,
    verificationNotice:
      prepared?.condition_check?.full_compliance_verified === false
        ? 'Учтены доступные контуры зданий и дорог. Полное соответствие нормам не подтверждено.'
        : undefined,
    intent: {
      action: operation === 'delete' ? 'delete' : 'recommend',
      reply: '',
      scope,
      zone_id: zones[0] ?? null,
      profile: 'balanced',
      screen_side: 'perimeter',
    },
  };
}
