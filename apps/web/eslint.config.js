import js from '@eslint/js';
import globals from 'globals';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import tseslint from 'typescript-eslint';

const relativeImportPatterns = (target) => {
  const paths = [`@/${target}`, `@/${target}/*`];

  for (let depth = 1; depth <= 5; depth += 1) {
    const prefix = '../'.repeat(depth);
    paths.push(`${prefix}${target}`, `${prefix}${target}/*`);
  }

  return paths;
};

const layerConfig = ({ files, ignores, allowApi, higherLayers = [] }) => ({
  files,
  ...(ignores ? { ignores } : {}),
  rules: {
    'no-restricted-imports': 'off',
    '@typescript-eslint/no-restricted-imports': [
      'error',
      {
        paths: allowApi
          ? []
          : [
              {
                name: '@green/api-client',
                allowTypeImports: true,
                message:
                  'UI layers consume application data through a feature or entity boundary.',
              },
            ],
        patterns: [
          ...(allowApi
            ? []
            : [
                {
                  group: relativeImportPatterns('api'),
                  allowTypeImports: true,
                  message:
                    'UI layers must not import the API implementation directly.',
                },
              ]),
          {
            group: relativeImportPatterns('domain-ui'),
            message:
              'New UI layers must not import the legacy domain-ui layer.',
          },
          ...higherLayers.map((target) => ({
            group: relativeImportPatterns(target),
            message: `This layer must not import the higher ${target} layer.`,
          })),
        ],
      },
    ],
  },
});

export default tseslint.config(
  { ignores: ['dist', 'playwright-report', 'test-results'] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ['**/*.{ts,tsx}'],
    languageOptions: { ecmaVersion: 2022, globals: globals.browser },
    plugins: { 'react-hooks': reactHooks, 'react-refresh': reactRefresh },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': [
        'warn',
        { allowConstantExport: true },
      ],
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_' },
      ],
      '@typescript-eslint/no-empty-object-type': [
        'error',
        { allowInterfaces: 'with-single-extends' },
      ],
    },
  },
  layerConfig({
    files: ['src/app/**/*.{ts,tsx}'],
    allowApi: false,
  }),
  layerConfig({
    files: ['src/pages/**/*.{ts,tsx}'],
    allowApi: false,
  }),
  layerConfig({
    files: ['src/widgets/**/*.{ts,tsx}'],
    allowApi: false,
    higherLayers: ['app', 'pages'],
  }),
  layerConfig({
    files: ['src/features/**/*.{ts,tsx}'],
    allowApi: true,
    higherLayers: ['app', 'pages', 'widgets'],
  }),
  layerConfig({
    files: ['src/entities/**/*.{ts,tsx}'],
    allowApi: true,
    higherLayers: ['app', 'pages', 'widgets', 'features'],
  }),
  layerConfig({
    files: ['src/shared/**/*.{ts,tsx}'],
    allowApi: true,
    higherLayers: ['app', 'pages', 'widgets', 'features', 'entities'],
  }),
  layerConfig({
    files: ['src/pages/**/*.test.{ts,tsx}'],
    allowApi: true,
  }),
);
