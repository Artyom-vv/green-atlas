import type {
  CadDrawingPassport,
  CadDrawingSelection,
  CadPackagePassport,
  CadPreviewRequest,
} from '@green/api-client';

export const drawingLabel = (path: string) =>
  path.split('/').slice(-2).join(' / ');

export function readableDrawings(passport: CadPackagePassport) {
  return passport.drawings.filter(
    (drawing) =>
      drawing.status === 'readable' &&
      drawing.source_sha256 &&
      drawing.normalized_sha256,
  );
}

function drawingSelection(drawing: CadDrawingPassport): CadDrawingSelection {
  if (!drawing.source_sha256 || !drawing.normalized_sha256)
    throw new Error('Паспорт не содержит проверенной версии чертежа.');
  return {
    path: drawing.path,
    source_sha256: drawing.source_sha256,
    normalized_sha256: drawing.normalized_sha256,
  };
}

export function previewRequest(
  intakeId: string,
  passport: CadPackagePassport,
  source: CadDrawingPassport,
  boundary: CadDrawingPassport,
  handle: string,
): CadPreviewRequest {
  const candidate = boundary.inspection?.boundary_catalog?.candidates?.find(
    (item) => item.handle === handle,
  );
  if (!candidate?.available_for_preview)
    throw new Error('Выберите доступный авторский контур.');
  return {
    intake_operation_id: intakeId,
    manifest_sha256: passport.manifest_sha256,
    source: drawingSelection(source),
    boundary: { ...drawingSelection(boundary), handle },
  };
}
