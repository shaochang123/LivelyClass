const $ = (selector) => document.querySelector(selector);
const form = $("#upload-form");
const fileInput = $("#pdf-file");
const dropzone = $("#dropzone");
// Reveal a required field before the browser tries to focus it in a closed panel.
form.addEventListener("invalid", (event) => {
  for (let parent = event.target.parentElement; parent; parent = parent.parentElement) {
    if (parent.tagName === "DETAILS") parent.open = true;
  }
}, true);
const representationNames = {geometry: "可计算几何", process: "过程图", relationship: "关系图", comparison: "对比图", source_figure: "原文图示讲解"};
const linkedJobId = new URLSearchParams(window.location.search).get("job") || "";
let currentJobId = /^[0-9a-f]{32}$/.test(linkedJobId) ? linkedJobId : localStorage.getItem("zhijiang-job-id");
let pollTimer = null;
let config = null;
let activeProvider = $("#llm-provider").value;
const providerProfiles = new Map(); // Service descriptors only; keys are encrypted on the server.
const credentialStates = {llm: {profile: "", saved: false, configured: false}, tts: {profile: "", saved: false, configured: false}};
const credentialTimers = {};
const credentialRequests = {llm: 0, tts: 0};
const credentialWrites = {llm: Promise.resolve(), tts: Promise.resolve()};
let speechPreviewState = {identity: "", info: null, loading: false, generating: false, error: ""};
let speechPreviewRequest = 0;
let speechPreviewTimer = null;
let shownSettingsJobId = null;
let voiceSelectionEdited = false;

function updateDirectorNote() {
  $("#director-description").textContent = selected("mode") === "ai"
    ? "模型阅读教材后，自动识别学科，选择课件风格、页面布局以及图示或动画。你可以在‘PPT编排要求’中描述希望达到的效果。"
    : "演示模式按教材顺序生成基础图文课件。选择AI生成后，由模型自动识别学科并安排画面。";
}

function appendDirectorOptions(body) {
  const ai = selected("mode") === "ai";
  body.append("style_mode", ai ? "subject" : "template");
  body.append("animation_mode", ai ? "auto" : "basic");
  body.append("ppt_template", "paper"); // Initial layout only; AI routing selects the actual template.
  body.append("content_checks", "warn");
  body.append("use_illustrations", "true");
  body.append("learner_enabled", String(ai && $("#learner-enabled").checked));
  for (const name of ["learner_background", "learner_goal", "learner_days", "learner_minutes_per_day"]) {
    body.append(name, form.elements[name].value);
  }
}

async function getJSON(url, options = {}) {
  const response = await fetch(url, options);
  let data = {};
  try { data = await response.json(); } catch (_) { /* Empty responses are valid for DELETE. */ }
  if (!response.ok) {
    const message = typeof data.detail === "string" ? data.detail : `请求失败（${response.status}）`;
    throw new Error(message);
  }
  return data;
}

function selected(name) { return form.querySelector(`input[name="${name}"]:checked`)?.value; }

function isLocalURL(value) {
  try { return ["localhost", "127.0.0.1", "[::1]"].includes(new URL(value).hostname); }
  catch (_) { return false; }
}

function credentialProfile(kind) {
  const provider = kind === "llm" ? $("#llm-provider").value : "openai";
  const value = $(`#${kind}-base-url`).value || (kind === "tts" ? config?.tts_base_url : provider === config?.llm_provider ? config?.llm_base_url : "");
  try {
    const url = new URL(value);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash) return null;
    return {kind, provider, base_url: url.href.replace(/\/+$/, "")};
  } catch (_) { return null; }
}
function credentialIdentity(profile) { return profile ? JSON.stringify(profile) : ""; }
function hasCredential(kind) {
  const state = credentialStates[kind];
  return state.profile === credentialIdentity(credentialProfile(kind)) && (state.saved || state.configured);
}

async function refreshCredential(kind) {
  const profile = credentialProfile(kind), identity = credentialIdentity(profile);
  const request = ++credentialRequests[kind];
  credentialStates[kind] = {profile: identity, saved: false, configured: false};
  $(`#clear-${kind}-key`).hidden = true;
  if (!profile) { $(`#${kind}-key-state`).textContent = ""; updateConsent(); return; }
  try {
    const state = await getJSON(`/api/credentials/status?${new URLSearchParams(profile)}`);
    if (request !== credentialRequests[kind] || identity !== credentialIdentity(credentialProfile(kind))) return;
    credentialStates[kind] = {profile: identity, ...state};
    $(`#${kind}-key-state`).textContent = state.saved ? "密钥已保存在本机" : state.configured ? "已使用本机配置中的密钥" : "";
    $(`#clear-${kind}-key`).hidden = !state.saved;
  } catch (error) {
    if (request !== credentialRequests[kind]) return;
    $(`#${kind}-key-state`).textContent = error.message;
  }
  updateConsent();
}

