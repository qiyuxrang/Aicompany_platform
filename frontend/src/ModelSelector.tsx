import { useEffect, useRef, useState } from 'react';
import { getModelRouteOptions, ModelRouteOptions, ModelSelection, selectionFor } from './model-selection-api';
import './model-selector.css';

export default function ModelSelector({ route, value, onChange, label = '使用模型', disabled = false, requiredCapability = 'text' }: {
  route: string;
  value: ModelSelection | null;
  onChange: (selection: ModelSelection | null) => void;
  label?: string;
  disabled?: boolean;
  requiredCapability?: 'text' | 'vision';
}) {
  const [options, setOptions] = useState<ModelRouteOptions | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const onChangeRef = useRef(onChange); onChangeRef.current = onChange;

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(''); setOptions(null);
    getModelRouteOptions(route, controller.signal).then(result => {
      if (controller.signal.aborted) return;
      const allowed = result.models.filter(model => model.capabilities[requiredCapability]);
      const filtered = { ...result, models: allowed };
      setOptions(filtered);
      if (!value || !allowed.some(model => model.id === value.model_id && model.config_version === value.config_version)) {
        const preferred = allowed.find(model => model.id === result.default_model_id) || allowed.find(model => model.is_default) || allowed[0];
        onChangeRef.current(preferred ? { model_id: preferred.id, config_version: preferred.config_version } : null);
      }
    }).catch(caught => {
      if (!controller.signal.aborted) { setError(caught instanceof Error ? caught.message : '无法读取已授权模型。'); onChangeRef.current(null); }
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  // A changed route or required capability must invalidate the previous selection.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route, requiredCapability]);

  return <div className="model-selector">
    <label>{label}
      <select aria-label={label} value={value?.model_id || ''} disabled={disabled || loading || !options?.models.length}
        onChange={event => onChange(selectionFor(options!, event.target.value))}>
        {loading && <option value="">正在读取已授权模型…</option>}
        {!loading && !options?.models.length && <option value="">暂无可用模型</option>}
        {options?.models.map(model => <option key={`${model.id}:${model.config_version}`} value={model.id}>{model.name}{model.is_default ? '（默认）' : ''}</option>)}
      </select>
    </label>
    {options && value && <span className="model-selector-meta">{options.models.find(model => model.id === value.model_id)?.capabilities.vision ? '支持文本与图片' : '支持文本'} · 任务提交后固定当前配置版本</span>}
    {error && <span className="model-selector-error" role="status">{error}</span>}
  </div>;
}
