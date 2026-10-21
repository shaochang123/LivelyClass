import hashlib
import json
import wave
from pathlib import Path
from zipfile import ZipFile

import pytest
from PIL import Image

from test_course_styles import lesson, StyleClient
from zhijiang.agents import GenerationError
from zhijiang.course_styles import build_course_style, plan_comic_story
from zhijiang.drama import (DramaShot, DramaStyleDraft, enrich_drama_media,
    require_drama_service, shot_prompt, source_sentences, validate_shots)
from zhijiang.drama_renderer import cue_time, render_drama_deck
from zhijiang.models import ArtOptions, ArtServiceOptions
from zhijiang.page_media import clip_duration, render_shared_video, _run
from zhijiang.presentation import PresentationError


def planned(tmp_path):
    item=lesson()
    build_course_style(item,'comic',StyleClient(),tmp_path)
    plan_comic_story(item,StyleClient(),tmp_path)
    return item


def art_options(**kwargs):
    return ArtOptions(image=ArtServiceOptions(protocol='images',base_url='http://127.0.0.1:9999/v1',model='test'),**kwargs)


def generated(tmp_path,monkeypatch,*,video=False):
    item=planned(tmp_path)
    calls=[]
    def asset(service,prompt,folder,kind):
        folder.mkdir(exist_ok=True)
        name=f'scene-{len(calls)}.png'
        Image.new('RGB',(1280,720),['#EA7733','#4267C3','#56B193'][len(calls)]).save(folder/name)
        calls.append(prompt)
        return {'poster':name,'sha256':{name:hashlib.sha256((folder/name).read_bytes()).hexdigest()}}
    monkeypatch.setattr('zhijiang.generated_media.generate_asset',asset)
    enrich_drama_media(item,art_options(),tmp_path,lambda *a:None)
    return item,calls


@pytest.mark.parametrize('text',['  第一幕。\n第二幕！  ','Same? Same?\nEnd.','\n一句诗\n','白  白。白  白。'])
def test_story_source_retains_every_character(text):
    assert ''.join(source_sentences(text))==text


def test_scene_plan_is_not_a_dialogue_and_binds_all_source(tmp_path):
    item=planned(tmp_path)
    story=item.deck_plan['comic_story']
    shots=story['segments']['1']['shots']
    assert story['format']=='scene_drama'
    assert ''.join(p['text'] for p in shots)==item.segments[0].narration
    assert [p['source_start'] for p in shots]==[0,len(shots[0]['text']),len(shots[0]['text'])+len(shots[1]['text'])]
    assert all('speaker' not in p and 'dialogue' not in p for p in shots)
    prompts=[shot_prompt(story,p) for p in shots]
    assert all('深蓝色长袖外套' in p and '观察室' in p for p in prompts)
    assert (tmp_path/'drama-story.json').is_file()


@pytest.mark.parametrize('mutation',[{'character_ids':['C9']},{'location_id':'L9'},
    {'sentence_ids':[2]},{'character_ids':['C1','C1']}])
def test_shots_reject_changed_identity_or_source(mutation,tmp_path):
    item=planned(tmp_path);story=item.deck_plan['comic_story']
    shots=story['segments']['1']['shots'];shots[0].update(mutation)
    with pytest.raises(ValueError):
        validate_shots(shots,[1,2,3],DramaStyleDraft.model_validate(story['design']))


def test_scene_character_is_optional_for_landscape_poetry(tmp_path):
    item=planned(tmp_path)
    data=item.deck_plan['comic_story']['design'];data['characters']=[]
    style=DramaStyleDraft.model_validate(data)
    shot=item.deck_plan['comic_story']['segments']['1']['shots'][0]
    shot['character_ids']=[]
    validate_shots([shot],[1],style)
    with pytest.raises(ValueError):DramaShot.model_validate({**{k:shot[k] for k in DramaShot.model_fields},'speaker':'teacher'})