async function saveCredential(kind) {
  const field = $(`#${kind}-api-key`), key = field.value.trim();
  const profile = credentialProfile(kind), identity = credentialIdentity(profile);
  if (!key || !profile || !$(`#remember-${kind}-key`).checked || !config?.credential_persistence) return;
  rememberServiceFields();
  try {
    const write = credentialWrites[kind].catch(() => {}).then(() => getJSON("/api/credentials", {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({...profile, api_key: key})}));
    credentialWrites[kind] = write;
    await write;
    if (identity !== credentialIdentity(credentialProfile(kind))) return;
    ++credentialRequests[kind]; // A delayed status read must not overwrite this save.
    credentialStates[kind] = {profile: identity, saved: true, configured: false};
    $(`#${kind}-key-state`).textContent = "密钥已保存在本机";
    $(`#clear-${kind}-key`).hidden = false;
    updateConsent();
    return true;
  } catch (error) {
    if (identity === credentialIdentity(credentialProfile(kind))) $(`#${kind}-key-state`).textContent = `密钥未保存：${error.message}`;
  }
  updateConsent();
}

function rememberServiceFields() {
  const profile = credentialProfile("llm");
  if (profile) providerProfiles.set(profile.provider, Object.fromEntries(
    ["llm_base_url", "llm_model", "llm_review_model", "llm_math_model"].map(name => [name, form.elements[name].value])));
  const voiceProfile = credentialProfile("tts");
  const voice = voiceProfile ? Object.fromEntries(["tts_base_url", "tts_model", "tts_voice"].map(name => [name, form.elements[name].value])) : null;
  const remember = Object.fromEntries(["llm", "tts"].map(kind => [kind, $(`#remember-${kind}-key`).checked]));
  try { localStorage.setItem("zhijiang-service-profiles", JSON.stringify({provider: $("#llm-provider").value, profiles: [...providerProfiles], voice, remember})); }
  catch (_) { /* Encryption still works when browser storage is unavailable. */ }
}

function selectedSpeechProfile() {
  const destination = credentialProfile("tts");
  const model = $("#tts-model").value.trim(), voice = $("#tts-voice").value.trim();
  return destination && model && voice ? {base_url: destination.base_url, model, voice} : null;
}
function speechIdentity(profile) { return profile ? JSON.stringify(profile) : ""; }

async function readSelectedSpeechPreview(profile, identity, request) {
  try {
    const result = await getJSON(`/api/speech-preview/selected?${new URLSearchParams(profile)}`);
    if (request !== speechPreviewRequest || identity !== speechIdentity(selectedSpeechProfile())) return;
    speechPreviewState = {identity, info: result.preview, loading: false, generating: false, error: ""};
  } catch (error) {
    if (request !== speechPreviewRequest || identity !== speechIdentity(selectedSpeechProfile())) return;
    speechPreviewState = {identity, info: null, loading: false, generating: false, error: error.message};
  }
  updateSpeechPreview();
}

function updateSpeechPreview() {
  if (!config) return;
  const mode = selected("voice_mode");
  const profile = selectedSpeechProfile(), identity = speechIdentity(profile);
  if (mode === "ai" && speechPreviewState.identity !== identity) {
    clearTimeout(speechPreviewTimer);
    const request = ++speechPreviewRequest;
    speechPreviewState = {identity, info: null, loading: !!profile, generating: false, error: ""};
    if (profile) speechPreviewTimer = setTimeout(() => readSelectedSpeechPreview(profile, identity, request), 200);
  }
  const state = speechPreviewState;
  const preview = mode === "system" ? config.speech_previews?.system : state.info;
  const audio = $("#speech-preview-audio"), source = preview?.url || "";
  $("#speech-preview").hidden = false;
  $("#speech-preview-title").textContent = mode === "system" ? "系统声线试听" : `AI 声线试听${profile ? ` · ${profile.voice}` : ""}`;
  $("#speech-preview").setAttribute("aria-label", $("#speech-preview-title").textContent);
  audio.setAttribute("aria-label", $("#speech-preview-title").textContent);
  if ((audio.getAttribute("src") || "") !== source) {
    audio.pause();
    if (source) audio.src = source; else audio.removeAttribute("src");
    audio.load();
  }
  audio.hidden = !preview;
  $("#speech-preview-status").hidden = !!preview;
  $("#speech-preview-status").textContent = mode === "system" ? "当前系统声线暂不可用" :
    !profile ? "填写语音 API 地址、模型和音色即可试听" : state.generating ? "正在生成当前声线的试听…" :
    state.loading ? "正在读取试听…" : state.error || "当前声线暂无试听，点击生成";
  const button = $("#generate-speech-preview");
  button.hidden = mode !== "ai";
  button.disabled = !profile || state.generating || state.loading;
  button.textContent = state.generating ? "正在生成…" : preview ? "重新生成试听" : "生成试听";
}

