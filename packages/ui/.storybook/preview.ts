import type { Preview } from '@storybook/react';
import { createElement } from 'react';
import '../src/styles.css';

const preview: Preview = {
  decorators: [
    (Story) =>
      createElement(
        'div',
        { className: 'font-sans text-neutral-800' },
        createElement(Story),
      ),
  ],
  parameters: {
    actions: { argTypesRegex: '^on[A-Z].*' },
    controls: { matchers: { color: /(background|color)$/i, date: /Date$/i } },
  },
};
export default preview;
