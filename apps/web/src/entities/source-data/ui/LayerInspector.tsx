import { EditorActions, EditorPanel } from '@/shared/ui/inspector/EditorPanel';
import type { Layer } from '@green/api-client';
import { Button, Text } from '@green/ui';
import { Eye, EyeOff, LocateFixed } from 'lucide-react';
import type { FC } from 'react';
import { LAYER_KIND_LABELS } from '../model/layerKinds';

export interface LayerInspectorProps {
  layer: Layer;
  visible: boolean;
  onVisibility: (visible: boolean) => void;
  onFit: () => void;
}
export const LayerInspector: FC<LayerInspectorProps> = ({
  layer,
  visible,
  onVisibility,
  onFit,
}) => (
  <EditorPanel
    title={
      layer.mapped_kind
        ? LAYER_KIND_LABELS[layer.mapped_kind]
        : 'Справочная геометрия'
    }
  >
    <div className="grid gap-3">
      <Text as="p" className="wrap-anywhere">
        {layer.source_name}
      </Text>
      <Text as="p" variant="caption" aria-live="polite">
        {visible
          ? `${layer.object_count} объектов на исходном чертеже.`
          : 'Слой скрыт'}
      </Text>
      <EditorActions grid>
        <Button
          variant="secondary"
          icon={visible ? <EyeOff /> : <Eye />}
          onClick={() => onVisibility(!visible)}
        >
          {visible ? 'Скрыть слой' : 'Показать слой'}
        </Button>
        <Button
          variant="secondary"
          icon={<LocateFixed />}
          disabled={!visible}
          onClick={onFit}
        >
          На карте
        </Button>
      </EditorActions>
    </div>
  </EditorPanel>
);