$("#generate-speech-preview").addEventListener("click", async () => {
  const profile = selectedSpeechProfile();
  if (!profile || selected("voice_mode") !== "ai") return;
  const identity = speechIdentity(profile), force = !!speechPreviewState.info;
  clearTimeout(speechPreviewTimer);
  const request = ++speechPreviewRequest;
  speechPreviewState = {identity, info: null, loading: false, generating: true, error: ""};
  updateSpeechPreview();
  try {
    if (!isLocalURL(profile.base_url) && !$("#remote-consent").checked) throw new Error("请先勾选发送到所选语音服务的同意选项");
    rememberServiceFields();
    await saveCredential("tts");
    if (request !== speechPreviewRequest || identity !== speechIdentity(selectedSpeechProfile())) return;
    const info = await getJSON("/api/speech-preview", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({...profile, api_key: $("#tts-api-key").value.trim(), remote_consent: $("#remote-consent").checked, force})});
    if (request !== speechPreviewRequest || identity !== speechIdentity(selectedSpeechProfile())) return;
    speechPreviewState = {identity, info, loading: false, generating: false, error: ""};
  } catch (error) {
    if (request !== speechPreviewRequest || identity !== speechIdentity(selectedSpeechProfile())) return;
    speechPreviewState = {identity, info: null, loading: false, generating: false, error: error.message};
  }
  updateSpeechPreview();
});

function clearSubmittedKeys() {
  for (const kind of ["image","video"]) $(`#${kind}-api-key`).value = "";
  for (const kind of ["llm","tts"]) {
    const state=credentialStates[kind];
    if (!$(`#remember-${kind}-key`).checked || (state.saved && state.profile===credentialIdentity(credentialProfile(kind)))) {
      $(`#${kind}-api-key`).value="";
    }
  }
}

function updateConsent() {
  const ai = selected("mode") === "ai";
  const aiVoice = selected("voice_mode") === "ai";
  $("#ai-settings").hidden = !ai;
  $("#tts-settings").hidden = !aiVoice;
  const modelURL = $("#llm-base-url").value || config?.llm_base_url || "";
  const ttsURL = $("#tts-base-url").value || config?.tts_base_url || "";
  const localVoice = isLocalURL(ttsURL);
  const modelName = $("#llm-model").value || config?.llm_model || "";
  const cloudModel = $("#llm-provider").value === "ollama" && [modelName,$("#llm-review-model").value,$("#llm-math-model").value].some(name=>/(:cloud|-cloud)$/i.test(name));
  let remoteArt = false;
  for (const kind of ["image", "video"]) {
    const enabled = $(`#${kind}-protocol`).value !== "disabled";
    const remote = enabled && !isLocalURL($(`#${kind}-base-url`).value);
    remoteArt ||= remote;
    for (const field of ["base-url","model","api-key"]) {
      $(`#${kind}-${field}`).disabled = !enabled;
      $(`#${kind}-${field}`).required = enabled && (field !== "api-key" || remote);
    }
  }
  const remote = (ai && (!isLocalURL(modelURL) || cloudModel)) || (aiVoice && !localVoice) || remoteArt;
  $("#remote-consent-row").hidden = !remote;
  $("#remote-consent").required = remote;
  if (!remote) $("#remote-consent").checked = false;
  const provider = $("#llm-provider").value;
  const usesDefaultModel = provider === config?.llm_provider &&
    modelURL.replace(/\/$/, "") === config?.llm_base_url?.replace(/\/$/, "") &&
    modelName === config?.llm_model;
  $("#llm-base-url").required = ai && provider !== config?.llm_provider;
  $("#llm-model").required = ai && provider !== config?.llm_provider;
  $("#llm-api-key").required = ai && provider === "openai" && !isLocalURL(modelURL) && !hasCredential("llm") && !(usesDefaultModel && config?.llm_ready);
  $("#llm-api-key").placeholder = hasCredential("llm") ? "已保存，留空即可使用" : provider === "ollama" ? "本机 Ollama 可留空" : "填写 API 密钥";
  for (const name of ["tts_base_url", "tts_model", "tts_voice"]) {
    form.elements[name].required = aiVoice;
  }
  const usesDefaultVoice = ttsURL === config?.tts_base_url &&
    $("#tts-model").value === config?.tts_model && $("#tts-voice").value === config?.tts_voice;
  $("#tts-api-key").required = aiVoice && !localVoice && !hasCredential("tts") && !(usesDefaultVoice && config?.ai_tts_ready);
  $("#tts-api-key").placeholder = hasCredential("tts") ? "已保存，留空即可使用" : localVoice ? "本机服务可留空" : "填写 API 密钥";
  $("#ai-ready-label").textContent = modelName;
  $("#tts-ready-label").textContent = $("#tts-voice").value;
  updateSpeechPreview();
}

