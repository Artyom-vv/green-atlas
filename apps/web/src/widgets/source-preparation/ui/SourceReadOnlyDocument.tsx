import type { FC } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button, Disclosure, InlineMessage } from '@green/ui';
import { Download, Pencil } from 'lucide-react';
import { DataPassportPanel } from '@/entities/source-data/ui/DataPassportPanel';
import { LayerMappingWorkspace } from '@/entities/source-data/ui/LayerMappingWorkspace';
import { PassportLoadState } from '@/entities/source-data/ui/passport/PassportLoadState';
import {
  requiresLayerRoleReview,
  toLayerMapping,
} from '@/entities/source-data/model/layerKinds';
import { sourceDataPassportQuery } from '@/features/source-preparation/api/preparationApi';
import type { SourceReadOnlyDocumentProps } from './SourceReadOnlyDocument.props';
import { SourceReviewLayout } from './SourceReviewLayout';
import { SourceReadIssues } from './SourceReadIssues';
import { NativeFaceReview } from './NativeFaceReview';

const sections = [
  {
    id: '02',
    label: 'Геометрия',
    title: 'Геометрия исходника',
    description: 'Собранные области и замечания чтения',
  },
  {
    id: '03',
    label: 'Слои',
    title: 'Назначения слоёв',
    description: 'Как слои чертежа используются в расчёте',
  },
  {
    id: '04',
    label: 'Сведения',
    title: 'Сведения об исходнике',
    description: 'Полнота и ограничения исходных данных',
  },
] as const;

type Section = '02' | '03' | '04';
export const SourceReadOnlyDocument: FC<
  SourceReadOnlyDocumentProps & {
    activeSection: Section;
    onSectionChange: (section: Section) => void;
  }
> = ({
  layers,
  mappings,
  setMappings,
  sourceWarnings,
  downloadSource,
  startSourceEditing,
  reviewOnly,
  layerRecognition,
  projectQuery,
  activeSection,
  onSectionChange,
}) => {
  const dataPassportQuery = useQuery(
    sourceDataPassportQuery(projectQuery.data, activeSection === '04'),
  );
  const pending = layers.some((layer) =>
    requiresLayerRoleReview(mappings[layer.id] ?? toLayerMapping(layer)),
  );
  const invalidBoundary =
    projectQuery.data?.import_status?.mode === 'autocad_live'
      ? layers.find(
          (layer) =>
            mappings[layer.id]?.kind === 'site_border' &&
            layer.boundary_candidate &&
            layer.boundary_candidate.status !== 'usable',
        )
      : undefined;
  const project = projectQuery.data;
  const hasReadIssues = Boolean(
    project?.source_file?.cad_snapshot_provenance?.live_capture,
  );

  return (
    <SourceReviewLayout
      sections={sections}
      activeSection={activeSection}
      onSectionChange={(section) => {
        if (section === '02' || section === '03' || section === '04')
          onSectionChange(section);
      }}
      headerActions={
        <div className="flex max-w-full shrink-0 flex-wrap items-center gap-2">
          {activeSection === '04' && (
            <Button
              variant="ghost"
              startIcon={<Download />}
              onClick={() => {
                window.location.href = downloadSource();
              }}
            >
              Скачать исходный DXF
            </Button>
          )}
          {!reviewOnly && (
            <Button
              variant="secondary"
              startIcon={<Pencil />}
              onClick={() => startSourceEditing(activeSection)}
            >
              {activeSection === '03'
                ? 'Изменить назначения'
                : activeSection === '02'
                  ? 'Уточнить геометрию'
                  : 'Уточнить исходные данные'}
            </Button>
          )}
        </div>
      }
      status={
        invalidBoundary ? (
          <InlineMessage
            tone="warning"
            title="Граница расчёта требует исправления"
          >
            Слой «{invalidBoundary.source_name}» не образует пригодную площадь
            {!reviewOnly && (
              <Button variant="ghost" onClick={() => startSourceEditing('02')}>
                Выбрать контур
              </Button>
            )}
          </InlineMessage>
        ) : undefined
      }
    >
      <section
        role="region"
        aria-label="Геометрия"
        hidden={activeSection !== '02'}
        className="space-y-6"
      >
        <div className="flex flex-wrap gap-2">
          {project && <NativeFaceReview project={project} />}
          {hasReadIssues && (
            <SourceReadIssues
              projectId={project?.id ?? ''}
              sourceSha={project?.source_file?.content_sha256 ?? ''}
            />
          )}
        </div>
        {!!sourceWarnings.length && (
          <Disclosure
            variant="section"
            title={`Замечания к исходному файлу (${sourceWarnings.length})`}
          >
            <ul className="m-0 list-disc space-y-2 pl-5 text-sm">
              {sourceWarnings.map((warning) => (
                <li className="wrap-anywhere" key={warning}>
                  {warning}
                </li>
              ))}
            </ul>
          </Disclosure>
        )}
        {!project?.source_file?.native_session &&
          !hasReadIssues &&
          !sourceWarnings.length && (
            <p className="m-0 px-6 text-sm text-neutral-500 max-[719px]:px-4">
              Сведения о геометрии недоступны
            </p>
          )}
      </section>
      <section role="region" aria-label="Слои" hidden={activeSection !== '03'}>
        <LayerMappingWorkspace
          recognition={layerRecognition}
          readOnly
          informationInHeader
          defaultView={pending ? 'review' : 'list'}
          layers={layers}
          mappings={mappings}
          onChange={setMappings}
        />
      </section>
      <section
        role="region"
        aria-label="Сведения"
        hidden={activeSection !== '04'}
      >
        {dataPassportQuery.isFetching && !dataPassportQuery.data ? (
          <PassportLoadState state="loading" />
        ) : dataPassportQuery.isError ? (
          <PassportLoadState
            state="error"
            onRetry={() => void dataPassportQuery.refetch()}
          />
        ) : dataPassportQuery.data ? (
          <DataPassportPanel passport={dataPassportQuery.data} header={null} />
        ) : null}
      </section>
    </SourceReviewLayout>
  );
};
