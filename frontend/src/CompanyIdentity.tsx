import { CompanyMark } from "./product/workbench-shared";

export const COMPANY_NAME = "陕西一二三数字信息技术有限公司";
export const COMPANY_ENGLISH_NAME = "SHAANXI 123 DIGITAL INFORMATION TECHNOLOGY";

export default function CompanyIdentity() {
  return <><CompanyMark/><span className="company-identity-text"><strong>{COMPANY_NAME}</strong><small>{COMPANY_ENGLISH_NAME}</small></span></>;
}
