import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import ProjectProgress from './ProjectProgress';

afterEach(cleanup);
it.each([
  ['finance', '在走质保金退款手续（5月质保到期）', '质保尾款'],
  ['finance', '剩余5%质保金,2028年3月底到期', '质保尾款'],
  ['finance', '还未进行竣工验收（2025年8月到期）', '等待验收'],
  ['presales', '需求调研阶段', '需求调研'],
  ['presales', '已签约', '已签约'],
  ['engineering', '施工中', '施工中'],
] as const)('shows supported %s stage without inventing a percentage', (department, status, stage) => {
  const { container } = render(<ProjectProgress department={department} status={status} />);
  expect(screen.getByRole('list', { name: `项目阶段：${stage}` })).toBeTruthy();
  expect(container.querySelectorAll('[aria-current="step"]')).toHaveLength(1);
  expect(screen.getByText(status, { selector: 'p' })).toBeTruthy();
  expect(screen.queryByRole('progressbar')).toBeNull();
});
it.each(['', '多来源状态', '计划完成合同签订', '未签约', '未验收', '已签约但合同已取消', '合同金额为折算的2公里对应的金额'])('keeps unsupported product status %s unconfirmed', status => {
  const { container } = render(<ProjectProgress department="presales" status={status} />);
  expect(container.querySelector('[aria-current="step"]')).toBeNull();
  expect(screen.getByRole('list', { name: /阶段待确认|多来源/ })).toBeTruthy();
});
