const $ = (selector) => document.querySelector(selector);
const form = $("#upload-form");
const fileInput = $("#pdf-file");
const dropzone = $("#dropzone");
const settingsDialog = $("#workspace-settings");
const composer = $(".composer");
const promptInput = $("#custom-prompt");
const conversation = $("#job-section");
const turnList = $("#conversation-turns");
const resultActions = $("#course-result-actions");
let reportingInvalid = false;

function showSettings(category) {
  for (const name of ["model", "generation"]) {
    $(`#${name}-panel`).hidden = name !== category;
    $(`[data-settings-tab="${name}"]`).setAttribute("aria-pressed", String(name === category));
  }
  $(".settings-content").scrollTop = 0;
  if (!settingsDialog.open) {
    const revising = Boolean(latestSuccess && !fileInput.files.length);
    if (revising) restoreSettings(latestSuccess.job);
    $("#revision-settings-note").hidden = !revising;
    for (const field of settingsDialog.querySelectorAll("input, select, textarea")) {
      if (field.id.endsWith("-api-key") || field.id.startsWith("remember-")) continue;
      // Some controls are disabled independently (for example by media protocol).
      if (revising && !field.disabled) {
        field.disabled = true;
        field.dataset.revisionLocked = "";
      }
    }
    settingsDialog.showModal();
  }
}

for (const button of document.querySelectorAll("[data-settings], [data-settings-tab]")) {
  button.addEventListener("click", () => showSettings(button.dataset.settings || button.dataset.settingsTab));
}
for (const selector of ["#close-settings", "#done-settings"]) {
  $(selector).addEventListener("click", () => settingsDialog.close());
}
for (const button of settingsDialog.querySelectorAll(".model-help button")) {
  button.addEventListener("click", () => button.focus({preventScroll: true}));
}
settingsDialog.addEventListener("close", () => {
  for (const field of settingsDialog.querySelectorAll("[data-revision-locked]")) {
    field.disabled = false;
    delete field.dataset.revisionLocked;
  }
  updateConsent();
});
settingsDialog.addEventListener("keydown", event => {
  if (event.key === "Enter" && event.target.matches('input:not([type="checkbox"]):not([type="radio"])')) {
    event.preventDefault();
  }
});

// Open the panel containing the first invalid field before native validation focuses it.
form.addEventListener("invalid", (event) => {
  if (reportingInvalid) {
    event.preventDefault();
    return;
  }
  reportingInvalid = true;
  queueMicrotask(() => { reportingInvalid = false; });
  const category = event.target.closest("#model-panel, #generation-panel");
  if (category) showSettings(category.id.replace("-panel", ""));
  for (let parent = event.target.parentElement; parent; parent = parent.parentElement) {
    if (parent.tagName === "DETAILS") parent.open = true;
  }
}, true);
const linkedJobId = new URLSearchParams(window.location.search).get("job") || "";
const initialJobId = /^[0-9a-f]{32}$/.test(linkedJobId) ? linkedJobId : localStorage.getItem("zhijiang-job-id");
let currentTurn = null;
let latestSuccess = null;
let busy = false;
let conversationVersion = 0;
const stageNames = {queued: "等待处理", running: "生成中", completed: "正在读取课程"};
const serviceKeyNames = ["llm_api_key", "tts_api_key", "image_api_key", "video_api_key"];
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

function showSubmitError(message) {
  const status = $("#submit-error");
  status.textContent = message;
  status.hidden = !message;
}

function updateComposer() {
  const hasFile = Boolean(fileInput.files.length);
  const revising = !hasFile && Boolean(latestSuccess || (currentTurn && busy));
  const showsConversation = Boolean(turnList.childElementCount);
  $(".workspace-main").classList.toggle("has-conversation", showsConversation);
  $("#workspace-intro").hidden = showsConversation;
  conversation.hidden = !showsConversation;
  $("#rights-row").hidden = Boolean(revising);
  $("#rights-confirmed").required = !revising;
  $("#remote-consent-row").hidden = Boolean(revising) || !$("#remote-consent").required;
  promptInput.placeholder = revising ? "想怎么调整这节课？" : "今天想学点什么？";
  $("#submit-label").textContent = revising ? "发送" : "生成一节课";
  $("#submit-button").disabled = !config?.default_narration_mode || busy ||
    (revising ? !promptInput.value.trim() : !hasFile);
  for (const retry of turnList.querySelectorAll('[data-state="failed"] .conversation-retry')) {
    retry.disabled = busy;
  }
}