def test_assets_are_required_and_limit_checked_before_paid_calls(tmp_path,monkeypatch):
    with pytest.raises(GenerationError,match='需要绘图或视频'):
        require_drama_service(ArtOptions())
    item=planned(tmp_path)
    def no_call(*a,**k):pytest.fail('No media call should occur before budget validation')
    monkeypatch.setattr('zhijiang.generated_media.generate_asset',no_call)
    with pytest.raises(GenerationError,match='3个漫剧镜头'):
        enrich_drama_media(item,art_options(max_assets=2),tmp_path,lambda *a:None)


def test_jimeng_checks_every_prompt_length_before_first_image(tmp_path,monkeypatch):
    item=planned(tmp_path)
    item.deck_plan['comic_story']['segments']['1']['shots'][-1]['composition']='细节'*450
    def no_call(*a,**k):pytest.fail('No paid request before validating all prompts')
    monkeypatch.setattr('zhijiang.generated_media.generate_asset',no_call)
    art=ArtOptions(image=ArtServiceOptions(protocol='jimeng',base_url='https://openapi.cv.volces.com',model='jimeng_t2i_v30'))
    with pytest.raises(GenerationError,match='尚未提交任何'):
        enrich_drama_media(item,art,tmp_path,lambda *a:None)


def test_every_generated_asset_has_one_shot_binding_and_hash(tmp_path,monkeypatch):
    item,calls=generated(tmp_path,monkeypatch)
    assets=item.deck_plan['generated_media']['1']
    assert len(calls)==3 and len({v['shot_id'] for v in assets})==3
    assert all(v.get('sha256') for v in assets)
    assert item.deck_plan['drama_production']['motion']=='illustrated_still_sequence'
    assert json.loads((tmp_path/'drama-production.json').read_text(encoding='utf8'))['production']['status']=='complete'


def test_media_failure_keeps_partial_assets_without_replacement(tmp_path,monkeypatch):
    item=planned(tmp_path);calls=[]
    def asset(service,prompt,folder,kind):
        calls.append(prompt)
        if len(calls)>1:raise GenerationError('provider unavailable')
        folder.mkdir(exist_ok=True);path=folder/'first.png';Image.new('RGB',(40,40),'red').save(path)
        return {'poster':path.name,'sha256':{path.name:hashlib.sha256(path.read_bytes()).hexdigest()}}
    monkeypatch.setattr('zhijiang.generated_media.generate_asset',asset)
    with pytest.raises(GenerationError):enrich_drama_media(item,art_options(),tmp_path,lambda *a:None)
    saved=json.loads((tmp_path/'drama-production.json').read_text(encoding='utf8'))
    assert len(saved['generated_media']['1'])==1 and len(calls)==2
    assert saved['production']['status']=='failed'
    assert not (tmp_path/'lesson.pptx').exists()


def test_scene_export_uses_actual_images_static_ppt_and_one_source_audio(tmp_path,monkeypatch):
    item,_=generated(tmp_path,monkeypatch)
    audio=tmp_path/'audio-0.wav'
    from test_literal_pipeline import SourceSpeech
    SourceSpeech().synthesize(item.segments[0].narration,audio)
    shots=item.deck_plan['comic_story']['segments']['1']['shots']
    durations=[.2,.3,.5];cursor=0;cues=[]
    for shot,seconds in zip(shots,durations):
        cues.append({'text':shot['text'],'start':cursor,'end':cursor+seconds});cursor+=seconds
    (tmp_path/'source-speech.json').write_text(json.dumps([{'segment_id':1,'audio_duration':1.,'cues':cues}]),encoding='utf8')
    manifest=render_drama_deck(item,tmp_path/'lesson.pptx',audio_files=[audio])
    assert manifest['segments'][0]['fractions']==pytest.approx([0,.2,.5])
    assert manifest['segments'][0]['narration_uses']==1
    assert manifest['motion']=='illustrated_still_sequence' and manifest['bookends']==[]
    assert len(manifest['pages'])==3
    render_shared_video(item,[audio],tmp_path/'lesson.mp4')
    assert clip_duration(tmp_path/'lesson.mp4')==pytest.approx(1,abs=.08)
    with ZipFile(tmp_path/'lesson.pptx') as z:
        assert not any(n.endswith(('.wav','.mp4')) for n in z.namelist())
        assert len([n for n in z.namelist() if n.startswith('ppt/slides/slide') and n.endswith('.xml')])==3
        assert not any(b'teacher' in z.read(n) or b'speaker' in z.read(n) for n in z.namelist() if n.startswith('ppt/notesSlides/') and n.endswith('.xml'))
    frame=tmp_path/'frame.png'
    _run(['-ss','0.35','-i',str(tmp_path/'lesson.mp4'),'-frames:v','1',str(frame)],frame)
    with Image.open(frame) as im:
        r,g,b=im.convert('RGB').getpixel((640,300));assert b>g>r
    timeline=json.loads((tmp_path/'video-timeline.json').read_text(encoding='utf8'))
    assert timeline['visual_source']=='illustrated-scene-drama'


