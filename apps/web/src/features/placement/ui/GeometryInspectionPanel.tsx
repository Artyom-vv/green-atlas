import { Button } from '@green/ui';
import type { CandidateInspection, InspectionPlant } from '../model/useCandidateInspection';
import { GeometryEvidenceList } from './GeometryEvidenceList';
import { evidenceMeters as meters } from '../model/evidencePresentation';

/** A map probe explains restrictions, never saves a plant or confirms a layer. */
export function GeometryInspectionPanel({ inspection, plant }: { inspection?: CandidateInspection; plant?: InspectionPlant }) {
  if (!inspection?.canExplain) return null;
  const { diagnosis, diagnosing, picking, checking } = inspection;
  const evidence = diagnosis?.geometry_evidence;
  return (
    <section className="grid gap-3 rounded border border-neutral-300 p-3 text-xs" aria-label="Проверка точки на карте">
      <Button variant="secondary" disabled={checking} onClick={() => inspection.startExplaining(plant)}>
        Проверить точку на карте
      </Button>
      {diagnosing && picking && <p className="m-0" role="status">Выберите точку на карте</p>}
      {diagnosing && checking && <p className="m-0" role="status">Проверяем объекты AutoCAD</p>}
      {diagnosing && inspection.error && <p className="m-0 text-red-700" role="alert">{inspection.error}</p>}
      {diagnosis && <>
        <p className="m-0 font-semibold">{evidence?.state === 'available'
          ? 'В этой точке геометрия не запрещает посадку'
          : evidence?.state === 'excluded' ? 'В этой точке есть запрет'
            : 'Для этой точки нужны уточнения'}</p>
        {diagnosis.status !== 'allowed' && <p className="m-0">{diagnosis.reason}</p>}
        {!evidence && <p className="m-0">Подробный ответ AutoCAD недоступен</p>}
        {evidence && <>
          <dl className="m-0 grid gap-1">
            <div className="flex justify-between gap-2"><dt>Проверяется</dt><dd className="m-0">{diagnosis.kind === 'shrub' ? 'Кустарник' : 'Дерево'}</dd></div>
            <div className="flex justify-between gap-2"><dt>Радиус посадки</dt><dd className="m-0">{meters(evidence.radius_m)}</dd></div>
            <div className="flex justify-between gap-2"><dt>Расчётная крона</dt><dd className="m-0">{meters(evidence.canopy_radius_m)}</dd></div>
            <div className="flex justify-between gap-2"><dt>Расчётные корни</dt><dd className="m-0">{meters(evidence.root_radius_m)}</dd></div>
          </dl>
          <p className="m-0 text-neutral-600">Проверена точка с выбранными параметрами растения, не весь участок</p>
          <GeometryEvidenceList causes={evidence.causes ?? []} />
          {evidence.unlocated_objects > 0 && <p className="m-0">Объекты без положения на карте: {evidence.unlocated_objects}</p>}
        </>}
      </>}
    </section>
  );
}
