import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type Layer, type LayerMapping } from '@green/api-client';
import { useNavigate, useParams } from 'react-router-dom';
import { Button, InlineMessage, Progress } from '@green/ui';
import { Map as MapIcon } from 'lucide-react';
import { AppHeader } from '../domain-ui/AppHeader';
import { LayerMappingTable } from '../domain-ui/LayerMappingTable';
import { DataPassportPanel } from '../domain-ui/DataPassportPanel';
import { OperationProgress } from '../domain-ui/OperationProgress';
import { ProjectConflictNotice } from '../domain-ui/ProjectConflictNotice';
import { isProjectConflict } from '../domain-ui/projectConflict';
import { FlowDocument, ProjectSteps } from '../domain-ui/ProjectFlow';

const EMPTY_LAYERS: Layer[] = [];

export function SetupPage() {
  const { projectId = '' } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const projectQuery = useQuery({ queryKey: ['setup-project', projectId], queryFn: () => api.getProject(projectId, false), enabled: Boolean(projectId) });
  const dataPassportQuery = useQuery({ queryKey: ['data-passport', projectId], queryFn: () => api.getDataPassport(projectId), enabled: Boolean(projectId), staleTime: 10_000 });
  const [mappings, setMappings] = useState<Record<string, LayerMapping>>({});
  const [operationId, setOperationId] = useState<string>();
  const layers = projectQuery.data?.layers ?? EMPTY_LAYERS;
  const sourceWarnings = projectQuery.data?.source_file?.warnings ?? [];

  useEffect(() => {
    if (!projectQuery.data || Object.keys(mappings).length) return;
    setMappings(Object.fromEntries(layers.map((layer) => [layer.id, { layer_id: layer.id, kind: layer.mapped_kind ?? 'ignore', visible: layer.visible }])));
  }, [layers, mappings, projectQuery.data]);

  const requiredLayers = useMemo(() => layers.filter((layer) => layer.required), [layers]);
  const requiredReady = useMemo(
    () => requiredLayers.every((layer) => {
      const kind = mappings[layer.id]?.kind;
      return Boolean(kind) && kind !== 'ignore';
    }),
    [mappings, requiredLayers],
  );
  const incompleteConstraintLayers = useMemo(
    () => layers.filter((layer) => !layer.geometry_complete && mappings[layer.id]?.kind && mappings[layer.id]?.kind !== 'ignore'),
    [layers, mappings],
  );
  const hasPlanningBoundary = requiredLayers.length > 0;
  const latestOperationQuery = useQuery({ queryKey: ['latest-operation', projectId, 'calculate_geometry'], queryFn: () => api.getLatestOperation(projectId, 'calculate_geometry'), enabled: Boolean(projectId), staleTime: 0 });
  const operationQuery = useQuery({ queryKey: ['operation', projectId, operationId], queryFn: () => api.getOperation(projectId, operationId!), enabled: Boolean(operationId), refetchInterval: (query) => ['queued', 'running', 'cancelling'].includes(query.state.data?.status ?? '') ? 350 : false });

  useEffect(() => {
    const latest = latestOperationQuery.data;
    if (!operationId && latest && latest.status !== 'completed') setOperationId(latest.id);
  }, [latestOperationQuery.data, operationId]);

  useEffect(() => {
    if (operationQuery.data?.status === 'completed') navigate(`/projects/${projectId}/workspace`);
  }, [navigate, operationQuery.data?.status, projectId]);

  const saveMutation = useMutation({
    mutationFn: async () => {
      await api.saveMappings(projectId, Object.values(mappings));
      return api.startGeometryOperation(projectId);
    },
    onSuccess: (operation) => setOperationId(operation.id),
  });
  const cancelOperation = useMutation({ mutationFn: (id: string) => api.cancelOperation(projectId, id), onSuccess: (next) => queryClient.setQueryData(['operation', projectId, next.id], next) });
  const retryOperation = useMutation({ mutationFn: () => api.startGeometryOperation(projectId), onSuccess: (next) => setOperationId(next.id) });
  const latestActiveOperation = latestOperationQuery.data?.status === 'queued' || latestOperationQuery.data?.status === 'running' ? latestOperationQuery.data : undefined;
  const operation = operationQuery.data ?? (operationId ? undefined : latestActiveOperation);
  const calculating = saveMutation.isPending || Boolean(operationId && operationQuery.isLoading) || ['queued', 'running', 'cancelling'].includes(operation?.status ?? '');
  const mutationError = saveMutation.error ?? cancelOperation.error ?? retryOperation.error;

  const reloadAfterConflict = async () => {
    // A refetch alone does not clear React Query's mutation error, so the
    // resolved conflict would remain visible over fresh layer mappings.
    saveMutation.reset();
    cancelOperation.reset();
    retryOperation.reset();
    const result = await projectQuery.refetch();
    if (result.data) setMappings(Object.fromEntries((result.data.layers ?? []).map((layer) => [layer.id, { layer_id: layer.id, kind: layer.mapped_kind ?? 'ignore', visible: layer.visible }])));
  };

  if (projectQuery.isLoading) return <div className="app-shell"><AppHeader /><main className="center-status"><Progress label="Загрузка проекта" /></main></div>;
  if (!projectQuery.data) return <div className="app-shell"><AppHeader /><main className="center-status"><InlineMessage tone="error">Проект не найден.</InlineMessage></main></div>;

  return <div className="app-shell flow-screen">
    <AppHeader projectName={projectQuery.data.name} />
    <main className="project-flow-layout setup-layout">
      <ProjectSteps active={2} projectName={projectQuery.data.name} />
      <FlowDocument title="Проверьте слои" description="Подтвердите слои, которые ограничивают посадку." footer={<><Button variant="secondary" onClick={() => navigate(`/projects/${projectId}/import`)} disabled={calculating}>Заменить DXF</Button><Button variant="primary" icon={MapIcon} loading={calculating} disabled={!requiredReady || calculating || incompleteConstraintLayers.length > 0} onClick={() => saveMutation.mutate()}>Подготовить карту</Button></>}>
        <div className="mapping-content">
          {projectQuery.data.import_status?.editability === 'read_only' ? <InlineMessage tone="warning" title="Ревизия только для просмотра">Загрузите полный ZIP-пакет выпуска, чтобы продолжить редактирование посадок и сохранить их идентификаторы.</InlineMessage> : null}
          {sourceWarnings.map((warning) => <InlineMessage key={warning} tone="warning">{warning}</InlineMessage>)}
          {incompleteConstraintLayers.length ? <InlineMessage tone="error" title="Нужен рабочий фрагмент">Часть объектов не попала на карту. Исключите эти слои из ограничений или загрузите меньший фрагмент DXF.</InlineMessage> : null}
          {!hasPlanningBoundary ? <InlineMessage tone="info" title="Границу можно задать на карте">В DXF нет замкнутой границы участка. После подготовки карты обведите рабочую область вручную; ограничения от подтверждённых слоёв всё равно останутся видны.</InlineMessage> : null}
          <LayerMappingTable layers={layers} mappings={mappings} onChange={setMappings} />
          {dataPassportQuery.data ? <DataPassportPanel passport={dataPassportQuery.data} /> : null}
          {operation ? <OperationProgress operation={operation} title="Подготовка карты" actionBusy={cancelOperation.isPending || retryOperation.isPending} onCancel={() => cancelOperation.mutate(operation.id!)} onRetry={() => retryOperation.mutate()} onDownloadSource={() => { window.location.href = api.sourceDownloadUrl(projectId); }} /> : null}
          {mutationError ? isProjectConflict(mutationError) ? <ProjectConflictNotice error={mutationError} onReload={() => void reloadAfterConflict()} reloading={projectQuery.isFetching} /> : <InlineMessage tone="error">Не удалось подготовить карту. Проверьте слои и повторите попытку.</InlineMessage> : null}
        </div>
      </FlowDocument>
    </main>
  </div>;
}
