import { apiRequest } from './api';

export interface ModelSelection {
  model_id: string;
  config_version: string;
}

export interface AuthorizedModelOption {
  id: string;
  name: string;
  capabilities: { text: boolean; vision: boolean };
  max_output_tokens: number;
  is_default: boolean;
  config_version: string;
}

export interface ModelRouteOptions {
  route: { code: string; name: string; module: string };
  default_model_id: string | null;
  models: AuthorizedModelOption[];
}

function validOptions(value: unknown, routeCode: string): value is ModelRouteOptions {
  if (!value || typeof value !== 'object') return false;
  const item = value as ModelRouteOptions;
  return !!item.route && item.route.code === routeCode && typeof item.route.name === 'string'
    && (item.default_model_id === null || typeof item.default_model_id === 'string') && Array.isArray(item.models)
    && item.models.every(model => typeof model.id === 'string' && typeof model.name === 'string'
      && typeof model.config_version === 'string' && Number.isInteger(model.max_output_tokens)
      && !!model.capabilities && typeof model.capabilities.text === 'boolean'
      && typeof model.capabilities.vision === 'boolean' && typeof model.is_default === 'boolean');
}

export async function getModelRouteOptions(routeCode: string, signal?: AbortSignal): Promise<ModelRouteOptions> {
  if (!/^[a-z][a-z0-9_-]{1,99}$/.test(routeCode)) throw new Error('模型能力参数无效。');
  const result = await apiRequest<unknown>(`/api/models/routes/${encodeURIComponent(routeCode)}/options/`, { signal });
  if (!validOptions(result, routeCode)) throw new Error('可用模型列表格式无效。');
  return result;
}

export function selectionFor(options: ModelRouteOptions, modelId: string): ModelSelection | null {
  const model = options.models.find(item => item.id === modelId);
  return model ? { model_id: model.id, config_version: model.config_version } : null;
}
