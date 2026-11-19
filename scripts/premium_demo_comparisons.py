"""Compose two approved, uncropped illustrations for appreciation comparisons."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'data/premium-demo-20261113'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def compose(work):
    original = json.loads((work / 'selected-images.json').read_text(encoding='utf-8'))
    result = dict(original)
    folder = work / 'comparison-images'
    folder.mkdir(exist_ok=True)
    font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 34)
    groups = [
        ('poetry-appreciation-02', [('poetry-line-03', '松间明月 · 静'), ('poetry-line-04', '石上清泉 · 动')]),
        ('poetry-appreciation-03', [('poetry-line-05', '竹喧 · 浣女归来'), ('poetry-line-06', '莲动 · 渔舟下行')]),
    ]
    for uid, members in groups:
        target = folder / f'{uid}.png'
        receipt = target.with_suffix('.json')
        if target.exists() or receipt.exists():
            raise ValueError('Keep the original composition; use a new work directory for revisions.')
        canvas = Image.new('RGB', (1920, 1080), '#102226')
        draw = ImageDraw.Draw(canvas)
        records = []
        for index, (source_uid, label) in enumerate(members):
            image_path = Path(original[source_uid])
            with Image.open(image_path) as im:
                im = im.convert('RGB')
                if im.size != (1280, 704):
                    raise ValueError('Comparison expects the frozen 1280x704 images.')
                panel = im.resize((900, 495), Image.Resampling.LANCZOS)
            x, y = 48 + index * 924, 282
            draw.rounded_rectangle((x - 2, y - 2, x + 902, y + 497), radius=6, fill='#7E9A94')
            canvas.paste(panel, (x, y))
            draw.text((x + 8, 226), label, font=font, fill='#D8E6DF')
            records.append({'unit_id': source_uid, 'path': str(image_path.resolve()),
                'sha256': digest(image_path), 'label': label, 'bounds': [x, y, 900, 495],
                'uncropped': True, 'aspect_ratio_preserved': True})
        canvas.save(target)
        receipt.write_text(json.dumps({'kind': 'approved_image_comparison', 'unit_id': uid,
            'image': str(target.resolve()), 'sha256': digest(target), 'local_composition': True,
            'additional_model_calls': 0, 'sources': records}, ensure_ascii=False, indent=2), encoding='utf-8')
        result[uid] = str(target.resolve())
    (work / 'selected-presentation-images.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, default=WORK)
    args = parser.parse_args()
    compose(args.work.resolve())


if __name__ == '__main__':
    main()
