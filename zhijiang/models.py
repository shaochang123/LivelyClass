"""各阶段共享的严格数据契约。"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from zhijiang.presentation_templates import PPTTemplate


class Mode(StrEnum):
    DEMO = "demo"
    AI = "ai"


class VoiceMode(StrEnum):
    SYSTEM = "system"
    AI = "ai"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


VisualKind = Literal["concept", "formula", "process"]
AnimationMode = Literal["auto", "math", "visual", "geometry", "basic"]
MathSceneKind = Literal["linearity", "basis", "plane", "projection", "composition"]


class PageText(BaseModel):
    page: int = Field(ge=1)
    text: str = Field(min_length=1)
    ocr: bool = False
    math_reading: dict | None = None


class SourceDocument(BaseModel):
    filename: str
    pages: list[PageText] = Field(min_length=1)


class Evidence(BaseModel):
    page: int = Field(ge=1)
    quote: str = Field(min_length=8, max_length=300)
    ocr: bool = False


class LiteralEvidence(Evidence):
    quote: str = Field(min_length=1, max_length=300)
    literal: Literal[True]


class SourceSpan(BaseModel):
    page: int = Field(ge=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1)
    page_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


class ArtServiceOptions(BaseModel):
    protocol: Literal['disabled', 'images', 'jimeng', 'seedance'] = 'disabled'
    base_url: str = Field(default='', max_length=500)
    model: str = Field(default='', max_length=160)
    api_key: str = Field(default='', exclude=True, repr=False)


class ArtOptions(BaseModel):
    image: ArtServiceOptions = Field(default_factory=ArtServiceOptions)
    video: ArtServiceOptions = Field(default_factory=ArtServiceOptions)
    max_assets: int = Field(default=12, ge=1, le=200)


class LearnerProfile(BaseModel):
    enabled: bool = False
    background: str = Field(default='', max_length=1000)
    goal: str = Field(default='', max_length=1000)
    days: int = Field(default=3, ge=1, le=30)
    minutes_per_day: int = Field(default=30, ge=5, le=240)


class GenerationOptions(BaseModel):
    provider: Literal["ollama", "openai"] = "openai"
    base_url: str = ""
    model: str = ""
    review_model: str = Field(default="",max_length=120)
    math_model: str = Field(default="",max_length=120)
    api_key: str = Field(default="", exclude=True)
    prompt: str = Field(default="", max_length=4000)
    animation_mode: AnimationMode = "auto"
    ppt_template: PPTTemplate = 'classic'
    style_mode: Literal['template', 'adaptive', 'comic', 'subject'] = 'template'
    narration_mode: Literal['explanatory', 'source_verbatim'] = 'explanatory'
    art: ArtOptions = Field(default_factory=ArtOptions)
    learner: LearnerProfile = Field(default_factory=LearnerProfile)
    use_illustrations: bool = True
    semantic_thinking: bool = True
    content_checks: Literal['warn', 'strict'] = 'warn'


class SpeechOptions(BaseModel):
    base_url: str = ""
    model: str = ""
    voice: str = ""
    api_key: str = Field(default="", exclude=True)


class KnowledgePoint(BaseModel):
    title: str = Field(min_length=2, max_length=80)
    kind: VisualKind
    explanation: str = Field(min_length=8, max_length=500)
    evidence: Evidence


class KnowledgeBundle(BaseModel):
    points: list[KnowledgePoint] = Field(min_length=1)


class CourseOutline(BaseModel):
    title: str = Field(min_length=2, max_length=80)
    objective: str = Field(min_length=8, max_length=240)
    point_titles: list[str] = Field(min_length=1)


class MathParameters(BaseModel):
    matrix: list[list[float]] = Field(default_factory=lambda: [[2, 1], [0, 1]])
    vector: list[float] = Field(default_factory=lambda: [1, 2])
    second_vector: list[float] = Field(default_factory=lambda: [1, -1])
    shift: list[float] = Field(default_factory=lambda: [1, 0])
    scalar: float = 2
    rotation_degrees: float = 45
    projection_degrees: float = 45
    stretch: list[float] = Field(default_factory=lambda: [2, 1])


class MathClaim(BaseModel):
    key: Literal["A", "v", "w", "shift", "Av", "Aw", "sum", "A_sum", "sum_images",
                 "scaled_image", "image_scaled", "determinant", "area", "e1_image", "e2_image",
                 "P", "Pv", "P_squared", "parallel", "perpendicular", "parallel_image",
                 "perpendicular_image", "diagonal_projection", "R", "S", "Rv", "Sv",
                 "SR", "RS", "SRv", "RSv"]
    values: list[float] = Field(min_length=1)


class MathBeat(BaseModel):
    action: str
    narration: str = Field(min_length=12, max_length=1200)


class MathScenePlan(BaseModel):
    kind: MathSceneKind
    question: str = Field(min_length=4, max_length=120)
    parameters: MathParameters
    beats: list[MathBeat] = Field(min_length=3)
    claims: list[MathClaim] = Field(default_factory=list)
    evidence: Evidence
    teaching_example: bool = True
    narration_binding: Literal["draft", "verified"] = "draft"
    verification: dict = Field(default_factory=dict)


class SceneObject(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    kind: str = Field(default="dot", max_length=32)
    points: list[list[str]] = Field(default_factory=list, max_length=128)
    position: list[str] = Field(default_factory=list, max_length=2)
    start: list[str] = Field(default_factory=list, max_length=2)
    end: list[str] = Field(default_factory=list, max_length=2)
    vertices: list[list[str]] = Field(default_factory=list, max_length=128)
    expression: str = Field(default="", max_length=240)
    parametric_expression: list[str] = Field(default_factory=list,max_length=2)
    domain: list[float] = Field(default_factory=lambda: [-2, 2], min_length=2, max_length=2)
    radius: str = Field(default="0.15", max_length=80)
    text: str = Field(default="", max_length=80)
    color: str = Field(default="#78BAFF", pattern=r"^#[0-9a-fA-F]{6}$")
    visible: bool = True
    reference: bool = False
    double_tip: bool = False
    line_extent: Literal['segment','ray','line'] = 'segment'
    coordinate_window: list[float] = Field(default_factory=list,max_length=4)


class SceneCalculation(BaseModel):
    label: str = Field(min_length=1, max_length=32)
    expression: str = Field(min_length=1, max_length=240)
    expected: float | None = None


class SceneCheck(BaseModel):
    checker: str = Field(default="equal", max_length=32)
    expression: str = Field(min_length=1, max_length=240)
    expected: float = 0
    tolerance: float = Field(default=1e-6, ge=0, le=0.01)


class VisualBeat(BaseModel):
    narration: str = Field(min_length=8, max_length=800)
    parameters: dict[str, float] = Field(default_factory=dict)
    show: list[str] = Field(default_factory=list)
    hide: list[str] = Field(default_factory=list)
    calculations: list[SceneCalculation] = Field(default_factory=list, max_length=4)


class GeometryConstraint(BaseModel):
    kind: Literal['on_function','on_circle','equal_distance','equal_length','equal_area','perpendicular','parallel','same_start']
    objects: list[str] = Field(min_length=2,max_length=3)
    source_quote: str = Field(min_length=8,max_length=300)


class TeachingNode(BaseModel):
    id: int = Field(ge=1, le=6)
    label: str = Field(min_length=2, max_length=48)
    source_quote: str = Field(min_length=8, max_length=300)
    source_term: str = Field(default='',max_length=64)
    kind: Literal['entity','operation','condition'] = 'entity'


class TeachingRelation(BaseModel):
    id: int = Field(ge=1, le=8)
    source: int = Field(ge=1, le=6)
    target: int = Field(ge=1, le=6)
    label: str = Field(min_length=1, max_length=32)
    source_quote: str = Field(min_length=8, max_length=300)
    source_term: str = Field(default='',max_length=100)
    supporting_quotes: list[str] = Field(default_factory=list,max_length=2)
    directed: bool = True
    source_fact_id: int | None = Field(default=None,ge=1)
    # Semantic edges retain all independently read assertions, including
    # conditions spanning sentences. They still require an itemized review.
    binding: Literal['extractive', 'semantic'] = 'extractive'
    supporting_fact_ids: list[int] = Field(default_factory=list,max_length=2)
    source_proposition_id: int | None = Field(default=None,ge=1)
    source_statements: list[str] = Field(default_factory=list,max_length=3)
    condition: str = Field(default='',max_length=120)


class TeachingSourceFact(BaseModel):
    id: int = Field(ge=1)
    source_id: int = Field(ge=1)
    source_quote: str = Field(min_length=8,max_length=300)
    statement: str = Field(min_length=12,max_length=120)


class TeachingStep(BaseModel):
    narration: str = Field(min_length=12, max_length=220)
    source_statement: str = Field(default='',max_length=120)
    source_fact_id: int | None = Field(default=None,ge=1)
    analogy: str = Field(default='',max_length=80)
    focus: list[int] = Field(min_length=1, max_length=6)
    relations: list[int] = Field(default_factory=list, max_length=8)


class TeachingDiagram(BaseModel):
    representation: Literal['process', 'relationship', 'comparison', 'source_figure']
    rationale: str = Field(min_length=8, max_length=180)
    nodes: list[TeachingNode] = Field(min_length=1, max_length=6)
    relations: list[TeachingRelation] = Field(default_factory=list, max_length=8)
    steps: list[TeachingStep] = Field(min_length=1, max_length=5)
    source_facts: list[TeachingSourceFact] = Field(default_factory=list,max_length=8)
    source_asset: str | None = None
    source_regions: dict[str, list[float]] = Field(default_factory=dict)


class VisualScenePlan(BaseModel):
    domain: str = Field(min_length=2, max_length=40)
    question: str = Field(min_length=4, max_length=120)
    parameters: dict[str, float] = Field(default_factory=dict)
    # Source constructions stay bounded separately. Compiled partition cells
    # and anchored annotations need room in the serialized renderer contract.
    objects: list[SceneObject] = Field(min_length=2, max_length=64)
    beats: list[VisualBeat] = Field(min_length=1, max_length=12)
    checks: list[SceneCheck] = Field(default_factory=list, max_length=16)
    geometry_constraints: list[GeometryConstraint] = Field(default_factory=list,max_length=12)
    derived_parameters: dict[str,str] = Field(default_factory=dict)
    mathematical_model: dict | None = None
    domain_data: dict[str, str] = Field(default_factory=dict)
    domain_validators: list[str] = Field(default_factory=list, max_length=12)
    x_range: list[float] = Field(default_factory=lambda: [-5, 5], min_length=2, max_length=2)
    y_range: list[float] = Field(default_factory=lambda: [-3, 3], min_length=2, max_length=2)
    axes: bool = False
    evidence: Evidence
    teaching_example: bool = True
    simplifications: list[str] = Field(default_factory=list, max_length=5)
    narration_binding: Literal["computed"] | None = None
    verification: dict = Field(default_factory=dict)
    diagram: TeachingDiagram | None = None


class LessonSegment(BaseModel):
    title: str = Field(min_length=2, max_length=80)
    kind: VisualKind
    narration: str = Field(min_length=1)
    bullets: list[str] = Field(min_length=1, max_length=4)
    evidence: Evidence | LiteralEvidence
    source_spans: list[SourceSpan] = Field(default_factory=list)
    math_scene: MathScenePlan | None = None
    visual_scene: VisualScenePlan | None = None

    @model_validator(mode='after')
    def narration_contract(self):
        if not self.source_spans and len(self.narration)<20:
            raise ValueError('普通讲稿至少20字；短原文必须绑定来源字符区间。')
        if self.source_spans and self.narration!=''.join(s.text for s in self.source_spans):
            raise ValueError('原文配音不得改写、遗漏或添加来源文字。')
        return self


class Lesson(BaseModel):
    title: str = Field(min_length=2, max_length=80)
    objective: str = Field(min_length=8, max_length=240)
    segments: list[LessonSegment] = Field(min_length=1)
    mode: Mode
    voice_mode: VoiceMode
    notice: str
    animation_report: dict = Field(default_factory=dict)
    ppt_template: PPTTemplate = 'classic'
    deck_plan: dict = Field(default_factory=dict)


class ReviewResult(BaseModel):
    approved: bool
    issues: list[str] = Field(default_factory=list, max_length=8)
