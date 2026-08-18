const $ = (selector) => document.querySelector(selector);

const defaultProfile = {
  user_id: "local-user",
  exam_types: ["public_institution"],
  year: new Date().getFullYear(),
  region_codes: ["JS"],
  education: "",
  degree: "",
  major: "",
  graduate_status: "unknown",
  political_status: "",
  grassroots_years: null,
  certificates: [],
  preferred_roles: [],
  include_keywords: [],
  exclude_keywords: [],
  include_process_updates: false,
  max_results: 20
};

document.addEventListener("DOMContentLoaded", async () => {
  bindEvents();
  await Promise.all([loadProfile(), loadOptions(), loadHealth()]);
});

function bindEvents() {
  $("#profileForm").addEventListener("submit", (event) => {
    event.preventDefault();
    submitRecommendation();
  });
  $("#refreshButton").addEventListener("click", () => submitRecommendation());
  $("#positionModalClose").addEventListener("click", closePositionDetail);
  $("#positionModal").addEventListener("click", (event) => {
    if (event.target === $("#positionModal")) closePositionDetail();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !$("#positionModal").hidden) closePositionDetail();
  });
}

async function loadProfile() {
  try {
    const response = await fetch("/api/v1/profile");
    if (response.ok) fillProfile(await response.json());
    else fillProfile(defaultProfile);
  } catch (_) {
    fillProfile(defaultProfile);
  }
}

async function loadOptions() {
  try {
    const response = await fetch("/api/v1/options");
    if (!response.ok) return;
    const data = await response.json();
    const select = $("#regionCode");
    for (const region of data.regions || []) {
      if ([...select.options].some((option) => option.value === region.value)) continue;
      const option = document.createElement("option");
      option.value = region.value;
      option.textContent = region.label;
      select.appendChild(option);
    }
  } catch (_) { /* The form still works with the Jiangsu default. */ }
}

async function loadHealth() {
  try {
    const response = await fetch("/api/v1/health");
    const data = await response.json();
    $("#systemStatus").textContent = `${data.enabled_sources} 个来源 · ${data.candidate_notices} 条候选公告`;
    setLLMStatus(Boolean(data.llm_configured));
  } catch (_) {
    $("#systemStatus").textContent = "本地服务未连接";
  }
}

function fillProfile(profile) {
  for (const checkbox of document.querySelectorAll("input[name=examType]")) {
    checkbox.checked = (profile.exam_types || []).includes(checkbox.value);
  }
  $("#regionCode").value = (profile.region_codes || ["JS"])[0] || "JS";
  $("#year").value = profile.year || defaultProfile.year;
  $("#education").value = profile.education || "";
  $("#degree").value = profile.degree || "";
  $("#graduateStatus").value = profile.graduate_status || "unknown";
  $("#major").value = profile.major || "";
  $("#preferredRoles").value = (profile.preferred_roles || []).join("、");
  $("#includeKeywords").value = (profile.include_keywords || []).join("、");
  $("#excludeKeywords").value = (profile.exclude_keywords || []).join("、");
  $("#includeProcessUpdates").checked = Boolean(profile.include_process_updates);
  $("#maxResults").value = String(profile.max_results || 20);
}

function readProfile() {
  const examTypes = [...document.querySelectorAll("input[name=examType]:checked")].map((item) => item.value);
  return {
    ...defaultProfile,
    exam_types: examTypes.length ? examTypes : ["public_institution"],
    year: Number($("#year").value) || defaultProfile.year,
    region_codes: [$("#regionCode").value || "JS"],
    education: $("#education").value,
    degree: $("#degree").value,
    graduate_status: $("#graduateStatus").value,
    major: $("#major").value.trim(),
    preferred_roles: splitTerms($("#preferredRoles").value),
    include_keywords: splitTerms($("#includeKeywords").value),
    exclude_keywords: splitTerms($("#excludeKeywords").value),
    include_process_updates: $("#includeProcessUpdates").checked,
    max_results: Number($("#maxResults").value) || 20
  };
}

function splitTerms(value) {
  return String(value || "").split(/[、,，\s]+/).map((item) => item.trim()).filter(Boolean);
}

async function submitRecommendation() {
  const button = $("#submitButton");
  setState("loading");
  button.disabled = true;
  try {
    const response = await fetch("/api/v1/recommendations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ profile: readProfile(), save_profile: true })
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "生成结果失败");
    renderResults(payload);
    setLLMStatus(payload.llm_used, payload.llm_error);
  } catch (error) {
    $("#errorState").textContent = error.message || "请求失败";
    setState("error");
  } finally {
    button.disabled = false;
  }
}

