import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button, Dialog, Disclosure, InlineMessage, Progress, SegmentedControl } from '@green/ui';
import type { NativeAreaPreview } from '@green/api-client';
import { preparationApi } from '@/features/source-preparation/api/preparationApi';
import {
  formatArea,
  formatGap,
  type AreaProposal,
} from './nativeAreaPresentation';

interface Props {
  projectId: string;
  sourceSha256: string;
  proposal: AreaProposal;
  readOnly: boolean;
  busy: boolean;
  saving?: boolean;
  error?: string;
  onRefresh?: () => void;
  onClose: () => void;
  onDecision: (
    proposal: AreaProposal,
    decision: 'accepted' | 'rejected',
  ) => void;
}

export function NativeAreaPreviewDialog({
  projectId,
  sourceSha256,
  proposal,
  readOnly,
  busy,
  saving = false,
  error,
  onRefresh,
  onClose,
  onDecision,
}: Props) {
  const [gapOnly, setGapOnly] = useState(false);
  const [action, setAction] = useState<'accepted' | 'rejected'>();
  const query = useQuery({
    queryKey: [
      'native-area-preview',
      projectId,
      sourceSha256,
      proposal.proposal_sha256,
    ],
    queryFn: () => preparationApi.getNativeAreaPreview(projectId, proposal.id),
    staleTime: Infinity,
    retry: false,
  });
  const data = query.data;
  const verified =
    data?.source_sha256 === sourceSha256 &&
    data?.proposal_sha256 === proposal.proposal_sha256;
  return (
    <Dialog
      open
      title="Проверка замыкания"
      onClose={() => { if (!saving) onClose(); }}
      size="wide"
      footer={
        <div className="flex flex-wrap items-center justify-between gap-3" aria-busy={saving}>
          <div role="status" aria-live="polite" className="text-sm text-neutral-600">
            {saving ? 'Сохраняем решение' : proposal.decision === 'accepted'
              ? 'Замыкание принято' : proposal.decision === 'rejected'
                ? 'Замыкание отклонено' : 'Проверьте выделенное соединение'}
          </div>
          <div className="flex flex-wrap gap-2">
          <Button variant="ghost" disabled={saving} onClick={onClose}>Закрыть</Button>
          <Button
            variant="secondary"
            disabled={
              readOnly || busy || saving || !verified || proposal.decision === 'rejected'
            }
            loading={saving && action === 'rejected'}
            onClick={() => { setAction('rejected'); onDecision(proposal, 'rejected'); }}
          >
            Отклонить замыкание
          </Button>
          <Button
            variant="primary"
            disabled={
              readOnly || busy || saving || !verified || proposal.decision === 'accepted'
            }
            loading={saving && action === 'accepted'}
            onClick={() => { setAction('accepted'); onDecision(proposal, 'accepted'); }}
          >
            Принять замыкание
          </Button>
          </div>
        </div>
      }
    >
      <div className="space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <dl className="m-0 flex gap-8">
            <div><dt className="text-xs text-neutral-500">Разрыв</dt>
              <dd className="m-0 text-lg font-medium">{formatGap(proposal.closure_gap_m)}</dd></div>
            <div><dt className="text-xs text-neutral-500">Площадь после замыкания</dt>
              <dd className="m-0 text-lg font-medium">{formatArea(proposal.area_m2)}</dd></div>
          </dl>
          {verified && <SegmentedControl label="Масштаб контура"
            value={gapOnly ? 'gap' : 'all'}
            options={[{value:'all',label:'Весь контур'}, {value:'gap',label:'Место разрыва'}]}
            onChange={(value) => setGapOnly(value === 'gap')} />}
        </div>
        {query.isPending ? (
          <div className="flex h-72 items-center justify-center rounded border border-neutral-200 bg-neutral-50">
            <div className="w-64"><Progress label="Проверяем контур по исходнику" /></div>
          </div>
        ) : !verified ? (
          <InlineMessage tone="error">
            Не удалось сверить контур с исходником
            <Button variant="ghost" onClick={() => void query.refetch()}>
              Повторить
            </Button>
          </InlineMessage>
        ) : (
          <>
            <ContourPreview data={data!} gapOnly={gapOnly} />
            <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs">
              <span className="text-blue-700">
                Исходный контур
              </span>
              <span className="text-green-700">
                Подтверждаемая площадь
              </span>
              <span className="text-amber-700">Соединение концов</span>
            </div>
          </>
        )}
        <Disclosure variant="plain" title="Источник контура">
          <dl className="m-0 grid gap-2 text-xs">
            <div><dt className="text-neutral-500">Слой</dt><dd className="m-0 wrap-anywhere">{proposal.layer}</dd></div>
            <div><dt className="text-neutral-500">Объект</dt><dd className="m-0">{proposal.source.handle}</dd></div>
          </dl>
          <div className="mt-2 text-xs text-neutral-500">Исходный чертёж не изменяется</div>
        </Disclosure>
        {error && (
          <InlineMessage tone="error" title="Сохранение не подтверждено">
            {error === 'Failed to fetch' ? 'Нет связи с сервисом' : error}
            {onRefresh && (
              <Button variant="ghost" disabled={busy} onClick={onRefresh}>
                Проверить состояние
              </Button>
            )}
          </InlineMessage>
        )}
      </div>
    </Dialog>
  );
}

