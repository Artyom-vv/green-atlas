import { useNavigate } from 'react-router-dom';
import { AutoCadProjectInstruction } from '@/features/projects/ui/AutoCadProjectInstruction';

export function NewProjectPage() {
  const navigate = useNavigate();
  return <AutoCadProjectInstruction onBack={() => navigate('/projects')} />;
}
