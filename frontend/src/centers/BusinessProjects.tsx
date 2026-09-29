import { useEffect, useRef, useState } from 'react';
import { apiRequest, isApiError } from '../api';
import { CenterLink } from './shared';
import ProjectProgress from './ProjectProgress';
import './business-projects.css';

export type BusinessDepartment = 'engineering' | 'finance' | 'presales';
type Person = { id: number | string; name: string };
type Project = {
  id: string; name: string; record_count: number; status: string; owner: string;
  updated_at: string | null; updated_by: Person | null; update_kind: string;
};
type Source = { name: string; as_of: string; imported_at: string; state?: string; revision?: number } | null;
type ProjectList = {
  department: BusinessDepartment; title: string; available: boolean; source: Source;
  projects: Project[]; total: number; record_total: number; scope: string;
};
type ProjectDetail = {
  department: BusinessDepartment; project: Project; fields: Record<string, string>;
  records: Record<string, string>[]; source: Source; scope: string;
  updates: { id: string; at: string; actor: Person | null; action: string; state: string; summary: string }[];
};
const names = { engineering: '工程部', finance: '财务部', presales: '产品事业部' };
const dateText = (value: string | null) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '未记录';
const personLabel = (kind: string) => kind === 'source_import' || kind === 'import' ? '导入人' : '更新人';
const stateLabels: Record<string, string> = { published: '已发布', submitted: '待发布', draft: '草稿' };
function validProject(value: unknown): value is Project {
  if (!value || typeof value !== 'object') return false;
  const project = value as Project;
  return typeof project.id === 'string' && !!project.id && typeof project.name === 'string'
    && typeof project.status === 'string' && typeof project.owner === 'string'
    && Number.isInteger(project.record_count) && project.record_count > 0
    && typeof project.update_kind === 'string'
    && (project.updated_at === null || typeof project.updated_at === 'string' && Number.isFinite(Date.parse(project.updated_at)))
    && (project.updated_by === null || !!project.updated_by && typeof project.updated_by.name === 'string');
}
function validList(value: unknown, department: BusinessDepartment): value is ProjectList {
  if (!value || typeof value !== 'object') return false;
  const list = value as ProjectList;
  return list.department === department && typeof list.available === 'boolean'
    && Array.isArray(list.projects) && list.projects.every(validProject)
    && Number.isInteger(list.total) && list.total >= 0 && Number.isInteger(list.record_total) && list.record_total >= 0;
}
function validDetail(value: unknown, department: BusinessDepartment, id: string): value is ProjectDetail {
  if (!value || typeof value !== 'object') return false;
  const detail = value as ProjectDetail;
  return detail.department === department && validProject(detail.project) && detail.project.id === id
    && !!detail.fields && typeof detail.fields === 'object' && Object.values(detail.fields).every(label => typeof label === 'string')
    && Array.isArray(detail.records) && detail.records.every(row => row && typeof row === 'object' && Object.values(row).every(field => typeof field === 'string'))
    && Array.isArray(detail.updates) && detail.updates.every(update => update && typeof update.id === 'string'
      && typeof update.summary === 'string' && typeof update.action === 'string' && typeof update.state === 'string'
      && typeof update.at === 'string' && Number.isFinite(Date.parse(update.at))
      && (update.actor === null || !!update.actor && typeof update.actor.name === 'string'));
}

