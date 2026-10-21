import json
from pathlib import Path

import pytest

from zhijiang.models import Evidence, Lesson, LessonSegment, Mode, VoiceMode
from zhijiang.course_styles import (
    CourseStyleDraft, ComicStyleDraft, build_course_style, effective_template,
    validate_style, validate_panel_coverage, plan_comic_story, readable_style, contrast, comic_sentences,
)
from zhijiang.drama import DramaStyleDraft


def lesson():
    return Lesson(title='学习条件反应', objective='理解刺激配对与学习后的反应。',
        mode=Mode.AI, voice_mode=VoiceMode.SYSTEM, notice='', ppt_template='paper',
        segments=[LessonSegment(title='刺激的配对', kind='process',
            narration='食物可以引起唾液分泌。铃声与食物反复配对。学习之后，铃声可以引起条件反应。',
            bullets=['铃声与食物配对', '学习后出现条件反应'],
            evidence=Evidence(page=1,quote='铃声与食物反复配对，随后铃声可以引起条件反应。'))])


def style_data():
    return dict(name='行为观察手记', subject='心理学基础',
        rationale='按刺激配对的过程使用清晰的观察记录配色。',
        background='FAF8F0', ink='172D35', accent='24676C', secondary='9B442C',
        panel='F0EADF', muted='4D5C60', motif='waves', title_size=36)


class StyleClient:
    model='test-local-model'
    base_url='http://127.0.0.1:11434'
    def __init__(self): self.calls=[]
    def generate(self,schema,instruction,material):
        self.calls.append(json.loads(material))
        if schema.__name__=='DramaShotReview':
            return schema(checks=[{'shot_number':s['shot_number'],
                'source_meaning':s['source_text'],'supported':True,'issues':[]}
                for s in json.loads(material)['shots']])
        if schema.__name__=='DramaShotBatch':
            ids=[s['id'] for s in json.loads(material)['sentences']]
            return schema.model_validate({'shots':[
                {'sentence_ids':[n],'character_ids':['C1'],'location_id':'L1',
                 'framing':'wide' if i==0 else 'detail','camera':'locked',
                 'action':'在实验环境中观察原文描述的刺激与反应。',
                 'composition':'以环境和实验物件为主体，人物处于场景中。',
                 'continuity':'沿用同一场景和物件位置。','depiction':'interpretive_visual'} for i,n in enumerate(ids)]})
        if 'ComicPanelBatch' in schema.__name__:
            ids=[s['id'] for s in json.loads(material)['sentences']]
            return schema.model_validate({'panels':[
                {'speaker':'teacher' if i%2==0 else 'student','sentence_ids':[n],
                 'shot':'medium','emotion':'curious','gesture':'point'} for i,n in enumerate(ids)]})
        data=style_data()
        if issubclass(schema,DramaStyleDraft):
            data={key:data[key] for key in ('name','subject','rationale')}
            data.update(art_direction='手绘动漫电影场景，以光影表现观察过程。',
                world='依据教材描述的观察环境展开画面。',arc=['观察刺激','观察反应'],
                characters=[dict(id='C1',name='观察者',appearance='短黑发、圆脸的成年观察者。',wardrobe='穿深蓝色长袖外套。',source_basis='interpretive_guide')],
                locations=[dict(id='L1',name='观察室',description='明亮房间内有实验桌和观察器材。')])
        if issubclass(schema,ComicStyleDraft):
            data['characters']=[dict(role='teacher',name='林老师',hair='short',shirt='24676C'),
                                dict(role='student',name='小禾',hair='bob',shirt='9B442C')]
        return schema.model_validate(data)


def test_generated_palette_reaches_template_without_mutating_fixed_template(tmp_path):
    from zhijiang.presentation_templates import get_template
    original=get_template('paper')
    item=lesson();client=StyleClient()
    build_course_style(item,'adaptive',client,tmp_path,prompt='适合大学课堂')
    template=effective_template(item)
    assert template.name=='行为观察手记'
    assert template.accent=='24676C'
    assert get_template('paper') is original
    assert get_template('paper').accent!=template.accent
    assert item.deck_plan['course_style']['planner']=='local_or_configured_model'
    assert client.calls[0]['style_examples'][0]['source_excerpt']==item.segments[0].evidence.quote


