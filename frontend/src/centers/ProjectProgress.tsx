const stages = {
  finance: ['等待验收', '回款跟进', '质保尾款', '已结清'],
  presales: ['需求调研', '方案推进', '商务招采', '已签约'],
  engineering: ['待开工', '施工中', '待验收', '已验收'],
};
const patterns = {
  finance: [/^(还未|尚未|未|等待|待).{0,4}验收/, /^(正在|目前|当前)?(办理付款|办理回款|跟进回款|催收|回款跟进|剩余款项资料已提交)/, /^(在走质保金退款手续|剩余\s*\d+[%％]质保金|质保金待退|质保期内)/, /^(已结清|全部款项已收回|回款已完成)[。！!\s]*$/],
  presales: [/^(需求调研阶段|正在调研|需求调研中)[。！!\s]*$/, /^(方案编制中|方案已完成|方案交流中|方案推进中)[。！!\s]*$/, /^(招标中|投标中|商务洽谈中|等待招标)[。！!\s]*$/, /^(已签约|已签订合同|合同已签订)[。！!\s]*$/],
  engineering: [/^(待开工|尚未开工|未开工)[。！!\s]*$/, /^(施工中|正在施工|实施中)[。！!\s]*$/, /^(待验收|等待验收|尚未验收|未验收)[。！!\s]*$/, /^(已验收|验收通过|已竣工验收)[。！!\s]*$/],
};

export default function ProjectProgress({ department, status }: { department: keyof typeof stages; status: string }) {
  const current = patterns[department].findIndex(pattern => pattern.test(status.trim()));
  const label = current < 0 ? status === '多来源状态' ? '多来源，查看详情' : '阶段待确认' : stages[department][current];
  return <div className="project-progress">
    <strong className="project-progress-label">{label}</strong>
    <ol aria-label={`项目阶段：${label}`} className="project-progress-track">
      {stages[department].map((stage, index) => <li key={stage} aria-current={index === current ? 'step' : undefined}><span aria-hidden="true" />{stage}</li>)}
    </ol>
    <p className="business-project-status">{status || '原表未填写当前状态，请负责人补充。'}</p>
  </div>;
}
