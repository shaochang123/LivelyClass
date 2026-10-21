import json
import pytest
from zhijiang.learning_plan import build_learning_plan
from zhijiang.models import LearnerProfile, SourceDocument, PageText, GenerationOptions, VoiceMode
from zhijiang.literal_course import build_literal_course


class Planner:
    model='local-plan-fixture'
    base_url='http://localhost:11434'
    def __init__(self, bad=None): self.bad=bad; self.calls=0
    def generate(self, schema, instruction, material):
        self.calls += 1
        data=json.loads(material); ids=[s['segment_id'] for s in data['catalogue']]
        if self.bad=='unknown': ids=[999]
        if self.bad=='missing': ids=[]
        return schema(overview='按原文概念和实际片段逐步学习，并进行有针对性的复习。',
            sessions=[{'day':day if self.bad!='days' else 1, 'title':'阅读与复习',
                'segment_ids':ids or [1], 'activities':['阅读指定片段，对照原页复述。'],
                'self_check':'请复述所读内容中的概念关系。'} for day in range(1,4)])


def course(tmp_path):
    source=SourceDocument(filename='input.pdf', pages=[PageText(page=1,text='原文第一段。'*150),
        PageText(page=2,text='原文第二段，保留来源。'*20)])
    return build_literal_course(source,None,GenerationOptions(narration_mode='source_verbatim'),
        VoiceMode.SYSTEM,tmp_path/'literal',lambda *_:None)


def test_study_plan_keeps_source_and_owns_budget(tmp_path):
    lesson=course(tmp_path); original=[s.narration for s in lesson.segments]
    profile=LearnerProfile(enabled=True,background='已有基础',goal='复习',days=3,minutes_per_day=15)
    planner=Planner();plan=build_learning_plan(lesson,profile,planner,tmp_path/'plan',audio_seconds=60)
    assert sum(s['minutes'] for s in plan['sessions'])==45
    assert {p for s in plan['sessions'] for p in s['source_pages']}=={1,2}
    assert [s.narration for s in lesson.segments]==original
    assert (tmp_path/'learning-plan.md').is_file()
    build_learning_plan(lesson,profile,planner,tmp_path/'plan',audio_seconds=60)
    assert planner.calls==1
    profile.goal='准备演讲'
    build_learning_plan(lesson,profile,planner,tmp_path/'plan',audio_seconds=60)
    assert planner.calls==2


@pytest.mark.parametrize('bad',['unknown','missing','days'])
def test_invalid_source_or_schedule_is_not_published(tmp_path,bad):
    from zhijiang.agents import GenerationError
    with pytest.raises(GenerationError):
        build_learning_plan(course(tmp_path),LearnerProfile(enabled=True),Planner(bad),tmp_path/'plan')
    assert not (tmp_path/'learning-plan.json').exists()


def test_api_create_and_retry_preserve_learner_profile(tmp_path,sample_pdf):
    from test_api import make_client
    client,store=make_client(tmp_path)
    with client:
        client.app.state.runner.submit=lambda _:None
        data={'rights_confirmed':'true','mode':'ai','llm_provider':'ollama',
            'llm_base_url':'http://127.0.0.1:11434','llm_model':'local-test',
            'narration_mode':'source_verbatim','learner_enabled':'true',
            'learner_background':'已学过基础概念','learner_goal':'完成复习','learner_days':'3',
            'learner_minutes_per_day':'15'}
        response=client.post('/api/jobs',files={'file':('input.pdf',sample_pdf)},data=data)
        assert response.status_code==202,response.text
        job=response.json()['id'];assert store.get_options(job).learner.goal=='完成复习'
        assert client.get(f'/api/jobs/{job}/learning-plan').status_code==409
        store.fail(job,'fixture')
        assert client.post(f'/api/jobs/{job}/retry',data={'learner_days':'4'}).status_code==202
        assert store.get_options(job).learner.days==4
        assert store.get_options(job).learner.background=='已学过基础概念'
        store.fail(job,'fixture')
        assert client.post(f'/api/jobs/{job}/retry',data={'learner_days':'0'}).status_code==422


def test_plan_flags_insufficient_audio_budget(tmp_path):
    plan=build_learning_plan(course(tmp_path),LearnerProfile(enabled=True,minutes_per_day=5),
        Planner(),tmp_path/'plan',audio_seconds=1000)
    assert plan['time_note']
