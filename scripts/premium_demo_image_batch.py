"""Generate three real local candidates per shot, then consume reviewed choices."""
from __future__ import annotations

import argparse
import base64
from io import BytesIO
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import httpx
from PIL import Image
from zhijiang.premium_demo import (DemoBlocked, DemoDeepSeek, ExpandedCaption,
    file_digest, load_private_key, write_json)

WORK = ROOT / 'data/premium-demo-20261113'


def load(p):
    return json.loads(Path(p).read_text(encoding='utf-8'))


def choices(work):
    p = work / 'image-choices.json'
    return load(p) if p.exists() else {'references': {}, 'units': {}}


def approved(work, category, name):
    item = choices(work).get(category, {}).get(name)
    if not isinstance(item, dict) or item.get('reviewed') is not True:
        raise DemoBlocked(f'尚未选择并审核图片：{category}/{name}')
    path = Path(item['path']).resolve()
    if not path.is_relative_to(work.resolve()) or file_digest(path) != item['sha256']:
        raise DemoBlocked('已选图片缺失、越出任务目录或被修改。')
    return path


def select(work, category, name, path):
    path = path.resolve(strict=True)
    if not path.is_relative_to(work.resolve()):
        raise DemoBlocked('只能选本次实际生成的图片。')
    Image.open(path).verify()
    # This command is invoked only after the operator inspects the actual image.
    data = choices(work)
    data.setdefault(category, {})[name] = {'path': str(path), 'sha256': file_digest(path), 'reviewed': True}
    write_json(work / 'image-choices.json', data)
    if category == 'references':
        refdir = work / 'image-references'
        refdir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, refdir / (name + '.png'))


def generate_candidates(work, name, caption, refs, seed_base):
    folder = work / 'image-candidates' / name
    folder.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=3700, trust_env=False) as client:
        for index in range(3):
            target = folder / f'{index + 1}.png'
            receipt = target.with_suffix('.json')
            direction_file = work / 'image-art-direction.txt'
            final_caption = caption + ('\n' + direction_file.read_text(encoding='utf-8') if direction_file.exists() else '')
            request = {'model': 'demo-flux3-klein', 'prompt': final_caption,
                       'n': 1, 'seed': seed_base + index, 'reference_images': refs}
            if target.exists() and receipt.exists() and load(receipt).get('request') == request:
                continue
            if target.exists() or receipt.exists():
                raise DemoBlocked('候选图已有不同请求记录；请使用新variant目录，保留首轮图像。')
            print(f'本地生图：{name} 候选 {index + 1}/3', flush=True)
            r = client.post('http://127.0.0.1:8793/v1/images/generations', json=request)
            if r.status_code != 200:
                raise DemoBlocked(f'本机 FLUX 请求未成功（HTTP {r.status_code}）。')
            data = r.json()
            raw = base64.b64decode(data['data'][0]['b64_json'], validate=True)
            im = Image.open(BytesIO(raw)); im.load()
            if im.size != (1280, 704):
                raise DemoBlocked('实际生图尺寸不符合冻结配置。')
            target.write_bytes(raw)
            write_json(receipt, {'request': request, 'gateway_receipt': data.get('receipt_id'),
                'sha256': file_digest(target), 'local_inference': True})


def anchor(work, role, *, prepare_only=False, variant=''):
    prompts = load(work / 'source-preparation/prompt-suite.json')
    story = load(work / 'plans/humanities-story.json')
    shot_id = {'landscape': 'poetry-line-03', 'washerwoman': 'poetry-line-05', 'boat': 'poetry-line-06'}[role]
    shot = next(s for s in story['shots'] if s['unit_id'] == shot_id)
    refs = []
    if role != 'landscape':
        approved(work, 'references', 'landscape')
        refs = ['landscape.png']
    client = DemoDeepSeek(load_private_key(ROOT / '.env.local'), work / 'model-audit', allow_official_api=True)
    try:
        caption = client.generate(ExpandedCaption, 'reference-caption-' + role,
            prompts['G_image_style'] + '\n' + prompts['G_image_expansion'] +
            '\n这是美术参考资产设计，不是课程单元。landscape时没有参考图，首次建立山谷。'
            'washerwoman/boat时只有landscape参考，第一次设计人物或舟，不能宣称已有角色参考。'
            '人物参考采用全身中景，主体清楚、衣服和道具可辨，脸与双手自然。',
            {'role': role, 'actual_references': refs, 'shot': shot})
        write_json(work / 'plans/captions' / ('reference-' + role + '.json'), caption.model_dump())
        if prepare_only:
            return
        name = 'reference-' + role + ('-' + variant if variant else '')
        generate_candidates(work, name, caption.caption, refs,
                            {'landscape': 202611130, 'washerwoman': 202611140, 'boat': 202611150}[role])
    finally:
        client.close()


def scenes(work):
    story = load(work / 'plans/humanities-story.json')
    for index, shot in enumerate(story['shots']):
        if shot['reuse_unit_id']:
            continue
        if shot['unit_id'] in choices(work).get('units', {}):
            approved(work, 'units', shot['unit_id'])
            continue
        refs = []
        for role in shot['reference_roles']:
            approved(work, 'references', role)
            refs.append(role + '.png')
        caption = load(work / 'plans/captions' / (shot['unit_id'] + '.json'))['caption']
        bindings = ', '.join(f'<ref_image_{i}> is the approved {Path(ref).stem} design' for i, ref in enumerate(refs))
        generate_candidates(work, shot['unit_id'], caption + '\nReference identities: ' + bindings,
                            refs, 202612000 + index * 10)


def materialize(work):
    story = load(work / 'plans/humanities-story.json')
    result = {}
    for shot in story['shots']:
        uid = shot['unit_id']
        reuse = shot['reuse_unit_id']
        if reuse:
            if reuse not in result:
                raise DemoBlocked('复用引用尚未出现的图片。')
            result[uid] = result[reuse]
        else:
            result[uid] = str(approved(work, 'units', uid))
    write_json(work / 'selected-images.json', result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['anchor', 'scenes', 'select', 'materialize'])
    p.add_argument('--work', type=Path, default=WORK)
    p.add_argument('--role', choices=['landscape', 'washerwoman', 'boat'], default='landscape')
    p.add_argument('--category', choices=['references', 'units'], default='units')
    p.add_argument('--name')
    p.add_argument('--path', type=Path)
    p.add_argument('--prepare-only', action='store_true')
    p.add_argument('--variant', default='')
    args = p.parse_args()
    if args.command == 'anchor': anchor(args.work, args.role, prepare_only=args.prepare_only, variant=args.variant)
    elif args.command == 'scenes': scenes(args.work)
    elif args.command == 'materialize': materialize(args.work)
    else:
        if not args.name or not args.path:
            p.error('select requires --name and --path after image inspection')
        select(args.work, args.category, args.name, args.path)


if __name__ == '__main__':
    main()
