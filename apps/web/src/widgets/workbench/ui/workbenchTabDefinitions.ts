import { Settings2, SlidersHorizontal } from 'lucide-react';
export const rightTabs = [
  { id: 'inspector', label: 'Свойства', icon: SlidersHorizontal },
  { id: 'tool', label: 'Инструмент', icon: Settings2 },
] as const;
export const resultsTabs = [
  { id: 'checks', label: 'Проверки' },
  { id: 'schedule', label: 'Ведомость' },
  { id: 'history', label: 'История' },
] as const;
