export type GrowthHorizon = number | undefined;

export function GrowthHorizonControl({ value, onChange }: { value: GrowthHorizon; onChange: (value: GrowthHorizon) => void }) {
  const year = value ?? 0;
  return <section className="growth-horizon-control" aria-label="Прогноз роста"><div className="growth-horizon-control__heading"><h3>Горизонт</h3><output>{year === 0 ? 'Сейчас' : `${year} лет`}</output></div><input aria-label="Горизонт прогноза" type="range" min="0" max="40" step="1" value={year} onChange={(event) => onChange(Number(event.target.value))} /><div className="growth-horizon-control__ticks" aria-hidden="true"><span>0</span><span>10</span><span>20</span><span>30</span><span>40</span></div><p>Прогнозный диапазон, не нормативный отступ</p></section>;
}
