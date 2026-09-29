import http from "node:http";

const HOST = "127.0.0.1";
const PORT = 5180;
const VITE_PORT = 5178;
const ORIGIN = `http://${HOST}:${PORT}`;
const PREFIX = "[ui_redesign_fixture]";

const user = {
  id: 9901,
  username: "ui-redesign-fixture",
  display_name: "产品设计验证用户",
  roles: [{ code: "product", name: "产品人员" }],
  must_change_password: false,
  is_platform_admin: false,
};

const productModule = {
  code: "product",
  name: "产品方案中心",
  description: "隔离的前端重设计验证入口。",
  status: "verified",
  enabled: true,
};

const projects = [
  {
    id: "ui-redesign-blueprint-review",
    title: "隔离验证 · 蓝图待确认",
    state: "WAITING_REVIEW",
    stage: "BLUEPRINT",
    version: 3,
    created_at: "2026-09-29T08:00:00Z",
    updated_at: "2026-09-29T09:30:00Z",
    owner_name: "产品设计验证用户",
    reviewer_name: "合成审核人员",
    pending_action: "blueprint",
    error_code: "",
  },
  {
    id: "ui-redesign-processing",
    title: "隔离验证 · 后台处理中",
    state: "RUNNING",
    stage: "WRITING",
    version: 7,
    created_at: "2026-09-28T08:00:00Z",
    updated_at: "2026-09-29T09:10:00Z",
    owner_name: "产品设计验证用户",
    reviewer_name: "合成审核人员",
    pending_action: "generate_outputs",
    error_code: "",
  },
];

const blueprints = {
  "ui-redesign-blueprint-review": {
    id: "ui-redesign-blueprint-v1",
    version: 1,
    sha256: "a".repeat(64),
    payload: {
      purpose: "验证产品方案中心的新工作台布局",
      audience: "项目评审人员",
      chapters: [{ id: "scope", title: "验证范围", scope: "概览、项目清单与知识库入口", source_ids: ["ui-redesign-source"] }],
      conditions: [{ text: "仅使用隔离 fixture 数据", type: "human" }],
      missing: [],
      conflicts: [],
      template_version: "ui-redesign-fixture-v1",
    },
  },
  "ui-redesign-processing": {
    id: "ui-redesign-processing-blueprint-v1",
    version: 1,
    sha256: "b".repeat(64),
    payload: {
      purpose: "验证处理中项目的状态呈现",
      audience: "交付项目人员",
      chapters: [{ id: "progress", title: "处理进度", scope: "展示后台编制中的状态", source_ids: ["ui-redesign-processing-source"] }],
      conditions: [{ text: "不生成真实文档", type: "human" }],
      missing: [],
      conflicts: [],
      template_version: "ui-redesign-fixture-v1",
    },
  },
};

