"""Original, fixed layout contracts shared by the planner and PPT renderer."""
from dataclasses import dataclass
from typing import Literal
import html

PPTTemplate = Literal['classic', 'paper', 'academic', 'editorial',
                      'newtouch_cloud', 'newtouch_blue', 'newtouch_white', 'newtouch_dark',
                      'stem_board', 'stem_lab', 'language_story', 'language_comic', 'professional_system']
DeckLayout = Literal['wide', 'split', 'captioned', 'evidence', 'illustrated', 'collage']


@dataclass(frozen=True)
class PresentationTemplate:
    id: str
    name: str
    description: str
    background: str
    ink: str
    accent: str
    muted: str
    panel: str
    layouts: tuple[str, ...]
    default_layout: str
    title_size: int
    # Diagram boxes in inches, independent of content or subject names.
    boxes: dict[str, tuple[float, float, float, float]]
    decoration: str = 'none'
    secondary: str = '356585'
    title_align: str = 'left'
    cover_align: str = 'left'
    subject_family: str = 'general'

    def public(self):
        return {'id': self.id, 'name': self.name, 'description': self.description,
                'colors': {'background': '#'+self.background, 'ink': '#'+self.ink,
                           'accent': '#'+self.accent, 'panel': '#'+self.panel},
                'layouts': list(self.layouts), 'default_layout': self.default_layout,
                'family': {'stem':'数理化','humanities':'语言与人文','professional':'专业与工程'}.get(
                    self.subject_family,'科技风格' if self.decoration != 'none' else '教学风格'),
                'subject_family': self.subject_family}


TEMPLATES = {
    'classic': PresentationTemplate('classic', '深色演示', '大幅图示与独立动画页，适合数学推演。',
        '081623', 'F4F7F9', '6DE2C0', 'A7C0CC', '10283A', ('wide',), 'wide', 32,
        {'wide': (.66, 1.55, 12.0, 5.0)}),
    'paper': PresentationTemplate('paper', '纸上讲解', '暖白纸面、黄色重点块；问题引入、图文分栏与回顾。',
        'FBFAF5', '242724', 'EBCB49', '626961', 'F0EEE3',
        ('split', 'captioned', 'evidence', 'wide'), 'split', 34,
        {'split': (5.05, 1.8, 7.45, 4.65), 'captioned': (.8, 1.5, 11.73, 4.6),
         'evidence': (.8, 1.7, 7.45, 4.8), 'wide': (.8, 1.5, 11.73, 5.1)}),
    'academic': PresentationTemplate('academic', '学术课堂', '蓝白课堂风格；标题层级、证据边栏与条件说明。',
        'F4F7FB', '182D49', '2864B4', '53657C', 'E5EDF8',
        ('evidence', 'split', 'wide'), 'evidence', 30,
        {'evidence': (.65, 1.5, 8.0, 5.15), 'split': (4.85, 1.55, 7.75, 5.0),
         'wide': (.65, 1.45, 12.0, 5.15)}),
    'editorial': PresentationTemplate('editorial', '杂志叙事', '红色章节带、较大标题；知识重点与辅助配图。',
        'F5F0E9', '292422', 'AA3F32', '6D625C', 'E9E0D6',
        ('captioned', 'split', 'wide'), 'captioned', 38,
        {'captioned': (1.3, 1.5, 10.8, 4.55), 'split': (5.0, 1.8, 7.5, 4.55),
         'wide': (1.25, 1.5, 11.3, 5.1)}),
    'newtouch_cloud': PresentationTemplate('newtouch_cloud','云彩渐变',
        '蓝紫云彩、柔和光晕与居中大标题；开放图示与双栏对比。',
        'F5F9FF','152644','2868EF','60738D','EAF0FF',
        ('wide','split','captioned'),'wide',42,
        {'wide':(.85,1.65,11.65,4.95),'split':(5.1,1.85,7.2,4.65),'captioned':(.85,1.65,11.65,4.8)},
        decoration='cloud',secondary='7B36C5',title_align='center',cover_align='center'),
    'newtouch_blue': PresentationTemplate('newtouch_blue','科技蓝',
        '科技蓝与青色轨道、轻盈三栏和宽幅示意图；大标题与蓝色重点线。',
        'F7FBFF','152C48','0866FF','53728E','E5F0FF',
        ('wide','captioned','split'),'captioned',40,
        {'wide':(.9,1.65,11.45,4.9),'captioned':(.9,1.7,11.45,4.65),'split':(5.25,1.8,7.05,4.7)},
        decoration='blue',secondary='027D9D'),
    'newtouch_white': PresentationTemplate('newtouch_white','企业白',
        '白底、蓝紫双色重点、细线与点阵；对照页面和简洁图文展示。',
        'FFFFFF','16263E','1769E8','60738D','F0F5FF',
        ('split','wide','evidence'),'split',40,
        {'wide':(.95,1.65,11.3,4.9),'split':(5.35,1.8,6.9,4.65),'evidence':(.95,1.7,7.2,4.85)},
        decoration='white',secondary='8B36C2',cover_align='center'),
    'newtouch_dark': PresentationTemplate('newtouch_dark','深蓝发布',
        '深蓝渐变、蓝紫光环和明亮青色重点；适合技术讲解与课程展示。',
        '0A1730','F4F8FF','58C7FF','B4C6E0','152B4D',
        ('wide','split','captioned'),'wide',42,
        {'wide':(.85,1.7,11.6,4.9),'split':(5.15,1.85,7.15,4.6),'captioned':(.85,1.7,11.6,4.7)},
        decoration='dark',secondary='C4ABFF',cover_align='center'),
}

