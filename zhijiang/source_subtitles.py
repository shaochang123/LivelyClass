"""Complete verbatim subtitles timed from separately synthesized source utterances."""
from __future__ import annotations
import hashlib
import json
import os
import re
from pathlib import Path
import wave
from functools import lru_cache
from PIL import Image,ImageDraw
from zhijiang.presentation import PresentationError
from zhijiang.video import _font,_normalize_audio
from zhijiang.speech_cache import synthesize_cached
from zhijiang.page_media import _run,_encoding,clip_duration
import imageio_ffmpeg


@lru_cache(maxsize=8)
def subtitle_font(size):
    return _font(size)


def subtitle_lines(text,width=1160,size=32):
    draw=ImageDraw.Draw(Image.new('RGB',(1,1)));font=subtitle_font(size)
    result=[];line=''
    for c in text:
        if c=='\n':result.append(line);line='';continue
        if line and draw.textlength(line+c,font=font)>width:result.append(line);line=''
        line+=c
    return result+[line]


def utterances(text):
    parts=[];start=0
    for end in range(1,len(text)+1):
        if len(subtitle_lines(text[start:end]))>2:
            cut=end-1
            # Preserve English words and ordinary clauses in speech inputs;
            # all separators remain in the source character ledger.
            boundaries=[start+m.end() for m in re.finditer(r'\s+|[。！？；，.!?;:]',text[start:cut])
                        if m.end()>=(cut-start)//2]
            if boundaries:cut=max(boundaries)
            if cut<=start:raise PresentationError('字幕单个字形超出可读区域。')
            parts.append(text[start:cut]);start=cut
    if start<len(text):parts.append(text[start:])
    # Blank formatting stays attached to an adjacent utterance, never discarded.
    merged=[];pending=''
    for p in parts:
        if not p.strip():
            if merged:merged[-1]+=p
            else:pending+=p
        else:merged.append(pending+p);pending=''
    if pending:
        if not merged:raise PresentationError('页面只有空白字符，无法生成原文配音。')
        merged[-1]+=pending
    assert ''.join(merged)==text
    return merged


def synthesize_source(lesson,speech,folder,progress):
    folder=Path(folder);records=[];files=[];counter=0
    for index,segment in enumerate(lesson.segments,1):
        parts=utterances(segment.narration);cue_folder=folder/'source-speech'/f'{index:04d}'
        cue_folder.mkdir(parents=True,exist_ok=True);audio=folder/f'audio-{index-1}.wav';cursor=0;record=[]
        with wave.open(str(audio),'wb') as output:
            output.setnchannels(1);output.setsampwidth(2);output.setframerate(24000)
            for n,text in enumerate(parts,1):
                progress(f'逐字原文配音（{index}/{len(lesson.segments)}段，{n}/{len(parts)}句）',73+index*12//len(lesson.segments))
                path=cue_folder/f'cue-{n:04d}.wav'
                synthesize_cached(speech,text,path)
                with wave.open(str(path)) as raw:
                    parameters=(raw.getnchannels(),raw.getsampwidth(),raw.getframerate())
                if parameters!=(1,2,24000):
                    normalized=cue_folder/f'normal-{n:04d}.wav'
                    _normalize_audio(path,normalized,imageio_ffmpeg.get_ffmpeg_exe());path=normalized
                with wave.open(str(path)) as raw:
                    frames=raw.getnframes();data=raw.readframes(frames)
                if frames<=0:raise PresentationError('原文配音返回空音频。')
                output.writeframes(data)
                record.append({'text':text,'source_start':counter,'source_end':counter+len(text),
                    'start':cursor/24000,'end':(cursor+frames)/24000,
                    'speech_input_sha256':hashlib.sha256(text.encode()).hexdigest()})
                cursor+=frames;counter+=len(text)
        if ''.join(c['text'] for c in record)!=segment.narration:raise PresentationError('原文配音句子未完整覆盖。')
        records.append({'segment_id':index,'audio_duration':cursor/24000,'cues':record});files.append(audio)
    (folder/'source-speech.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf8')
    return files,records


def timestamp(value,vtt=False):
    n=round(value*1000);h,n=divmod(n,3600000);m,n=divmod(n,60000);s,ms=divmod(n,1000)
    return f'{h:02d}:{m:02d}:{s:02d}{"." if vtt else ","}{ms:03d}'


def burn_source_subtitles(lesson,records,video):
    video=Path(video);folder=video.parent
    manifest=json.loads((folder/'presentation-preview/manifest.json').read_text(encoding='utf8'))
    offset=sum(x['duration'] for x in manifest.get('bookends',[]) if x['kind']=='cover')
    clips={x['segment_id']:x for x in manifest['segments']};cues=[]
    for segment in records:
        duration=clips[segment['segment_id']]['duration'];scale=duration/segment['audio_duration']
        for cue in segment['cues']:
            cues.append({**cue,'segment_id':segment['segment_id'],
                'start':offset+cue['start']*scale,'end':offset+cue['end']*scale})
        offset+=duration
    if ''.join(c['text'] for c in cues)!=''.join(s.narration for s in lesson.segments):
        raise PresentationError('最终字幕未逐字覆盖原文配音。')
    (folder/'subtitles.json').write_text(json.dumps({'mode':'source_verbatim','timing':'actual_utterance_audio_durations',
        'cues':cues,'display_whitespace':'Original text is exact in JSON; blank-line formatting is normalized in SRT/VTT.'},ensure_ascii=False,indent=2),encoding='utf8')
    for suffix in ('srt','vtt'):
        body=['WEBVTT\n'] if suffix=='vtt' else []
        for i,cue in enumerate(cues,1):
            text='\n'.join(line for line in cue['text'].replace('\r','').splitlines() if line.strip()).strip()
            body.append(f'{i}\n{timestamp(cue["start"],suffix=="vtt")} --> {timestamp(cue["end"],suffix=="vtt")}\n{text}\n')
        (folder/f'subtitles.{suffix}').write_text('\n'.join(body),encoding='utf8')
    asset_folder=folder/'source-subtitle-frames';asset_folder.mkdir(exist_ok=True)
    blank=asset_folder/'blank.png';Image.new('RGBA',(1280,720),(0,0,0,0)).save(blank)
    entries=[];cursor=0.
    for i,cue in enumerate(cues,1):
        if cue['start']>cursor+.000001:entries.append((blank,cue['start']-cursor))
        im=Image.new('RGBA',(1280,720),(0,0,0,0));draw=ImageDraw.Draw(im)
        draw.rectangle((0,608,1280,720),fill='#0A1423')
        lines=subtitle_lines(cue['text'])
        if len([x for x in lines if x.strip()])>2:raise PresentationError('字幕未能在两行内完整呈现。')
        visible=[x for x in lines if x.strip()]
        y=620+(2-len(visible))*20
        for line in visible:
            width=draw.textlength(line,font=subtitle_font(32));draw.text(((1280-width)/2,y),line,font=subtitle_font(32),fill='#FFFFFF');y+=42
        path=asset_folder/f'cue-{i:06d}.png';im.save(path);entries.append((path,cue['end']-cue['start']));cursor=cue['end']
    duration=clip_duration(video)
    if duration>cursor:entries.append((blank,duration-cursor))
    concat=asset_folder/'frames.txt'
    quoted=lambda p:str(p.resolve().as_posix()).replace("'","'\\''")
    concat.write_text('\n'.join(f"file '{quoted(path)}'\nduration {seconds:.9f}" for path,seconds in entries)+
        f"\nfile '{quoted(entries[-1][0])}'\n",encoding='utf8')
    base=folder/'presentation-media/course-without-subtitles.mp4';os.replace(video,base)
    _run(['-i',str(base),'-f','concat','-safe','0','-i',str(concat),
        '-filter_complex','[0:v]scale=1280:608:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:0:color=0x0A1423[board];[board][1:v]overlay=shortest=1:format=auto[v]',
        '-map','[v]','-map','0:a:0',*_encoding(),'-t',str(duration),str(video)],video)
    return cues
