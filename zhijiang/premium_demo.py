"""Explicit, isolated premium-demo contracts; production routing is unchanged.

The demo intentionally permits the official DeepSeek API. It never relaxes the
source-course service's normal local-only policy or accepts an arbitrary URL.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import time
from datetime import datetime, timezone
from typing import Literal

import httpx
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field


class DemoBlocked(RuntimeError):
    """A missing real dependency, not a successful or simulated generation."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + '.pending')
    pending.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    pending.replace(path)


def load_private_key(path: Path | None = None) -> str:
    """Read a caller-selected private config; never echo its contents."""
    key = os.environ.get('DEEPSEEK_API_KEY') or os.environ.get('ZHIJIANG_LLM_API_KEY')
    if path:
        path = path.resolve(strict=True)
        if path.suffix.lower() == '.json':
            values = json.loads(path.read_text(encoding='utf-8-sig'))
        else:
            values = dotenv_values(path)
        key = values.get('DEEPSEEK_API_KEY') or values.get('ZHIJIANG_LLM_API_KEY') or key
    if not key:
        from zhijiang.credentials import LocalCredentials
        store = LocalCredentials(Path('data').resolve())
        key = store.get('llm', 'openai', 'https://api.deepseek.com') or store.get(
            'llm', 'openai', 'https://api.deepseek.com/v1')
    if not isinstance(key, str) or not key.strip():
        raise DemoBlocked('DeepSeek 官方密钥未配置；请提供本机私有配置路径。')
    return key.strip()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Shot(StrictModel):
    unit_id: str
    scene_title: str = Field(min_length=2, max_length=30)
    subject: str = Field(min_length=5, max_length=500)
    action: str = Field(min_length=2, max_length=300)
    framing: str = Field(min_length=2, max_length=300)
    continuity: str = Field(min_length=2, max_length=500)
    characters: list[str] = Field(max_length=3)
    reference_roles: list[Literal['landscape', 'washerwoman', 'boat']] = Field(max_length=3)
    reuse_unit_id: str | None = None
    keywords: list[str] = Field(max_length=3)


class StoryPlan(StrictModel):
    title: str
    shots: list[Shot] = Field(min_length=8, max_length=20)


class ExpandedCaption(StrictModel):
    caption: str = Field(min_length=400, max_length=6000)
    reference_bindings: list[str] = Field(default_factory=list, max_length=5)


class SourceIdea(StrictModel):
    unit_ids: list[str] = Field(min_length=1)
    meaning: str = Field(min_length=4, max_length=700)


class SourceUnderstanding(StrictModel):
    subject: str
    unit_order: list[str]
    ideas: list[SourceIdea] = Field(min_length=2, max_length=12)


class DeckPage(StrictModel):
    unit_ids: list[str] = Field(min_length=1)
    title: str = Field(min_length=2, max_length=30)
    keywords: list[str] = Field(min_length=1, max_length=3)
    visual: Literal['circle_line', 'textbook_example', 'poem_illustration']


class DeckPlan(StrictModel):
    pages: list[DeckPage] = Field(min_length=4, max_length=22)


class MathBeat(StrictModel):
    action: str
    duration: float = Field(ge=.25, le=600)


class MathPlan(StrictModel):
    schema_version: Literal[1]
    scene: Literal['circle_line', 'textbook_example']
    source: dict
    parameters: dict
    beats: list[MathBeat]


class StudyDay(StrictModel):
    day: int = Field(ge=1, le=30)
    title: str = Field(min_length=2, max_length=60)
    unit_ids: list[str] = Field(min_length=1)
    activities: list[str] = Field(min_length=2, max_length=6)
    self_check: str = Field(min_length=4, max_length=500)


class StudyPlan(StrictModel):
    days: list[StudyDay] = Field(min_length=1, max_length=30)


class ReviewItem(StrictModel):
    unit_id: str
    supported: bool
    source_meaning: str
    observed: str
    reason: str