function atConversationEnd() {
  return conversation.scrollHeight - conversation.clientHeight - conversation.scrollTop < 75;
}

function followConversation(shouldFollow) {
  if (shouldFollow) conversation.scrollTop = conversation.scrollHeight;
}

function updateResultActions() {
  const result = busy ? null : latestSuccess;
  resultActions.hidden = !result;
  if (result) result.view.querySelector(".conversation-preview").append(resultActions);
  window.LivelyCourses?.setJob(result?.job || null, result?.lesson);
}

function setBusy(value) {
  busy = value;
  updateComposer();
  updateResultActions();
}

function clearConversation() {
  ++conversationVersion;
  clearTimeout(pollTimer);
  currentTurn = null;
  latestSuccess = null;
  turnList.querySelectorAll("video").forEach(video => video.pause());
  turnList.replaceChildren();
  promptInput.value = "";
  fileInput.value = "";
  $("#rights-confirmed").checked = false;
  try { localStorage.removeItem("zhijiang-job-id"); } catch { /* Browser storage is unavailable. */ }
  const url = new URL(location.href);
  if (url.searchParams.has("job")) {
    url.searchParams.delete("job");
    history.replaceState(history.state, "", url);
  }
  showSubmitError("");
  syncAttachment();
  setBusy(false);
}

window.LivelyWorkspace = {clearConversation};

function appendDirectorOptions(body) {
  body.append("style_mode", "subject");
  body.append("animation_mode", "auto");
  body.append("ppt_template", "paper"); // Initial layout only; AI routing selects the actual template.
  body.append("content_checks", "warn");
  body.append("use_illustrations", "true");
  body.append("learner_enabled", String($("#learner-enabled").checked));
  for (const name of ["learner_background", "learner_goal", "learner_days", "learner_minutes_per_day"]) {
    body.append(name, form.elements[name].value);
  }
}

function appendModelOptions(body, prompt) {
  body.append("mode", "ai");
  for (const name of ["llm_provider", "llm_base_url", "llm_model", "llm_math_model", "llm_review_model", "llm_api_key"]) {
    body.append(name, form.elements[name].value);
  }
  body.append("custom_prompt", prompt);
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
  const aiVoice = selected("voice_mode") === "ai";
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
  const remote = !isLocalURL(modelURL) || cloudModel || (aiVoice && !localVoice) || remoteArt;
  $("#remote-consent-row").hidden = !remote;
  $("#remote-consent").required = remote;
  if (!remote) $("#remote-consent").checked = false;
  const provider = $("#llm-provider").value;
  const usesDefaultModel = provider === config?.llm_provider &&
    modelURL.replace(/\/$/, "") === config?.llm_base_url?.replace(/\/$/, "") &&
    modelName === config?.llm_model;
  $("#llm-base-url").required = provider !== config?.llm_provider;
  $("#llm-model").required = provider !== config?.llm_provider;
  $("#llm-api-key").required = provider === "openai" && !isLocalURL(modelURL) && !hasCredential("llm") && !(usesDefaultModel && config?.llm_ready);
  $("#llm-api-key").placeholder = hasCredential("llm") ? "已保存，留空即可使用" : provider === "ollama" ? "本机 Ollama 可留空" : "填写 API 密钥";
  for (const name of ["tts_base_url", "tts_model", "tts_voice"]) {
    form.elements[name].required = aiVoice;
  }
  const usesDefaultVoice = ttsURL === config?.tts_base_url &&
    $("#tts-model").value === config?.tts_model && $("#tts-voice").value === config?.tts_voice;
  $("#tts-api-key").required = aiVoice && !localVoice && !hasCredential("tts") && !(usesDefaultVoice && config?.ai_tts_ready);
  $("#tts-api-key").placeholder = hasCredential("tts") ? "已保存，留空即可使用" : localVoice ? "本机服务可留空" : "填写 API 密钥";
  $("#tts-ready-label").textContent = $("#tts-voice").value;
  updateSpeechPreview();
  updateComposer();
}

