/** Native AcDbDimension classes are annotation context, including cached proposals. */
export const annotationEntityTypes = new Set([
  'TEXT',
  'MTEXT',
  'ATTRIB',
  'ATTDEF',
  'DIMENSION',
  'LEADER',
  'MLEADER',
  'AUTOCAD:AcDbMLeader',
  'AUTOCAD:AcDbAlignedDimension',
  'AUTOCAD:AcDbRotatedDimension',
  'AUTOCAD:AcDbRadialDimension',
  'AUTOCAD:AcDbDiametricDimension',
  'AUTOCAD:AcDb2LineAngularDimension',
  'AUTOCAD:AcDb3PointAngularDimension',
  'AUTOCAD:AcDbOrdinateDimension',
  'AUTOCAD:AcDbArcDimension',
  'AUTOCAD:AcDbRadialDimensionLarge',
]);

export function annotationOnly(entityTypes?: Record<string, number>): boolean {
  const kinds = Object.entries(entityTypes ?? {});
  return (
    kinds.length > 0 &&
    kinds.every(([kind, count]) => annotationEntityTypes.has(kind) && count > 0)
  );
}