@pytest.mark.parametrize('change',['missing','changed_bytes','changed_source','bad_span'])
def test_render_cannot_fall_back_to_dialogue_or_accept_stale_asset(change,tmp_path,monkeypatch):
    item,_=generated(tmp_path,monkeypatch)
    from test_literal_pipeline import SourceSpeech
    audio=tmp_path/'audio-0.wav';SourceSpeech().synthesize(item.segments[0].narration,audio)
    if change=='missing':item.deck_plan['generated_media']['1'].pop()
    elif change=='changed_bytes':(tmp_path/item.deck_plan['generated_media']['1'][0]['file']).write_bytes(b'changed')
    elif change=='changed_source':item.segments[0].narration+='新句子。'
    else:item.deck_plan['comic_story']['segments']['1']['shots'][0]['source_end']+=1
    with pytest.raises(PresentationError):render_drama_deck(item,tmp_path/'lesson.pptx',audio_files=[audio])


def test_cue_interpolation_uses_measured_audio_instead_of_global_character_ratio():
    cues=[{'text':'短。','start':0.,'end':3.},{'text':'另一句较长的话。','start':3.,'end':4.}]
    assert cue_time(2,cues,10,4)==3
    assert cue_time(1,cues,10,4)==1.5


def test_story_coverage_feedback_identifies_missing_metadata_id(tmp_path):
    item=planned(tmp_path);story=item.deck_plan['comic_story']
    with pytest.raises(ValueError,match=r'缺少\[3\]'):
        validate_shots(story['segments']['1']['shots'][:2],[1,2,3],
            DramaStyleDraft.model_validate(story['design']))


def test_source_cast_retains_original_role_identity(tmp_path):
    from zhijiang.drama import validate_source_cast
    style=DramaStyleDraft.model_validate(planned(tmp_path).deck_plan['comic_story']['design'])
    style.characters[0].name='管理员'
    with pytest.raises(ValueError,match='已在原文出现'):
        validate_source_cast(style,'管理员把卡片还给学生。')
    style.characters[0].source_basis='source_character'
    validate_source_cast(style,'管理员把卡片还给学生。')
    with pytest.raises(ValueError,match='没有同名原文依据'):
        validate_source_cast(style,'天空中飘着白云。')


def test_source_review_repairs_invented_plot_and_keeps_first_rejection(tmp_path):
    class Client(StyleClient):
        def __init__(self):
            super().__init__();self.drafts=0;self.reviews=0;self.feedback=[]
        def generate(self,schema,instruction,material):
            result=super().generate(schema,instruction,material)
            if schema.__name__=='DramaShotBatch':
                self.drafts+=1;self.feedback.append(instruction)
                if self.drafts==1:result.shots[0].action='观察者将礼物递给另一名观察者。'
            elif schema.__name__=='DramaShotReview':
                self.reviews+=1
                if self.reviews==1:
                    result.checks[0].supported=False
                    result.checks[0].issues=['原句描述刺激与反应，没有赠物事件。']
            return result
    item=lesson();client=Client()
    build_course_style(item,'comic',client,tmp_path)
    plan_comic_story(item,client,tmp_path)
    assert client.drafts==2 and client.reviews==2
    assert '没有赠物事件' in client.feedback[1]
    attempts=json.loads((tmp_path/'drama-001-001.attempts.json').read_text(encoding='utf8'))
    assert '礼物' in attempts[0]['data']['shots'][0]['action']
    assert '逐镜来源核对' in attempts[0]['error']
    assert '礼物' not in item.deck_plan['comic_story']['segments']['1']['shots'][0]['action']


