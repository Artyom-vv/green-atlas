/** Подготовка исходника: проверка сохранённых решений после потерянного ответа записи */
import type {
  NativeFaceDecision,
  Project,
  SourceAreaGroupRequest,
  SourceObjectDecisionRequest,
} from '@green/api-client';

const sourceKey = (source: SourceObjectDecisionRequest['source']) =>
  JSON.stringify([source.instance_chain, source.handle]);

const membersKey = (members: SourceAreaGroupRequest['members']) =>
  JSON.stringify(
    members
      .map((member) =>
        JSON.stringify([sourceKey(member.source), member.geometry_sha256]),
      )
      .sort(),
  );

/** Сверяет текущее решение; исходник и минимальную ревизию проверяет владелец записи */
export function matchesObjectDecision(
  project: Project,
  request: SourceObjectDecisionRequest,
) {
  const source = project.source_file;
  if (
    source?.content_sha256 !== request.source_sha256 ||
    !Array.isArray(source.object_decisions)
  )
    return false;
  const decision = source.object_decisions.find(
    (item) => sourceKey(item.source) === sourceKey(request.source),
  );
  if (request.interpretation === 'area' && request.kind == null)
    return decision === undefined;
  return (
    decision?.interpretation === request.interpretation &&
    (decision.kind ?? null) === (request.kind ?? null) &&
    decision.geometry_sha256 === request.geometry_sha256
  );
}

export function matchesAreaGroup(
  project: Project,
  request: SourceAreaGroupRequest,
) {
  const source = project.source_file;
  return (
    source?.content_sha256 === request.source_sha256 &&
    source.area_groups?.some(
      (group) =>
        group.source_sha256 === request.source_sha256 &&
        group.kind === request.kind &&
        membersKey(group.members) === membersKey(request.members),
    ) === true
  );
}

export function matchesRemovedAreaGroup(project: Project, groupId: string) {
  const groups = project.source_file?.area_groups;
  return Array.isArray(groups) && !groups.some((group) => group.id === groupId);
}

export function matchesNativeFaceDecision(
  project: Project,
  request: NativeFaceDecision,
) {
  const source = project.source_file;
  const keys = source?.rejected_native_face_keys;
  return (
    source?.content_sha256 === request.source_sha256 &&
    Array.isArray(keys) &&
    keys.includes(request.key) === request.rejected
  );
}