function setFile(file) {
  if (!file) return;
  const transfer = new DataTransfer();
  transfer.items.add(file);
  fileInput.files = transfer.files;
  $("#file-label").textContent = file.name;
}

dropzone.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") { event.preventDefault(); fileInput.click(); }
});
dropzone.addEventListener("dragover", (event) => { event.preventDefault(); dropzone.classList.add("dragover"); });
dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragover"));
dropzone.addEventListener("drop", (event) => {
  event.preventDefault(); dropzone.classList.remove("dragover");
  if (event.dataTransfer.files.length) setFile(event.dataTransfer.files[0]);
});
fileInput.addEventListener("change", () => { if (fileInput.files.length) $("#file-label").textContent = fileInput.files[0].name; });
form.querySelectorAll('input[type="radio"]').forEach((input) => input.addEventListener("change", () => {
  if (selected("mode") === "demo") {
    $("#image-protocol").value = "disabled";$("#video-protocol").value = "disabled";
  }
  updateDirectorNote(); updateConsent();
}));
for (const kind of ["image","video"]) {
  $(`#${kind}-protocol`).addEventListener("change", () => {
    if (kind === "image" && $("#image-protocol").value === "jimeng") {
      $("#image-base-url").value = "https://openapi.cv.volces.com";
      $("#image-model").value = "jimeng_t2i_v30";
      $("#image-api-key").value = "";
    }
    if ($(`#${kind}-protocol`).value !== "disabled") form.querySelector('input[name="mode"][value="ai"]').checked = true;
    $("#remote-consent").checked = false; updateConsent();
  });
  $(`#${kind}-base-url`).addEventListener("input", () => {
    $(`#${kind}-api-key`).value = "";$("#remote-consent").checked = false;updateConsent();
  });
  $(`#${kind}-model`).addEventListener("input", () => {$("#remote-consent").checked=false;});
}
form.querySelectorAll('input[name="voice_mode"]').forEach(input => input.addEventListener("change", () => { voiceSelectionEdited = true; }));
$("#llm-provider").addEventListener("change", () => {
  const fields = ["llm_base_url", "llm_model", "llm_review_model", "llm_math_model"];
  providerProfiles.set(activeProvider, Object.fromEntries(fields.map(name => [name, form.elements[name].value])));
  activeProvider = $("#llm-provider").value;
  const defaults = activeProvider === config?.llm_provider ? config : null;
  const profile = providerProfiles.get(activeProvider) || {
    llm_base_url: defaults?.llm_base_url || (activeProvider === "ollama" ? "http://127.0.0.1:11434" : ""),
    llm_model: defaults?.llm_model || "",
    llm_review_model: defaults?.llm_review_model || "",
    llm_math_model: defaults?.llm_math_model || "",
  };
  for (const name of fields) form.elements[name].value = profile[name];
  $("#llm-api-key").value = "";
  rememberServiceFields();
  refreshCredential("llm");
  updateConsent();
});
$("#llm-base-url").addEventListener("input", updateConsent);
for (const name of ["llm_model", "llm_math_model", "llm_review_model", "tts_base_url", "tts_model", "tts_voice"]) {
  form.elements[name].addEventListener("input", updateConsent);
}

for (const kind of ["llm", "tts"]) {
  $(`#${kind}-api-key`).addEventListener("input", () => {
    clearTimeout(credentialTimers[kind]);
    credentialTimers[kind] = setTimeout(() => saveCredential(kind), 450);
  });
  $(`#${kind}-api-key`).addEventListener("blur", async () => {
    const field = $(`#${kind}-api-key`), key = field.value.trim();
    const identity = credentialIdentity(credentialProfile(kind));
    clearTimeout(credentialTimers[kind]);
    const saved = await saveCredential(kind);
    if (saved && key && field.value.trim() === key && identity === credentialIdentity(credentialProfile(kind))) field.value = "";
  });
  $(`#remember-${kind}-key`).addEventListener("change", () => { rememberServiceFields(); saveCredential(kind); });
  $(`#clear-${kind}-key`).addEventListener("click", async () => {
    const profile = credentialProfile(kind); if (!profile) return;
    clearTimeout(credentialTimers[kind]); $(`#${kind}-api-key`).value = "";
    try { await credentialWrites[kind].catch(() => {});
      await getJSON(`/api/credentials?${new URLSearchParams(profile)}`, {method:"DELETE"});
      await refreshCredential(kind);
    } catch (error) { $(`#${kind}-key-state`).textContent = error.message; }
  });
  $(`#${kind}-base-url`).addEventListener("input", () => {
    clearTimeout(credentialTimers[kind]);
    $(`#${kind}-api-key`).value = "";
    clearTimeout(credentialTimers[`${kind}-status`]);
    credentialTimers[`${kind}-status`] = setTimeout(() => { rememberServiceFields(); refreshCredential(kind); }, 200);
  });
}
for (const name of ["llm_model","llm_math_model","llm_review_model","tts_model","tts_voice"]) {
  form.elements[name].addEventListener("input", rememberServiceFields);
  form.elements[name].addEventListener("change", rememberServiceFields);
}
$("#speech-preview-audio").addEventListener("error", () => {
  if (!$("#speech-preview-audio").getAttribute("src") || !$("#speech-preview-audio").error) return;
  $("#speech-preview-status").hidden = false;
  $("#speech-preview-status").textContent = "试听暂不可用，请检查对应语音服务";
});

