import { Button, Text } from '@green/ui';

interface AutoCadProjectInstructionProps {
  onBack: () => void;
}

export function AutoCadProjectInstruction({
  onBack,
}: AutoCadProjectInstructionProps) {
  return (
    <main className="mx-auto flex w-full max-w-160 flex-col gap-6 px-6 py-12">
      <div className="space-y-3">
        <Text as="h1" variant="pageHeading">
          Новый проект из AutoCAD
        </Text>
        <Text tone="muted">
          Green Atlas получает чертёж через установленный плагин. Передача
          автоматически создаёт проект в библиотеке.
        </Text>
      </div>
      <ol className="list-decimal space-y-4 pl-6">
        <li>Откройте нужный DWG или DXF в AutoCAD.</li>
        <li>В меню Green Atlas выберите «Открыть в Green Atlas…».</li>
        <li>Дождитесь подготовки. Green Atlas откроет переданный проект.</li>
      </ol>
      <Text tone="muted">
        Новый снимок создаёт отдельный проект. Предыдущие планы остаются в
        библиотеке.
      </Text>
      <div>
        <Button variant="secondary" onClick={onBack}>
          К проектам
        </Button>
      </div>
    </main>
  );
}
