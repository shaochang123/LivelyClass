# LivelyClass 绘声绘课

**Language / 语言:** [简体中文](README.md) | [English](README.en.md)

Convert textbooks, notes and knowledge PDFs you have permission to use into Chinese teaching videos and PPTX courseware with source references. The pipeline supports text extraction, local OCR, course planning, scripts, SVG diagrams, speech and video assembly. The website shows scripts, PDF pages, quotations and knowledge relations, with MP4, PPTX and scene data downloads.

**Real AI mode** uses local Ollama or a compatible service. **Deterministic demo mode** needs no model or key and is explicitly labelled as rule-generated. Source matching, model review and computation checks have limited scopes; teaching content still needs human review.

## Four core capabilities

- **Source reading:** The new website copies all extracted textbook text into subtitle and speech inputs without knowledge selection. Page IDs, character ranges and hashes support checks for omissions, repetition, reordering and rewriting. Slides may summarize. Scanned pages remain dependent on OCR quality.
- **Automatic direction:** Thirteen templates cover STEM, humanities and professional families. A local model identifies the subject from source content, then selects the template, layout and diagrams or animation. STEM uses figures and computational scenes; humanities uses scene dramas. The website requires no manual presentation, template or animation selection.
- **Personalized study paths:** Enter prior knowledge, learning goals, days and daily available minutes under “个性化学习规划”. The model schedules study, revision and self checks using actual course segment IDs and PDF page references. Code checks coverage and assigns the requested time budget. View the plan online or download Markdown/JSON; the source course remains unabridged.
- **Two dynamic presentation paths:** Quantitative content can use Manim for computed figures and processes. Humanities scene dramas plan characters, locations and shots, then call image or video services. Results identify the medium actually produced. Manim constructions require computation and rendering checks; scene dramas require a configured media service. PPT slides contain static keyframes and motion is delivered in a separate MP4.

The English product name is **LivelyClass** and the Chinese name is **绘声绘课**. **Bring knowledge to life, bring the classroom alive.** Internal Python packages, configuration variables and existing data paths retain compatible names. See the [four-feature implementation review](docs/四项功能核对.md) for code locations.

Subject names and template routing serve different purposes. Formulae, geometry and computed mechanisms use STEM layouts; entity relations, table structures and system dependencies use professional structure layouts, even when the academic field also belongs to STEM. For media integration, Seedream image models draw scenes and characters, while Seedance video models generate motion clips. The Volcengine Ark base URL is `https://ark.cn-beijing.volces.com/api/v3`; enter the image/video model IDs enabled in your account and their credentials separately. See [subject routing and drama integration tests](docs/学科选版与漫剧接入测试.md) for configuration and test steps.

The responsive website uses a light technology theme: white navigation, a blue-violet hero, blue action buttons and an open workspace. Navigation links lead directly to the course workspace, automatic direction and generation workflow on desktop and mobile.

Hard-refresh the browser after UI updates. Restart an already running Python service to load updated template display names. See the [interface visual guidelines](docs/界面视觉规范.md).

### Adaptive styling and Comic lessons

Upload a textbook in real AI mode. The model identifies its subject, chooses the style and layout, then plans each segment as a relationship diagram, computational figure or drawn scene. Describe audience, detail or art preferences in the PPT instructions. Website creation and retries always use automatic direction, without restoring manual template or animation settings from old jobs. Adaptive palettes follow course content; the application checks readability and retains necessary color adjustments.

Humanities uses scene dramas. The model defines the world, character appearance and wardrobe, and locations, then plans establishing views, character actions and details in source order. The complete textbook text remains narration and subtitles, without assigning it to a fixed teacher/student dialogue. Each shot uses an actual image or video service asset. PPT contains scene keyframes and complete speaker notes; MP4 uses the original narration once. Image-only services produce illustrated sequences; video services supply moving clips. Stills are not labelled as character animation. Existing dialogue jobs retain their original records.

The API retains `style_mode=subject|template|adaptive|comic` and existing animation parameters for compatibility. AI jobs created or retried through the website always submit `narration_mode=source_verbatim`, `style_mode=subject` and `animation_mode=auto`; deterministic demo mode uses basic illustrated pages. Classification and template selection are separate model calls, followed by per-segment visual tool planning. Legacy clients omitting the new parameters retain the explanatory workflow. Source-course understanding and planning allow only local or the authorized academy-server models, excluding DeepSeek and cloud language-model inference.

