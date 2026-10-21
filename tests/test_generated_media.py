import base64
from io import BytesIO
import json
import httpx
import pytest
from PIL import Image
from zhijiang.generated_media import generate_asset, validate_art_options
from zhijiang.models import ArtOptions, ArtServiceOptions
from zhijiang.agents import GenerationError


def png():
    data=BytesIO();Image.new('RGB',(320,180),'#e07431').save(data,format='PNG');return data.getvalue()


def test_drawing_adapter_generates_valid_image_caches_and_hides_key(tmp_path):
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(png()).decode()}]})
    service=ArtServiceOptions(protocol='images',base_url='https://draw.example/v1',model='original-art',api_key='secret-test')
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        one=generate_asset(service,'Draw an original teacher.',tmp_path,'image',client=client)
        two=generate_asset(service,'Draw an original teacher.',tmp_path,'image',client=client)
    assert len(calls)==1 and calls[0].headers['authorization']=='Bearer secret-test'
    assert one==two
    assert Image.open(tmp_path/one['poster']).size==(320,180)
    assert all('secret-test' not in p.read_text(encoding='utf8') for p in tmp_path.glob('*.json'))
    (tmp_path/one['poster']).write_bytes(b'corrupt')
    with pytest.raises(GenerationError,match='损坏'):
        generate_asset(service,'Draw an original teacher.',tmp_path,'image')


def test_paid_request_unknown_outcome_is_never_automatically_resubmitted(tmp_path):
    calls=[]
    def handler(request):calls.append(request);raise httpx.ReadTimeout('secret should not leak')
    service=ArtServiceOptions(protocol='images',base_url='https://draw.example/v1',model='art')
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GenerationError):generate_asset(service,'scene',tmp_path,'image',client=client)
        with pytest.raises(GenerationError,match='不会自动重复'):generate_asset(service,'scene',tmp_path,'image',client=client)
    assert len(calls)==1
    assert 'secret should not leak' not in next(tmp_path.glob('*.json')).read_text(encoding='utf8')


def test_seedance_real_media_decode_and_no_key_on_download(tmp_path):
    from zhijiang.page_media import _run, clip_duration
    clip=tmp_path/'fixture.mp4'
    _run(['-f','lavfi','-i','color=c=orange:s=320x180:r=30','-t','1','-c:v','libx264',str(clip)],clip)
    creates=[];downloads=[]
    def handler(request):
        if request.method=='POST':
            creates.append(json.loads(request.content));return httpx.Response(200,json={'id':'task-test-1'})
        if request.url.path.endswith('/task-test-1'):
            return httpx.Response(200,json={'status':'succeeded','content':{'video_url':'http://127.0.0.1:9999/media.mp4'}})
        downloads.append(request);return httpx.Response(200,content=clip.read_bytes())
    service=ArtServiceOptions(protocol='seedance',base_url='http://127.0.0.1:9999/api/v3',model='seedance-test',api_key='secret-test')
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        asset=generate_asset(service,'Original drawn classroom.',tmp_path/'media','video',client=client,poll_seconds=0)
        again=generate_asset(service,'Original drawn classroom.',tmp_path/'media','video',client=client,poll_seconds=0)
    assert len(creates)==1 and again==asset
    assert creates[0]['generate_audio'] is False
    assert all('authorization' not in req.headers for req in downloads)
    assert clip_duration(tmp_path/'media'/asset['video'])==pytest.approx(1,abs=.05)
    assert (tmp_path/'media'/asset['poster']).is_file()


@pytest.mark.parametrize('url',['http://external.example/v1','https://user:secret@example.com/v1','https://example.com/v1?key=secret'])
def test_invalid_media_endpoint_rejected(url):
    art=ArtOptions(image=ArtServiceOptions(protocol='images',base_url=url,model='draw',api_key='x'))
    with pytest.raises(ValueError):validate_art_options(art)


def test_jimeng_api_key_submit_poll_and_cache_without_expansion(tmp_path):
    calls=[]
    def handler(request):
        calls.append(request)
        assert request.headers['authorization']=='Bearer secret-test'
        if request.url.path.endswith('/submit_task_v2'):
            body=json.loads(request.content)
            assert body['req_key']=='jimeng_t2i_v30' and body['use_pre_llm'] is False
            assert (body['width'],body['height'])==(1664,936)
            return httpx.Response(200,json={'code':10000,'data':{'task_id':'test-1'}})
        assert request.url.path=='/api/common/v3/get_result_v2'
        return httpx.Response(200,json={'code':10000,'data':{'status':'done',
            'binary_data_base64':[base64.b64encode(png()).decode()]}})
    service=ArtServiceOptions(protocol='jimeng',base_url='https://openapi.cv.volces.com',
        model='jimeng_t2i_v30',api_key='secret-test')
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        one=generate_asset(service,'原创动漫图书馆场景。',tmp_path,'image',client=client)
        assert generate_asset(service,'原创动漫图书馆场景。',tmp_path,'image',client=client)==one
    assert len(calls)==2 and one['task_id']=='test-1'
    assert all('secret-test' not in p.read_text(encoding='utf8') for p in tmp_path.glob('*.json'))


def test_jimeng_query_failure_resumes_existing_task_without_second_submission(tmp_path):
    creates=[];queries=[]
    def handler(request):
        if request.url.path.endswith('/submit_task_v2'):
            creates.append(request)
            return httpx.Response(200,json={'code':10000,'data':{'task_id':'test-2'}})
        queries.append(request)
        if len(queries)==1:raise httpx.ReadTimeout('not saved')
        return httpx.Response(200,json={'code':10000,'data':{'status':'done',
            'binary_data_base64':[base64.b64encode(png()).decode()]}})
    service=ArtServiceOptions(protocol='jimeng',base_url='https://openapi.cv.volces.com',model='jimeng_t2i_v30')
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GenerationError):generate_asset(service,'scene',tmp_path,'image',client=client)
        saved=json.loads(next(tmp_path.glob('*.json')).read_text(encoding='utf8'))
        assert saved['status']=='submitted' and saved['task_id']=='test-2'
        assert generate_asset(service,'scene',tmp_path,'image',client=client)['status']=='complete'
    assert len(creates)==1 and len(queries)==2


def test_jimeng_denied_submission_keeps_code_and_does_not_repeat(tmp_path):
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(401,json={'code':50400,'message':'Access Denied secret-test',
            'request_id':'request-123','data':None})
    service=ArtServiceOptions(protocol='jimeng',base_url='https://openapi.cv.volces.com',model='jimeng_t2i_v30')
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GenerationError,match='HTTP 401'):
            generate_asset(service,'scene',tmp_path,'image',client=client)
        with pytest.raises(GenerationError,match='不会自动重复'):
            generate_asset(service,'scene',tmp_path,'image',client=client)
    saved=json.loads(next(tmp_path.glob('*.json')).read_text(encoding='utf8'))
    assert saved['provider_code']==50400 and saved['request_id']=='request-123'
    assert 'secret-test' not in next(tmp_path.glob('*.json')).read_text(encoding='utf8')
    assert len(calls)==1


def test_jimeng_long_prompt_is_rejected_before_paid_request(tmp_path):
    def no_request(request):pytest.fail('Prompt must be checked before transmission')
    service=ArtServiceOptions(protocol='jimeng',base_url='https://openapi.cv.volces.com',model='jimeng_t2i_v30')
    with httpx.Client(transport=httpx.MockTransport(no_request)) as client:
        with pytest.raises(GenerationError,match='尚未提交'):
            generate_asset(service,'画'*801,tmp_path,'image',client=client)
    assert not list(tmp_path.glob('*.json'))
