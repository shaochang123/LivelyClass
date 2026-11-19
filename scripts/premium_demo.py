"""Explicit two-case showcase runner, separate from the normal web application.

Run with .venv/Scripts/python.exe scripts/premium_demo.py <command>.
No inference result is synthesized when a real dependency is missing.
"""
from __future__ import annotations

import argparse
import base64
from io import BytesIO
import json
from pathlib import Path
import re
import sys
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import httpx
from PIL import Image
from zhijiang.premium_demo import (DemoBlocked, DemoDeepSeek, DeckPlan, ExpandedCaption,
    MathPlan, Review, SourceUnderstanding, StoryPlan, StudyPlan, digest, file_digest,
    load_private_key, validate_source, validate_story, validate_study, write_json)

WORK = ROOT / 'data/premium-demo-20261113'


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def inputs(work):
    folder = work / 'source-preparation'
    sources = {name: load(folder / f'{name}-source.json') for name in ('math', 'humanities')}
    for source in sources.values():
        validate_source(source)
    return sources, load(folder / 'prompt-suite.json'), load(folder / 'demo-profile.json')


def verify_understanding(result, source):
    ids = [u['unit_id'] for u in source['units']]
    if result.unit_order != ids or {i for point in result.ideas for i in point.unit_ids} != set(ids):
        raise DemoBlocked('来源理解没有完整引用实际教材单元。')


def verify_deck(plan, source):
    if [i for page in plan.pages for i in page.unit_ids] != [u['unit_id'] for u in source['units']]:
        raise DemoBlocked('PPT 规划没有按顺序完整关联来源单元。')


def original_math_images():
    result = []
    for name in ('156.jpg', '157.jpg'):
        path = ROOT / 'data/textbook-benchmark-v3/sources/math-junior/pages' / name
        buf = BytesIO()
        Image.open(path).convert('RGB').save(buf, format='PNG')
        result.append(buf.getvalue())
    return result


def plan(work, private_config=None):
    from zhijiang.demo_math import default_plans, plan_schema, validate_plan
    sources, prompts, profile = inputs(work)
    llm = DemoDeepSeek(load_private_key(private_config), work / 'model-audit', allow_official_api=True)
    try:
        llm.capabilities()
        for name, source in sources.items():
            print('理解来源：' + name, flush=True)
            material = {'source': source, 'user_preferences': prompts[
                'B_math_frontend' if name == 'math' else 'E_humanities_frontend']}
            understanding = llm.generate(SourceUnderstanding, name + '-understanding',
                prompts['A_common'] + '\n独立理解来源的含义、条件与顺序，每个单元至少被一个含义条目引用；unit_order逐项复制来源ID。',
                material, images=original_math_images() if name == 'math' else None)
            verify_understanding(understanding, source)
            write_json(work / 'plans' / f'{name}-understanding.json', understanding.model_dump())
            print('规划课件：' + name, flush=True)
            deck = llm.generate(DeckPlan, name + '-deck', prompts['A_common'] +
                '\n规划静态课件。每个来源单元恰好属于一页，按原顺序，不删减来源音轨。'
                '数理页只用circle_line或textbook_example；人文页只用poem_illustration。'
                '文字用短标题与少量关键词。', {**material, 'understanding': understanding.model_dump()})
            verify_deck(deck, source)
            write_json(work / 'plans' / f'{name}-deck.json', deck.model_dump())
            learner = profile['learner_profiles'][name]
            study = llm.generate(StudyPlan, name + '-study', prompts['I_learning_plan'] +
                '\n本次引用字段统一为unit_ids，使用下方真实课程单元ID。每天活动至少两项，不要编造成绩。',
                {'source': source, 'learner': learner})
            validate_study(study, source, learner['days'])
            write_json(work / 'plans' / f'{name}-study.json', study.model_dump())
        print('规划人文分镜', flush=True)
        story = llm.generate(StoryPlan, 'humanities-story',
            prompts['A_common'] + prompts['E_humanities_frontend'] + prompts['F_storyboard'] +
            '\n每个单元一镜，包括标题作者、8诗行和4赏析。字段以Schema为准。'
            '开头标题不出现人物。前4诗行只含景物。reference_roles引用三种固定资产角色。',
            {'source': sources['humanities'], 'shot_guidance': prompts['G_shots']})
        validate_story(story, sources['humanities'])
        write_json(work / 'plans/humanities-story.json', story.model_dump())
        for shot in story.shots:
            if shot.reuse_unit_id:
                continue
            print('扩写画面：' + shot.unit_id, flush=True)
            result = llm.generate(ExpandedCaption, 'caption-' + shot.unit_id,
                prompts['G_image_style'] + '\n' + prompts['G_image_expansion'],
                {'shot': shot.model_dump(), 'source_unit': next(u for u in sources['humanities']['units']
                    if u['unit_id'] == shot.unit_id)})
            write_json(work / 'plans/captions' / f'{shot.unit_id}.json', result.model_dump())
        for scene in ('circle_line', 'textbook_example'):
            print('规划数学构造：' + scene, flush=True)
            candidate = llm.generate(MathPlan, 'math-' + scene,
                prompts['A_common'] + prompts['C_math_construction'] +
                '\n严格使用当前单场景动作契约，不增加字段；source与parameters复制例子。'
                '动作一次演示的时序先规划，原文音轨合成后由程序按实际单元时长绑定。',
                {'source': sources['math'], 'scene_schema': plan_schema(scene),
                 'schema_example': default_plans()[scene]}, effort='max')
            data = candidate.model_dump()
            validate_plan(data)
            write_json(work / 'plans' / f'math-{scene}.json', data)
        write_json(work / 'planning-complete.json', {'real_model': llm.model,
            'source_sha256': {n: file_digest(work / 'source-preparation' / f'{n}-source.json')
                for n in sources}, 'complete': True})
    finally:
        llm.close()


