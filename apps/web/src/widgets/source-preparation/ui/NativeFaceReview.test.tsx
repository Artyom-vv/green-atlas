import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { NativeFaceReview as Review, Project, SourceObjectContext } from '@green/api-client';
import { preparationApi } from '@/features/source-preparation/api/preparationApi';
import { NativeFaceReview } from './NativeFaceReview';

const sha = 'a'.repeat(64);
const project = { id:'test', state_version:2, geometry_version:3,
  source_file:{content_sha256:sha,native_session:{session_id:'a'.repeat(32)}},
  import_status:{editability:'editable'},
} as unknown as Project;
const review: Review = {source_sha256:sha,items:[1,2].map(i => ({key:String(i).repeat(64),
  anchor:String(i),layer:'Здания',area_m2:100*i,member_count:3,repair_count:1,status:'active',
  path:[[0,0],[10,0],[10,10],[0,0]],repairs:[[[0,0],[.01,0]]],
}))};
const context: SourceObjectContext = {source_sha256:sha,focus_route:'1',extent:[-5,-5,15,15],objects:[],total:0,limited:false};
function open() {
  render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false},mutations:{retry:false}}})}>
    <NativeFaceReview project={project}/></QueryClientProvider>);
  fireEvent.click(screen.getByRole('button',{name:'Собранные области'}));
}
describe('NativeFaceReview', () => {
  beforeEach(() => {
    vi.spyOn(preparationApi,'getNativeFaces').mockResolvedValue(review);
    vi.spyOn(preparationApi,'getSourceObjectContext').mockResolvedValue(context);
    vi.spyOn(preparationApi,'decideNativeFace').mockResolvedValue(project);
  });
  afterEach(() => {cleanup();vi.restoreAllMocks();});
  it('shows the derived area with context and keyboard navigation', async () => {
    open();
    const scene=await screen.findByRole('group',{name:'Выбранный объект и окружение чертежа'});
    expect(screen.getByText('1 / 2')).toBeInTheDocument();
    fireEvent.keyDown(scene,{key:'ArrowDown'});
    expect(screen.getByText('2 / 2')).toBeInTheDocument();
    await waitFor(() => expect(preparationApi.getSourceObjectContext).toHaveBeenCalledWith('test',`native-face:${'1'.repeat(64)}`,2));
  });
  it('revokes only the selected face and shows saving state', async () => {
    let complete!: (value:Project) => void;
    vi.mocked(preparationApi.decideNativeFace).mockImplementation(() => new Promise(resolve => {complete=resolve;}));
    open();
    await screen.findByText('1 / 2');
    fireEvent.click(screen.getByRole('button',{name:'Отменить область'}));
    await screen.findByText('Сохраняем решение');
    expect(preparationApi.decideNativeFace).toHaveBeenCalledWith('test',{source_sha256:sha,key:'2'.repeat(64),rejected:true},{expectedStateVersion:2});
    expect(screen.getByRole('button',{name:'Далее'})).toBeDisabled();
    complete(project);
    await waitFor(() => expect(screen.queryByText('Сохраняем решение')).not.toBeInTheDocument());
  });
});
