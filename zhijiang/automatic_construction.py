"""Optional, computed objects in ordinary courses, independent of subject names."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import create_model, Field,model_validator
from zhijiang.agents import GenerationError, ModelContractError
from zhijiang.models import Evidence, SourceDocument


def validate_step_claim_coverage(program):
    from zhijiang.visual_planning import VisualSceneError
    for step in range(1,len(program.operations)+1):
        if not any(not claim.steps or step in claim.steps for claim in program.claims):
            raise VisualSceneError(f'第{step}步缺少核验其结论的实际数学关系；必须补充相应claim，不删除步骤或主题。')


def step_review_schema(program):
    from pydantic import BaseModel
    from zhijiang.math_construction import MathProgramReview
    entries=[]
    for step in range(1,len(program.operations)+1):
        claims=tuple(i for i,claim in enumerate(program.claims,1) if not claim.steps or step in claim.steps)
        entries.append(create_model(f'MathStepReview{step}',step=(Literal[step],Field()),
            observed_relation=(str,Field(min_length=8)),claim_ids=(list[Literal[claims]],Field(min_length=1)),
            supported=(bool,Field())))
    return create_model('MathProgramReview',__base__=MathProgramReview,
                        step_checks=(tuple[tuple(entries)],Field()))


def graph_patch_schema(graph, object_id=None):
    from zhijiang.math_graph_repair import MathConstructionPatch
    from zhijiang.math_tool_schema import typed_construction
    known={item.id for item in graph.constructions}
    # An invalid initial value has a concrete object identity. Require its
    # actual replacement; an empty or metadata-only patch cannot repair it.
    target=object_id if object_id in known else None
    def actual_edit(value):
        if target:
            before=next(item for item in graph.constructions if item.id==target)
            if value.upserts[0].model_dump()==before.model_dump():
                raise ValueError('错误对象的替换定义与原定义完全相同；必须实际修正expr/refs，而不是重复原候选。')
        return value
    return create_model('MathConstructionPatch',__base__=MathConstructionPatch,
        __doc__='upserts须实际修改错误对象的expr/refs；空补丁、重复原定义或只改教学目标不能修复图形。',
        __validators__={'actual_edit':model_validator(mode='after')(actual_edit)},
        upserts=(list[typed_construction(object_ids=(target,) if target else None)],
                 Field(min_length=1,max_length=1) if target else Field(default_factory=list,max_length=16)))


def source_timing_behavior(schema):
    """Source-reading scenes plan geometry only; they do not author new speech."""
    from typing import get_args
    operation=get_args(schema.model_fields['operations'].annotation)[0]
    placeholder='观察当前对象与参数的变化，具体测量以画面为准。'
    timed=create_model('SourceTimingOperation',__base__=operation,
        narration=(Literal[placeholder],Field(default=placeholder)))
    return create_model('SourceTimingBehavior',__base__=schema,
        operations=(list[timed],Field(min_length=3,max_length=10)))


def build_scene(client, document, requests, *, folder, source_image_loader=None,planning_client=None,review_client=None,
                source_verbatim=False):
    from zhijiang.mathematical_planning import source_material, CONTRACT, review_step_states
    from zhijiang.math_construction import MathProgramDraft, MathConstructionDraft, MathematicalClaim, MathProgramReview, ConstructionLayoutError, compile_program, Compiler
    from zhijiang.math_tool_schema import typed_construction
    from zhijiang.math_behavior import behavior_schema, decode_behavior, attach_measurement_glyphs
    from zhijiang.math_inventory import computed_inventory, validate_initial_geometry
    from zhijiang.visual_planning import verify_visual_scene, VisualSceneError
    from zhijiang.visual_media import visual_summary_svg
    from zhijiang.math_media import render_summary_png
    mapping,source=source_material(document)
    planning_client=planning_client or client;review_client=review_client or client
    ids=tuple(mapping)
    claim=create_model('AutomaticClaim',__base__=MathematicalClaim,source_id=(Literal[ids],Field()))
    schema=create_model('MathAutomaticProgram',__base__=MathProgramDraft,
        source_id=(Literal[ids],Field()),claims=(list[claim],Field(min_length=1,max_length=12)))
    graph_schema=create_model('MathConstructionDraft',__base__=MathConstructionDraft,
        source_id=(Literal[ids],Field()),constructions=(list[typed_construction()],Field(min_length=2,max_length=32)))
    code_hashes={name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
        for name in ('math_construction.py','math_tool_schema.py','math_behavior.py','math_inventory.py','math_graph_repair.py',
                     'visual_planning.py','visual_media.py','automatic_construction.py','source_figures.py')}
    fingerprint=hashlib.sha256(json.dumps({'version':'automatic-construction-v3-staged-tools','source':source,'code':code_hashes,
        'requests':requests,'contract':CONTRACT,'source_verbatim':source_verbatim,
        'planning_model':getattr(planning_client,'model',''),'planning_endpoint':getattr(planning_client,'base_url',''),
        'review_model':getattr(review_client,'model',''),'review_endpoint':getattr(review_client,'base_url',''),
        'planning_thinking':getattr(planning_client,'semantic_thinking',True),
        'review_thinking':getattr(review_client,'semantic_thinking',True),'model':getattr(client,'model',''),
        'endpoint':getattr(client,'base_url',''),'thinking':getattr(client,'semantic_thinking',True)},
        ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    cache=folder/'accepted.json'
    def compile_candidate(program):
        validate_step_claim_coverage(program)
        entry=mapping[program.source_id]
        scene=compile_program(program,Evidence(page=entry['page'],quote=entry['quote']),mapping)
        scene.verification=verify_visual_scene(scene)
        scene.mathematical_model['model_roles']={'construction_and_behavior':getattr(planning_client,'model',''),
            'source_and_visual_review':getattr(review_client,'model','')}
        return scene
    try:
        saved=json.loads(cache.read_text(encoding='utf-8'))
        if saved['fingerprint']==fingerprint:
            return compile_candidate(schema.model_validate(saved['program']))
    except (OSError,ValueError,KeyError):pass
    attempts=[];feedback='';previous=None;graph=None;repair_graph=False;repair_object_id=None
    material=source+'\n本组要解释的完整讲稿与问题：'+json.dumps(requests,ensure_ascii=False)
    for attempt in range(3):
        entry={'attempt':attempt+1};attempts.append(entry)
        try:
            if graph is not None and repair_graph:
                from zhijiang.math_graph_repair import apply_construction_patch
                patch_type=graph_patch_schema(graph,repair_object_id)
                patch=planning_client.generate(patch_type,
                    '只修复反馈指出的对象错误，不重新设计课程。输出MathConstructionPatch。'
                    'parameters增改控制参数，upserts按真实对象ID增改构造，remove只删除冗余或错误对象。'
                    '初态必须能计算成有限实数；函数自由变量不能直接充当点的实际坐标，需绑定已声明控制参数。'
                    '绘图范围错误时修改相应对象domain，仍覆盖来源例子与整个运动。'
                    '自由度错误时把控制参数显式用于对象expr，不能只声明未使用的参数。'
                    '保留来源主题与正确对象，不改讲稿来掩盖图形错误。'
                    +(f'本次必须替换错误对象 {repair_object_id} 的实际定义，其余正确对象保留。' if repair_object_id else '')+
                    '错误：'+feedback,
                    material+'\n当前待修对象图：'+graph.model_dump_json())
                entry['construction_patch']=patch.model_dump(mode='json')
                graph=apply_construction_patch(graph,patch);repair_graph=False;repair_object_id=None
            if graph is None:
                graph=planning_client.generate(graph_schema,CONTRACT+
                '\n这是普通课程里的具体数学或科学演示。保持本组原题的对象身份、条件、角边对应与数值；'
                '不可把示例改成所有对象的结论。使用最少的构造和连续参数，真正改变对象关系，不能只整体平移或淡入文字。'
                '一个图说明本组同一关系的多个方面；完整涵盖requests，不生成文字流程图。来源无数值时可明确标注原创示意。'
                '本阶段只返回MathConstructionDraft的对象依赖图和初始参数，不返回operations、claims或roles。'
                '点必须有两个坐标，矩形四个边界坐标；按照Schema中各工具的实际参数个数填写。'
                '声明至少一个用于实际对象的连续控制参数。不要使用自由label对象。'
                '有否定条件时用对象对照，不能靠审核小字。'+('\n工具反馈，须修正：'+feedback if feedback else ''),
                material+('\n上一被拒候选：'+previous if previous else ''))
            entry['construction']=graph.model_dump(mode='json')
            try:
                compiler=Compiler(graph)
                entry['dependency_order']=[item.id for item in compiler.dependency_order]
                ledger=computed_inventory(compiler)
                entry['computed_inventory']=ledger
                validate_initial_geometry(ledger)
            except GenerationError as exc:
                previous=graph.model_dump_json();repair_graph=True;repair_object_id=getattr(exc,'object_id',None)
                raise
            try:
                behavior_type=behavior_schema(graph,mapping)
                if source_verbatim:behavior_type=source_timing_behavior(behavior_type)
            except GenerationError:
                repair_graph=True
                raise
            behavior_material=material+'\n已编译的对象依赖图：'+graph.model_dump_json()+\
                '\n实际工具计算结果：'+json.dumps(ledger,ensure_ascii=False)
            behavior=planning_client.generate(behavior_type,
                '根据原文和已经编译的对象，规划连续演示，不修改对象、函数或来源。返回MathBehaviorDraft。'
                +('此流程已经有逐字教材配音，禁止编写新的讲解。narration只填Schema的固定占位句；'
                  '该句不进入配音或字幕，模型仅规划图形、参数、测量与核验。' if source_verbatim else '')+
                'operations为三至十步，每步changes使用Schema中已有控制参数，至少两步实际运动。'
                '口播解释真实变化、原因和限制，不写数字或等号；具体数值由测量工具计算并播报。'
                'measurements使用类型化MeasuredSum：terms为工具项数组，divisor可为分母数组，absolute可取绝对值。'
                '每项kind及对应对象字段由Schema限定；factor为字符串系数，power为整数幂。'
                'distance的first/second只能是点；area的polygon只能是多边形；value的function只能是函数。'
                '不要手写length(...)等函数字符串或预填答案。每个measurements必须含实际对象测量。'
                '测量名称简短中文，claims.left/right采用同样工具项，核查正在演示的核心关系。'
                'invariant表示指定步骤连续成立，endpoint表示指定步骤终点成立，steps从一开始。'
                '每步核心结论都有claim；差异、反例和不相等用not_equal，不用正值或恒等式替代不同关系。'
                'roles只绑定真实对象ID。保留requests内容及原题条件，不把示意当一般性证明。'
                +('\n工具反馈须修正：'+feedback if feedback else ''),behavior_material+
                ('\n上一被拒候选：'+previous if previous else ''))
            entry['behavior']=behavior.model_dump(mode='json')
            operations,claims=decode_behavior(behavior)
            program=schema.model_validate({**graph.model_dump(mode='json'),
                'operations':[op.model_dump() for op in operations],
                'claims':[c.model_dump() for c in claims],'roles':behavior.roles})
            program=attach_measurement_glyphs(program,behavior)
            entry['program']=program.model_dump(mode='json');previous=program.model_dump_json()
            try:
                scene=compile_candidate(program)
            except GenerationError as exc:
                if isinstance(exc,ConstructionLayoutError) or '实际数学关系失败' in str(exc):repair_graph=True
                raise
            images=[source_image_loader(page.page) for page in document.pages] if source_image_loader else []
            for n in range(len(scene.beats)):
                svg=folder/f'attempt-{attempt+1}-step-{n+1}.svg'
                svg.write_text(visual_summary_svg(scene,n),encoding='utf-8')
                png=svg.with_suffix('.png');render_summary_png(svg,png);images.append(png.read_bytes())
            review=review_client.generate(step_review_schema(program),
                '对照来源和实际绘制图，核对source、geometry、motion三方面。前面的图片是原页，后面依次是演示终态。'
                '检查原题身份、正负号、单位、对象数量、角边配对、条件范围、坐标尺度、以及changes是否真正解释关系。'
                '完整覆盖requests的核心内容；计算自洽不等于来源正确。若只画文字/框图或将个别示例泛化为全部，应拒绝。'
                'step_checks逐步填写口播声称的具体关系、对应claim编号及实际测量是否支持。'
                '不能只检查最初几个正确步骤；“不同”“反例”等断言也必须有数值和对象支持。'
                'source_coverage写实际覆盖内容，issues写具体问题。修订主体与审核使用同一模型，不是独立盲评。',
                material+'\n实际构造：'+program.model_dump_json()+'\n核算与操作：'+json.dumps(review_step_states(scene),ensure_ascii=False),
                **({'images':images} if images else {}))
            entry['review']=review.model_dump(mode='json')
            if not review.approved or any(not check.supported for check in review.step_checks):
                if review.repair_target=='construction':graph=None
                rejected=[f'第{check.step}步：{check.observed_relation}' for check in review.step_checks if not check.supported]
                raise VisualSceneError('；'.join(review.issues+rejected))
            cache.write_text(json.dumps({'fingerprint':fingerprint,'program':program.model_dump(mode='json'),
                'review':review.model_dump(mode='json'),'assessment':'same_model_source_review_plus_computed_relations'},
                ensure_ascii=False,indent=2),encoding='utf-8')
            return scene
        except GenerationError as exc:
            feedback=str(exc);entry['error']=feedback
            if not isinstance(exc,(VisualSceneError,ModelContractError)):
                # Rewriting the diagram cannot restore a failed model service.
                raise
        finally:
            (folder/'attempts.json').write_text(json.dumps(attempts,ensure_ascii=False,indent=2),encoding='utf-8')
    raise GenerationError('对象演示未完成：'+feedback)


def enrich_constructions(lesson,document,client,requests,*,folder,pdf_path,progress,planning_client=None,review_client=None):
    """Reuse a scene for related requests, retain every original lesson segment."""
    from zhijiang.visual_planning import visual_capabilities
    if not requests:return
    capability=visual_capabilities()
    warnings=lesson.animation_report.setdefault('warnings',[])
    if not capability['geometry_ready']:
        warnings.append({'stage':'construction','message':capability['geometry_reason']});return
    groups={}
    for item in requests:groups.setdefault(item['graphic_group'] or str(item['segment_id']),[]).append(item)
    data=Path(pdf_path).read_bytes()
    from zhijiang.source_figures import page_preview
    for n,(name,members) in enumerate(groups.items(),1):
        progress(f'构造实际对象与连续演示（{n}/{len(groups)}）：{name}',64)
        pages={p for item in members for p in (item['page']-1,item['page'],item['page']+1)}
        selected=SourceDocument(filename=document.filename,pages=[p for p in document.pages if p.page in pages])
        try:
            from zhijiang.construction_intent import read_construction_intent
            # The literal-source planner already selects a typed visual tool.
            # Keep a single tool decision, then apply all actual construction,
            # geometry, calculation and source-review gates below.
            explicit_source_tool=all(item.get('visual_medium')=='computed' for item in members) and bool(
                lesson.segments[members[0]['segment_id']-1].source_spans)
            intent=None if explicit_source_tool else read_construction_intent(
                planning_client or client,selected,members,Path(folder)/f'group-{n:03d}')
            if intent and intent.relation_kind in {'symbolic_sequence','concept_comparison'}:
                lesson.animation_report.setdefault('construction_routing',[]).append({
                    'segment_ids':[item['segment_id'] for item in members],
                    'relation_kind':intent.relation_kind,'observation':intent.observation,
                    'renderer':'teaching_diagram'})
                continue
            scene=build_scene(client,selected,members,folder=Path(folder)/f'group-{n:03d}',
                              source_image_loader=lambda p:page_preview(data,p),
                              planning_client=planning_client,review_client=review_client,
                              source_verbatim=bool(lesson.segments[members[0]['segment_id']-1].source_spans))
        except GenerationError as exc:
            warnings.append({'stage':'construction','segment_id':members[0]['segment_id'],'message':str(exc)});continue
        segment=lesson.segments[members[0]['segment_id']-1]
        lesson.animation_report.setdefault('construction_fallbacks',{})[str(members[0]['segment_id'])]={
            'narration':segment.narration,'evidence':segment.evidence.model_dump()}
        segment.visual_scene=scene;segment.evidence=scene.evidence
        for page in lesson.deck_plan.get('pages',[]):
            if page['segment_id']==members[0]['segment_id']:
                page.update(layout='wide',include_animation=True,visual_groups=[])
        # This is the actual animation script, including computed values.
        segment.narration=' '.join(beat.narration for beat in scene.beats)
        lesson.animation_report.setdefault('construction_groups',[]).append({
            'name':name,'segment_ids':[m['segment_id'] for m in members],
            'rendered_segment_id':members[0]['segment_id'],'computed_relations':len(scene.mathematical_model['claims'])})
