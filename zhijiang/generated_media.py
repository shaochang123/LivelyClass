"""Opt-in image generation and Seedance task adapters; never searches for photos."""
from __future__ import annotations
import base64
import hashlib
from io import BytesIO
import ipaddress
import json
from pathlib import Path
import re
import socket
import time
from urllib.parse import urlsplit
import httpx
from PIL import Image
from zhijiang.credentials import endpoint
from zhijiang.config import Settings
from zhijiang.agents import GenerationError
from zhijiang.page_media import _run,clip_duration


def validate_art_options(art):
    for kind,service in (('image',art.image),('video',art.video)):
        if service.protocol=='disabled':
            service.base_url='';service.model='';service.api_key=''
            continue
        if service.protocol not in ({'images','jimeng'} if kind=='image' else {'seedance'}):
            raise ValueError('绘图与视频服务的协议类型不匹配。')
        service.base_url=endpoint(service.base_url)
        if not service.model.strip():raise ValueError('启用的绘图或视频服务需要填写模型ID。')
        if service.protocol=='jimeng' and not re.fullmatch(r'[a-zA-Z0-9_]{1,160}',service.model):
            raise ValueError('即梦模型请填写接口文档中的req_key，例如jimeng_t2i_v30。')
        if not Settings._is_loopback(service.base_url):
            if not service.base_url.startswith('https://'):raise ValueError('外部媒体接口必须使用HTTPS。')
            if not service.api_key:raise ValueError('外部媒体接口需要本次任务的API密钥。')


def art_is_remote(art):
    return any(s.protocol!='disabled' and not Settings._is_loopback(s.base_url) for s in (art.image,art.video))


def _download(url,service,max_bytes,client):
    parsed=urlsplit(url)
    if parsed.username or parsed.password or not parsed.hostname:raise GenerationError('媒体下载地址无效。')
    origin=urlsplit(service.base_url)
    same_local=(Settings._is_loopback(service.base_url) and parsed.hostname==origin.hostname
                and parsed.port==origin.port and parsed.scheme==origin.scheme)
    if not same_local:
        if parsed.scheme!='https':raise GenerationError('媒体下载仅允许HTTPS。')
        addresses=socket.getaddrinfo(parsed.hostname,parsed.port or 443,type=socket.SOCK_STREAM)
        if any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise GenerationError('拒绝接口返回的内部网络媒体地址。')
    chunks=[];total=0
    # Deliberately no Authorization header and no redirect following on downloads.
    with client.stream('GET',url,follow_redirects=False) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            total+=len(chunk)
            if total>max_bytes:raise GenerationError('媒体文件超过大小限制。')
            chunks.append(chunk)
    return b''.join(chunks)