def preflight(work, private_config):
    from huggingface_hub import get_token
    status = {'hf_local_login': bool(get_token()), 'deepseek': False,
        'flux_weights': (work / 'runtime/flux-model-manifest.json').exists(),
        'vox_weights': (work / 'models/VoxCPM2/download-receipt.json').exists()}
    try:
        c = DemoDeepSeek(load_private_key(private_config), work / 'model-audit', allow_official_api=True)
        try:
            c.capabilities()
            status['deepseek'] = True
        finally:
            c.close()
    except DemoBlocked as e:
        status['deepseek_reason'] = str(e)
    write_json(work / 'preflight.json', status)
    print(json.dumps(status, ensure_ascii=False))


def synthesize(work, case):
    sources, _, _ = inputs(work)
    source = sources[case]
    folder = work / 'audio' / case
    folder.mkdir(parents=True, exist_ok=True)
    timeline, all_audio = [], bytearray()
    sample_count = 0
    with httpx.Client(timeout=1800, trust_env=False) as client:
        health = client.get('http://127.0.0.1:8794/health').json()
        if not health.get('voice_anchor_ready'):
            raise DemoBlocked('先试听并冻结本机 VoxCPM2 声线。')
        for unit in source['units']:
            target = folder / (unit['unit_id'] + '.wav')
            voice = 'math' if case == 'math' else 'poetry' if unit['kind'] in {'poem_line', 'title_author'} else 'prose'
            spoken = unit['tts_text']
            extra_normalizations = []
            if case == 'math' and re.search(r'(?<![A-Za-z])r(?![A-Za-z])', spoken):
                spoken = re.sub(r'(?<![A-Za-z])r(?![A-Za-z])', '阿尔', spoken)
                extra_normalizations.append({'source': 'r', 'reading': '阿尔',
                    'stage': 'runtime_letter_frontend',
                    'evidence': 'tts/letter-preflight-ab-summary.json',
                    'source_and_subtitle_changed': False})
            request = {'model': 'demo-voxcpm2', 'voice': voice, 'input': spoken,
                       'metadata': {'source_text': unit['text'], 'source_unit_id': unit['unit_id']}}
            request_sha = digest(json.dumps(request, sort_keys=True, ensure_ascii=False).encode())
            receipt = target.with_suffix('.receipt.json')
            if target.exists() and (not receipt.exists() or load(receipt).get('request_sha256') != request_sha
                                    or load(receipt).get('wav_sha256') != file_digest(target)):
                raise DemoBlocked('已有音频的来源凭据不匹配，不能静默复用或覆盖。')
            if not target.exists():
                print('合成原文：' + unit['unit_id'], flush=True)
                r = client.post('http://127.0.0.1:8794/v1/audio/speech', json=request)
                if r.status_code != 200:
                    raise DemoBlocked(f'TTS 请求未成功（HTTP {r.status_code}）。')
                target.write_bytes(r.content)
                write_json(receipt, {'request_sha256': request_sha, 'request': request,
                    'wav_sha256': file_digest(target), 'tts_request_id': r.headers.get('X-Demo-Request-ID')})
            with wave.open(str(target), 'rb') as wav:
                if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 3, 48000):
                    raise DemoBlocked('原始音频不是预期的48kHz单声道PCM24。')
                frames = wav.getnframes()
                raw = wav.readframes(frames)
            pause = .4 if case == 'math' else 1.1 if unit['kind'] == 'poem_line' else .65
            silence = int(48000 * pause)
            timeline.append({'unit_id': unit['unit_id'], 'text': unit['text'],
                'tts_text': spoken, 'start': sample_count / 48000,
                'speech_end': (sample_count + frames) / 48000,
                'end': (sample_count + frames + silence) / 48000,
                'sample_start': sample_count, 'samples': frames, 'pause_samples': silence,
                'unit_wav_sha256': file_digest(target),
                'normalizations': unit['normalizations'] + extra_normalizations})
            all_audio.extend(raw)
            all_audio.extend(b'\x00' * silence * 3)
            sample_count += frames + silence
    with wave.open(str(folder / 'master.wav'), 'wb') as wav:
        wav.setparams((1, 3, 48000, sample_count, 'NONE', 'not compressed'))
        wav.writeframes(all_audio)
    write_json(folder / 'timeline.json', {'sample_rate': 48000, 'samples': sample_count,
        'seconds': sample_count / 48000, 'source_text': source['source_text'], 'units': timeline,
        'master_sha256': file_digest(folder / 'master.wav'), 'complete': True})