def test_style_cache_and_mode_changes_are_bound_to_content_and_preferences(tmp_path):
    item=lesson();client=StyleClient()
    build_course_style(item,'adaptive',client,tmp_path,prompt='观察记录')
    build_course_style(item,'adaptive',client,tmp_path,prompt='观察记录')
    assert len(client.calls)==1
    build_course_style(item,'adaptive',client,tmp_path,prompt='正式课堂')
    assert len(client.calls)==2
    item.segments[0].narration+='条件反应来自学习。'
    build_course_style(item,'adaptive',client,tmp_path,prompt='正式课堂')
    assert len(client.calls)==3
    build_course_style(item,'template',client,tmp_path)
    assert 'course_style' not in item.deck_plan
    assert 'comic_story' not in item.deck_plan


def test_unreadable_generated_palette_is_rejected():
    data=style_data();data.update(ink='FFFFFE',background='FFFFFF')
    with pytest.raises(ValueError,match='对比度'):
        validate_style(CourseStyleDraft(**data))


def test_light_generated_accents_are_corrected_without_losing_theme_or_raw_color():
    data=style_data();data['accent']='E6A675'
    original=CourseStyleDraft(**data)
    adjusted,changes=readable_style(original)
    validate_style(adjusted)
    assert original.accent=='E6A675'
    assert adjusted.name==original.name and adjusted.motif==original.motif
    assert changes[0]['model_color']=='E6A675'
    assert contrast(adjusted.accent,adjusted.background)>=3


@pytest.mark.parametrize('ids',[[1,1,3],[1,3],[2,1,3],[1,2,4]])
def test_comic_cannot_repeat_omit_reorder_or_invent_speech(ids):
    panels=[{'sentence_ids':[i],'speaker':'teacher' if n%2==0 else 'student'} for n,i in enumerate(ids)]
    with pytest.raises(ValueError): validate_panel_coverage(panels,[1,2,3])


def test_comic_keeps_every_original_sentence_and_stable_cast(tmp_path):
    item=lesson();client=StyleClient()
    build_course_style(item,'comic',client,tmp_path)
    plan_comic_story(item,client,tmp_path)
    story=item.deck_plan['comic_story']
    panels=story['segments']['1']['shots']
    sentences=comic_sentences(item.segments[0].narration)
    assert [sid for p in panels for sid in p['sentence_ids']]==list(range(1,len(sentences)+1))
    assert [p['text'] for p in panels]==sentences
    assert all('speaker' not in p for p in panels)
    assert [x['name'] for x in story['design']['characters']]==['观察者']
    assert story['audio_source']=='unchanged_source_narration_not_character_dialogue'
    before=len(client.calls)
    plan_comic_story(item,client,tmp_path)
    assert len(client.calls)==before


def test_new_style_mode_persists_on_create_and_retry(tmp_path,sample_pdf):
    from test_api import make_client
    client,store=make_client(tmp_path)
    with client:
        client.app.state.runner.submit=lambda _:None
        response=client.post('/api/jobs',files={'file':('source.pdf',sample_pdf)},data={
            'mode':'ai','rights_confirmed':'true','animation_mode':'basic','style_mode':'comic',
            'llm_provider':'ollama','llm_base_url':'http://127.0.0.1:11434','llm_model':'local-test'})
        assert response.status_code==202,response.text
        job=response.json()['id']
        assert store.get_options(job).style_mode=='comic'
        store.fail(job,'test interruption')
        assert client.post('/api/jobs/'+job+'/retry').status_code==202
        assert store.get_options(job).style_mode=='comic'
        store.fail(job,'test interruption')
        assert client.post('/api/jobs/'+job+'/retry',data={'style_mode':'adaptive'}).status_code==202
        assert store.get_options(job).style_mode=='adaptive'