function showJob(job) {
  if (shownSettingsJobId !== job.id) {
    shownSettingsJobId = job.id;
    const modeChoice = form.querySelector(`input[name="mode"][value="${job.mode}"]`);
    if (modeChoice) modeChoice.checked = true;
    const voiceChoice = form.querySelector(`input[name="voice_mode"][value="${job.voice_mode}"]`);
    if (voiceChoice && !voiceSelectionEdited) voiceChoice.checked = true;
    // A delayed restore of the previous job must not replace a new choice.
    for (const kind of ["image","video"]) {
      const media = job.model_settings?.art?.[kind];
      if (media) for (const field of ["protocol","base_url","model"]) form.elements[`${kind}_${field}`].value = media[field] || (field === "protocol" ? "disabled" : "");
    }
    $("#art-max-assets").value = job.model_settings?.art?.max_assets || 12;
    const learner = job.model_settings?.learner || {};
    $("#learner-enabled").checked = Boolean(learner.enabled);
    $("#learner-background").value = learner.background || "";
    $("#learner-goal").value = learner.goal || "";
    $("#learner-days").value = learner.days || 3;
    $("#learner-minutes").value = learner.minutes_per_day || 30;
    if (job.mode === "ai" && job.model_settings) {
      $("#llm-provider").value = job.model_settings.provider || "openai";
      activeProvider = $("#llm-provider").value;
      $("#llm-base-url").value = job.model_settings.base_url || "";
      $("#llm-model").value = job.model_settings.model || "";
      $("#llm-review-model").value = job.model_settings.review_model || "";
      $("#llm-math-model").value = job.model_settings.math_model || "";
      $("#custom-prompt").value = job.model_settings.prompt || "";
    }
    if (job.voice_mode === "ai" && job.voice_settings) {
      $("#tts-base-url").value = job.voice_settings.base_url || "";
      $("#tts-model").value = job.voice_settings.model || "";
      $("#tts-voice").value = job.voice_settings.voice || "";
    }
    updateDirectorNote(); updateConsent();
    rememberServiceFields();
    refreshCredential("llm"); refreshCredential("tts");
    if (!$("#remote-consent-row").hidden) $("#remote-consent").checked = job.remote_consent;
  }
  $("#job-section").hidden = false;
  $("#job-filename").textContent = job.filename || "";
  const badge = $("#job-badge");
  const states = { queued: "排队中", running: "生成中", completed: "已完成", failed: "失败" };
  badge.textContent = states[job.status] || job.status;
  badge.className = `status-badge ${job.status}`;
  const stages = { queued: "等待处理", completed: "生成完成", failed: "生成失败", interrupted: "服务中断" };
  $("#job-stage").textContent = stages[job.stage] || job.stage || "等待处理";
  $("#job-progress").style.width = `${job.progress || 0}%`;
  $("#job-message").textContent = job.error || "";
  $("#job-message").hidden = !job.error;
  $("#presentation-export").hidden = !(job.status === "completed" && job.has_presentation);
  $("#retry-button").hidden = job.status !== "failed";
  $("#delete-button").hidden = !["completed", "failed"].includes(job.status);
}