export default function BusinessProjects({ department, preview = false }: { department: BusinessDepartment; preview?: boolean }) {
  const selectedId = () => new URLSearchParams(window.location.search).get('project') || '';
  const [projectId, setProjectId] = useState(selectedId);
  const [list, setList] = useState<ProjectList | null>(null);
  const [detail, setDetail] = useState<ProjectDetail | null>(null);
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(!preview);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const heading = useRef<HTMLHeadingElement>(null);
  const base = `${preview ? '/preview' : '/centers'}/business/${department}`;

  useEffect(() => {
    const changed = () => setProjectId(selectedId());
    window.addEventListener('popstate', changed);
    return () => window.removeEventListener('popstate', changed);
  }, []);
  useEffect(() => {
    setError('');
    setList(current => !preview && !projectId && current?.department === department ? current : null);
    setDetail(current => !preview && current?.department === department && current.project.id === projectId ? current : null);
    if (preview) { setLoading(false); return; }
    const controller = new AbortController();
    setLoading(true);
    const suffix = projectId ? `${encodeURIComponent(projectId)}/` : '';
    void apiRequest<unknown>(`/api/business/boards/${department}/projects/${suffix}`, { signal: controller.signal })
      .then(result => {
        if (controller.signal.aborted) return;
        if (projectId) {
          if (!validDetail(result, department, projectId)) throw new Error('项目详情格式无效，请刷新重试。');
          setDetail(result);
        } else {
          if (!validList(result, department)) throw new Error('项目列表格式无效，请刷新重试。');
          setList(result);
        }
      }).catch(caught => {
        if (!controller.signal.aborted) {
          setList(null); setDetail(null);
          setError(isApiError(caught) ? caught.message : caught instanceof Error ? caught.message : '项目读取失败，请重试。');
        }
      }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [department, preview, projectId, refresh]);
  useEffect(() => {
    heading.current?.focus({ preventScroll: true });
  }, [projectId, loading]);
  useEffect(() => {
    if (preview) return;
    const onFocus = () => { if (!document.hidden) setRefresh(value => value + 1); };
    window.addEventListener('focus', onFocus);
    return () => window.removeEventListener('focus', onFocus);
  }, [preview]);

  const query = search.trim().toLocaleLowerCase();
  const projects = list?.projects.filter(project => !query || [project.name, project.status, project.owner, project.updated_by?.name || ''].some(value => value.toLocaleLowerCase().includes(query))) ?? [];
  return <section className="business-projects" aria-label={`${names[department]}项目工作区`}>
    {projectId && <CenterLink href={base} className="business-project-back">← 返回项目列表</CenterLink>}
    <header className="business-project-header">
      <div><h1 ref={heading} tabIndex={-1}>{projectId ? detail?.project.name || '项目详情' : `${names[department]}项目`}</h1>
        {!projectId && <p>{list ? `${list.total} 个项目` : '项目列表'}<span>直接查看当前状态，点击项目查看进展详情</span></p>}</div>
      {!preview && <button type="button" className="button secondary" disabled={loading} onClick={() => setRefresh(value => value + 1)}>刷新项目</button>}
    </header>
    {preview ? <p className="center-empty">预览不读取项目数据，请使用已授权的总经理账号查看。</p> : <>
      {error && <p className="notice error" role="alert">{error}</p>}
      {loading && !list && !detail && <p className="business-project-loading" role="status">正在读取{projectId ? '项目详情' : '项目列表'}…</p>}
      {list && <>
        <div className="business-project-toolbar"><label>搜索项目<input type="search" value={search} onChange={event => setSearch(event.target.value)} maxLength={100} placeholder="项目名称、状态或人员" /></label>
          {list.source && <span>{stateLabels[list.source.state || 'published'] || '工作数据'} · {department === 'presales' ? '最新工作版本' : '最新发布版本'}</span>}</div>
        {!list.projects.length ? <div className="center-empty"><h2>暂无{names[department]}项目</h2><p>{department === 'engineering' ? '工程数据暂未提供，保持空白。' : '当前没有可查看的项目数据。'}</p></div>
          : <div className="business-project-table-wrap"><table className="business-project-table" aria-label={`${names[department]}项目列表`}>
            <thead><tr><th>项目名称</th><th>当前状态 / 项目进度</th><th>数据记录时间</th><th>记录操作人</th></tr></thead>
            <tbody>{projects.map(project => <tr key={project.id}>
              <td><CenterLink href={`${base}?project=${encodeURIComponent(project.id)}`} className="business-project-name">{project.name}<span aria-hidden="true">↗</span></CenterLink>{project.record_count > 1 && <small>{project.record_count} 条来源记录</small>}</td>
              <td data-label="当前状态 / 项目进度"><ProjectProgress department={department} status={project.status} /></td>
              <td data-label="数据记录时间"><time dateTime={project.updated_at || undefined}>{dateText(project.updated_at)}</time><small>非业务发生时间</small></td>
              <td data-label="记录操作人">{project.updated_by?.name || '未记录'}{project.updated_by && <small>{personLabel(project.update_kind)}</small>}</td>
            </tr>)}</tbody>
          </table>{!projects.length && <p className="center-empty">没有符合搜索条件的项目。</p>}</div>}
        {!!list.projects.length && <p className="business-project-caption">显示 {projects.length} / {list.total} 个项目 · 阶段图按状态原文提示，不代表完成百分比；阶段不明确的保持待确认。同名项目归在一起，来源记录未合并或删除。</p>}
      </>}
      {detail && <ProjectContent detail={detail} />}
    </>}
  </section>;
}

function ProjectContent({ detail }: { detail: ProjectDetail }) {
  const project = detail.project;
  const imported = ['source_import', 'import'].includes(project.update_kind);
  const [recordId, setRecordId] = useState(detail.records[0]?.project_id || '');
  const record = detail.records.find(item => item.project_id === recordId) || detail.records[0];
  return <div className="business-project-detail">
    <section className="business-project-current"><h2>当前状态与项目进度</h2>
      <ProjectProgress department={detail.department} status={record?.status || record?.project_progress || record?.current_status || project.status} />
      <p className="business-project-caption">按当前来源记录的状态原文提示阶段，不代表完成百分比。多个来源的进展请在下方切换查看。</p>
    </section>
    <dl className="business-project-summary">
      <div><dt>当前进展来源</dt><dd>{record?.source_sheet || '项目记录'}</dd></div>
      <div><dt>{personLabel(project.update_kind)}</dt><dd>{project.updated_by?.name || '未记录'}</dd></div>
      <div><dt>最近平台{imported ? '导入' : '更新'}</dt><dd>{dateText(project.updated_at)}</dd></div>
      <div><dt>原表负责人／跟进人员</dt><dd>{record?.owner || '未记录'}</dd></div>
    </dl>
    <p className="business-project-caption">{imported ? '此条记录来自原表导入，导入人不等同于原始业务更新人。' : '更新人按本项目实际变更记录显示，不使用其他项目的更新人。'}原表历史未注明更新人的，不作推断。</p>
    <section className="business-project-original"><div className="business-project-original-head"><h2>项目资料</h2>
      {detail.records.length > 1 && <label>来源记录<select value={record?.project_id || ''} onChange={event => setRecordId(event.target.value)}>{detail.records.map((item, index) => <option key={item.project_id} value={item.project_id}>{item.source_sheet || `记录 ${index + 1}`}{item.source_row ? ` · 第 ${item.source_row} 行` : ''}{item.source_group ? ` · ${item.source_group}` : ''}</option>)}</select></label>}</div>
      {record && <dl className="business-project-fields">{Object.entries(detail.fields).filter(([key]) => !['project_name', 'follow_up_history', 'project_id'].includes(key) && String(record[key] ?? '').trim() !== '').map(([key, label]) => <div key={key} className={['description', 'project_progress', 'current_status', 'annual_plan', 'notes'].includes(key) ? 'business-project-wide' : undefined}><dt>{label}</dt><dd>{record[key]}</dd></div>)}</dl>}
      {record?.follow_up_history && <details className="business-project-source-history"><summary>原表跟进历史（更新人以原文为准）</summary><pre>{record.follow_up_history}</pre></details>}
      {detail.source && <p className="business-project-caption">来源：{detail.source.name} · 版本 {detail.source.revision ?? '—'} · {stateLabels[detail.source.state || 'published'] || '工作数据'}</p>}
    </section>
  </div>;
}