def generate_asset(service,prompt,folder,kind,*,client=None,poll_seconds=3):
    if service.protocol=='jimeng' and (kind!='image' or not 1<=len(prompt)<=800):
        raise GenerationError('即梦绘图提示词必须为1至800字符；请缩短场景设定后重试，尚未提交生成。')
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    key=hashlib.sha256(json.dumps({'version':1,'protocol':service.protocol,'endpoint':service.base_url,
        'model':service.model,'prompt':prompt,'kind':kind},sort_keys=True).encode()).hexdigest()
    path=folder/f'{key}.json';saved={}
    try:saved=json.loads(path.read_text(encoding='utf8'))
    except (OSError,ValueError):pass
    if saved.get('status')=='complete':
        if all((folder/n).is_file() and hashlib.sha256((folder/n).read_bytes()).hexdigest()==h
               for n,h in saved.get('sha256',{}).items()) and saved.get('sha256'):
            return saved
        raise GenerationError('已生成的媒体缓存缺失或损坏，请核对原记录；不会自动重复提交付费生成。')
    if saved.get('status') in {'submission_unknown','failed'}:
        raise GenerationError('该媒体请求的首次失败已保留；不会自动重复提交付费生成。请核对记录后使用新任务。')
    own=client is None;client=client or httpx.Client(timeout=180,follow_redirects=False)
    record={'fingerprint':key,'kind':kind,'model':service.model,'protocol':service.protocol,
        'prompt':prompt,'status':'preparing','identity':'generated_illustration_not_source_figure'}
    headers={'Authorization':'Bearer '+service.api_key} if service.api_key else {}
    def save():path.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf8')
    try:
        if kind=='image':
            if service.protocol=='jimeng':
                def request(action,body):
                    response=client.post(service.base_url+'/api/common/v3/'+action,headers=headers,json=body)
                    record['http_status']=response.status_code
                    data=response.json()
                    record['provider_code']=data.get('code')
                    if re.fullmatch(r'[a-zA-Z0-9_-]{1,160}',str(data.get('request_id',''))):
                        record['request_id']=data['request_id']
                    if not response.is_success or data.get('code')!=10000:
                        if response.status_code in {400,401,403,404,422} and action=='submit_task_v2':
                            record['status']='failed'
                        save()
                        raise GenerationError(f'即梦服务拒绝请求（HTTP {response.status_code}，错误码{data.get("code") if isinstance(data.get("code"),int) else "未知"}）。已保留请求记录，不自动重复创建。')
                    return data.get('data') or {}
                task=saved.get('task_id')
                if not task:
                    record['status']='submission_unknown';save()
                    data=request('submit_task_v2',{'req_key':service.model,'prompt':prompt,
                        'use_pre_llm':False,'width':1664,'height':936})
                    task=data.get('task_id')
                if not isinstance(task,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}',task):
                    raise GenerationError('即梦任务编号无效。')
                record.update(task_id=task,status='submitted');save()
                for _ in range(100):
                    data=request('get_result_v2',{'req_key':service.model,'task_id':task,
                        'req_json':json.dumps({'return_url':False})})
                    if data.get('status')=='done':break
                    if data.get('status') not in {'in_queue','generating'}:
                        record['status']='failed';save()
                        raise GenerationError('即梦服务返回终止或未知任务状态。')
                    time.sleep(poll_seconds)
                else:
                    save()
                    raise GenerationError('即梦任务仍未完成，已保留任务编号；重试仅查询，不重复创建。')
                encoded=data.get('binary_data_base64') or []
                urls=data.get('image_urls') or []
                if not encoded and not urls:raise GenerationError('即梦任务完成但没有返回图片。')
                data={'b64_json':encoded[0]} if encoded else {'url':urls[0]}
            else:
                record['status']='submission_unknown';save()
                response=client.post(service.base_url+'/images/generations',headers=headers,
                    json={'model':service.model,'prompt':prompt,'n':1})
                response.raise_for_status();data=response.json()['data'][0]
            if data.get('b64_json'):
                if len(data['b64_json'])>28_000_000:raise GenerationError('图片编码超过大小限制。')
                content=base64.b64decode(data['b64_json'],validate=True)
            else:content=_download(data['url'],service,20_000_000,client)
            with Image.open(BytesIO(content)) as im:
                if im.width*im.height>20_000_000:raise GenerationError('图片像素数量超过限制。')
                im.load();poster=folder/f'{key}.png';im.convert('RGB').save(poster)
            record['poster']=poster.name
        else:
            task=saved.get('task_id')
            if not task:
                record['status']='submission_unknown';save()
                response=client.post(service.base_url+'/contents/generations/tasks',headers=headers,json={
                    'model':service.model,'content':[{'type':'text','text':prompt}],
                    'ratio':'16:9','duration':5,'generate_audio':False})
                response.raise_for_status();task=response.json()['id']
            if not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}',task):raise GenerationError('视频任务编号无效。')
            record.update(task_id=task,status='submitted');save()
            for _ in range(100):
                response=client.get(service.base_url+'/contents/generations/tasks/'+task,headers=headers)
                response.raise_for_status();data=response.json();status=data['status']
                if status=='succeeded':break
                if status in {'failed','cancelled','expired'}:
                    record['status']='failed';save()
                    raise GenerationError('视频服务返回终止状态。')
                if status not in {'queued','running'}:raise GenerationError('视频服务返回未知任务状态。')
                time.sleep(poll_seconds)
            else:
                record['status']='submitted';save()
                raise GenerationError('视频任务仍未完成，已保留任务编号；重试仅查询，不重复创建。')
            video=folder/f'{key}.mp4';video.write_bytes(_download(data['content']['video_url'],service,100_000_000,client))
            duration=clip_duration(video)
            if not 0<duration<=120:raise GenerationError('视频时长无效。')
            poster=folder/f'{key}.png'
            _run(['-i',str(video),'-frames:v','1',str(poster)],poster)
            record.update(poster=poster.name,video=video.name,duration=duration)
        record['status']='complete'
        record['sha256']={record[k]:hashlib.sha256((folder/record[k]).read_bytes()).hexdigest()
                         for k in ('poster','video') if k in record};save()
        return record
    except Exception as exc:
        if record['status']!='submitted':record['status']='submission_unknown' if record['status']=='submission_unknown' else 'failed'
        record['error_type']=type(exc).__name__;save()
        if isinstance(exc,GenerationError):raise
        raise GenerationError('媒体服务请求未完成；已保存不含密钥的状态记录，请检查服务配置。') from None
    finally:
        if own:client.close()


def enrich_generated_media(lesson,art,folder,progress):
    if lesson.deck_plan.get('comic_story',{}).get('format')=='scene_drama':
        from zhijiang.drama import enrich_drama_media
        return enrich_drama_media(lesson,art,folder,progress)
    folder=Path(folder);media=folder/'generated-media';items={};used=0
    for shot in lesson.deck_plan.get('storyline',[]):
        if not shot.get('illustration_prompt'):continue
        for kind,service in (('image',art.image),('video',art.video)):
            if service.protocol=='disabled' or used>=art.max_assets:continue
            used+=1;number=shot['segment_id']
            progress(f'绘制课程插画与视频（{used}/{art.max_assets}）',68)
            # Only the selected scene brief is transmitted, never the entire PDF or raw speech.
            prompt='原创教学动漫/插画场景。不要照片、不要现成人物或版权角色、不要画文字、公式或字幕。\n'+shot['illustration_prompt']
            try:
                asset=generate_asset(service,prompt,media,kind)
                item={'file':(Path('generated-media')/asset['poster']).as_posix(),
                    'kind':'generated_'+kind,'identity':'原创生成插画，非教材原图','prompt':prompt,
                    **({'video':(Path('generated-media')/asset['video']).as_posix()} if asset.get('video') else {})}
                items.setdefault(str(number),[]).append(item)
            except GenerationError as exc:
                lesson.animation_report.setdefault('warnings',[]).append({'segment_id':number,'stage':'generated_media','message':str(exc)})
    lesson.deck_plan['generated_media']=items
