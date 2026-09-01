import { useState } from 'react';
import type { Plan, RegulatoryReleaseBasis, ReleaseCreateRequest, ReleasePackage } from '@green/api-client';
import { Download, FileArchive, FileCode2, FileSpreadsheet, Map, PackageCheck } from 'lucide-react';
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
      <div><strong>Снимок текущей ревизии</strong><span>DXF, ведомость, дендроплан и основания используют единые ID.</span></div>
    </section>

    {release ? <>
      <StatusIndicator tone="success" label={release.mode === 'draft' ? 'Черновой пакет готов' : 'Финальный пакет готов'} value={`Версия плана ${release.plan_version}`} />
      <StatusIndicator tone="success" label="Горизонт сцены" value={horizonLabel(release.scene_horizon)} />
      {bundle ? <Button variant="primary" icon={Download} onClick={() => onDownload(bundle.download_url)}>Скачать полный пакет</Button> : null}
      <section className="release-package__files" aria-label="Файлы выпуска">
        {artifacts.filter((artifact) => artifact.kind !== 'bundle').map((artifact) => {
          const Icon = icons[artifact.kind];
          return <button type="button" key={artifact.id} onClick={() => onDownload(artifact.download_url)}><Icon size={16} /><span><strong>{labels[artifact.kind]}</strong><small>{Math.max(1, Math.round(artifact.size / 1024))} КБ</small></span><Download size={16} /></button>;
        })}
      </section>
      {(release.warnings ?? []).map((warning) => <InlineMessage key={warning} tone="warning">{warning}</InlineMessage>)}
    </> : <>
      {onGrowthHorizon ? <GrowthHorizonControl value={growthHorizon} forecasts={objects} onChange={onGrowthHorizon} /> : null}
      <section className="release-package__readiness">
        <StatusIndicator tone={hardErrors ? 'error' : 'success'} label="Ошибки размещения" value={hardErrors || 'Нет'} />
        <StatusIndicator tone={missingSpecies ? 'warning' : 'success'} label="Без назначенного вида" value={missingSpecies || 'Нет'} />
      </section>
      <section className="release-package__basis" aria-label="Основания финального выпуска">
        <h3>Основания финального выпуска</h3>
        <FormField label="ПП-616"><Select aria-label="Решение по ПП-616" value={basis.pp616_status} onChange={(event) => setBasis((current) => ({ ...current, pp616_status: event.target.value as RegulatoryReleaseBasis['pp616_status'] }))}><option value="pending">Нужно определить</option><option value="documented">Компенсация оформлена</option><option value="not_applicable">Не применяется</option></Select></FormField>
        <FormField label="Основание ПП-616"><TextInput aria-label="Основание решения по ПП-616" value={basis.pp616_reference} onChange={(event) => setBasis((current) => ({ ...current, pp616_reference: event.target.value }))} placeholder="Документ или причина неприменимости" /></FormField>
        <FormField label="ПП-1160"><Select aria-label="Решение по ПП-1160" value={basis.pp1160_status} onChange={(event) => setBasis((current) => ({ ...current, pp1160_status: event.target.value as RegulatoryReleaseBasis['pp1160_status'] }))}><option value="pending">Нужно определить</option><option value="documented">Процедура оформлена</option><option value="not_required">Не требуется</option></Select></FormField>
        <FormField label="Основание ПП-1160"><TextInput aria-label="Основание решения по ПП-1160" value={basis.pp1160_reference} onChange={(event) => setBasis((current) => ({ ...current, pp1160_reference: event.target.value }))} placeholder="Билет, разрешение или причина" /></FormField>
        <FormField label="Проверил"><TextInput aria-label="Ответственный за проверку" value={basis.confirmed_by} onChange={(event) => setBasis((current) => ({ ...current, confirmed_by: event.target.value }))} placeholder="Фамилия и инициалы" /></FormField>
      </section>
      <Button variant="primary" icon={FileArchive} loading={loading} disabled={!objects.length} onClick={() => onCreate({ mode: 'draft', scene_horizon: growthHorizon ?? 0 })}>Собрать черновой пакет</Button>
      <Button variant="secondary" icon={PackageCheck} loading={loading} disabled={!finalReady} onClick={() => onCreate({ mode: 'final', scene_horizon: growthHorizon ?? 0, regulatory_basis: basis })}>Собрать финальный пакет</Button>
      {!finalReady ? <InlineMessage tone="warning">Для финального выпуска устраните ошибки, назначьте виды и зафиксируйте основания ПП-616 и ПП-1160. Черновик можно скачать сейчас.</InlineMessage> : null}
    </>}
    {error ? <InlineMessage tone="error">{error}</InlineMessage> : null}
    <p className="release-package__note">Пакет не является согласованием или порубочным билетом.</p>
  </div>;
}
