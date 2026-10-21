import json
import wave
import pytest
from zhijiang.models import SourceDocument, PageText, GenerationOptions, VoiceMode, Lesson
from zhijiang.literal_course import build_literal_course, verify_literal, source_units
from zhijiang.source_subtitles import utterances, synthesize_source
from zhijiang.agents import GenerationError


def document():
    return SourceDocument(filename='source.pdf',pages=[
        PageText(page=1,text='序'),
        PageText(page=2,text=('重复原句。\n不要删除重复原句！ x² + y² = 1。\n'*35)+'\n终。',ocr=True)])


def build(tmp_path, doc=None):
    return build_literal_course(doc or document(),None,GenerationOptions(narration_mode='source_verbatim'),
        VoiceMode.SYSTEM,tmp_path/'slides',lambda *a:None)


def test_exact_source_roundtrip_and_character_coverage(tmp_path):
    doc=document();lesson=build(tmp_path,doc)
    lesson=Lesson.model_validate_json(lesson.model_dump_json())
    assert ''.join(s.narration for s in lesson.segments)==''.join(p.text for p in doc.pages)
    report=verify_literal(doc,lesson)
    assert report['source_characters']==report['narration_characters']
    assert report['pages'][1]['ocr']
    assert len(source_units(doc))>2
    assert lesson.segments[0].evidence.literal


@pytest.mark.parametrize('change',['missing','duplicate','reorder','rewrite'])
def test_literal_rejects_loss_repetition_reorder_rewrite(tmp_path,change):
    lesson=build(tmp_path)
    if change=='missing':lesson.segments.pop()
    elif change=='duplicate':lesson.segments.append(lesson.segments[-1])
    elif change=='reorder':lesson.segments.reverse()
    else:lesson.segments[0].narration='模型改写'
    with pytest.raises(GenerationError):verify_literal(document(),lesson)


def test_source_voice_and_subtitles_use_all_exact_characters(tmp_path):
    from zhijiang.source_subtitles import subtitle_lines
    text='\n完整原文\n\n'+('Do not omit any words. 数学条件 x ≠ 0。'*20)+'\n'
    parts=utterances(text)
    assert ''.join(parts)==text
    assert all(len([x for x in subtitle_lines(p) if x.strip()])<=2 for p in parts)
    doc=SourceDocument(filename='literal.pdf',pages=[PageText(page=1,text=text)])
    lesson=build(tmp_path,doc);calls=[]
    class Speech:
        voice='literal-test'
        def synthesize(self,text,path):
            calls.append(text)
            with wave.open(str(path),'wb') as w:
                w.setnchannels(1);w.setsampwidth(2);w.setframerate(24000)
                w.writeframes(b'\0\0'*2400)
    paths,records=synthesize_source(lesson,Speech(),tmp_path,lambda *a:None)
    assert ''.join(calls)==text
    assert ''.join(c['text'] for r in records for c in r['cues'])==text
    assert all(r['audio_duration']==pytest.approx(len(r['cues'])*.1) for r in records)
    assert all(p.is_file() for p in paths)


def test_source_utterances_do_not_split_ordinary_english_words():
    text='Every original textbook word remains readable and spoken completely. '*20
    parts=utterances(text)
    assert ''.join(parts)==text
    for a,b in zip(parts,parts[1:]):assert not (a[-1].isalpha() and b[0].isalpha())


def test_model_plans_layout_and_typed_drawing_tool_without_owning_source_speech(tmp_path):
    doc=SourceDocument(filename='geometry.pdf',pages=[PageText(page=1,text='函数的图像随参数连续变化。原文公式与条件必须保留。')])
    class Client:
        model='fixture';base_url='http://localhost:11434'
        def generate(self,schema,instruction,material):
            source=json.loads(material)['source']
            assert source['text']==doc.pages[0].text
            return schema(title='函数图像',bullets=['观察参数变化'],visual_medium='computed',
                visual={'representation':'key_idea','items':[{'label':'函数图像','caption':'观察参数变化','sentence_id':1}],
                    'links':[],'context':''},story_stage='观察原例',layout='split',role='example',
                motion_request='按原文构建函数曲线，连续改变给定参数。')
    lesson=build_literal_course(doc,Client(),GenerationOptions(ppt_template='paper',narration_mode='source_verbatim'),
        VoiceMode.SYSTEM,tmp_path/'slides',lambda *a:None)
    assert lesson.segments[0].narration==doc.pages[0].text
    assert lesson.deck_plan['pages'][0]['layout']=='split'
    request=lesson.deck_plan['construction_requests'][0]
    assert request['visual_medium']=='computed' and request['narration']==doc.pages[0].text
