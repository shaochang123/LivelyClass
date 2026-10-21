"""Local reference-conditioned Chinese speech, isolated from the main environment."""
from __future__ import annotations

import gc
import hashlib
import io
import json
import logging
import os
import re
import threading
import time
import wave
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
from dotenv import dotenv_values
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

MODEL_NAME = 'qwen3-tts-0.6b-base'
SUPPORTED_MODELS = {MODEL_NAME, 'qwen3-tts-1.7b-base'}
LOGGER = logging.getLogger('uvicorn.error')


@dataclass(frozen=True)
class VoiceSettings:
    model_dir: Path = Path('data/models/qwen3-tts-0.6b-base')
    reference_file: Path = Path('data/voice-reference/voice.json')
    device: str = 'auto'
    idle_seconds: float = 60
    codec_device: str = 'cpu'
    quantization: str = 'none'
    model_name: str = MODEL_NAME

    @classmethod
    def from_env(cls):
        local_file = Path(os.getenv('ZHIJIANG_ENV_FILE', '.env.local'))
        local = dotenv_values(local_file) if local_file.is_file() else {}
        def value(key, default):
            return (os.getenv(key, local.get(key)) or default).strip()
        return cls(
            Path(value('ZHIJIANG_QWEN_TTS_MODEL_DIR', 'data/models/qwen3-tts-0.6b-base')),
            Path(value('ZHIJIANG_QWEN_TTS_REFERENCE', 'data/voice-reference/voice.json')),
            value('ZHIJIANG_QWEN_TTS_DEVICE', 'auto'),
            float(value('ZHIJIANG_QWEN_TTS_IDLE_SECONDS', '60')),
            value('ZHIJIANG_QWEN_TTS_CODEC_DEVICE', 'cpu'),
            value('ZHIJIANG_QWEN_TTS_QUANTIZATION', 'none'),
            value('ZHIJIANG_TTS_MODEL', MODEL_NAME),
        )


class BackendError(RuntimeError):
    pass


def check_generation_budget(code_batches, budget: int):
    # The pinned SDK strips EOS before returning codes. A full budget means
    # generation stopped at the limit, so decoding would risk partial speech.
    if any(codes.shape[0] >= budget - 1 for codes in code_batches):
        raise BackendError('语音生成达到长度上限，未提供截断音频；请缩短讲稿或更换清晰参考段。')


def quantize_talker_layers(layers, device):
    """Quantize only language transformer Linear layers, not the voice codec."""
    import torch
    from bitsandbytes.nn import Linear8bitLt
    count = 0
    def replace(module):
        nonlocal count
        for name, child in list(module.named_children()):
            if isinstance(child, torch.nn.Linear):
                target = Linear8bitLt(child.in_features, child.out_features,
                                      bias=child.bias is not None, has_fp16_weights=False, threshold=6)
                target.load_state_dict(child.state_dict())
                setattr(module, name, target.to(device))
                count += 1
            else:
                replace(child)
    replace(layers)
    if not count:
        raise BackendError('当前语音模型结构无法应用指定量化。')
    return count


def validate_model_identity(settings: VoiceSettings):
    try:
        config = json.loads((settings.model_dir / 'config.json').read_text(encoding='utf-8'))
        expected = {'qwen3-tts-0.6b-base': '0b6', 'qwen3-tts-1.7b-base': '1b7'}[settings.model_name]
        if config.get('tts_model_size') != expected or config.get('tts_model_type') != 'base':
            raise ValueError('model identity')
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise BackendError('语音模型名称与本地 Base 模型配置不一致。') from exc


class SpeechRequest(BaseModel):
    model: str
    voice: str
    input: str = Field(min_length=1, max_length=1000)
    response_format: Literal['wav'] = 'wav'
    speed: float = Field(default=1, ge=.8, le=1.2)


def split_text(text: str, limit=50) -> list[str]:
    """Split at punctuation without changing, dropping, or duplicating any text."""
    pieces = re.findall(r'[^。！？!?；;\n，,]+[。！？!?；;\n，,]*|[。！？!?；;\n，,]+', text)
    chunks, pending = [], ''
    for piece in pieces:
        while len(piece) > limit:
            if pending:
                chunks.append(pending); pending = ''
            chunks.append(piece[:limit]); piece = piece[limit:]
        if len(pending) + len(piece) > limit:
            chunks.append(pending); pending = ''
        pending += piece
    if pending:
        chunks.append(pending)
    if ''.join(chunks) != text:
        raise BackendError('讲稿切段未保持完整文字。')
    return chunks