function taskFor(id) {
  const summary = projects.find((project) => project.id === id);
  if (!summary) return null;
  const processing = id === "ui-redesign-processing";
  const source = {
    id: processing ? "ui-redesign-processing-source" : "ui-redesign-source",
    original_name: processing ? "后台处理验证资料.txt" : "蓝图验证资料.txt",
    purpose: "background",
    size: 4096,
    media_type: "text/plain",
    sha256: processing ? "d".repeat(64) : "c".repeat(64),
    created_at: summary.created_at,
    warnings: [],
    parsed: {
      status: "completed",
      method: "native",
      extraction_hash: processing ? "e".repeat(64) : "f".repeat(64),
      parser_version: "ui-redesign-fixture-v1",
      block_count: 1,
      item_count: 1,
      character_count: 80,
      truncated: false,
      metadata: { pages: 1 },
    },
  };
  const knowledge = {
    mode: "source_only_preview",
    required: false,
    status: "source_only_preview",
    ragflow_used: false,
    source_count: 0,
    detail: "隔离 fixture 仅展示项目资料状态，不连接真实知识库。",
  };
  return {
    ...summary,
    input_version: 1,
    blueprint_version: 1,
    blueprint_approved: processing,
    input: {
      project: summary.title,
      requirements: processing ? "验证后台处理中状态" : "验证蓝图人工确认状态",
      background: "仅用于前端重设计验证的合成背景。",
      conditions: ["不写入真实后端"],
      items: [{ row_id: "1", name: "验证设备", quantity: "1", unit: "套" }],
    },
    blueprint: blueprints[id],
    chapters: [],
    reports: [],
    artifacts: [],
    sources: [source],
    approvals: [],
    issues: [],
    input_issues: [],
    impact: {},
    error_code: "",
    actions: processing ? ["cancel", "retry"] : ["edit", "save_blueprint", "add_source", "confirm_blueprint", "cancel"],
    blockers: {},
    reviewer_id: 9902,
    owner_id: 9901,
    blueprint_review: processing
      ? { revision_count: 0, revision_limit: 3, revisions_remaining: 3 }
      : { revision_count: 1, revision_limit: 3, revisions_remaining: 2 },
    blueprint_knowledge: knowledge,
    knowledge,
    checkpoint: {
      analysis_progress: processing
        ? {
            documents: { status: "completed", source_count: 1 },
            equipment: { status: "completed", item_count: 1 },
            knowledge: { status: "waiting", detail: "隔离 fixture 不连接 RAGFlow" },
            blueprint: { status: "completed", detail: "项目蓝图已准备" },
            review: { status: "completed", detail: "蓝图已确认" },
            outputs: { status: "running", detail: "隔离 fixture 展示后台处理中" },
          }
        : {
            documents: { status: "completed", source_count: 1 },
            equipment: { status: "completed", item_count: 1 },
            knowledge: { status: "waiting", detail: "隔离 fixture 不连接 RAGFlow" },
            blueprint: { status: "completed", detail: "项目蓝图 v1 已生成" },
            review: { status: "waiting", detail: "等待人工确认蓝图" },
          },
      current_node: processing ? "outputs" : "review",
      detail: "fixture-only",
    },
    workflow: { current_node: processing ? "outputs" : "review", status: processing ? "running" : "waiting", detail: "fixture-only" },
  };
}

const knowledgeDatasets = [
  { id: "fixture-engineering", name: "05_产品事业部_P5工程知识库", document_count: 2 },
  { id: "fixture-delivery", name: "02_产品事业部_项目案例与交付库", document_count: 1 },
];
const knowledgeSessions = [
  { id: "11111111-1111-4111-8111-111111111111", title: "设备资料核验", version: 1 },
  { id: "22222222-2222-4222-8222-222222222222", title: "方案范围确认", version: 1 },
];
const knowledgeSource = {
  id: "S1",
  dataset_id: "fixture-engineering",
  document_id: "fixture-engineering-doc-1",
  title: "工程建设规范.pdf",
  content: "隔离 fixture 示例原文：配电系统应按项目确认的设备清单和验收条件执行。",
};
const knowledgeDetail = {
  ...knowledgeSessions[0],
  turns: [{
    question: "设备资料里有哪些验收条件？",
    answer: "隔离 fixture 示例回答：请按项目确认的设备清单和验收条件核对。 [S1]",
    sources: [knowledgeSource],
    outcome: "answered",
    request_id: "ui-redesign-history-1",
  }],
};

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function jsonResponse(request, response, status, payload) {
  const body = Buffer.from(JSON.stringify(payload));
  console.log(`${PREFIX} API ${request.method} ${request.url} -> ${status}`);
  response.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": body.length,
    "Cache-Control": "no-store",
    "X-UI-Redesign-Fixture": "true",
  });
  response.end(request.method === "HEAD" ? undefined : body);
}

function notFound(request, response) {
  jsonResponse(request, response, 404, {
    detail: `UI redesign fixture has no route for ${request.method} ${request.url}; the real backend was not contacted.`,
    code: "ui_redesign_fixture_route_not_found",
    fixture_only: true,
  });
}

