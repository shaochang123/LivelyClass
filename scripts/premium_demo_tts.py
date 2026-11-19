"""Pinned, local VoxCPM2 speech adapter for the two-case premium demo.

This intentionally lives outside the application's normal speech configuration.
Run with the isolated demo TTS interpreter. The gateway binds to loopback only,
loads local verified weights, and never sends text/audio to an inference service.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import importlib.metadata
import json
import os
from pathlib import Path
import re
import ssl
import threading
import time
import uuid

REPO_ID = "openbmb/VoxCPM2"
REVISION = "7936080b41163874f38610f46f438e2c0757f9fd"
MODEL_NAME = "demo-voxcpm2"
FILES = {
    "audiovae.pth": (376951122, "94b5d51e107e0507d4acc976cfdadb64edd6fd06d1f751dadbf2fd1594274bf1"),
    "model.safetensors": (4580080592, "f7f964cfa9da23653baec6e6f7750719977ad944ed9f95fe52fe3a620506891d"),
    "config.json": (4336, None),
    "special_tokens_map.json": (1632, None),
    "tokenization_voxcpm2.py": (2895, None),
    "tokenizer.json": (3676772, None),
    "tokenizer_config.json": (5059, None),
}
DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "data/premium-demo-20261113"
STYLES = {
    "anchor": "成年女性，普通话清晰自然，音色温润、清澈，耐心亲切，语速适中。",
    "math": "普通话，温润清澈的成年女性，耐心的几何教师，语速平稳，字母、条件和比较关系清楚，自然讲述，不用广告腔。",
    "poetry": "普通话，同一温润清澈的成年女性，舒缓自然地朗读古诗，每句自然停顿，语意连贯，不唱读，不戏曲化。",
    "prose": "普通话，同一温润清澈的成年女性，温柔清楚地讲述，语速适中，不拖沓，不唱读。",
}
ANCHOR_TEXT = "雨后的山谷清新宁静，月光穿过松林，清泉从山石上缓缓流过。让我们一起走近诗中的秋天。"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def verify_model(model_dir: Path) -> dict:
    """Check full bytes, not only a directory name or a remote revision label."""
    receipt_path = model_dir / "download-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("repo_id") != REPO_ID or receipt.get("revision") != REVISION:
        raise ValueError("VoxCPM2 model identity differs from the approved frozen revision")
    actual = []
    recorded = {item["file"]: item for item in receipt.get("files", [])}
    for name, (size, official_sha) in FILES.items():
        path = model_dir / name
        if not path.is_file() or path.stat().st_size != size:
            raise ValueError(f"Missing or incomplete model file: {name}")
        digest = sha256_file(path)
        expected = official_sha or recorded.get(name, {}).get("sha256")
        if not expected or digest != expected:
            raise ValueError(f"Model checksum mismatch: {name}")
        actual.append({"file": name, "bytes": size, "sha256": digest, "official_lfs_sha256": official_sha})
    return {"repo_id": REPO_ID, "revision": REVISION, "files": actual}


def download_model(model_dir: Path) -> dict:
    """Download official public weights once, without copying them into HF cache."""
    import httpx
    from concurrent.futures import ThreadPoolExecutor

    model_dir.mkdir(parents=True, exist_ok=True)
    # Use the host's trusted roots (including Windows roots) with TLS verification
    # enabled. One bounded group of four 8MB ranges avoids slow long CDN streams.
    # Completed blocks append in order: interruption always leaves a valid prefix.
    context = ssl.create_default_context()
    chunk_size = 8 * 1024 * 1024

    def chunk(name, start, end, size):
        for attempt in range(1, 6):
            try:
                url = f"https://huggingface.co/{REPO_ID}/resolve/{REVISION}/{name}?download=true&range_start={start}"
                with httpx.Client(follow_redirects=True, verify=context,
                                  timeout=httpx.Timeout(90.0, connect=30.0)) as client:
                    with client.stream("GET", url, headers={"Range": f"bytes={start}-{end}"}) as response:
                        response.raise_for_status()
                        if response.status_code != 206 or response.headers.get("content-range") != f"bytes {start}-{end}/{size}":
                            raise ValueError("Server did not honor the exact byte range; partial file retained")
                        body = bytearray()
                        for block in response.iter_bytes(65536):
                            body.extend(block)
                            if len(body) > end - start + 1:
                                raise ValueError("Model range exceeded the requested length")
                        if len(body) != end - start + 1:
                            raise ValueError("Incomplete model range")
                        return bytes(body)
            except (httpx.HTTPError, OSError, ValueError) as exc:
                print(json.dumps({"download_file": name, "range_start": start, "attempt": attempt,
                                  "error_type": type(exc).__name__}), flush=True)
                if attempt == 5:
                    raise RuntimeError(f"Model range failed after five attempts: {name} at {start}") from None
                time.sleep(min(attempt, 3))

    for name, (size, expected_sha) in FILES.items():
        target = model_dir / name
        if target.exists() and target.stat().st_size == size:
            if expected_sha is None or sha256_file(target) == expected_sha:
                continue
        partial = target.with_name(target.name + ".incomplete")
        have = partial.stat().st_size if partial.exists() else 0
        if have > size:
            raise ValueError(f"Oversize partial download: {name}")
        # No preallocation and no extra chunk files: at most 32MB pending in RAM.
        with ThreadPoolExecutor(max_workers=4) as executor, partial.open("ab") as stream:
            next_offset = have
            queue = []
            last_progress = time.monotonic()
            while next_offset < size or queue:
                while next_offset < size and len(queue) < 4:
                    end = min(size, next_offset + chunk_size) - 1
                    queue.append(executor.submit(chunk, name, next_offset, end, size))
                    next_offset = end + 1
                block = queue.pop(0).result()
                stream.write(block)
                stream.flush()
                have += len(block)
                if have == size or time.monotonic() - last_progress >= 20:
                    print(json.dumps({"download_file": name, "bytes": have, "total": size,
                                      "transport": "bounded_8MB_ranges", "workers": 4}), flush=True)
                    last_progress = time.monotonic()
        if partial.stat().st_size != size:
            raise ValueError(f"Incomplete official model download: {name}")
        if expected_sha and sha256_file(partial) != expected_sha:
            raise ValueError(f"Official model checksum mismatch: {name}")
        partial.replace(target)
    records = []
    for name, (size, official_sha) in FILES.items():
        path = model_dir / name
        if path.stat().st_size != size:
            raise ValueError(f"Unexpected downloaded length: {name}")
        digest = sha256_file(path)
        if official_sha and digest != official_sha:
            raise ValueError(f"Official LFS checksum mismatch: {name}")
        records.append({"file": name, "bytes": size, "sha256": digest, "official_lfs_sha256": official_sha})
    receipt = {"repo_id": REPO_ID, "revision": REVISION, "download_only": True,
               "inference_requests": 0, "files": records}
    json_write(model_dir / "download-receipt.json", receipt)
    return receipt


def validate_request(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("JSON object required")
    if data.get("model", MODEL_NAME) != MODEL_NAME:
        raise ValueError("Only the frozen demo VoxCPM2 model is available")
    voice = data.get("voice", "math")
    if voice not in STYLES:
        raise ValueError("voice must be anchor, math, poetry or prose")
    text = data.get("input")
    if not isinstance(text, str) or not text.strip() or len(text) > 1000:
        raise ValueError("input must contain 1 to 1000 characters")
    if data.get("response_format", "wav") != "wav":
        raise ValueError("The demo preserves WAV audio only")
    metadata = data.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be an object")
    source_text = metadata.get("source_text", text)
    unit_id = metadata.get("source_unit_id")
    seed = data.get("seed", 2026111301)
    if not isinstance(source_text, str) or len(source_text) > 4000:
        raise ValueError("Invalid source_text")
    if unit_id is not None and (not isinstance(unit_id, str) or len(unit_id) > 100):
        raise ValueError("Invalid source_unit_id")
    if not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed < 2**32:
        raise ValueError("seed must be a 32-bit unsigned integer")
    return {"input": text, "voice": voice, "source_text": source_text,
            "source_unit_id": unit_id, "seed": seed}


def sample_counts(samples, sample_rate: int) -> dict:
    import numpy as np

    wav = np.asarray(samples, dtype=np.float32).reshape(-1)
    if sample_rate != 48000:
        raise ValueError(f"VoxCPM2 must return native 48000 Hz, received {sample_rate}")
    if not len(wav) or not np.isfinite(wav).all():
        raise ValueError("Speech output is empty or non-finite")
    if float(np.max(np.abs(wav))) > 1.00001:
        raise ValueError("Speech output exceeds normalized audio range")
    rms = float(np.sqrt(np.mean(np.square(wav, dtype=np.float64))))
    if rms < 0.00001:
        raise ValueError("Speech output is silent")
    return {"sample_rate": sample_rate, "samples": len(wav),
            "seconds": len(wav) / sample_rate, "peak": float(np.max(np.abs(wav))), "rms": rms}


class SpeechEngine:
    def __init__(self, model_dir: Path, output_dir: Path, device: str = "cuda", request_cap: int = 200,
                 require_reviewed_anchor: bool = True):
        self.model_dir, self.output_dir, self.device = model_dir, output_dir, device
        self.request_cap = request_cap
        self.count = 0
        self.lock = threading.Lock()
        self.model = None
        self.anchor = output_dir / "voice-anchor.wav"
        self.receipt = None
        self.require_reviewed_anchor = require_reviewed_anchor

    def load(self) -> dict:
        self.receipt = verify_model(self.model_dir)
        # Offline flags apply only to this demo process, after explicit download.
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        from voxcpm import VoxCPM
        import torch

        if self.device == "cpu":
            torch.set_num_threads(4)
        self.model = VoxCPM.from_pretrained(str(self.model_dir.resolve()),
                                          load_denoiser=False, optimize=False,
                                          local_files_only=True, device=self.device)
        sr = self.model.tts_model.sample_rate
        if sr != 48000:
            raise ValueError(f"Unexpected native sample rate: {sr}")
        info = {"ready": True, "local_inference": True, "model": MODEL_NAME,
                "repo_id": REPO_ID, "revision": REVISION, "sample_rate": sr,
                "device": self.device, "torch": torch.__version__, "optimize": False,
                "voxcpm": importlib.metadata.version("voxcpm"),
                "adapter_sha256": sha256_file(Path(__file__)),
                "model_files": self.receipt["files"],
                "denoiser": False, "cfg_value": 2.0, "inference_timesteps": 10,
                "internal_badcase_retries_max": 2}
        if self.device.startswith("cuda"):
            info["gpu"] = torch.cuda.get_device_name()
            info["allocated_bytes"] = torch.cuda.memory_allocated()
        json_write(self.output_dir / "loaded-environment.json", info)
        return info

    def synthesize(self, data: dict) -> tuple[bytes, dict]:
        request = validate_request(data)
        if self.model is None:
            raise RuntimeError("Speech model is not loaded")
        import numpy as np
        import soundfile as sf
        import torch

        with self.lock:
            if self.count >= self.request_cap:
                raise ValueError("Demo TTS request cap reached")
            self.count += 1
            started = time.monotonic()
            voice, source = request["voice"], request["input"]
            control = STYLES[voice]
            torch.manual_seed(request["seed"])
            if self.device.startswith("cuda"):
                torch.cuda.manual_seed_all(request["seed"])
            kwargs = {"text": f"({control}){source}", "cfg_value": 2.0,
                      "inference_timesteps": 10, "normalize": False, "denoise": False,
                      "retry_badcase": True, "retry_badcase_max_times": 2,
                      "max_len": min(2048, max(64, len(source) * 6))}
            if voice != "anchor":
                if not self.anchor.exists():
                    raise ValueError("Create and review voice-anchor.wav before using other voice profiles")
                if self.require_reviewed_anchor:
                    anchor_record = json.loads((self.output_dir / "voice-anchor.json").read_text(encoding="utf-8"))
                    if not (anchor_record.get("reviewed") and anchor_record.get("frozen")):
                        raise ValueError("Voice anchor has not been listened to and frozen")
                    if sha256_file(self.anchor) != anchor_record.get("wav_sha256"):
                        raise ValueError("Frozen voice anchor checksum mismatch")
                kwargs["reference_wav_path"] = str(self.anchor.resolve())
            with torch.inference_mode():
                samples = np.asarray(self.model.generate(**kwargs), dtype=np.float32).reshape(-1)
            stats = sample_counts(samples, self.model.tts_model.sample_rate)
            wav_buffer = io.BytesIO()
            # 24-bit PCM retains native 48kHz output and provides broad WAV support.
            sf.write(wav_buffer, samples, 48000, format="WAV", subtype="PCM_24")
            raw = wav_buffer.getvalue()
            request_id = f"{self.count:03d}-{uuid.uuid4().hex[:8]}"
            output = self.output_dir / f"{request_id}-{voice}.wav"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(raw)
            record = {"request_id": request_id, "model": MODEL_NAME, "repo_id": REPO_ID,
                      "revision": REVISION, "device": self.device, "voice": voice,
                      "seed": request["seed"],
                      "source_text": request["source_text"], "spoken_text": source,
                      "source_unit_id": request["source_unit_id"],
                      "control_text": control, "control_is_narration": False,
                      "wav": str(output), "wav_sha256": hashlib.sha256(raw).hexdigest(),
                      "seconds_elapsed": time.monotonic() - started, **stats}
            if voice != "anchor":
                record["anchor_sha256"] = sha256_file(self.anchor)
            json_write(output.with_suffix(".json"), record)
            return raw, record


def create_app(engine: SpeechEngine):
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse, Response

    app = FastAPI(title="Lively Class premium demo local VoxCPM2", docs_url=None, redoc_url=None)

    @app.get("/health")
    def health():
        return {"ready": engine.model is not None, "model": MODEL_NAME, "local_only": True,
                "requests": engine.count, "request_cap": engine.request_cap,
                "sample_rate": 48000, "voice_anchor_ready": engine.anchor.exists()}

    async def speech(request):
        if int(request.headers.get("content-length", "0")) > 20000:
            return JSONResponse({"error": "Request body too large"}, status_code=413)
        try:
            data = await request.json()
            raw, record = engine.synthesize(data)
            return Response(raw, media_type="audio/wav", headers={
                "X-Demo-Request-ID": record["request_id"],
                "X-Audio-Samples": str(record["samples"]), "X-Audio-Sample-Rate": "48000"})
        except (ValueError, json.JSONDecodeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception as exc:
            # Avoid dumping model internals or unrelated environment/secrets.
            return JSONResponse({"error": "Local TTS generation failed", "type": type(exc).__name__}, status_code=500)

    speech.__annotations__["request"] = Request
    app.add_api_route("/v1/audio/speech", speech, methods=["POST"])
    return app


def text_error_rate(reference: str, observed: str) -> dict:
    """Transparent ASR transcript comparison, not a claim of human listening."""
    normalize = lambda text: re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", text).lower()
    expected, actual = normalize(reference), normalize(observed)
    previous = list(range(len(actual) + 1))
    for row, left in enumerate(expected, 1):
        current = [row]
        for col, right in enumerate(actual, 1):
            current.append(min(current[-1] + 1, previous[col] + 1,
                               previous[col - 1] + (left != right)))
        previous = current
    edits = previous[-1]
    return {"normalized_reference": expected, "normalized_asr": actual,
            "edit_distance": edits, "reference_characters": len(expected),
            "approximate_character_error_rate": edits / max(1, len(expected)),
            "interpretation": "Independent small-model ASR comparison; homophones and ASR errors require listening."}


def audit_preflight(root: Path) -> dict:
    from faster_whisper import WhisperModel
    import soundfile as sf
    from scipy.signal import resample_poly

    model_dir = root.parents[1] / "data/models/whisper-small"
    if not model_dir.exists():
        raise ValueError(f"Cached local ASR model is missing: {model_dir}")
    model = WhisperModel(str(model_dir.resolve()), device="cpu", compute_type="int8",
                         cpu_threads=4, num_workers=1, local_files_only=True)
    preflight = json.loads((root / "tts/preflight.json").read_text(encoding="utf-8"))
    results = []
    for record in preflight["records"]:
        wav = Path(record.get("candidate") or record["wav"])
        samples, rate = sf.read(wav, dtype="float32", always_2d=False)
        if getattr(samples, "ndim", 1) != 1:
            raise ValueError("Expected mono native demo audio")
        if rate != 48000:
            raise ValueError("Expected the unmodified native 48kHz demo master")
        # NumPy input at 16kHz bypasses an unrelated PyAV/faster-whisper version
        # mismatch in the host's previous environment; master WAV stays unchanged.
        asr_samples = resample_poly(samples, 1, 3).astype("float32")
        segments, info = model.transcribe(asr_samples, language="zh", beam_size=5,
                                          condition_on_previous_text=False, word_timestamps=True,
                                          vad_filter=False)
        decoded = []
        for segment in segments:
            decoded.append({"start": segment.start, "end": segment.end, "text": segment.text,
                            "avg_logprob": segment.avg_logprob,
                            "words": [{"start": word.start, "end": word.end,
                                       "word": word.word, "probability": word.probability}
                                      for word in (segment.words or [])]})
        observed = "".join(segment["text"] for segment in decoded)
        results.append({"wav": str(wav.resolve()), "wav_sha256": sha256_file(wav),
                        "reference_text": record["spoken_text"], "asr_text": observed,
                        "segments": decoded, **text_error_rate(record["spoken_text"], observed)})
    result = {"model_path": str(model_dir.resolve()), "asr_device": "cpu", "compute_type": "int8",
              "model_sha256": sha256_file(model_dir / "model.bin"),
              "audio_decode": "soundfile native 48kHz master; scipy polyphase 1/3 resample for ASR only",
              "language": "zh", "reference_prompt_provided_to_asr": False,
              "human_listening_completed": False, "results": results}
    json_write(root / "tts/preflight-asr-audit.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["download", "verify", "anchor", "sample", "preflight", "audit", "serve"])
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--device", choices=["cuda", "cpu"], default="cuda")
    parser.add_argument("--voice", choices=list(STYLES), default="math")
    parser.add_argument("--input", default=None)
    parser.add_argument("--port", type=int, default=8794)
    parser.add_argument("--seed", type=int, default=2026111301)
    args = parser.parse_args()
    model_dir = args.root / "models/VoxCPM2"
    output_dir = args.root / "tts"
    if args.command == "audit":
        audit = audit_preflight(args.root)
        print(json.dumps({"asr_samples_checked": len(audit["results"]), "human_listening_completed": False}), flush=True)
        return
    if args.command == "download":
        receipt = download_model(model_dir)
        print(json.dumps({"downloaded": True, "revision": receipt["revision"],
                          "bytes": sum(item["bytes"] for item in receipt["files"])}), flush=True)
        return
    if args.command == "verify":
        receipt = verify_model(model_dir)
        print(json.dumps({"verified": True, "revision": receipt["revision"]}), flush=True)
        return
    engine = SpeechEngine(model_dir, output_dir, device=args.device,
                          require_reviewed_anchor=args.command != "preflight")
    print(json.dumps(engine.load(), ensure_ascii=False), flush=True)
    if args.command == "anchor":
        raw, record = engine.synthesize({"input": ANCHOR_TEXT, "voice": "anchor", "seed": args.seed})
        engine.anchor.write_bytes(raw)
        json_write(output_dir / "voice-anchor.json", {**record, "reviewed": False, "frozen": False,
                                                     "origin": "new synthetic voice, no real-person reference"})
        print(json.dumps(record, ensure_ascii=False), flush=True)
    elif args.command == "preflight":
        records = []
        for index in range(3):
            raw, record = engine.synthesize({"input": ANCHOR_TEXT, "voice": "anchor", "seed": args.seed + index})
            candidate = output_dir / f"voice-anchor-candidate-{index + 1}.wav"
            candidate.write_bytes(raw)
            json_write(candidate.with_suffix(".json"), {**record, "reviewed": False,
                                                        "origin": "new synthetic voice, no real-person reference"})
            records.append({**record, "candidate": str(candidate)})
        # A provisional reference enables comparison samples. It is not marked
        # reviewed or frozen until a person has listened and selected a candidate.
        engine.anchor.write_bytes(Path(records[0]["candidate"]).read_bytes())
        json_write(output_dir / "voice-anchor.json", {**records[0], "reviewed": False, "frozen": False,
                                                     "selection": "provisional candidate 1 for preflight only"})
        for voice, text in [("math", "当圆心到直线的距离大于半径时，直线与圆相离，没有公共点。半径是二点五。"),
                            ("poetry", "山居秋暝。唐，王维。明月松间照，清泉石上流。竹喧归浣女，莲动下渔舟。随意春芳歇，王孙自可留。")]:
            raw, record = engine.synthesize({"input": text, "voice": voice, "seed": args.seed})
            output_dir.joinpath(f"preflight-{voice}.wav").write_bytes(raw)
            records.append(record)
        json_write(output_dir / "preflight.json", {"reviewed": False, "frozen": False,
                                                  "records": records, "full_course_generated": False})
        print(json.dumps({"preflight_complete": True, "voices": 3, "samples": 2,
                          "sample_rate": 48000, "reviewed": False}), flush=True)
    elif args.command == "sample":
        text = args.input or ("明月松间照，清泉石上流。" if args.voice == "poetry" else
                              "当圆心到直线的距离大于半径时，直线与圆相离，没有公共点。")
        _, record = engine.synthesize({"input": text, "voice": args.voice, "seed": args.seed})
        print(json.dumps(record, ensure_ascii=False), flush=True)
    else:
        import uvicorn
        uvicorn.run(create_app(engine), host="127.0.0.1", port=args.port, workers=1)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Keep a small reproducible failure receipt without spilling environment
        # variables, authentication material or large model-internal tensors.
        json_write(DEFAULT_ROOT / "tts/last-runtime-failure.json", {
            "error_type": type(exc).__name__, "message": str(exc)[:1000],
            "time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "model": MODEL_NAME, "revision": REVISION,
        })
        raise
