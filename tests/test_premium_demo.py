"""Real request wiring, privacy and missing-dependency behavior for demo mode."""
import json
from pathlib import Path
import importlib.util

import httpx
import pytest
from pydantic import BaseModel

from zhijiang.premium_demo import DemoBlocked, DemoDeepSeek, validate_source, apply_display_overrides


class Answer(BaseModel):
    ok: bool


def test_demo_requires_explicit_remote_opt_in(tmp_path):
    with pytest.raises(DemoBlocked):
        DemoDeepSeek('secret', tmp_path, allow_official_api=False)


def test_stage_request_has_real_effort_json_vision_and_no_secret_on_disk(tmp_path):
    requests = []
    def server(req):
        requests.append(req)
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
            'message': {'content': '{"ok":true}'}}], 'usage': {'total_tokens': 34}})
    client = DemoDeepSeek('private-key-test', tmp_path, allow_official_api=True,
        http_client=httpx.Client(transport=httpx.MockTransport(server)))
    assert client.generate(Answer, 'review', '检查来源', {'source': '原句'},
                           effort='max', images=[b'actual-png-bytes']).ok
    body = json.loads(requests[0].content)
    assert requests[0].url == 'https://api.deepseek.com/chat/completions'
    assert body['reasoning_effort'] == 'max'
    assert body['thinking'] == {'type': 'enabled'}
    assert body['response_format'] == {'type': 'json_object'}
    assert 'temperature' not in body
    assert body['messages'][1]['content'][1]['image_url']['url'].startswith('data:image/png;base64,')
    assert not any('private-key-test' in p.read_text(encoding='utf-8') for p in tmp_path.rglob('*.json'))
    # Identical stage resumes from a validated result; no repeated paid request.
    client.generate(Answer, 'review', '检查来源', {'source': '原句'}, effort='max', images=[b'actual-png-bytes'])
    assert len(requests) == 1


def test_request_failure_is_not_silently_retried_or_fake_success(tmp_path):
    calls = []
    def server(req):
        calls.append(req)
        return httpx.Response(401, json={'error': 'private upstream text'})
    c = DemoDeepSeek('secret', tmp_path, allow_official_api=True,
        http_client=httpx.Client(transport=httpx.MockTransport(server)))
    with pytest.raises(DemoBlocked, match='401'):
        c.generate(Answer, 'planning', 'instruction', {})
    assert len(calls) == 1
    assert not list(tmp_path.rglob('validated.json'))


def test_source_ledger_rejects_rewriting_order_and_missing_units():
    s = {'source_text': '一\n二', 'units': [{'unit_id': '1', 'text': '一'}, {'unit_id': '2', 'text': '二'}]}
    validate_source(s)
    s['units'].reverse()
    with pytest.raises(ValueError):
        validate_source(s)


def image_module():
    path = Path(__file__).resolve().parents[1] / 'scripts/premium_demo_images.py'
    spec = importlib.util.spec_from_file_location('premium_images_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_flux_trusted_workflow_uses_distilled_steps_and_real_references():
    graph = image_module().workflow('full scene caption', 23, ['mother.png', 'actor.png'])
    assert graph['1']['inputs']['unet_name'] == 'flux3-image-klein-int8-convrot.safetensors'
    assert graph['2']['inputs']['type'] == 'flux3'
    assert graph['9']['inputs']['steps'] == 16
    assert graph['6']['class_type'] == 'BasicGuider'
    assert graph['6']['inputs']['conditioning'] == ['25', 0]
    assert graph['25']['inputs']['conditioning'] == ['22', 0]
    assert graph['22']['inputs']['conditioning'] == ['4', 0]
    assert graph['11']['class_type'] == 'VAEDecodeTiled'
    assert not any('CFG' in n['class_type'] for n in graph.values())


def test_gateway_refuses_cloud_backend_and_missing_real_weights(tmp_path):
    from fastapi.testclient import TestClient
    m = image_module()
    with pytest.raises(ValueError):
        m.create_app(tmp_path, 'https://example.com')
    c = TestClient(m.create_app(tmp_path))
    assert c.get('/health').json()['weights_verified'] is False
    r = c.post('/v1/images/generations', json={'model': 'demo-flux3-klein', 'prompt': 'a' * 500})
    assert r.status_code == 503


def test_editorial_fix_cannot_change_source_or_apply_to_a_different_model_draft():
    deck = {'pages': [{'unit_ids': ['real-1'], 'title': '原题', 'keywords': ['归']}]}
    changes = [{'case': 'humanities', 'unit_ids': ['real-1'], 'old': {'keywords': ['归']},
                'new': {'keywords': ['下行']}}]
    fixed = apply_display_overrides(deck, changes, 'humanities')
    assert fixed['pages'][0]['keywords'] == ['下行']
    assert deck['pages'][0]['keywords'] == ['归']
    with pytest.raises(ValueError):
        apply_display_overrides(fixed, changes, 'humanities')
    changes[0]['new'] = {'source_text': '改写'}
    with pytest.raises(ValueError):
        apply_display_overrides(deck, changes, 'humanities')


def test_gateway_rechecks_model_bytes_instead_of_trusting_receipt_exists(tmp_path):
    from zhijiang.premium_demo import digest
    m = image_module()
    items = []
    for name in m.FILES:
        target = tmp_path / 'runtime/ComfyUI/models' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'approved-weight-fixture')
        items.append({'file': name, 'bytes': target.stat().st_size, 'sha256': digest(target.read_bytes())})
    manifest = tmp_path / 'runtime/flux-model-manifest.json'
    manifest.write_text(json.dumps({'repo': m.REPO, 'revision': 'official-test-revision', 'files': items}), encoding='utf-8')
    # Same size: a mere presence/length check would fail to catch this mutation.
    target.write_bytes(b'changed!-weight-fixture')
    with pytest.raises(ValueError, match='字节核验'):
        m.create_app(tmp_path)
