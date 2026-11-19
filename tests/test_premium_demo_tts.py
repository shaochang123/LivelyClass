"""Contract tests run without downloading weights or importing the TTS runtime."""
import importlib.util
import hashlib
from pathlib import Path

import numpy as np
import pytest

SPEC = importlib.util.spec_from_file_location("premium_demo_tts", Path(__file__).parents[1] / "scripts/premium_demo_tts.py")
tts = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tts)


def test_verbatim_source_and_spoken_normalization_are_kept_separate():
    request = tts.validate_request({"model": "demo-voxcpm2", "voice": "math", "input": "半径是二点五。",
                                   "metadata": {"source_text": "r=2.5。", "source_unit_id": "math-014"}})
    assert request["source_text"] == "r=2.5。"
    assert request["input"] == "半径是二点五。"
    assert request["source_unit_id"] == "math-014"


@pytest.mark.parametrize("data", [
    {"input": ""}, {"input": "a" * 1001}, {"input": "你好", "voice": "foreign"},
    {"input": "你好", "model": "cloud-tts"}, {"input": "你好", "response_format": "mp3"},
    {"input": "你好", "metadata": "invalid"}, {"input": "你好", "metadata": {"source_text": 12}},
])
def test_invalid_inputs_are_rejected(data):
    with pytest.raises(ValueError):
        tts.validate_request(data)


def test_native_sample_count_determines_timeline():
    stats = tts.sample_counts(np.full(60000, 0.1, dtype=np.float32), 48000)
    assert stats["seconds"] == 1.25
    assert stats["samples"] == 60000
    with pytest.raises(ValueError, match="48000"):
        tts.sample_counts(np.zeros(60000), 24000)
    with pytest.raises(ValueError, match="non-finite"):
        tts.sample_counts(np.array([np.nan]), 48000)
    with pytest.raises(ValueError, match="silent"):
        tts.sample_counts(np.zeros(48000), 48000)


@pytest.mark.parametrize("seed", [-1, 2**32, True, "random"])
def test_invalid_reproduction_seeds_rejected(seed):
    with pytest.raises(ValueError, match="seed"):
        tts.validate_request({"input": "明月松间照。", "seed": seed})


def test_checksum_verification_detects_missing_model(tmp_path):
    tts.json_write(tmp_path / "download-receipt.json", {"repo_id": tts.REPO_ID, "revision": tts.REVISION, "files": []})
    with pytest.raises(ValueError, match="Missing or incomplete"):
        tts.verify_model(tmp_path)


def test_no_silent_model_revision_substitution(tmp_path):
    tts.json_write(tmp_path / "download-receipt.json", {"repo_id": tts.REPO_ID, "revision": "new-revision"})
    with pytest.raises(ValueError, match="identity differs"):
        tts.verify_model(tmp_path)


def test_asr_character_comparison_does_not_hide_missing_words():
    same = tts.text_error_rate("明月松间照，清泉石上流。", "明月松间照清泉石上流")
    assert same["edit_distance"] == 0
    missing = tts.text_error_rate("明月松间照，清泉石上流。", "明月松间照")
    assert missing["edit_distance"] == 5
    assert missing["approximate_character_error_rate"] == 0.5


def test_official_range_download_preserves_existing_prefix(tmp_path, monkeypatch):
    import httpx

    content = b"preserved-prefix-and-remaining-verified-weight"
    digest = hashlib.sha256(content).hexdigest()
    prefix = content[:16]
    (tmp_path / "weight.bin.incomplete").write_bytes(prefix)
    monkeypatch.setattr(tts, "FILES", {"weight.bin": (len(content), digest)})
    requests = []

    def handle(request):
        requests.append(request)
        assert request.headers["range"] == f"bytes=16-{len(content)-1}"
        return httpx.Response(206, headers={"Content-Range": f"bytes 16-{len(content)-1}/{len(content)}"},
                              content=content[16:])

    client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(handle)))
    receipt = tts.download_model(tmp_path)
    assert (tmp_path / "weight.bin").read_bytes() == content
    assert receipt["files"][0]["sha256"] == digest
    assert len(requests) == 1
    assert tts.REVISION in requests[0].url.path


def test_ignored_range_never_overwrites_or_appends_to_valid_prefix(tmp_path, monkeypatch):
    import httpx

    prefix = b"existing-valid-prefix"
    (tmp_path / "weight.bin.incomplete").write_bytes(prefix)
    monkeypatch.setattr(tts, "FILES", {"weight.bin": (len(prefix) + 10, "not-yet-complete")})
    monkeypatch.setattr(tts.time, "sleep", lambda _: None)
    client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"server-ignored-the-range"))))
    with pytest.raises(RuntimeError, match="five attempts"):
        tts.download_model(tmp_path)
    assert (tmp_path / "weight.bin.incomplete").read_bytes() == prefix
    assert not (tmp_path / "weight.bin").exists()


def test_local_gateway_returns_raw_wav_and_count_headers():
    from fastapi.testclient import TestClient

    class FakeEngine:
        count, request_cap = 0, 1
        model = object()
        anchor = Path("not-an-existing-reference.wav")

        def synthesize(self, data):
            tts.validate_request(data)
            self.count += 1
            return b"RIFFtest", {"request_id": "test-001", "samples": 48000}

    engine = FakeEngine()
    client = TestClient(tts.create_app(engine))
    response = client.post("/v1/audio/speech", json={"input": "明月松间照。", "voice": "poetry"})
    assert response.status_code == 200
    assert response.content == b"RIFFtest"
    assert response.headers["x-audio-sample-rate"] == "48000"
    assert client.get("/health").json()["requests"] == 1