def test_demo_does_not_claim_model_created_styles(tmp_path,sample_pdf):
    from test_api import make_client
    client,_=make_client(tmp_path)
    with client:
        response=client.post('/api/jobs',files={'file':('source.pdf',sample_pdf)},data={
            'rights_confirmed':'true','style_mode':'adaptive'})
        assert response.status_code==400
        assert 'AI' in response.json()['detail']


def test_dialogue_preserves_repeated_sentences_and_question_marks(tmp_path):
    item=lesson();client=StyleClient()
    item.segments[0].narration='条件为什么重要？条件为什么重要？这个问题需要结合教材的例子来理解。'
    build_course_style(item,'comic',client,tmp_path)
    plan_comic_story(item,client,tmp_path)
    panels=item.deck_plan['comic_story']['segments']['1']['shots']
    assert ''.join(p['text'] for p in panels)==item.segments[0].narration
    assert [p['sentence_ids'] for p in panels]==[[1],[2],[3]]


def test_retry_can_switch_demo_to_ai_comic(tmp_path,sample_pdf):
    from test_api import make_client
    client,store=make_client(tmp_path)
    with client:
        client.app.state.runner.submit=lambda _:None
        job=store.create('sample.pdf',Mode.DEMO,VoiceMode.SYSTEM,True,False,sample_pdf)
        store.fail(job['id'],'test interruption')
        response=client.post('/api/jobs/'+job['id']+'/retry',data={'mode':'ai','style_mode':'comic',
            'animation_mode':'basic','llm_provider':'ollama','llm_base_url':'http://127.0.0.1:11434','llm_model':'local-test'})
        assert response.status_code==202,response.text
        assert store.get_options(job['id']).style_mode=='comic'


def test_preview_modes_are_readonly_and_invalid_mode_is_rejected(tmp_path):
    from test_api import make_client
    from xml.etree import ElementTree as ET
    client,_=make_client(tmp_path)
    with client:
        response=client.get('/api/ppt-templates/paper/preview?style_mode=comic')
        assert response.status_code==200
        ET.fromstring(response.content)
        assert '漫剧' in response.text
        assert client.get('/api/ppt-templates/paper/preview?style_mode=unknown').status_code==422


