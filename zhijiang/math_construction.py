"""Generic mathematical constructions compiled into the shared scene graph.

Operators implement mathematics, not textbook/topic storyboards. The model
chooses a source-backed construction graph and teaching operations. Coordinates,
coupling, projections, rigid motion, inverses and finite series are computed.
No model Python, SVG, TeX, imports or arbitrary SymPy strings are executed.
"""
from __future__ import annotations

import ast
import hashlib
import json
import math
import re
from typing import Literal, Annotated

from pydantic import BaseModel, Field, model_validator
import sympy as sp

from zhijiang.models import SceneObject, VisualBeat, VisualScenePlan, SceneCalculation
from zhijiang.visual_planning import expression_tree, evaluate, VisualSceneError


Op=Literal['point','segment','ray','line','polygon','rectangle','circle','function','parametric',
           'point_on_function','point_on_curve','polar_point','midpoint','projection','intersection','square',
           'angle_arc','partition','transform','align','locus','inverse','series','label']


class ConstructionLayoutError(VisualSceneError):
    """The actual drawing requires a graph/domain edit, not rewritten speech."""


class ConstructionObjectError(VisualSceneError):
    """A computed error tied to one real object, for targeted data repair."""
    def __init__(self,object_id,message):
        super().__init__(message)
        self.object_id=object_id


class StrictMathModel(BaseModel):
    model_config={'extra':'forbid'}


class Construction(StrictMathModel):
    id: str = Field(pattern=r'^[a-z][a-z0-9_]{0,23}$')
    op: Op
    refs: list[Annotated[str,Field(pattern=r'^[a-z][a-z0-9_]*$')]] = Field(default_factory=list,max_length=16)
    expr: list[str] = Field(default_factory=list,max_length=8)
    domain: list[float] = Field(default_factory=lambda:[-2,2],min_length=2,max_length=2)
    color: str = Field(default='#78BAFF',pattern=r'^#[0-9a-fA-F]{6}$')
    label: str = Field(default='',max_length=40)
    visible: bool = True
    reference: bool = False


class MathematicalClaim(StrictMathModel):
    source_id: int
    lhs: str = Field(min_length=1,max_length=300)
    rhs: str = Field(default='0',max_length=300)
    relation: Literal['equal','not_equal','positive','nonnegative','at_most'] = 'equal'
    phase: Literal['invariant','endpoint'] = 'invariant'
    steps: list[Annotated[int,Field(ge=1,le=10)]] = Field(default_factory=list,max_length=10)


class MathOperation(StrictMathModel):
    narration: str = Field(min_length=8,max_length=180)
    changes: dict[str,float] = Field(default_factory=dict)
    show: list[str] = Field(default_factory=list,max_length=40)
    hide: list[str] = Field(default_factory=list,max_length=40)
    measurements: dict[Annotated[str,Field(min_length=1,max_length=24,pattern=r'^[^=＝{}]+$')],str] = Field(default_factory=dict,max_length=4,
        description='Display labels name the measured object (numbered point names are allowed); values must be object measurement expressions, never numeric answers.')


class MathProgramDraft(StrictMathModel):
    title: str = Field(min_length=2,max_length=40)
    objective: str = Field(min_length=12,max_length=120)
    source_id: int
    parameters: dict[str,float]
    constructions: list[Construction] = Field(min_length=2,max_length=32)
    operations: list[MathOperation] = Field(min_length=3,max_length=10)
    claims: list[MathematicalClaim] = Field(min_length=1,max_length=12)
    teaching_example: bool = True
    simplifications: list[str] = Field(default_factory=list,max_length=8)
    roles: dict[str,str] = Field(default_factory=dict,max_length=40)


class MathReviewAspect(StrictMathModel):
    aspect: Literal['source','geometry','motion']
    observation: str = Field(min_length=12,max_length=600)
    supported: bool


class MathProgramReview(StrictMathModel):
    checks: list[MathReviewAspect] = Field(min_length=3,max_length=3)
    issues: list[str] = Field(default_factory=list,max_length=12)
    source_coverage: list[str] = Field(min_length=1,max_length=12)
    repair_target: Literal['construction','behavior'] = 'construction'
    approved: bool

    @model_validator(mode='after')
    def complete_review(self):
        if {c.aspect for c in self.checks}!={'source','geometry','motion'}:
            raise ValueError('必须逐项覆盖来源、几何与连续操作。')
        if self.approved!=(all(c.supported for c in self.checks) and not self.issues):
            raise ValueError('整体批准必须与逐项检查及问题列表一致。')
        return self


class MathConstructionDraft(StrictMathModel):
    """Source-backed object graph, before teaching operations are planned."""
    title: str = Field(min_length=2,max_length=40)
    objective: str = Field(min_length=12,max_length=120)
    source_id: int
    parameters: dict[str,float]
    constructions: list['MathConstructionObject'] = Field(min_length=2,max_length=32)
    teaching_example: bool = True
    simplifications: list[str] = Field(default_factory=list,max_length=8)

    @property
    def operations(self):
        # Inventory compilation only; this draft is never rendered or accepted.
        return []


class MathConstructionObject(Construction):
    # Floating labels caused unattached captions to appear on the wrong curve.
    # Point names are anchored by the compiler; curves display actual formulas.
    op: Literal['point','segment','ray','line','polygon','rectangle','circle','function','parametric',
        'point_on_function','point_on_curve','polar_point','midpoint','projection','intersection','square','angle_arc','partition','transform','align','locus','inverse','series']

    @model_validator(mode='after')
    def concrete_coordinates(self):
        if self.op not in {'function','parametric','locus'}:
            for expression in self.expr:
                names={node.id for node in ast.walk(expression_tree(expression)) if isinstance(node,ast.Name)}
                if names&{'x','y'}:
                    raise ValueError('实际对象位置不能使用函数自由变量x/y；请声明连续控制参数，并绑定到该对象的expr。')
        return self


MathConstructionDraft.model_rebuild()


class MathBehaviorDraft(StrictMathModel):
    operations: list[MathOperation] = Field(min_length=3,max_length=10)
    claims: list[MathematicalClaim] = Field(min_length=1,max_length=12)
    roles: dict[str,str] = Field(default_factory=dict,max_length=32)


FUNCTIONS={'sin':sp.sin,'cos':sp.cos,'tan':sp.tan,'asin':sp.asin,'acos':sp.acos,
           'atan':sp.atan,'atan2':sp.atan2,'sqrt':sp.sqrt,'exp':sp.exp,'log':sp.log,
           'abs':sp.Abs,'min':sp.Min,'max':sp.Max}
MEASURES={'distance','line_distance','length','area','angle','signed_angle','angle_at','xcoord','ycoord','value','derivative'}


def discrete_controls(program):
    locked=set()
    for item in program.constructions:
        indices={'series':[1],'partition':[0,1],'square':[0],'intersection':[0]}.get(item.op,[])
        for index in indices:
            if index<len(item.expr):
                locked.update(node.id for node in ast.walk(expression_tree(item.expr[index]))
                    if isinstance(node,ast.Name) and node.id in program.parameters)
    return locked


