from io import BytesIO
import json
import wave
import zipfile
import pytest
from pathlib import Path
from PIL import Image
from reportlab.pdfgen import canvas
from zhijiang.models import GenerationOptions, Mode, VoiceMode
from zhijiang.config import Settings
from zhijiang.pipeline import JobProcessor
from zhijiang.storage import JobStore
from zhijiang.page_media import clip_duration, _run


class SourceSpeech:
    voice='test-source-audio'
    def __init__(self):self.inputs=[]
    def synthesize(self,text,path):
        self.inputs.append(text)
        import math,struct
        with wave.open(str(path),'wb') as w:
            w.setnchannels(1);w.setsampwidth(2);w.setframerate(24000)
            w.writeframes(b''.join(struct.pack('<h',int(3000*math.sin(2*math.pi*440*i/24000))) for i in range(24000)))


def original_pdf():
    output=BytesIO();c=canvas.Canvas(output)
    c.drawString(40,780,'An original learning example: every sentence stays in the spoken text.')
    c.drawString(40,755,'The PPT may summarize the source while captions retain its exact words.')
    c.save();return output.getvalue()


def test_source_pipeline_exports_real_static_ppt_video_and_exact_subtitles(tmp_path):
    settings=Settings(data_dir=tmp_path);store=JobStore(tmp_path);speech=SourceSpeech()
    job=store.create('original.pdf',Mode.DEMO,VoiceMode.SYSTEM,True,False,original_pdf(),
        GenerationOptions(narration_mode='source_verbatim',animation_mode='basic',ppt_template='paper'))
    processor=JobProcessor(settings,store,speech_factory=lambda _:speech)
    processor.process(job['id']);result=store.get(job['id'])
    assert result['status']=='completed',result['error']
    folder=store.jobs_dir/job['id'];lesson=store.lesson(job['id'])
    parsed=json.loads((folder/'parsed-source.json').read_text(encoding='utf8'))['data']
    source=''.join(p['text'] for p in parsed['pages'])
    cues=json.loads((folder/'subtitles.json').read_text(encoding='utf8'))['cues']
    assert ''.join(speech.inputs)==source==''.join(c['text'] for c in cues)
    assert json.loads((folder/'source-coverage.json').read_text(encoding='utf8'))['verified']
    assert (folder/'subtitles.srt').is_file() and (folder/'subtitles.vtt').is_file()
    with zipfile.ZipFile(folder/'lesson.pptx') as z:
        assert not any(n.startswith('ppt/media/') and n.endswith(('.mp4','.wav','.mp3')) for n in z.namelist())
    assert clip_duration(folder/'lesson.mp4')>len(speech.inputs)
    image=folder/'actual-frame.png'
    _run(['-ss',str(cues[0]['start']+.1),'-i',str(folder/'lesson.mp4'),'-frames:v','1',str(image)],image)
    with Image.open(image) as im:
        assert im.size==(1280,720)
        assert im.getpixel((10,710))[:3]!=(255,255,255)


