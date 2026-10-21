import json
import pytest
from zhijiang.models import SourceDocument, PageText
from zhijiang.subject_routing import route_subject


class Router:
    model='local-contract-fixture'
    base_url='http://localhost:11434'
    def __init__(self,family):self.family=family;self.calls=[];self.instructions=[]
    def generate(self,schema,instruction,material):
        self.instructions.append(instruction)
        self.calls.append((schema.__name__,json.loads(material)))
        if schema.__name__=='SubjectDraft':
            return schema(subject='物理学' if self.family=='stem' else '英语语言学习',family=self.family,
                source_ids=[1],reason='原文含有支持该学科分类的明确内容。')
        return schema(template_id='stem_board' if self.family=='stem' else 'language_comic',
            presentation='adaptive' if self.family=='stem' else 'comic',reason='该模板适合原文的知识组织和教学表达。')


@pytest.mark.parametrize('family,text',[('stem','Frequency equals wave speed divided by wavelength.'),
    ('humanities','A complete sentence needs a subject and a predicate.')])
def test_model_identifies_content_then_selects_family_template(tmp_path,family,text):
    client=Router(family)
    doc=SourceDocument(filename='misleading-math-or-language-name.pdf',pages=[PageText(page=1,text=text)])
    result=route_subject(doc,client,tmp_path)
    assert result['classification']['family']==family
    assert [c[0] for c in client.calls]==['SubjectDraft','TemplateDraft']
    assert client.calls[0][1]['classification_samples'][0]['text']==text
    assert all(t['subject_family']==family for t in client.calls[1][1]['templates'])
    route_subject(doc,client,tmp_path)
    assert len(client.calls)==2


def test_routing_distinguishes_visual_requirements_from_broad_stem_taxonomy(tmp_path):
    client=Router('stem')
    text='A point moves along a measured straight line.'
    route_subject(SourceDocument(filename='neutral.pdf',pages=[PageText(page=1,text=text)]),client,tmp_path)
    instruction=client.instructions[0]
    assert '不是广义STEM学科门类的归类' in instruction
    assert '实体及其关联' in instruction and '按当前材料的这些表达需求' in instruction
    assert set(client.calls[0][1])=={'filename','source_sha256','classification_samples','scope'}


def test_api_source_options_secrets_consent_and_retry(tmp_path,sample_pdf):
    from test_api import make_client
    client,store=make_client(tmp_path)
    with client:
        client.app.state.runner.submit=lambda _:None
        fields={'rights_confirmed':'true','mode':'ai','llm_provider':'ollama',
            'llm_base_url':'http://127.0.0.1:11434','llm_model':'local-test','animation_mode':'basic',
            'style_mode':'subject','image_protocol':'images','image_base_url':'https://art.example/v1',
            'image_model':'draw-test','image_api_key':'placeholder-image-key'}
        submit=lambda data:client.post('/api/jobs',files={'file':('test.pdf',sample_pdf)},data=data)
        assert submit(fields).status_code==400
        fields['remote_consent']='true'
        response=submit(fields);assert response.status_code==202,response.text
        job=response.json()['id']
        options=store.get_options(job)
        assert options.narration_mode=='source_verbatim'
        assert options.art.image.api_key=='placeholder-image-key'
        assert 'placeholder-image-key' not in store.db_path.read_bytes().decode('latin1')
        assert 'placeholder-image-key' not in response.text
        for name in ('subtitles','transcript','source-coverage'):
            assert client.get(f'/api/jobs/{job}/{name}').status_code==409
        store.fail(job,'fixture')
        response=client.post(f'/api/jobs/{job}/retry',data={'image_base_url':'https://different.example/v1'})
        assert response.status_code==422  # keys never migrate to a different endpoint
        response=client.post(f'/api/jobs/{job}/retry')
        assert response.status_code==202,response.text
        assert store.get_options(job).art.image.api_key=='placeholder-image-key'
        assert store.get_options(job).style_mode=='subject'
        store.clear_api_key(job)
        assert not store.get_options(job).art.image.api_key


@pytest.mark.parametrize('model,url',[('deepseek-local','http://localhost:11434'),
    ('qwen:cloud','http://localhost:11434'),('other','https://external.example/v1')])
def test_source_workflow_rejects_forbidden_language_inference(tmp_path,sample_pdf,model,url):
    from test_api import make_client
    client,_=make_client(tmp_path)
    with client:
        response=client.post('/api/jobs',files={'file':('test.pdf',sample_pdf)},data={
            'rights_confirmed':'true','mode':'ai','narration_mode':'source_verbatim','remote_consent':'true',
            'llm_provider':'ollama','llm_model':model,'llm_base_url':url})
        assert response.status_code==400
