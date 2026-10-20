import { Button, InlineMessage, Progress } from '@green/ui';
import { useNavigate, useParams } from 'react-router-dom';
import { AutoCadProjectInstruction } from '@/features/projects/ui/AutoCadProjectInstruction';
import { useSavedProjectEntry } from '@/features/projects/model/useSavedProjectEntry';
import { projectResumeRouteForKind } from '@/entities/project/model/projectResume';

/** Old route links remain usable without exposing the retired upload wizard. */
export function ImportPage() {
  const { projectId = '' } = useParams();
  const navigate = useNavigate();
  const project = useSavedProjectEntry(projectId);
  return (
    <>
      <AutoCadProjectInstruction onBack={() => navigate('/projects')} />
      <section
        className="mx-auto flex w-full max-w-160 flex-col gap-4 px-6 pb-12"
        aria-label="Сохранённый проект"
      >
        {project.isPending ? (
          <Progress label="Проверяем сохранённый проект" />
        ) : project.isError ? (
          <InlineMessage tone="error">
            Не удалось прочитать сохранённый проект.{' '}
            <Button variant="ghost" onClick={() => void project.refetch()}>
              Повторить
            </Button>
          </InlineMessage>
        ) : (
          project.data && (
            <>
              <p className="m-0 text-sm">
                Текущий проект: {project.data.name}. Его исходник и сохранённые
                решения остаются в библиотеке.
              </p>
              <div>
                <Button
                  variant="secondary"
                  onClick={() =>
                    navigate(
                      projectResumeRouteForKind(
                        projectId,
                        project.data.resume_kind,
                      ),
                    )
                  }
                >
                  Вернуться к сохранённому проекту
                </Button>
              </div>
            </>
          )
        )}
      </section>
    </>
  );
}
