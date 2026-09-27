import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type {
  Project,
  SourceAreaGroupRequest,
  SourceContextObject,
} from '@green/api-client';
import { preparationApi } from '@/features/source-preparation/api/preparationApi';

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
    // Keep an already loaded neighbor interactive while its tighter window is
    // fetched. Never borrow context across source/review revisions.
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
    check.reset();
  };
  const mutation = useMutation({
    mutationFn: async (
      action: 'linear' | 'reference' | 'area' | 'group' | `remove:${string}`,
    ) => {
      const options = { expectedStateVersion: project.state_version ?? 0 };
      if (action === 'group')
        return preparationApi.acceptSourceAreaGroup(id, request, options);
      if (action.startsWith('remove:'))
        return preparationApi.removeSourceAreaGroup(
          id,
          action.slice(7),
          options,
        );
      if (
        !focus?.geometry_sha256 ||
        !context.data ||
        context.data.source_sha256 !== project.source_file?.content_sha256 ||
        query.data?.source_sha256 !== project.source_file?.content_sha256
      )
        throw new Error('Исходные данные изменились');
      return preparationApi.decideSourceObject(
        id,
        {
          source: focus.source,
          source_sha256: context.data.source_sha256,
          geometry_sha256: focus.geometry_sha256,
          interpretation: action as 'linear' | 'reference' | 'area',
        },
        options,
      );
    },
    onSuccess: sync,
  });
  const recover = useMutation({
    mutationFn: () => preparationApi.getProject(id, false),
    onSuccess: (updated) => {
      sync(updated);
      mutation.reset();
    },
  });
  const busy = mutation.isPending || check.isPending || recover.isPending;
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
  const interpretation =
    project.source_file?.object_decisions?.find(
      (item) =>
        item.source.handle === focus?.source.handle &&
        JSON.stringify(item.source.instance_chain) ===
          JSON.stringify(focus?.source.instance_chain),
    )?.interpretation ?? 'area';
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
    request,
    check,
    checked,
    mutation,
    recover,
    busy,
    toggle,
    interpretation,
  };
}