export function ContourPreview({
  data,
  gapOnly,
}: {
  data: NativeAreaPreview;
  gapOnly: boolean;
}) {
  const start = data.source_path[0];
  const end = data.source_path.at(-1);
  if (!start || !end) return null;
  const points = gapOnly
    ? [start, end]
    : [...data.source_path, ...data.proposed_rings.flat()];
  let minX = Infinity,
    minY = Infinity,
    maxX = -Infinity,
    maxY = -Infinity;
  for (const [x, y] of points) {
    minX = Math.min(minX, x);
    maxX = Math.max(maxX, x);
    minY = Math.min(minY, -y);
    maxY = Math.max(maxY, -y);
  }
  const width = maxX - minX,
    height = maxY - minY;
  const size = Math.max(width, height, gapOnly ? 0.01 : 1);
  const pad = size * 0.15;
  // Keep millimetre gaps near the SVG origin: world-coordinate magnitudes
  // otherwise swallow the tiny endpoint markers during browser rendering.
  const path = (ring: number[][]) =>
    ring.map((p, i) => `${i ? 'L' : 'M'}${p[0] - minX},${-p[1] - minY}`).join(' ');
  return (
    <svg
      role="img"
      aria-label={
        !data.proposed_rings.length ? 'Исходная открытая линия' : gapOnly
          ? 'Разрыв исходной линии и предлагаемое замыкание'
          : 'Исходная линия и площадь после замыкания'
      }
      className="h-72 w-full rounded border border-neutral-200 bg-neutral-50"
      viewBox={`${-pad} ${-pad} ${Math.max(width, size * 0.02) + 2 * pad} ${Math.max(height, size * 0.02) + 2 * pad}`}
    >
      <path
        d={data.proposed_rings.map((ring) => `${path(ring)} Z`).join(' ')}
        fill="#16a34a"
        fillOpacity="0.15"
        fillRule="evenodd"
        stroke="#15803d"
        strokeWidth="2"
        vectorEffect="non-scaling-stroke"
      />
      <path
        d={path(data.source_path)}
        fill="none"
        stroke="#2855ff"
        strokeWidth="2"
        vectorEffect="non-scaling-stroke"
      />
      {!!data.proposed_rings.length && <path d={path([end, start])} fill="none" stroke="#b45309"
        strokeWidth="3" strokeDasharray="5 3" vectorEffect="non-scaling-stroke" />}
      {[start, end].map((p, i) => (
        <circle
          key={i}
          cx={p[0] - minX}
          cy={-p[1] - minY}
          r={size * 0.016}
          fill="#fff"
          stroke="#b45309"
          strokeWidth="2"
          vectorEffect="non-scaling-stroke"
        />
      ))}
    </svg>
  );
}