Source courses export burned-in subtitles, SRT, VTT, exact-text JSON, a transcript and per-page coverage records. Subtitle timings use actual utterance durations. The full slide is scaled above a separate subtitle band. PPT contains no embedded audio/video; each segment's source speech occurs once in MP4. Continuous STEM scenes retain construction and calculation checks; incomplete scenes retain SVG and explicit records.

Image/video services are disabled by default. Scene dramas require an Images-compatible gateway or the Seedance task protocol; video takes priority when both are enabled. The asset limit defaults to 12, up to 200 per course. If the shot count exceeds it, generation stops before asset requests and asks for an adjustment, without increasing the limit or inserting dialogue characters. Only scene briefs are transmitted with explicit consent; exported assets are labelled as AI interpretations. Shared character and location descriptions guide continuity; reference-image conditioning is not implemented, so visual continuity requires review. Media keys stay in task memory, outside SQLite, files and browser storage. Services such as Xiaoyunque need a compatible gateway. See [source reading and subject routing](docs/原文课程与学科编排.md) and [adaptive styling and comic lessons](docs/学科风格与漫剧.md).

Jimeng text-to-image 3.0 has a direct **Jimeng (API Key)** option: use `https://openapi.cv.volces.com`, model/req_key `jimeng_t2i_v30`, and the API Key Secret. This Bearer endpoint differs from the AK/SK-signed `visual.volcengineapi.com` endpoint. Submitted task IDs are retained; query retries do not create another task. Prompts over 800 characters stop before asset requests instead of being truncated, and server-side prompt expansion is disabled. Drama planning reviews each shot against the source meaning, character and prop identities, and event order. Rejected drafts receive bounded revisions and retain the first record. Actual artwork still requires inspection beyond model review.

## Quick start (Windows)