function renderResults(payload) {
  $("#overview").querySelector("p").textContent = payload.overview || "已生成整理结果。";
  $("#resultMeta").textContent = `本次返回 ${payload.items.length} 条 · 运行编号 ${payload.run_id.slice(0, 10)}`;
  const list = $("#resultList");
  list.replaceChildren();
  for (const item of payload.items || []) list.appendChild(createResult(item));
  const excluded = payload.excluded_notices || [];
  if (excluded.length) {
    list.appendChild(createExcludedSection(excluded));
  }
  const warnings = $("#warningList");
  warnings.replaceChildren();
  for (const value of payload.warnings || []) {
    const li = document.createElement("li");
    li.textContent = value;
    warnings.appendChild(li);
  }
  $("#warnings").hidden = !(payload.warnings || []).length;
  $("#emptyState").hidden = Boolean(payload.items.length);
  setState(payload.items.length ? "results" : "empty");
}

const MATCH_LABELS = { eligible: "岗位匹配", relevant: "相关", needs_review: "待核实" };
const POSITION_VERDICT_LABELS = { eligible: "初步符合", needs_review: "待核实", not_eligible: "不符合" };

function createResult(item) {
  const article = document.createElement("article");
  article.className = "result-item";
  const header = document.createElement("div");
  header.className = "result-header";
  const title = document.createElement("a");
  title.href = item.url;
  title.target = "_blank";
  title.rel = "noreferrer";
  title.textContent = item.title;
  const badge = document.createElement("span");
  badge.className = `match-badge ${item.match_level}`;
  badge.textContent = MATCH_LABELS[item.match_level] || "待核实";
  header.append(title, badge);
  article.appendChild(header);

  const meta = document.createElement("p");
  meta.className = "result-meta";
  meta.textContent = `${item.publisher} · ${item.region_code} · 证据：${item.detail_status === "fetched" ? "已抓取" : "缺失"}`;
  article.appendChild(meta);

  const reason = document.createElement("p");
  reason.className = "result-summary";
  reason.textContent = item.summary;
  article.appendChild(reason);

  if (item.reasons?.length) {
    const reasonList = document.createElement("ul");
    reasonList.className = "reason-list";
    for (const value of item.reasons) {
      const li = document.createElement("li"); li.textContent = value; reasonList.appendChild(li);
    }
    article.appendChild(reasonList);
  }
  if (item.positions?.length) {
    article.appendChild(createPositionList(item.positions));
  }
  if (item.evidence_excerpt) {
    const evidence = document.createElement("blockquote");
    evidence.textContent = item.evidence_excerpt;
    article.appendChild(evidence);
  }
  const checks = document.createElement("p");
  checks.className = "checks";
  checks.textContent = `报名前核对：${(item.checks || []).join("；")}`;
  article.appendChild(checks);
  return article;
}

function createPositionList(positions) {
  const wrapper = document.createElement("div");
  wrapper.className = "position-list";
  const heading = document.createElement("p");
  heading.className = "position-heading";
  heading.textContent = `职位表岗位级初核（${positions.length} 个岗位）`;
  wrapper.appendChild(heading);
  const shown = positions.filter((position) => position.verdict !== "not_eligible");
  const hidden = positions.length - shown.length;
  for (const position of shown.slice(0, 12)) {
    const row = document.createElement("div");
    row.className = "position-item";
    if (position.position_id) {
      row.classList.add("clickable");
      row.title = "点击查看岗位详情";
      row.addEventListener("click", () => openPositionDetail(position.position_id));
    }
    const chip = document.createElement("span");
    chip.className = `position-verdict ${position.verdict}`;
    chip.textContent = POSITION_VERDICT_LABELS[position.verdict] || position.verdict;
    const name = document.createElement("span");
    name.className = "position-name";
    const parts = [position.position_code, position.employer, position.position_name].filter(Boolean);
    name.textContent = parts.join(" · ") + (position.headcount ? `（招 ${position.headcount} 人）` : "");
    row.append(chip, name);
    wrapper.appendChild(row);
    if (position.reasons?.length) {
      const reasons = document.createElement("p");
      reasons.className = "position-reasons";
      reasons.textContent = position.reasons.join("；");
      wrapper.appendChild(reasons);
    }
    if (position.questions?.length) {
      const questions = document.createElement("p");
      questions.className = "position-questions";
      questions.textContent = `需核实：${position.questions.join("；")}`;
      wrapper.appendChild(questions);
    }
  }
  if (shown.length > 12) {
    const more = document.createElement("p");
    more.className = "position-reasons";
    more.textContent = `其余 ${shown.length - 12} 个岗位略…`;
    wrapper.appendChild(more);
  }
  if (hidden > 0) {
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = `${hidden} 个岗位因硬条件不符合被过滤（展开查看原因）`;
    details.appendChild(summary);
    for (const position of positions.filter((item) => item.verdict === "not_eligible").slice(0, 20)) {
      const row = document.createElement("div");
      row.className = "position-item";
      const chip = document.createElement("span");
      chip.className = "position-verdict not_eligible";
      chip.textContent = "不符合";
      const name = document.createElement("span");
      name.className = "position-name";
      const parts = [position.position_code, position.employer, position.position_name].filter(Boolean);
      name.textContent = parts.join(" · ");
      row.append(chip, name);
      details.appendChild(row);
      if (position.reasons?.length) {
        const reasons = document.createElement("p");
        reasons.className = "position-reasons";
        reasons.textContent = position.reasons.filter((reason) => reason.includes("不符合")).join("；") || position.reasons.join("；");
        details.appendChild(reasons);
      }
    }
    if (hidden > 20) {
      const more = document.createElement("p");
      more.className = "position-reasons";
      more.textContent = `其余 ${hidden - 20} 个被过滤岗位略，完整原因见推荐记录。`;
      details.appendChild(more);
    }
    wrapper.appendChild(details);
  }
  return wrapper;
}

