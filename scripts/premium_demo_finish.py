"""Render measured source-audio demos, assemble static-deck inputs, verify delivery."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import wave
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from zhijiang.premium_demo import DemoBlocked, apply_display_overrides, file_digest, validate_source, write_json
from zhijiang.demo_math import (bind_source_audio, render_plan, validate_plan,
    circle_summary_svg, circle_line_geometry, circle_comparison_svg,
    example_summary_svg, textbook_geometry)

WORK = ROOT / 'data/premium-demo-20261113'


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def ffmpeg(args, logfile):
    import imageio_ffmpeg
    result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-y', *args],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    logfile.parent.mkdir(parents=True, exist_ok=True)
    logfile.write_bytes(result.stdout)
    if result.returncode:
        raise DemoBlocked('FFmpeg 未完成，详见本次私有运行日志。')


def math(work, revision):
    source = load(work / 'source-preparation/math-source.json')
    validate_source(source)
    timeline = load(work / 'audio/math/timeline.json')
    if [u['text'] for u in timeline['units']] != [u['text'] for u in source['units']]:
        raise DemoBlocked('音轨正文与冻结数学来源不同。')
    plans = {scene: load(work / 'plans' / f'math-{scene}.json')
             for scene in ('circle_line', 'textbook_example')}
    if not revision or not all(c.isalnum() or c in '-_' for c in revision):
        raise DemoBlocked('渲染版本名称仅允许字母、数字、横线和下划线。')
    folder = work / ('math-final-' + revision)
    if folder.exists():
        raise DemoBlocked('此渲染版本目录已存在，请使用新的版本名称保留首次结果。')
    measured = [{key: u[key] for key in ('unit_id', 'text', 'start', 'end')}
                for u in timeline['units']]
    bound = bind_source_audio(plans, measured, work / 'audio/math/master.wav', folder / 'bound')
    videos = []
    for scene in plans:
        plan_path = folder / 'bound' / f'{scene}-audio-bound.json'
        # The math interpreter writes its normalized bound plans with this name.
        if not plan_path.exists():
            plan_path = folder / 'bound' / f'{scene}.json'
            write_json(plan_path, bound[scene])
        receipt = render_plan(plan_path, folder / scene, preview=False)
        videos.append(Path(receipt['movie']))
    listing = folder / 'concat.txt'
    listing.write_text(''.join("file '" + v.resolve().as_posix().replace("'", "'\\''") + "'\n" for v in videos), encoding='utf-8')
    out = work / 'deliverables' / ('math-' + revision)
    out.mkdir(parents=True, exist_ok=True)
    target = out / '直线与圆_完整原文几何演示.mp4'
    # Each Manim part may contain its own preview audio. Drop it, and mux the
    # untouched complete master once to avoid any repeated sentence at the join.
    ffmpeg(['-f', 'concat', '-safe', '0', '-i', str(listing), '-i', str(work / 'audio/math/master.wav'),
        '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
        '-movflags', '+faststart', str(target)], folder / 'final-mux.log')
    ffmpeg(['-i', str(target), '-f', 'null', '-'], folder / 'final-decode.log')
    receipt = {'video': str(target), 'sha256': file_digest(target), 'revision': revision,
        'source_sha256': file_digest(work / 'source-preparation/math-source.json'),
        'audio_master_sha256': file_digest(work / 'audio/math/master.wav'), 'audio_uses': 1,
        'dimensions': [1920, 1080], 'fps': 30, 'complete_decode': True,
        'full_listening_review': False, 'powerpoint_slideshow_review': False}
    write_json(folder / 'final-receipt.json', receipt)
    write_json(work / 'math-current-candidate.json', {**receipt, 'render_directory': str(folder),
        'approved_for_demo': False})


def static_math(work):
    """Static diagrams from the real model's frozen scene parameters and deck."""
    source = load(work / 'source-preparation/math-source.json')
    validate_source(source)
    units = {u['unit_id']: u for u in source['units']}
    for scene in ('circle_line', 'textbook_example'):
        validate_plan(load(work / 'plans' / f'math-{scene}.json'))
    deck = load(work / 'plans/math-deck.json')
    if (work / 'plans/display-overrides.json').exists():
        deck = apply_display_overrides(deck, load(work / 'plans/display-overrides.json'), 'math')
    folder = work / 'math-static-final'
    folder.mkdir(parents=True, exist_ok=True)
    pages = []
    for index, page in enumerate(deck['pages'], 1):
        end = int(page['unit_ids'][-1].split('-')[-1])
        if end <= 15:
            svg = (circle_comparison_svg() if len(page['unit_ids']) > 2 or end > 7 else
                   circle_summary_svg(circle_line_geometry((0, 0), 2, (0, 1), 3 if end <= 6 else 2)))
        else:
            stage = 'show_circle' if end <= 17 else 'reason_DF' if end <= 21 else 'conclusion'
            svg = example_summary_svg(textbook_geometry(), stage=stage)
        path = folder / f'{index:02d}.svg'
        path.write_text(svg, encoding='utf-8')
        pages.append({**page, 'svg': str(path.resolve()),
            'source_text': '\n'.join(units[u]['text'] for u in page['unit_ids']),
            'source_note': '人教数学九年级上册第148—149页。原页保留个人学习水印；本页示意由声明式数学执行器实际计算。'})
    write_json(work / 'math-static-final-deck-manifest.json', {'decks': [{'case': 'math', 'pages': pages}]})
    write_json(folder / 'provenance.json', {'source': 'real model deck plan and validated scene parameters',
        'actual_computation': True, 'literal_video_frames': False,
        'module_sha256': file_digest(ROOT / 'zhijiang/demo_math.py'),
        'display_overrides': str(work / 'plans/display-overrides.json')})