def readable_color(color):
    """Preserve color correspondence while raising contrast on the dark plot."""
    rgb=[int(color[i:i+2],16)/255 for i in [1,3,5]]
    def luminance(values):
        linear=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in values]
        return sum(v*w for v,w in zip(linear,[.2126,.7152,.0722]))
    background=luminance([16/255,40/255,58/255])
    for factor in [i/100 for i in range(101)]:
        adjusted=[v+(1-v)*factor for v in rgb]
        if (luminance(adjusted)+.05)/(background+.05)>=4.5:
            return '#'+''.join(f'{round(v*255):02X}' for v in adjusted)
    return '#FFFFFF'


def scalar(text,symbols,measure=None):
    if not isinstance(text,str) or len(text)>1000:raise VisualSceneError('数学表达式过长。')
    def walk(n):
        if isinstance(n,ast.Constant) and type(n.value) in {int,float}:
            return sp.Rational(str(n.value))
        if isinstance(n,ast.Name):
            if n.id in symbols:
                value=symbols[n.id]
                return sp.Rational(str(value)) if type(value) in {int,float} else value
            if n.id in {'pi','π','e'}:return sp.pi if n.id in {'pi','π'} else sp.E
            raise VisualSceneError('未定义的数学符号：'+n.id)
        if isinstance(n,ast.UnaryOp) and isinstance(n.op,(ast.UAdd,ast.USub)):
            v=walk(n.operand);return -v if isinstance(n.op,ast.USub) else v
        if isinstance(n,ast.BinOp):
            a,b=walk(n.left),walk(n.right)
            if isinstance(n.op,ast.Add):return a+b
            if isinstance(n.op,ast.Sub):return a-b
            if isinstance(n.op,ast.Mult):return a*b
            if isinstance(n.op,ast.Div):return a/b
            if isinstance(n.op,ast.Pow):
                if b.is_number and abs(float(b))>12:raise VisualSceneError('数学幂次超出范围。')
                return a**b
        if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and not n.keywords:
            if n.func.id in MEASURES and measure:return measure(n.func.id,n.args,walk)
            if n.func.id in FUNCTIONS and 1<=len(n.args)<=4:
                return FUNCTIONS[n.func.id](*[walk(a) for a in n.args])
        raise VisualSceneError('数学构造只允许受限表达式与已注册测量，禁止代码。')
    return walk(expression_tree(text))


def numeric_string(expr):
    text=sp.sstr(expr).replace('Abs(','abs(').replace('Min(','min(').replace('Max(','max(')
    text=re.sub(r'\bE\b','e',text)
    if len(text)>1000:raise VisualSceneError('数学构造展开过长，请拆分对象。')
    return text


