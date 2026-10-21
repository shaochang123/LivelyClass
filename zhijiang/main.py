"""本地网站与受限 API。使用 `python -m uvicorn zhijiang.main:app` 启动。"""

from __future__ import annotations

import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated,Literal
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, UploadFile,Request
from fastapi import Path as PathParameter
from pydantic import BaseModel,ConfigDict,Field,field_validator
from fastapi.responses import FileResponse, Response
from zhijiang.presentation_templates import PPTTemplate, TEMPLATES, template_preview_svg
from fastapi.staticfiles import StaticFiles

from zhijiang.config import Settings
from zhijiang.models import GenerationOptions, JobStatus, Mode, SpeechOptions, VoiceMode, ArtOptions, LearnerProfile
from zhijiang.models import AnimationMode
from zhijiang.math_planning import math_capabilities
from zhijiang.visual_planning import PRIMITIVES, CHECKERS, DOMAIN_VALIDATORS, visual_capabilities
from zhijiang.pdf import PDFError, validate_pdf
from zhijiang.pipeline import JobProcessor, JobRunner
from zhijiang.storage import JobStore
from zhijiang.speech_preview import preview_info, preview_path
from zhijiang.runtime_lock import RuntimeLock
from zhijiang.credentials import LocalCredentials,CredentialError,endpoint


STATIC_DIR = Path(__file__).parent / "static"


def configure_source_options(options, fields, previous=None):
    """Keep old API clients compatible; new UI explicitly requests source reading."""
    from zhijiang.generated_media import validate_art_options
    options.narration_mode = fields.get('narration_mode') or (
        'source_verbatim' if options.style_mode == 'subject' else
        previous.narration_mode if previous else 'explanatory')
    options.art = previous.art.model_copy(deep=True) if previous else ArtOptions()
    learner = previous.learner.model_dump() if previous else LearnerProfile().model_dump()
    for key in learner:
        value = fields.get('learner_'+key)
        if value is not None:
            learner[key] = value
    options.learner = LearnerProfile.model_validate(learner)
    if options.learner.enabled and options.narration_mode != 'source_verbatim':
        raise HTTPException(400, '学习计划请使用原文课程模式。')
    if options.learner.enabled and fields.get('mode') == Mode.DEMO:
        raise HTTPException(400, '个性化学习计划需要真实AI模式。')
    for kind in ('image', 'video'):
        service = getattr(options.art, kind)
        new_url = fields.get(kind+'_base_url')
        if new_url is not None and new_url.strip().rstrip('/') != service.base_url.rstrip('/'):
            service.api_key = ''
        for field in ('protocol', 'base_url', 'model', 'api_key'):
            value = fields.get(kind+'_'+field)
            if value is not None and (field != 'api_key' or value):
                setattr(service, field, value.strip())
    if fields.get('art_max_assets') is not None:
        options.art.max_assets = fields['art_max_assets']
    if options.style_mode == 'subject' and options.narration_mode != 'source_verbatim':
        raise HTTPException(400, '自动学科编排需要原文字幕与配音模式。')
    if any(s.protocol != 'disabled' for s in (options.art.image, options.art.video)) and options.narration_mode != 'source_verbatim':
        raise HTTPException(400, '绘图与视频接口用于原文课程流程，请选择原文模式。')
    try:validate_art_options(options.art)
    except ValueError as exc:raise HTTPException(422, str(exc)) from exc
    if options.narration_mode == 'source_verbatim':
        names = [options.model, options.review_model, options.math_model]
        if any('deepseek' in n.lower() for n in names):
            raise HTTPException(400, '当前原文课程仅使用已授权的本地模型，不使用DeepSeek。')
        academy = urlparse(options.base_url).hostname == '58.199.177.109'
        if any((not academy and Settings.model_is_remote(options.provider, options.base_url, n)) or
               options.provider == 'ollama' and n.lower().endswith((':cloud','-cloud')) for n in names if n):
            raise HTTPException(400, '原文课程的教材理解与规划须使用本机或学院服务器模型。')


class CredentialInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    kind:Literal['llm','tts']
    provider:Literal['openai','ollama']='openai'
    base_url:str
    api_key:str=Field(min_length=1,repr=False)

    @field_validator('base_url')
    @classmethod
    def valid_endpoint(cls,value):return endpoint(value)

    @field_validator('api_key')
    @classmethod
    def nonempty_key(cls,value):
        value=value.strip()
        if not value:raise ValueError('请输入密钥。')
        return value


class SpeechPreviewInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    base_url: str = ''
    model: str = ''
    voice: str = ''
    api_key: str = Field(default='', repr=False)
    remote_consent: bool = False
    force: bool = False

    @field_validator('base_url')
    @classmethod
    def valid_endpoint(cls, value):
        return endpoint(value) if value.strip() else ''


def model_options(settings: Settings, provider: str, base_url: str, model: str,
                  api_key: str, prompt: str,credentials: LocalCredentials | None = None) -> GenerationOptions:
    if len(prompt) > 4000:
        raise HTTPException(400, "自定义提示词不能超过 4000 字。")
    if provider and provider != settings.llm_provider and (not base_url.strip() or not model.strip()):
        raise HTTPException(400, "切换模型提供方时，请同时填写对应 API 地址和模型名称。")
    same_destination = (not provider or provider == settings.llm_provider) and (
        not base_url or base_url.strip().rstrip("/") == settings.llm_base_url.rstrip("/"))
    try:
        options = GenerationOptions(
            provider=provider or settings.llm_provider,
            base_url=(base_url or settings.llm_base_url).strip().rstrip("/"),
            model=(model or settings.llm_model).strip(),
            api_key=api_key.strip() or (settings.llm_api_key if same_destination else ""),
            prompt=prompt.strip(),
            semantic_thinking=settings.ollama_semantic_thinking,
        )
    except ValueError as exc:
        raise HTTPException(400, "模型提供方只支持 Ollama 或兼容接口。") from exc
    parsed = urlparse(options.base_url)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise HTTPException(400, "请填写有效的 HTTP(S) 模型 API 地址。")
    if not api_key.strip() and credentials:
        try:options.api_key=credentials.get('llm',options.provider,options.base_url) or options.api_key
        except ValueError as exc:raise HTTPException(400,str(exc)) from exc
        except CredentialError as exc:
            if not options.api_key:raise HTTPException(503,str(exc)) from exc
    if not options.model or (options.provider == "openai" and not options.api_key and
                             not Settings._is_loopback(options.base_url) and parsed.hostname != '58.199.177.109'):
        raise HTTPException(400, "请填写模型名称和所需的 API 密钥。")
    return options


def speech_options(settings: Settings, base_url: str, model: str, voice: str,
                   api_key: str,credentials: LocalCredentials | None = None) -> SpeechOptions:
    same_destination = not base_url or base_url.strip().rstrip("/") == settings.tts_base_url.rstrip("/")
    options = SpeechOptions(
        base_url=(base_url or settings.tts_base_url).strip().rstrip("/"),
        model=(model or settings.tts_model).strip(),
        voice=(voice or settings.tts_voice).strip(),
        api_key=api_key.strip() or (settings.tts_api_key if same_destination else ""),
    )
    parsed = urlparse(options.base_url)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise HTTPException(400, "请填写有效的 HTTP(S) 语音 API 地址。")
    if not api_key.strip() and credentials:
        try:options.api_key=credentials.get('tts','openai',options.base_url) or options.api_key
        except ValueError as exc:raise HTTPException(400,str(exc)) from exc
        except CredentialError as exc:
            if not options.api_key:raise HTTPException(503,str(exc)) from exc
    if not options.model or not options.voice:
        raise HTTPException(400, "请填写语音模型名称和音色。")
    if not Settings._is_loopback(options.base_url) and not options.api_key:
        raise HTTPException(400, "外部语音 API 需要密钥。")
    return options