class Review(StrictModel):
    items: list[ReviewItem] = Field(min_length=1)


def validate_source(source: dict) -> None:
    units = source['units']
    ids = [u['unit_id'] for u in units]
    if len(ids) != len(set(ids)) or not all(isinstance(i, str) and i for i in ids):
        raise ValueError('来源单元编号必须唯一。')
    if source.get('unit_separator', '\n').join(u['text'] for u in units) != source['source_text']:
        raise ValueError('来源单元未逐字覆盖冻结全文。')


def validate_story(plan: StoryPlan, source: dict) -> None:
    validate_source(source)
    ids = [u['unit_id'] for u in source['units']]
    observed = [s.unit_id for s in plan.shots]
    if observed != ids:
        raise ValueError('分镜必须按顺序恰好覆盖所有原文单元。')
    earlier: set[str] = set()
    for shot in plan.shots:
        if shot.reuse_unit_id is not None and shot.reuse_unit_id not in earlier:
            raise ValueError('复用画面必须引用已经出现的来源单元。')
        earlier.add(shot.unit_id)


def validate_study(plan: StudyPlan, source: dict, days: int) -> None:
    ids = {u['unit_id'] for u in source['units']}
    if [d.day for d in plan.days] != list(range(1, days + 1)):
        raise ValueError('学习日必须连续且符合指定天数。')
    used = {uid for d in plan.days for uid in d.unit_ids}
    if used != ids:
        raise ValueError('学习计划缺少真实来源单元，或引用了不存在的单元。')


def apply_display_overrides(deck: dict, changes: list[dict], case: str) -> dict:
    """Explicit human editorial fixes, preserving the untouched model plan."""
    import copy
    result = copy.deepcopy(deck)
    for change in changes:
        if change['case'] != case:
            continue
        matches = [p for p in result['pages'] if p['unit_ids'] == change['unit_ids']]
        if len(matches) != 1:
            raise ValueError('课件编辑没有唯一对应的实际来源单元。')
        page = matches[0]
        if any(page.get(key) != val for key, val in change['old'].items()):
            raise ValueError('课件原稿已变化，不能套用旧的编辑记录。')
        if set(change['new']) - {'title', 'keywords'}:
            raise ValueError('课件编辑只能调整标题和关键词，不得更改来源或音轨。')
        page.update(change['new'])
    return result


