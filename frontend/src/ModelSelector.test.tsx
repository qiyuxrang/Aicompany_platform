import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import ModelSelector from './ModelSelector';

const models = vi.hoisted(() => ({ getModelRouteOptions: vi.fn() }));
vi.mock('./model-selection-api', async original => ({ ...await original<typeof import('./model-selection-api')>(), ...models }));
afterEach(() => { cleanup(); models.getModelRouteOptions.mockReset(); });

it('shows only safe authorized names, selects the default and pins its configuration version', async () => {
  models.getModelRouteOptions.mockResolvedValue({
    route: { code: 'hr_jd_draft', name: 'JD生成', module: 'hr' }, default_model_id: 'public-a',
    models: [
      { id: 'public-a', name: '企业通用模型', capabilities: { text: true, vision: false }, max_output_tokens: 4096, is_default: true, config_version: 'v-a' },
      { id: 'public-b', name: '高质量长文模型', capabilities: { text: true, vision: true }, max_output_tokens: 8192, is_default: false, config_version: 'v-b' },
    ],
  });
  const changed = vi.fn();
  const view = render(<ModelSelector route="hr_jd_draft" value={null} onChange={changed} label="JD 生成模型" />);
  expect(await screen.findByRole('option', { name: '企业通用模型（默认）' })).toBeTruthy();
  await waitFor(() => expect(changed).toHaveBeenCalledWith({ model_id: 'public-a', config_version: 'v-a' }));
  view.rerender(<ModelSelector route="hr_jd_draft" value={{ model_id: 'public-a', config_version: 'v-a' }} onChange={changed} label="JD 生成模型" />);
  await userEvent.selectOptions(screen.getByLabelText('JD 生成模型'), 'public-b');
  expect(changed).toHaveBeenLastCalledWith({ model_id: 'public-b', config_version: 'v-b' });
  expect(document.body.textContent).not.toContain('api_key');
  expect(document.body.textContent).not.toContain('provider');
});

it('filters out models without the capability required by the business action', async () => {
  models.getModelRouteOptions.mockResolvedValue({ route: { code: 'vision', name: '视觉', module: 'hr' }, default_model_id: null, models: [
    { id: 'text', name: '文本模型', capabilities: { text: true, vision: false }, max_output_tokens: 1000, is_default: false, config_version: 't' },
  ] });
  render(<ModelSelector route="vision" requiredCapability="vision" value={null} onChange={() => undefined} />);
  expect(await screen.findByRole('option', { name: '暂无可用模型' })).toBeTruthy();
  expect(screen.queryByText('文本模型')).toBeNull();
});
