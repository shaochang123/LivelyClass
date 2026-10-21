import json
import pytest

pytest.importorskip('sympy')
from zhijiang.agents import GenerationError
from zhijiang.models import PageText, SourceDocument
from zhijiang.automatic_construction import build_scene, enrich_constructions


def test_source_timing_schema_prevents_alternative_speech_without_relaxing_math_tools():
    from zhijiang.automatic_construction import source_timing_behavior
    from zhijiang.math_behavior import behavior_schema,decode_behavior
    from zhijiang.math_construction import MathConstructionDraft
    from pydantic import ValidationError
    graph=MathConstructionDraft(title='点的连续变化',objective='观察点沿坐标的连续变化。',source_id=1,
        parameters={'t':1},constructions=[{'id':'o','op':'point','expr':['0','0']},
            {'id':'p','op':'point','expr':['t','1']}])
    schema=source_timing_behavior(behavior_schema(graph,{1:{}}))
    data={'operations':[{'changes':{'t':t},'measurements':{'横坐标':{'terms':[{'kind':'xcoord','point':'p'}]}}}
        for t in (1,1.5,2)],'claims':[{'source_id':1,'left':{'terms':[{'kind':'ycoord','point':'p'}]},
            'right':{'terms':[{'kind':'scalar','expr':'1'}]}}]}
    draft=schema.model_validate(data)
    operations,claims=decode_behavior(draft)
    assert all(op.narration=='观察当前对象与参数的变化，具体测量以画面为准。' for op in operations)
    assert operations[-1].changes['t']==2 and claims
    data['operations'][0]['narration']='模型另写的口播不允许进入这个流程。'
    with pytest.raises(ValidationError):schema.model_validate(data)
    del data['operations'][0]['narration']
    data['operations'][0]['measurements']['横坐标']['terms'][0]['point']='not_an_object'
    with pytest.raises(ValidationError):schema.model_validate(data)


def test_curve_reference_type_error_keeps_real_object_id_for_bounded_repair():
    from zhijiang.math_construction import Compiler,MathConstructionDraft,ConstructionObjectError
    graph=MathConstructionDraft(title='曲线上的点',objective='保持函数图像与点的真实类型对应。',source_id=1,
        parameters={'u':.2},constructions=[{'id':'f','op':'function','expr':['sin(x)']},
            {'id':'p','op':'point_on_curve','refs':['f'],'expr':['u']}])
    with pytest.raises(ConstructionObjectError,match='point_on_function') as error:Compiler(graph)
    assert error.value.object_id=='p'
    fixed=graph.model_copy(deep=True);fixed.constructions[1].op='point_on_function'
    assert Compiler(fixed).items['p']['type']=='point'


def source():
    return SourceDocument(filename='unseen.pdf',pages=[PageText(page=7,
        text='A varying input is mapped to the sine of that input. The point lies on the sine curve.')])


def candidate():
    return dict(title='输入与曲线上实际点',objective='通过移动输入观察曲线上的点与对应函数值。',source_id=1,
        parameters={'u':0},constructions=[{'id':'f','op':'function','expr':['sin(x)'],'domain':[-1,1]},
            {'id':'p','op':'point_on_function','refs':['f'],'expr':['u']}],
        operations=[{'narration':'沿曲线移动实际点，比较当前位置的函数值。','changes':{'u':u}}
                    for u in [.2,.5,.8]],
        claims=[{'source_id':1,'lhs':'ycoord(p)','rhs':'value(f,u)'}])


def reviewed_steps(supported=True):
    return [{'step':i,'observed_relation':'实际点与函数值的对应保持成立。','claim_ids':[1],'supported':supported}
            for i in range(1,4)]