from dataclasses import replace
TEMPLATES.update({
    'stem_board': replace(TEMPLATES['classic'],id='stem_board',name='数理推演',subject_family='stem',
        boxes={'wide':(.65,1.3,12.,5.35)},
        description='深色板书、稳定对象颜色、公式与曲线的连续变化；先提出问题，再展开条件、推理和原例。'),
    'stem_lab': replace(TEMPLATES['newtouch_blue'],id='stem_lab',name='实验与过程',subject_family='stem',
        boxes={'wide':(.75,1.45,11.85,5.1),'split':(5.25,1.65,7.2,4.9),'captioned':(.75,1.5,11.85,4.6)},
        description='蓝白实验记录、变量与单位、过程示意和前后对比；图片辅助，定量关系用可计算图形。'),
    'language_story': replace(TEMPLATES['editorial'],id='language_story',name='人文故事线',subject_family='humanities',
        boxes={'wide':(.95,1.65,11.4,4.95),'split':(5.25,1.95,7.,4.4),'captioned':(.95,1.65,11.4,4.55)},
        description='暖色叙事、人物与场景插画；按原文顺序展开情境、线索、转折与理解，不添加教材未述事实。'),
    'language_comic': replace(TEMPLATES['paper'],id='language_comic',name='动漫场景',subject_family='humanities',
        boxes={'wide':(.7,1.6,11.95,5.0),'split':(4.8,1.9,7.8,4.65),
               'captioned':(.7,1.6,11.95,4.5),'evidence':(.7,1.8,7.7,4.7)},
        description='动漫场景漫剧：环境、人物行动与连续镜头；教材原文作完整旁白，PPT收录场景关键帧。'),
    'professional_system': replace(TEMPLATES['academic'],id='professional_system',name='系统与结构',subject_family='professional',
        boxes={'wide':(.6,1.5,12.1,5.05),'split':(4.65,1.7,8.,4.75),'evidence':(.6,1.7,8.25,4.95)},
        description='清晰的系统分层、实体关系、操作流程与实例；保留专业符号和前提。'),
})

# Subject-independent illustration layouts; retain each template's margins.
for _key, _template in list(TEMPLATES.items()):
    _left = 1.3 if _key == 'editorial' else .8
    TEMPLATES[_key] = replace(_template, title_size=max(36, _template.title_size),
        layouts=(*_template.layouts, 'illustrated', 'collage'),
        boxes={**_template.boxes, 'illustrated': (_left, 1.7, 5.1, 4.9),
               'collage': (_left, 1.7, 11.4-(_left-.8), 4.9)})


def get_template(template_id: str) -> PresentationTemplate:
    try:
        return TEMPLATES[template_id]
    except KeyError as exc:
        raise ValueError('未知 PPT 模板。') from exc


def template_instruction(template_id: str) -> str:
    template = get_template(template_id)
    return ('\n教学表达要求：先提出当前来源能够回答的问题，再解释概念或条件，'
            '沿用来源中合适的例子与对比，最后回顾。每个片段解决一个具体问题；'
            '画面提示词短，口播补充原因和前提，不朗读整页。'
            '适合比较、原文插图或定义的内容不强行做流程图或坐标动画。'
            '来源中的假设、适用范围和不确定性必须保留；不能为模仿视频简化掉条件。'
            '不要固定使用某学科例子，不复制参考视频的图像、文字或音色。'
            '\n所选 PPT 模板：'+template.name+'；'+template.description+
            '。版式可选：'+', '.join(template.layouts)+'。')