function taskSummary(task) {
  return {
    id: task.id,
    title: task.title,
    state: task.state,
    stage: task.stage,
    version: task.version,
    created_at: task.created_at,
    updated_at: task.updated_at,
    owner_name: task.owner_name,
    reviewer_name: task.reviewer_name,
    pending_action: task.pending_action,
    error_code: task.error_code,
  };
}

function overview(url) {
  const filter = url.searchParams.get("filter") || "all";
  const query = (url.searchParams.get("q") || "").trim().toLocaleLowerCase();
  const page = Math.max(1, Number.parseInt(url.searchParams.get("page") || "1", 10) || 1);
  const pageSize = Math.max(1, Number.parseInt(url.searchParams.get("page_size") || "12", 10) || 12);
  const allTasks = projects.map((project) => taskSummary(project));
  const filtered = allTasks.filter((task) => {
    if (query && !task.title.toLocaleLowerCase().includes(query)) return false;
    if (filter === "review") return task.state === "WAITING_REVIEW";
    if (filter === "generation") return ["QUEUED", "RUNNING"].includes(task.state);
    if (filter === "completed" || filter === "completed_month") return task.state === "COMPLETED";
    if (filter === "attention") return ["WAITING_REVIEW", "WAITING_INPUT", "FAILED"].includes(task.state);
    return true;
  });
  const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const currentPage = Math.min(page, pages);
  const start = (currentPage - 1) * pageSize;
  const pageProjects = filtered.slice(start, start + pageSize);
  return {
    as_of: "2026-09-29T09:30:00Z",
    scope: "authorized_projects",
    metrics: { active: 2, review: 1, generation: 1, completed_month: 0 },
    projects: pageProjects,
    pagination: { page: currentPage, page_size: pageSize, total: filtered.length, pages },
    recent_projects: allTasks,
    todos: allTasks.filter((task) => task.state === "WAITING_REVIEW"),
    recent_outputs: [],
    reviewers: [{ id: 9902, name: "合成审核人员" }],
    capabilities: {
      model_generation: false,
      retrieval: false,
      formal_release: false,
      office_preview: false,
      upload_extensions: [".csv", ".txt", ".pdf"],
      upload_max_bytes: 1048576,
      local_ocr: false,
      parser_ready: false,
    },
    templates: [{
      id: "ui-redesign-fixture-v1",
      name: "重设计验证模板",
      version: "ui-redesign-fixture-v1",
      families: ["technical-solution", "feasibility", "presentation"],
      approved: false,
      description: "仅用于浏览器 UI 验证。",
    }],
  };
}

function knowledgeDocuments(datasetId) {
  const name = datasetId === "fixture-delivery" ? "某市智慧交通交付案例.docx" : "工程建设规范.pdf";
  return [{ id: `${datasetId}-doc-1`, name, type: name.endsWith("pdf") ? "PDF" : "DOCX", size: 4096, chunk_count: 11, run: "completed", progress: 1, updated_at: "2026-09-29T08:00:00Z" }];
}