function syncAttachment() {
  const file = fileInput.files[0];
  $("#file-label").textContent = file?.name || "添加 PDF";
  $("#remove-file").hidden = !file;
  updateComposer();
}

function acceptFile(file) {
  if (!file) return;
  const isPDF = /\.pdf$/i.test(file.name) || file.type === "application/pdf";
  const tooLarge = config?.max_pdf_bytes && file.size > config.max_pdf_bytes;
  if (!isPDF || tooLarge) {
    showSubmitError(isPDF ? `PDF 超过 ${Math.round(config.max_pdf_bytes / 1024 / 1024)} MB` : "请选择 PDF 文件");
    return false;
  }
  $("#rights-confirmed").checked = false;
  showSubmitError("");
  return true;
}

dropzone.addEventListener("keydown", (event) => {
  if (event.target === dropzone && (event.key === "Enter" || event.key === " ")) {
    event.preventDefault();
    fileInput.click();
  }
});
composer.addEventListener("dragover", (event) => {
  if (!event.dataTransfer.types.includes("Files")) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = "copy";
  composer.classList.add("dragover");
});
composer.addEventListener("dragleave", event => {
  if (!composer.contains(event.relatedTarget)) composer.classList.remove("dragover");
});
composer.addEventListener("drop", (event) => {
  if (!event.dataTransfer.types.includes("Files")) return;
  event.preventDefault();
  composer.classList.remove("dragover");
  const file = event.dataTransfer.files[0];
  if (!acceptFile(file)) return;
  const transfer = new DataTransfer();
  transfer.items.add(file);
  fileInput.files = transfer.files;
  syncAttachment();
});
fileInput.addEventListener("change", () => {
  if (!acceptFile(fileInput.files[0])) fileInput.value = "";
  syncAttachment();
});
$("#remove-file").addEventListener("click", () => {
  fileInput.value = "";
  showSubmitError("");
  syncAttachment();
  if (latestSuccess) {
    shownSettingsJobId = null;
    restoreSettings(latestSuccess.job);
  }
  dropzone.focus();
});
promptInput.addEventListener("input", updateComposer);
promptInput.addEventListener("keydown", event => {
  if (event.key === "Enter" && (event.metaKey || event.ctrlKey) && !event.isComposing) {
    event.preventDefault();
    if (!$("#submit-button").disabled) form.requestSubmit();
  }
});
form.querySelectorAll('input[name="voice_mode"]').forEach(input => input.addEventListener("change", () => {
  voiceSelectionEdited = true;
  updateConsent();
}));
for (const kind of ["image","video"]) {
  $(`#${kind}-protocol`).addEventListener("change", () => {
    if (kind === "image" && $("#image-protocol").value === "jimeng") {
      $("#image-base-url").value = "https://openapi.cv.volces.com";
      $("#image-model").value = "jimeng_t2i_v30";
      $("#image-api-key").value = "";
    }
    $("#remote-consent").checked = false; updateConsent();
  });
  $(`#${kind}-base-url`).addEventListener("input", () => {
    $(`#${kind}-api-key`).value = "";$("#remote-consent").checked = false;updateConsent();
  });
  $(`#${kind}-model`).addEventListener("input", () => {$("#remote-consent").checked=false;});
}
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
    for (const turn of [currentTurn, latestSuccess]) {
      if (turn?.keys && sameService(turn.job, kind)) delete turn.keys[`${kind}_api_key`];
    }
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

