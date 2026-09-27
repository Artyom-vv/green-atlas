import type { FC } from 'react';
import { Button, Disclosure, InlineMessage, Progress, Text } from '@green/ui';
import { Map as MapIcon } from 'lucide-react';
import { Link } from 'react-router-dom';
import { FlowDocument } from '@/widgets/project-flow';
import { DataPassportPanel } from '@/entities/source-data/ui/DataPassportPanel';
import { LayerMappingWorkspace } from '@/entities/source-data/ui/LayerMappingWorkspace';
import { LayerRecognitionStatus } from '@/entities/source-data/ui/LayerRecognitionStatus';
import type { SourceReadOnlyDocumentProps } from './SourceReadOnlyDocument.props';
import { SourceSection } from './SourceSection';
import { SourceReadIssues } from './SourceReadIssues';
import { NativeFaceReview } from './NativeFaceReview';
export const SourceReadOnlyDocument: FC<SourceReadOnlyDocumentProps> = ({
  dataPassportQuery,
  layers,
  mappings,
  setMappings,
  sourceWarnings,
  downloadSource,
  onPlan,
  startSourceEditing,
  reviewOnly,
  layerRecognition,
  layerRecognitionQuery,
  retryLayerRecognition,
  projectQuery,
}) => (
  <FlowDocument
    title="Исходные данные"
    footer={
      <>
        {!reviewOnly && (
          <Button variant="secondary" onClick={startSourceEditing}>
            Уточнить слои и контуры
          </Button>
        )}
        <Button
          variant="secondary"
          onClick={() => {
            window.location.href = downloadSource();
          }}
        >
          Скачать исходный DXF
        </Button>
        <Button variant="primary" icon={MapIcon} onClick={onPlan}>
          Вернуться к плану
        </Button>
      </>
    }
  >
    <div className="space-y-3">
      <SourceSection
        number="01"
        title="Полнота данных"
        description={
          dataPassportQuery.data?.summary ??
          'Что участвует в расчёте и где есть пропуски'
        }
        defaultOpen={false}
      >
        {dataPassportQuery.data ? (
          <DataPassportPanel passport={dataPassportQuery.data} header={null} />
        ) : dataPassportQuery.isError ? (
          <InlineMessage tone="error">
            Не удалось загрузить сведения об исходных данных{' '}
            <Button
              variant="ghost"
              onClick={() => void dataPassportQuery.refetch()}
            >
              Повторить
            </Button>
          </InlineMessage>
        ) : (
          <Progress label="Загружаем сведения об исходных данных" />
        )}
      </SourceSection>
      <SourceSection
        number="02"
        title="Слои"
        description={`Слои чертежа (${layers.length})`}
        defaultOpen={false}
      >
        <LayerRecognitionStatus
          recognition={layerRecognition}
          loading={layerRecognitionQuery.isLoading}
          error={
            layerRecognitionQuery.isError || retryLayerRecognition?.isError
          }
          retrying={retryLayerRecognition?.isPending}
          onRetry={() => retryLayerRecognition?.mutate()}
        />
        <LayerMappingWorkspace
          recognition={layerRecognition}
          readOnly
          layers={layers}
          mappings={mappings}
          onChange={setMappings}
        />
      </SourceSection>
      <SourceSection
        number="03"
        title="Чтение файла"
        description="Собранные области и замечания AutoCAD"
        defaultOpen={false}
      >
        <div className="flex flex-wrap gap-2">
          {projectQuery.data && (
            <NativeFaceReview project={projectQuery.data} />
          )}
          {projectQuery.data?.source_file?.cad_snapshot_provenance
            ?.live_capture && (
            <SourceReadIssues
              projectId={projectQuery.data.id ?? ''}
              sourceSha={projectQuery.data.source_file.content_sha256 ?? ''}
            />
          )}
        </div>
        {!!sourceWarnings.length && (
          <Disclosure
            variant="panel"
            title={`Замечания к исходному файлу (${sourceWarnings.length})`}
          >
            <ul className="m-0 list-disc space-y-1 pl-5 text-xs">
              {sourceWarnings.map((warning) => (
                <li className="wrap-anywhere" key={warning}>
                  {warning}
                </li>
              ))}
            </ul>
          </Disclosure>
        )}
      </SourceSection>
      <Text as="p" variant="caption">
        Другой чертёж —{' '}
        <Link
          className="text-blue-700 underline underline-offset-2"
          to="/projects/new/import"
        >
          новый проект
        </Link>
      </Text>
    </div>
  </FlowDocument>
);
