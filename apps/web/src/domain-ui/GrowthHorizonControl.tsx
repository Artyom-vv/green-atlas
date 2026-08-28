import { Button } from '@green/ui';

export type GrowthHorizon = 5 | 10 | 20 | undefined;

export function GrowthHorizonControl({ value, onChange }: { value: GrowthHorizon; onChange: (value: GrowthHorizon) => void }) {
  return <section className="growth-horizon-control" aria-label="Прогноз роста"><h3>Прогноз роста</h3><div><Button variant={value === undefined ? 'primary' : 'secondary'} controlSize="compact" onClick={() => onChange(undefined)}>Сейчас</Button>{([5, 10, 20] as const).map((year) => <Button key={year} variant={value === year ? 'primary' : 'secondary'} controlSize="compact" onClick={() => onChange(year)}>{year} лет</Button>)}</div><p>Крона и корни показаны диапазоном.</p></section>;
}