def deck_manifest(work):
    decks = []
    for name in ('math', 'humanities'):
        source = load(work / 'source-preparation' / f'{name}-source.json')
        units = {u['unit_id']: u for u in source['units']}
        deck = load(work / 'plans' / f'{name}-deck.json')
        if (work / 'plans/display-overrides.json').exists():
            deck = apply_display_overrides(deck, load(work / 'plans/display-overrides.json'), name)
        pages = []
        for page in deck['pages']:
            ids = page['unit_ids']
            row = {**page, 'source_text': '\n'.join(units[u]['text'] for u in ids)}
            if name == 'math':
                # Use computed, stage-matched SVGs, not diffusion geometry.
                last = int(ids[-1].split('-')[-1])
                if last <= 15:
                    stage = 'show_line' if last <= 5 else 'show_distance' if last <= 6 else 'hold_tangent' if last == 7 else 'compare_states'
                    svg_folder = work / 'math-final/circle_line'
                    if last in {13, 14, 15} and len(ids) == 1:
                        stage = {13:'set_disjoint',14:'set_tangent',15:'set_secant'}[last]
                else:
                    stage = 'show_circle' if last <= 17 else 'reason_DF' if last <= 21 else 'conclusion'
                    svg_folder = work / 'math-final/textbook_example'
                candidates = list(svg_folder.glob('*-' + stage + '.svg'))
                if not candidates:
                    raise DemoBlocked('缺少对应实际步骤的数学 SVG，不能用占位图导出。')
                row['svg'] = str(candidates[-1].resolve())
                row['source_note'] = '人教数学九年级上册，原印页148—149；保留原页个人学习水印。'
            else:
                image_map = work / 'selected-presentation-images.json'
                images = load(image_map if image_map.exists() else work / 'selected-images.json')
                row['image'] = images[ids[0]]
                poem = next((units[u]['text'] for u in ids if units[u]['kind'] == 'poem_line'), None)
                row['poem_line'] = poem
                row['source_note'] = '王维原诗为公版；赏析为项目原创CC BY 4.0补充读本。插画为AI情境演绎。'
            pages.append(row)
        decks.append({'case': name, 'pages': pages})
    write_json(work / 'deck-manifest.json', {'decks': decks})