class DemoDeepSeek:
    """Role-specific JSON and vision requests with bounded repair and audit.

    Only this explicitly invoked demo class permits cloud inference. Headers and
    credentials are excluded from disk; source/response records stay in ignored
    private demo data, never in the tracked repository or deliverable bundle.
    """
    endpoint = 'https://api.deepseek.com'
    model = 'deepseek-flash'

    def __init__(self, api_key: str, audit_dir: Path, *, allow_official_api: bool,
                 http_client: httpx.Client | None = None):
        if allow_official_api is not True:
            raise DemoBlocked('本配置未显式允许 DeepSeek 官方 API。')
        self._key = api_key
        self.audit_dir = audit_dir
        self._owned = http_client is None
        self.client = http_client or httpx.Client(timeout=httpx.Timeout(
            connect=30, read=900, write=90, pool=30), follow_redirects=False)

    def close(self):
        if self._owned:
            self.client.close()

    def capabilities(self) -> dict:
        r = self.client.get(self.endpoint + '/models', headers={
            'Authorization': 'Bearer ' + self._key})
        if r.status_code != 200:
            raise DemoBlocked(f'DeepSeek 模型预检失败（HTTP {r.status_code}）。')
        models = r.json().get('data', [])
        info = next((m for m in models if m.get('id') == self.model), None)
        if not info:
            raise DemoBlocked('官方服务未列出 deepseek-flash；不能静默替换模型。')
        if not {'text', 'image'}.issubset(info.get('input_modalities', [])):
            raise DemoBlocked('实际服务没有确认所需的文字与图像输入能力。')
        if not {'high', 'max'}.issubset(info.get('effort', {}).get('supported_levels', [])):
            raise DemoBlocked('实际服务没有确认 high/max 思考档位。')
        write_json(self.audit_dir / 'loaded-model.json', info)
        write_json(self.audit_dir / 'environment' / (str(time.time_ns()) + '.json'),
                   {'checked_at': datetime.now(timezone.utc).isoformat(), 'model': info})
        return info

    def generate(self, schema: type[BaseModel], stage: str, instruction: str,
                 material: dict, *, effort: Literal['high', 'max'] = 'high',
                 images: list[bytes] | None = None) -> BaseModel:
        system = instruction + '\n仅输出一个完整 JSON 对象。JSON Schema：\n' + json.dumps(
            schema.model_json_schema(), ensure_ascii=False)
        text = json.dumps(material, ensure_ascii=False)
        content: str | list = text
        if images:
            content = [{'type': 'text', 'text': text}, *[{
                'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' +
                    base64.b64encode(im).decode('ascii')}} for im in images]]
        messages = [{'role': 'system', 'content': system}, {'role': 'user', 'content': content}]
        # The request hash includes the exact prompts and images, but no API key.
        budget = 65536 if effort == 'max' else 16384
        fingerprint = digest(json.dumps([self.model, effort, budget, messages], ensure_ascii=False).encode())
        base_folder = self.audit_dir / stage / fingerprint
        cached = base_folder / 'validated.json'
        if cached.exists():
            return schema.model_validate_json(cached.read_text(encoding='utf-8'))
        folder = base_folder / ('run-' + str(time.time_ns()))
        for attempt in range(1, 3):
            payload = {'model': self.model, 'messages': messages,
                       'thinking': {'type': 'enabled'}, 'reasoning_effort': effort,
                       'response_format': {'type': 'json_object'}, 'max_tokens': budget}
            write_json(folder / f'request-{attempt}.json', payload)
            started = time.monotonic()
            try:
                r = self.client.post(self.endpoint + '/chat/completions', headers={
                    'Authorization': 'Bearer ' + self._key}, json=payload)
            except httpx.HTTPError as exc:
                write_json(folder / f'failure-{attempt}.json', {'kind': type(exc).__name__})
                raise DemoBlocked('DeepSeek 网络请求未完成；未自动重复提交。') from None
            write_json(folder / f'metrics-{attempt}.json', {'http_status': r.status_code,
                'elapsed_seconds': time.monotonic() - started, 'model': self.model, 'effort': effort})
            if r.status_code != 200:
                raise DemoBlocked(f'DeepSeek 请求失败（HTTP {r.status_code}）；未自动重试。')
            result = r.json()
            write_json(folder / f'response-{attempt}.json', result)
            choice = result.get('choices', [{}])[0]
            if choice.get('finish_reason') == 'length':
                raise DemoBlocked(f'{stage} 输出预算耗尽，未产生完整结论；原响应保留，不自动重复同一预算。')
            try:
                choice = result['choices'][0]
                if choice.get('finish_reason') != 'stop':
                    raise ValueError('incomplete_finish')
                answer = choice['message']['content']
                if not isinstance(answer, str) or not answer.strip():
                    raise ValueError('empty_content')
                validated = schema.model_validate_json(answer)
            except (KeyError, IndexError, TypeError, ValueError):
                if attempt == 2:
                    raise DemoBlocked(f'{stage} 两次输出未通过完整 JSON 契约；原响应已保留。') from None
                messages = [*messages, {'role': 'user', 'content':
                    '上次响应未通过完整 JSON Schema。重新输出完整对象，保留全部来源单元，不要省略字段。'}]
                continue
            write_json(cached, validated.model_dump(mode='json'))
            return validated
        raise AssertionError('unreachable')