function addSegment(segment, index) {
  const details = document.createElement("details");
  details.className = "segment";
  if (index === 0) details.open = true;
  const summary = document.createElement("summary");
  const title = document.createElement("strong");
  title.textContent = `${String(index + 1).padStart(2, "0")} · ${segment.title}`;
  const page = document.createElement("span");
  page.textContent = `PDF 第 ${segment.evidence.page} 页${segment.evidence.ocr ? " · OCR" : ""} ↗`;
  summary.append(title, page);
  const body = document.createElement("div");
  body.className = "segment-body";
  const narration = document.createElement("p");
  narration.textContent = segment.narration;
  const source = document.createElement("details");
  source.className = "source-evidence";
  const sourceHeading = document.createElement("summary");
  sourceHeading.textContent = "原文依据";
  const quote = document.createElement("div");
  quote.className = "source-quote";
  quote.textContent = segment.evidence.quote;
  source.append(sourceHeading, quote);
  const scene = segment.math_scene || segment.visual_scene;
  if (!scene || segment.source_spans?.length) body.append(narration);
  if (scene) {
    if (scene.diagram) {
      const strategy = document.createElement("p");
      strategy.textContent = `教学表达：${representationNames[scene.diagram.representation] || scene.diagram.representation}。${scene.diagram.rationale}`;
      body.append(strategy);
      if (scene.diagram.relations.length) {
        const graph = document.createElement("details");
        const heading = document.createElement("summary");
        heading.textContent = "知识关系与来源";
        graph.append(heading);
        const nodes = new Map(scene.diagram.nodes.map(node => [node.id, node.label]));
        scene.diagram.relations.forEach(edge => {
          const triple = document.createElement("p");
          triple.textContent = `${nodes.get(edge.source)} ${edge.directed ? "→" : "—"} ${edge.label} ${edge.directed ? "→" : "—"} ${nodes.get(edge.target)}（PDF 第 ${segment.evidence.page} 页）`;
          const statements = document.createElement("p");
          statements.textContent = (edge.source_statements || []).join(" ");
          graph.append(triple, statements);
        });
        body.append(graph);
      }
    }
    const steps = document.createElement("ol");
    (segment.source_spans?.length ? [] : scene.beats).forEach((beat) => {
      const item = document.createElement("li");
      item.textContent = beat.narration;
      steps.append(item);
    });
    body.append(steps);
  }
  body.append(source);
  details.append(summary, body);
  $("#segments").append(details);
}

async function showLesson(jobId) {
  const lesson = await getJSON(`/api/jobs/${jobId}/lesson`);
  $("#lesson-result").hidden = false;
  $("#lesson-title").textContent = lesson.title;
  $("#lesson-objective").textContent = lesson.objective;
  $("#lesson-notice").replaceChildren();
  for (const warning of lesson.animation_report?.warnings || []) {
    const line = document.createElement("p");
    line.textContent = `第 ${warning.segment_id} 段：${warning.message}`;
    $("#lesson-notice").append(line);
  }
  if (!$("#lesson-notice").childElementCount) $("#lesson-notice").textContent = "无生成提示";
  const template = config?.ppt_templates?.find(item => item.id === lesson.ppt_template);
  const courseStyle = lesson.deck_plan?.course_style;
  $("#ppt-template-result").textContent = courseStyle
    ? `${courseStyle.format === "scene_drama" ? "场景漫剧" : courseStyle.mode === "comic" ? "漫剧讲解" : "学科自适应"}：${courseStyle.design.name} · ${courseStyle.design.subject}${lesson.deck_plan?.drama_production?.medium === "image" ? " · 插画分镜" : lesson.deck_plan?.drama_production?.medium === "video" ? " · 动态片段" : ""}`
    : `PPT：${template?.name || "深色演示"}${lesson.deck_plan?.planner === "model_layout_selection" ? " · AI 规划页面版式" : lesson.deck_plan?.planner === "deterministic_demo_layout" ? " · 规则排版" : ""}`;
  $("#segment-count").textContent = `${lesson.segments.length} 个讲解片段`;
  $("#voice-note").textContent = lesson.voice_mode === "ai" ? "AI 配音" : "系统配音";
  $("#generation-mode").textContent = lesson.mode === "ai" ? "AI 生成" : "规则生成";
  const routing = lesson.deck_plan?.subject_routing;
  const literal = lesson.deck_plan?.narration_mode === "source_verbatim";
  const routedTemplate = config?.ppt_templates?.find(item => item.id === routing?.selection?.template_id);
  const mediumNames = {svg:"SVG图解", computed:"连续图形演示", scene_drama:"场景漫剧", generated_illustration:"原创插画"};
  const actualMedia = [...new Set(Object.values(lesson.deck_plan?.visual_story?.visuals || {}).map(v => mediumNames[v.executed_medium]).filter(Boolean))];
  $("#source-route-result").textContent = [routing ? `已识别学科：${routing.classification.subject} · ${routedTemplate?.name || "课程模板"}` : "",
    actualMedia.length ? `实际画面：${actualMedia.join("、")}` : "",
    literal ? "字幕、配音逐字使用教材提取文本；PPT独立提炼与绘图。" : ""].filter(Boolean).join("\n");
  $("#source-exports").hidden = !literal;
  $("#download-subtitles").href = `/api/jobs/${jobId}/subtitles`;
  $("#download-transcript").href = `/api/jobs/${jobId}/transcript`;
  $("#download-coverage").href = `/api/jobs/${jobId}/source-coverage`;
  const studyPlan = lesson.deck_plan?.learning_plan;
  $("#learning-plan-result").hidden = !studyPlan;
  $("#learning-plan-sessions").replaceChildren();
  if (studyPlan) {
    $("#download-learning-plan").href = `/api/jobs/${jobId}/learning-plan`;
    $("#learning-plan-overview").textContent = studyPlan.overview;
    $("#learning-plan-time").textContent = `${studyPlan.sessions.length}天 · 每天${studyPlan.profile.minutes_per_day}分钟 · 共${studyPlan.total_minutes}分钟。${studyPlan.time_note || ""}`;
    for (const session of studyPlan.sessions) {
      const details = document.createElement("details"); details.className = "segment";
      const title = document.createElement("summary"); title.textContent = `第${session.day}天 · ${session.title}（${session.minutes}分钟）`;
      const content = document.createElement("div"); content.className = "segment-body";
      const refs = document.createElement("p"); refs.textContent = `课程片段 ${session.segment_ids.join("、")} · PDF 第${session.source_pages.join("、")}页`;
      const activities = document.createElement("ul");
      for (const activity of session.activities) { const item = document.createElement("li"); item.textContent = activity; activities.append(item); }
      const check = document.createElement("p"); check.textContent = `自测：${session.self_check}`;
      content.append(refs, activities, check); details.append(title, content);
      $("#learning-plan-sessions").append(details);
    }
  }
  $("#segments").replaceChildren();
  lesson.segments.forEach(addSegment);
  const videoURL = `/api/jobs/${jobId}/video`;
  $("#lesson-video").src = videoURL;
  $("#download-video").href = videoURL;
  $("#download-presentation").href = `/api/jobs/${jobId}/presentation`;
  const report = lesson.animation_report || {};
  $("#math-report").hidden = !report.renderer;
  $("#math-report").textContent = report.scene_count ?
    `${report.scene_count} 个${report.scene_type === "general" ? "教学" : "数学推演"}场景 · ${report.renderer}` : report.reason || "基础图示";
  $("#download-math-scenes").hidden = !report.scene_count;
  $("#download-math-scenes").href = `/api/jobs/${jobId}/scenes`;
}