def test_rejected_scene_is_repaired_and_only_accepted_scene_is_cached(tmp_path):
    calls=[]
    class Client:
        model='test-model'
        def generate(self,schema,instruction,material,**kwargs):
            calls.append(schema.__name__)
            if schema.__name__=='MathConstructionDraft':
                raw=candidate()
                return schema.model_validate({k:v for k,v in raw.items() if k not in {'operations','claims','roles'}})
            if schema.__name__=='MathBehaviorDraft':
                return schema.model_validate({'operations':candidate()['operations'],
                    'claims':[{'source_id':1,'left':{'terms':[{'kind':'ycoord','point':'p'}]},
                               'right':{'terms':[{'kind':'value','function':'f','at':'u'}]}}]})
            assert schema.__name__=='MathProgramReview' and kwargs['images']
            supported=calls.count('MathProgramReview')>1
            return schema.model_validate(dict(approved=supported,
                checks=[{'aspect':a,'supported':supported,
                         'observation':'测试替身检查实际曲线、点坐标和参数变化的对应关系。'}
                        for a in ['source','geometry','motion']],
                repair_target='behavior',issues=[] if supported else ['第一轮尚未解释原有参数的意义。'],
                source_coverage=['函数和实际点的对应。'],step_checks=reviewed_steps(supported)))
    scene=build_scene(Client(),source(),[{'segment_id':1}],folder=tmp_path)
    assert scene.verification['passed']
    attempts=json.loads((tmp_path/'attempts.json').read_text(encoding='utf-8'))
    assert len(attempts)==2 and attempts[0]['error'] and attempts[1]['review']['approved']
    assert calls.count('MathConstructionDraft')==1  # Retain the compiled graph during behavior repair.
    assert (tmp_path/'accepted.json').is_file()
    old_calls=len(calls)
    build_scene(Client(),source(),[{'segment_id':1}],folder=tmp_path)
    assert len(calls)==old_calls


def test_numbered_measurement_name_preserves_computed_values():
    from zhijiang.math_construction import MathProgramDraft,compile_program
    from zhijiang.models import Evidence
    from zhijiang.visual_planning import verify_visual_scene,VisualSceneError
    raw=candidate()
    raw['constructions'][1]['id']='p1'
    raw['claims'][0]['lhs']='ycoord(p1)'
    for op in raw['operations']:op['measurements']={'p1纵坐标':'ycoord(p1)'}
    program=MathProgramDraft.model_validate(raw)
    scene=compile_program(program,Evidence(page=7,quote=source().pages[0].text),
                          {1:{'quote':source().pages[0].text}})
    assert verify_visual_scene(scene)['passed']
    assert scene.beats[0].calculations[0].label=='p1纵坐标'
    raw['operations'][0]['measurements']={'p1纵坐标':'42'}
    with pytest.raises(VisualSceneError,match='必须测量实际对象'):
        compile_program(MathProgramDraft.model_validate(raw),scene.evidence,{1:{'quote':source().pages[0].text}})


def test_uncoupled_control_repairs_graph_before_planning_behavior(tmp_path):
    calls=[]
    class Client:
        def generate(self,schema,instruction,material,**kwargs):
            calls.append(schema.__name__)
            if schema.__name__=='MathConstructionDraft':
                raw=candidate();raw['constructions'][1]['expr']=['0']
                return schema.model_validate({k:v for k,v in raw.items() if k not in {'operations','claims','roles'}})
            if schema.__name__=='MathConstructionPatch':
                assert '未耦合' in instruction
                return schema(upserts=[candidate()['constructions'][1]])
            if schema.__name__=='MathBehaviorDraft':
                return schema.model_validate({'operations':candidate()['operations'],
                    'claims':[{'source_id':1,'left':{'terms':[{'kind':'ycoord','point':'p'}]},
                               'right':{'terms':[{'kind':'value','function':'f','at':'u'}]}}]})
            return schema.model_validate({'approved':True,'checks':[
                {'aspect':a,'supported':True,'observation':'检查实际曲线和动点的坐标以及运动轨迹。'}
                for a in ['source','geometry','motion']],'source_coverage':['实际函数关系。'],'issues':[],
                'step_checks':reviewed_steps()})
    scene=build_scene(Client(),source(),[{'segment_id':1}],folder=tmp_path)
    assert scene.verification['passed'] and calls.count('MathConstructionDraft')==1
    assert calls.count('MathConstructionPatch')==1 and calls.count('MathBehaviorDraft')==1
    attempts=json.loads((tmp_path/'attempts.json').read_text(encoding='utf-8'))
    assert '未耦合' in attempts[0]['error'] and attempts[1]['construction_patch']


