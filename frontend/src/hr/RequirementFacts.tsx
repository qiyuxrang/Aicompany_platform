export const requirementLabels: Record<string, string> = {
  position_name: '岗位名称', headcount: '招聘人数', responsibilities: '岗位职责', required_requirements: '必备要求',
  preferred_requirements: '加分要求', education_requirement: '学历要求', experience_requirement: '经验要求',
  skill_requirements: '技能要求', work_location: '工作地点', salary: '薪资', benefits: '福利', social_insurance: '社保', notes: '备注',
};
export default function RequirementFacts({ requirements }: { requirements?: Record<string, unknown> }) {
  if (!requirements) return null;
  return <details className="hr-requirement-facts" open><summary>本 JD 提取要求（确认后用于筛选的版本快照）</summary>
    <dl>{Object.entries(requirements).map(([key, value]) => <div key={key}><dt>{requirementLabels[key] || key}</dt><dd>{(Array.isArray(value) ? value.join('、') : String(value ?? '')) || '待补充'}</dd></div>)}</dl>
    <p className="hr-muted">请核对提取要求。修改 JD 正文并保存新版本会重新提取；年龄只作备注，不参与筛选。</p>
  </details>;
}