async function refreshJob() {
  if (!currentJobId) return;
  try {
    const job = await getJSON(`/api/jobs/${currentJobId}`);
    showJob(job);
    if (job.status === "completed") {
      clearInterval(pollTimer); pollTimer = null;
      await showLesson(currentJobId);
    } else if (job.status === "failed") {
      clearInterval(pollTimer); pollTimer = null;
      $("#lesson-result").hidden = true;
    }
  } catch (error) {
    clearInterval(pollTimer); pollTimer = null;
    if (error.message.includes("任务不存在")) {
      currentJobId = null;
      localStorage.removeItem("zhijiang-job-id");
    }
    $("#job-section").hidden = false;
    $("#job-stage").textContent = error.message;
  }
}

function appendSourceOptions(body) {
  body.append("narration_mode", "source_verbatim");
  body.append("art_max_assets", $("#art-max-assets").value);
  for (const kind of ["image","video"]) for (const field of ["protocol","base_url","model","api_key"]) {
    body.append(`${kind}_${field}`, form.elements[`${kind}_${field}`].value);
  }
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  refreshJob();
  pollTimer = setInterval(refreshJob, 1400);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!form.reportValidity()) return;
  const button = $("#submit-button");
  button.disabled = true;
  button.textContent = "正在提交…";
  try {
    rememberServiceFields();
    await Promise.all([saveCredential("llm"), saveCredential("tts")]);
    const body = new FormData();
    body.append("file", fileInput.files[0]);
    body.append("mode", selected("mode"));
    body.append("voice_mode", selected("voice_mode"));
    appendDirectorOptions(body);
    appendSourceOptions(body);
    body.append("rights_confirmed", String($("#rights-confirmed").checked));
    body.append("remote_consent", String($("#remote-consent").checked));
    if (selected("mode") === "ai") {
      for (const name of ["llm_provider", "llm_base_url", "llm_model", "llm_math_model", "llm_review_model", "llm_api_key", "custom_prompt"]) {
        body.append(name, form.elements[name].value);
      }
    }
    if (selected("voice_mode") === "ai") {
      for (const name of ["tts_base_url", "tts_model", "tts_voice", "tts_api_key"]) {
        body.append(name, form.elements[name].value);
      }
    }
    const job = await getJSON("/api/jobs", { method: "POST", body });
    clearSubmittedKeys();
    currentJobId = job.id;
    localStorage.setItem("zhijiang-job-id", job.id);
    $("#lesson-result").hidden = true;
    showJob(job);
    $("#job-section").scrollIntoView({ behavior: "smooth", block: "start" });
    startPolling();
  } catch (error) {
    alert(error.message);
  } finally {
    button.disabled = false;
    button.innerHTML = '生成一节课 <span aria-hidden="true">↗</span>';
  }
});

