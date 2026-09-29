import { useEffect, useState } from 'react';
import { apiRequest, isApiError } from '../api';
import { CenterLink } from './shared';
import BusinessProjects from './BusinessProjects';
import './business-boards.css';

type Department = 'engineering' | 'finance' | 'presales';
type Board = {
  department: Department; title: string; available: boolean; snapshot_id: string;
  fields: Record<string, string>; statuses: string[];
  metrics: { key: string; label: string; value: number | string | null; unit: string }[];
  distribution: { label: string; count: number }[]; records: Record<string, string>[];
  total: number; filtered_count: number; scope: string; currency: string;
  source: { name: string; as_of: string; imported_at: string; kind: string; state?: string; revision?: number } | null;
};
const departments: [Department, string][] = [['finance', '财务部看板'], ['presales', '产品事业部看板'], ['engineering', '工程部看板']];
const explanations: Record<Department, string> = {
  engineering: '交付中包含待开工、实施中、待验收和整改中；交付后包含已验收和质保中。逾期按台账截止日期判断。',
  finance: '金额与指标名称以来源台账和接口口径为准；期初应收、应收余额、月度实际与计划分别展示，不相加、不相减推算。空值不视为零。',
  presales: '按来源记录展示，保留不同工作表的同名项目。未填写的阶段、金额不补全，不按阶段推算成交概率。',
};
function validBoard(value: unknown): value is Board {
  if (!value || typeof value !== 'object') return false;
  const b = value as Board;
  return typeof b.available === 'boolean' && typeof b.snapshot_id === 'string' && typeof b.title === 'string' && typeof b.scope === 'string'
    && !!b.fields && typeof b.fields === 'object' && Object.values(b.fields).every(x => typeof x === 'string')
    && Array.isArray(b.statuses) && b.statuses.every(x => typeof x === 'string')
    && Number.isInteger(b.total) && b.total >= 0 && Number.isInteger(b.filtered_count) && b.filtered_count >= 0
    && Array.isArray(b.records) && b.records.every(x => x && typeof x === 'object' && Object.values(x).every(v => typeof v === 'string'))
    && Array.isArray(b.metrics) && b.metrics.every(x => x && typeof x.label === 'string' && typeof x.key === 'string' && typeof x.unit === 'string'
      && (x.value === null || typeof x.value === 'string' && /^\d+(\.\d{1,2})?$/.test(x.value) || typeof x.value === 'number' && Number.isFinite(x.value)))
    && Array.isArray(b.distribution) && b.distribution.every(x => x && typeof x.label === 'string' && Number.isInteger(x.count) && x.count >= 0)
    && (b.source === null || !!b.source && typeof b.source.name === 'string' && typeof b.source.as_of === 'string' && Number.isFinite(Date.parse(b.source.imported_at)));
}
function display(value: number | string | null, unit: string) {
  if (value === null) return '—';
  // Keep decimal money exact even when many large project amounts are aggregated.
  const [whole, fraction = ''] = String(value).split('.');
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return unit === '元' ? `${grouped}.${fraction.padEnd(2, '0')}` : grouped;
}
function SourceStatus({ board }: { board: Board }) {
  if (!board.source) return null;
  const source = board.source;
  return <p className="business-source">来源：{source.name} · 台账截止 {source.as_of || '未填写'} · {board.department === 'presales'
    ? `工作数据（${source.state === 'published' ? '已发布' : source.state === 'submitted' ? '待发布' : '草稿'}）· 版本 ${source.revision ?? '—'} · 更新于`
    : '已发布台账 · 发布于'} {new Date(source.imported_at).toLocaleString('zh-CN')}</p>;
}
function FinancialChart({ metrics }: { metrics: Board['metrics'] }) {
  const amounts = metrics.filter(item => item.unit === '元');
  const maximum = Math.max(1, ...amounts.map(item => Math.abs(Number(item.value))));
  return <figure className="business-money-chart" aria-label="财务金额对比">
    <figcaption>金额对比 <small>人民币元 · 各项独立，不叠加</small></figcaption>
    {amounts.length ? amounts.map(item => <div className="business-money-row" key={item.key}>
      <div><span>{item.label}</span><strong>{display(item.value, item.unit)} <small>元</small></strong></div>
      {item.value !== null && <svg viewBox="0 0 400 12" preserveAspectRatio="none" role="img" aria-label={`${item.label} ${display(item.value, item.unit)} 元`}>
        <rect width="400" height="12" rx="4" className="business-chart-track" />
        <rect width={Math.abs(Number(item.value)) / maximum * 400} height="12" rx="4" className="business-chart-fill" />
      </svg>}
    </div>) : <p>暂无金额指标，未提供的数据以“—”展示。</p>}
  </figure>;
}
export function BusinessOverview({ preview = false }: { preview?: boolean }) {
  const [boards, setBoards] = useState<Partial<Record<Department, Board>>>({});
  const [failures, setFailures] = useState<Partial<Record<Department, string>>>({});
  const [loading, setLoading] = useState(!preview);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    if (preview) { setBoards({}); setFailures({}); setLoading(false); return; }
    const controller = new AbortController();
    setBoards({}); setFailures({}); setLoading(true);
    void Promise.all(departments.map(async ([key]) => {
      try {
        const result = await apiRequest<unknown>(`/api/business/boards/${key}/`, { signal: controller.signal });
        if (!validBoard(result) || result.department !== key) throw new Error('看板数据格式无效，请稍后重试。');
        return { key, board: result };
      } catch (error) {
        return { key, error: isApiError(error) ? error.message : error instanceof Error ? error.message : '无法读取看板。' };
      }
    })).then(results => {
      if (controller.signal.aborted) return;
      const nextBoards: Partial<Record<Department, Board>> = {};
      const nextFailures: Partial<Record<Department, string>> = {};
      for (const result of results) {
        if (result.board) nextBoards[result.key] = result.board;
        if (result.error) nextFailures[result.key] = result.error;
      }
      setBoards(nextBoards); setFailures(nextFailures); setLoading(false);
    });
    return () => controller.abort();
  }, [preview, refresh]);

  useEffect(() => {
    if (preview) return;
    const interval = window.setInterval(() => {
      if (document.visibilityState === 'visible') setRefresh(value => value + 1);
    }, 15000);
    return () => window.clearInterval(interval);
  }, [preview]);

  return <section className="business-overview" aria-label="企业台账总览">
    <div className="business-overview-head"><div><p className="eyebrow">数据分析</p><h2>数据分析 / BI报表看板</h2><p>财务、工程读取最新已发布台账；产品事业部展示最新工作数据。仅展示接口提供的指标，不推算收入、利润或成交概率。</p></div>
      {!preview && <button type="button" className="button secondary" disabled={loading} onClick={() => setRefresh(value => value + 1)}>刷新数据</button>}</div>
    <div className="business-overview-grid">{departments.map(([key, label]) => {
      const board = boards[key];
      const distribution = board?.distribution ?? [];
      return <article className={`business-overview-card business-overview-${key}`} key={key} aria-label={label}>
        <div className="business-overview-card-head"><h3>{label}</h3><strong>{preview ? '预览' : loading ? '读取中' : failures[key] ? '读取失败' : !board?.available ? key === 'presales' ? '待录入' : '待发布' : key === 'presales' ? board.source?.state === 'published' ? '已发布工作数据' : '未发布工作数据' : '已发布'}</strong></div>
        {preview ? <p className="business-overview-message">预览不读取业务数据。</p> : loading ? <p className="business-overview-message" role="status">正在读取台账…</p>
          : failures[key] ? <p className="business-overview-message" role="alert">{failures[key]}</p>
            : board ? <>
              {board.available ? <>
                {key === 'finance' && <FinancialChart metrics={board.metrics} />}
                <div className="business-overview-metrics">{board.metrics.filter(item => key !== 'finance' || item.unit !== '元').map(item => <div key={item.key}><span>{item.label}</span><strong>{display(item.value, item.unit)}<small>{item.unit}</small></strong></div>)}</div>
                <div className="business-overview-distribution"><p>{key === 'finance' ? '财务条目分布' : key === 'presales' ? '产品条目分布' : '工程条目分布'}</p>{distribution.length ? distribution.map(item => <div className="business-overview-bar" key={item.label}><span>{item.label}</span><meter min={0} max={Math.max(1, board.total, item.count)} value={item.count} aria-label={`${item.label} ${item.count} 项`} /><strong>{item.count}</strong></div>) : <span>暂无分布数据</span>}</div>
                <SourceStatus board={board} /><p className="business-definition">{explanations[key]}</p><p className="business-scope">{board.scope.replace(/售前(?:部门)?/g, '产品事业部')}</p></>
                : <p className="business-overview-source">{key === 'presales' ? '暂无产品事业部跟进数据' : '暂无已发布台账'}，未提供的数据以“—”展示。</p>}
            </> : <p className="business-overview-message">暂无看板数据。</p>}
        <CenterLink href={`/centers/business/${key}`} className="business-overview-link">查看数据明细 <span aria-hidden="true">→</span></CenterLink>
      </article>;
    })}</div>
  </section>;
}
export default function BusinessBoards({ initial = 'engineering', preview = false }: { initial?: Department; preview?: boolean }) {
  return <BusinessProjects key={initial} department={initial} preview={preview} />;
}
