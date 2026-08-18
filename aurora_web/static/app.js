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
  badge.textContent = item.match_level === "relevant" ? "相关" : "待核实";
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