$("#delete-button").addEventListener("click", async () => {
  if (!currentJobId || !confirm("确定删除此任务的本地 PDF、课程、视频和 PPT 吗？")) return;
  try {
    await getJSON(`/api/jobs/${currentJobId}`, { method: "DELETE" });
    currentJobId = null;
    localStorage.removeItem("zhijiang-job-id");
    $("#job-section").hidden = true;
    $("#lesson-result").hidden = true;
    $("#lesson-video").removeAttribute("src");
    $("#lesson-video").load();
  } catch (error) { alert(error.message); }
});

$("#retry-button").addEventListener("click", async () => {
  if (!currentJobId) return;
  const button = $("#retry-button");
  button.disabled = true;
  try {
    rememberServiceFields();
    await Promise.all([saveCredential("llm"), saveCredential("tts")]);
    const body = new FormData();
    body.append("mode", selected("mode"));
    body.append("voice_mode", selected("voice_mode"));
    appendDirectorOptions(body);
    appendSourceOptions(body);
    body.append("remote_consent", String($("#remote-consent").checked));
    body.append("replace_settings", "true");
    if (selected("mode") === "ai") {
      for (const name of ["llm_provider", "llm_base_url", "llm_model", "llm_math_model", "llm_review_model", "llm_api_key", "custom_prompt"]) {
        body.append(name, form.elements[name].value);
      }
    }
    if (selected("voice_mode") === "ai") {
      for (const name of ["tts_base_url", "tts_model", "tts_voice", "tts_api_key"]) {
        body.append(name, form.elements[name].value);
      }
    }
    const job = await getJSON(`/api/jobs/${currentJobId}/retry`, { method: "POST", body });
    clearSubmittedKeys();
    $("#lesson-result").hidden = true;
    showJob(job);
    startPolling();
  } catch (error) {
    alert(error.message);
  } finally {
    button.disabled = false;
  }
});

(async function initialize() {
  try {
    config = await getJSON("/api/config");
    if (!config.default_narration_mode) {
      $("#submit-button").disabled = true;
      $("#retry-button").disabled = true;
      $("#submit-button").textContent = "请重启服务以启用原文课程";
      $(".source-policy").textContent = "界面已更新，当前后台仍是旧版本。请重启启动服务的终端，再使用原文字幕、学科编排与绘图接口。";
    }
    updateDirectorNote();
    $("#pdf-limits").textContent = `文字版 / 扫描版 · ${Math.round(config.max_pdf_bytes / 1024 / 1024)} MB 以内`;
    $("#llm-provider").value = config.llm_provider === "ollama" ? "ollama" : "openai";
    activeProvider = $("#llm-provider").value;
    $("#llm-base-url").value = config.llm_base_url || "";
    $("#llm-model").value = config.llm_model || "";
    $("#tts-base-url").value = config.tts_base_url || "";
    $("#tts-model").value = config.tts_model || "";
    $("#tts-voice").value = config.tts_voice || "";
    if (!voiceSelectionEdited) {
      const preferredVoice = form.querySelector(`input[name="voice_mode"][value="${config.default_voice_mode || 'system'}"]`);
      if (preferredVoice) preferredVoice.checked = true;
    }
    try {
      const saved = JSON.parse(localStorage.getItem("zhijiang-service-profiles") || "null");
      for (const [provider, profile] of saved?.profiles || []) {
        if (["openai", "ollama"].includes(provider) && profile && typeof profile === "object") providerProfiles.set(provider, profile);
      }
      const profile = providerProfiles.get(saved?.provider);
      if (profile) {
        $("#llm-provider").value = saved.provider; activeProvider = saved.provider;
        for (const name of ["llm_base_url","llm_model","llm_review_model","llm_math_model"]) form.elements[name].value = typeof profile[name] === "string" ? profile[name] : "";
      }
      if (saved?.voice) for (const name of ["tts_base_url","tts_model","tts_voice"]) {
        if (typeof saved.voice[name] === "string") form.elements[name].value = saved.voice[name];
      }
      for (const kind of ["llm", "tts"]) {
        if (typeof saved?.remember?.[kind] === "boolean") $(`#remember-${kind}-key`).checked = saved.remember[kind];
      }
    } catch (_) { /* Ignore malformed browser preferences. */ }
    for (const kind of ["llm","tts"]) {
      $(`#remember-${kind}-key`).disabled = !config.credential_persistence;
      if (!config.credential_persistence) $(`#remember-${kind}-key`).checked = false;
    }
    if (!config.llm_ready) $("#ai-settings").open = true;
    if (!config.ai_tts_ready) $("#tts-settings").open = true;
    updateConsent();
    refreshCredential("llm"); refreshCredential("tts");
    if (currentJobId) startPolling();
  } catch (error) {
    $("#ai-ready-label").textContent = "配置读取失败";
    $("#tts-ready-label").textContent = "配置读取失败";
  }
})();