function createExcludedSection(excluded) {
  const section = document.createElement("details");
  section.className = "excluded-section";
  const summary = document.createElement("summary");
  summary.textContent = `已过滤公告 ${excluded.length} 条：职位表岗位全部未通过硬条件初核（展开查看原因）`;
  section.appendChild(summary);
  for (const notice of excluded) {
    const item = document.createElement("details");
    item.className = "excluded-notice";
    const head = document.createElement("summary");
    const link = document.createElement("a");
    link.href = notice.url;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = notice.title;
    link.addEventListener("click", (event) => event.stopPropagation());
    const count = document.createElement("span");
    count.className = "position-reasons";
    count.textContent = ` · ${notice.positions.length} 个岗位不符合`;
    head.append(link, count);
    item.appendChild(head);
    for (const position of notice.positions) {
      const row = document.createElement("div");
      row.className = "position-item";
      const name = document.createElement("span");
      name.className = "position-name";
      const parts = [position.position_code, position.employer, position.position_name].filter(Boolean);
      name.textContent = parts.join(" · ");
      row.appendChild(name);
      item.appendChild(row);
      if (position.reasons?.length) {
        const reasons = document.createElement("p");
        reasons.className = "position-reasons";
        reasons.textContent = position.reasons.join("；");
        item.appendChild(reasons);
      }
    }
    section.appendChild(item);
  }
  return section;
}

function setState(state) {
  $("#loadingState").hidden = state !== "loading";
  $("#errorState").hidden = state !== "error";
  if (state === "loading" || state === "error") $("#emptyState").hidden = true;
  if (state === "empty") $("#emptyState").hidden = false;
}

function setLLMStatus(used, error = "") {
  const badge = $("#llmStatus");
  badge.textContent = used ? "LLM 已整理" : "规则整理";
  badge.className = `mode-badge ${used ? "active" : ""}`;
  if (error) badge.title = error;
}

const POSITION_FIELD_LABELS = [
  ["position_code", "职位代码"],
  ["employer", "招聘单位"],
  ["position_name", "招聘岗位"],
  ["work_location", "工作地点"],
  ["headcount", "招录人数"],
  ["education", "学历"],
  ["degree", "学位"],
  ["major_requirement", "专业要求"],
  ["fresh_graduate_requirement", "应届要求"],
  ["grassroots_requirement", "基层经历"],
  ["political_requirement", "政治面貌"],
  ["certificate_requirement", "资格证书"],
  ["age_requirement", "年龄"],
  ["gender_requirement", "性别"],
  ["household_requirement", "户籍/生源地"],
  ["application_schedule", "报名考试时间"],
  ["other_requirements", "其他条件"],
];

async function openPositionDetail(positionId) {
  const backdrop = $("#positionModal");
  const body = $("#positionModalBody");
  body.replaceChildren(document.createTextNode("加载中…"));
  backdrop.hidden = false;
  try {
    const response = await fetch(`/api/v1/positions/${positionId}`);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "加载岗位详情失败");
    body.replaceChildren(...renderPositionDetail(payload));
  } catch (error) {
    body.replaceChildren(document.createTextNode(error.message || "加载岗位详情失败"));
  }
}

function closePositionDetail() {
  $("#positionModal").hidden = true;
}