function restoreSettings(job) {
  if (shownSettingsJobId !== job.id) {
    shownSettingsJobId = job.id;
    for (const name of serviceKeyNames) {
      const kind = name.slice(0, -"_api_key".length);
      if (!sameService(job, kind)) {
        clearTimeout(credentialTimers[kind]);
        form.elements[name].value = "";
      }
    }
    const voiceChoice = form.querySelector(`input[name="voice_mode"][value="${job.voice_mode}"]`);
    if (voiceChoice && (!voiceSelectionEdited || latestSuccess?.job.id === job.id)) voiceChoice.checked = true;
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
    }
    if (job.voice_mode === "ai" && job.voice_settings) {
      $("#tts-base-url").value = job.voice_settings.base_url || "";
      $("#tts-model").value = job.voice_settings.model || "";
      $("#tts-voice").value = job.voice_settings.voice || "";
    }
    updateConsent();
    rememberServiceFields();
    refreshCredential("llm"); refreshCredential("tts");
    $("#remote-consent").checked = job.remote_consent;
  }
}

function makeSegment(segment, index) {
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
  if (segment.source_spans?.length || !scene?.beats?.length) {
    body.append(narration);
  } else {
    const steps = document.createElement("ol");
    for (const beat of scene.beats) {
      const item = document.createElement("li");
      item.textContent = beat.narration;
      steps.append(item);
    }
    body.append(steps);
  }
  body.append(source);
  details.append(summary, body);
  return details;
}

function addTurn(prompt, filename) {
  const view = $("#conversation-turn-template").content.firstElementChild.cloneNode(true);
  const message = view.querySelector(".user-message");
  message.textContent = prompt;
  message.hidden = !prompt;
  const attachment = view.querySelector(".user-attachment");
  attachment.querySelector("span").textContent = filename || "";
  attachment.hidden = !filename;
  const turn = {view, prompt, job: null, lesson: null, keys: null};
  view.querySelector(".conversation-retry").addEventListener("click", () => retryTurn(turn));
  turnList.append(view);
  return turn;
}

function showStage(turn, message, state, retry = false) {
  const status = turn.view.querySelector(".conversation-status");
  status.hidden = false;
  status.dataset.state = state;
  const stage = status.querySelector(".conversation-stage");
  if (stage.textContent !== message) stage.textContent = message;
  const button = status.querySelector(".conversation-retry");
  button.hidden = !retry;
  button.disabled = busy && state === "failed";
  button.textContent = state === "failed" ? "重新生成" : "重试读取";
}

function displayStage(job) {
  return (stageNames[job.stage] || job.stage || "等待处理")
    .replace(/（\s*\d+(?:\.\d+)?\s*秒[，,]\s*\d+\s*字）/g, "");
}

async function showLesson(turn) {
  const {id} = turn.job;
  const lesson = await getJSON(`/api/jobs/${id}/lesson`);
  if (!turn.view.isConnected) return;
  turn.lesson = lesson;
  const preview = turn.view.querySelector(".conversation-preview");
  preview.querySelector("h3").textContent = lesson.title;
  const video = preview.querySelector("video");
  video.setAttribute("aria-label", `${lesson.title} · 教学视频`);
  if (turn === currentTurn) video.preload = "metadata";
  video.src = `/api/jobs/${id}/video`;
  preview.querySelector(".segments").replaceChildren(...lesson.segments.map(makeSegment));
  turn.view.querySelector(".conversation-status").hidden = true;
  preview.hidden = false;
  if (!latestSuccess || (latestSuccess.view.compareDocumentPosition(turn.view) & Node.DOCUMENT_POSITION_FOLLOWING)) {
    latestSuccess = turn;
  }
}

function revealReply(turn) {
  const replyTop = turn.view.querySelector(".conversation-reply").getBoundingClientRect().top;
  conversation.scrollTop += replyTop - conversation.getBoundingClientRect().top - 12;
}

function persistLatestJob(job) {
  localStorage.setItem("zhijiang-job-id", job.id);
  // From a shared ?job= entry, keep its reload link on the newest turn.
  if (linkedJobId) {
    const url = new URL(location.href);
    url.searchParams.set("job", job.id);
    history.replaceState(history.state, "", url);
  }
}

function showJobStage(turn, job) {
  turn.job = job;
  if (job.status === "failed") {
    showStage(turn, job.error || "生成失败，请重试", "failed", turn === currentTurn);
  } else {
    showStage(turn, displayStage(job), "running");
  }
}

async function finishTurn(turn) {
  if (turn.job.status === "completed") {
    try {
      await showLesson(turn);
    } catch (error) {
      showStage(turn, error.message, "unavailable", true);
    }
  }
  if (currentTurn === turn) setBusy(false);
}

