import type { Plan, ReleasePackage } from '@green/api-client';
import { Download, FileArchive, FileCode2, FileSpreadsheet, Map, PackageCheck } from 'lucide-react';
import { Button, InlineMessage, StatusIndicator } from '@green/ui';

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
  loading,
  error,
  onCreate,
  onDownload,
}: {
  plan: Plan;
  release?: ReleasePackage;
  loading?: boolean;
  error?: string;
  onCreate: (mode: 'draft' | 'final') => void;
  onDownload: (path: string) => void;
}) {
  const objects = plan.objects ?? [];
  const artifacts = release?.artifacts ?? [];
  const missingSpecies = objects.filter((object) => !object.species_revision_id).length;
  const hardErrors = (plan.issues ?? []).filter((issue) => issue.severity === 'error').length;
  const finalReady = missingSpecies === 0 && hardErrors === 0 && objects.length > 0;
  const bundle = artifacts.find((artifact) => artifact.kind === 'bundle');

  return <div className="release-package">
    <section className="release-package__summary">
      <PackageCheck size={20} />
      <div><strong>Снимок текущей ревизии</strong><span>DXF, ведомость, дендроплан и основания используют единые ID.</span></div>
    </section>

    {release ? <>
      <StatusIndicator tone="success" label={release.mode === 'draft' ? 'Черновой пакет готов' : 'Финальный пакет готов'} value={`Версия плана ${release.plan_version}`} />
      {bundle ? <Button variant="primary" icon={Download} onClick={() => onDownload(bundle.download_url)}>Скачать полный пакет</Button> : null}
      <section className="release-package__files" aria-label="Файлы выпуска">
        {artifacts.filter((artifact) => artifact.kind !== 'bundle').map((artifact) => {
          const Icon = icons[artifact.kind];
          return <button type="button" key={artifact.id} onClick={() => onDownload(artifact.download_url)}><Icon size={16} /><span><strong>{labels[artifact.kind]}</strong><small>{Math.max(1, Math.round(artifact.size / 1024))} КБ</small></span><Download size={16} /></button>;
        })}
      </section>
      {(release.warnings ?? []).map((warning) => <InlineMessage key={warning} tone="warning">{warning}</InlineMessage>)}
    </> : <>
      <section className="release-package__readiness">
        <StatusIndicator tone={hardErrors ? 'error' : 'success'} label="Ошибки размещения" value={hardErrors || 'Нет'} />
        <StatusIndicator tone={missingSpecies ? 'warning' : 'success'} label="Без назначенного вида" value={missingSpecies || 'Нет'} />
      </section>
      <Button variant="primary" icon={FileArchive} loading={loading} disabled={!objects.length} onClick={() => onCreate('draft')}>Собрать черновой пакет</Button>
      <Button variant="secondary" icon={PackageCheck} loading={loading} disabled={!finalReady} onClick={() => onCreate('final')}>Собрать финальный пакет</Button>
      {!finalReady ? <InlineMessage tone="warning">Финальный выпуск появится после устранения ошибок и назначения видов. Черновик можно скачать сейчас.</InlineMessage> : null}
    </>}
    {error ? <InlineMessage tone="error">{error}</InlineMessage> : null}
    <p className="release-package__note">Пакет не является согласованием или порубочным билетом.</p>
  </div>;
}