def template_preview_svg(template_id: str) -> str:
    template = get_template(template_id)
    if template.decoration != 'none':return corporate_preview_svg(template)
    x, y, w, h = template.boxes[template.default_layout]
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">',
        f'<title>{html.escape(template.name)}：版式示意</title>',
        backdrop_elements(template),
        f'<rect x="48" y="35" width="12" height="70" fill="#{template.accent}"/>',
        f'<text x="82" y="84" font-family="Microsoft YaHei, sans-serif" font-size="38" fill="#{template.ink}">{html.escape(template.name)}</text>',
        f'<rect x="{x*96}" y="{y*96}" width="{w*96}" height="{h*96}" rx="18" fill="#{template.panel}"/>',
        f'<circle cx="{(x+w/2)*96}" cy="{(y+h/2)*96}" r="65" fill="none" stroke="#{template.accent}" stroke-width="8"/>',
        f'<text x="{(x+w/2)*96}" y="{(y+h/2)*96+120}" text-anchor="middle" font-family="Microsoft YaHei, sans-serif" font-size="24" fill="#{template.ink}">图示 / 原文 / 演示</text>']
    if template.default_layout == 'split':
        lx, ly, lw = 120, 230, 280
    elif template.default_layout == 'evidence':
        lx, ly, lw = 884, 190, 265
    else:
        lx, ly, lw = 150, 650, 480
    for i in range(3 if template.default_layout != 'captioned' else 1):
        parts.append(f'<rect x="{lx}" y="{ly+i*86}" width="{lw}" height="16" rx="8" fill="#{template.accent}"/>')
    parts.append('</svg>')
    return ''.join(parts)


def backdrop_elements(template):
    """Original procedural decoration inspired by the supplied website images."""
    if template.decoration.startswith('adaptive:'):
        from zhijiang.course_styles import adaptive_backdrop
        return adaptive_backdrop(template)
    base=f'<rect width="1280" height="720" fill="#{template.background}"/>'
    if template.decoration=='none':return base
    dark=template.decoration=='dark'
    colors=('194583','523583','10213F') if dark else ('B8EFFF','CFD5FF','F7FBFF')
    defs=(f'<defs><linearGradient id="wash" x1="0" y1="0" x2="1" y2="1">'
          f'<stop stop-color="#{colors[0]}"/><stop offset=".5" stop-color="#{colors[1]}"/>'
          f'<stop offset="1" stop-color="#{colors[2]}"/></linearGradient>'
          f'<radialGradient id="halo"><stop stop-color="#{template.accent}" stop-opacity=".20"/>'
          '<stop offset="1" stop-color="'+template.background+'" stop-opacity="0"/></radialGradient></defs>')
    p=[base,defs]
    if template.decoration in {'cloud','dark'}:
        p.append('<rect width="1280" height="720" fill="url(#wash)" opacity=".40"/>')
        p.append('<ellipse cx="1130" cy="40" rx="530" ry="400" fill="url(#halo)"/>')
        p.append('<ellipse cx="30" cy="740" rx="540" ry="300" fill="url(#halo)"/>')
        p.append(f'<path d="M870,0 L1280,0 L1280,360 Z" fill="#{template.accent}" opacity=".035"/>')
    elif template.decoration=='blue':
        p.append(f'<path d="M0,0 H1280 V18 H0 Z" fill="#{template.accent}"/>')
        for radius in (90,135,180):
            p.append(f'<circle cx="1220" cy="90" r="{radius}" fill="none" stroke="#{template.accent}" stroke-width="2" opacity=".08"/>')
        p.append(f'<path d="M0,680 C400,610 860,760 1280,645 V720 H0 Z" fill="#{template.panel}"/>')
    else:
        p.append('<rect width="1280" height="8" fill="url(#wash)"/>')
        for row in range(9):
            for col in range(12):
                p.append(f'<circle cx="{1100+col*14}" cy="{590+row*14}" r="1.4" fill="#{template.accent}" opacity=".12"/>')
    return ''.join(p)


def template_backdrop_svg(template):
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
            '<title>课程风格背景</title>'+backdrop_elements(template)+'</svg>')


