"""Build matching self-contained HTML decks from bilingual report content."""
import json
import math
from pathlib import Path
import struct
from html import escape
import argparse
import base64
import hashlib
import shutil

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--demo',type=Path,help='Embed this MP4 in both standalone decks; retain assets/demo.mp4 for rebuilds')
args=parser.parse_args()
media_path=HERE/'assets/demo.mp4'
media_info=None
if args.demo:
    source=args.demo.resolve(strict=True)
    if source.suffix.lower()!='.mp4':parser.error('The bundled demo must be an MP4')
    media_path.parent.mkdir(parents=True,exist_ok=True)
    if source!=media_path.resolve():shutil.copyfile(source,media_path)
    with media_path.open('rb') as stream:media_hash=hashlib.file_digest(stream,'sha256').hexdigest()
    media_info={'filename':source.name,'mime':'video/mp4','bytes':media_path.stat().st_size,'sha256':media_hash}
    (media_path.parent/'demo.json').write_text(json.dumps(media_info,ensure_ascii=False,indent=2),encoding='utf-8')
elif media_path.exists():
    media_info=json.loads((media_path.parent/'demo.json').read_text(encoding='utf-8'))
    with media_path.open('rb') as source:
        assert hashlib.file_digest(source,'sha256').hexdigest()==media_info['sha256']
    assert media_path.stat().st_size==media_info['bytes']

def choose(value,language):
    if isinstance(value,dict):
        if set(value)=={'zh','en'}:return value[language]
        return {key:choose(child,language) for key,child in value.items()}
    if isinstance(value,list):return [choose(child,language) for child in value]
    return value

data=json.loads((HERE/'content.bilingual.json').read_text(encoding='utf-8'))
if media_info:data['video']=media_info
results=json.loads((ROOT/'artifacts/combat-v006/results.json').read_text(encoding='utf-8'))['experiments']
for entry,result in zip(data['experiments'],results):
    assert entry['id']==result['id'].upper()
    test=result['test_on_observed_positive']
    entry.update(epoch=result['selected_epoch'],loss=round(result['validation']['stratified_nll'],4),
                 move=round(test['move_accuracy']*100,2),shoot=round(test['shoot_accuracy']*100,2),
                 stay=round(test['stay_fraction']*100,2))
model=ROOT/'artifacts/combat-v010-tactical/combat-v010-tactical-s01'
config=json.loads((model/'policy_config.json').read_text(encoding='utf-8'))
with (model/'head.safetensors').open('rb') as weights:
    header_size=struct.unpack('<Q',weights.read(8))[0]
    tensors=json.loads(weights.read(header_size))
counts={name:math.prod(tensor['shape']) for name,tensor in tensors.items() if name!='__metadata__'}
def count(prefix):return sum(n for name,n in counts.items() if name.startswith(prefix))
data['head']={'total':sum(counts.values()),'move_total':count('move_tower.')+count('move.'),
              'shoot_total':count('shoot_tower.')+count('shoot.'),
              'text_per_tower':count('move_tower.text.'),'numeric_per_tower':count('move_tower.numeric.'),
              'fusion_per_tower':count('move_tower.shared.'),'move_output':count('move.'),'shoot_output':count('shoot.'),
              'auxiliary':count('quality.')+count('quality_action.'),'numeric_size':config['numeric_size'],
              'embedding_size':config['embedding_size']}
assert data['head']['total']==config['trainable_parameters']==1002753
assert data['head']['move_total']+data['head']['shoot_total']+data['head']['auxiliary']==data['head']['total']
roots={'V008':'combat-v008-quality','V009':'combat-v009-feedback','V010':'combat-v010-tactical'}
for entry in data['recentExperiments']:
    name=roots[entry['id']];result=json.loads((ROOT/'artifacts'/name/(name+'-s01')/'result.json').read_text(encoding='utf-8'))
    entry.update(epoch=result['selected_epoch'],move=round(result['test']['move_accuracy']*100,2),
        shoot=round(result['test']['shoot_accuracy']*100,2),stay=round(result['test']['stay_fraction']*100,2))
