/** Подготовка исходника: адресные решения по объектам и проверка объединённых областей */
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type {
  Project,
  SourceAreaGroupRequest,
  SourceContextObject,
  SourceObjectDecisionRequest,
} from '@green/api-client';
import { preparationApi } from '@/features/source-preparation/api/preparationApi';
import { useProjectWorkContribution } from '@/features/project-navigation/model/ProjectWorkContext';
import { useProjectWrite } from '@/entities/project/model/useProjectWrite';
import {
  matchesAreaGroup,
  matchesObjectDecision,
  matchesRemovedAreaGroup,
} from '@/features/source-preparation/model/sourceDecisionProof';
import { sourceObjectKindFromValue } from '@/entities/source-data/model/layerKinds';

export const OBJECT_PAGE_SIZE = 30;
export function useObjectReview(project: Project, layer: string) {
  const id = project.id ?? '',
    client = useQueryClient();
  const [offset, setOffset] = useState(0),
    [index, setIndex] = useState(0);
  const [inspected, setInspected] = useState<string>(),
    [scale, setScale] = useState(1);
  const [assembling, setAssembling] = useState(false),
    [chosen, setChosen] = useState<SourceContextObject[]>([]);
  const [kind, setKind] = useState<SourceAreaGroupRequest['kind']>('building');
  const [roleDraft, setRoleDraft] = useState<{
    route: string;
    kind: NonNullable<SourceObjectDecisionRequest['kind']>;
  }>();
  const query = useQuery({
    queryKey: [
      'source-object-review',
      id,
      project.geometry_version,
      layer,
      offset,
    ],
    queryFn: () => preparationApi.getSourceObjectReview(id, layer, offset),
    retry: false,
  });
  const queueItem =
    query.data?.items[Math.min(index, (query.data?.items.length ?? 1) - 1)];
  const context = useQuery({
    queryKey: [
      'source-object-context',
      id,
      project.geometry_version,
      queueItem?.route,
      scale,
    ],
    queryFn: () =>
      preparationApi.getSourceObjectContext(id, queueItem!.route, scale),
    enabled: !!queueItem,
    retry: false,
    staleTime: 60_000,
    // Соседний объект остаётся доступен при уточнении окружения той же ревизии
    placeholderData: (previous, previousQuery) =>
      previousQuery?.queryKey[2] === project.geometry_version &&
      previous?.source_sha256 === project.source_file?.content_sha256 &&
      previous?.objects.some((item) => item.route === queueItem?.route)
        ? previous
        : undefined,
  });
  const focus = context.data?.objects.find(
    (item) => item.route === (inspected ?? queueItem?.route),
  );
  const objectDecision = project.source_file?.object_decisions?.find(
    (item) =>
      item.source.handle === focus?.source.handle &&
      item.geometry_sha256 === focus?.geometry_sha256 &&
      JSON.stringify(item.source.instance_chain) ===
        JSON.stringify(focus?.source.instance_chain),
  );
  const assignedKind =
    objectDecision?.kind ?? sourceObjectKindFromValue(focus?.kind ?? '');
  const objectKind =
    roleDraft?.route === focus?.route ? roleDraft?.kind : assignedKind;
  const hasRoleDraft =
    roleDraft?.route === focus?.route && roleDraft?.kind !== assignedKind;
  const request: SourceAreaGroupRequest = {
    source_sha256: project.source_file?.content_sha256 ?? '',
    kind,
    members: chosen.map((item) => ({
      source: item.source,
      geometry_sha256: item.geometry_sha256!,
    })),
  };
  const signature = JSON.stringify(request);
  const check = useMutation({
    mutationFn: async (input: SourceAreaGroupRequest) => ({
      signature: JSON.stringify(input),
      result: await preparationApi.checkSourceAreaGroup(id, input),
    }),
  });
  const checked =
    check.data?.signature === signature ? check.data.result : undefined;
  const sync = (updated: Project) => {
    client.setQueryData(['setup-project', id], updated);
    for (const key of [
      'workspace-project',
      'data-passport',
      'source-object-review',
      'source-object-context',
    ])
      void client.invalidateQueries({ queryKey: [key, id] });
    setOffset(0);
    setIndex(0);
    setInspected(undefined);
    setChosen([]);
    setAssembling(false);
    setRoleDraft(undefined);
    check.reset();
  };
  const write = useProjectWrite(project, sync);
  const mutation = useMutation({
    mutationFn: async (
      action:
        'linear' | 'reference' | 'area' | 'kind' | 'group' | `remove:${string}`,
    ) => {
      const options = { expectedStateVersion: project.state_version ?? 0 };
      if (action === 'group') {
        if (!checked?.valid)
          throw new Error('Сначала проверьте выбранный контур в AutoCAD');
        return write.execute({
          send: () =>
            preparationApi.acceptSourceAreaGroup(id, request, options),
          matches: (fresh) => matchesAreaGroup(fresh, request),
        });
      }
      if (action.startsWith('remove:')) {
        const groupId = action.slice(7);
        return write.execute({
          send: () =>
            preparationApi.removeSourceAreaGroup(id, groupId, options),
          matches: (fresh) => matchesRemovedAreaGroup(fresh, groupId),
        });
      }
      if (
        !focus?.geometry_sha256 ||
        !context.data ||
        context.data.source_sha256 !== project.source_file?.content_sha256 ||
        query.data?.source_sha256 !== project.source_file?.content_sha256
      )
        throw new Error('Исходные данные изменились');
      const decision: SourceObjectDecisionRequest = {
        source: focus.source,
        source_sha256: context.data.source_sha256,
        geometry_sha256: focus.geometry_sha256,
        interpretation:
          action === 'kind'
            ? objectDecision?.interpretation === 'linear'
              ? 'linear'
              : 'area'
            : (action as 'linear' | 'reference' | 'area'),
        ...(action === 'kind'
          ? { kind: objectKind }
          : action === 'linear' && objectDecision?.kind
            ? { kind: objectDecision.kind }
            : {}),
      };
      if (action === 'kind' && !objectKind)
        throw new Error('Выберите назначение объекта');
      return write.execute({
        send: () => preparationApi.decideSourceObject(id, decision, options),
        matches: (fresh) => matchesObjectDecision(fresh, decision),
      });
    },
    retry: false,
  });
  const recover = useMutation({
    mutationFn: write.recover,
    onSuccess: () => mutation.reset(),
    retry: false,
  });
  const busy =
    mutation.isPending || check.isPending || recover.isPending || write.blocked;
  const committedSelection =
    (mutation.variables === 'group' || mutation.variables === 'kind') &&
    write.writeOutcome === 'confirmed' &&
    write.readRecovery;
  const hasDraft = (chosen.length > 0 || hasRoleDraft) && !committedSelection;
  useProjectWorkContribution(`source-objects:${id}`, {
    hasDraft,
    writeOutcome: write.writeOutcome,
    readRecovery: write.readRecovery,
    editingBlocked: busy,
  });
  const chooseIndex = (next: number) => {
    setIndex(next);
    setInspected(undefined);
  };
  const page = (direction: number) => {
    setOffset(Math.max(0, offset + direction * OBJECT_PAGE_SIZE));
    chooseIndex(0);
  };
  const toggle = (item: SourceContextObject) => {
    if (!item.can_join || !item.geometry_sha256 || busy) return;
    setChosen((items) =>
      items.some((value) => value.route === item.route)
        ? items.filter((value) => value.route !== item.route)
        : [...items, item],
    );
    setInspected(item.route);
  };
  const interpretation = objectDecision?.interpretation ?? 'area';
  return {
    query,
    context,
    focus,
    queueItem,
    offset,
    index,
    scale,
    setScale,
    chooseIndex,
    page,
    setInspected,
    assembling,
    setAssembling,
    chosen,
    setChosen,
    kind,
    setKind,
    objectKind,
    assignedKind,
    hasRoleDraft,
    setObjectKind: (next: NonNullable<SourceObjectDecisionRequest['kind']>) => {
      if (focus && !busy) setRoleDraft({ route: focus.route, kind: next });
    },
    discardObjectKind: () => setRoleDraft(undefined),
    selectionBlocked: busy || hasRoleDraft,
    request,
    check,
    checked,
    mutation,
    recover,
    writeError: write.error ?? mutation.error,
    needsRecovery: write.readRecovery,
    writeOutcome: write.writeOutcome,
    closeBlocked:
      hasDraft ||
      mutation.isPending ||
      write.writeOutcome === 'writing' ||
      write.writeOutcome === 'unknown',
    hasDraft,
    busy,
    toggle,
    interpretation,
    hasKindOverride: objectDecision?.kind != null,
  };
}