async function pollJob(turn) {
  try {
    const job = await getJSON(`/api/jobs/${turn.job.id}`);
    if (currentTurn !== turn) return;
    const follow = atConversationEnd();
    showJobStage(turn, job);
    if (job.status === "completed" || job.status === "failed") await finishTurn(turn);
    else pollTimer = setTimeout(() => pollJob(turn), 1400);
    if (currentTurn !== turn) return;
    if (follow && turn.lesson && job.status === "completed") revealReply(turn);
    else followConversation(follow);
  } catch (error) {
    if (currentTurn !== turn) return;
    // A brief connection failure does not end the server task.
    showStage(turn, error.message, "unavailable", true);
  }
}

function startPolling(turn) {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(() => pollJob(turn), 0);
}

async function restoreConversation(jobId) {
  const version = conversationVersion;
  const jobs = [];
  const seen = new Set();
  while (jobId && !seen.has(jobId)) {
    seen.add(jobId);
    try {
      const job = await getJSON(`/api/jobs/${jobId}`);
      if (version !== conversationVersion) return;
      jobs.unshift(job);
      jobId = job.parent_job_id;
    } catch (error) {
      if (version !== conversationVersion) return;
      if (!error.message.includes("任务不存在")) showSubmitError(error.message);
      else if (!jobs.length) localStorage.removeItem("zhijiang-job-id");
      break;
    }
  }
  if (!jobs.length) return;
  restoreSettings(jobs.at(-1));
  const turns = jobs.map(job => {
    const turn = addTurn(job.model_settings?.prompt || "", job.parent_job_id ? "" : job.filename);
    turn.job = job;
    return turn;
  });
  currentTurn = turns.at(-1);
  updateComposer();
  for (const turn of turns) {
    showJobStage(turn, turn.job);
    if (turn.job.status === "completed") {
      try { await showLesson(turn); }
      catch (error) { showStage(turn, error.message, "unavailable", true); }
      if (version !== conversationVersion) return;
    }
  }
  if (currentTurn.lesson) revealReply(currentTurn);
  else followConversation(true);
  if (["queued", "running"].includes(currentTurn.job.status)) startPolling(currentTurn);
  else setBusy(false);
}

function appendSourceOptions(body) {
  body.append("narration_mode", "source_verbatim");
  body.append("art_max_assets", $("#art-max-assets").value);
  for (const kind of ["image","video"]) for (const field of ["protocol","base_url","model","api_key"]) {
    body.append(`${kind}_${field}`, form.elements[`${kind}_${field}`].value);
  }
}

function initialOptions(prompt) {
  const body = new FormData();
  appendModelOptions(body, prompt);
  body.append("voice_mode", selected("voice_mode"));
  appendDirectorOptions(body);
  appendSourceOptions(body);
  body.append("remote_consent", String($("#remote-consent").checked));
  if (selected("voice_mode") === "ai") {
    for (const name of ["tts_base_url", "tts_model", "tts_voice", "tts_api_key"]) {
      body.append(name, form.elements[name].value);
    }
  }
  return body;
}

function sameService(job, kind) {
  const saved = kind === "llm" ? job.model_settings : kind === "tts" ? job.voice_settings : job.model_settings?.art?.[kind];
  const active = kind === "llm" || kind === "tts" ? credentialProfile(kind) : {
    provider: form.elements[`${kind}_protocol`].value, base_url: form.elements[`${kind}_base_url`].value,
  };
  if (!active || !saved?.base_url) return false;
  return (kind === "tts" || active.provider === (saved.provider || saved.protocol)) &&
    active.base_url.trim().replace(/\/+$/, "") === saved.base_url.replace(/\/+$/, "");
}

function revisionOptions(prompt, turn) {
  const body = new FormData();
  if (prompt !== null) body.append("custom_prompt", prompt);
  for (const name of serviceKeyNames) {
    const kind = name.slice(0, -"_api_key".length);
    const key = (sameService(turn.job, kind) && form.elements[name].value) || turn.keys?.[name];
    if (key) body.append(name, key);
  }
  return body;
}