def read_reference(path: Path) -> dict:
    try:
        profile = json.loads(path.read_text(encoding='utf-8'))
        root = path.parent.resolve()
        audio_path = (root / profile['audio_file']).resolve()
        if not audio_path.is_relative_to(root) or audio_path.suffix.lower() != '.wav':
            raise ValueError('reference path')
        content = audio_path.read_bytes()
        if hashlib.sha256(content).hexdigest() != profile['sha256']:
            raise ValueError('reference checksum')
        with wave.open(io.BytesIO(content), 'rb') as audio:
            duration = audio.getnframes() / audio.getframerate()
            if audio.getnchannels() != 1 or audio.getsampwidth() != 2 or not 3 <= duration <= 25:
                raise ValueError('reference format')
        if not isinstance(profile['voice_id'], str) or not profile['voice_id']:
            raise ValueError('voice id')
        profile['audio_path'] = audio_path
        return profile
    except (OSError, ValueError, KeyError, TypeError, wave.Error) as exc:
        raise BackendError('本机参考声音未准备好，或校验失败。请运行 prepare_voice_reference.py。') from exc


class QwenEngine:
    def __init__(self, settings: VoiceSettings, profile: dict):
        validate_model_identity(settings)
        try:
            import torch
            from qwen_tts import Qwen3TTSModel
            import soundfile as sf
        except ImportError as exc:
            raise BackendError('请在隔离语音环境安装 requirements-tts.txt。') from exc
        self.torch = torch
        self.device = ('cuda:0' if torch.cuda.is_available() else 'cpu') if settings.device == 'auto' else settings.device
        if settings.quantization not in {'none', 'int8'}:
            raise BackendError('语音量化设置只支持 none 或 int8。')
        if settings.quantization == 'int8' and not self.device.startswith('cuda'):
            raise BackendError('int8 语音量化只用于 CUDA；CPU 请使用 none。')
        self.quantization = settings.quantization
        torch.set_num_threads(1 if self.device.startswith('cuda') else min(8, os.cpu_count() or 1))
        started = time.perf_counter()
        LOGGER.info('装载本机语音模型，设备 %s，音频编解码设备 %s', self.device, settings.codec_device)
        # Local directories only. Model initialization cannot upload the reference
        # or fetch another model if a file is missing.
        self.model = Qwen3TTSModel.from_pretrained(
            str(settings.model_dir.resolve()), device_map='cpu' if self.quantization == 'int8' else self.device,
            dtype=torch.bfloat16 if self.device.startswith('cuda') else torch.float32,
            attn_implementation='sdpa', local_files_only=True,
        )
        if self.quantization == 'int8' and self.device.startswith('cuda'):
            count = quantize_talker_layers(self.model.model.talker.model.layers, self.device)
            self.model.model.to(self.device)
            self.model.device = torch.device(self.device)
            LOGGER.info('语音语言模型 %d 个线性层采用 int8，声音编解码保持浮点', count)
        # Leave the sequential language model on GPU while the one-shot audio
        # codec runs on CPU. This avoids VRAM paging on an 8GB desktop GPU.
        codec = self.model.model.speech_tokenizer
        codec.model.to(device=settings.codec_device, dtype=torch.float32 if settings.codec_device == 'cpu' else torch.bfloat16)
        codec.device = torch.device(settings.codec_device)
        if self.device.startswith('cuda'):
            torch.cuda.empty_cache()
            LOGGER.info('语音显存：已分配 %.2f GiB，缓存 %.2f GiB', torch.cuda.memory_allocated() / 2**30, torch.cuda.memory_reserved() / 2**30)
        samples, rate = sf.read(str(profile['audio_path']), dtype='float32')
        transcript = profile.get('transcript', '').strip()
        with torch.inference_mode():
            self.prompt = self.model.create_voice_clone_prompt(
                ref_audio=(samples, rate), ref_text=transcript or None,
                x_vector_only_mode=not bool(transcript),
            )
        LOGGER.info('语音模型与参考提示就绪，耗时 %.2f 秒', time.perf_counter() - started)

    def synthesize(self, text: str):
        budget = max(128, min(1024, len(text) * 6))
        native_generate = self.model.model.generate
        def bounded_generate(*args, **kwargs):
            codes, states = native_generate(*args, **kwargs)
            check_generation_budget(codes, budget)
            return codes, states
        started = time.perf_counter()
        self.model.model.generate = bounded_generate
        try:
            with self.torch.inference_mode():
                self.torch.manual_seed(17)
                waves, rate = self.model.generate_voice_clone(
                    text=text, language='Chinese', voice_clone_prompt=self.prompt,
                    non_streaming_mode=False, max_new_tokens=budget,
                    do_sample=True, temperature=.7, top_p=.9, repetition_penalty=1.05,
                )
        finally:
            self.model.model.generate = native_generate
        LOGGER.info('语音片段完成：%d 字，%.2f 秒声音，合成 %.2f 秒', len(text), len(waves[0]) / rate, time.perf_counter() - started)
        return np.asarray(waves[0], dtype=np.float32), rate

    def close(self):
        self.prompt = None
        self.model = None
        gc.collect()
        if self.device.startswith('cuda'):
            self.torch.cuda.empty_cache()