assert sum(slide['duration'] for slide in data['slides'])==300
css=(HERE/'deck.css').read_text(encoding='utf-8')
js=(HERE/'deck.js').read_text(encoding='utf-8')
for language,langtag,name in [('zh','zh-CN','midterm.zh-CN.html'),('en','en','midterm.en.html')]:
    selected=choose(data,language);selected['language']=language
    encoded=json.dumps(selected,ensure_ascii=False).replace('</','<\/')
    title='中期报告' if language=='zh' else 'Midterm report'
    document=f'''<!doctype html>
<html lang="{langtag}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><title>Isaac Jev — {title}</title>
<style>{css}</style></head><body><main id="stage" aria-label="Presentation"></main>
<nav id="toolbar" aria-label="Presentation controls"></nav><aside id="notes" hidden></aside>
<div id="overview" hidden></div><div id="toast" role="status"></div>
<script id="deck-data" type="application/json">{encoded}</script>'''
    with (HERE/name).open('w',encoding='utf-8',newline='\n') as output:
        output.write(document)
        if media_info:
            output.write(f'<script id="demo-media" type="application/octet-stream" data-mime="{escape(media_info["mime"],quote=True)}">')
            with media_path.open('rb') as source:
                for block in iter(lambda:source.read(3*1024*1024),b''):
                    output.write(base64.b64encode(block).decode('ascii'))
            output.write('</script>')
        output.write(f'<script id="deck-runtime">{js}</script></body></html>')
    notes=[]
    for index,slide in enumerate(selected['slides'],1):
        heading=slide['title'].replace(chr(10),' ')
        refs='\n'.join(f"- [{r['label']}]({r['url'] if r['url'].startswith('http') else '../../'+r['url']})" for r in slide.get('references',[]))
        cue=slide.get('presenterCue','')
        notes.append(f"## {index:02d}. {heading}\n\n{slide['notes']}\n"+(f"\n*{cue}*\n" if cue else '')+(f"\n{'资料依据' if language=='zh' else 'Sources'}:\n\n{refs}\n" if refs else ''))
    (HERE/f'speaker-notes.{language}.md').write_text('\n'.join(notes),encoding='utf-8')
words=sum(len(s['notes']['en'].split()) for s in data['slides'][:data['mainCount']])
script_md=['# 中英对照讲稿 / Bilingual speaker script\n',
    f'主稿约 5 分钟，含约 40 秒 Demo。英文 {words} 词，适合约 125–135 词/分钟，并留出看图与换页的停顿。演讲任选一种语言，附录仅用于问答。\n',
    'The main talk targets five minutes including a 40-second demo. Read one language, with short pauses for diagrams and slide transitions. The appendices are for questions.\n']
sections=[]
for i,s in enumerate(data['slides'][:data['mainCount']],1):
    title_zh=choose(s['title'],'zh').replace('\n',' ');title_en=choose(s['title'],'en').replace('\n',' ')
    script_md.append(f'## P{i}. {title_en}\n\n{title_zh} · {s["duration"]} s\n')
    pairs=s.get('scriptPairs',[s['notes']]);rows=[]
    for j,p in enumerate(pairs,1):
        script_md.append(f'**English {j}**\n\n{p["en"]}\n\n**中文 {j}**\n\n{p["zh"]}\n')
        rows.append(f'<div class="pair"><p class="en">{escape(p["en"])}</p><p class="zh">{escape(p["zh"])}</p></div>')
    cue=s.get('presenterCue')
    cue_html=f'<p class="cue">{escape(cue["en"])}<br>{escape(cue["zh"])}</p>' if cue else ''
    if cue:script_md.append(f'*{cue["en"]}*\n\n*{cue["zh"]}*\n')
    refs=''.join(f'<li><a href="{escape(r["url"] if r["url"].startswith("http") else "../../"+r["url"],quote=True)}">{escape(r["label"])}</a></li>' for r in s.get('references',[]))
    refs_html=f'<details><summary>Sources / 资料依据</summary><ul>{refs}</ul></details>' if refs else ''
    sections.append(f'<section data-slide="{s["id"]}"><h2>P{i}. {escape(title_en)}</h2><p class="heading-zh">{escape(title_zh)} <span>{s["duration"]} s</span></p>'+''.join(rows)+cue_html+refs_html+'</section>')
