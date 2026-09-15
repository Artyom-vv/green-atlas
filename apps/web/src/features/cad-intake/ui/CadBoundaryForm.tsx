import { useState } from 'react';
import type { CadPackagePassport, CadPreviewRequest } from '@green/api-client';
import { Button, Text } from '@green/ui';
import { Map } from 'lucide-react';
import { previewRequest, readableDrawings } from '../model/cadBoundaryChoices';
import { CadDrawingSelect } from './CadDrawingSelect';
import { CadContourSelect } from './CadContourSelect';

interface Props {
  intakeId: string;
  passport: CadPackagePassport;
  disabled: boolean;
  onStart: (request: CadPreviewRequest) => void;
}

export function CadBoundaryForm({
  intakeId,
  passport,
  disabled,
  onStart,
}: Props) {
  const drawings = readableDrawings(passport);
  const [sourcePath, setSource] = useState(passport.entry);
  const [boundaryPath, setBoundary] = useState(passport.entry);
  const [handle, setHandle] = useState('');
  const source = drawings.find((drawing) => drawing.path === sourcePath);
  const boundary = drawings.find((drawing) => drawing.path === boundaryPath);
  const catalog = boundary?.inspection?.boundary_catalog;
  const candidate = catalog?.candidates?.find((item) => item.handle === handle);
  return (
    <div className="grid min-w-0 gap-3">
      <div className="grid min-w-0 gap-3 sm:grid-cols-2">
        <CadDrawingSelect
          label="Чертёж для просмотра"
          drawings={drawings}
          value={sourcePath}
          disabled={disabled}
          onChange={(path) => {
            setSource(path);
            setBoundary(path);
            setHandle('');
          }}
        />
        <CadDrawingSelect
          label="Чертёж с границей"
          drawings={drawings}
          value={boundaryPath}
          disabled={disabled}
          onChange={(path) => {
            setBoundary(path);
            setHandle('');
          }}
        />
      </div>
      <CadContourSelect
        catalog={catalog}
        value={handle}
        disabled={disabled}
        onChange={setHandle}
      />
      <Text variant="caption">
        Откроется предварительная карта выбранной территории. Полнота комплекта
        и ограничения для расчёта проверяются отдельно.
      </Text>
      <Button
        variant="primary"
        icon={Map}
        disabled={disabled || !source || !candidate?.available_for_preview}
        onClick={() =>
          source &&
          boundary &&
          onStart(previewRequest(intakeId, passport, source, boundary, handle))
        }
      >
        Подготовить предварительную карту
      </Button>
    </div>
  );
}