def test_configured_planner_and_reviewer_are_used_for_ordinary_constructions(tmp_path):
    calls=[]
    class UnusedWriter:
        model='writer'
        def generate(self,*args,**kwargs):raise AssertionError('Configured roles must receive their own stages')
    class Planner:
        model='planner'
        def generate(self,schema,*args,**kwargs):
            calls.append(('planner',schema.__name__))
            if schema.__name__=='MathConstructionDraft':
                return schema.model_validate({k:v for k,v in candidate().items() if k not in {'operations','claims','roles'}})
            return schema.model_validate({'operations':candidate()['operations'],
                'claims':[{'source_id':1,'left':{'terms':[{'kind':'ycoord','point':'p'}]},
                           'right':{'terms':[{'kind':'value','function':'f','at':'u'}]}}]})
    class Reviewer:
        model='reviewer'
        def generate(self,schema,*args,**kwargs):
            calls.append(('reviewer',schema.__name__))
            assert kwargs['images'] and schema.__name__=='MathProgramReview'
            return schema.model_validate({'approved':True,'checks':[
                {'aspect':a,'supported':True,'observation':'实际关系与曲线上点的运动保持一致。'}
                for a in ['source','geometry','motion']],'source_coverage':['实际函数关系。'],'issues':[],
                'step_checks':reviewed_steps()})

    scene=build_scene(UnusedWriter(),source(),[{'segment_id':1}],folder=tmp_path,
                      planning_client=Planner(),review_client=Reviewer())
    assert scene.verification['passed']
    assert calls==[('planner','MathConstructionDraft'),('planner','MathBehaviorDraft'),('reviewer','MathProgramReview')]
    assert scene.mathematical_model['model_roles']['construction_and_behavior']=='planner'


def test_each_step_needs_a_claim_including_final_counterexample():
    from zhijiang.automatic_construction import validate_step_claim_coverage
    from zhijiang.math_construction import MathProgramDraft
    from zhijiang.visual_planning import VisualSceneError
    raw=candidate();raw['claims'][0]['steps']=[1,2]
    with pytest.raises(VisualSceneError,match='第3步缺少'):
        validate_step_claim_coverage(MathProgramDraft.model_validate(raw))


def test_model_service_failure_does_not_replan_the_same_scene(tmp_path):
    calls=[]
    class Offline:
        def generate(self,*args,**kwargs):
            calls.append(args[0].__name__)
            raise GenerationError('模型服务额度不可用')
    with pytest.raises(GenerationError,match='模型服务额度不可用'):
        build_scene(Offline(),source(),[{'segment_id':1}],folder=tmp_path)
    assert len(calls)==1


@pytest.mark.parametrize('width,passes',[(2,False),(3,True)])
def test_actual_inequality_rejects_an_identical_counterexample(width,passes):
    from zhijiang.math_construction import MathProgramDraft,compile_program
    from zhijiang.models import Evidence
    from zhijiang.visual_planning import verify_visual_scene,VisualSceneError
    raw={'title':'对照实际图形的差异','objective':'通过比较实际面积，验证不同与相同的说法。','source_id':1,
        'parameters':{'u':1},'constructions':[{'id':'a','op':'rectangle','expr':['0','0','2','u']},
            {'id':'b','op':'rectangle','expr':['4','0',f'4+{width}','u']}],
        'operations':[{'narration':'同步改变图形，观察实际测量是否不同。','changes':{'u':v}} for v in [1,1.2,1.4]],
        'claims':[{'source_id':1,'lhs':'area(a)','rhs':'area(b)','relation':'not_equal'}]}
    scene=compile_program(MathProgramDraft.model_validate(raw),Evidence(page=7,quote=source().pages[0].text),
        {1:{'quote':source().pages[0].text}})
    if passes:assert verify_visual_scene(scene)['passed']
    else:
        with pytest.raises(VisualSceneError,match='实际数学关系失败'):verify_visual_scene(scene)


def test_optional_object_generation_failure_preserves_entire_course(tmp_path,monkeypatch):
    from test_deck_planning import example_lesson
    import zhijiang.visual_planning as planning
    import zhijiang.automatic_construction as automatic
    import zhijiang.construction_intent as routing
    from types import SimpleNamespace
    monkeypatch.setattr(planning,'visual_capabilities',lambda:{'geometry_ready':True})
    monkeypatch.setattr(routing,'read_construction_intent',lambda *a,**k:SimpleNamespace(relation_kind='quantitative'))
    def fail(*args,**kwargs):raise GenerationError('模型服务不可用')
    monkeypatch.setattr(automatic,'build_scene',fail)
    pdf=tmp_path/'source.pdf';pdf.write_bytes(b'test input read only')
    lesson=example_lesson(2);before=[s.model_dump() for s in lesson.segments]
    enrich_constructions(lesson,source(),object(),[{'segment_id':1,'page':7,'graphic_group':'mapping'}],
        folder=tmp_path,pdf_path=pdf,progress=lambda *args:None)
    assert [s.model_dump() for s in lesson.segments]==before
    assert '模型服务不可用' in lesson.animation_report['warnings'][0]['message']


