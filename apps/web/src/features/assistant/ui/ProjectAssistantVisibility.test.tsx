import { featureAvailability } from '@/shared/config/featureAvailability';
import { cleanup, render } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { ProjectAssistantSidebar } from './ProjectAssistantSidebar';
import { ProjectAssistantTrigger } from './ProjectAssistantTrigger';

afterEach(cleanup);

describe('assistant availability', () => {
  it('omits the trigger and panel without starting their context or effects', () => {
    expect(featureAvailability.assistant).toBe(false);
    const { container } = render(
      <>
        <ProjectAssistantTrigger />
        <ProjectAssistantSidebar />
      </>,
    );

    expect(container).toBeEmptyDOMElement();
  });
});