def wav_bytes(samples: np.ndarray, rate: int) -> bytes:
    samples = np.asarray(samples, dtype=np.float32)
    if (samples.ndim != 1 or not 8000 <= rate <= 96000 or samples.size < rate / 5
            or not np.isfinite(samples).all() or np.max(np.abs(samples)) < 1e-5):
        raise BackendError('语音模型返回空白、无效或静音音频。')
    peak = float(np.max(np.abs(samples)))
    if peak > .98:
        samples = samples * (.98 / peak)
    pcm = (samples * 32767).astype('<i2')
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as output:
        output.setnchannels(1); output.setsampwidth(2); output.setframerate(rate)
        output.writeframes(pcm.tobytes())
    return buffer.getvalue()


def create_app(settings: VoiceSettings | None = None, *, engine_factory=QwenEngine) -> FastAPI:
    settings = settings or VoiceSettings.from_env()
    lock = threading.RLock()
    engine = None
    timer = None
    reference_hash = None
    last_error = None

    def release():
        nonlocal engine, timer
        with lock:
            if engine is not None:
                engine.close(); engine = None
            timer = None

    @asynccontextmanager
    async def lifespan(_app):
        yield
        if timer is not None:
            timer.cancel()
        release()

    application = FastAPI(title='Lively Class——AI教育Agent 本机 Qwen3-TTS 参考声音服务', lifespan=lifespan)

    def installed():
        return all((settings.model_dir / name).is_file() for name in
                   ['config.json', 'model.safetensors', 'speech_tokenizer/config.json',
                    'speech_tokenizer/model.safetensors'])

    @application.get('/health')
    def health():
        try:
            profile = read_reference(settings.reference_file)
        except BackendError:
            profile = None
        # A health check must not wait behind a multi-minute synthesis request.
        acquired = lock.acquire(blocking=False)
        try:
            snapshot = engine
            return {'model': settings.model_name, 'installed': installed(), 'loaded': snapshot is not None,
                    'reference_ready': profile is not None, 'voice': profile['voice_id'] if profile else None,
                    'device': getattr(snapshot, 'device', settings.device), 'last_error': last_error,
                    'quantization': getattr(snapshot, 'quantization', settings.quantization),
                    'busy': not acquired, 'local_only': True}
        finally:
            if acquired:
                lock.release()

    @application.post('/v1/audio/speech')
    def speech(request: SpeechRequest):
        nonlocal engine, timer, reference_hash, last_error
        if settings.model_name not in SUPPORTED_MODELS or request.model != settings.model_name:
            raise HTTPException(400, f'本机服务使用模型 {settings.model_name}。')
        if not request.input.strip():
            raise HTTPException(400, '讲稿不能为空白。')
        with lock:
            if timer is not None:
                timer.cancel(); timer = None
            try:
                if not installed():
                    raise BackendError('本机模型尚未安装，请运行 scripts/download_qwen_tts.py。')
                profile = read_reference(settings.reference_file)
                if request.voice != profile['voice_id']:
                    raise HTTPException(400, '本机服务没有该声线。')
                fingerprint = hashlib.sha256(json.dumps({
                    'audio': profile['sha256'], 'transcript': profile.get('transcript', ''),
                }, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                if engine is not None and reference_hash != fingerprint:
                    release()
                if engine is None:
                    engine = engine_factory(settings, profile)
                    reference_hash = fingerprint
                pieces, sample_rate = [], None
                for chunk in split_text(request.input):
                    samples, rate = engine.synthesize(chunk)
                    # Validate each chunk before concatenating; a failed chunk
                    # must not turn into a successful partial narration.
                    wav_bytes(samples, rate)
                    if sample_rate is not None and rate != sample_rate:
                        raise BackendError('语音片段的采样率不一致。')
                    sample_rate = rate
                    pieces.append(samples)
                samples = np.concatenate(pieces)
                if request.speed != 1:
                    import librosa
                    samples = librosa.effects.time_stretch(samples, rate=request.speed)
                output = wav_bytes(samples, sample_rate)
                last_error = None
                return Response(output, media_type='audio/wav', headers={'X-Speech-Voice': profile['voice_id']})
            except BackendError as exc:
                last_error = str(exc)
                raise HTTPException(503, last_error) from exc
            except HTTPException:
                raise
            except Exception as exc:
                LOGGER.exception('本机语音生成失败')
                release()
                last_error = '语音生成失败；请检查隔离环境和显存，必要时设置语音设备为 cpu。'
                raise HTTPException(503, last_error) from exc
            finally:
                if engine is not None and settings.idle_seconds > 0:
                    timer = threading.Timer(settings.idle_seconds, release)
                    timer.daemon = True
                    timer.start()

    return application


app = create_app()