function submittedKeys(body) {
  return Object.fromEntries(serviceKeyNames.map(name => [name, body.get(name) || ""]));
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy || $("#submit-button").disabled) return;
  showSubmitError("");
  const file = fileInput.files[0];
  const parent = file ? null : latestSuccess;
  const prompt = promptInput.value.trim();
  if (!parent && !form.reportValidity()) return;
  if (parent && !prompt) return;
  const earlierTurn = currentTurn;
  const follow = !earlierTurn || atConversationEnd();
  const turn = addTurn(prompt, file?.name);
  currentTurn = turn;
  setBusy(true);
  showStage(turn, "正在提交", "running");
  promptInput.value = "";
  followConversation(follow);
  try {
    rememberServiceFields();
    const body = parent ? revisionOptions(prompt, parent) : initialOptions(prompt);
    if (!parent) {
      body.append("file", file);
      body.append("rights_confirmed", String($("#rights-confirmed").checked));
    }
    await Promise.all([saveCredential("llm"), saveCredential("tts")]);
    if (currentTurn !== turn) return;
    const job = await getJSON(parent ? `/api/jobs/${parent.job.id}/revise` : "/api/jobs", {method: "POST", body});
    if (currentTurn !== turn) return;
    turn.keys = submittedKeys(body);
    turn.job = job;
    clearSubmittedKeys();
    if (file && fileInput.files[0] === file) fileInput.value = "";
    if (!parent) {
      latestSuccess = null;
      turnList.replaceChildren(turn.view);
      followConversation(true);
    }
    if (earlierTurn) earlierTurn.view.querySelector(".conversation-retry").hidden = true;
    syncAttachment();
    persistLatestJob(job);
    showJobStage(turn, job);
    startPolling(turn);
  } catch (error) {
    if (currentTurn !== turn) return;
    turn.view.remove();
    currentTurn = earlierTurn;
    if (!promptInput.value) promptInput.value = prompt;
    showSubmitError(error.message);
    setBusy(false);
  }
});

async function retryTurn(turn) {
  if (turn !== currentTurn) {
    try {
      await showLesson(turn);
    } catch (error) {
      showStage(turn, error.message, "unavailable", true);
    } finally {
      updateResultActions();
    }
    return;
  }
  if (turn.job.status !== "failed") {
    showStage(turn, "正在读取课程", "running");
    setBusy(true);
    startPolling(turn);
    return;
  }
  if (busy) return;
  const isRevision = Boolean(turn.job.parent_job_id);
  if (!isRevision) {
    // Validate settings, without requiring an upload or replacing a pending draft.
    for (const field of settingsDialog.querySelectorAll("input, select, textarea")) {
      if (!field.reportValidity()) return;
    }
    if (!$("#remote-consent").reportValidity()) return;
  }
  setBusy(true);
  try {
    const body = isRevision ? revisionOptions(null, turn) : initialOptions(turn.prompt);
    if (!isRevision) body.append("replace_settings", "true");
    await Promise.all([saveCredential("llm"), saveCredential("tts")]);
    if (currentTurn !== turn) return;
    const job = await getJSON(`/api/jobs/${turn.job.id}/retry`, {method: "POST", body});
    if (currentTurn !== turn) return;
    turn.keys = submittedKeys(body);
    clearSubmittedKeys();
    showJobStage(turn, job);
    startPolling(turn);
  } catch (error) {
    if (currentTurn !== turn) return;
    showStage(turn, error.message, "failed", true);
    setBusy(false);
  }
}

(async function initialize() {
  try {
    config = await getJSON("/api/config");
    if (!config.default_narration_mode) {
      showSubmitError("请重启课程服务后再生成。");
    }
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
    if (!config.ai_tts_ready) $("#tts-settings").open = true;
    updateConsent();
    refreshCredential("llm"); refreshCredential("tts");
    if (initialJobId) {
      setBusy(true);
      await restoreConversation(initialJobId);
      if (!currentTurn) setBusy(false);
    }
    updateComposer();
  } catch (error) {
    showSubmitError("配置读取失败，请刷新页面后重试。");
    $("#tts-ready-label").textContent = "配置读取失败";
  }
})();