async function handleApi(request, response, url) {
  const path = url.pathname;
  const method = request.method || "GET";

  const tenderSource = { code: "fixture", name: "隔离验证公告来源", health_state: "ok", last_success_at: "2026-09-29T02:00:00Z" };
  const tenderItem = { id: 9901, project_name: "隔离验证 · 智慧交通建设及设备运行维护服务采购项目", project_code: "UI-ONLY-9901", region: "陕西省榆林市", purchaser: "隔离验证采购单位", budget: { amount_yuan: "1200000", cap_yuan: null, raw: "" }, publish_at: null, publish_date: "2026-09-29", publish_precision: "date", bid_deadline: "2026-10-15T01:30:00Z", status: "ACTIVE", source: tenderSource, original_url: null, current_version: 1, first_seen_at: "2026-09-29T02:00:00Z", industry_code: "transport", industry_label: "交通运输", digital_tags: ["智能化建设", "物联网与监测"], notice_category: "procurement", relevance_tier: "core", participation_status: "open", user_state: { is_read: true, is_favorite: false, is_irrelevant: false } };
  if (path === "/api/product/opportunities/" && method === "GET") return jsonResponse(request, response, 200, { items: [tenderItem], total: 1, page: 1, page_size: 20, has_more: false, stats: { total: 1, today_new: 1, latest_batch_new: 1, closing_soon: 0 }, last_updated_at: "2026-09-29T02:00:00Z" });
  if (path === "/api/product/opportunities/9901/" && method === "GET") return jsonResponse(request, response, 200, tenderItem);
  if (path === "/api/product/options/" && method === "GET") return jsonResponse(request, response, 200, { regions: ["陕西省"], industries: [{ value: "transport", label: "交通运输" }], notice_categories: [{ value: "procurement", label: "招标采购" }] });
  if (path === "/api/product/sources/" && method === "GET") return jsonResponse(request, response, 200, [tenderSource]);

  if ((path === "/api/me/" || path === "/api/auth/me") && method === "GET") return jsonResponse(request, response, 200, clone(user));
  if (path === "/api/modules/" && method === "GET") return jsonResponse(request, response, 200, [clone(productModule)]);
  const moduleMatch = path.match(/^\/api\/modules\/([^/]+)\/$/);
  if (moduleMatch && method === "GET" && decodeURIComponent(moduleMatch[1]) === "product") return jsonResponse(request, response, 200, clone(productModule));
  if (path === "/api/product/workspace/" && method === "GET") return jsonResponse(request, response, 200, overview(url));
  if (path === "/api/product/tasks/" && method === "GET") return jsonResponse(request, response, 200, projects.map((project) => taskSummary(project)));
  const outputsMatch = path.match(/^\/api\/product\/tasks\/([^/]+)\/outputs\/$/);
  if (outputsMatch && method === "GET") return jsonResponse(request, response, 200, { task_version: 3, outputs: [] });
  const historyMatch = path.match(/^\/api\/product\/tasks\/([^/]+)\/history\/$/);
  if (historyMatch && method === "GET") return jsonResponse(request, response, 200, { task_id: decodeURIComponent(historyMatch[1]), task_version: 3, history_immutable: true, tamper_claim: "application_read_only", timeline: [] });

  const taskMatch = path.match(/^\/api\/product\/tasks\/([^/]+)\/$/);
  if (taskMatch && method === "GET") {
    const task = taskFor(decodeURIComponent(taskMatch[1]));
    return task ? jsonResponse(request, response, 200, task) : notFound(request, response);
  }

  if (path === "/api/product/knowledge/status/" && method === "GET") {
    return jsonResponse(request, response, 200, { available: true, code: "fixture_ready", help: "仅由 UI fixture 提供；不连接真实知识库。" });
  }
  if (path === "/api/product/knowledge/datasets/" && method === "GET") return jsonResponse(request, response, 200, { datasets: clone(knowledgeDatasets) });
  if (path === "/api/product/knowledge/conversations/" && method === "GET") return jsonResponse(request, response, 200, { conversations: clone(knowledgeSessions) });

  const datasetDocumentsMatch = path.match(/^\/api\/product\/knowledge\/datasets\/([^/]+)\/documents\/$/);
  if (datasetDocumentsMatch && method === "GET") {
    const datasetId = decodeURIComponent(datasetDocumentsMatch[1]);
    if (!knowledgeDatasets.some((dataset) => dataset.id === datasetId)) return notFound(request, response);
    return jsonResponse(request, response, 200, { documents: knowledgeDocuments(datasetId), total: 1, page: 1, page_size: 20, has_more: false });
  }

  const chunksMatch = path.match(/^\/api\/product\/knowledge\/datasets\/([^/]+)\/documents\/([^/]+)\/chunks\/$/);
  if (chunksMatch && method === "GET") {
    const datasetId = decodeURIComponent(chunksMatch[1]);
    const documentId = decodeURIComponent(chunksMatch[2]);
    if (!knowledgeDatasets.some((dataset) => dataset.id === datasetId)) return notFound(request, response);
    const document = knowledgeDocuments(datasetId)[0];
    if (document.id !== documentId) return notFound(request, response);
    return jsonResponse(request, response, 200, { document: { id: document.id, name: document.name }, chunks: [{ id: `${document.id}-chunk-1`, content: "隔离 fixture 示例正文：资料内容仅供 UI 验证。" }], total: 1, page: 1, page_size: 10, has_more: false });
  }

  const conversationMatch = path.match(/^\/api\/product\/knowledge\/conversations\/([^/]+)\/$/);
  if (conversationMatch && method === "GET") {
    const conversationId = decodeURIComponent(conversationMatch[1]);
    const session = knowledgeSessions.find((item) => item.id === conversationId);
    return session ? jsonResponse(request, response, 200, conversationId === knowledgeSessions[0].id ? clone(knowledgeDetail) : { ...clone(session), turns: [] }) : notFound(request, response);
  }

  if (path === "/api/business/summary/" && method === "GET") return jsonResponse(request, response, 200, { projects: projects.map((project) => ({ id: project.id, name: project.title })), summary: { project_count: 2 }, source: "ui-redesign-fixture", updated_at: "2026-09-29T09:30:00Z" });
  if (path === "/api/work/summary/" && method === "GET") return jsonResponse(request, response, 200, { modules: { product: { available: true }, hr: { available: false, reason: "fixture 未提供 HR 数据" } }, sections: { my_tasks: { available: true, count: 2, items: [], reason: "ui fixture" }, pending_reviews: { available: true, count: 1, items: [], reason: "ui fixture" }, recent_results: { available: true, count: 0, items: [], reason: "ui fixture" } } });

  return notFound(request, response);
}