def create_app(settings: Settings | None = None, runner: JobRunner | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    store = JobStore(settings.data_dir)
    credentials=LocalCredentials(settings.data_dir)
    processor = JobProcessor(settings, store)
    runner = runner or JobRunner(processor)
    @asynccontextmanager
    async def lifespan(_application: FastAPI):
        with RuntimeLock(settings.data_dir / 'service.lock'):
            store.recover_interrupted()
            try:
                yield
            finally:
                runner.shutdown()

    application = FastAPI(title="绘声绘课 LivelyClass", version="0.1.0", lifespan=lifespan)
    from fastapi.exceptions import RequestValidationError
    from fastapi.exception_handlers import request_validation_exception_handler
    from fastapi.responses import JSONResponse
    @application.exception_handler(RequestValidationError)
    async def validation_error(request:Request,error:RequestValidationError):
        if request.url.path.startswith('/api/credentials'):
            return JSONResponse(status_code=422,content={'detail':'密钥类型、API 地址或输入格式无效。'})
        if request.url.path == '/api/speech-preview' and request.method == 'POST':
            return JSONResponse(status_code=422,content={'detail':'语音模型、音色、API 地址或输入格式无效。'})
        return await request_validation_exception_handler(request,error)
    application.state.settings = settings
    application.state.store = store
    application.state.runner = runner
    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @application.get("/", include_in_schema=False)
    def home() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @application.get("/api/config")
    def config() -> dict:
        return {
            "llm_ready": settings.llm_ready,
            "llm_provider": settings.llm_provider,
            "llm_model": settings.llm_model,
            "llm_is_local": settings.llm_is_local,
            "ai_tts_ready": settings.ai_tts_ready,
            "tts_is_local": settings.tts_is_local,
            "max_pdf_bytes": settings.max_pdf_bytes,
            "llm_base_url": settings.llm_base_url,
            "tts_base_url": settings.tts_base_url,
            "tts_model": settings.tts_model,
            "tts_voice": settings.tts_voice,
            "default_voice_mode": "ai" if settings.default_voice_mode == "ai" and settings.ai_tts_ready else "system",
            "speech_preview": preview_info(settings),
            "speech_previews": {'ai':preview_info(settings),'system':preview_info(settings,VoiceMode.SYSTEM)},
            "credential_persistence": credentials.supported,
            "demo_notice": "演示模式的知识选择和讲稿由确定性规则生成，并非 AI 生成。",
            "ppt_templates": [template.public() for template in TEMPLATES.values()],
            "default_narration_mode": "source_verbatim",
            "subject_templates": __import__('zhijiang.subject_routing', fromlist=['FAMILIES']).FAMILIES,
            "ollama_semantic_thinking": settings.ollama_semantic_thinking,
            "ollama_thinking_level": settings.ollama_thinking_level,
            "math_animation": math_capabilities(),
            "visual_animation": {**visual_capabilities(), "subject_restriction": None,
                "topics": None, "primitives": list(PRIMITIVES),
                "representations": ['geometry','process','relationship','comparison','source_figure'],
                "numeric_checks": sorted(CHECKERS), "domain_validators": sorted(DOMAIN_VALIDATORS),
                "verification_scope": "按表达类型核对来源摘录、关系引用或几何与数值关系；教学含义和领域事实需复核"},
        }

    @application.get('/api/speech-preview')
    def speech_preview(mode: VoiceMode = VoiceMode.AI) -> FileResponse:
        if preview_info(settings,mode) is None:
            raise HTTPException(404, '当前配置还没有匹配的试听音频。')
        if mode==VoiceMode.SYSTEM:
            from zhijiang.speech_preview import ensure_system_preview
            from zhijiang.speech import SpeechError
            try:ensure_system_preview(settings)
            except SpeechError as exc:raise HTTPException(503,str(exc)) from exc
        return FileResponse(preview_path(settings,mode), media_type='audio/wav',
                            headers={'Cache-Control': 'no-cache'})

    def local_request(request):
        if request.url.hostname not in {'localhost','127.0.0.1','::1','testserver'}:
            raise HTTPException(403,'密钥保存仅允许从本机网站操作。')
        if request.client and request.client.host not in {'127.0.0.1','::1','testclient','localhost'}:
            raise HTTPException(403,'密钥保存仅允许从本机网站操作。')
        origin=request.headers.get('origin')
        if origin and origin.rstrip('/')!=str(request.base_url).rstrip('/'):
            raise HTTPException(403,'密钥操作须来自本机网站。')

    @application.get('/api/speech-preview/selected')
    def selected_speech_preview(request: Request, response: Response, base_url: str, model: str, voice: str):
        local_request(request)
        response.headers['Cache-Control'] = 'no-store'
        from zhijiang.speech_preview import selected_preview_info
        try:
            if not model.strip() or not voice.strip():
                raise ValueError('请填写语音模型和音色。')
            return {'preview': selected_preview_info(settings, base_url, model, voice)}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @application.post('/api/speech-preview')
    def generate_speech_preview(request: Request, payload: SpeechPreviewInput):
        local_request(request)
        options = speech_options(settings, payload.base_url, payload.model, payload.voice,
                                 payload.api_key, credentials)
        if not Settings._is_loopback(options.base_url) and not payload.remote_consent:
            raise HTTPException(400, '请先同意将原创试听文本发送到所选语音服务。')
        from zhijiang.speech_preview import generate_ai_preview
        from zhijiang.speech import SpeechError
        try:
            return generate_ai_preview(settings, options, force=payload.force)
        except SpeechError as exc:
            raise HTTPException(503, str(exc)) from exc

    @application.get('/api/speech-previews/{preview_id}')
    def selected_speech_audio(preview_id: Annotated[str, PathParameter(pattern='^[0-9a-f]{64}$')]):
        from zhijiang.speech_preview import cached_preview_info, selected_preview_path
        if cached_preview_info(settings, preview_id) is None:
            raise HTTPException(404, '该声线试听不存在或需要重新生成。')
        return FileResponse(selected_preview_path(settings, preview_id), media_type='audio/wav',
                            headers={'Cache-Control': 'no-store'})

    @application.get('/api/credentials/status')
    def credential_status(request:Request,response:Response,kind:Literal['llm','tts'],provider:Literal['openai','ollama'],base_url:str):
        local_request(request)
        response.headers['Cache-Control']='no-store'
        try:saved=bool(credentials.get(kind,provider,endpoint(base_url)))
        except ValueError as exc:raise HTTPException(422,str(exc)) from exc
        except CredentialError as exc:raise HTTPException(503,str(exc)) from exc
        configured=(kind=='llm' and provider==settings.llm_provider and base_url.rstrip('/')==settings.llm_base_url.rstrip('/')
                    and bool(settings.llm_api_key)) or (kind=='tts' and base_url.rstrip('/')==settings.tts_base_url.rstrip('/')
                    and bool(settings.tts_api_key))
        return {'saved':saved,'configured':bool(configured),'supported':credentials.supported}

    @application.post('/api/credentials')
    def save_credential(request:Request,payload:CredentialInput):
        local_request(request)
        try:credentials.save(payload.kind,payload.provider,payload.base_url,payload.api_key)
        except CredentialError as exc:raise HTTPException(503,str(exc)) from exc
        return {'saved':True}

    @application.delete('/api/credentials',status_code=204)
    def remove_credential(request:Request,kind:Literal['llm','tts'],provider:Literal['openai','ollama'],base_url:str):
        local_request(request)
        try:credentials.remove(kind,provider,base_url)
        except ValueError as exc:raise HTTPException(422,str(exc)) from exc
        except CredentialError as exc:raise HTTPException(503,str(exc)) from exc

    @application.get('/api/ppt-templates')
    def ppt_templates() -> list[dict]:
        return [template.public() for template in TEMPLATES.values()]

    @application.get('/api/ppt-templates/{template_id}/preview')
    def ppt_template_preview(template_id: PPTTemplate,
            style_mode: Literal['template','adaptive','comic','subject']='template') -> Response:
        if style_mode == 'comic':
            from zhijiang.drama_renderer import drama_preview_svg
            from zhijiang.presentation_templates import get_template
            get_template(template_id)
            return Response(drama_preview_svg(), media_type='image/svg+xml')
        return Response(template_preview_svg(template_id), media_type='image/svg+xml')

    @application.post("/api/jobs", status_code=202)
    async def create_job(
        file: Annotated[UploadFile, File()],
        rights_confirmed: Annotated[bool, Form()],
        mode: Annotated[Mode, Form()] = Mode.DEMO,
        voice_mode: Annotated[VoiceMode, Form()] = VoiceMode.SYSTEM,
        remote_consent: Annotated[bool, Form()] = False,
        llm_provider: Annotated[str, Form()] = "",
        llm_base_url: Annotated[str, Form()] = "",
        llm_model: Annotated[str, Form()] = "",
        llm_review_model: Annotated[str, Form(max_length=120)] = "",
        llm_math_model: Annotated[str, Form(max_length=120)] = "",
        llm_api_key: Annotated[str, Form()] = "",
        custom_prompt: Annotated[str, Form()] = "",
        animation_mode: Annotated[AnimationMode, Form()] = "auto",
        ppt_template: Annotated[PPTTemplate, Form()] = 'classic',
        style_mode: Annotated[Literal['template','adaptive','comic','subject'], Form()] = 'template',
        narration_mode: Annotated[Literal['explanatory','source_verbatim'] | None, Form()] = None,
        learner_enabled: Annotated[bool | None, Form()] = None,
        learner_background: Annotated[str | None, Form(max_length=1000)] = None,
        learner_goal: Annotated[str | None, Form(max_length=1000)] = None,
        learner_days: Annotated[int | None, Form(ge=1, le=30)] = None,
        learner_minutes_per_day: Annotated[int | None, Form(ge=5, le=240)] = None,
        image_protocol: Annotated[Literal['disabled','images','jimeng'] | None, Form()] = None,
        image_base_url: Annotated[str | None, Form(max_length=500)] = None,
        image_model: Annotated[str | None, Form(max_length=160)] = None,
        image_api_key: Annotated[str | None, Form(max_length=2000)] = None,
        video_protocol: Annotated[Literal['disabled','seedance'] | None, Form()] = None,
        video_base_url: Annotated[str | None, Form(max_length=500)] = None,
        video_model: Annotated[str | None, Form(max_length=160)] = None,
        video_api_key: Annotated[str | None, Form(max_length=2000)] = None,
        art_max_assets: Annotated[int | None, Form(ge=1, le=200)] = None,
        use_illustrations: Annotated[bool, Form()] = True,
        content_checks: Annotated[str, Form(pattern='^(warn|strict)$')] = 'warn',
        tts_base_url: Annotated[str, Form()] = "",
        tts_model: Annotated[str, Form()] = "",
        tts_voice: Annotated[str, Form()] = "",
        tts_api_key: Annotated[str, Form()] = "",
    ) -> dict:
        if not rights_confirmed:
            raise HTTPException(400, "请先确认拥有资料使用权。")
        options = model_options(settings, llm_provider, llm_base_url, llm_model,
                                llm_api_key, custom_prompt,credentials) if mode == Mode.AI else GenerationOptions()
        options.review_model=llm_review_model.strip() if mode==Mode.AI else ''
        options.math_model=llm_math_model.strip() if mode==Mode.AI else ''
        if mode == Mode.DEMO and animation_mode in {"math","visual","geometry"}:
            raise HTTPException(400, "数学推演需要选择真实 AI 模式。")
        options.animation_mode = animation_mode if mode == Mode.AI else "basic"
        options.ppt_template = ppt_template
        if style_mode != 'template' and mode != Mode.AI:
            raise HTTPException(400, '学科自适应与漫剧风格需要真实 AI 模式。')
        options.style_mode = style_mode
        configure_source_options(options, locals())
        options.use_illustrations = use_illustrations
        options.content_checks = content_checks
        if options.animation_mode in {"math","visual","geometry"} and (options.content_checks=='strict' or options.animation_mode!='visual') and not (
                math_capabilities() if options.animation_mode=='math' else visual_capabilities())["ready"]:
            capability=math_capabilities() if options.animation_mode=='math' else visual_capabilities()
            raise HTTPException(400, "教学动画环境未就绪：" + capability["reason"])
        if options.animation_mode=='geometry' and not visual_capabilities().get('geometry_ready'):
            raise HTTPException(400, "数学对象推演环境未就绪：" + visual_capabilities().get('geometry_reason','缺少 TeX'))
        voice_options = speech_options(settings, tts_base_url, tts_model, tts_voice,
                                       tts_api_key,credentials) if voice_mode == VoiceMode.AI else SpeechOptions()
        external_text = mode == Mode.AI and any(Settings.model_is_remote(options.provider,options.base_url,name)
            for name in [options.model,options.review_model,options.math_model] if name)
        external_voice = voice_mode == VoiceMode.AI and not Settings._is_loopback(voice_options.base_url)
        from zhijiang.generated_media import art_is_remote
        if (external_text or external_voice or art_is_remote(options.art)) and not remote_consent:
            raise HTTPException(400, "调用外部服务前须同意发送提取文本、数学模式的教材页图像或讲稿。")
        filename = (file.filename or "source.pdf").replace("\\", "/").split("/")[-1]
        if not filename.lower().endswith(".pdf"):
            raise HTTPException(400, "仅支持 PDF 文件。")
        chunks = []
        total = 0
        while chunk := await file.read(1024 * 1024):
            total += len(chunk)
            if total > settings.max_pdf_bytes:
                raise HTTPException(413, "PDF 文件超过上传上限。")
            chunks.append(chunk)
        content = b"".join(chunks)
        try:
            validate_pdf(content)
        except PDFError as exc:
            raise HTTPException(400, str(exc)) from exc
        job = store.create(filename, mode, voice_mode, rights_confirmed, remote_consent,
                           content, options, voice_options)
        runner.submit(job["id"])
        return job

    @application.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404, "任务不存在。")
        return job

    @application.get("/api/jobs/{job_id}/lesson")
    def get_lesson(job_id: str) -> dict:
        if store.get(job_id) is None:
            raise HTTPException(404, "任务不存在。")
        lesson = store.lesson(job_id)
        if lesson is None:
            raise HTTPException(409, "课程尚未生成。")
        return lesson.model_dump(mode="json")

    @application.get("/api/jobs/{job_id}/video")
    def get_video(job_id: str) -> FileResponse:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404, "任务不存在。")
        path = store.jobs_dir / job_id / "lesson.mp4"
        if job["status"] != JobStatus.COMPLETED or not path.is_file():
            raise HTTPException(409, "视频尚未生成。")
        return FileResponse(path, media_type="video/mp4", filename="LivelyClass-lesson.mp4",
                            content_disposition_type="inline")

    @application.get("/api/jobs/{job_id}/presentation")
    def get_presentation(job_id: str) -> FileResponse:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404, "任务不存在。")
        path = store.jobs_dir / job_id / "lesson.pptx"
        if job["status"] != JobStatus.COMPLETED or not path.is_file():
            raise HTTPException(409, "PPT 课件尚未生成。")
        return FileResponse(
            path,
            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            filename="LivelyClass-lesson.pptx",
        )

    @application.get("/api/jobs/{job_id}/math-scenes")
    def get_math_scenes(job_id: str) -> FileResponse:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404, "任务不存在。")
        path = store.jobs_dir / job_id / "math-scenes.json"
        if job["status"] != JobStatus.COMPLETED or not path.is_file():
            raise HTTPException(409, "数学场景数据尚未生成。")
        return FileResponse(path, media_type="application/json", filename="math-scenes.json")

    @application.get("/api/jobs/{job_id}/scenes")
    def get_scenes(job_id: str) -> FileResponse:
        job=store.get(job_id)
        if job is None:
            raise HTTPException(404,"任务不存在。")
        folder=store.jobs_dir/job_id
        path=folder/"scene-data.json"
        if not path.is_file():
            path=folder/"math-scenes.json"
        if job["status"]!=JobStatus.COMPLETED or not path.is_file():
            raise HTTPException(409,"教学场景数据尚未生成。")
        return FileResponse(path,media_type="application/json",filename="teaching-scenes.json")

    @application.get('/api/jobs/{job_id}/subtitles')
    def get_source_subtitles(job_id: str, format: Literal['srt','vtt','json']='srt'):
        return source_artifact(job_id, 'subtitles.'+format,
            {'srt':'application/x-subrip','vtt':'text/vtt','json':'application/json'}[format])

    @application.get('/api/jobs/{job_id}/transcript')
    def get_source_transcript(job_id: str):
        return source_artifact(job_id, 'source-transcript.txt', 'text/plain')

    @application.get('/api/jobs/{job_id}/source-coverage')
    def get_source_coverage(job_id: str):
        return source_artifact(job_id, 'source-coverage.json', 'application/json')

    @application.get('/api/jobs/{job_id}/learning-plan')
    def get_learning_plan(job_id: str, format: Literal['md','json']='md'):
        return source_artifact(job_id, 'learning-plan.'+format,
            'application/json' if format == 'json' else 'text/markdown')

    def source_artifact(job_id, name, media_type):
        job=store.get(job_id)
        if job is None:raise HTTPException(404, '任务不存在。')
        path=store.jobs_dir/job_id/name
        if job['status']!=JobStatus.COMPLETED or not path.is_file():
            raise HTTPException(409, '请求的课程附件尚未完成。')
        return FileResponse(path, media_type=media_type, filename=name)

    @application.post("/api/jobs/{job_id}/retry", status_code=202)
    def retry_job(job_id: str, llm_api_key: Annotated[str, Form()] = "",
                  tts_api_key: Annotated[str, Form()] = "",
                  mode: Annotated[Mode | None, Form()] = None,
                  voice_mode: Annotated[VoiceMode | None, Form()] = None,
                  llm_provider: Annotated[str | None, Form()] = None,
                  llm_base_url: Annotated[str | None, Form()] = None,
                  llm_model: Annotated[str | None, Form()] = None,
                  llm_review_model: Annotated[str | None, Form(max_length=120)] = None,
                  llm_math_model: Annotated[str | None, Form(max_length=120)] = None,
                  custom_prompt: Annotated[str | None, Form()] = None,
                  animation_mode: Annotated[AnimationMode | None, Form()] = None,
                  ppt_template: Annotated[PPTTemplate | None, Form()] = None,
                  style_mode: Annotated[Literal['template','adaptive','comic','subject'] | None, Form()] = None,
                  narration_mode: Annotated[Literal['explanatory','source_verbatim'] | None, Form()] = None,
                  learner_enabled: Annotated[bool | None, Form()] = None,
                  learner_background: Annotated[str | None, Form(max_length=1000)] = None,
                  learner_goal: Annotated[str | None, Form(max_length=1000)] = None,
                  learner_days: Annotated[int | None, Form(ge=1, le=30)] = None,
                  learner_minutes_per_day: Annotated[int | None, Form(ge=5, le=240)] = None,
                  image_protocol: Annotated[Literal['disabled','images','jimeng'] | None, Form()] = None,
                  image_base_url: Annotated[str | None, Form(max_length=500)] = None,
                  image_model: Annotated[str | None, Form(max_length=160)] = None,
                  image_api_key: Annotated[str | None, Form(max_length=2000)] = None,
                  video_protocol: Annotated[Literal['disabled','seedance'] | None, Form()] = None,
                  video_base_url: Annotated[str | None, Form(max_length=500)] = None,
                  video_model: Annotated[str | None, Form(max_length=160)] = None,
                  video_api_key: Annotated[str | None, Form(max_length=2000)] = None,
                  art_max_assets: Annotated[int | None, Form(ge=1, le=200)] = None,
                  use_illustrations: Annotated[bool | None, Form()] = None,
                  content_checks: Annotated[str | None, Form(pattern='^(warn|strict)$')] = None,
                  tts_base_url: Annotated[str | None, Form()] = None,
                  tts_model: Annotated[str | None, Form()] = None,
                  tts_voice: Annotated[str | None, Form()] = None,
                  replace_settings: Annotated[bool, Form()] = False,
                  remote_consent: Annotated[bool | None, Form()] = None) -> dict:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404, "任务不存在。")
        if job["status"] != JobStatus.FAILED:
            raise HTTPException(409, "只有失败任务可以重新生成。")
        mode = mode or Mode(job["mode"])
        voice_mode = voice_mode or VoiceMode(job["voice_mode"])
        if mode == Mode.DEMO and animation_mode in {"math", "visual", "geometry"}:
            raise HTTPException(400, "教学过程与数学推演需要选择真实 AI 模式。")
        options = store.get_options(job_id)
        if mode == Mode.AI:
            provider = options.provider if llm_provider is None else llm_provider
            base_url = options.base_url if llm_base_url is None else llm_base_url
            same_destination = (base_url.strip().rstrip("/") == options.base_url.rstrip("/")
                                and provider == options.provider)
            options = model_options(settings, provider if options.base_url else llm_provider or "",
                base_url, options.model if llm_model is None else llm_model,
                llm_api_key or (options.api_key if same_destination else ""),
                ("" if replace_settings else options.prompt) if custom_prompt is None else custom_prompt,credentials)
            options.review_model=(llm_review_model.strip() if llm_review_model is not None else
                ('' if replace_settings or not same_destination else store.get_options(job_id).review_model))
            options.math_model=(llm_math_model.strip() if llm_math_model is not None else
                ('' if replace_settings or not same_destination else store.get_options(job_id).math_model))
            options.animation_mode = animation_mode or store.get_options(job_id).animation_mode
            options.content_checks = content_checks or store.get_options(job_id).content_checks
            if options.animation_mode in {"math", "visual", "geometry"} and (options.content_checks=='strict' or options.animation_mode!='visual') and not (
                    math_capabilities() if options.animation_mode=='math' else visual_capabilities())["ready"]:
                capability=math_capabilities() if options.animation_mode=='math' else visual_capabilities()
                raise HTTPException(400, "教学动画环境未就绪：" + capability["reason"])
            if options.animation_mode=='geometry' and not visual_capabilities().get('geometry_ready'):
                raise HTTPException(400, "数学对象推演环境未就绪：" + visual_capabilities().get('geometry_reason','缺少 TeX'))
        else:
            options = GenerationOptions(animation_mode="basic")
        options.ppt_template = ppt_template or store.get_options(job_id).ppt_template
        options.style_mode = style_mode or store.get_options(job_id).style_mode
        previous_options = store.get_options(job_id)
        configure_source_options(options, locals(), previous_options)
        if options.style_mode != 'template' and mode != Mode.AI:
            raise HTTPException(400, '学科自适应与漫剧风格需要真实 AI 模式。')
        options.use_illustrations = (store.get_options(job_id).use_illustrations
                                    if use_illustrations is None else use_illustrations)
        options.content_checks = content_checks or store.get_options(job_id).content_checks
        voice_options = store.get_speech_options(job_id)
        if voice_mode == VoiceMode.AI:
            base_url = voice_options.base_url if tts_base_url is None else tts_base_url
            same_destination = base_url.strip().rstrip("/") == voice_options.base_url.rstrip("/")
            voice_options = speech_options(settings, base_url,
                voice_options.model if tts_model is None else tts_model,
                voice_options.voice if tts_voice is None else tts_voice,
                tts_api_key or (voice_options.api_key if same_destination else ""),credentials)
        else:
            voice_options = SpeechOptions()
        external_text = mode == Mode.AI and any(Settings.model_is_remote(options.provider,options.base_url,name)
            for name in [options.model,options.review_model,options.math_model] if name)
        external_voice = (voice_mode == VoiceMode.AI
                          and not Settings._is_loopback(voice_options.base_url))
        from zhijiang.generated_media import art_is_remote
        external_art = art_is_remote(options.art)
        consent = job["remote_consent"] if remote_consent is None else remote_consent
        changed_external_destination = ((external_text and (options.base_url != job["model_settings"].get("base_url") or
                                         options.model != job["model_settings"].get("model") or
                                         options.review_model != job["model_settings"].get("review_model",'') or
                                         options.math_model != job["model_settings"].get("math_model",'')))
                                        or (external_voice and voice_options.base_url != job["voice_settings"].get("base_url")))
        changed_external_destination = changed_external_destination or (external_art and
            options.art.model_dump() != previous_options.art.model_dump())
        if (external_text or external_voice or external_art) and (not consent or (changed_external_destination and remote_consent is not True)):
            raise HTTPException(400, "调用外部服务前须重新提交并同意发送文本及数学模式的教材页图像。")
        if not (store.jobs_dir / job_id / "source.pdf").is_file():
            raise HTTPException(409, "原始 PDF 已丢失，请重新上传。")
        if not store.retry(job_id, mode=mode, voice_mode=voice_mode, options=options,
                           speech_options=voice_options, remote_consent=consent):
            raise HTTPException(409, "任务状态已改变，请刷新页面。")
        store.clear_api_key(job_id)
        store.set_api_key(job_id, options.api_key)
        store.set_tts_api_key(job_id, voice_options.api_key)
        store.set_art_api_keys(job_id, options.art)
        runner.submit(job_id)
        return store.get(job_id)

    @application.delete("/api/jobs/{job_id}", status_code=204)
    def delete_job(job_id: str) -> None:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404, "任务不存在。")
        if job["status"] in (JobStatus.QUEUED, JobStatus.RUNNING):
            raise HTTPException(409, "任务仍在处理中，请完成后再删除。")
        folder = (store.jobs_dir / job_id).resolve()
        root = store.jobs_dir.resolve()
        if folder == root or not folder.is_relative_to(root):
            raise HTTPException(400, "无效任务路径。")
        shutil.rmtree(folder, ignore_errors=False)
        store.delete(job_id)

    return application


class LazyApplication:
    """Do not open the persistent job store merely by importing this module."""

    def __init__(self) -> None:
        self._app: FastAPI | None = None

    async def __call__(self, scope: dict, receive, send) -> None:
        if self._app is None:
            self._app = create_app()
        await self._app(scope, receive, send)


app = LazyApplication()
