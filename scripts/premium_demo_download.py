"""Verified official FLUX weights, bounded resumable ranges and Windows TLS roots.

Credentials and signed CDN URLs remain in memory. Range progress records retain
only repository identity, hashes, sizes and completed byte ranges.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import math
from pathlib import Path
import ssl
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import httpx
from huggingface_hub import HfApi, get_token
from premium_demo_images import REPO, FILES, WORK
from zhijiang.premium_demo import file_digest, write_json

CHUNK = 4 * 1024 * 1024


def main():
    token = get_token()
    if not token:
        raise RuntimeError('请先在本机执行 hf auth login。')
    info = HfApi().model_info(REPO, files_metadata=True)
    metadata = {f.rfilename: f for f in info.siblings if f.rfilename in FILES}
    if set(metadata) != set(FILES):
        raise RuntimeError('官方文件清单不完整。')
    lock = threading.Lock()
    context = ssl.create_default_context()
    started = time.monotonic()
    records = []
    for name in FILES:
        meta = metadata[name]
        expected = meta.lfs.sha256
        size = meta.size
        final = WORK / 'runtime/ComfyUI/models' / name
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.exists() and final.stat().st_size == size and file_digest(final) == expected:
            records.append({'file': name, 'bytes': size, 'sha256': expected})
            continue
        target = final.with_suffix(final.suffix + '.ranges-incomplete')
        statefile = final.with_suffix(final.suffix + '.ranges.json')
        identity = {'repo': REPO, 'revision': info.sha, 'file': name, 'bytes': size,
                    'sha256': expected, 'chunk_bytes': CHUNK}
        state = json.loads(statefile.read_text(encoding='utf-8')) if statefile.exists() else {
            **identity, 'completed': {}}
        if any(state[k] != value for k, value in identity.items()):
            raise RuntimeError('分块进度与当前官方权重身份不同；不能覆盖。')
        if not target.exists():
            with target.open('wb') as stream:
                stream.truncate(size)
        if target.stat().st_size != size:
            raise RuntimeError('分块目标尺寸异常。')
        # Recheck the stored chunk bytes before treating an interrupted run as done.
        with target.open('rb') as stream:
            for index, sha in list(state['completed'].items()):
                start = int(index) * CHUNK
                stream.seek(start)
                actual = hashlib.sha256(stream.read(min(CHUNK, size - start))).hexdigest()
                if actual != sha:
                    del state['completed'][index]
        write_json(statefile, state)
        tasks = [i for i in range(math.ceil(size / CHUNK)) if str(i) not in state['completed']]
        print(json.dumps({'file': name, 'remaining_chunks': len(tasks), 'workers': 6}), flush=True)

        def fetch(index):
            start = index * CHUNK
            end = min(size, start + CHUNK) - 1
            for attempt in range(4):
                try:
                    # Fresh connections bound the impact of a stalled CDN stream.
                    with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(90, connect=25),
                                      verify=context) as client:
                        url = f'https://huggingface.co/{REPO}/resolve/{info.sha}/{name}?demo_range={index}'
                        response = client.get(url, headers={'Authorization': 'Bearer ' + token,
                                                           'Range': f'bytes={start}-{end}'})
                    if (response.status_code != 206 or
                            response.headers.get('content-range') != f'bytes {start}-{end}/{size}' or
                            len(response.content) != end - start + 1):
                        raise ValueError('range_identity_mismatch')
                    sha = hashlib.sha256(response.content).hexdigest()
                    with target.open('r+b') as stream:
                        stream.seek(start)
                        stream.write(response.content)
                        stream.flush()
                    with lock:
                        state['completed'][str(index)] = sha
                        if len(state['completed']) % 8 == 0:
                            write_json(statefile, state)
                            print(json.dumps({'file': name, 'completed_chunks': len(state['completed']),
                                'total_chunks': math.ceil(size / CHUNK),
                                'seconds': round(time.monotonic() - started)}), flush=True)
                    return
                except (httpx.HTTPError, ValueError):
                    if attempt == 3:
                        raise RuntimeError('分块传输未完成，已保留进度；错误响应与URL不写入日志。') from None
                    time.sleep(attempt + 1)

        try:
            with ThreadPoolExecutor(max_workers=6) as executor:
                futures = [executor.submit(fetch, index) for index in tasks]
                for future in as_completed(futures):
                    future.result()
        finally:
            write_json(statefile, state)
        if file_digest(target) != expected:
            raise RuntimeError('整文件 SHA256 不匹配，不能加载。')
        target.replace(final)
        records.append({'file': name, 'bytes': size, 'sha256': expected})
    manifest = {'repo': REPO, 'revision': info.sha, 'files': records,
        'precision': 'int8-convrot', 'distilled': True, 'steps': 16, 'cfg': None,
        'width': 1280, 'height': 704, 'upsampler': 'external-deepseek-full-caption',
        'tls_verification': 'Windows trusted roots, enabled', 'inference_requests': 0}
    write_json(WORK / 'runtime/flux-model-manifest.json', manifest)
    print(json.dumps({'verified': True, 'revision': info.sha, 'seconds': round(time.monotonic() - started)}), flush=True)


if __name__ == '__main__':
    main()
