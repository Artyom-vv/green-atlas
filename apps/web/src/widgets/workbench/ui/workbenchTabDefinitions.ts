import { featureAvailability } from '@/shared/config/featureAvailability';
import { Bot, Settings2, SlidersHorizontal } from 'lucide-react';
const rightTabDefinitions = [
  { id: 'inspector', label: 'Свойства', icon: SlidersHorizontal },
  { id: 'tool', label: 'Инструмент', icon: Settings2 },
  { id: 'assistant', label: 'Помощник', icon: Bot },
] as const;
export const rightTabs = rightTabDefinitions.filter(
  (tab) => tab.id !== 'assistant' || featureAvailability.assistant,
);
export const resultsTabs = [
  { id: 'checks', label: 'Проверки' },
  { id: 'schedule', label: 'Ведомость' },
  { id: 'history', label: 'История' },
] as const;
