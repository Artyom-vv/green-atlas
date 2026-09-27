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
  const [boundaryPath, setBoundary] = useState(passport.entry);
  const [handle, setHandle] = useState('');
  const source = drawings.find((drawing) => drawing.path === passport.entry);
  const boundary = drawings.find((drawing) => drawing.path === boundaryPath);
  const catalog = boundary?.inspection?.boundary_catalog;
  const candidate = catalog?.candidates?.find((item) => item.handle === handle);
  return (
    <div className="grid min-w-0 gap-3">
      <div className="grid min-w-0 gap-3">
        <CadDrawingSelect
          label="Граница территории"
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
        Все DXF комплекта попадут на карту. Перед расчётом останется подтвердить
        только спорные слои.
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
        Подготовить территорию
      </Button>
    </div>
  );
}
