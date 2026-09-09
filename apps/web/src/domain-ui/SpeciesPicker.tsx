import { useState } from 'react';
import type { SpeciesRevision } from '@green/api-client';
import { Dialog, TextInput } from '@green/ui';
import { Check, Search, ImageOff } from 'lucide-react';
import { speciesPhotos } from './speciesPhotos';
import './workspace-modules.css';

export function SpeciesPhoto({ species, credits = false }: { species: SpeciesRevision; credits?: boolean }) {
  const photo = speciesPhotos[species.species_id];
  const [failed, setFailed] = useState(false);
  return <figure className="species-photo">{photo && !failed ? <img src={photo.url} alt={`${species.common_name}: ${photo.detail}`} loading="lazy" onError={() => setFailed(true)} /> : <div className="species-photo__missing"><ImageOff size={24} /><span>Фото недоступно</span></div>}{credits && photo ? <figcaption>{photo.detail}. <a href={photo.source} target="_blank" rel="noreferrer">{photo.author}</a> / <a href={photo.licenseUrl} target="_blank" rel="noreferrer">{photo.license}</a></figcaption> : null}</figure>;
}

export function SpeciesCatalog({ species, value, onChange, disabled = false, loading = false }: { species: SpeciesRevision[]; value?: string; onChange: (id: string) => void; disabled?: boolean; loading?: boolean }) {
  const [query, setQuery] = useState('');
  const filtered = species.filter(item => `${item.common_name} ${item.scientific_name}`.toLocaleLowerCase('ru').includes(query.toLocaleLowerCase('ru').trim()));
  return <div className="species-catalog"><label className="catalog-search"><span>Поиск породы</span><TextInput aria-label="Поиск в каталоге пород" placeholder="Название или латинское имя" value={query} onChange={event => setQuery(event.target.value)} /></label>
    {loading ? <p role="status">Загружаем породы для выбранных участков</p> : <div className="species-catalog__grid">{filtered.map(item => <article className={value === item.id ? 'is-selected' : ''} key={item.id}>
      <SpeciesPhoto species={item} /><div className="species-catalog__identity"><strong>{item.common_name}</strong><span>{item.scientific_name}</span></div><p>Высота {item.mature_height_min_m}–{item.mature_height_max_m} м<br />Крона {item.mature_crown_diameter_min_m}–{item.mature_crown_diameter_max_m} м</p>
      <button className="species-catalog__choose" type="button" disabled={disabled} aria-label={value === item.id ? `Выбрано: ${item.common_name}` : `Выбрать: ${item.common_name}`} aria-pressed={value === item.id} onClick={() => onChange(item.id)}>{value === item.id ? <Check size={18} /> : null}</button>
    </article>)}</div>}
    {!loading && !filtered.length ? <p role="status">По этому запросу пород нет. Измените название.</p> : null}
    <details className="species-catalog__credits"><summary>О фотографиях и источниках</summary><p>Облик вида, а не конкретный посадочный материал. Размеры указаны для взрослого растения.</p>{filtered.map(item => { const photo = speciesPhotos[item.species_id]; return photo ? <p key={item.id}>{item.common_name}: <a href={photo.source} target="_blank" rel="noreferrer">{photo.author}</a>, <a href={photo.licenseUrl} target="_blank" rel="noreferrer">{photo.license}</a></p> : null; })}</details>
  </div>;
}

export function SpeciesPicker({ species, value, onChange, disabled, onBrowse, label = 'Выбрать породу' }: { species: SpeciesRevision[]; value?: string; onChange: (id: string) => void; disabled?: boolean; label?: string; onBrowse?: () => void }) {
  const [open, setOpen] = useState(false);
  const selected = species.find(item => item.id === value);
  return <><button className="species-picker" type="button" aria-label={label} disabled={disabled} onClick={() => onBrowse ? onBrowse() : setOpen(true)}>{selected ? <SpeciesPhoto key={selected.id} species={selected} /> : <Search size={20} />}<span><strong>{selected?.common_name ?? 'Выбрать породу'}</strong><small>{selected?.scientific_name ?? 'Каталог с фотографиями'}</small></span><Search size={16} /></button>{!onBrowse ? <Dialog open={open} title="Каталог пород" size="wide" onClose={() => setOpen(false)}><SpeciesCatalog species={species} value={value} onChange={id => { onChange(id); setOpen(false); }} /></Dialog> : null}</>;
}
