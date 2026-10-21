from dataclasses import replace
import math
from xml.etree import ElementTree as ET

import pytest

from zhijiang.course_styles import contrast
from zhijiang.presentation_templates import get_template
from zhijiang.story_svg import outline_port, visual_svg


def graph(shape):
    return {'representation':'relationship','items':[
        {'label':'Sender','caption':'','shape':shape,'sentence_id':1},
        {'label':'Receiver','caption':'','shape':shape,'sentence_id':2}],
        'links':[{'source':1,'target':2,'label':'Forward','kind':'directed','sentence_id':1},
                 {'source':2,'target':1,'label':'Return','kind':'directed','sentence_id':2}],
        'context':'','context_sentence_id':1}


@pytest.mark.parametrize('shape',['process','data','concept','decision','document'])
def test_reciprocal_edges_keep_distinct_lanes_heads_and_labels(shape):
    svg,boxes=visual_svg(graph(shape),get_template('paper'),1130,490)
    r=ET.fromstring(svg);ns={'s':'http://www.w3.org/2000/svg'}
    paths=r.findall('s:path[@marker-end]',ns)
    assert len(paths)==2 and paths[0].get('d')!=paths[1].get('d')
    import re
    values=[[float(v) for v in re.findall(r'-?\d+(?:\.\d+)?',p.get('d'))] for p in paths]
    assert values[0][0]<values[0][2] and values[1][0]>values[1][2]
    assert abs(values[0][1]-values[1][1])>=20
    labels={n.text:n for n in r.findall('s:text',ns)}
    assert abs(float(labels['Forward'].get('y'))-float(labels['Return'].get('y')))>40
    assert all(0<float(n.get('x'))<1130 and 0<float(n.get('y'))<490 for n in labels.values())


@pytest.mark.parametrize('shape',['process','data','concept','decision','document'])
def test_offset_connector_ends_on_original_outline(shape):
    a=(20,30,300,150);b=(500,30,300,150)
    x,y=outline_port(a,b,shape,offset=(0,18))
    assert y==pytest.approx(123)
    if shape=='concept':
        assert ((x-170)/150)**2+((y-105)/75)**2==pytest.approx(1)
    elif shape=='data':assert x==pytest.approx(320-(y-30)/150*36)
    elif shape=='decision':assert abs(x-170)/150+abs(y-105)/75==pytest.approx(1)
    else:assert x==pytest.approx(320)


def test_pale_decorative_color_does_not_make_semantic_text_invisible():
    template=replace(get_template('paper'),id='generated',background='F0F4F8',panel='FFFFFF',
        ink='1A202C',accent='0066CC',secondary='E0E5EC')
    assert contrast(template.secondary,template.panel)<2
    svg,_=visual_svg(graph('data'),template,1130,490)
    root=ET.fromstring(svg);ns={'s':'http://www.w3.org/2000/svg'}
    for node in root.findall('s:text',ns):
        for background in (template.panel,template.background):
            assert contrast(node.get('fill').lstrip('#'),background)>=4.5
    boundaries=root.findall('s:polygon',ns)
    assert all(contrast(n.get('stroke').lstrip('#'),template.panel)>=3 for n in boundaries)


def test_unconnected_edge_labels_keep_complete_aliases_without_extra_entities():
    from zhijiang.literal_course import normalize_edge_labels
    visual=graph('data')
    visual['items'] += [{'label':'Forward (消息)','caption':'','sentence_id':1},
                        {'label':'Return (回执)','caption':'','sentence_id':2}]
    normalized,changes=normalize_edge_labels(visual)
    assert len(visual['items'])==4 and len(normalized['items'])==2
    assert [v['label'] for v in normalized['links']]==['Forward (消息)','Return (回执)']
    assert len(changes)==2 and all(v['full_label'] for v in changes)
    visual['items'][2]['caption']='This box contains its own source explanation.'
    normalized,_=normalize_edge_labels(visual)
    assert len(normalized['items'])==3


def test_edge_label_normalization_remaps_only_node_indices_and_keeps_source_binding():
    from zhijiang.literal_course import normalize_edge_labels
    visual=graph('process')
    visual['items'].insert(0,{'label':'Forward','caption':'','sentence_id':1})
    for edge in visual['links']:
        edge['source']+=1;edge['target']+=1
    normalized,_=normalize_edge_labels(visual)
    assert [(e['source'],e['target'],e['sentence_id']) for e in normalized['links']]==[(1,2,1),(2,1,2)]