@pytest.mark.parametrize('problem',['nonfinite_coordinate','compressed_view'])
def test_graph_errors_are_patched_before_rewriting_behavior(tmp_path,problem):
    calls=[]
    class Client:
        def generate(self,schema,instruction,material,**kwargs):
            calls.append(schema.__name__)
            if schema.__name__=='MathConstructionDraft':
                raw=candidate()
                # Forward references are harmless ordering errors, repaired by
                # the compiler. The remaining error really needs a graph edit.
                raw['constructions'].reverse()
                if problem=='nonfinite_coordinate':raw['constructions'][0]['expr']=['sqrt(-1)']
                else:raw['constructions'].append({'id':'far','op':'point','expr':['10000','0']})
                return schema.model_validate({k:v for k,v in raw.items() if k not in {'operations','claims','roles'}})
            if schema.__name__=='MathConstructionPatch':
                if problem=='nonfinite_coordinate':
                    assert '有限实数' in instruction
                    return schema(upserts=[candidate()['constructions'][1]])
                assert '不足六十像素' in instruction
                return schema(upserts=[{'id':'far','op':'point','expr':['1','0']}])
            if schema.__name__=='MathBehaviorDraft':
                return schema.model_validate({'operations':candidate()['operations'],
                    'claims':[{'source_id':1,'left':{'terms':[{'kind':'ycoord','point':'p'}]},
                               'right':{'terms':[{'kind':'value','function':'f','at':'u'}]}}]})
            assert schema.__name__=='MathProgramReview'
            return schema.model_validate({'approved':True,'checks':[
                {'aspect':a,'supported':True,'observation':'实际曲线和对应动点完整保持原有函数关系。'}
                for a in ['source','geometry','motion']],'source_coverage':['函数与动点。'],
                'issues':[],'step_checks':reviewed_steps()})
    scene=build_scene(Client(),source(),[{'segment_id':1}],folder=tmp_path)
    assert scene.verification['passed']
    assert calls.count('MathConstructionDraft')==1 and calls.count('MathConstructionPatch')==1
    assert calls.index('MathConstructionPatch')<calls.index('MathProgramReview')
    if problem=='nonfinite_coordinate':assert calls.index('MathConstructionPatch')<calls.index('MathBehaviorDraft')
    attempts=json.loads((tmp_path/'attempts.json').read_text(encoding='utf-8'))
    expected=['f','p'] if problem=='nonfinite_coordinate' else ['f','p','far']
    assert attempts[0]['dependency_order']==expected
    assert attempts[0]['error'] and attempts[1]['construction_patch']


def test_invalid_object_repair_requires_its_real_definition():
    from pydantic import ValidationError
    from zhijiang.math_construction import MathConstructionDraft
    from zhijiang.automatic_construction import graph_patch_schema
    raw=candidate();raw['constructions'][1]['expr']=['0']
    graph=MathConstructionDraft.model_validate({k:v for k,v in raw.items() if k not in {'operations','claims'}})
    schema=graph_patch_schema(graph,'p')
    for patch in [{},{'upserts':[]},{'objective':'仅改目标，实际错误依然没有得到修复。'},
                  {'upserts':[raw['constructions'][0]]},{'upserts':[raw['constructions'][1]]}]:
        with pytest.raises(ValidationError):schema.model_validate(patch)
    good=schema.model_validate({'upserts':[candidate()['constructions'][1]]})
    assert good.upserts[0].id=='p'


@pytest.mark.parametrize('op,expr,refs',[
    ('point',['x','0'],[]),('point_on_function',['x'],['f']),
    ('transform',['0','x','0'],['p']),
])
def test_concrete_object_schema_rejects_free_function_coordinates(op,expr,refs):
    from pydantic import ValidationError
    from zhijiang.math_tool_schema import typed_construction
    from pydantic import TypeAdapter
    with pytest.raises(ValidationError,match='函数自由变量'):
        TypeAdapter(typed_construction()).validate_python({'id':'p','op':op,'expr':expr,'refs':refs})


def test_trajectory_tools_keep_their_real_free_variable_contract():
    from pydantic import TypeAdapter
    from zhijiang.math_tool_schema import typed_construction
    adapter=TypeAdapter(typed_construction())
    curve=adapter.validate_python({'id':'curve','op':'parametric','expr':['cos(x)','sin(x)']})
    assert curve.expr==['cos(x)','sin(x)']
    schema=adapter.json_schema()
    actual=schema['$defs']['Tool_parametric']['description']
    assert '可使用函数自变量x' in actual and '禁止' not in actual