@pytest.mark.parametrize('comic',[False,True])
def test_generated_drawing_and_video_reach_real_courseware_and_frames(tmp_path,comic):
    from zhijiang.literal_course import build_literal_course
    from zhijiang.models import SourceDocument,PageText
    from zhijiang.page_media import render_shared_video
    from zhijiang.deck_renderer import render_template_presentation
    from zhijiang.source_subtitles import synthesize_source
    from zhijiang.course_styles import build_course_style,plan_comic_story
    from test_course_styles import StyleClient
    doc=SourceDocument(filename='art-sample.pdf',pages=[PageText(page=1,text='Every sentence is kept. The drawn scene is original teaching artwork.')])
    lesson=build_literal_course(doc,None,GenerationOptions(ppt_template='paper'),VoiceMode.SYSTEM,tmp_path/'slides',lambda *a:None)
    media=tmp_path/'generated-media';media.mkdir()
    poster=media/'poster.png';Image.new('RGB',(320,180),'#E07431').save(poster)
    video=media/'clip.mp4'
    _run(['-f','lavfi','-i','color=c=0xE07431:s=320x180:r=30','-t','1','-c:v','libx264',str(video)],video)
    lesson.deck_plan['generated_media']={'1':[{'file':'generated-media/poster.png','video':'generated-media/clip.mp4',
        'kind':'generated_video','identity':'AI原创教学插画，非教材原图'}]}
    if comic:
        client=StyleClient();build_course_style(lesson,'comic',client,tmp_path/'style');plan_comic_story(lesson,client,tmp_path/'style')
        import hashlib
        original=lesson.deck_plan['generated_media']['1'][0]
        original['sha256']={name:hashlib.sha256((tmp_path/name).read_bytes()).hexdigest()
                            for name in ('generated-media/poster.png','generated-media/clip.mp4')}
        lesson.deck_plan['generated_media']['1']=[{**original,'shot_id':s['id']}
            for s in lesson.deck_plan['comic_story']['segments']['1']['shots']]
    paths,_=synthesize_source(lesson,SourceSpeech(),tmp_path,lambda *a:None)
    render_template_presentation(lesson,tmp_path/'lesson.pptx',audio_files=paths)
    render_shared_video(lesson,paths,tmp_path/'lesson.mp4')
    manifest=json.loads((tmp_path/'presentation-preview/manifest.json').read_text(encoding='utf8'))
    assert any(p['kind']==('drama_shot' if comic else 'generated_illustration') for p in manifest['pages'])
    segment=manifest['segments'][0]
    if comic:
        assert segment['shots'][0]['actual_medium']=='provider_video'
    else:
        assert segment['generated_media'][0]['video']=='generated-media/clip.mp4'
    image=tmp_path/'actual-art.png'
    time=(segment['fractions'][-1]+1)/2*segment['duration'] if comic else segment['duration']*.95
    _run(['-ss',str(time),'-i',str(tmp_path/segment['clip']),'-frames:v','1',str(image)],image)
    with Image.open(image) as im:
        import numpy as np
        pixels=np.asarray(im.convert('RGB')).reshape(-1,3)
        assert sum(r>180 and 65<g<155 and b<90 for r,g,b in pixels)>30000
    with zipfile.ZipFile(tmp_path/'lesson.pptx') as z:
        assert any(z.read(n)==poster.read_bytes() for n in z.namelist() if n.endswith('.png'))


def test_computed_motion_uses_only_exact_source_voice(tmp_path,monkeypatch):
    from zhijiang.literal_course import build_literal_course
    from zhijiang.literal_pipeline import render_source_motion
    from zhijiang.source_subtitles import synthesize_source
    from zhijiang.models import SourceDocument,PageText,Evidence
    from zhijiang.math_construction import compile_program,MathProgramDraft
    from zhijiang.visual_planning import visual_capabilities
    if not visual_capabilities()['geometry_ready']:pytest.skip('Actual Manim/TeX renderer unavailable')
    text='这是连续曲线的原文说明。逐字配音与字幕保持一致，不使用模型额外编写的动画讲稿。'
    doc=SourceDocument(filename='motion-original.pdf',pages=[PageText(page=1,text=text)])
    lesson=build_literal_course(doc,None,GenerationOptions(ppt_template='paper'),VoiceMode.SYSTEM,tmp_path/'slides',lambda *a:None)
    program=MathProgramDraft(title='曲线上的动点',objective='观察输入变化与曲线上点的对应关系。',source_id=1,
        parameters={'u':0},constructions=[{'id':'f','op':'function','expr':['sin(x)'],'domain':[-1,1]},
            {'id':'p','op':'point_on_function','refs':['f'],'expr':['u']}],
        operations=[{'narration':'这段模型动画讲稿不得进入原文配音。','changes':{'u':u}} for u in (.1,.4,.8)],
        claims=[{'source_id':1,'lhs':'ycoord(p)','rhs':'value(f,u)'}])
    scene=compile_program(program,Evidence(page=1,quote=text),{1:{'quote':text}})
    def prepared(clone,*a,**k):
        clone.segments[0].visual_scene=scene
        clone.segments[0].narration='另一份未播出的模型讲解。'
    monkeypatch.setattr('zhijiang.automatic_construction.enrich_constructions',prepared)
    lesson.deck_plan['construction_requests']=[{'segment_id':1}]
    speech=SourceSpeech();paths,_=synthesize_source(lesson,speech,tmp_path,lambda *a:None)
    render_source_motion(lesson,doc,object(),tmp_path,tmp_path/'source.pdf',GenerationOptions(),lambda *a:None,paths)
    assert lesson.segments[0].narration==text==''.join(speech.inputs)
    assert lesson.animation_report['source_motion'][0]['audio']=='verbatim_source_only'
    assert lesson.animation_report['scene_count']==1
    with wave.open(str(paths[0])) as w:seconds=w.getnframes()/w.getframerate()
    assert clip_duration(tmp_path/'visual/scene-01/clip.mp4')==pytest.approx(seconds,abs=.12)
    assert (tmp_path/'scene-data.json').is_file()