def test_source_review_cannot_approve_only_one_of_several_shots(tmp_path):
    class Client(StyleClient):
        def generate(self,schema,instruction,material):
            result=super().generate(schema,instruction,material)
            if schema.__name__=='DramaShotReview':result.checks=result.checks[:1]
            return result
    item=lesson();client=Client()
    build_course_style(item,'comic',client,tmp_path)
    with pytest.raises(GenerationError,match='逐镜恰好覆盖'):
        plan_comic_story(item,client,tmp_path)
    assert 'comic_story' not in item.deck_plan


def test_full_source_drama_pipeline_orders_planning_art_voice_and_export(tmp_path,monkeypatch):
    from test_literal_pipeline import SourceSpeech,original_pdf
    from zhijiang.config import Settings
    from zhijiang.storage import JobStore
    from zhijiang.pipeline import JobProcessor
    from zhijiang.models import GenerationOptions,Mode,VoiceMode
    from zhijiang.agents import AIAgents
    class Client(StyleClient):
        def generate(self,schema,instruction,material):
            if schema.__name__=='SlideDraft':
                return schema(title='原文场景',bullets=['观察原文事件'],visual_medium='drawn',
                    visual={'representation':'key_idea','items':[{'label':'原文场景','caption':'按原文顺序','sentence_id':1}],
                        'links':[],'context':''},story_stage='事件展开',layout='wide',role='explanation',
                    illustration_prompt='在原文描述的环境里观察场景。')
            return super().generate(schema,instruction,material)
        def close(self):pass
    speech=SourceSpeech();calls=[]
    def draw(service,prompt,folder,kind):
        assert not speech.inputs, 'Storyboard and image generation must precede source speech'
        assert 'shot_id' in prompt and 'location' in prompt
        folder.mkdir(exist_ok=True);path=folder/f'{len(calls)}.png'
        Image.new('RGB',(1280,720),'#4B6BAA').save(path);calls.append(prompt)
        return {'poster':path.name,'sha256':{path.name:hashlib.sha256(path.read_bytes()).hexdigest()}}
    monkeypatch.setattr('zhijiang.generated_media.generate_asset',draw)
    settings=Settings(data_dir=tmp_path);store=JobStore(tmp_path)
    options=GenerationOptions(narration_mode='source_verbatim',style_mode='comic',animation_mode='basic',art=art_options())
    job=store.create('source.pdf',Mode.AI,VoiceMode.SYSTEM,True,False,original_pdf(),options)
    processor=JobProcessor(settings,store,agent_factory=lambda _:AIAgents(Client()),speech_factory=lambda _:speech)
    processor.process(job['id'])
    result=store.get(job['id']);assert result['status']=='completed',result['error']
    folder=store.jobs_dir/job['id'];item=store.lesson(job['id'])
    source=''.join(s.narration for s in item.segments)
    cues=json.loads((folder/'subtitles.json').read_text())['cues']
    assert ''.join(speech.inputs)==source==''.join(c['text'] for c in cues)
    assert item.deck_plan['drama_production']['status']=='complete'
    assert len(calls)==sum(len(e['shots']) for e in item.deck_plan['comic_story']['segments'].values())
    assert (folder/'lesson.pptx').is_file() and (folder/'lesson.mp4').is_file()
    assert json.loads((folder/'source-coverage.json').read_text())['verified']