function renderPositionDetail(detail) {
  const nodes = [];
  const position = detail.position;
  const header = document.createElement("h3");
  const parts = [position.employer, position.position_name].filter((value) => value && value !== "unknown");
  header.textContent = (parts.length ? parts.join(" · ") : "岗位详情")
    + (position.position_code && position.position_code !== "unknown" ? `（代码 ${position.position_code}）` : "");
  nodes.push(header);

  const meta = document.createElement("p");
  meta.className = "result-meta";
  meta.textContent = `岗位级判定：${POSITION_VERDICT_LABELS[detail.verdict] || detail.verdict} · 来源公告：`;
  const noticeLink = document.createElement("a");
  noticeLink.href = detail.notice_url;
  noticeLink.target = "_blank";
  noticeLink.rel = "noreferrer";
  noticeLink.textContent = detail.notice_title || "查看公告";
  meta.appendChild(noticeLink);
  nodes.push(meta);

  const fieldTitle = document.createElement("p");
  fieldTitle.className = "section-title";
  fieldTitle.textContent = "职位表信息";
  nodes.push(fieldTitle);
  const fieldTable = document.createElement("table");
  for (const [key, label] of POSITION_FIELD_LABELS) {
    const value = position[key];
    const tr = document.createElement("tr");
    const th = document.createElement("th");
    th.textContent = label;
    const td = document.createElement("td");
    td.textContent = value && value !== "unknown" ? value : "未列出";
    tr.append(th, td);
    fieldTable.appendChild(tr);
  }
  nodes.push(fieldTable);

  const checkTitle = document.createElement("p");
  checkTitle.className = "section-title";
  checkTitle.textContent = "逐项资格判定（按当前画像）";
  nodes.push(checkTitle);
  const checkTable = document.createElement("table");
  const headRow = document.createElement("tr");
  for (const label of ["条件", "职位表要求", "判定", "说明"]) {
    const th = document.createElement("th");
    th.textContent = label;
    headRow.appendChild(th);
  }
  checkTable.appendChild(headRow);
  for (const condition of detail.conditions || []) {
    const tr = document.createElement("tr");
    const labelTd = document.createElement("td");
    labelTd.textContent = condition.label;
    const reqTd = document.createElement("td");
    reqTd.textContent = condition.requirement;
    const verdictTd = document.createElement("td");
    verdictTd.textContent = condition.verdict;
    verdictTd.className = `verdict-${condition.verdict}`;
    const reasonTd = document.createElement("td");
    reasonTd.textContent = condition.reason;
    tr.append(labelTd, reqTd, verdictTd, reasonTd);
    checkTable.appendChild(tr);
  }
  nodes.push(checkTable);

  if ((position.raw_row || []).length) {
    const rawTitle = document.createElement("p");
    rawTitle.className = "section-title";
    rawTitle.textContent = "职位表原始行";
    nodes.push(rawTitle);
    const raw = document.createElement("div");
    raw.className = "raw-row";
    raw.textContent = position.raw_row.map((cell) => (cell === "" ? "（空）" : cell)).join(" | ");
    nodes.push(raw);
  }

  if ((detail.sources || []).length) {
    const sourceTitle = document.createElement("p");
    sourceTitle.className = "section-title";
    sourceTitle.textContent = "来源凭证（可核验真实性）";
    nodes.push(sourceTitle);
    for (const source of detail.sources) {
      const item = document.createElement("div");
      item.className = "source-item";
      const line = document.createElement("p");
      line.className = "source-line";
      const tag = document.createElement("span");
      tag.className = source.is_origin ? "source-tag origin" : "source-tag";
      tag.textContent = source.is_origin ? "本岗位解析自" : "关联证据";
      const official = document.createElement("a");
      official.href = source.source_url;
      official.target = "_blank";
      official.rel = "noreferrer";
      official.textContent = "官方原文";
      line.append(tag, official);
      if (source.has_file) {
        const file = document.createElement("a");
        file.href = `/api/v1/evidence/${source.evidence_id}/file`;
        file.target = "_blank";
        file.rel = "noreferrer";
        file.textContent = "下载抓取原件";
        line.append(document.createTextNode(" · "), file);
      }
      item.appendChild(line);
      const info = document.createElement("p");
      info.className = "position-reasons";
      const status = source.parser_status === "parsed" ? "解析成功" : `解析状态 ${source.parser_status}`;
      info.textContent = `${source.source_url} · 抓取于 ${source.retrieved_at} · ${status} · SHA-256 ${source.content_sha256.slice(0, 16)}…`;
      item.appendChild(info);
      nodes.push(item);
    }
  }
  return nodes;
}
