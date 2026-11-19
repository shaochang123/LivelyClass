(() => {
  "use strict";

  const find = selector => document.querySelector(selector);
  const personalList = find("#my-course-list");
  const communityList = find("#community-course-list");
  const saveButton = find("#save-course");
  const shareButton = find("#share-course");
  const signIn = find("#login-button");
  const signUp = find("#register-button");
  const signOut = find("#logout-button");
  const accountName = find("#course-account");
  if (!personalList || !communityList || !saveButton || !shareButton) return;

  function element(tag, className = "", text = "") {
    const node = document.createElement(tag);
    node.className = className;
    node.textContent = text;
    return node;
  }

  function button(label, className = "") {
    const node = element("button", className, label);
    node.type = "button";
    return node;
  }

  async function request(path, method = "GET", body) {
    const response = await fetch(path, {
      method,
      ...(body === undefined ? {} : {
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(body),
      }),
    });
    const json = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(typeof json.detail === "string" ? json.detail : "暂时无法完成操作，请重试。");
      error.status = response.status;
      throw error;
    }
    return json;
  }

  const storageKey = id => `livelyclass-study:${id}`;

  function readStudy(id) {
    const empty = {position: 0, duration: 0, highlights: [], checked: {}};
    try {
      const record = JSON.parse(localStorage.getItem(storageKey(id)));
      if (!record) return empty;
      return {
        position: Math.max(0, Number(record.position) || 0),
        duration: Math.max(0, Number(record.duration) || 0),
        highlights: Array.isArray(record.highlights) ? record.highlights.filter(range =>
          Array.isArray(range) && Number.isSafeInteger(range[0]) && range[0] >= 0
          && Number.isSafeInteger(range[1]) && range[1] > range[0]) : [],
        checked: record.checked && typeof record.checked === "object" ? record.checked : {},
      };
    } catch {
      return empty;
    }
  }

  let storageError = false;
  function writeStudy(id, study) {
    try {
      localStorage.setItem(storageKey(id), JSON.stringify(study));
    } catch {
      if (!storageError) showNotice("浏览器暂时无法保存学习记录。");
      storageError = true;
    }
  }

  const notice = element("div", "course-toast");
  notice.setAttribute("role", "status");
  notice.hidden = true;
  document.body.append(notice);
  let noticeTimer;
  function showNotice(message, actionLabel, action) {
    clearTimeout(noticeTimer);
    notice.replaceChildren(element("span", "", message));
    if (action) {
      const link = button(actionLabel, "course-toast-link");
      link.addEventListener("click", () => {
        notice.hidden = true;
        action();
      });
      notice.append(link);
    }
    notice.hidden = false;
    noticeTimer = setTimeout(() => { notice.hidden = true; }, action ? 7500 : 4300);
  }

  const listState = {
    "my-courses": {root: personalList, path: "/api/courses/mine", generation: 0, courses: null},
    community: {root: communityList, path: "/api/community/courses", generation: 0, courses: null},
  };

  function goTo(screen) {
    window.LivelyScreens?.go(screen);
  }

  function coverArt(imageUrl, title = "") {
    const cover = element("span", "course-cover");
    cover.setAttribute("aria-hidden", "true");
    const art = element("span", "course-cover-art");
    art.append(element("i", "course-sheet course-sheet-back"),
      element("i", "course-sheet course-sheet-front"), element("i", "course-cover-orbit"));
    cover.append(art);
    if (imageUrl) {
      const image = element("img");
      image.alt = "";
      image.loading = "lazy";
      image.src = imageUrl;
      image.addEventListener("error", () => image.remove(), {once: true});
      cover.append(image);
    }
    if (title) cover.dataset.tone = String(title.codePointAt(0) % 3);
    return cover;
  }

  function progressPercent(study) {
    if (!Number.isFinite(study.duration) || study.duration <= 0) return 0;
    return Math.max(0, Math.min(100, Math.round(study.position / study.duration * 100)));
  }

  function progressBar(percent, label) {
    const meter = element("progress", "course-progress");
    meter.max = 100;
    meter.value = percent;
    meter.setAttribute("aria-label", label);
    return meter;
  }

  function courseCard(course, shared) {
    const card = button("", "saved-course-card");
    card.dataset.course = course.id;
    const copy = element("span", "saved-course-copy");
    const title = element("span", "saved-course-name", course.title);
    const meta = element("span", "saved-course-meta");
    if (shared) {
      let publishingDate = "";
      const published = new Date(course.published_at);
      if (course.published_at && Number.isFinite(published.getTime())) {
        publishingDate = new Intl.DateTimeFormat("zh-CN", {dateStyle: "medium"}).format(published);
      }
      const author = typeof course.author === "string" ? course.author : course.author?.username;
      meta.textContent = [author, publishingDate].filter(Boolean).join(" · ");
      copy.append(title, meta);
    } else {
      const percent = progressPercent(readStudy(course.id));
      meta.textContent = percent ? `已观看 ${percent}%` : "待学习";
      copy.append(title, meta, progressBar(percent, "视频观看进度"));
    }
    card.append(coverArt(course.assets?.cover, course.title), copy);
    card.addEventListener("click", () => showCourse(course, shared));
    return card;
  }

  function renderList(screen, state) {
    const shared = screen === "community";
    if (state.courses?.length) {
      const grid = element("div", "saved-courses-grid");
      for (const course of state.courses) grid.append(courseCard(course, shared));
      state.root.replaceChildren(grid);
      return;
    }
    const empty = element("div", "saved-courses-empty");
    empty.append(coverArt(null), element("h3", "", shared ? "等待第一堂分享课程" : "在这里继续你的课程"));
    empty.append(element("p", "", shared ? "在工作台生成课程后，就可以把课程分享给其他学习者。"
      : "在工作台生成课程并保存，即可在这里打开讲稿、计划和观看记录。"));
    const open = button("前往工作台", "course-primary");
    open.addEventListener("click", () => goTo("workspace"));
    empty.append(open);
    state.root.replaceChildren(empty);
  }

  async function refresh(screen) {
    const state = listState[screen];
    if (!state) return;
    const generation = ++state.generation;
    if (state.courses === null) state.root.setAttribute("aria-busy", "true");
    try {
      const {courses} = await request(state.path);
      if (generation !== state.generation) return;
      if (state.courses !== null && JSON.stringify(courses) === JSON.stringify(state.courses)) return;
      state.courses = courses;
      renderList(screen, state);
    } catch (error) {
      if (generation !== state.generation || state.courses !== null) return;
      const failure = element("div", "saved-courses-empty");
      const retry = button("重新加载", "course-primary");
      retry.addEventListener("click", () => refresh(screen));
      failure.append(element("p", "", error.message), retry);
      state.root.replaceChildren(failure);
    } finally {
      if (generation === state.generation) state.root.removeAttribute("aria-busy");
    }
  }

  const courseDialog = element("dialog", "lesson-dialog");
  courseDialog.setAttribute("aria-labelledby", "saved-lesson-title");
  const dialogHead = element("header", "lesson-dialog-header");
  const closeCourse = button("← 返回课程列表", "course-quiet-button");
  const dialogContent = element("div", "lesson-dialog-content");
  const courseTitle = element("h2", "saved-lesson-title");
  courseTitle.id = "saved-lesson-title";
  dialogHead.append(closeCourse);
  dialogContent.setAttribute("data-scroll-lock", "");
  courseDialog.append(dialogHead, dialogContent);
  document.body.append(courseDialog);
  closeCourse.addEventListener("click", () => courseDialog.close());

  let courseRequest = 0;
  let activeLesson = null;
  function leaveCourse() {
    ++courseRequest;
    if (activeLesson) {
      activeLesson.flush();
      activeLesson.stop();
      const {id} = activeLesson.course;
      const matching = personalList.querySelector(`[data-course="${CSS.escape(id)}"]`);
      if (matching) {
        const percent = progressPercent(activeLesson.study);
        matching.querySelector("progress").value = percent;
        matching.querySelector(".saved-course-meta").textContent = percent ? `已观看 ${percent}%` : "待学习";
      }
      activeLesson = null;
    }
    dialogContent.replaceChildren();
  }

  courseDialog.addEventListener("close", () => {
    if (!courseDialog.open) leaveCourse();
  });
  window.addEventListener("pagehide", () => activeLesson?.flush());

  async function showCourse(summary, shared) {
    leaveCourse();
    const generation = ++courseRequest;
    courseTitle.textContent = summary.title;
    const busy = element("p", "lesson-loading", "正在打开课程…");
    busy.setAttribute("role", "status");
    dialogContent.replaceChildren(courseTitle, busy);
    dialogContent.scrollTop = 0;
    if (!courseDialog.open) courseDialog.showModal();
    closeCourse.focus({preventScroll: true});
    const prefix = shared ? "/api/community/courses" : "/api/courses";
    try {
      const course = await request(`${prefix}/${encodeURIComponent(summary.id)}`);
      if (!courseDialog.open || generation !== courseRequest) return;
      courseTitle.textContent = course.title;
      activeLesson = renderLesson(course);
    } catch (error) {
      if (!courseDialog.open || generation !== courseRequest) return;
      const retry = button("重试", "course-primary");
      retry.addEventListener("click", () => showCourse(summary, shared));
      busy.replaceChildren(element("span", "", error.message), retry);
    }
  }

  function timeLabel(seconds) {
    const whole = Math.floor(Math.max(0, seconds));
    return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
  }

  function downloadLink(label, url, video = false) {
    const link = element("a", "course-download", label);
    if (!url) {
      link.setAttribute("aria-disabled", "true");
      return link;
    }
    const target = new URL(url, window.location.origin);
    if (video) target.searchParams.set("download", "true");
    link.href = target.href;
    link.download = "";
    return link;
  }

  function renderStudyPlan(plan, study, persist) {
    const section = element("section", "lesson-plan");
    const heading = element("div", "lesson-plan-header");
    heading.append(element("h3", "", "学习计划"));
    const playback = element("div", "lesson-playback");
    const playbackLabel = element("span", "", "视频进度");
    const meter = progressBar(progressPercent(study), "视频观看进度");
    const stats = element("strong", "", `${meter.value}%`);
    playback.append(playbackLabel, meter, stats);
    heading.append(playback);
    section.append(heading);
    if (plan?.overview) section.append(element("p", "lesson-plan-overview", plan.overview));

    const days = element("div", "lesson-days");
    function task(label, key) {
      const line = element("label", "lesson-task");
      const input = element("input");
      input.type = "checkbox";
      input.checked = Boolean(study.checked[key]);
      input.addEventListener("change", () => {
        study.checked[key] = input.checked;
        persist();
      });
      line.append(input, element("span", "", label));
      return line;
    }

    const sessions = plan?.sessions || [];
    for (const session of sessions) {
      const day = element("article", "lesson-day");
      const top = element("div", "lesson-day-heading");
      top.append(element("span", "lesson-day-label", `第 ${session.day} 天`));
      if (session.minutes) top.append(element("span", "", `${session.minutes} 分钟`));
      day.append(top, element("h4", "", session.title));
      (session.activities || []).forEach((activity, index) => {
        day.append(task(activity, `day-${session.day}-${index}`));
      });
      if (session.self_check) day.append(task(`自测 · ${session.self_check}`, `day-${session.day}-check`));
      days.append(day);
    }
    if (!sessions.length) {
      const basic = element("article", "lesson-day");
      basic.append(element("h4", "", "课程待办"), task("观看课程视频", "watch"), task("回顾课程讲稿", "read"));
      days.append(basic);
    }
    section.append(days);
    return {section, update: () => {
      meter.value = progressPercent(study);
      stats.textContent = `${meter.value}%`;
    }};
  }

  function mergeRanges(ranges) {
    const sorted = ranges.slice().sort((a,b) => a[0] - b[0] || a[1] - b[1]);
    const merged = [];
    for (const [start, end] of sorted) {
      const previous = merged.at(-1);
      if (previous && start <= previous[1]) previous[1] = Math.max(previous[1], end);
      else merged.push([start, end]);
    }
    return merged;
  }

  function renderTranscript(course, study, video, persist) {
    const panel = element("section", "lesson-transcript");
    const header = element("header", "lesson-transcript-header");
    const tools = element("div", "lesson-transcript-tools");
    const highlight = button("高亮", "course-quiet-button");
    const follow = button("跟随播放", "course-quiet-button");
    highlight.disabled = true;
    follow.setAttribute("aria-pressed", "true");
    header.append(element("h3", "", "课程讲稿"), tools);
    tools.append(highlight, follow);

    const scroll = element("div", "lesson-transcript-scroll");
    scroll.setAttribute("data-scroll-lock", "");
    scroll.tabIndex = 0;
    scroll.setAttribute("aria-label", "课程讲稿");
    const lines = element("div", "lesson-transcript-lines");
    scroll.append(lines);
    panel.append(header, scroll);

    let offset = 0;
    const cues = (course.transcript || []).filter(cue => cue.text).map(cue => {
      const {text, start_seconds: start, end_seconds: end} = cue;
      const row = element("div", "lesson-cue");
      const paragraph = element("p", "lesson-cue-text");
      row.dataset.offset = String(offset);
      const anchor = offset;
      offset += text.length;
      row.append(paragraph);
      if (Number.isFinite(start)) {
        const timestamp = button(timeLabel(start), "lesson-cue-time");
        timestamp.setAttribute("aria-label", `跳到视频 ${timeLabel(start)}`);
        timestamp.addEventListener("click", () => {
          if (video.readyState >= HTMLMediaElement.HAVE_METADATA) video.currentTime = start;
        });
        row.classList.add("is-timed");
        row.append(timestamp);
      }
      lines.append(row);
      return {text, start, end, offset: anchor, row, paragraph};
    });
    if (!cues.length) lines.append(element("p", "lesson-empty-transcript", "暂无课程讲稿。"));
    const timed = cues.filter(cue => Number.isFinite(cue.start) && Number.isFinite(cue.end))
      .sort((a,b) => a.start - b.start);
    follow.hidden = !timed.length;
    highlight.hidden = !cues.length;
    study.highlights = mergeRanges(study.highlights.filter(([start, end]) => start < end && end <= offset));

    function renderMarks() {
      let firstRange = 0;
      for (const {paragraph, text, offset} of cues) {
        const end = offset + text.length;
        while (firstRange < study.highlights.length && study.highlights[firstRange][1] <= offset) ++firstRange;
        const fragment = document.createDocumentFragment();
        let cursor = 0;
        for (let i = firstRange; i < study.highlights.length; ++i) {
          const [start, stop] = study.highlights[i];
          if (start >= end) break;
          const lower = Math.max(0, start - offset);
          const upper = Math.min(text.length, stop - offset);
          fragment.append(text.slice(cursor, lower), element("mark", "", text.slice(lower, upper)));
          cursor = upper;
        }
        fragment.append(text.slice(cursor));
        paragraph.replaceChildren(fragment);
      }
    }
    renderMarks();

    function transcriptOffset(node, position) {
      const parent = node.nodeType === Node.TEXT_NODE ? node.parentElement : node;
      const row = parent.closest?.(".lesson-cue");
      const text = row?.querySelector(".lesson-cue-text");
      if (!text?.contains(node)) return null;
      const before = document.createRange();
      before.selectNodeContents(text);
      before.setEnd(node, position);
      return Number(row.dataset.offset) + before.toString().length;
    }

    function selectionRange() {
      const selection = document.getSelection();
      if (!selection || selection.isCollapsed || !selection.rangeCount) return null;
      const range = selection.getRangeAt(0);
      if (!lines.contains(range.startContainer) || !lines.contains(range.endContainer)) return null;
      const start = transcriptOffset(range.startContainer, range.startOffset);
      const end = transcriptOffset(range.endContainer, range.endOffset);
      return start !== null && end !== null && end > start ? [start, end] : null;
    }

    let picked = null;
    const hasHighlight = ([from,to]) => study.highlights.some(([start, end]) => start <= from && end >= to);
    function updateSelection() {
      picked = selectionRange();
      highlight.textContent = picked && hasHighlight(picked) ? "取消高亮" : "高亮";
      highlight.disabled = !picked;
    }

    // Preserve the selected text while the toolbar button receives a pointer action.
    highlight.addEventListener("pointerdown", event => event.preventDefault());
    highlight.addEventListener("click", () => {
      if (!picked) return;
      const [start, end] = picked;
      if (hasHighlight(picked)) {
        study.highlights = study.highlights.flatMap(([from, to]) => {
          if (to <= start || from >= end) return [[from,to]];
          const result = [];
          if (from < start) result.push([from,start]);
          if (to > end) result.push([end,to]);
          return result;
        });
      } else {
        study.highlights = mergeRanges([...study.highlights, [start,end]]);
      }
      document.getSelection()?.removeAllRanges();
      updateSelection();
      renderMarks();
      persist();
    });
    document.addEventListener("selectionchange", updateSelection);

    let currentCue = null;
    let following = true;
    let pauseFollowUntil = 0;
    for (const event of ["wheel", "touchstart"]) {
      scroll.addEventListener(event, () => { pauseFollowUntil = performance.now() + 3500; }, {passive: true});
    }

    function scrollToCue() {
      if (!currentCue || !following || picked || performance.now() < pauseFollowUntil) return;
      const {top, bottom} = currentCue.row.getBoundingClientRect();
      const bounds = scroll.getBoundingClientRect();
      if (top >= bounds.top && bottom <= bounds.bottom) return;
      const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
      scroll.scrollTo({top: scroll.scrollTop + top - bounds.top - scroll.clientHeight * .22,
        behavior: reduce ? "instant" : "smooth"});
    }

    follow.addEventListener("click", () => {
      following = !following;
      pauseFollowUntil = 0;
      follow.setAttribute("aria-pressed", String(following));
      scrollToCue();
    });

    function update(time) {
      // Browsers may round a seek position down when converting to the media time scale.
      const tolerance = .002;
      let left = 0;
      let right = timed.length;
      while (left < right) {
        const pivot = (left + right) >>> 1;
        if (timed[pivot].start <= time + tolerance) left = pivot + 1;
        else right = pivot;
      }
      const nearest = timed[left - 1];
      const next = nearest && time <= nearest.end + tolerance ? nearest : null;
      if (currentCue === next) return;
      currentCue?.row.classList.remove("is-current");
      currentCue?.row.removeAttribute("aria-current");
      currentCue = next;
      currentCue?.row.classList.add("is-current");
      currentCue?.row.setAttribute("aria-current","true");
      scrollToCue();
    }
    return {panel, update, stop: () => document.removeEventListener("selectionchange", updateSelection)};
  }

  function renderLesson(course) {
    const study = readStudy(course.id);
    const persist = () => writeStudy(course.id, study);
    const layout = element("div", "lesson-detail-grid");
    const player = element("section", "lesson-player");
    const video = element("video", "saved-lesson-video");
    video.controls = true;
    video.playsInline = true;
    video.preload = "metadata";
    video.setAttribute("aria-label", "课程视频");
    if (course.assets?.cover) video.poster = course.assets.cover;
    if (course.assets?.video) video.src = course.assets.video;
    else video.hidden = true;
    const downloads = element("div", "lesson-downloads");
    downloads.append(downloadLink("保存 PPTX", course.assets?.presentation), downloadLink("保存视频", course.assets?.video, true));
    player.append(video, downloads);
    if (!course.assets?.video) player.prepend(coverArt(course.assets?.cover, course.title));
    const transcript = renderTranscript(course, study, video, persist);
    const plan = renderStudyPlan(course.learning_plan, study, persist);
    layout.append(player, transcript.panel);
    dialogContent.replaceChildren(courseTitle);
    if (course.objective) dialogContent.append(element("p", "saved-lesson-objective", course.objective));
    dialogContent.append(layout, plan.section);

    let ready = false;
    let lastSaved = 0;
    function sync(force = false) {
      if (!ready) return;
      study.position = video.currentTime;
      if (Number.isFinite(video.duration)) study.duration = video.duration;
      transcript.update(study.position);
      plan.update();
      if (force || performance.now() - lastSaved > 1250) {
        persist();
        lastSaved = performance.now();
      }
    }

    video.addEventListener("loadedmetadata", () => {
      if (!ready && study.position > 0 && Number.isFinite(video.duration)) {
        video.currentTime = Math.min(study.position, video.duration);
      }
      ready = true;
      sync(true);
    });
    video.addEventListener("timeupdate", () => sync());
    for (const event of ["seeking", "seeked", "play", "pause", "ended"]) {
      video.addEventListener(event, () => sync(true));
    }
    video.addEventListener("error", () => {
      player.append(element("p", "lesson-video-error", "视频暂时无法播放，可尝试重新打开课程。"));
    }, {once: true});

    return {course, study, flush: () => sync(true), stop: () => {
      video.pause();
      transcript.stop();
    }};
  }

  let job = null;
  let lessonTitle = "";
  let account = null;
  let saving = false;
  function setJob(next, lesson) {
    if (next?.id !== job?.id) lessonTitle = "";
    job = next;
    if (lesson?.title) lessonTitle = lesson.title;
    saveButton.hidden = shareButton.hidden = job?.status !== "completed";
  }

  saveButton.addEventListener("click", async () => {
    if (job?.status !== "completed" || saving) return;
    saving = true;
    saveButton.disabled = true;
    const id = job.id;
    try {
      await request(`/api/jobs/${id}/save`, "POST");
      await refresh("my-courses");
      showNotice("已保存到我的课程", "查看", () => goTo("my-courses"));
    } catch (error) {
      showNotice(error.message);
    } finally {
      saving = false;
      saveButton.disabled = false;
    }
  });

  const auth = element("dialog", "course-auth-dialog");
  auth.setAttribute("aria-labelledby", "course-auth-title");
  const authPane = element("div", "course-auth-pane");
  const authTitle = element("h2", "course-auth-title");
  authTitle.id = "course-auth-title";
  const closeAuth = button("×", "course-dialog-close");
  closeAuth.setAttribute("aria-label", "关闭");
  const authBody = element("div", "course-auth-body");
  authPane.append(closeAuth, authTitle, authBody);
  auth.append(authPane);
  document.body.append(auth);
  closeAuth.addEventListener("click", () => auth.close());
  let authRender = 0;
  let sessionVersion = 0;

  function setAccount(user) {
    account = user;
    if (signIn) signIn.hidden = Boolean(user);
    if (signUp) signUp.hidden = Boolean(user);
    if (signOut) signOut.hidden = !user;
    if (accountName) {
      accountName.textContent = user?.username || "";
      accountName.hidden = !user;
    }
  }

  const bootSession = sessionVersion;
  request("/api/auth/session").then(({user}) => {
    if (bootSession === sessionVersion) setAccount(user);
  }).catch(() => {});

  function showAuthPanel(title) {
    const generation = ++authRender;
    authTitle.textContent = title;
    authBody.replaceChildren();
    if (!auth.open) auth.showModal();
    return generation;
  }

  function authError() {
    const status = element("p", "course-auth-error");
    status.setAttribute("role", "alert");
    status.hidden = true;
    return status;
  }

  function showLogin(mode, publication = null) {
    const registering = mode === "register";
    const generation = showAuthPanel(registering ? "注册社区账号" : "登录共享社区");
    const form = element("form", "course-auth-form");
    const usernameLabel = element("label", "", "用户名");
    const username = element("input");
    username.name = "username";
    username.required = true;
    username.autocomplete = "username";
    if (registering) {
      username.minLength = 2;
      username.maxLength = 32;
    }
    usernameLabel.append(username);
    const passwordLabel = element("label", "", "密码");
    const password = element("input");
    password.name = "password";
    password.type = "password";
    password.required = true;
    password.autocomplete = "current-password";
    password.maxLength = 128;
    if (registering) {
      password.autocomplete = "new-password";
      password.minLength = 8;
    }
    passwordLabel.append(password);
    const submit = element("button", "course-primary", registering ? "注册" : "登录");
    submit.type = "submit";
    const errorBox = authError();
    form.append(usernameLabel, passwordLabel, errorBox, submit);
    const switchMode = button(registering ? "已有账号？登录" : "还没有账号？注册", "course-auth-switch");
    switchMode.addEventListener("click", () => showLogin(registering ? "login" : "register", publication));
    if (publication) authBody.append(element("p", "course-auth-note", "登录后即可确认分享此课程。"));
    authBody.append(form, switchMode);
    username.focus();
    form.addEventListener("submit", async event => {
      event.preventDefault();
      const requestVersion = ++sessionVersion;
      submit.disabled = true;
      switchMode.disabled = true;
      errorBox.hidden = true;
      try {
        const {user} = await request(`/api/auth/${mode}`, "POST", {username: username.value.trim(), password: password.value});
        if (requestVersion === sessionVersion) setAccount(user);
        if (!auth.open || generation !== authRender) return;
        if (publication) showPublication(publication);
        else auth.close();
      } catch (error) {
        errorBox.textContent = error.message;
        errorBox.hidden = false;
      } finally {
        submit.disabled = false;
        switchMode.disabled = false;
      }
    });
  }

  function showPublication(publication) {
    const generation = showAuthPanel("分享到共享社区");
    const description = element("p", "course-auth-note", "社区访客可以观看视频、阅读讲稿与学习计划，并下载课件及视频。");
    const confirm = button("确认发布", "course-primary");
    const cancel = button("取消", "course-quiet-button");
    cancel.addEventListener("click", () => auth.close());
    const actions = element("div", "course-auth-actions");
    actions.append(cancel, confirm);
    const errorBox = authError();
    authBody.append(element("h3", "", publication.title), description, errorBox, actions);
    cancel.focus();
    confirm.addEventListener("click", async () => {
      confirm.disabled = true;
      errorBox.hidden = true;
      try {
        const course = await request(`/api/jobs/${publication.id}/publish`, "POST");
        if (auth.open && generation === authRender) auth.close();
        await refresh("community");
        showNotice("课程已发布到共享社区", "查看课程", () => {
          goTo("community");
          showCourse(course, true);
        });
      } catch (error) {
        if (!auth.open || generation !== authRender) return;
        if (error.status === 401) {
          setAccount(null);
          showLogin("login", publication);
        } else {
          errorBox.textContent = error.message;
          errorBox.hidden = false;
        }
      } finally {
        confirm.disabled = false;
      }
    });
  }

  shareButton.addEventListener("click", () => {
    if (job?.status !== "completed") return;
    const publication = {
      id: job.id,
      title: lessonTitle || find("#lesson-title")?.textContent || job.filename || "课程",
    };
    if (account) showPublication(publication);
    else showLogin("login", publication);
  });
  signIn?.addEventListener("click", () => showLogin("login"));
  signUp?.addEventListener("click", () => showLogin("register"));
  signOut?.addEventListener("click", async () => {
    signOut.disabled = true;
    ++sessionVersion;
    try {
      const {user} = await request("/api/auth/logout", "POST");
      setAccount(user);
    } catch (error) {
      showNotice(error.message);
    } finally {
      signOut.disabled = false;
    }
  });

  window.LivelyCourses = {setJob, refresh};
  refresh("my-courses");
  refresh("community");
})();
