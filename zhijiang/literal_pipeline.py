"""New source-reading workflow, isolated from retained explanatory benchmarks."""
from __future__ import annotations
import json
from pathlib import Path
import wave
from zhijiang.agents import AIAgents,GenerationError
from zhijiang.models import Mode
from zhijiang.course_styles import build_course_style,plan_comic_story
from zhijiang.literal_course import build_literal_course,verify_literal
from zhijiang.source_subtitles import synthesize_source,burn_source_subtitles
from zhijiang.generated_media import enrich_generated_media
from zhijiang.page_media import _run,_encoding,clip_duration
from zhijiang.presentation import PresentationError


def render_source_motion(lesson,document,client,folder,pdf_path,options,progress,audio_files,*,planning_client=None,review_client=None):
    requests=lesson.deck_plan.get('construction_requests',[])
    if not requests or options.animation_mode=='basic' or client is None:return
    from zhijiang.visual_planning import visual_capabilities
    if not visual_capabilities()['ready']:
        lesson.animation_report.setdefault('warnings',[]).append({'stage':'source_motion',
            'message':'连续演示依赖未就绪，保留SVG教学图示与完整原文。'})
        return
    from zhijiang.automatic_construction import enrich_constructions
    from zhijiang.visual_media import render_visual_scene
    clone=lesson.model_copy(deep=True)
    # Geometry planners may create explanatory narration internally. It is never
    # substituted for the source narration, and never sent to a speech service.
    enrich_constructions(clone,document,client,requests,folder=folder/'source-constructions',
        pdf_path=pdf_path,progress=progress,planning_client=planning_client,review_client=review_client)
    for name in ('construction_routing','construction_groups'):
        if clone.animation_report.get(name):lesson.animation_report[name]=clone.animation_report[name]
    for index,(original,planned,audio) in enumerate(zip(lesson.segments,clone.segments,audio_files),1):
        if planned.visual_scene is None:continue
        with wave.open(str(audio)) as w:target_duration=w.getnframes()/w.getframerate()
        duration_per_beat=target_duration/len(planned.visual_scene.beats)
        class TimingOnlySpeech:
            voice='silent-visual-timing-'+str(duration_per_beat)
            def synthesize(self,text,output):
                with wave.open(str(output),'wb') as w:
                    w.setnchannels(1);w.setsampwidth(2);w.setframerate(24000)
                    w.writeframes(b'\x00\x00'*max(1,round(duration_per_beat*24000)))
        scene_folder=folder/'visual'/f'scene-{index:02d}'
        asset=render_visual_scene(planned.visual_scene,TimingOnlySpeech(),scene_folder)
        clip=scene_folder/'clip.mp4';duration=clip_duration(clip);temporary=scene_folder/'literal-audio.mp4'
        _run(['-i',str(clip),'-i',str(audio),'-map','0:v:0','-map','1:a:0',
              '-vf',f'setpts={target_duration/duration:.12f}*PTS,tpad=stop_mode=clone:stop_duration=1',
              *_encoding(),'-t',str(target_duration),str(temporary)],temporary)
        temporary.replace(clip)
        original.visual_scene=planned.visual_scene
        lesson.animation_report.setdefault('source_motion',[]).append({'segment_id':index,
            'audio':'verbatim_source_only','timing':'scaled_to_full_source_audio_not_word_aligned',
            'scene_duration':target_duration,'verification':asset.get('verification',{})})
    lesson.animation_report.setdefault('warnings',[]).extend(clone.animation_report.get('warnings',[]))
    scenes=[{'segment_id':i,'plan':s.visual_scene.model_dump(mode='json')} for i,s in enumerate(lesson.segments,1) if s.visual_scene]
    if scenes:
        lesson.animation_report.update(scene_count=len(scenes),scene_type='general')
        (folder/'scene-data.json').write_text(json.dumps({'mode':'source_verbatim','scenes':scenes,
            'speech':'original_source_only','timing':'scaled_to_source_audio_not_word_aligned'},ensure_ascii=False,indent=2),encoding='utf8')


