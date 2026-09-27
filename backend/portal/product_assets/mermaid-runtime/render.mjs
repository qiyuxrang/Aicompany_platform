import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { createRequire } from "node:module";
import { chromium } from "playwright-core";

const require = createRequire(import.meta.url);
const mermaidBundle = require.resolve("mermaid/dist/mermaid.min.js");
const nodes = [
  ["业务输入","资料解析","规则校验","智能生成","人工审核","正式交付"],
  ["访问入口","业务应用","智能体能力","数据服务","基础设施","运营治理"],
  ["部门用户","统一门户","任务中心","模型服务","知识服务","审计中心"],
  ["Web前端","API服务","任务队列","模型网关","关系数据库","对象存储"],
  ["用户访问区","边界接入区","应用服务区","数据服务区","运维管理区","备份恢复区"],
  ["终端用户","访问网关","负载均衡","应用集群","数据集群","外部服务"],
  ["原始资料","内容解析","结构化数据","文档生成","审核记录","成果归档"],
  ["企业平台","统一身份","知识库接口","模型接口","业务系统","消息服务"],
  ["身份安全","网络安全","应用安全","数据安全","审计追踪","安全运营"],
  ["账号登录","首次改密","角色授权","资源校验","操作执行","审计留痕"],
  ["业务日志","操作日志","模型日志","安全事件","集中分析","审计报告"],
  ["流量入口","健康检查","应用副本","任务执行器","主备数据","故障切换"],
  ["生产数据","定时备份","完整性校验","异地保留","恢复演练","复盘优化"],
  ["指标采集","日志汇聚","规则判断","分级告警","响应处置","闭环复盘"],
  ["需求变更","开发验证","发布审批","灰度上线","运行观察","回滚恢复"],
  ["业务可行","技术可行","经济可行","实施可行","合规可行","综合结论"],
  ["本期建设","现有能力","外部依赖","后续演进","排除事项","边界确认"],
  ["决策层","业务部门","建设团队","运维团队","合作供应方","治理办公室"],
  ["立项门禁","方案门禁","试点门禁","上线门禁","验收门禁","运营复盘"],
  ["能力建设","用户采用","流程改善","质量提升","效益度量","持续优化"],
];

function source(index) {
  const lines = [`flowchart ${index % 3 === 2 ? "TB" : "LR"}`];
  nodes[index % nodes.length].forEach((label, i) => lines.push(`N${i}["${label}"]`));
  const patterns = [
    ["N0 --> N1","N1 --> N2","N2 --> N3","N3 --> N4","N4 --> N5","N5 --> N1"],
    ["N0 --> N1","N0 --> N2","N1 --> N3","N2 --> N3","N3 --> N4","N4 --> N5"],
    ["N0 --> N2","N1 --> N2","N2 --> N3","N2 --> N4","N3 --> N5","N4 --> N5"],
    ["N0 --> N1","N1 --> N2","N2 --> N3","N3 --> N4","N4 --> N5"],
  ];
  lines.push(...patterns[index % patterns.length]);
  lines.push(
    "classDef c0 fill:#E7F0FF,stroke:#246BCE,color:#000000,stroke-width:2px",
    "classDef c1 fill:#E5FAF5,stroke:#12856E,color:#000000,stroke-width:2px",
    "classDef c2 fill:#FFF2D8,stroke:#E28A16,color:#000000,stroke-width:2px",
    "classDef c3 fill:#F1EAFE,stroke:#7652C8,color:#000000,stroke-width:2px",
    "classDef c4 fill:#FFE9EE,stroke:#D84A6B,color:#000000,stroke-width:2px",
    "class N0,N5 c0","class N1 c1","class N2 c2","class N3 c3","class N4 c4"
  );
  return lines.join("\n");
}

async function main() {
  if (process.argv.length !== 3) throw new Error("usage: render.mjs content.json");
  const contentPath = path.resolve(process.argv[2]);
  const root = path.dirname(contentPath);
  const content = JSON.parse(await fs.readFile(contentPath, "utf8"));
  const figures = content.blocks.filter((block) => block.type === "figure");
  const browser = await chromium.launch({headless:true, executablePath:process.env.PORTAL_CHROMIUM_PATH || undefined,
    args:["--no-sandbox","--disable-dev-shm-usage"]});
  const page = await browser.newPage({viewport:{width:1600,height:900},deviceScaleFactor:1.5});
  await page.setContent('<style>html,body{margin:0;background:#F8FBFF}#canvas{box-sizing:border-box;width:1600px;height:900px;padding:34px 50px 40px;overflow:hidden}#canvas .mermaid{display:flex;align-items:center;justify-content:center;width:1500px;height:760px}#canvas svg{width:1460px!important;height:700px!important;max-width:none!important}</style><main id="canvas"></main>', {waitUntil:"domcontentloaded"});
  await page.addScriptTag({path:mermaidBundle});
  let rendered = 0;
  try {
    for (let index=0; index<figures.length; index+=1) {
      const figure=figures[index];
      const destination=path.resolve(root,figure.path);
      if (!destination.startsWith(root+path.sep) || path.extname(destination).toLowerCase()!==".png") throw new Error("unsafe diagram path");
      await fs.mkdir(path.dirname(destination),{recursive:true});
      const diagram=source(index);
      await page.evaluate(async ({diagram,title})=>{
        const host=document.querySelector("#canvas"); host.replaceChildren();
        const heading=document.createElement("h1"); heading.textContent=title;
        heading.style.cssText="font:700 28px 'Microsoft YaHei','Noto Sans CJK SC',sans-serif;color:#000000;text-align:center;margin:8px 0 18px";
        const graph=document.createElement("div"); graph.className="mermaid"; graph.textContent=diagram; host.append(heading,graph);
        window.mermaid.initialize({startOnLoad:false,securityLevel:"strict",theme:"base",fontFamily:"Microsoft YaHei, Noto Sans CJK SC, sans-serif",flowchart:{htmlLabels:false,curve:"basis",nodeSpacing:42,rankSpacing:58}});
        await window.mermaid.run({nodes:[graph]});
        const svg=graph.querySelector("svg"); svg.style.cssText+="background:#F8FBFF;border:1px solid #D7E3F2;border-radius:14px;padding:22px;box-sizing:border-box";
      },{diagram,title:figure.caption});
      await page.locator("#canvas").screenshot({path:destination,type:"png"}); rendered+=1;
    }
  } finally { await browser.close(); }
  process.stdout.write(JSON.stringify({rendered,sourcePersisted:false,engine:"mermaid-js-12.0.0"}));
}
main().catch((error)=>{process.stderr.write(String(error.stack||error));process.exit(2);});
