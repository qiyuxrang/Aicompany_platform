export const requirementLabels: Record<string, string> = {
  position_name: '岗位名称', headcount: '招聘人数', responsibilities: '岗位职责', required_requirements: '必备要求',
  preferred_requirements: '加分要求', education_requirement: '学历要求', experience_requirement: '经验要求',
  skill_requirements: '技能要求', work_location: '工作地点', salary: '薪资', benefits: '福利', social_insurance: '社保', notes: '备注',
};
export default function RequirementFacts({ requirements, reply = false }: { requirements?: Record<string, unknown>; reply?: boolean }) {
  if (!requirements && !reply) return null;
  const facts = <><dl>{Object.entries(reply ? requirementLabels : requirements || {}).map(([key, label]) => {
    const value = requirements?.[key];
    return <div key={key}><dt>{reply ? String(label) : requirementLabels[key] || key}</dt><dd>{(Array.isArray(value) ? value.join('、') : String(value ?? '')) || '待补充'}</dd></div>;
  })}</dl><p className="hr-muted">请核对提取要求。修改 JD 正文并保存新版本会重新提取；年龄只作备注，不参与筛选。</p></>;
  return reply ? <div className="hr-requirement-facts hr-reply-facts"><h3>结构化招聘信息</h3>{facts}</div>
    : <details className="hr-requirement-facts" open><summary>本 JD 事实快照（确认后用于筛选）</summary>{facts}</details>;
}
