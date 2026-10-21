"""Local, CPU-backed Chinese Kokoro TTS with a compatible /v1/audio/speech API."""

from __future__ import annotations

import io
import threading
import wave
from pathlib import Path
from typing import Literal

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field


MODEL_NAME = "kokoro-82m-v1.1-zh"
MODEL_DIR = Path("data/models/kokoro").resolve()


class SpeechRequest(BaseModel):
    model: str
    voice: str
    input: str = Field(min_length=1, max_length=12000)
    response_format: Literal["wav"] = "wav"
    speed: float = Field(default=1.0, ge=0.5, le=2.0)


def create_app(model_dir: Path = MODEL_DIR) -> FastAPI:
    application = FastAPI(title="Lively Class——AI教育Agent 本机 Kokoro 语音服务")
    model_path = model_dir / "kokoro-v1.1-zh-int8.onnx"
    voices_path = model_dir / "voices-v1.1-zh.bin"
    config_path = model_dir / "config.json"
    lock = threading.Lock()
    engine = None
    g2p = None

    @application.get("/health")
    def health() -> dict:
        return {
            "model": MODEL_NAME,
            "installed": all(path.is_file() for path in (model_path, voices_path, config_path)),
            "loaded": engine is not None,
        }

    @application.post("/v1/audio/speech")
    def synthesize(request: SpeechRequest) -> Response:
        nonlocal engine, g2p
        if request.model != MODEL_NAME:
            raise HTTPException(400, f"本机服务只支持模型 {MODEL_NAME}。")
        if not all(path.is_file() for path in (model_path, voices_path, config_path)):
            raise HTTPException(503, "本机语音模型尚未下载；请运行 scripts/download_local_tts.py。")
        with lock:
            if engine is None:
                try:
                    from kokoro_onnx import Kokoro
                    from misaki.zh import ZHG2P
                except ImportError as exc:
                    raise HTTPException(503, '请先安装项目的 local-tts 可选依赖。') from exc

                engine = Kokoro(str(model_path), str(voices_path), vocab_config=str(config_path))
                g2p = ZHG2P()
            if request.voice not in engine.get_voices():
                raise HTTPException(400, f"没有音色 {request.voice}。")
            try:
                phonemes = g2p(request.input)
                samples, sample_rate = engine.create(
                    phonemes, voice=request.voice, speed=request.speed,
                    lang="cmn", is_phonemes=True,
                )
            except ValueError as exc:
                raise HTTPException(400, "语音模型无法处理这段文字；请检查讲稿和音色。") from exc
        if len(samples) == 0:
            raise HTTPException(500, "语音模型没有生成音频。")
        pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(sample_rate)
            audio.writeframes(pcm.tobytes())
        return Response(buffer.getvalue(), media_type="audio/wav")

    return application


app = create_app()