def test_comic_export_has_vector_cast_real_video_and_single_narration(tmp_path):
    import wave
    from zipfile import ZipFile
    from zhijiang.comic_renderer import render_comic_deck
    from zhijiang.page_media import render_shared_video,clip_duration
    item=lesson();client=StyleClient()
    # Retained version-1 jobs must remain replayable; new jobs use scene drama.
    data=style_data()
    data['characters']=[dict(role='teacher',name='林老师',hair='short',shirt='24676C'),
                        dict(role='student',name='小禾',hair='bob',shirt='9B442C')]
    item.deck_plan['course_style']={'version':1,'mode':'comic','design':data}
    plan_comic_story(item,client,tmp_path)
    audio=tmp_path/'audio-0.wav'
    with wave.open(str(audio),'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(24000);w.writeframes(b'\x00\x00'*24000)
    manifest=render_comic_deck(item,tmp_path/'lesson.pptx',audio_files=[audio])
    render_shared_video(item,[audio],tmp_path/'lesson.mp4')
    assert manifest['segments'][0]['narration_uses']==1
    assert 2.8 < clip_duration(tmp_path/'lesson.mp4') < 3.2
    with ZipFile(tmp_path/'lesson.pptx') as z:
        assert not any(n.endswith(('.mp4','.wav')) for n in z.namelist())
        svgs=[z.read(n).decode() for n in z.namelist() if n.endswith('.svg')]
        assert any('林老师' in s and '小禾' in s for s in svgs)
        assert all('foreignObject' not in s and '<script' not in s for s in svgs)
    before=tmp_path/'presentation-media/segment-001.mp4'
    stamp=before.stat().st_mtime_ns
    render_shared_video(item,[audio],tmp_path/'lesson.mp4')
    assert before.stat().st_mtime_ns==stamp


def test_comic_render_refuses_stale_sentence_bindings(tmp_path):
    from zhijiang.comic_renderer import render_comic_deck
    from zhijiang.presentation import PresentationError
    item=lesson();client=StyleClient()
    build_course_style(item,'comic',client,tmp_path)
    plan_comic_story(item,client,tmp_path)
    item.segments[0].narration+='这里增加了一句内容。'
    with pytest.raises(PresentationError,match='重新规划'):
        render_comic_deck(item,tmp_path/'lesson.pptx')


def test_dense_comic_board_expands_instead_of_dropping_conditions(tmp_path):
    from zhijiang.comic_renderer import _knowledge_boards
    from xml.etree import ElementTree as ET
    item=lesson();client=StyleClient()
    build_course_style(item,'comic',client,tmp_path)
    visual={'representation':'process','items':[
        {'label':'初始状态','caption':'铃声不引起反应，食物引起反应','sentence_id':1,'shape':'concept'},
        {'label':'配对过程','caption':'铃声与食物反复同时出现','sentence_id':2,'shape':'process'},
        {'label':'学习完成','caption':'铃声单独出现引发唾液分泌','sentence_id':3,'shape':'result'}],
        'links':[{'source':1,'target':2,'label':'反复配对','sentence_id':2,'kind':'directed'},
                 {'source':2,'target':3,'label':'形成关联','sentence_id':3,'kind':'directed'}],
        'context':'巴甫洛夫实验：铃声与食物配对导致唾液分泌习得。','context_sentence_id':1}
    item.deck_plan['visual_story']={'visuals':{'1':{'visual':visual}}}
    boards,kind,clip=_knowledge_boards(item,1,effective_template(item),tmp_path,tmp_path)
    assert kind=='teaching_svg_panorama' and clip is None
    text=''.join(''.join(ET.fromstring(svg).itertext()) for svg in boards)
    for x in visual['items']:
        assert x['label'] in text
        assert x['caption'].replace(' ','') in text.replace(' ','')
    for edge in visual['links']:assert edge['label'] in text


def test_continuous_scene_overlay_keeps_background_narration_only(tmp_path):
    import subprocess
    import imageio_ffmpeg
    import numpy as np
    from PIL import Image
    from zhijiang.page_media import scene_on_video,clip_duration
    ffmpeg=imageio_ffmpeg.get_ffmpeg_exe()
    for name,color,size,tone in [('background','blue','1280x720',440),('source','red','160x90',880)]:
        subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-y',
            '-f','lavfi','-i',f'color=c={color}:s={size}:r=30:d=1',
            '-f','lavfi','-i',f'sine=frequency={tone}:sample_rate=48000:duration=1',
            '-c:v','libx264','-threads','2','-pix_fmt','yuv420p','-c:a','aac','-t','1',str(tmp_path/f'{name}.mp4')],check=True)
    target=tmp_path/'overlay.mp4'
    scene_on_video(tmp_path/'background.mp4',tmp_path/'source.mp4',(100,120,640,360),target,1)
    assert .95<=clip_duration(target)<=1.05
    audio=subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-i',str(target),'-vn',
        '-f','f32le','-ac','1','-ar','48000','-'],capture_output=True,check=True).stdout
    values=np.frombuffer(audio,dtype=np.float32)
    spectrum=np.abs(np.fft.rfft(values));frequencies=np.fft.rfftfreq(len(values),1/48000)
    assert abs(frequencies[np.argmax(spectrum)]-440)<5
    assert spectrum[np.argmin(abs(frequencies-880))]<spectrum.max()*.2


def test_teaching_board_wraps_complete_english_words_without_losing_text():
    import re
    from zhijiang.story_svg import lines
    text="Children helping in the kitchen often make a mess. Don't split English words."
    wrapped=lines(text,300)
    assert re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)*",' '.join(wrapped))==re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)*",text)
    assert ''.join(''.join(wrapped).split())==''.join(text.split())
    assert lines('铃声\n食物',100)==['铃声','食物']


def test_teaching_board_rejects_unreadable_word_instead_of_cutting_it():
    from zhijiang.story_svg import lines
    from zhijiang.presentation import PresentationError
    with pytest.raises(PresentationError,match='完整单词'):
        lines('conditioning',20)