class Compiler:
    def __init__(self,program):
        from zhijiang.math_graph_repair import dependency_order
        self.program=program;self.items={};self.shapes=[];self.derived={};self.groups={};self.proofs=[];self.label_checks=[];self.arc_checks=[];self.alignment_checks=[];self.intersection_checks=[];self.curve_point_checks=[];self.display_expressions={};self.render_symbols={};self.actor_of={}
        if set(program.parameters)&{item.id for item in program.constructions}:
            raise VisualSceneError('控制参数与数学对象ID不能同名。')
        locked=discrete_controls(program)
        inventory_only=isinstance(program,MathConstructionDraft)
        dynamic=(set(program.parameters)-locked if inventory_only else
                 {k for step in program.operations for k in step.changes})
        if dynamic & locked:
            raise VisualSceneError('阶数、分割数和朝向不能作为连续控制参数；增加项数请使用各项权重：'+str(sorted(dynamic & locked)))
        self.symbols={}
        for name,value in program.parameters.items():
            if not re.fullmatch(r'[a-z][a-z0-9_]{0,23}',name) or name in {'x','y','pi','e'}:
                raise VisualSceneError('数学控制参数名无效：'+name)
            if not math.isfinite(value) or abs(value)>1e4:raise VisualSceneError('数学参数必须有限。')
            values=[value]+[step.changes[name] for step in program.operations if name in step.changes]
            self.symbols[name]=(sp.Symbol(name,positive=True) if min(values)>0 and not inventory_only else sp.Symbol(name,real=True)) if name in dynamic else sp.Rational(str(value))
        if dynamic-set(program.parameters):raise VisualSceneError('数学步骤改变未声明参数：'+str(sorted(dynamic-set(program.parameters))))
        self.x=sp.Symbol('x',real=True);self.y=sp.Symbol('y',real=True)
        self.symbols.update(x=self.x,y=self.y)
        self.dependency_order=dependency_order(program.constructions,program.parameters)
        for item in self.dependency_order:
            try:self.add(item)
            except VisualSceneError as exc:
                raise ConstructionObjectError(item.id,str(exc)) from exc

    def parse(self,text):return scalar(text,self.symbols,self.measure)

    def render_expression(self,expr):
        """Lower computed expressions into a bounded, acyclic numeric graph.

        Symbolic objects remain intact for proofs. Only renderer expressions
        reuse intermediate values; existing AST and field limits stay enforced.
        """
        def fits(value):
            raw=sp.sstr(value)
            return len(raw)<=220 and len(list(ast.walk(ast.parse(raw,mode='eval'))))<=80
        if fits(expr):return numeric_string(expr)
        def lower(value):
            if value in self.render_symbols:return sp.Symbol(self.render_symbols[value])
            if value.is_Atom:return value
            rebuilt=value.func(*(lower(arg) for arg in value.args))
            if not fits(rebuilt):raise VisualSceneError('数学构造仍过于复杂，请分解函数或计算对象。')
            if self.x in value.free_symbols:return rebuilt
            if len(self.render_symbols)>=512:raise VisualSceneError('数学派生计算超过五百一十二项。')
            name=f'zbc_aux_{len(self.render_symbols)}'
            while name in self.symbols or name in self.derived:name+='_a'
            self.derived[name]=numeric_string(rebuilt);self.render_symbols[value]=name
            return sp.Symbol(name)
        return numeric_string(lower(expr))

    def point(self,name):
        item=self.items.get(name,{})
        if item.get('type')!='point':raise VisualSceneError('数学构造需要先定义点：'+name)
        return sp.Matrix(item['p'])

    def line(self,name):
        item=self.items.get(name,{})
        if item.get('type')!='line':raise VisualSceneError('数学构造需要先定义线段：'+name)
        return sp.Matrix(item['a']),sp.Matrix(item['b'])

    def vertex_vectors(self,vertex,first,second):
        vectors=[]
        for name in (first,second):
            a,b=self.line(name);extent=self.items[name].get('extent','segment')
            if extent=='line':raise VisualSceneError('顶点内角需要有限线段或有起点的射线：'+name)
            at_start=all(sp.simplify(q)==0 for q in vertex-a)
            at_end=all(sp.simplify(q)==0 for q in vertex-b)
            if extent=='ray' and not at_start:raise VisualSceneError('角的顶点必须是射线起点：'+name)
            if not at_start and not at_end:raise VisualSceneError('角的顶点不是线段端点：'+name)
            vector=b-a if at_start else a-b
            if all(sp.simplify(q)==0 for q in vector):raise VisualSceneError('角的边退化：'+name)
            vectors.append(vector)
        return vectors

    def measure(self,name,args,walk):
        def ref(n):
            if not isinstance(n,ast.Name) or n.id not in self.items:raise VisualSceneError('测量须引用已有数学对象。')
            return n.id
        ids=[ref(n) for n in args[:({'value':1,'derivative':1}.get(name,len(args)))]]
        if name=='distance' and len(ids)==2:
            delta=self.point(ids[0])-self.point(ids[1])
            return sp.sqrt(sp.simplify(delta.dot(delta)))
        if name=='line_distance' and len(ids)==2:
            p=self.point(ids[0]);a,b=self.line(ids[1]);v=b-a
            return sp.Abs(sp.simplify(v[0]*(p[1]-a[1])-v[1]*(p[0]-a[0])))/sp.sqrt(sp.simplify(v.dot(v)))
        if name=='length' and len(ids)==1:
            if self.items[ids[0]].get('extent','segment')!='segment':raise VisualSceneError('无限直线和射线不能测量有限长度，请引用线段。')
            a,b=self.line(ids[0]);return sp.sqrt(sp.simplify((b-a).dot(b-a)))
        if name=='area' and len(ids)==1:
            shape=self.items[ids[0]]
            if shape['type']=='group':return sum(self._area(self.items[i]['vertices']) for i in shape['members'])
            if shape['type']=='polygon':return self._area(shape['vertices'])
            raise VisualSceneError('面积须引用多边形或分割组。')
        if name in {'angle','signed_angle'} and len(ids)==2:
            a,b=self.line(ids[0]);c,d=self.line(ids[1]);u,v=b-a,d-c
            dot=sp.simplify(u.dot(v))
            if name=='signed_angle':return sp.atan2(sp.simplify(u[0]*v[1]-u[1]*v[0]),dot)
            return sp.acos(dot/sp.sqrt(sp.simplify(u.dot(u))*sp.simplify(v.dot(v))))
        if name=='angle_at' and len(ids)==3:
            u,v=self.vertex_vectors(self.point(ids[0]),ids[1],ids[2])
            return sp.acos(sp.simplify(u.dot(v))/sp.sqrt(sp.simplify(u.dot(u))*sp.simplify(v.dot(v))))
        if name in {'xcoord','ycoord'} and len(ids)==1:return self.point(ids[0])[0 if name=='xcoord' else 1]
        if name in {'value','derivative'}:
            curve=self.items[ids[0]]
            if curve['type']!='function':raise VisualSceneError('函数测量须引用函数对象。')
            expr=curve['expr'];at=walk(args[1])
            if name=='derivative':
                order=walk(args[2]);
                if not order.is_Integer or not 0<=int(order)<=8:raise VisualSceneError('导数阶数须为零至八的整数。')
                expr=sp.diff(expr,self.x,int(order))
            return expr.subs(self.x,at)
        raise VisualSceneError('数学测量参数不匹配：'+name)

    @staticmethod
    def _area(vertices):
        # Reduce the actual vertex shoelace expression before serialization.
        # Rigid motion otherwise produces hundreds of redundant trig terms.
        signed=sp.simplify(sum(a[0]*b[1]-a[1]*b[0] for a,b in zip(vertices,vertices[1:]+vertices[:1])))
        return sp.Abs(signed)/2

    def output(self,item,kind,**data):
        if item.id in self.items:raise VisualSceneError('数学构造ID重复：'+item.id)
        self.actor_of[item.id]=self.actor_of.get(item.refs[0],item.refs[0]) if item.op in {'transform','align'} else item.id
        self.shapes.append(SceneObject(id=item.id,kind=kind,color=readable_color(item.color),visible=item.visible,reference=item.reference,**data))

    def bind_point(self,item,p):
        coords=[]
        for i,suffix in enumerate(['x','y']):
            name=item.id+'_'+suffix
            if name in self.symbols:raise VisualSceneError('数学符号命名冲突：'+name)
            value=sp.simplify(p[i]);self.derived[name]=self.render_expression(value);self.symbols[name]=value;coords.append(name)
        self.output(item,'dot',position=coords)
        self.items[item.id]={'type':'point','p':list(p)}

    def add(self,item):
        refs=item.refs;op=item.op
        requirements={
            'point':(0,2),'segment':(2,0),'ray':(2,0),'line':(2,0),'rectangle':(0,4),'circle':(1,1),'function':(0,1),
            'parametric':(0,2),'point_on_function':(1,1),'point_on_curve':(1,1),'midpoint':(2,0),'projection':(2,0),
            'intersection':(2,None),'angle_arc':(2,None),'partition':(1,2),'locus':(0,2),'inverse':(1,0),'label':(0,2)}
        if op in requirements:
            ref_count,expr_count=requirements[op]
            if len(refs)!=ref_count or expr_count is not None and len(item.expr)!=expr_count:
                raise VisualSceneError(f'构造{item.id}（{op}）需要refs数量{ref_count}、expr数量{expr_count}。')
        missing=set(refs)-set(self.items)
        if missing:raise VisualSceneError('数学构造引用不存在或尚未定义的对象：'+str(sorted(missing)))
        v=[] if op=='locus' else [self.parse(e) for e in item.expr]
        if item.id in self.items:raise VisualSceneError('数学构造ID重复。')
        if op=='point':self.bind_point(item,sp.Matrix(v[:2]));return
        if op=='polar_point':
            center=self.point(refs[0]) if refs else sp.zeros(2,1)
            self.bind_point(item,center+sp.Matrix([v[0]*sp.cos(v[1]),v[0]*sp.sin(v[1])]))
            self.proofs.append({'construction':item.id,'law':'Cartesian coordinates are computed from radius and angle'});return
        if op=='midpoint':self.bind_point(item,(self.point(refs[0])+self.point(refs[1]))/2);return
        if op=='projection':
            p=self.point(refs[0]);a,b=self.line(refs[1]);direction=b-a
            self.bind_point(item,a+direction*((p-a).dot(direction)/direction.dot(direction)));return
        if op=='intersection':
            parents=[self.items.get(ref,{}) for ref in refs]
            kinds=[parent.get('type') for parent in parents]
            if kinds==['line','line']:
                if v:raise VisualSceneError('两线交点的expr须为空。')
                a,b=self.line(refs[0]);c,d=self.line(refs[1]);u,w=b-a,d-c
                cross=lambda first,second:sp.simplify(first[0]*second[1]-first[1]*second[0])
                det=cross(u,w)
                if det==0:raise VisualSceneError('两线平行或重合，没有唯一交点。')
                first=cross(c-a,w)/det;second=cross(c-a,u)/det
                p=a+first*u
                check={'kind':'lines','object':item.id,'refs':refs,
                    'determinant':self.render_expression(det),
                    'parameters':[self.render_expression(first),self.render_expression(second)],
                    'extents':[parent.get('extent','segment') for parent in parents]}
            elif kinds==['circle','circle']:
                if len(v)>1 or v and v[0] not in (sp.Integer(-1),sp.Integer(1)):
                    raise VisualSceneError('两圆交点expr可为空或固定分支正一/负一。')
                a,b=map(lambda parent:sp.Matrix(parent['center']),parents)
                r,q=[parent['radius'] for parent in parents];direction=b-a
                d2=sp.simplify(direction.dot(direction))
                if d2==0:raise VisualSceneError('同心圆没有唯一的选定交点。')
                distance=sp.sqrt(d2);along=sp.simplify((r*r-q*q+d2)/(2*distance))
                height2=sp.simplify(r*r-along*along)
                if height2.is_negative:raise VisualSceneError('两圆没有实交点。')
                normal=sp.Matrix([-direction[1],direction[0]])
                p=a+direction*along/distance+(v[0] if v else 1)*normal*sp.sqrt(height2)/distance
                check={'kind':'circles','object':item.id,'refs':refs,
                    'center_distance_squared':self.render_expression(d2),
                    'height_squared':self.render_expression(height2)}
            else:
                raise VisualSceneError('交点支持两条线段/射线/直线或两个圆，不能混用。')
            self.bind_point(item,sp.Matrix(p));self.intersection_checks.append(check)
            self.proofs.append({'construction':item.id,'law':'Selected intersection solved from referenced line incidence or circle distances','parents':refs})
            return
        if op=='point_on_function':
            curve=self.items[refs[0]]
            if curve['type']!='function':raise ConstructionObjectError(item.id,
                f"point_on_function必须引用真实函数；{refs[0]}的实际类型为{curve['type']}。参数曲线应使用point_on_curve。")
            shape=next(shape for shape in self.shapes if shape.id==refs[0])
            self.curve_point_checks.append({'object':item.id,'curve':refs[0],'parameter':self.render_expression(v[0]),'domain':shape.domain})
            item=item.model_copy(update={'color':next(s.color for s in self.shapes if s.id==refs[0])})
            self.bind_point(item,sp.Matrix([v[0],curve['expr'].subs(self.x,v[0])]))
            self.proofs.append({'construction':item.id,'law':'Point coordinates derive from the referenced function'});return
        if op=='point_on_curve':
            curve=self.items[refs[0]]
            if curve['type']!='parametric':raise ConstructionObjectError(item.id,
                f"point_on_curve必须引用参数曲线或角弧；{refs[0]}的实际类型为{curve['type']}。函数图像上的点应使用point_on_function。")
            shape=next(shape for shape in self.shapes if shape.id==refs[0])
            self.curve_point_checks.append({'object':item.id,'curve':refs[0],'parameter':self.render_expression(v[0]),'domain':shape.domain})
            self.bind_point(item,sp.Matrix([e.subs(self.x,v[0]) for e in curve['expr']]))
            self.proofs.append({'construction':item.id,'law':'Point derives from the actual parametric curve at its parameter'});return
        if op in {'segment','ray','line'}:
            a,b=self.point(refs[0]),self.point(refs[1])
            direction=b-a
            shown_a=a-direction/4 if op=='line' else a
            shown_b=b+direction/4 if op!='segment' else b
            self.output(item,'line' if op=='segment' else 'arrow',double_tip=op=='line',line_extent=op,
                start=[self.render_expression(e) for e in shown_a],end=[self.render_expression(e) for e in shown_b])
            self.items[item.id]={'type':'line','a':list(a),'b':list(b),'extent':op};return
        if op in {'polygon','rectangle','square'}:
            if op=='polygon':vertices=[list(self.point(r)) for r in refs]
            elif op=='rectangle':vertices=[[v[0],v[1]],[v[2],v[1]],[v[2],v[3]],[v[0],v[3]]]
            else:
                orientation=v[0] if v else 1
                if orientation not in (sp.Integer(-1),sp.Integer(1)):raise VisualSceneError('正方形方向只能为正一或负一。')
                a,b=self.line(refs[0]);d=b-a;normal=sp.Matrix([-d[1],d[0]])*orientation
                vertices=[list(a),list(b),list(b+normal),list(a+normal)]
                self.proofs.append({'construction':item.id,'law':'Square area equals its segment length squared'})
            self.output(item,'polygon',vertices=[[self.render_expression(e) for e in p] for p in vertices])
            self.items[item.id]={'type':'polygon','vertices':vertices};return
        if op=='circle':
            p=self.point(refs[0]);self.output(item,'circle',position=[self.render_expression(e) for e in p],radius=self.render_expression(v[0]))
            self.items[item.id]={'type':'circle','center':list(p),'radius':v[0]};return
        if op=='function':
            self.output(item,'curve',expression=self.render_expression(v[0]),domain=item.domain)
            self.items[item.id]={'type':'function','expr':v[0]};return
        if op=='inverse':
            f=self.items[refs[0]]['expr'];solutions=sp.solve(f-self.y,self.x)
            if len(solutions)!=1:raise VisualSceneError('逆函数须有唯一实分支，请明确原函数定义域。')
            expr=sp.simplify(solutions[0].subs(self.y,self.x))
            self.output(item,'curve',expression=self.render_expression(expr),domain=item.domain)
            self.items[item.id]={'type':'function','expr':expr}
            self.proofs.append({'construction':item.id,'law':'Inverse derived by solving the source function equation'});return
        if op=='parametric':
            if not any(self.x in coordinate.free_symbols for coordinate in v[:2]):
                raise VisualSceneError('参数曲线坐标没有曲线自由变量x，实际是单个控制点：'+item.id+'。请改用point expr=[横坐标,纵坐标]，保留真实依赖；不能把控制参数运动误称为曲线。')
            self.output(item,'parametric_curve',parametric_expression=[self.render_expression(e) for e in v[:2]],domain=item.domain)
            self.items[item.id]={'type':'parametric','expr':v[:2]};return
        if op=='angle_arc':
            if len(v) not in {0,1,2}:raise VisualSceneError('角弧expr为半径，可选第二项为实际有向扫角。')
            a,b=self.line(refs[0]);c,d=self.line(refs[1])
            candidates=[]
            for p in (a,b):
                if any(all(sp.simplify(q)==0 for q in p-other) for other in (c,d)):
                    if not any(all(sp.simplify(q)==0 for q in p-old) for old in candidates):candidates.append(p)
            if len(candidates)!=1:raise VisualSceneError('角的两边必须有唯一共端点：'+str(refs))
            a=candidates[0];u,w=self.vertex_vectors(a,*refs)
            start=sp.atan2(sp.simplify(u[1]),sp.simplify(u[0]))
            sweep=v[1] if len(v)==2 else sp.atan2(sp.simplify(u[0]*w[1]-u[1]*w[0]),sp.simplify(u.dot(w)))
            radius=v[0] if v else sp.Rational(1,2)
            expr=[a[0]+radius*sp.cos(start+self.x*sweep),a[1]+radius*sp.sin(start+self.x*sweep)]
            self.output(item,'parametric_curve',parametric_expression=[self.render_expression(e) for e in expr],domain=[0,1])
            self.arc_checks.append({'object':item.id,'edges':refs,
                'center':[self.render_expression(e) for e in a],'radius':self.render_expression(radius)})
            self.items[item.id]={'type':'parametric','expr':expr};return
        if op=='partition':
            parent=self.items[refs[0]];ps=parent['vertices']
            if len(ps)!=4 or any(sp.simplify(ps[0][i]+ps[2][i]-ps[1][i]-ps[3][i])!=0 for i in (0,1)):
                raise VisualSceneError('等面积网格分割需要平行四边形。')
            if len(v)!=2 or any(not e.is_Integer for e in v):raise VisualSceneError('网格行列数需要整数。')
            rows,columns=[int(e) for e in v]
            if rows<1 or columns<1 or rows*columns>24:raise VisualSceneError('分割网格须为一至二十四格。')
            a,b,d=sp.Matrix(ps[0]),sp.Matrix(ps[1]),sp.Matrix(ps[3]);members=[]
            for row in range(rows):
                for column in range(columns):
                    corner=a+(b-a)*sp.Rational(column,columns)+(d-a)*sp.Rational(row,rows)
                    vertices=[list(corner),list(corner+(b-a)/columns),list(corner+(b-a)/columns+(d-a)/rows),list(corner+(d-a)/rows)]
                    child=item.model_copy(update={'id':f'{item.id}_{row}_{column}'})
                    self.output(child,'polygon',vertices=[[self.render_expression(e) for e in p] for p in vertices]);self.items[child.id]={'type':'polygon','vertices':vertices};members.append(child.id)
            self.items[item.id]={'type':'group','members':members};self.groups[item.id]=members
            self.proofs.append({'construction':item.id,'law':'Equal-cell partition conserves the parent area'});return
        if op in {'transform','align'}:
            if op=='align':
                if len(refs)!=5 or len(v)!=1:raise VisualSceneError('几何对齐需要refs=[对象,源锚点,源方向点,目标锚点,目标方向点]、expr=[进度]。')
                pivot=self.point(refs[1]);source_direction=self.point(refs[2])-pivot
                target=self.point(refs[3]);target_direction=self.point(refs[4])-target
                for vector in (source_direction,target_direction):
                    if all(sp.simplify(q)==0 for q in vector):raise VisualSceneError('几何对齐方向不能退化为一点。')
                angle=v[0]*sp.atan2(sp.simplify(source_direction[0]*target_direction[1]-source_direction[1]*target_direction[0]),sp.simplify(source_direction.dot(target_direction)))
                dx,dy=list(v[0]*(target-pivot))
                self.alignment_checks.append({'object':item.id,'progress':self.render_expression(v[0]),
                    'directions':[[self.render_expression(q) for q in vector] for vector in (source_direction,target_direction)]})
                self.proofs.append({'construction':item.id,'law':'Rigid alignment computed from ordered source and target landmarks; no scale or guessed rotation','landmarks':refs[1:]})
            else:
                if len(refs) not in {1,2} or len(v)!=3:raise VisualSceneError('刚体变换需要refs=[原对象,可选中心点]、expr=[旋转角,横移,纵移]。')
                angle,dx,dy=v[:3];pivot=self.point(refs[1]) if len(refs)>1 else sp.zeros(2,1)
            # Rigid rotation and translation; no independent transformed vertices.
            rotation=sp.Matrix([[sp.cos(angle),-sp.sin(angle)],[sp.sin(angle),sp.cos(angle)]])
            parent=self.items[refs[0]]
            def move(p):return list(rotation*(sp.Matrix(p)-pivot)+pivot+sp.Matrix([dx,dy]))
            if parent['type']=='point':self.bind_point(item,sp.Matrix(move(parent['p'])))
            elif parent['type']=='line':
                a,b=sp.Matrix(move(parent['a'])),sp.Matrix(move(parent['b']));d=b-a
                extent=parent.get('extent','segment')
                shown_a=a-d/4 if extent=='line' else a
                shown_b=b+d/4 if extent!='segment' else b
                self.output(item,'line' if extent=='segment' else 'arrow',double_tip=extent=='line',line_extent=extent,
                    start=[self.render_expression(e) for e in shown_a],end=[self.render_expression(e) for e in shown_b])
                self.items[item.id]={'type':'line','a':list(a),'b':list(b),'extent':extent}
            elif parent['type']=='polygon':
                vertices=[move(p) for p in parent['vertices']];self.output(item,'polygon',vertices=[[self.render_expression(e) for e in p] for p in vertices]);self.items[item.id]={'type':'polygon','vertices':vertices}
            elif parent['type']=='circle':
                center=move(parent['center']);radius=parent['radius']
                self.output(item,'circle',position=[self.render_expression(e) for e in center],radius=self.render_expression(radius))
                self.items[item.id]={'type':'circle','center':center,'radius':radius}
            elif parent['type'] in {'function','parametric'}:
                original=[self.x,parent['expr']] if parent['type']=='function' else parent['expr']
                expression=move(original)
                source_shape=next(shape for shape in self.shapes if shape.id==refs[0])
                self.output(item,'parametric_curve',parametric_expression=[self.render_expression(e) for e in expression],domain=source_shape.domain)
                self.items[item.id]={'type':'parametric','expr':expression}
            else:raise VisualSceneError('刚体变换需要点、线、多边形、圆或曲线。')
            self.proofs.append({'construction':item.id,'law':'Rotation/translation preserve lengths and areas'});return
        if op=='locus':
            # A virtual point p=(x,y) allows generic measured implicit equations.
            if 'p' in self.items:raise VisualSceneError('轨迹的虚拟点p不能与已有点重名。')
            self.items['p']={'type':'point','p':[self.x,self.y]}
            left,right=[self.parse(e) for e in item.expr[:2]]
            del self.items['p']
            equation=sp.simplify(left**2-right**2) if left.is_nonnegative and right.is_nonnegative else left-right
            solutions=sp.solve(equation,self.y)
            if len(solutions)!=1:raise VisualSceneError('轨迹不具有唯一y分支；使用参数曲线或分支明确的函数。')
            expr=sp.simplify(solutions[0])
            self.output(item,'curve',expression=self.render_expression(expr),domain=item.domain)
            self.items[item.id]={'type':'function','expr':expr}
            self.proofs.append({'construction':item.id,'law':'Function derived from the measured implicit relation'});return
        if op=='series':
            function=self.items[refs[0]]['expr'];center=v[0];order=int(v[1])
            if not 0<=order<=6 or v[1]!=order:raise VisualSceneError('有限级数阶数须为零至六的整数。')
            weights=item.expr[2:] or ['1']*order
            if len(weights)!=order:raise VisualSceneError('有限级数每个非常数项需要一个已声明权重。')
            expr=function.subs(self.x,center)
            coefficients=[]
            display_terms=[numeric_string(expr)] if expr!=0 else []
            for degree in range(1,order+1):
                coefficient=sp.diff(function,self.x,degree).subs(self.x,center)/sp.factorial(degree)
                if not coefficient.is_finite:raise VisualSceneError('展开点处导数不有限。')
                expr+=coefficient*(self.x-center)**degree*self.parse(weights[degree-1]);coefficients.append(str(coefficient))
                if coefficient!=0:
                    display_terms.append(f'({numeric_string(coefficient)})*({numeric_string(self.parse(weights[degree-1]))})*(x-({numeric_string(center)}))**{degree}')
            self.output(item,'curve',expression=self.render_expression(sp.expand(expr)),domain=item.domain)
            self.items[item.id]={'type':'function','expr':expr}
            self.display_expressions[item.id]='+'.join(display_terms) or '0'
            self.proofs.append({'construction':item.id,'law':'Coefficients computed from source function derivatives','center':str(center),'coefficients':coefficients});return
        if op=='label':
            if re.search(r'[=＝{}]',item.label):
                match=re.fullmatch(r'([xy])\s*=\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))',item.label)
                if not match:raise VisualSceneError('公式标签需由可信计算层显示；坐标标签仅允许x或y等于明确数值，并核验实际锚点。')
                self.label_checks.append({'object':item.id,'axis':0 if match[1]=='x' else 1,'value':float(match[2])})
            self.output(item,'label',position=[self.render_expression(e) for e in v[:2]],text=item.label)
            self.items[item.id]={'type':'label'};return
        raise VisualSceneError('未知数学构造：'+op)

    def expand_ids(self,ids):
        return [child for id in ids for child in self.groups.get(id,[id])]

    def expression(self,text):
        tree=expression_tree(text)
        if not any(isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id in MEASURES for n in ast.walk(tree)):
            raise VisualSceneError('measurements必须测量实际对象，不准写预先猜测的结果：'+text)
        return self.render_expression(sp.simplify(self.parse(text)))


