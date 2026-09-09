import { useState, type ReactNode } from 'react';
import type { PlanObject, PlantingZoneAssignment } from '@green/api-client';
import { ChevronDown, ChevronRight, Crosshair, Settings2, Sprout } from 'lucide-react';
import { IconButton } from '@green/ui';
import './workspace-explorer.css';
import './workspace-modules.css';

export function WorkspaceExplorer({ zones, objects, selectedZoneIds, layers, sourceName, sourceCount, onManageZones, onManagePlantings, onSource, onClose, collapsed = false, disabled = false }: {
  zones: PlantingZoneAssignment[]; objects: PlanObject[]; selectedIds: string[]; selectedZoneIds: string[];
  speciesNames: Map<string, string>; layers: ReactNode; sourceName?: string; sourceCount: number;
  onSelect: (ids: string[]) => void; onZone: (zone: PlantingZoneAssignment) => void;
  onZonesChange?: (ids: string[]) => void; collapsed?: boolean; disabled?: boolean; zoneSelectionDisabled?: boolean;
  onManageZones: () => void; onSource: () => void; onClose: () => void;
  onManagePlantings?: () => void;
}) {
  const [tab, setTab] = useState<'project' | 'layers'>('project');
  return <div className="workspace-explorer">
    <nav className="explorer-tabs" aria-label="Обозреватель проекта">
      <button type="button" aria-pressed={tab === 'project'} onClick={() => { setTab('project'); if (collapsed) onClose(); }}>Проект</button>
      <button type="button" aria-pressed={tab === 'layers'} onClick={() => { setTab('layers'); if (collapsed) onClose(); }}>Слои</button>
      <IconButton icon={collapsed ? ChevronRight : ChevronDown} label={collapsed ? 'Развернуть проект' : 'Свернуть проект'} aria-expanded={!collapsed} variant="ghost" onClick={onClose} />
    </nav>
    <div className="module-launchers" hidden={collapsed || tab !== 'project'}>
      <button type="button" onClick={onManageZones}><Crosshair size={18} /><span>Рабочие участки</span><small>{selectedZoneIds.length} / {zones.length}</small></button>
      <button type="button" disabled={disabled} onClick={onManagePlantings}><Sprout size={18} /><span>Посадки проекта</span><small>{objects.length}</small></button>
    </div>
    <div className="explorer-layer-content" hidden={collapsed || tab !== 'layers'}>{layers}</div>
    <div className="explorer-source" hidden={collapsed}><button type="button" title={`${sourceName ?? 'Исходный DXF'}: ${sourceCount.toLocaleString('ru')} объектов`} onClick={onSource}><Settings2 size={14} />Исходные данные</button></div>
  </div>;
}