def run_literal_job(processor,document,agents,options,voice_mode,speech,folder,progress,job_id=None,*,planning_client=None,review_client=None):
    from zhijiang.subject_routing import route_subject
    folder=Path(folder);client=agents.client if isinstance(agents,AIAgents) else None
    resolved=options.model_copy(deep=True);routing=None
    if options.style_mode=='subject':
        if client is None:raise GenerationError('自动学科编排需要真实AI模式。')
        progress('阅读教材并识别学科',22)
        routing=route_subject(document,client,folder/'subject-routing')
        resolved.ppt_template=routing['selection']['template_id']
        resolved.style_mode=routing['selection']['presentation']
    if resolved.style_mode=='comic':
        from zhijiang.drama import require_drama_service
        require_drama_service(options.art)
    lesson=build_literal_course(document,client,resolved,voice_mode,folder/'literal-slides',progress,
        routing=routing,mode=Mode.AI if client else Mode.DEMO)
    build_course_style(lesson,resolved.style_mode,client,folder/'course-style',prompt=options.prompt)
    if resolved.style_mode=='comic':
        progress('规划漫剧角色、场景与连续镜头',65)
        plan_comic_story(lesson,client,folder/'course-style')
        (folder/'presentation-plan.json').write_text(json.dumps(lesson.deck_plan,ensure_ascii=False,indent=2),encoding='utf8')
    if job_id:processor.store.save_lesson(job_id,lesson)
    enrich_generated_media(lesson,options.art,folder,progress)
    verify_literal(document,lesson)
    audio_files,records=synthesize_source(lesson,speech,folder,progress)
    if options.learner.enabled:
        from zhijiang.learning_plan import build_learning_plan
        progress('根据学习基础、目标与时间安排学习路径',85)
        build_learning_plan(lesson, options.learner, client, folder/'learning-plan',
            audio_seconds=sum(r['audio_duration'] for r in records))
    try:
        render_source_motion(lesson,document,client,folder,folder/'source.pdf',resolved,progress,audio_files,
            planning_client=planning_client,review_client=review_client)
    except (GenerationError,PresentationError,ValueError) as exc:
        lesson.animation_report.setdefault('warnings',[]).append({'stage':'source_motion','message':str(exc)})
        # Only keep scenes whose video was actually completed with source audio.
        completed={r['segment_id'] for r in lesson.animation_report.get('source_motion',[])}
        for i,s in enumerate(lesson.segments,1):
            if i not in completed:s.visual_scene=None
    report=verify_literal(document,lesson)
    completed_motion={r['segment_id'] for r in lesson.animation_report.get('source_motion',[])}
    for key,entry in lesson.deck_plan.get('visual_story',{}).get('visuals',{}).items():
        entry['executed_medium']=('computed' if int(key) in completed_motion else
            'scene_drama' if resolved.style_mode=='comic' else
            'generated_illustration' if lesson.deck_plan.get('generated_media',{}).get(key) else 'svg')
    lesson.deck_plan['subtitle_tracks']={'burned':True,'srt':'subtitles.srt','vtt':'subtitles.vtt',
        'raw':'subtitles.json','source_transcript':'source-transcript.txt','coverage':'source-coverage.json'}
    (folder/'source-coverage.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    (folder/'presentation-plan.json').write_text(json.dumps(lesson.deck_plan,ensure_ascii=False,indent=2),encoding='utf8')
    if job_id:processor.store.save_lesson(job_id,lesson)
    progress('导出PPT与原文朗读视频',88)
    processor.presentation_renderer(lesson,folder/'lesson.pptx')
    processor.video_renderer(lesson,audio_files,folder/'lesson.mp4')
    progress('逐字核对并写入完整原文字幕',96)
    burn_source_subtitles(lesson,records,folder/'lesson.mp4')
    verify_literal(document,lesson)
    return lesson
