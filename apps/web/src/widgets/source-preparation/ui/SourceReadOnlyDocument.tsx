import type { FC } from 'react';
import { Button, Disclosure, InlineMessage, Progress, Text } from '@green/ui';
import { Map as MapIcon } from 'lucide-react';
import { Link } from 'react-router-dom';
import { FlowDocument } from '@/widgets/project-flow';
import { DataPassportPanel } from '@/entities/source-data/ui/DataPassportPanel';
import { LayerMappingTable } from '@/entities/source-data/ui/LayerMappingTable';
import type { SourceReadOnlyDocumentProps } from './SourceReadOnlyDocument.props';
export const SourceReadOnlyDocument: FC<SourceReadOnlyDocumentProps> = ({
  dataPassportQuery,
  layers,
  mappings,
  setMappings,
  sourceWarnings,
  downloadSource,
  onPlan,
}) => (
  <FlowDocument
    title="Исходные данные"
    description="Исходный чертёж и ограничения, учтённые в плане."
    footer={
      <>
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
    <div className="flex flex-col gap-3">
      {dataPassportQuery.data ? (
        <DataPassportPanel passport={dataPassportQuery.data} />
      ) : dataPassportQuery.isError ? (
        <InlineMessage tone="error">
          Не удалось загрузить сведения об исходных данных.{' '}
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
      <Disclosure variant="plain" title={`Слои чертежа (${layers.length})`}>
        <LayerMappingTable
          readOnly
          layers={layers}
          mappings={mappings}
          onChange={setMappings}
        />
      </Disclosure>
      {!!sourceWarnings.length && (
        <Disclosure
          variant="plain"
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
      <Text as="p" variant="caption">
        Слои зафиксированы при создании плана. Для работы с другим чертежом{' '}
        <Link
          className="text-blue-700 underline underline-offset-2"
          to="/projects/new/import"
        >
          создайте новый проект
        </Link>
        .
      </Text>
    </div>
  </FlowDocument>
);