Run from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[test]"
.\.venv\Scripts\python -m uvicorn zhijiang.main:app --host 127.0.0.1 --port 8765
```

Open [http://127.0.0.1:8765/](http://127.0.0.1:8765/), select the [original sample PDF](examples/binary_search_original.pdf), confirm rights, and try demo mode with Windows system speech. Review the script, sources, video and slides. **Press `Ctrl+C` in the server terminal to stop it**; closing the browser does not stop the service.

Python 3.11+ is required; Python 3.13 has been verified. System narration requires Windows Chinese speech; other environments can use a compatible speech API. `imageio-ffmpeg` supplies FFmpeg, so a system installation is unnecessary.

The website focuses on uploads, automatic direction, voice audition and generated results, with larger body text and concise labels. Model, speech and media-service settings are collapsed by default; unconfigured model or speech settings open automatically, as do panels containing invalid inputs. Generation warnings and technical information are available under “生成记录” (generation records); each script segment has expandable “原文依据” (source evidence).

## Model and animation configuration

Copy the template for initial setup. If `.env.local` exists, edit it to preserve your settings:

```powershell
Copy-Item .env.local.example .env.local
ollama list
```

Set `ZHIJIANG_LLM_MODEL` to an installed model, start Ollama, and restart the web service. Mathematical object mode requires image input; the local model is `qwen3.5:9b`, with `ZHIJIANG_OLLAMA_SEMANTIC_THINKING=true` recommended. `.env.local`, model weights, uploads and generated artifacts are ignored by Git. The website also accepts per-job settings. External services require consent to send text, mathematical page images or narration.

On Windows, “在本机记住密钥” (remember locally) is checked by default. Model and speech keys are encrypted for the current Windows account in Git-ignored `data/private/credentials.dpapi`. Refreshes and restarts show a saved status; leave the key field empty to reuse it. Keys are separated by purpose, provider and API endpoint; switching endpoints never borrows an old key. “清除保存” removes the current endpoint's saved key; unchecking remember only stops new saves. The browser persists public endpoint, model and voice settings only. Keys are not written to SQLite, lesson JSON or web responses. Other platforms use local environment configuration or per-job keys.

Optional `ZHIJIANG_OLLAMA_NUM_GPU` controls GPU layer allocation; leave it empty for Ollama's automatic allocation. This 8GB desktop uses `24` to preserve memory headroom while other applications run; adjust it to your hardware. Speed depends on available GPU memory, RAM and the model.

The project imposes no generated-token quota or total inference deadline: every Ollama stage requests `num_predict=-1`, and compatible APIs receive no output-length cap. Context memory follows Ollama settings by default; optional `ZHIJIANG_OLLAMA_CONTEXT_TOKENS` accepts a positive integer with no project maximum. This machine uses `16384`. If an upstream service still truncates, the client requests a complete result again and identifies the service-side failure, never approving partial JSON. See [configuration and recovery](docs/configuration.en.md).

Ordinary semantic deliberation follows model/service sampling settings instead of forced zero temperature; the user's thinking switch still applies.

`ZHIJIANG_OLLAMA_THINKING_LEVEL` selects a reasoning level actually advertised by the service; leave it empty for the service default. Unsupported levels fail explicitly. Models with only a Boolean thinking switch continue to use `ZHIJIANG_OLLAMA_SEMANTIC_THINKING`. Results with different models, levels or hardware are not an intrinsic model ranking.

```powershell
.\.venv\Scripts\python -m pip install -e ".[math-animation]"
```

| Animation mode | Current behavior |
| --- | --- |
| `auto` | AI default: narration, SVG relations/processes/comparisons and progressive reveals from complete source context. Strict review uses independent source checks and Manim scenes. |
| `visual` | Direct teaching diagrams by default; general Manim scenes with strict review. |
| `geometry` | General 2-D mathematical objects; requires TeX and fails explicitly without relation-graph or page-highlight fallback. |
| `math` | Specialized 2-D linear transformations; requires suitable sources and TeX. |
| `basic` | Compact AI teaching diagrams by default; the basic narration workflow with strict review, and rule-based bullets in demo mode. |

Ordinary source revision can send page images to the selected model; remote services require the existing material-sharing consent. Native relations and page annotations do not need TeX; computational geometry and specialized math need `latex` and `dvisvgm`. See [configuration and recovery](docs/configuration.en.md) for speech, Kokoro, external APIs and resume; see [teaching diagrams and animations](docs/animations.en.md) for diagrams and skills.

The generation policy defaults to **direct generation** (API: `content_checks=warn`). Ordinary slides read complete cited and neighboring pages, then revise titles, narration and diagrams against source text and available page images, preserving negation, conditions, examples and angle/side identities. This uses the same model and is not an independent factual assessment. Unavailable revision retains the course with a warning. `auto` and `visual` can attempt actual mathematical constructions and continuous demonstrations; unsuccessful constructions retain the ordinary diagram instead of failing the course. The program handles citation identity, file structure and executable rendering. Choose `strict` for the previous review workflow. Create and retry both persist this option. See [direct generation and recovery](docs/直接生成与失败恢复.md).

Ordinary source revision retrieves actual embedded PDF picture regions. The model chooses a relevant candidate ID; the program resolves its page and crop bounds. Full-page scans are excluded as illustration candidates; scans and vector drawings can still use image-based cropping. Selected pictures are read separately from the slide draft before titles, narration and diagrams are aligned; this remains same-model revision. When no suitable source picture is available, the teaching diagram remains. Mathematical demonstrations compile object dependencies before planning actions and measurements against computed tool results, retaining valid objects during action repair. Numbered object names are permitted in measurement labels; values must still be computed from actual objects rather than supplied as answers.

Source revision and picture alignment also follow the thinking configuration. Ordinary `auto` and `visual` modes can use the configured mathematics planner and reviewer. Ollama cloud requests omit local GPU controls and negative generation limits; transient throttling follows service retry guidance, while exhausted monthly usage is reported separately. Mathematical demonstrations bind every step to actual relations, including inequality for differences and counterexamples, and review source and numerical correspondence step by step.

Complete revisions that omit per-page headings retain the existing knowledge-point titles without rewriting narration. Pages that repeat the overall course title also use their corresponding topic titles. Source IDs, narration references and necessary diagram content still follow the full contract.

Automatic object demonstrations first distinguish quantitative relations and physical mechanisms from symbolic reading sequences or concept comparisons. Symbolic sequences and comparisons use teaching diagrams rather than invented coordinate measurements; quantitative, physical and uncertain content retains an object-demonstration attempt. Decisions record source-based reasons without textbook lists or subject exclusions.

## Natural narration (optional)

Run **Qwen3-TTS 0.6B Base** in a separate `.venv-tts` environment to condition narration on a local reference voice. Auditions follow the selected speech mode: system speech uses the configured Windows voice; AI speech looks up an audition for the currently entered endpoint, model and voice. Click “生成试听” to generate a missing clip or “重新生成试听” to refresh one. Auditions use the same API adapter and credential resolution as course narration; external services require consent to send the original sample text. Changed settings stop the previous audio, and cached auditions survive refreshes. Models or voices unavailable from the service report an error without playing a different voice. AI narration belongs to the separate video; slides retain complete speaker notes. Auditions, voice prompts, weights, and `.env.local` are Git-ignored. Reference audio and scripts remain local. See [local reference-conditioned speech](docs/configuration.en.md#local-reference-conditioned-speech) for installation, startup, and CPU/GPU settings.

Start speech with `.\scripts\start_voice_service.ps1`; stop it with `Ctrl+C` in its terminal. With `ZHIJIANG_DEFAULT_VOICE_MODE=ai`, new website sessions prefer the configured AI voice. Existing jobs and API calls without a voice mode retain their previous behavior. Direct generation switches the entire lesson to system speech if AI speech is unavailable, recording a warning and disclosing the actual voice. Judge voice similarity and naturalness by listening to the audition.

## PPT templates and course director

The model automatically chooses styles and layouts from STEM, humanities, professional and general template families. Available designs include dark presentations, paper explainers, academic, editorial and technology themes. Template instructions guide AI planning; PPT and video share backgrounds, headings and diagrams. Real AI selects visual tools, permitted layouts and static teaching steps from the content, while demo mode uses rule-based layouts. Internal IDs remain compatible with old jobs; advanced API clients can still specify a template explicitly.

Reciprocal relations use separate arrow lanes and label positions. Pale decorative colors are not used for low-contrast semantic text. Unconnected, empty boxes that duplicate edge labels are merged into those labels, retaining complete aliases and the model draft.

Diagrams support concept ovals, operation boxes, data parallelograms, documents and results; decision diamonds represent actual conditional decisions, and comparison pages use open space. Narrow nodes and crowded content change composition or paginate while retaining objects, relations and conditions. Each narration plays once. Failed exports retain synthesized audio; retries reuse files only when narration, voice identity and audio hashes match. Switching model providers requires a matching endpoint and model. Endpoint and model settings are restored after refresh.

Reproduce the file-format and feature matrix, real local-model runs and long-video probes using [generation stability and feature tests](docs/生成稳定性与功能测试.md). For ordinary jobs that failed during export, run `python scripts/resume_job_export.py YOUR_JOB_ID` to export the retained course with local speech without calling the language model again.

New jobs export **a static PPT and a separate narrated MP4**, without embedded video or audio in the deck. They share templates, diagrams and illustrations. The deck shows static mathematical steps; the video shows continuous demonstrations, using each narration once. Relevant textbook illustrations or sentence images can be cited with crop and source information in notes. Complex connections and long annotations use the full teaching viewport; source figures receive a separate page when needed. Titles use approximately 36–44 pt and body text at least 24 pt. Existing generated files remain accessible; new exports use this behavior. AI website jobs use the template selected from the subject classification; demo mode uses `paper`. See [course director and PPT templates](docs/课程导演与PPT模板.md).

New jobs separate speech from screen content. Full narration stays in notes and on the website; compact object labels, example tables, comparisons, sourced arrows and concept diagrams take its place on screen. In strict mode, every shortened assertion and connector is bound to a complete script sentence and reviewed for conditions, negation and direction. Crowded diagrams change composition. Existing mathematical SVGs and continuous scenes retain priority.

Direct generation includes a cover, roadmap and closing slide. The model selects core takeaways from complete narration sentences, which the program copies unchanged; final segments about journals or organizations do not automatically become the summary. If roadmap planning fails, the existing navigation is kept with a warning and production continues.

Strict-review AI captions are first grounded in complete narration sentences, then a separate stage designs compact visuals. Full sentences are review evidence, not mandatory screen text. Independently read propositions restore knowledge diagrams; explicit fields and values can become example tables. Unsupported arrows are rejected and recorded. Model review does not establish that the narration itself is correct. Chinese candidate boundaries use `jieba`, now a core dependency; ordinary slides do not require the math-animation extra.

The strict workflow includes an example planner that selects attribute records, instance matrices, comparisons, relations, timelines, numerical examples, annotations or conclusions according to the content. Open compositions retain the director's column and paired-row choices. Explicit movement from an origin to a destination can use arrows that retain the moving object's identity and direction; named parallel parts do not become an invented causal chain. Bounded arithmetic checks numerical equality, with source meaning reviewed separately. Rejected exhibits retain literal source content or the earlier reviewed visual and rejection record rather than inventing an example. Reveals, focus changes and arrow drawing use sentence-length timing estimates, not forced audio alignment; existing continuous mathematics scenes take priority. See [concrete examples and visual variety](docs/具体示例与画面多样性.md) for the workflow, limits and actual verification.

Strict-mode basic AI narration must cover every planned knowledge point. Explicit numeric calculations and records in selected quotations are checked for omitted spoken values. If a retry still omits them, the complete quotation and its conditions are restored, disclosed in the web notice and `script-coverage.json`. This limited check does not cover all textbook examples, Chinese number words, algebra or proofs; displaying numbers does not establish correct relationships.

Chinese source extraction joins physical PDF line wraps before bounding quotations at sentence or clause boundaries, keeping units and conditions together. Long formulas without punctuation still require comparison with the original page.

Strict mode groups the roadmap into at most four consecutive learning themes (at least two for five or more passages, and three for twelve or more) and checks each theme against its actual passages, then independently selects and reviews complete knowledge statements for the ending instead of listing every slide title. Both PPT and new complete videos include a cover and ending. Videos hold the cover for 3 seconds and the ending for 5 seconds with intentional silence, without repeating narration. Existing generated files remain accessible.

## Searchable teaching illustrations

The package includes **100 original line symbols and 9 sourced CC0 hand-drawn illustrations** across agriculture and everyday life, computing, mathematics, physics and engineering, chemistry, biology, education, medicine, finance, law, transport and history. The books retain pencil texture; the watermelon and bulb retain freehand strokes. Authors, origins and licenses are listed beside each sourced asset. Automatic illustration selection is enabled by default. The real model selects at most one primary illustration and two supporting symbols per page from up to eight local candidates, preferring illustrations of the same object; demo mode uses deterministic matching. Pictures are bound to their examples or teaching points and together occupy less than 8% of an ordinary slide. Key knowledge and demonstrations remain prominent. Textbook evidence and independent demonstrations supply the knowledge and mathematical relations.

Search tags and object aliases are separate: a related subject term cannot rename a picture, so a lung illustration is not labelled as an alveolus. English aliases use word boundaries. Each named object has one preferred candidate depiction. Missing artwork leaves the knowledge layout intact and records a gap. The current nine hand-drawn entries do not cover every subject.

Browse [zhijiang/assets/visuals](zhijiang/assets/visuals/README.md). Every directory README describes its children and assets. Add SVG or transparent PNG files using the documented metadata format. The core dependency `resvg_py==0.5.0` creates transparent PNG fallbacks. Indexes, previews and caches are stored in ignored `data/`:

```powershell
.\.venv\Scripts\python scripts/index_visual_assets.py --check --rebuild --contact-sheet
```

Creation and retry APIs accept `use_illustrations`, enabled by default; omitted retry values retain the previous choice. Changes to descriptions or files invalidate illustration planning caches. See [illustration library and shared pages](docs/素材库与共享画面.md) for authoring and acceptance procedures.

## Documentation and directories

| Entry | Contents |
| --- | --- |
| [Documentation index](docs/README.md) | Reading order and directory responsibilities. |
| [Product manual](docs/产品说明书.md) | Goals, design, value and limitations. |
| [User guide](docs/使用指南.md) | Start/stop, upload, inspect, download, retry and delete. |
| [Course director and PPT templates](docs/课程导演与PPT模板.md) | Reference analysis, template layouts, actual agent stages and limits. |
| [Architecture and API](docs/架构与接口.md) | Actual modules, contracts, endpoints, states and caches. |
| [Implementation status and roadmap](docs/实现状态与路线图.md) | Reference design, implemented, partial and planned capabilities. |
| [Validation and acceptance](docs/验证与验收.md) | Automated, media and teaching-quality evidence. |
| [Compact visuals and closing acceptance](docs/画面修复验收-2026-10.md) | Visual regression repair, grouped roadmap, bookends and cross-domain media. |
| [Concrete examples and visual variety](docs/具体示例与画面多样性.md) | Attribute records, instance matrices, equations, open compositions and source-based timing. |
| [Illustration and shared-page acceptance](docs/素材库验收-2026-10.md) | Actual visual/media results for eight inputs, repairs and remaining limits. |
| [AI Coding record](docs/AI_Coding_全过程.md) | Actual Codex use, feedback, fixes and commits. |
| [Sources and licenses](docs/第三方来源与许可.md) | Dependencies, models and source documents. |

The index and main product documents are in Chinese; configuration and animation references have English equivalents. `zhijiang/` holds application and generation modules, `tests/` automated checks, `scripts/` sample/validation/document tools, `examples/` original inputs and source manifests, and `benchmarks/` public benchmark definitions and protocols. `data/`, `demo/`, `submission/` and `output/` contain ignored local data or generated artifacts; the repository retains their scripts and documentation. See the [archive](docs/archive/2026-09/README.md).

## Textbook benchmark

The [paired textbook benchmark](benchmarks/textbooks_v1/README.md) contains database design, geology, psychology and business management excerpts at 2, 6 and 12 pages. Each has a native-text and an image-only PDF counterpart, giving 24 cases. The real production Agent generates courses, PPT and complete videos, recording first-attempt outcomes, OCR, latency, tokens, media checks and separate manual teaching reviews. Sources and evaluation definitions are versioned; books, outputs and measured results stay in Git-ignored `data/textbook-benchmark/`.

The [expanded benchmark](benchmarks/textbooks_v2/README.md) adds junior, senior and university mathematics, poetry appreciation, English grammar, chemistry, biology and physics: eight groups with 2/6/12-page text/scan pairs, defining 48 cases. Poetry uses an original supplemental reader; other inputs are openly licensed textbooks. Together the suites define ten disciplines, twelve subject/level groups and 72 cases; measured scores from different code versions remain separate.

The [Chinese PEP benchmark v3](benchmarks/textbooks_v3/README.md) uses official textbook pages for junior/senior mathematics, unified Chinese poetry, Chinese explanations of English grammar, physics, chemistry and biology: seven groups at 2/6/12 pages, preparing 21 image-only PDFs requiring OCR. Native-text versions and the university mathematics electronic body remain explicit coverage gaps; model OCR text is never added to manufacture a native-text baseline. `scripts/benchmark_chinese_textbooks.py` imports recorded local images, runs the production Agent, reports results and creates a personal-study archive for the same local user. It uses local models only. Original watermarks, attribution and page proportions are retained; redistribution is not authorized, and textbook pages and outputs remain Git-ignored. Sources, evaluation and retest protocols are documented in the suite.

The benchmark defaults to a local model and system speech and excludes DeepSeek. Authorized public excerpts can be tested in a separate results directory with `--model gemma4:cloud --allow-ollama-cloud`; transmission consent is recorded and different models and configurations are scored separately. Export recoveries preserve first failures. `audit` rechecks retained artifacts; `package --include-media` bundles static slides and separate videos. Expanded commands add `--suite benchmarks/textbooks_v2 --root data/textbook-benchmark-v2`.

Illustration sentence IDs outside the current narration no longer crash PPT/video exports. The renderer skips the unsupported fallback binding instead of attaching the asset to a different sentence. New page caches use an updated version; existing generated files remain accessible.

Source revision checks coverage of every knowledge-point ID. Missing pages, duplicate IDs or invalid fields trigger individual script and visual generation for the affected pages while preserving typed valid pages from complete responses. If one page cannot be revised, its original draft and an explicit warning remain, and other successful revisions are retained. A one-page response cannot count as a completed batch; visual sentence references must belong to that page's narration.

Identical redundant condition flags are structurally normalized; conflicting flags still require clarification. Invalid sentence references are repaired against program-numbered full narration without rewriting the speech. Pictures are first classified independently: video covers, decorations and uncertain images are excluded from mechanism diagrams. Shared-category links are undirected. Ollama knowledge extraction uses JSON output with full type validation rather than complex native decoding constraints at that stage.

```powershell
.\.venv\Scripts\python scripts/benchmark_textbooks.py prepare
.\.venv\Scripts\python scripts/benchmark_textbooks.py run
.\.venv\Scripts\python scripts/benchmark_textbooks.py report
```

Engineering success, keyword mentions and teaching correctness are evaluated separately. Clear simulated scans of English excerpts do not establish performance for photographs, handwriting, Chinese textbooks or complete books.

## Git commits and local data

Keys and local `.env*` files, uploads, databases, model weights, caches, generated slides/video/audio/course JSON, exported PDF/ZIP files and result screenshots are ignored. Empty configuration templates, original public input fixtures, authored assets and documentation remain versioned. Existing artifacts have been untracked while preserving local copies; this does not remove them from historical commits.

Enable the push check in each new clone:

```powershell
git config core.hooksPath .githooks
.\.venv\Scripts\python scripts\check_git_privacy.py
```

The check inspects outgoing commits, including intermediate commits, and rejects force-added ignored files and detected embedded credentials. It prints filenames and issue types only. It uses Python's standard library, without a model service. Details and historical scope are in [Git privacy and artifact management](docs/Git隐私与产物管理.md).

## Workflow

[![From PDF to videos and slides](docs/workflow.en.svg)](docs/workflow.en.svg)

Strict-mode qualitative diagrams start with independent source reading and propositions. Subjects, predicates, objects and conditions form knowledge relations. Models select source IDs; the program binds quotations and shares SVG/animation layouts. Parametric geometry uses restricted expressions and numeric checks; model code is never executed. See [architecture and API](docs/架构与接口.md).

Strict-mode qualitative scenes first retrieve required evidence from the current and adjacent input pages and review the title's scope, then bind facts to narration. Reading is no longer limited to three sentences around a citation. The program preserves complete inline italic foreign-language examples; source annotations zoom into the current evidence at each step. See the [Agent and model capability audit](docs/agent-model-capability-2026-10.md) for evidence and remaining issues.

Mathematical object mode first recovers formulas and diagram meaning from the actual page image, then separately plans teaching, mathematical constructions and operations. Generic constructions include points, segments, functions, parametric curves, midpoints, projections, rigid transforms, equal-area partitions, implicit distance loci, inverses and finite expansions. The program computes dependent coordinates, derivatives and coefficients rather than selecting a preset storyboard by textbook title. SVG and video use equal coordinate units.

Construction graphs compile in dependency order, recomputing derived values on each motion frame; static narration holds retain a verified state. Relations measure actual object distances, angles, areas, function values and derivatives; parameter-only tautologies are rejected. Camera bounds cover motion samples, and visible point and label anchors are checked. Speech, SVG and video share calculations, with rounded values marked as approximations. Structural, source and numeric errors trigger repair or explicit failure; model review does not replace mathematical checks. These partial checks still do not guarantee all knowledge.

Coordinate measurements retain signs, separately from distances and unsigned included angles; directed angles are also available. Rotated areas and lengths simplify from actual vertices while rendered geometry is still checked. Invariants and designated step-end relations are validated separately. Visible finite anchors determine an equal-unit viewport; remote function tails may be clipped instead of compressing the teaching objects. Optional mathematics planning and independent review models use the same API: the generation model reads pages and reviews images, the planner produces constructions and behavior, and the reviewer checks sources and text. Empty fields use the generation model. Choose a different model from the planner for independent review. Actual roles and calls are recorded separately; review is not a correctness guarantee. Ollama cloud models require transmission consent even through a local URL; see the [configuration reference](docs/configuration.en.md#general-mathematical-object-mode).

Mathematical review compares the original page images with each generated SVG. Source, geometry and motion observations precede approval. Shared-endpoint segments support vertex angles; directed arcs can retain a full signed sweep, whose actual endpoints are checked. Complex computed renderer expressions use bounded intermediate nodes without relaxing AST limits. Numeric narration repairs preserve the validated objects and operations; motion frames are checked continuously and holds retain a verified final state.

A rigid transform shares object identity with its source. Set the original to `reference=true` for before/after comparison; it is dimmed and labelled as a reference. Hide the original when moving the same object. Arcs, circles and function curves also support rigid transforms; rotated functions render as parametric curves.

After reading the original pages, the system independently lists required conditions, conclusions and core worked examples before planning images. It checks each requirement against the actual narration, operation values and rendered geometry. Points on parametric curves can measure actual joins between arc endpoints. Mathematics lessons use 3–10 steps according to their content; concise complete qualitative narration needs no padding. Model responses without a completion marker are never accepted, and interrupted service calls retry at most twice. See the [mathematics repair report](docs/math-repair-acceptance-2026-10.md) for current repairs and joint textbook acceptance.

Source subjects bind to actual object types, vertex counts and measurement dependencies, rejecting unrelated placeholder figures. Concept photographs and decorative illustrations may be explained with equivalent correct mathematical diagrams. `align` computes continuous rigid alignment from actual anchor and direction pairs, without guessing rotations or changing lengths and areas. Angular sums and differences show radians. Persistent semantic construction errors trigger a bounded design revision, retaining earlier drafts and rejection evidence.

Source subjects may specify how many objects of the same type are required. Bindings check each object's type, visibility and measured dependencies, rejecting duplicate or unrelated objects. Heterogeneous subjects remain separate; completed diagrams with quantitative or correspondence labels also count as worked examples. Undefined initial construction data is rejected before model review, including hidden auxiliary objects at infinity.

The source scope is generated against original page images, then independently checked for translation, conditions and worked examples before scene design. After construction, tools return actual coordinates, areas, lengths and angles, and check whether the objects and controls can express the required content. Generic intersections support two lines/rays/segments or two circles; lines and rays extend to the actual viewport, and curve points must stay within the plotted domain. Invalid fields in a complete behavior JSON receive targeted repair while valid fields remain intact. Malformed JSON is never accepted from partial content; the full contract and mathematical, source, SVG and motion checks still apply.

Construction repair supports bounded additions, replacements and removals, preserving untouched valid objects and restoring actual dependency order. Two consecutive behavior failures return to construction repair even when error wording differs. Drafts allow at most 32 definitions; expanded core objects remain capped at 48 and the rendering contract, including point labels, at 64. Patches cannot bypass source or actual-animation checks.

The compiler also resolves dependency order in newly generated complete graphs, preserving expressions, object identities and valid drawing order. Duplicate IDs, missing references and cycles are never guessed away. Non-finite initial states and drawing ranges that compress the teaching objects in ordinary-course demonstrations return directly to graph repair instead of repeatedly rewriting unrelated narration. Local records retain candidates, actual compilation order and rejection reasons.


Source coverage and construction readiness are reviewed in batches of two, retaining every requirement and rejection in the aggregate. The program binds the reviewed Chinese source summary verbatim to explanation cards and speech: definitions and conditions precede motion, and worked examples accompany their demonstration steps. Cards are not mathematical proofs. Dynamic values still come from actual object measurements, and free narration retains numeric validation. Mathematical motion starts after the source cards finish; the script includes those explanations. Acceptance inspects both source cards and actual motion frames.

Concrete geometric worked examples also receive a separately read source identity graph, preserving parts, wholes, positions and labelled ownership. Models bind entities to computed objects; the program checks types, relative positions and vertex inclusion in finite polygons, then assigns consistent colours to source identity groups and their transformed copies. Incidental static construction points are hidden. Explicit translation directions must agree with sampled coordinates. Completely overlapping collinear segments, rays and lines of the same colour require separate visibility or a clearer expression. These are limited structural and motion checks; source and actual media still require human review.

## Tests and reproduction

```powershell
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python scripts\build_demo.py
.\.venv\Scripts\python scripts\build_ollama_demo.py
.\.venv\Scripts\python scripts\verify_teaching_artifacts.py --job-dir data\jobs\YOUR_JOB_ID
.\.venv\Scripts\python scripts\build_documents.py
```

AI samples require a running local model; deterministic samples do not. Documents export to `output/pdf/` without creating jobs or calling models. See the [demo guide](docs/Demo_操作说明.md) for full operations and historical versions.

Selected textbook pages from linguistics, economics, sociology and database design produced 22 SVG/narrated clips, 4 MP4s and 4 PPTX files. Media checks passed; teaching quality was partial. These inputs informed debugging, so this is not an independent blind test or proof of full-book coverage. See [textbook validation](docs/generalization-validation-2026-10.md).

See [four-level mathematics validation](docs/math-level-validation-2026-10.md) for selected pages, SVGs and actual animation assessment. The tested textbooks have not met full teaching-quality acceptance; successful media exports have also contained wrong geometry. Reproduce with `python -m scripts.verify_math_textbooks`; [the source manifest](examples/math-level-sources.json) records sources and pages. Flowcharts, page highlights and fading text cards do not pass as mathematical demonstrations.

## Current limitations

- One sequential local queue; one PDF produces one lesson. Only one web service may own a data directory; duplicate startup exits without interrupting active jobs. Upload limit: 200 MB, with no fixed page or target-duration limit. Long books increase processing time and size.
- OCR does not guarantee recovery of complex formulas, tables or layout. No manual formula-confirmation interface exists.
- Mathematical object mode reads actual mixed-page images and formulas, but recognition and model review can still be wrong; compare with the original. Supported expressions are restricted 2-D constructions and unique real function branches. Model planning, unclear source pages and unsupported mathematical operations fail explicitly.
- Each strict-mode qualitative scene uses one source page and may reselect an adjacent input page. Missing title requirements or explanations requiring joint evidence across pages fail explicitly. Scope and semantic reviews can still be wrong and do not guarantee factual correctness.
- The website displays courses and sources but has no project list, knowledge selection/editor, script editor or human approval/publishing workflow.
- PPTX is static courseware with editable titles and notes; SVG paths are not separate PowerPoint shapes. Narration and continuous animations belong to the separate full MP4.
- Restricted 2-D scenes, source-relation tracing and source-course SRT/VTT exports are supported. Complex 3-D mechanisms, cross-chapter reasoning, fact proofs and course ZIP export are not implemented. Character coverage does not independently establish OCR or diagram semantics.

Source code uses the [MIT License](LICENSE). Dependencies and textbooks have their own licenses; see [sources and licenses](docs/第三方来源与许可.md).