def corporate_preview_svg(template):
    """Show real composition choices, rather than the same empty figure box."""
    p=['<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">',
       f'<title>{html.escape(template.name)}：版式示意</title>',backdrop_elements(template),
       f'<defs><linearGradient id="heading"><stop stop-color="#{template.secondary}"/>'
       f'<stop offset="1" stop-color="#{template.accent}"/></linearGradient></defs>']
    def words(value,x,y,size=32,color=None,center=False,bold=False):
        p.append(f'<text x="{x}" y="{y}" text-anchor="{"middle" if center else "start"}" '
                 f'font-family="Microsoft YaHei" font-size="{size}" font-weight="{700 if bold else 400}" '
                 f'fill="{color or "#"+template.ink}">{html.escape(value)}</text>')
    if template.decoration=='cloud':
        words('让知识，清晰可见',640,150,66,'url(#heading)',True,True)
        words('读懂资料 · 看见关系 · 掌握方法',640,218,32,center=True)
        for i,label in enumerate(['阅读资料','理解概念','应用示例']):
            x=240+i*400
            p.append(f'<circle cx="{x}" cy="408" r="95" fill="#FFFFFF" opacity=".80"/>')
            if i==0:
                p.append(f'<path d="M{x-35},350 H{x+22} L{x+42},372 V458 H{x-35} Z '
                         f'M{x-20},388 H{x+23} M{x-20},408 H{x+23} M{x-20},428 H{x+12}" '
                         f'fill="none" stroke="#{template.accent}" stroke-width="5"/>')
            elif i==1:
                for dx,dy in [(-40,-25),(40,-25),(0,45)]:
                    p.append(f'<path d="M{x},408 L{x+dx},{408+dy}" stroke="#{template.accent}" stroke-width="4"/>')
                    p.append(f'<circle cx="{x+dx}" cy="{408+dy}" r="17" fill="#{template.secondary}"/>')
                p.append(f'<circle cx="{x}" cy="408" r="24" fill="#{template.accent}"/>')
            else:
                p.append(f'<path d="M{x-40},450 H{x+45} M{x-25},450 V410 M{x},450 V383 M{x+25},450 V350" '
                         f'fill="none" stroke="#{template.accent}" stroke-width="12"/>')
            words(label,x,552,36,center=True,bold=True)
            if i<2:p.append(f'<path d="M{x+130},408 H{x+260} l-14,-9 m14,9 l-14,9" fill="none" stroke="#{template.accent}" stroke-width="3"/>')
    elif template.decoration=='blue':
        words('从一个问题，走向一个方法',88,134,54,'url(#heading)',bold=True)
        words('宽幅示意图，让推理过程更清楚',88,203,32)
        p.append(f'<path d="M148,420 H1120" stroke="#{template.accent}" stroke-width="3"/>')
        for i,label in enumerate(['提出问题','展开推理','检验结论']):
            x=230+i*400
            p.append(f'<circle cx="{x}" cy="420" r="72" fill="#{template.panel}" stroke="#{template.accent}" stroke-width="3"/>')
            words(str(i+1),x,441,60,'#'+template.accent,True,True)
            words(label,x,568,36,center=True,bold=True)
    elif template.decoration=='white':
        words('观察差异，理解联系',88,130,56,'url(#heading)',bold=True)
        words('开放对比，保留每个观点的关键条件',88,198,32)
        p.append('<path d="M640,310 V624" stroke="#D9E4F5" stroke-width="2"/>')
        for i,(title,color) in enumerate([('概念 A',template.accent),('概念 B',template.secondary)]):
            x=140+i*650;words(title,x,334,44,'#'+color,bold=True)
            for j,label in enumerate(['对象与定义','条件与范围','例子与应用']):
                p.append(f'<circle cx="{x+10}" cy="{406+j*72}" r="5" fill="#{color}"/>')
                words(label,x+36,417+j*72,34)
    else:
        words('把抽象，变成看得见的推演',88,133,52,'url(#heading)',bold=True)
        words('连续演示 · 对象关系 · 关键结论',88,201,32)
        p.append(f'<path d="M170,555 H1140 M215,600 V305" fill="none" stroke="#{template.muted}" stroke-width="2"/>')
        p.append(f'<path d="M225,540 C370,540 420,340 560,340 S800,540 940,420 S1070,340 1130,330" '
                 f'fill="none" stroke="#{template.accent}" stroke-width="7"/>')
        for x,y in [(560,340),(940,420)]:p.append(f'<circle cx="{x}" cy="{y}" r="10" fill="#{template.secondary}"/>')
        words('过程',380,624,32,center=True);words('关系',710,624,32,center=True);words('结果',1050,624,32,center=True)
    p.append('</svg>');return ''.join(p)
