"""Local FLUX 3 INT8 / ComfyUI gateway for the two selected demo cases.

No cloud image inference. Run this with the project's normal Python; ComfyUI
runs separately in its isolated CUDA environment, bound to 127.0.0.1:8188.
"""
from __future__ import annotations

import argparse
import base64
from io import BytesIO
import json
from pathlib import Path
import sys
import subprocess
import threading
import time
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
from fastapi import FastAPI, HTTPException
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field
from zhijiang.premium_demo import digest, file_digest, write_json

REPO = 'black-forest-labs/flux-3-image-klein-comfy'
FILES = ('diffusion_models/flux3-image-klein-int8-convrot.safetensors',
         'text_encoders/qwen3vl_4b_int8_convrot.safetensors', 'vae/flux2-vae.safetensors')
WORK = ROOT / 'data/premium-demo-20261113'
ADAPTER_SHA256 = file_digest(Path(__file__))


def workflow(prompt: str, seed: int, references: list[str]) -> dict:
    """Build only trusted nodes; model output is never executable code."""
    graph = {
        '1': {'class_type': 'UNETLoader', 'inputs': {'unet_name': Path(FILES[0]).name,
                                                      'weight_dtype': 'default'}},
        '2': {'class_type': 'CLIPLoader', 'inputs': {'clip_name': Path(FILES[1]).name,
                                                     'type': 'flux3', 'device': 'cpu'}},
        '3': {'class_type': 'VAELoader', 'inputs': {'vae_name': Path(FILES[2]).name}},
        '4': {'class_type': 'CLIPTextEncode', 'inputs': {'clip': ['2', 0], 'text': prompt}},
        '5': {'class_type': 'EmptyFlux2LatentImage', 'inputs': {
            'width': 1280, 'height': 704, 'batch_size': 1}},
        '6': {'class_type': 'BasicGuider', 'inputs': {'model': ['1', 0], 'conditioning': ['4', 0]}},
        '7': {'class_type': 'RandomNoise', 'inputs': {'noise_seed': seed}},
        '8': {'class_type': 'KSamplerSelect', 'inputs': {'sampler_name': 'euler'}},
        '9': {'class_type': 'BasicScheduler', 'inputs': {'model': ['1', 0],
            'scheduler': 'simple', 'steps': 16, 'denoise': 1.0}},
        '10': {'class_type': 'SamplerCustomAdvanced', 'inputs': {'noise': ['7', 0],
            'guider': ['6', 0], 'sampler': ['8', 0], 'sigmas': ['9', 0], 'latent_image': ['5', 0]}},
        '11': {'class_type': 'VAEDecodeTiled', 'inputs': {'samples': ['10', 0], 'vae': ['3', 0],
            'tile_size': 512, 'overlap': 64, 'temporal_size': 64, 'temporal_overlap': 8}},
        '12': {'class_type': 'SaveImage', 'inputs': {'images': ['11', 0], 'filename_prefix': 'premium-demo'}},
    }
    previous = ['4', 0]
    for i, name in enumerate(references):
        start = 20 + i * 3
        graph[str(start)] = {'class_type': 'LoadImage', 'inputs': {'image': name}}
        graph[str(start + 1)] = {'class_type': 'VAEEncode', 'inputs': {
            'pixels': [str(start), 0], 'vae': ['3', 0]}}
        graph[str(start + 2)] = {'class_type': 'ReferenceLatent', 'inputs': {
            'conditioning': previous, 'latent': [str(start + 1), 0]}}
        previous = [str(start + 2), 0]
    graph['6']['inputs']['conditioning'] = previous
    return graph


def download(work: Path) -> dict:
    """Authenticated official download with pinned revision and LFS hashes."""
    from huggingface_hub import HfApi, hf_hub_download
    info = HfApi().model_info(REPO, files_metadata=True)
    selected = {f.rfilename: f for f in info.siblings if f.rfilename in FILES}
    if len(selected) != len(FILES):
        raise RuntimeError('官方仓库没有列出计划中的三个文件，停止而不更换权重。')
    total = sum(f.size or 0 for f in selected.values())
    import shutil
    if shutil.disk_usage(work).free < total + 4_000_000_000:
        raise RuntimeError('磁盘空间不足以保留权重和运行余量。')
    records = []
    for name in FILES:
        entry = selected[name]
        target = Path(hf_hub_download(REPO, name, revision=info.sha,
            local_dir=work / 'runtime/ComfyUI/models'))
        sha = file_digest(target)
        expected = entry.lfs.sha256 if entry.lfs else None
        if not expected or expected != sha:
            raise RuntimeError('权重未通过官方 LFS SHA256 校验。')
        records.append({'file': name, 'bytes': target.stat().st_size, 'sha256': sha})
    manifest = {'repo': REPO, 'revision': info.sha, 'files': records,
                'precision': 'int8-convrot', 'distilled': True, 'steps': 16,
                'cfg': None, 'width': 1280, 'height': 704, 'upsampler': 'external-deepseek-full-caption'}
    write_json(work / 'runtime/flux-model-manifest.json', manifest)
    return manifest


class ImageRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model: str
    prompt: str = Field(min_length=400, max_length=10000)
    n: int = Field(default=1, ge=1, le=1)
    seed: int = Field(default=20261113, ge=0, le=2**63 - 1)
    reference_images: list[str] = Field(default_factory=list, max_length=3)
    response_format: str = 'b64_json'


def create_app(work: Path, comfy_url='http://127.0.0.1:8188') -> FastAPI:
    parsed = urlparse(comfy_url)
    if parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}:
        raise ValueError('绘图后端必须是本机回环接口。')
    app = FastAPI(title='Lively Class premium local FLUX gateway')
    lock = threading.Lock()
    work = work.resolve()
    reference_dir = work / 'image-references'
    output = work / 'image-generation'
    client = httpx.Client(base_url=comfy_url, timeout=60, trust_env=False)
    manifest_file = work / 'runtime/flux-model-manifest.json'
    verified_weights = False
    if manifest_file.exists():
        record = json.loads(manifest_file.read_text(encoding='utf-8'))
        if record.get('repo') != REPO or {f['file'] for f in record.get('files', [])} != set(FILES):
            raise ValueError('官方模型清单身份不匹配。')
        for item in record['files']:
            weight = work / 'runtime/ComfyUI/models' / item['file']
            if not weight.is_file() or weight.stat().st_size != item['bytes'] or file_digest(weight) != item['sha256']:
                raise ValueError('实际权重未通过清单逐文件字节核验。')
        verified_weights = True

    @app.get('/health')
    def health():
        return {'model': 'demo-flux3-klein', 'local_only': True,
                'weights_verified': verified_weights,
                'generation_validated': any(output.glob('*/result.json'))}

    @app.post('/v1/images/generations')
    def generate(req: ImageRequest):
        if req.model != 'demo-flux3-klein' or req.response_format != 'b64_json':
            raise HTTPException(400, '模型别名或返回格式不匹配。')
        manifest_file = work / 'runtime/flux-model-manifest.json'
        if not verified_weights:
            raise HTTPException(503, 'FLUX 官方权重尚未下载并校验。')
        manifest = json.loads(manifest_file.read_text(encoding='utf-8'))
        if not manifest.get('revision') or {f['file'] for f in manifest['files']} != set(FILES):
            raise HTTPException(503, '权重清单不完整。')
        paths = []
        for name in req.reference_images:
            candidate = (reference_dir / name).resolve()
            if not candidate.is_relative_to(reference_dir.resolve()) or not candidate.is_file():
                raise HTTPException(400, '参考图必须存在于本次 Demo 的参考图目录。')
            paths.append(candidate)
        ref_hashes = [digest(p.read_bytes()) for p in paths]
        key = digest(json.dumps([req.model_dump(), manifest, ref_hashes], sort_keys=True).encode())
        folder = output / key
        folder.mkdir(parents=True, exist_ok=True)
        with lock:
            final = folder / 'image.png'
            if final.exists() and (folder / 'result.json').exists():
                cached = json.loads((folder / 'result.json').read_text(encoding='utf-8'))
                if file_digest(final) != cached.get('image_sha256'):
                    raise HTTPException(503, '已生成图片与原始收据不同，不能静默复用。')
                return {'created': int(time.time()), 'data': [{'b64_json':
                    base64.b64encode(final.read_bytes()).decode()}], 'receipt_id': key}
            try:
                stats_response = client.get('/system_stats')
                stats_response.raise_for_status()
                stats = stats_response.json()
                if not any(device.get('type') == 'cuda' for device in stats.get('devices', [])):
                    raise HTTPException(503, '绘图后端未实际报告 CUDA 设备，请使用本次GPU启动命令。')
                if file_digest(Path(__file__)) != ADAPTER_SHA256:
                    raise HTTPException(503, '运行期间绘图适配器字节发生变化，请保留当前任务后重新启动。')
                job_file = folder / 'job.json'
                if job_file.exists():
                    job = json.loads(job_file.read_text(encoding='utf-8'))
                else:
                    pending = folder / 'submission-pending.json'
                    if pending.exists():
                        raise HTTPException(503, '上次提交是否被接收尚不确定；请核对本地队列，不自动重复提交。')
                    uploaded = []
                    for p in paths:
                        r = client.post('/upload/image', files={'image': (p.name, p.read_bytes(), 'image/png')},
                                        data={'overwrite': 'false', 'type': 'input'})
                        r.raise_for_status()
                        meta = r.json()
                        uploaded.append('/'.join(x for x in [meta.get('subfolder', ''), meta['name']] if x))
                    graph = workflow(req.prompt, req.seed, uploaded)
                    info = client.get('/object_info'); info.raise_for_status()
                    missing = {n['class_type'] for n in graph.values()} - info.json().keys()
                    if missing:
                        raise HTTPException(503, 'ComfyUI 缺少所需节点：' + ', '.join(sorted(missing)))
                    write_json(folder / 'request.json', req.model_dump())
                    write_json(folder / 'workflow.json', graph)
                    write_json(pending, {'client_id': key, 'submission_started': int(time.time())})
                    r = client.post('/prompt', json={'prompt': graph, 'client_id': key})
                    r.raise_for_status()
                    job = r.json()
                    write_json(job_file, job)
                prompt_id = job['prompt_id']
                for _ in range(3600):
                    r = client.get('/history/' + prompt_id); r.raise_for_status()
                    item = r.json().get(prompt_id)
                    if item:
                        write_json(folder / 'history.json', item)
                        if item.get('status', {}).get('status_str') == 'error':
                            raise HTTPException(502, 'ComfyUI 执行失败；保留原任务，不重复提交。')
                        images = item.get('outputs', {}).get('12', {}).get('images', [])
                        if images:
                            r = client.get('/view', params=images[0]); r.raise_for_status()
                            im = Image.open(BytesIO(r.content)); im.load()
                            if im.size != (1280, 704):
                                raise HTTPException(502, '实际图片尺寸与冻结配置不符。')
                            im.save(final)
                            write_json(folder / 'result.json', {'prompt_id': prompt_id, 'seed': req.seed,
                                'reference_hashes': ref_hashes, 'image_sha256': digest(final.read_bytes()),
                                'manifest': manifest, 'actual_size': list(im.size),
                                'adapter_sha256': ADAPTER_SHA256, 'actual_comfy_system_stats': stats,
                                'comfy_commit': subprocess.run(['git', '-C', str(work / 'runtime/ComfyUI'),
                                    'rev-parse', 'HEAD'], capture_output=True, text=True, check=True).stdout.strip()})
                            return {'created': int(time.time()), 'data': [{'b64_json':
                                base64.b64encode(final.read_bytes()).decode()}], 'receipt_id': key}
                    time.sleep(1)
                raise HTTPException(504, '任务仍在执行；任务ID已保留，下次请求继续查询。')
            except httpx.HTTPError:
                raise HTTPException(502, '本地 ComfyUI 请求未完成；已有任务不自动重复提交。') from None
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['download', 'serve', 'workflow'])
    parser.add_argument('--work', type=Path, default=WORK)
    parser.add_argument('--prompt-file', type=Path)
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)
    if args.command == 'download':
        try:
            receipt = download(args.work)
            print(json.dumps({'downloaded': True, 'revision': receipt['revision']}))
        except Exception as exc:
            # Hub exceptions may contain signed URLs: log only the class.
            write_json(args.work / 'runtime/flux-download-status.json', {
                'downloaded': False, 'error_type': type(exc).__name__,
                'requires_gated_repo_access': True})
            print('FLUX 下载未完成。请核对 HF 授权、空间和本机网络。错误类型：' + type(exc).__name__)
            raise SystemExit(2)
    elif args.command == 'workflow':
        prompt = args.prompt_file.read_text(encoding='utf-8') if args.prompt_file else 'CAPTION_FROM_DS'
        write_json(args.work / 'runtime/workflow-template.json', workflow(prompt, 20261113, []))
    else:
        import uvicorn
        uvicorn.run(create_app(args.work), host='127.0.0.1', port=8793)


if __name__ == '__main__':
    main()