function proxyToVite(request, response) {
  const proxyRequest = http.request({
    hostname: HOST,
    port: VITE_PORT,
    path: request.url || "/",
    method: request.method,
    headers: { ...request.headers, host: `${HOST}:${VITE_PORT}`, "x-ui-redesign-fixture": "asset-proxy" },
  }, (upstreamResponse) => {
    response.writeHead(upstreamResponse.statusCode || 502, { ...upstreamResponse.headers, "x-ui-redesign-fixture": "asset-proxy" });
    upstreamResponse.pipe(response);
  });
  proxyRequest.on("error", () => {
    if (!response.headersSent) {
      response.writeHead(502, { "Content-Type": "text/plain; charset=utf-8", "X-UI-Redesign-Fixture": "true" });
      response.end(`UI redesign fixture could not reach Vite at http://${HOST}:${VITE_PORT}.`);
    } else response.destroy();
  });
  request.pipe(proxyRequest);
}

const server = http.createServer((request, response) => {
  const url = new URL(request.url || "/", ORIGIN);
  if (url.pathname === "/api" || url.pathname.startsWith("/api/")) void handleApi(request, response, url);
  else proxyToVite(request, response);
});

server.listen(PORT, HOST, () => {
  console.log(`${PREFIX} FIXTURE-ONLY mode; no real credentials and no API request is proxied to a backend.`);
  console.log(`${PREFIX} Browser: ${ORIGIN}/centers/product`);
  console.log(`${PREFIX} Assets: ${ORIGIN} -> http://${HOST}:${VITE_PORT}`);
  console.log(`${PREFIX} Synthetic routes: /api/me/, /api/modules/, /api/product/workspace/, /api/product/knowledge/*`);
  console.log(`${PREFIX} Projects: ui-redesign-blueprint-review, ui-redesign-processing`);
});

function shutdown() {
  server.close(() => process.exit(0));
}
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
