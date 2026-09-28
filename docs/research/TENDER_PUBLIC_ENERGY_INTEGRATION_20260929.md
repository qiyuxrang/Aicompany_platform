# 榆林周边能源企业公开来源接入验证

验证日期：2026-09-29，北京时间。本记录描述代码与真实出站验证；不代表已完成全部历史覆盖。

| 来源 | code | 公共查询方式 | 已验证分页 |
|---|---|---|---|
| 榆能集团电子采购交易平台 | yuneng | 官方页面声明的只读 JSON POST | 20 条/页，第 1、2 页无重复 |
| 中煤招标与采购网 | zmzb | HTTPS 静态 HTML | 工程、货物、服务各 10 条/页，第 1、2 页无重复 |
| 国家能源集团招标网 | chnenergy | HTTPS 静态 HTML | 15 条/页，第 1、2 页无重复 |

## 榆能：本地优先

- 首页：<https://dzsw.sxylny.com/>。
- 公开前端文件 `js/0.b8917caf29760437f995.js` 中 `GCH0` 声明查询端点；`pXoC`、`NoticeShow` 页面给出请求参数。
- 列表：`POST /mall/announce/search`，参数 `annoType=1,page,pageSize`。
- 正文：`POST /mall/announce/detail`，参数 `id,annoType=1`。
- 未登录、未带 Cookie、未签名的普通请求取得公开公告，使用默认 TLS 校验；未绕过验证码或访问控制。
- 原始 JSON 留作快照，正文位于 `content.content`。提取、分类须通过限定 `yuneng` 来源的 `notice_content` 解包，不能把合成 HTML 当成原始响应。
- 官方详情使用 `/#/home/NoticeShow?id=<数字>&annoType=1`。只有该路由、这两个参数及采购公告类型纳入白名单。
- 可验证实例：[榆树湾煤矿主运系统煤流智能调速改造，2026-09-21](https://dzsw.sxylny.com/#/home/NoticeShow?id=52241&annoType=1)、[银河煤业井下排水配电柜智能改造，2026-09-15](https://dzsw.sxylny.com/#/home/NoticeShow?id=51643&annoType=1)、[银河煤业洗选车间配电柜智能改造，2026-09-18](https://dzsw.sxylny.com/#/home/NoticeShow?id=51939&annoType=1)。
- 前两条明确在交货/服务地点说明榆林市；不会因为采购企业位于榆林而将其全部外地项目归为榆林。
- 列表日期并非严格倒序，不按首个旧日期终止整个来源扫描。

## 中煤与国能

- 中煤入口：<https://www.zmzb.com/cms/index.htm>。仅 `ywgg1hw/ywgg1gc/ywgg1fw` 招标公告；官方 `page.js` 用 `pageNo` 查询参数翻页。
- 实例：[中国中煤档案馆软硬件系统集成，2026-09-24](https://www.zmzb.com/cms/channel/ywgg1hw/61786.htm)。正文交货地点明确北京市，不归为榆林。
- 国能入口：<https://www.chnenergybidding.com.cn/bidweb/001/001002/moreinfo.html>。列表直接提供 `/2.html` 等分页链接；仅 `001002001/002/003` 采购频道。
- 实例：[榆林化工聚丙烯装置 EPC，2026-09-28](https://www.chnenergybidding.com.cn/bidweb/001/001002/001002002/20260928/222d9c1c-2c7c-4b52-86c6-3a3fd4397be5.html)、[吉林热电设备智能预测性维护，2026-09-28](https://www.chnenergybidding.com.cn/bidweb/001/001002/001002003/20260928/c223624c-4cf7-4499-8f0d-23bd3cf8a7f0.html)。采集公告不意味着所有 EPC 都被认定为数字化项目，相关性由真实采购范围决定。

## 当前限制

- 榆林公共资源交易入口 `https://yl.sxggzyjy.cn/` 直连仍出现证书链验证失败；未降级 TLS、未创建可用来源假象。
- 每来源受页数、候选条数、请求间隔、超时和体积上限约束。30 天是目标日期范围；达到扫描上限仍须显示部分覆盖。
- 测试证据保存在 `backend/portal/tests/fixtures/{yuneng,public_energy}_20260929/`，包含官方响应原文或节选及完整响应摘要。
