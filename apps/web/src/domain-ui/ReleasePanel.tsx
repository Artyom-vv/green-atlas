import { useState } from 'react';
import type { Plan, RegulatoryReleaseBasis, ReleaseCreateRequest, ReleasePackage } from '@green/api-client';
import { ChevronRight, Download, FileArchive, FileCode2, FileSpreadsheet, Map, PackageCheck } from 'lucide-react';
import { Button, FormField, InlineMessage, Select, StatusIndicator, TextInput } from '@green/ui';
import { GrowthHorizonControl, type GrowthHorizon } from './GrowthHorizonControl';

const labels = {
  bundle: 'Полный пакет',
  dxf: 'План в DXF',
  schedule: 'Посадочная ведомость',
  manifest: 'Основания и пробелы данных',
  scene: 'Снимок 3D-сцены',
  dendroplan: 'Дендроплан',
} as const;

const icons = {
  bundle: FileArchive,
  dxf: Map,
  schedule: FileSpreadsheet,
  manifest: FileCode2,
  scene: FileCode2,
  dendroplan: Map,
} as const;

export function ReleasePanel({
  plan,
  release,
  growthHorizon,
  onGrowthHorizon,
  loading,
  error,
  onCreate,
  onDownload,
}: {
  plan: Plan;
  release?: ReleasePackage;
  growthHorizon?: GrowthHorizon;
  onGrowthHorizon?: (value: GrowthHorizon) => void;
  loading?: boolean;
  error?: string;
  onCreate: (request: ReleaseCreateRequest) => void;
  onDownload: (path: string) => void;
}) {
  const [basis, setBasis] = useState<RegulatoryReleaseBasis>({
    pp616_status: 'pending', pp616_reference: '',
    pp1160_status: 'pending', pp1160_reference: '',
    confirmed_by: '',
  });
  const [mode, setMode] = useState<'draft' | 'final'>('draft');
  const [editingReleaseId, setEditingReleaseId] = useState<string>();
  const showRelease = Boolean(release && release.id !== editingReleaseId);
  const objects = plan.objects ?? [];
  const artifacts = release?.artifacts ?? [];
  const missingSpecies = objects.filter((object) => !object.species_revision_id).length;
  const hardErrors = (plan.issues ?? []).filter((issue) => issue.severity === 'error').length;
  const regulatoryReady = basis.pp616_status !== 'pending' && basis.pp1160_status !== 'pending'
    && Boolean(basis.pp616_reference.trim() && basis.pp1160_reference.trim() && basis.confirmed_by.trim());
  const finalReady = missingSpecies === 0 && hardErrors === 0 && objects.length > 0 && regulatoryReady;
  const bundle = artifacts.find((artifact) => artifact.kind === 'bundle');
  const horizonLabel = (horizon: number) => horizon === 0 ? 'Сейчас' : `${horizon} лет`;

  return <div className="release-package">
    <section className="release-package__summary">
      <PackageCheck size={20} />
      <div><strong>{showRelease ? 'Файлы проекта' : `План версии ${plan.version}`}</strong><span>Чертёж DXF, ведомость, дендроплан и 3D-сцена.</span></div>
    </section>

    {showRelease && release ? <>
      <StatusIndicator tone="success" label={release.mode === 'draft' ? 'Черновой пакет готов' : 'Финальный пакет готов'} value={`Версия плана ${release.plan_version}`} />
      <p className="release-package__note">Прогноз в пакете: {horizonLabel(release.scene_horizon).toLowerCase()}</p>
      {release.plan_version !== plan.version ? <InlineMessage tone="warning">Этот пакет содержит версию {release.plan_version}. Текущий план — версия {plan.version}. Для актуальных файлов соберите новый пакет.</InlineMessage> : null}
      {bundle ? <Button variant="primary" icon={Download} onClick={() => onDownload(bundle.download_url)}>Скачать полный пакет</Button> : null}
      <details className="source-details"><summary><ChevronRight size={14} aria-hidden="true" />Отдельные файлы</summary><section className="release-package__files" aria-label="Файлы выпуска">
        {artifacts.filter((artifact) => artifact.kind !== 'bundle').map((artifact) => {
          const Icon = icons[artifact.kind];
          return <button type="button" key={artifact.id} onClick={() => onDownload(artifact.download_url)}><Icon size={16} /><span><strong>{labels[artifact.kind]}</strong><small>{Math.max(1, Math.round(artifact.size / 1024))} КБ</small></span><Download size={16} /></button>;
        })}
      </section></details>
      {(release.warnings ?? []).length ? <details className="source-details"><summary><ChevronRight size={14} aria-hidden="true" />Ограничения пакета ({release.warnings!.length})</summary><ul>{release.warnings!.map(warning => <li key={warning}>{warning}</li>)}</ul></details> : null}
      <Button variant="secondary" onClick={() => setEditingReleaseId(release.id)}>Собрать новый пакет</Button>
    </> : <>
      <div className="release-package__modes" role="group" aria-label="Вид пакета"><Button variant={mode === 'draft' ? 'primary' : 'secondary'} aria-pressed={mode === 'draft'} disabled={loading} onClick={() => setMode('draft')}>Черновой</Button><Button variant={mode === 'final' ? 'primary' : 'secondary'} aria-pressed={mode === 'final'} disabled={loading} onClick={() => setMode('final')}>Финальный</Button></div>
      {mode === 'draft' ? <p className="release-package__note">Для обмена и продолжения работы. Замечания и неназначенные виды останутся в пакете.</p> : null}
      {onGrowthHorizon ? <GrowthHorizonControl value={growthHorizon} onChange={onGrowthHorizon} showMetrics={false} /> : null}
      <section className="release-package__readiness">
        <StatusIndicator tone={hardErrors ? 'error' : 'success'} label="Ошибки размещения" value={hardErrors || 'Нет'} />
        <StatusIndicator tone={missingSpecies ? 'warning' : 'success'} label="Без назначенного вида" value={missingSpecies || 'Нет'} />
      </section>
      {mode === 'final' ? <section className="release-package__basis" aria-label="Основания финального выпуска">
        <h3>Основания финального выпуска</h3>
        <FormField label="ПП-616"><Select aria-label="Решение по ПП-616" value={basis.pp616_status} onChange={(event) => setBasis((current) => ({ ...current, pp616_status: event.target.value as RegulatoryReleaseBasis['pp616_status'] }))}><option value="pending">Нужно определить</option><option value="documented">Компенсация оформлена</option><option value="not_applicable">Не применяется</option></Select></FormField>
        <FormField label="Основание ПП-616"><TextInput aria-label="Основание решения по ПП-616" value={basis.pp616_reference} onChange={(event) => setBasis((current) => ({ ...current, pp616_reference: event.target.value }))} placeholder="Документ или причина неприменимости" /></FormField>
        <FormField label="ПП-1160"><Select aria-label="Решение по ПП-1160" value={basis.pp1160_status} onChange={(event) => setBasis((current) => ({ ...current, pp1160_status: event.target.value as RegulatoryReleaseBasis['pp1160_status'] }))}><option value="pending">Нужно определить</option><option value="documented">Процедура оформлена</option><option value="not_required">Не требуется</option></Select></FormField>
        <FormField label="Основание ПП-1160"><TextInput aria-label="Основание решения по ПП-1160" value={basis.pp1160_reference} onChange={(event) => setBasis((current) => ({ ...current, pp1160_reference: event.target.value }))} placeholder="Билет, разрешение или причина" /></FormField>
        <FormField label="Проверил"><TextInput aria-label="Ответственный за проверку" value={basis.confirmed_by} onChange={(event) => setBasis((current) => ({ ...current, confirmed_by: event.target.value }))} placeholder="Фамилия и инициалы" /></FormField>
      </section> : null}
      {mode === 'draft' ? <Button variant="primary" icon={FileArchive} loading={loading} disabled={!objects.length} onClick={() => onCreate({ mode: 'draft', scene_horizon: growthHorizon ?? 0 })}>Собрать черновой пакет</Button> : <Button variant="primary" icon={PackageCheck} loading={loading} disabled={!finalReady} onClick={() => onCreate({ mode: 'final', scene_horizon: growthHorizon ?? 0, regulatory_basis: basis })}>Собрать финальный пакет</Button>}
      {mode === 'final' && !finalReady ? <p className="release-package__note">{hardErrors || missingSpecies ? 'Сначала устраните ошибки размещения и назначьте виды. ' : ''}Заполните оба решения, их основания и ответственного за проверку.</p> : null}
      {!objects.length ? <InlineMessage tone="info">Добавьте посадки в план перед выпуском.</InlineMessage> : null}
    </>}
    {error ? <InlineMessage tone="error">{error}</InlineMessage> : null}
    <p className="release-package__note">Пакет не является согласованием или порубочным билетом.</p>
  </div>;
}