def measurement_label(label,expression):
    root=expression_tree(expression)
    def angular(node):
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Name):
            return node.func.id in {'angle','signed_angle','angle_at'} or (node.func.id=='abs' and len(node.args)==1 and angular(node.args[0]))
        if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)):return angular(node.operand)
        if isinstance(node,ast.BinOp) and isinstance(node.op,(ast.Add,ast.Sub)):return angular(node.left) and angular(node.right)
        if isinstance(node,ast.BinOp) and isinstance(node.op,ast.Mult):
            def unit_factor(n):
                if isinstance(n,ast.UnaryOp) and isinstance(n.op,(ast.UAdd,ast.USub)):return unit_factor(n.operand)
                return isinstance(n,ast.Constant) and n.value==1
            return angular(node.left) and unit_factor(node.right) or unit_factor(node.left) and angular(node.right)
        return False
    if angular(root):
        return label+'（弧度）'
    return label


def compile_program(program,evidence,source_map):
    compiler=Compiler(program)
    if len(compiler.shapes)>48:raise VisualSceneError('分割后数学对象超过四十八个，请分步规划较少对象。')
    if set(program.roles)-set(compiler.items) or any(not re.fullmatch(r'[^=＝{}$\\^*/<>\n]{1,24}',role) for role in program.roles.values()):
        raise VisualSceneError('角色名称必须引用真实对象，且不得含公式或数字。')
    claims=[]
    for claim in program.claims:
        if any(step>len(program.operations) for step in claim.steps) or claim.phase=='endpoint' and not claim.steps:
            raise VisualSceneError('终点关系必须指定存在的步骤编号；不能把终点结论作为全程恒等式。')
        if claim.source_id not in source_map:raise VisualSceneError('数学关系缺少来源编号。')
        tree=expression_tree(claim.lhs+'+('+claim.rhs+')')
        if not any(isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id in MEASURES for n in ast.walk(tree)):
            raise VisualSceneError('核心数学关系必须测量实际对象，不能只验证参数或常数恒等式。')
        left,right=compiler.parse(claim.lhs),compiler.parse(claim.rhs)
        symbolic=sp.simplify(left-right)
        claims.append({**claim.model_dump(),'source_quote':source_map[claim.source_id]['quote'],
            'symbolic_identity':symbolic==0,'symbolic_difference':str(symbolic)})
    beats=[]
    for op in program.operations:
        beats.append(VisualBeat(narration=op.narration,parameters=op.changes,
            show=compiler.expand_ids(op.show),hide=compiler.expand_ids(op.hide),
            calculations=[SceneCalculation(label=measurement_label(label,expr),expression=compiler.expression(expr)) for label,expr in op.measurements.items()]))
    model={'version':'math-construction-v1','program':program.model_dump(),'claims':claims,
           'proofs':compiler.proofs,'groups':compiler.groups,'source_map':source_map,
           'label_checks':compiler.label_checks,'display_expressions':compiler.display_expressions,
           'arc_checks':compiler.arc_checks,
           'alignment_checks':compiler.alignment_checks,
           'intersection_checks':compiler.intersection_checks,'curve_point_checks':compiler.curve_point_checks,
           'actor_of':compiler.actor_of,
           'unbounded_lines':[id for id,item in compiler.items.items() if item.get('extent') in {'ray','line'}],
           'roles':{**{item.id:item.label for item in program.constructions if item.label and
                    re.fullmatch(r'[^=＝{}$\\^*/<>\n]{1,24}',item.label)},**program.roles}}
    scene=VisualScenePlan(domain=program.title,question=program.objective,
        parameters=program.parameters,derived_parameters=compiler.derived,objects=compiler.shapes,
        beats=beats,evidence=evidence,mathematical_model=model,narration_binding='computed',
        teaching_example=program.teaching_example,simplifications=program.simplifications[:5],
        axes=any(s.kind=='curve' for s in compiler.shapes))
    model=scene.mathematical_model
    for obj in scene.objects:
        if obj.reference and obj.id in model['roles']:model['roles'][obj.id]='参照·'+model['roles'][obj.id]
    # Function tails may leave the viewport. Visible finite anchors, rather than
    # unrelated remote extrema, determine the equal-unit teaching window.
    from zhijiang.visual_planning import states,object_geometry
    points=[]
    anchor_camera=any(obj.kind=='dot' and obj.visible for obj in scene.objects)
    model['camera_policy']='finite-anchors' if anchor_camera else 'all-geometry'
    for before,after,visible in states(scene):
        for fraction in [0,.25,.5,.75,1]:
            params=resolve_parameters(scene,{k:before[k]+fraction*(after[k]-before[k]) for k in before})
            for obj in scene.objects:
                if obj.id not in visible:continue
                if anchor_camera and (obj.kind=='curve' or obj.id in model['unbounded_lines']):continue
                g=object_geometry(obj,params)
                if obj.kind=='circle':
                    x,y=g['points'][0];r=g['radius'];points.extend([[x-r,y-r],[x+r,y+r]])
                else:points.extend(g['points'])
    if not points:raise VisualSceneError('数学场景没有可见对象。')
    # Include the nearest relevant part of each unbounded line without using
    # remote direction anchors to compress finite teaching objects.
    center=[(min(p[i] for p in points)+max(p[i] for p in points))/2 for i in (0,1)]
    for before,after,visible in states(scene):
        for fraction in [0,.25,.5,.75,1]:
            params=resolve_parameters(scene,{k:before[k]+fraction*(after[k]-before[k]) for k in before})
            for obj in scene.objects:
                if obj.id not in visible or obj.line_extent=='segment':continue
                a,b=object_geometry(obj,params)['points'];direction=[b[i]-a[i] for i in (0,1)]
                denominator=sum(q*q for q in direction)
                if denominator<1e-18:continue
                t=sum((center[i]-a[i])*direction[i] for i in (0,1))/denominator
                if obj.line_extent=='ray':t=max(0,t)
                points.append([a[i]+t*direction[i] for i in (0,1)])
    xs,ys=zip(*points);padding=max(.4,.08*max(max(xs)-min(xs),max(ys)-min(ys)))
    scene.x_range=[min(xs)-padding,max(xs)+padding];scene.y_range=[min(ys)-padding,max(ys)+padding]
    for obj in scene.objects:
        if obj.line_extent!='segment':obj.coordinate_window=scene.x_range+scene.y_range
    # Point roles follow their actual dependent coordinates throughout motion.
    # This replaces model-guessed text positions with anchored annotations.
    point_labels={}
    from zhijiang.visual_coordinates import EuclideanViewport
    viewport=EuclideanViewport(scene.x_range,scene.y_range,1060,430,(600,310))
    focus_ids={item.id for item in program.constructions if item.op=='point_on_function'}
    focus=[]
    poses=[]
    for before,after,visible in states(scene):
        for fraction in [0,.25,.5,.75,1]:
            values=resolve_parameters(scene,{k:before[k]+fraction*(after[k]-before[k]) for k in before})
            poses.append({obj.id:viewport.point(*object_geometry(obj,values)['points'][0])
                for obj in scene.objects if obj.kind=='dot' and obj.id in visible})
            focus.extend(position for id,position in poses[-1].items() if id in focus_ids)
    if focus:
        fx,fy=zip(*focus)
        if max(max(fx)-min(fx),max(fy)-min(fy))<60:
            raise ConstructionLayoutError('函数绘图区间的极端值将核心动点/对应关系压缩到不足六十像素。请重规划合理的函数domain，覆盖教材例子和所有运动点但避免不必要远端极值；保持等单位坐标，不用额外远处对象撑大窗口。')
    placed=[]
    def overlap(a,b):
        return max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    for obj in list(scene.objects):
        if obj.kind!='dot' or obj.id not in model['roles']:continue
        label_id=obj.id+'_name'
        if any(other.id==label_id for other in scene.objects):raise VisualSceneError('自动点标注ID冲突。')
        width=max(24,len(model['roles'][obj.id])*22);height=26
        candidates=[]
        for gap in [14,32,52]:
            for dx,dy in [(1,1),(-1,1),(1,-1),(-1,-1),(1,0),(-1,0),(0,1),(0,-1)]:
                ox=dx*(width/2+gap);oy=dy*(height/2+gap);boxes=[];score=gap*.01
                for index,pose in enumerate(poses):
                    if obj.id not in pose:boxes.append(None);continue
                    x,y=pose[obj.id];box=(x+ox-width/2,y+oy-height/2,x+ox+width/2,y+oy+height/2);boxes.append(box)
                    for old in placed:
                        if old[index]:score+=overlap(box,old[index])
                    for x0,y0 in pose.values():score+=overlap(box,(x0-7,y0-7,x0+7,y0+7))
                    score+=100*(max(0,55-box[0])+max(0,box[2]-1145)+max(0,135-box[1])+max(0,box[3]-525))
                candidates.append((score,ox,oy,boxes))
        _,ox,oy,boxes=min(candidates,key=lambda candidate:candidate[0]);placed.append(boxes)
        offset_x=ox/viewport.scale;offset_y=oy/viewport.scale
        scene.objects.append(SceneObject(id=label_id,kind='label',
            position=[f'({obj.position[0]})+{offset_x}',f'({obj.position[1]})+{offset_y}'],
            text=model['roles'][obj.id],color=obj.color,visible=obj.visible,reference=obj.reference))
        point_labels[obj.id]=label_id
    model['point_labels']=point_labels
    for beat in scene.beats:
        beat.show=list(dict.fromkeys(beat.show+[point_labels[id] for id in beat.show if id in point_labels]))
        beat.hide=list(dict.fromkeys(beat.hide+[point_labels[id] for id in beat.hide if id in point_labels]))
    # Validate the final expanded contract before starting audio or Manim.
    return VisualScenePlan.model_validate(scene.model_dump())