def math_preview(work, private_config, only_scene=None):
    from zhijiang.demo_math import render_plan
    _, prompts, _ = inputs(work)
    c = DemoDeepSeek(load_private_key(private_config), work / 'model-audit', allow_official_api=True)
    try:
        for scene in ('circle_line', 'textbook_example'):
            if only_scene and only_scene != scene:
                continue
            folder = work / 'model-math-preview' / scene
            plan_path = work / 'plans' / f'math-{scene}.json'
            receipt_file = folder / 'render-receipt.json'
            receipt = load(receipt_file) if receipt_file.exists() else None
            if not (receipt and receipt.get('plan_sha256') == file_digest(plan_path) and
                    Path(receipt['movie']).is_file() and file_digest(Path(receipt['movie'])) == receipt['movie_sha256']):
                receipt = render_plan(plan_path, folder, preview=True)
            frames = list(folder.glob('*.png'))
            if not frames:
                frames = list(folder.rglob('*.png'))
            full_audit = load(folder / 'geometry-audit.json')
            audit_summary = {k: v for k, v in full_audit.items()
                             if k in {'scene', 'duration', 'source', 'geometry', 'checks', 'snapshots', 'actual_rendered_seconds'}}
            audit_summary['full_audit_sha256'] = file_digest(folder / 'geometry-audit.json')
            scope = ('本次只审核位置关系教学示意：圆O半径2、直线l的d从3经2到1。'
                '教材例1由另一独立textbook_example场景展示，不属于本镜头；本镜头不需要出现AC/BC或D/E/F。'
                if scene == 'circle_line' else
                '本次只审核教材例1：直角三角形ABC、D为AB中点、半径2.5、E在AC、F在BC；'
                '位置关系的连续平移由另一独立circle_line场景展示，不属于本镜头。')
            review = c.generate(Review, 'math-actual-preview-review-' + scene,
                prompts['D_math_review'] + '\n' + scope +
                '\n只审核当前场景应该承担的内容，不能因独立另一场景的内容未出现在本镜头而判错。'
                'items中unit_id使用场景或动作ID，逐项核对可见画面，不能仅复述目标。',
                {'plan': load(work / 'plans' / f'math-{scene}.json'),
                 'geometry_audit': audit_summary, 'receipt': receipt},
                effort='max', images=original_math_images() + [p.read_bytes() for p in frames[-6:]])
            previous = folder / 'model-visual-review.json'
            if previous.exists():
                history = folder / 'review-history'
                history.mkdir(exist_ok=True)
                import time
                previous.replace(history / (str(time.time_ns()) + '.json'))
            write_json(previous, review.model_dump())
    finally:
        c.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['preflight', 'plan', 'math-preview', 'tts'])
    p.add_argument('--work', type=Path, default=WORK)
    p.add_argument('--private-config', type=Path)
    p.add_argument('--case', choices=['math', 'humanities'], default='math')
    p.add_argument('--scene', choices=['circle_line', 'textbook_example'])
    args = p.parse_args()
    try:
        if args.command == 'preflight': preflight(args.work, args.private_config)
        elif args.command == 'plan': plan(args.work, args.private_config)
        elif args.command == 'math-preview': math_preview(args.work, args.private_config, args.scene)
        else: synthesize(args.work, args.case)
    except DemoBlocked as exc:
        write_json(args.work / 'last-blocker.json', {'stage': args.command, 'reason': str(exc)})
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)


if __name__ == '__main__':
    main()