(HERE/'speaker-script.bilingual.md').write_text('\n'.join(script_md),encoding='utf-8')
script_css='''*{box-sizing:border-box}body{max-width:1200px;margin:32px auto;padding:0 30px;color:#183536;background:#f5f3ee;font:18px/1.65 "Segoe UI","Microsoft YaHei",sans-serif}h1{font-size:30px;line-height:1.2;margin:0 0 16px}h2{font-size:24px;line-height:1.3;margin:0}header{padding-bottom:24px;border-bottom:2px solid #2f846e}header p{margin:8px 0;color:#617473}.controls{display:flex;gap:8px;margin:16px 0 0}button{font:inherit;border:1px solid #8ba697;background:transparent;color:#183536;padding:7px 12px;cursor:pointer}button:focus-visible,a:focus-visible{outline:3px solid #2f846e}section{padding:28px 0;border-bottom:1px solid #d1d9d2}.heading-zh{font-size:17px;color:#617473;margin:8px 0 16px}.heading-zh span{float:right}.pair{display:grid;grid-template-columns:1fr 1fr;gap:32px;break-inside:avoid}.pair p{margin:0 0 18px}.en{padding-right:12px}.zh{color:#465d57;border-left:1px solid #d1d9d2;padding-left:24px}.cue{font-size:16px;color:#936143;background:#f2ddd2;padding:12px 16px}details{font-size:14px;color:#617473}a{color:#2f846e}body.english .pair{grid-template-columns:1fr}body.english .zh{display:none}body.chinese .pair{grid-template-columns:1fr}body.chinese .en{display:none}body.chinese .zh{border:0;padding:0;color:#183536}@media(max-width:760px){body{padding:0 18px;font-size:17px}.pair{grid-template-columns:1fr;gap:0}.zh{border-left:3px solid #d1d9d2;padding-left:16px}h2{font-size:21px}.controls{flex-wrap:wrap}}@page{size:A4;margin:15mm}@media print{body{font-size:11pt;line-height:1.5;margin:0;padding:0;background:white}h1{font-size:18pt}h2{font-size:14pt}header{padding-bottom:12px}section{padding:14px 0}h2,.heading-zh{break-after:avoid}.controls,details{display:none}.pair{gap:18px}.zh{padding-left:14px}.cue{font-size:9pt;break-inside:avoid}}'''
script_html=f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Isaac Jev · Bilingual speaker script</title><style>{script_css}</style></head><body><header><h1>Isaac Jev · Speaker script<br>中英对照讲稿</h1><p>Five-minute talk including a 40-second demo. Read one language and pause briefly for diagrams.</p><p>约 5 分钟，含 40 秒 Demo；英文 {words} 词。面向未参与项目的听众，任选一种语言朗读。</p><div class="controls"><button onclick="document.body.className=''">Bilingual / 对照</button><button onclick="document.body.className='english'">English</button><button onclick="document.body.className='chinese'">中文</button><button onclick="window.print()">Print / 打印</button></div></header>{''.join(sections)}</body></html>'''
(HERE/'speaker-script.bilingual.html').write_text(script_html,encoding='utf-8')
print(json.dumps({'slides':len(data['slides']),'main_slides':data['mainCount'],'main_seconds':300,
                  'head_parameters':data['head']['total'],'english_script_words':words,
                  'embedded_video':media_info,'outputs':['midterm.zh-CN.html','midterm.en.html','speaker-script.bilingual.html','speaker-script.bilingual.md']},ensure_ascii=False))