def package(work):
    """Explicit allowlist: never add runtime, credentials, weights or API logs."""
    current = load(work / 'math-current-candidate.json')
    math_video = Path(current['video']).resolve()
    if not math_video.is_relative_to(work.resolve()) or file_digest(math_video) != current['sha256']:
        raise DemoBlocked('当前数学成片路径或哈希不一致。')
    required = [math_video,
                work / 'deliverables/humanities/山居秋暝_原诗与赏析.mp4',
                work / 'ppt-final/output/直线与圆_几何课件.pptx',
                work / 'ppt-final/output/山居秋暝_插画分镜课件.pptx']
    if not all(p.exists() for p in required):
        raise DemoBlocked('两套真实完整成品尚未齐全，不能打包成最终交付。')
    for name in ('math', 'humanities'):
        review_path = work / 'reviews' / f'{name}-final.json'
        if not review_path.exists() or load(review_path).get('approved_for_visual_demo') is not True:
            raise DemoBlocked('两套成品尚未完成展示前复核，不能打包成最终交付。')
        reviewed = load(review_path)
        video = required[0 if name == 'math' else 1]
        if reviewed.get('video_sha256') != file_digest(video):
            raise DemoBlocked('复核记录与待交付视频哈希不同。')
        ppt_review_name = 'math-ppt-independent.json' if name == 'math' else 'humanities-ppt-final.json'
        ppt_review = load(work / 'reviews' / ppt_review_name)
        if (not ppt_review['approved_for_static_demo']
                or ppt_review['sha256'] != file_digest(required[2 if name == 'math' else 3])):
            raise DemoBlocked('课件复核记录与待交付文件不同。')
    audio_review = load(work / 'reviews/audio-final.json')
    if not audio_review['waveform']['master_sample_checks_passed']:
        raise DemoBlocked('完整音轨尚未通过采样检查。')
    for row in audio_review['cases']:
        if (not row['source_coverage_complete_in_order'] or not row['each_unit_present_once_in_master']
                or row['master_raw_wav_sha256'] != file_digest(work / f'audio/{row["case"]}/master.wav')):
            raise DemoBlocked('音轨复核记录与当前母带不一致。')
    # OOXML check distinguishes static PPTX from embedded AV courseware.
    for p in required[2:]:
        with zipfile.ZipFile(p) as archive:
            if any(n.lower().endswith(('.mp4','.wav','.mp3','.m4a','.webm')) for n in archive.namelist()):
                raise DemoBlocked('课件中检测到嵌入音视频。')
    dest = ROOT / 'output/premium-demo-20261113'
    dest.mkdir(parents=True, exist_ok=True)
    output = dest / 'LivelyClass_两份精品Demo_完整包.zip'
    if output.exists():
        raise DemoBlocked('交付包已经存在，禁止覆盖；修订时使用独立交付目录。')
    inputs = list((work / 'source-preparation').glob('*.pdf'))
    if len(inputs) != 2:
        raise DemoBlocked('交付输入必须恰好是本次两份已冻结教材。')
    guides = [work / 'source-preparation/完整提示词.md', work / 'source-preparation/demo-profile.json',
        work / 'source-preparation/来源与阅读口径.md', work / 'guides/数学学习计划.json',
        work / 'guides/诗词学习计划.json', work / 'guides/数学学习计划.md',
        work / 'guides/诗词学习计划.md', work / 'guides/Demo使用说明.md',
        work / 'guides/实际阶段提示词.json', work / 'guides/实际画面提示词.md',
        work / 'guides/实际运行配置.md',
        work / 'source-preparation/math-source.json', work / 'source-preparation/humanities-source.json',
        work / 'source-preparation/speech-normalizations.json',
        work / 'plans/display-overrides.json', work / 'plans/study-display-overrides.json']
    import re
    for p in guides:
        if re.search(r'\bsk-[A-Za-z0-9]{16,}|\bhf_[A-Za-z0-9]{20,}', p.read_text(encoding='utf-8')):
            raise DemoBlocked('交付文本检测到疑似凭据，已阻止打包。')
    import uuid
    staging_dir = work / 'delivery-build'
    staging_dir.mkdir(exist_ok=True)
    staging_zip = staging_dir / (uuid.uuid4().hex + '.zip')
    with zipfile.ZipFile(staging_zip, 'w', zipfile.ZIP_DEFLATED) as z:
        for p in required:
            z.write(p, '课程/' + p.name)
        for p in inputs: z.write(p, '输入教材/' + p.name)
        for p in guides: z.write(p, '提示词与计划/' + p.name)
        for p in (work / 'guides/subtitles').glob('*.srt'):
            z.write(p, '完整原文字幕/' + p.name)
        z.write(work / 'tts/voice-anchor.wav', '语音参考/原创合成声线.wav')
        z.write(work / 'tts/voice-anchor.json', '语音参考/声线选择记录.json')
        for case, label in (('math', '数学'), ('humanities', '诗词')):
            z.write(work / f'audio/{case}/master.wav', f'原始音轨/{label}_48kHz.wav')
            z.write(work / f'audio/{case}/timeline.json', f'原始音轨/{label}_原文与时间线.json')
        images = load(work / 'selected-images.json')
        for uid, image in images.items(): z.write(image, '人文插画/' + uid + '.png')
        for p in (work / 'image-references').glob('*.png'):
            z.write(p, '人文参考图/' + p.name)
        for p in (work / 'comparison-images').glob('*'):
            if p.suffix in {'.png', '.json'}: z.write(p, '人文对照画面/' + p.name)
        for p in Path(current['render_directory']).glob('*/*.svg'): z.write(p, '数学关键帧/' + p.parent.name + '/' + p.name)
        for p in (work / 'delivery-keyframes/math').glob('*.png'):
            z.write(p, '数学实际视频关键帧/' + p.name)
        for p in (work / 'humanities-final/keyframes').glob('*.png'):
            z.write(p, '诗词实际视频关键帧/' + p.name)
    # Reopen after closing: Windows ZipInfo.orig_filename can still contain
    # backslashes during write mode, while the encoded member uses forward
    # slashes. The finalized central directory has the normalized names.
    records = []
    with zipfile.ZipFile(staging_zip) as z:
        for name in z.namelist():
            raw = z.read(name)
            import hashlib
            records.append({'file': name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
    with zipfile.ZipFile(staging_zip, 'a', zipfile.ZIP_DEFLATED) as z:
        z.writestr('文件清单.json', json.dumps({'files': records, 'selected_showcase_cases': 2,
            'complete_human_listening': False, 'powerpoint_slideshow_review': False}, ensure_ascii=False, indent=2))
    with zipfile.ZipFile(staging_zip) as z:
        if z.testzip() is not None or len(z.namelist()) != len(set(z.namelist())):
            raise DemoBlocked('交付压缩包完整性检查未通过。')
    staging_zip.rename(output)
    write_json(dest / 'delivery.json', {'path': str(output), 'sha256': file_digest(output),
        'contains_api_keys': False, 'contains_model_weights': False,
        'showcase_not_benchmark': True, 'math_original_private_learning_only': True})
    print(str(output))


def guides(work):
    profile = load(work / 'source-preparation/demo-profile.json')
    folder = work / 'guides'
    folder.mkdir(parents=True, exist_ok=True)
    edit_file = work / 'plans/study-display-overrides.json'
    edits = load(edit_file)['edits'] if edit_file.exists() else []
    for case, label in (('math', '数学'), ('humanities', '诗词')):
        source = load(work / 'source-preparation' / f'{case}-source.json')
        study = load(work / 'plans' / f'{case}-study.json')
        for edit in [e for e in edits if e['case'] == case]:
            day = next(d for d in study['days'] if d['day'] == edit['day'])
            old = day[edit['field']] if 'index' not in edit else day[edit['field']][edit['index']]
            if old != edit['before']:
                raise DemoBlocked('学习计划展示修订与原始模型输出不匹配。')
            if 'index' in edit:
                day[edit['field']][edit['index']] = edit['after']
            else:
                day[edit['field']] = edit['after']
        write_json(folder / f'{label}学习计划.json', study)
        learner = profile['learner_profiles'][case]
        lines = [f'# {source["title"]}学习计划', '', f'学习基础：{learner["background"]}', '',
                 f'学习目标：{learner["goal"]}', '',
                 f'安排：{learner["days"]}天，每天{learner["minutes_per_day"]}分钟。', '']
        timeline = load(work / f'audio/{case}/timeline.json')
        times = {u['unit_id']: u for u in timeline['units']}
        clock = lambda t: f'{int(t)//60}:{int(t)%60:02d}'
        for day in study['days']:
            lines += [f'## 第{day["day"]}天：{day["title"]}', '']
            lines += [f'- {activity}' for activity in day['activities']]
            lines += ['', '自测建议：' + day['self_check'], '',
                      '对应课程单元：' + '、'.join(day['unit_ids']), '']
            start = min(times[uid]['start'] for uid in day['unit_ids'])
            end = max(times[uid]['end'] for uid in day['unit_ids'])
            lines += [f'视频位置：{clock(start)}—{clock(end)}。其余时间用于观察、练习与复述。', '']
        (folder / f'{label}学习计划.md').write_text('\n'.join(lines), encoding='utf-8')
    (folder / 'Demo使用说明.md').write_text(
        '# Lively Class 两教材 Demo\n\n'
        '先播放两份MP4展示完整课程，再打开静态PPTX展示可独立使用的课件。动态演示位于MP4，PPT不嵌入音视频。'
        '数学展示圆线位置关系与教材原例；诗词展示完整原诗和原创赏析的插画分镜。\n\n'
        '讲稿、字幕和配音输入来自同一冻结原文单元表；PPT可以概括。数学扫描页由原图核对转写，公式口读映射另行记录；'
        '诗词赏析是项目原创补充读本。教材原页仅供同一用户个人学习，保留水印与署名。\n\n'
        '本次是离线准备的两个精选展示案例。理解和规划使用DeepSeek官方API；图像使用本机FLUX 3 Image Klein INT8，'
        '语音使用本机VoxCPM2，数学由可信声明式Manim执行器实际计算绘制。插画中的人物不是生成视频动画。\n\n'
        '“提示词与计划”包含完整提示词、模型配置和逐日学习安排。密钥、权重、缓存与原始API响应不包含在交付包内。\n', encoding='utf-8')
    timings = {case: load(work / f'audio/{case}/timeline.json') for case in ('math', 'humanities')}
    anchor = load(work / 'tts/voice-anchor.json')
    weights = load(work / 'runtime/flux-model-manifest.json')
    details = ['# 实际运行配置', '',
        '本次在 Windows 本机完成，RTX 4060 Laptop 8GB、32GB 内存。仅理解、规划和看图复核调用 DeepSeek 官方接口。', '',
        '## 理解与规划', '',
        f'- 模型ID：{profile["llm"]["model"]}；入口：{profile["llm"]["base_url"]}。',
        '- 使用 JSON 结构化输出和分阶段独立上下文；理解、课件和学习计划为 high，数学构造与复核为 max。',
        '- Agent 输出声明式对象、动作与时间规划；可信程序进行几何计算和 Manim 绘制，不执行模型任意 Python。', '',
        '## 本地插画', '',
        f'- 权重仓库：{weights["repo"]}；revision：{weights["revision"]}。',
        '- INT8 ConvRot；16步；无额外CFG；1280×704；分块VAE解码；文本编码器在CPU，绘图网络在CUDA。',
        '- 每组3个固定种子候选，实际查看后选图。母图和人物/舟参考图参与后续镜头生成。',
        '- 插画没有生成文字；诗句、关键词和字幕由程序排版。视频使用平稳推拉与转场。', '',
        '## 本地语音与原文', '',
        f'- VoxCPM2 revision：{anchor["revision"]}；原生48kHz、单声道、24-bit PCM。',
        f'- 用户选择声线1；参考音频SHA-256：{anchor["wav_sha256"]}。',
        '- CFG 2.0、10步；图像和语音模型分时加载。',
        '- 原文、字幕与实际送读文字分别保存。数学符号按公开映射口读，另对独立字母r采用“阿尔”读法，字幕保留r。',
        '- 时间线按真实采样数计算；长段内部字幕采用字符比例估计，未标为逐字强制对齐。',
        f'- 数学：{len(timings["math"]["units"])}个单元，{timings["math"]["seconds"]:.2f}秒。',
        f'- 诗词：{len(timings["humanities"]["units"])}个单元，{timings["humanities"]["seconds"]:.2f}秒。', '',
        '## 输出与检查', '',
        '- 视频1920×1080、30fps，完整原文音轨只合成进入成片一次；PPTX为独立静态课件。',
        '- 检查原文覆盖、音轨采样、字幕、实际图形/插画与视频关键帧，并进行独立本地ASR定位可疑读音。',
        '- ASR和字符检查不能代替人工听辨。本次未记录完整人工试听或PowerPoint应用内放映验收。', '',
        '此配置和选图面向这两个精选案例，未替代普通网页的通用流程或广泛教材评测。', '']
    (folder / '实际运行配置.md').write_text('\n'.join(details), encoding='utf-8')
    # Export only successful stage request instructions, never reasoning traces
    # or authorization headers. Images are represented by hashes, not base64.
    import copy
    import base64
    import hashlib
    stages = []
    audit_root = work / 'model-audit'
    if audit_root.exists():
        for stage in audit_root.iterdir():
            if not stage.is_dir() or stage.name == 'environment':
                continue
            candidates = sorted(stage.rglob('validated.json'), key=lambda p: p.stat().st_mtime)
            if not candidates:
                continue
            folder = candidates[-1].parent
            requests = sorted(folder.rglob('request-*.json'), key=lambda p: p.stat().st_mtime)
            if not requests:
                continue
            request_path = requests[-1]
            body = copy.deepcopy(load(request_path))
            for message in body.get('messages', []):
                if not isinstance(message.get('content'), list):
                    continue
                for part in message['content']:
                    if part.get('type') == 'image_url':
                        url = part['image_url']['url']
                        raw = base64.b64decode(url.split(',', 1)[1])
                        part['image_url'] = {'replaced_in_delivery': True,
                            'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
                            'reason': '对应教材原页或实际数学关键帧，图片另在交付材料中提供。'}
            stages.append({'stage': stage.name, 'actual_request_sha256': file_digest(request_path),
                           'request_without_credentials_or_image_base64': body})
    write_json(work / 'guides/实际阶段提示词.json', {'successful_stages': stages,
        'raw_model_reasoning_included': False, 'api_headers_included': False})
    selected = work / 'selected-images.json'
    lines = ['# 实际选图提示词', '', '以下从已选图片的真实请求收据导出。固定种子用于复现，参考图用于维持场景或人物一致。', '']
    if selected.exists():
        for uid, image in load(selected).items():
            path = Path(image)
            receipt = load(path.with_suffix('.json'))
            req = receipt['request']
            lines += [f'## {uid}', '', f'种子：{req["seed"]}；参考图：' + '、'.join(req['reference_images']), '',
                      req['prompt'], '', f'图片SHA-256：{file_digest(path)}', '']
    (work / 'guides/实际画面提示词.md').write_text('\n'.join(lines), encoding='utf-8')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['math', 'static-math', 'deck-manifest', 'guides', 'package'])
    p.add_argument('--work', type=Path, default=WORK)
    p.add_argument('--revision', default='v2')
    a = p.parse_args()
    if a.command == 'math':
        math(a.work, a.revision)
    else:
        {'static-math': static_math, 'deck-manifest': deck_manifest, 'guides': guides, 'package': package}[a.command](a.work)


if __name__ == '__main__':
    main()