def resolve_parameters(scene,parameters):
    values=dict(parameters);pending=dict(scene.derived_parameters)
    while pending:
        changed=False
        for name,expr in list(pending.items()):
            if name in values:raise VisualSceneError('不能独立覆盖数学派生量：'+name)
            names={n.id for n in ast.walk(expression_tree(expr)) if isinstance(n,ast.Name)}-set(FUNCTIONS)-{'pi','e'}
            if names<=values.keys():
                values[name]=evaluate(expr,values);del pending[name];changed=True
        if not changed:raise VisualSceneError('数学派生量含循环或未定义依赖。')
    return values


def validate_actual_claims(scene,geometry,parameters,*,step_index=None,endpoint=False):
    model=scene.mathematical_model
    if not model:return []
    shapes={o.id:o for o in scene.objects};groups=model.get('groups',{})
    def measured(name,args,walk):
        def ref(node):
            if not isinstance(node,ast.Name) or node.id not in geometry and node.id not in groups:
                raise VisualSceneError('实际数学测量引用不存在对象。')
            return node.id
        ids=[ref(n) for n in args[:({'value':1,'derivative':1}.get(name,len(args)))]]
        def point(i):
            if shapes[i].kind!='dot':raise VisualSceneError('坐标测量须引用实际点。')
            return geometry[i]['points'][0]
        def line(i):
            if shapes[i].kind not in {'line','arrow'}:raise VisualSceneError('距离/角测量须引用实际线。')
            return geometry[i]['points']
        if name=='distance':return sp.Float(math.dist(point(ids[0]),point(ids[1])))
        if name=='line_distance':
            p=point(ids[0]);a,b=line(ids[1]);v=[b[i]-a[i] for i in range(2)];length=math.hypot(*v)
            if length<1e-9:raise VisualSceneError('实际参照线退化。')
            return sp.Float(abs(v[0]*(p[1]-a[1])-v[1]*(p[0]-a[0]))/length)
        if name=='length':
            if ids[0] in model.get('unbounded_lines',[]):raise VisualSceneError('无限直线或射线没有有限长度。')
            return sp.Float(math.dist(*line(ids[0])))
        if name=='area':
            result=0
            for id in groups.get(ids[0],[ids[0]]):
                if shapes[id].kind!='polygon':raise VisualSceneError('面积须来自实际多边形。')
                ps=geometry[id]['points'];result+=abs(sum(a[0]*b[1]-a[1]*b[0] for a,b in zip(ps,ps[1:]+ps[:1])))/2
            return sp.Float(result)
        if name in {'angle','signed_angle'}:
            a,b=line(ids[0]);c,d=line(ids[1]);u=[b[i]-a[i] for i in range(2)];v=[d[i]-c[i] for i in range(2)];den=math.hypot(*u)*math.hypot(*v)
            if den<1e-9:raise VisualSceneError('实际角的射线退化。')
            if name=='signed_angle':return sp.Float(math.atan2(u[0]*v[1]-u[1]*v[0],sum(a*b for a,b in zip(u,v))))
            return sp.Float(math.acos(max(-1,min(1,sum(a*b for a,b in zip(u,v))/den))))
        if name=='angle_at':
            vertex=point(ids[0]);vectors=[]
            source_objects={o['id']:o for o in model['program']['constructions']}
            for id in ids[1:]:
                a,b=line(id)
                at_start=math.dist(vertex,a)<1e-7;at_end=math.dist(vertex,b)<1e-7
                if shapes[id].line_extent=='ray' and not at_start:raise VisualSceneError('实际角的顶点必须是射线起点。')
                if not at_start and not at_end:raise VisualSceneError('实际角的顶点不是边端点。')
                vectors.append([b[i]-a[i] if at_start else a[i]-b[i] for i in range(2)])
            u,v=vectors;den=math.hypot(*u)*math.hypot(*v)
            if den<1e-9:raise VisualSceneError('实际角的边退化。')
            return sp.Float(math.acos(max(-1,min(1,sum(a*b for a,b in zip(u,v))/den))))
        if name in {'xcoord','ycoord'}:return sp.Float(point(ids[0])[0 if name=='xcoord' else 1])
        if name in {'value','derivative'}:
            obj=shapes[ids[0]]
            if obj.kind!='curve':raise VisualSceneError('函数测量须来自实际绘制曲线。')
            if name=='value':return sp.Float(evaluate(obj.expression,{**parameters,'x':float(walk(args[1]))}))
            x=sp.Symbol('x',real=True);expr=scalar(obj.expression,{**{k:sp.Rational(str(v)) for k,v in parameters.items()},'x':x})
            order=int(walk(args[2]))
            if not 0<=order<=8:raise VisualSceneError('实际导数阶数超界。')
            return sp.diff(expr,x,order).subs(x,walk(args[1]))
        raise VisualSceneError('未知实际数学测量。')
    report=[]
    for check in model.get('curve_point_checks',[]):
        parameter=evaluate(check['parameter'],parameters);lower,upper=check['domain']
        if not lower-1e-9<=parameter<=upper+1e-9:
            raise VisualSceneError('曲线上的点参数超出实际绘图域：'+check['object'])
    for check in model.get('intersection_checks',[]):
        p=geometry[check['object']]['points'][0]
        if check['kind']=='lines':
            if abs(evaluate(check['determinant'],parameters))<1e-9:
                raise VisualSceneError('实际两线平行或重合，交点不唯一。')
            for ref,expression,extent in zip(check['refs'],check['parameters'],check['extents']):
                t=evaluate(expression,parameters)
                if extent=='segment' and not -1e-9<=t<=1+1e-9:
                    raise VisualSceneError('实际交点超出有限线段：'+ref)
                if extent=='ray' and t < -1e-9:
                    raise VisualSceneError('实际交点位于射线反方向：'+ref)
                a,b=geometry[ref]['points'];direction=[b[i]-a[i] for i in range(2)]
                norm=math.hypot(*direction)
                error=abs(direction[0]*(p[1]-a[1])-direction[1]*(p[0]-a[0]))
                if norm<1e-9 or error/norm>1e-6:
                    raise VisualSceneError('实际交点未落在对应线方向：'+check['object'])
        else:
            if evaluate(check['center_distance_squared'],parameters)<=1e-18 or evaluate(check['height_squared'],parameters)<-1e-9:
                raise VisualSceneError('实际两圆不存在选定实交点。')
            for ref in check['refs']:
                circle=geometry[ref];r=circle['radius']
                if r<=0 or abs(math.dist(p,circle['points'][0])-r)>1e-6*max(1,r):
                    raise VisualSceneError('实际交点不满足对应圆半径：'+check['object'])
    for check in model.get('alignment_checks',[]):
        progress=evaluate(check['progress'],parameters)
        if not 0<=progress<=1:raise VisualSceneError('几何对齐进度必须在零到一之间：'+check['object'])
        if any(math.hypot(*(evaluate(q,parameters) for q in vector))<1e-9 for vector in check['directions']):
            raise VisualSceneError('实际几何对齐方向退化：'+check['object'])
    for arc in model.get('arc_checks',[]):
        center=[evaluate(e,parameters) for e in arc['center']];radius=evaluate(arc['radius'],parameters)
        if radius<=0:raise VisualSceneError('实际角弧半径必须为正。')
        for endpoint,edge_id in zip([geometry[arc['object']]['points'][0],geometry[arc['object']]['points'][-1]],arc['edges']):
            a,b=geometry[edge_id]['points'];target=b if math.dist(center,a)<1e-7 else a
            direction=[target[i]-center[i] for i in range(2)];norm=math.hypot(*direction)
            if norm<1e-9 or math.dist(endpoint,[center[i]+radius*direction[i]/norm for i in range(2)])>1e-6:
                raise VisualSceneError('实际角弧端点与对应边方向不一致：'+arc['object'])
    for check in model.get('label_checks',[]):
        actual=geometry[check['object']]['points'][0][check['axis']]
        if abs(actual-check['value'])>1e-6:raise VisualSceneError('坐标标签与实际锚点不一致：'+check['object'])
    for claim in model['claims']:
        if claim.get('steps') and step_index not in claim['steps']:continue
        if claim.get('phase','invariant')=='endpoint' and not endpoint:continue
        left=float(scalar(claim['lhs'],parameters,measured));right=float(scalar(claim['rhs'],parameters,measured))
        tolerance=1e-6*max(1,abs(left),abs(right));difference=left-right
        passed=({'equal':abs(difference)<=tolerance,'not_equal':abs(difference)>tolerance,'nonnegative':difference>=-tolerance,
                 'positive':difference>tolerance,'at_most':difference<=tolerance}[claim['relation']])
        if not math.isfinite(left+right) or not passed:
            controls={k:parameters[k] for k in model['program']['parameters']}
            related={n.id for expression in (claim['lhs'],claim['rhs'])
                for n in ast.walk(expression_tree(expression)) if isinstance(n,ast.Name) and n.id in geometry}
            exc=VisualSceneError('实际数学关系失败：'+claim['lhs']+' '+claim['relation']+' '+claim['rhs']+
                f'，测量{left:.8g}与{right:.8g}，步骤{step_index}，'+
                ('终点' if endpoint else '连续状态')+'，控制参数：'+json.dumps(controls,ensure_ascii=False)+'。')
            exc.math_failure={'step':step_index,'endpoint':endpoint,'parameters':controls,
                'claim':claim,'measured':[left,right],'actual_geometry':{id:geometry[id] for id in related}}
            raise exc
        report.append({'lhs':claim['lhs'],'rhs':claim['rhs'],'phase':claim.get('phase','invariant'),
            'step':step_index,'measured':[left,right],'passed':True})
    return report
