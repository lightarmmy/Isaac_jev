"use strict";
const DATA=JSON.parse(document.getElementById('deck-data').textContent),U=DATA.ui;
const stage=document.getElementById('stage'),toolbar=document.getElementById('toolbar'),notes=document.getElementById('notes'),overview=document.getElementById('overview');
const h=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const br=value=>h(value).replace(/\n/g,'<br>');
const text=(x,y,value,size=24,fill='#183536',anchor='start',weight=400)=>`<text x="${x}" y="${y}" font-size="${size}" fill="${fill}" text-anchor="${anchor}" font-weight="${weight}">${String(value).split('\n').map((line,i)=>`<tspan x="${x}" dy="${i?size*1.25:0}">${h(line)}</tspan>`).join('')}</text>`;
const defs=`<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#6d9181"/></marker><marker id="orange-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" fill="#d5754c"/></marker></defs>`;
const path=(d,color='#6d9181',dash='',arrow=true)=>`<path d="${d}" fill="none" stroke="${color}" stroke-width="2.5" ${dash?`stroke-dasharray="${dash}"`:''} ${arrow?'marker-end="url(#arrow)"':''}/>`;
function node(x,y,w,height,title,sub='',fill='#e2ece3',titleSize=23){return `<rect x="${x}" y="${y}" width="${w}" height="${height}" fill="${fill}" stroke="#b0c5b9"/>${text(x+w/2,y+32,title,titleSize,'#183536','middle',600)}${sub?text(x+w/2,y+62,sub,19,'#617473','middle'):''}`}
let svgSequence=0;
function svg(contents,width=1304,height=485,label='Diagram'){const n=++svgSequence,body=(defs+contents).replaceAll('id="arrow"',`id="arrow-${n}"`).replaceAll('id="orange-arrow"',`id="orange-arrow-${n}"`).replaceAll('url(#arrow)',`url(#arrow-${n})`).replaceAll('url(#orange-arrow)',`url(#orange-arrow-${n})`);return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${h(label)}">${body}</svg>`}
function coverFigure(label){
 let grid='';for(let i=0;i<9;i++)grid+=`<path d="M ${70+i*56} 65 V 389" stroke="#48615d" stroke-width="1"/>`;for(let j=0;j<7;j++)grid+=`<path d="M 70 ${65+j*54} H 574" stroke="#48615d" stroke-width="1"/>`;
 return svg(`<rect x="70" y="65" width="504" height="324" fill="#1f403e" stroke="#92b3a2" stroke-width="2"/>${grid}<rect x="275" y="55" width="82" height="21" fill="#183536" stroke="#92b3a2"/><rect x="72" y="206" width="20" height="49" fill="#183536" stroke="#92b3a2"/><rect x="352" y="173" width="55" height="56" fill="#6b8375"/><rect x="184" y="279" width="54" height="55" fill="#6b8375"/><path d="M 486 321 L 486 264 L 448 264 L 448 141 L 333 141" stroke="#82c8b0" stroke-width="3" fill="none" stroke-dasharray="6 5"/><circle cx="486" cy="321" r="16" fill="#89ccb9"/><circle cx="274" cy="197" r="16" fill="#d5754c"/><circle cx="330" cy="305" r="16" fill="#d5754c"/><circle cx="276" cy="258" r="6" fill="#e5b16e"/><circle cx="374" cy="305" r="6" fill="#e5b16e"/><circle cx="417" cy="305" r="6" fill="#e5b16e"/><path d="M 510 104 l 15 15 l -15 15 l -15 -15 z" fill="#f2ede0"/>`,640,456,label)
}
function architecture(s){const l=s.labels;return svg(
 `<rect x="0" y="0" width="546" height="445" fill="#e6ece3"/><rect x="770" y="0" width="534" height="445" fill="#eceee6"/>${text(26,35,l.local,22,'#617473')}${text(792,35,l.remote,22,'#617473')}`+
 node(105,65,361,85,l.game,l.gameSub)+node(105,185,361,85,l.hub,l.hubSub)+node(105,316,361,85,l.runtime,l.runtimeSub)+
 node(801,81,474,85,l.dsh,l.dshSub,'#dbe9df',24)+node(801,280,474,85,l.qwen,l.qwenSub,'#e8e4d8',23)+
 path('M 284 150 V 185')+path('M 284 270 V 316')+path('M 105 358 H 52 V 105 H 105')+text(39,238,l.input,18,'#617473','middle').replace('<text ','<text transform="rotate(-90 39 238)" ')+
 path('M 466 212 H 704 V 103 H 801')+text(621,194,l.events,18,'#617473','middle')+
 path('M 801 145 H 739 V 382 H 466')+text(617,410,l.goal,18,'#617473','middle')+
 path('M 466 250 H 660 V 302 H 801')+text(638,286,l.state,18,'#617473','middle')+
 path('M 801 344 H 600 V 342 H 466')+text(658,370,l.action,18,'#617473','middle')+
 text(650,479,l.guard,23,'#183536','middle',500),1304,485,s.title)}
function modelFigure(s){const l=s.labels;return svg(
 node(7,7,390,62,l.text,'','#eceee6',22)+node(7,103,390,81,l.frozen,l.cache,'#dcebe3',26)+
 node(7,218,390,62,l.embedding,'','#eceee6',21)+node(451,218,360,62,l.numeric,'','#efe2d5',21)+
 node(7,345,390,77,l.move,l.moveOut,'#dcebe3',25)+node(451,345,360,77,l.shoot,l.shootOut,'#dcebe3',25)+
 path('M 202 69 V 103')+path('M 202 184 V 218')+
 path('M 202 280 V 302 H 631 V 345')+path('M 202 302 V 345')+
 path('M 631 280 V 318 H 202 V 345','#b78c62')+path('M 631 318 V 345','#b78c62')+
 path('M 202 422 V 447 H 408', '#6d9181','',false)+path('M 631 422 V 447 H 408','#6d9181','',false)+
 text(408,476,l.action,24,'#183536','middle',600),820,485,s.title)}
function headFigure(s){
 const l=s.labels,counts=DATA.head;let shape='<rect x="0" y="111" width="488" height="366" fill="#ebefe7" stroke="#c1cec3"/><rect x="510" y="111" width="488" height="366" fill="#ebefe7" stroke="#c1cec3"/>';
 shape+=`<rect x="82" y="0" width="490" height="69" fill="#dcebe3" stroke="#b0c5b9"/>${text(327,27,l.backbone,23,'#183536','middle',600)}${text(327,53,l.pool,18,'#617473','middle')}`;
 shape+=`<rect x="651" y="0" width="340" height="69" fill="#efe2d5" stroke="#b0c5b9"/>${text(821,40,l.numeric,19,'#183536','middle',600)}`;
 shape+=path('M 327 69 V 83 H 127 V 161')+path('M 327 83 H 637 V 161');
 shape+=path('M 821 69 V 96 H 374 V 161','#b78c62')+path('M 821 96 H 881 V 161','#b78c62');
 for(const [x,title,out,label,total] of [[0,l.move,9,l.moveOut,counts.move_total],[510,l.shoot,5,l.shootOut,counts.shoot_total]]){
  shape+=text(x+244,139,title,23,'#183536','middle',600);
  shape+=`<rect x="${x+17}" y="160" width="220" height="87" fill="#fbfbf5" stroke="#c1cec3"/>${text(x+127,181,'LN(2048)\nFC(2048,128)\nGELU + LN(128)',18,'#183536','middle')}`;
  shape+=`<rect x="${x+264}" y="160" width="220" height="87" fill="#fbfbf5" stroke="#c1cec3"/>${text(x+374,197,`FC(${counts.numeric_size},128)\nGELU + LN(128)`,18,'#183536','middle')}`;
  shape+=path(`M ${x+127} 247 V 257 H ${x+244} V 261`)+path(`M ${x+374} 247 V 257 H ${x+244}`,'#b78c62','',false)+`<rect x="${x+113}" y="262" width="262" height="28" fill="#fbfbf5" stroke="#c1cec3"/>`+text(x+244,282,'Concat: 128 + 128 = 256',18,'#617473','middle')+path(`M ${x+244} 290 V 297`);
  shape+=`<rect x="${x+95}" y="297" width="299" height="62" fill="#dcebe3" stroke="#b0c5b9"/>${text(x+244,323,'FC(256,256)',22,'#183536','middle',600)}${text(x+244,347,'GELU + Dropout(0.1)',18,'#617473','middle')}`;
  shape+=path(`M ${x+244} 359 V 386`);
  shape+=`<rect x="${x+126}" y="386" width="236" height="62" fill="#dcebe3" stroke="#b0c5b9"/>${text(x+244,412,`FC(256,${out})`,22,'#183536','middle',600)}${text(x+244,436,label,18,'#617473','middle')}`;
  shape+=text(x+244,469,total.toLocaleString('en-US')+' params',18,'#617473','middle');
 }
 return svg(shape,1000,485,s.title);
}
function skillFigure(s){const l=s.labels;let drawing='';
 drawing+=node(0,0,510,79,l.protocol,l.protocolText,'#dcebe3',26)+node(0,107,510,79,l.tools,l.toolsText,'#ebefe7',24)+node(0,215,510,91,l.runtime,l.runtimeText,'#dcebe3',23);
 drawing+=path('M 255 79 V 107')+path('M 255 186 V 215');
 drawing+=text(0,344,l.loop,23,'#183536','start',600);
 for(const [i,label] of [l.observe,l.candidates,l.execute,l.wait].entries()){
  const x=i*132;drawing+=`<rect x="${x}" y="376" width="112" height="51" fill="#fbfbf5" stroke="#b0c5b9"/>${text(x+56,407,label,16,'#183536','middle')}`;
  if(i<3)drawing+=path(`M ${x+112} 402 H ${x+132}`);
 }
 drawing+=path('M 452 427 V 451 H 56 V 427','#6d9181','5 4');
 return svg(drawing,510,483,s.title);
}
function parametersTable(s){const l=s.labels,c=DATA.head;
 const rows=[
  [l.text,'LN(2048) + FC(2048,128) + GELU + LN(128)',c.text_per_tower,2],
  [l.numeric,`FC(${c.numeric_size},128) + GELU + LN(128)`,c.numeric_per_tower,2],
  [l.fusion,'FC(256,256) + GELU + Dropout(0.1)',c.fusion_per_tower,2],
  [l.move,'FC(256,9)',c.move_output,1],[l.shoot,'FC(256,5)',c.shoot_output,1],
  [l.auxiliary,'Embedding(45,16) + FC(528,128) + GELU + FC(128,35)',c.auxiliary,1]];
 return `<table class="parameter-table"><colgroup><col><col><col><col><col></colgroup><thead><tr>${s.columns.map((v,i)=>`<th class="${i>1?'num':''}">${h(v)}</th>`).join('')}</tr></thead><tbody>${rows.map(([name,layers,n,copies])=>`<tr><td>${h(name)}</td><td class="layer-formula">${h(layers)}</td><td class="num">${n.toLocaleString('en-US')}</td><td class="num">${copies}</td><td class="num">${(n*copies).toLocaleString('en-US')}</td></tr>`).join('')}<tr class="parameter-total"><td colspan="4">${h(l.total)}</td><td class="num">${c.total.toLocaleString('en-US')}</td></tr></tbody></table><p class="parameter-footnote">${h(l.activation)}</p>`;
}
function experimentTable(columns,full=false){return `<table class="${full?'full-table':'suite-table'}"><colgroup>${columns.map(()=>'<col>').join('')}</colgroup><thead><tr>${columns.map((c,i)=>`<th class="${i>1?'num':''}">${h(c)}</th>`).join('')}</tr></thead><tbody>${DATA.experiments.map(e=>`<tr class="row-${e.id.toLowerCase()}" data-experiment="${e.id}"><td class="experiment-id">${e.id}</td><td class="scheme">${h(e.scheme)}</td>${full?`<td class="num">${e.epoch}</td><td class="num">${e.loss.toFixed(4)}</td><td class="num">${e.move.toFixed(2)}%</td><td class="num">${e.shoot.toFixed(2)}%</td><td class="num">${e.stay.toFixed(2)}%</td>`:`<td class="num">${e.loss.toFixed(4)}</td><td class="num">${e.move.toFixed(2)}%</td><td class="num">${e.shoot.toFixed(2)}%</td>`}</tr>`).join('')}</tbody></table>`}
function recentResults(s){return `<div class="suite-layout"><div><table class="recent-table"><colgroup>${s.columns.map(()=>'<col>').join('')}</colgroup><thead><tr>${s.columns.map((v,i)=>`<th class="${i>1?'num':''}">${h(v)}</th>`).join('')}</tr></thead><tbody>${DATA.recentExperiments.map(e=>`<tr class="row-${e.id.toLowerCase()}" data-recent="${e.id}"><td class="experiment-id">${h(e.id)}</td><td>${h(e.scheme)}</td><td class="num">${e.move.toFixed(2)}%</td><td class="num">${e.shoot.toFixed(2)}%</td><td class="num">${e.stay.toFixed(2)}%</td></tr>`).join('')}</tbody></table><p class="recent-takeaway">${h(s.takeaway)}</p></div><div class="suite-side">${s.side.map(item=>`<div class="side-item"><div class="side-value">${h(item.value)}</div><div class="side-label">${br(item.label)}</div></div>`).join('')}</div></div>`}
function progressTimeline(s){return `<div class="progress-layout"><div class="progress-milestones">${s.milestones.map(m=>`<div class="milestone"><div class="milestone-version">${h(m.version)}</div><div class="milestone-dot" aria-hidden="true"></div><h2>${h(m.name)}</h2><p>${br(m.detail)}</p></div>`).join('')}</div><div class="progress-next"><h2>${h(s.futureTitle)}</h2><div class="progress-next-grid">${s.nextSteps.map(n=>`<div class="next-step"><h3>${h(n.title)}</h3><p>${br(n.detail)}</p></div>`).join('')}</div></div><p class="progress-goal">${h(s.goal)}</p></div>`}
function thanksReferences(s){return `<div class="thanks-layout"><h2>${h(s.referencesTitle)}</h2><ol class="thanks-citations">${s.citations.map(c=>`<li><div><h3>${h(c.name)}</h3><p>${h(c.description)}</p><div class="citation-links">${c.links.map(r=>`<a href="${h(r.url)}" target="_blank" rel="noopener">${h(r.label)}</a>`).join('')}</div></div></li>`).join('')}</ol></div>`}
function fireFigure(s){const l=s.labels;return svg(
 `<rect x="18" y="15" width="528" height="171" fill="#e8ece2" stroke="#bdccbf"/><path d="M 45 102 H 525" stroke="#d5754c" stroke-width="2.5" stroke-dasharray="6 5"/><path d="M 491 102 L 308 47 L 141 102" fill="none" stroke="#2f846e" stroke-width="3"/><circle cx="311" cy="102" r="24" fill="#efc9ae"/><path d="M 311 82 C 292 102 296 116 312 121 C 330 113 326 96 311 82" fill="#d5754c"/><circle cx="491" cy="102" r="12" fill="#2f846e"/><path d="M 141 90 l12 12 l-12 12 l-12 -12 z" fill="#7d9889"/>`+
 text(491,156,l.player,19,'#617473','middle')+text(141,156,l.item,19,'#617473','middle')+
 text(306,31,l.new,18,'#2f846e','middle')+text(314,165,l.old,18,'#bd6644','middle'),566,207,l.fire)}
function epochFigure(s){const l=s.labels;const x=e=>66+(e-1)/29*732,y=v=>252-(v-2.4)/1.6*205;let chart='';
 for(const value of [2.5,3,3.5,4])chart+=`<path d="M 66 ${y(value)} H 798" stroke="#d1d9d2"/>${text(51,y(value)+6,value.toFixed(1),18,'#617473','end')}`;
 for(const e of [1,7,13,20,30])chart+=text(x(e),283,e,18,'#617473','middle');
 chart+=`<path d="M ${DATA.history.map(p=>`${x(p.epoch)},${y(p.loss)}`).join(' L ')}" stroke="#5a8f7b" stroke-width="3" fill="none"/>`;
 for(const e of [4,13]){const row=DATA.history[e-1];chart+=`<circle cx="${x(e)}" cy="${y(row.loss)}" r="6" fill="${e===4?'#2f846e':'#d5754c'}"/>`+text(x(e)+12,y(row.loss)-16,`E${e}  ${row.loss.toFixed(3)}`,19,e===4?'#2f846e':'#d5754c')}
 chart+=text(69,25,l.loss,21,'#617473')+text(798,313,l.epoch,19,'#617473','end');
 return svg(chart,842,323,l.loss)
}
function content(s){switch(s.kind){
 case 'jev-input':{const l=s.labels;return `<div class="input-layout"><div class="prompt-panel"><h2>${h(l.example)}</h2><pre class="prompt-code">${h(s.exampleText)}</pre><p class="prompt-caption">${h(l.sketch)}</p></div><div class="input-details">${['rules','state','history','format'].map(key=>`<div class="input-detail"><h2>${h(l[key])}</h2><p>${br(l[key+'Text'])}</p></div>`).join('')}</div></div>`}
 case 'jev-architecture':{const l=s.labels;return `<div class="head-layout"><div class="head-figure">${headFigure(s)}</div><div class="head-side"><div class="head-count">${DATA.head.total.toLocaleString('en-US')}</div><p class="head-count-label">${h(l.parameters)}</p><div class="head-combos">45</div><p class="head-count-label">${h(l.combos)}</p><p class="head-sharing">${br(l.sharing)}</p><p class="head-auxiliary">${br(l.quality)}</p><p class="head-legend">${br(l.legend)}</p></div></div>`}
 case 'dsh-skill':{const l=s.labels;return `<div class="skill-layout"><div class="skill-figure">${skillFigure(s)}</div><div class="skill-catalog"><h2>${h(l.count)}</h2>${s.families.map(f=>`<div class="tool-family"><h3>${h(f.title)}</h3><p>${br(f.tools.replaceAll(' · ', ', '))}</p></div>`).join('')}<p class="skill-result">${h(l.control)}<br>${h(l.result)}</p></div></div>`}
 case 'improvements':return `<table class="improvement-table"><colgroup><col><col><col></colgroup><thead><tr>${s.columns.map(v=>`<th>${h(v)}</th>`).join('')}</tr></thead><tbody>${s.versions.map(v=>`<tr><td class="improvement-version">${v.id}</td><td class="improvement-change">${h(v.change)}</td><td class="improvement-detail">${br(v.details)}</td></tr>`).join('')}</tbody></table>`;
 case 'head-parameters':return parametersTable(s);
 case 'process':return `<div class="step-grid">${s.steps.map((step,i)=>`<div class="step"><span class="step-number">${String(i+1).padStart(2,'0')}</span><div><h2>${h(step.name)}</h2><p>${br(step.text)}</p></div></div>`).join('')}</div>`;
 case 'architecture':return architecture(s);
 case 'data-model':{const l=s.labels;return `<div class="data-layout"><div class="dataset"><div class="dataset-value">20,667</div><div class="dataset-label">${h(l.samples)}</div><div class="dataset-origin">${h(l.sessions)}</div><div class="split-bar"><span></span><span></span><span></span></div><div class="split-labels"><span>${h(l.train)}<b>14,075</b></span><span>${h(l.val)}<b>3,513</b></span><span>${h(l.test)}<b>3,079</b></span></div><p class="dataset-note">${br(l.outcome)}</p><p class="dataset-note">${br(l.timing)}</p></div><div class="model-figure">${modelFigure(s)}</div></div>`}
 case 'demo':return `<div class="video-box"><div class="video-placeholder"><div class="play-icon" aria-hidden="true">▷</div><h2>${h(U.slot)}</h2><p>${h(U.slotSub)}</p><button class="video-load">${h(U.load)}</button></div><video hidden controls playsinline preload="metadata" aria-label="${h(s.title)}"></video><button class="video-replace" hidden>${h(U.replace)}</button><button class="video-export" hidden>${h(U.exportVideo)}</button><input class="video-input" type="file" accept="video/*" hidden></div>`;
 case 'iterations':return `<div class="version-grid">${s.versions.map(v=>`<div class="version"><div class="version-code">${v.id}</div><div class="version-metric">${v.metric}</div><p class="version-metric-label">${h(v.metricLabel)}</p><h2>${h(v.heading)}</h2><ul class="plain-list">${v.items.map(i=>`<li>${h(i)}</li>`).join('')}</ul></div>`).join('')}</div>`;
 case 'suite':return `<div class="suite-layout">${experimentTable(s.columns)}<div class="suite-side">${s.side.map(item=>`<div class="side-item"><div class="side-value">${item.value}</div><div class="side-label">${br(item.label)}</div></div>`).join('')}</div></div>`;
 case 'recent-results':return recentResults(s);
 case 'progress-timeline':return progressTimeline(s);
 case 'thanks-references':return thanksReferences(s);
 case 'live':{const l=s.labels;return `<div class="live-layout"><div class="cases">${s.cases.map(c=>`<div class="case"><div class="case-top"><span class="case-room">${h(c.room)}</span><span class="case-detail">${h(c.metric)}</span></div><div class="hp-labels"><span>${h(l.before)}</span><strong>6 → ${c.hp}</strong></div><div class="hp-bar"><span class="hp-remain" style="width:${c.hp/6*100}%"></span><span class="hp-loss" style="width:${(6-c.hp)/6*100}%"></span></div><p class="case-movement">${h(c.movement)}</p></div>`).join('')}</div><div class="live-side"><h2>${h(l.candidate)}</h2><div class="candidate-code">${h(l.model)}</div><p class="live-conclusion">${br(l.conclusion)}</p><p class="live-note">${h(l.note)}</p><div class="hp-legend"><span><i></i>${h(l.retained)}</span><span><i></i>${h(l.lost)}</span></div></div></div>`}
 case 'reliability':{const l=s.labels;return `<div class="reliability-layout"><div><h2>${h(l.fire)}</h2><div class="fire-figure">${fireFigure(s)}</div><p class="repair-text">${br(l.fireText)}</p><div class="verification"><b>183</b><span>${h(l.tests)}</span></div></div><div><h2>${h(l.rpc)}</h2><div class="timing-bars">${[[l.model,'≈ 52 ms',52],[l.usual,'≈ 60 ms',60],[l.spike,'134–181 ms',181]].map(([name,value,ms])=>`<div class="timing-row"><div class="timing-label"><span>${h(name)}</span><span>${value}</span></div><div class="timing-track"><div class="timing-fill" style="width:${ms/190*100}%"></div></div></div>`).join('')}</div><p class="repair-text">${br(l.rpcText)}</p><div class="verification"><b>32 / 32</b><span>${h(l.match)}</span></div></div><p class="reliability-limit">${h(l.limit)}</p></div>`}
 case 'outlook':return `<div class="outlook-columns">${s.columns.map(c=>`<div class="outlook-column"><h2>${h(c.title)}</h2><ul class="plain-list">${c.items.map(i=>`<li>${h(i)}</li>`).join('')}</ul></div>`).join('')}</div><p class="closing">${h(s.closing)}</p>`;
 case 'full-results':return experimentTable(s.columns,true);
 case 'epoch-selection':{const l=s.labels;return `<div class="epoch-layout"><div><div class="epoch-chart">${epochFigure(s)}</div><div class="epoch-rows">${[4,13,30].map(e=>`<div class="epoch-stat">${h(l.epoch)} ${e}<strong>${DATA.history[e-1].loss.toFixed(4)}</strong>${h(l.stay)}<br>${DATA.history[e-1].stay.toFixed(2)}%</div>`).join('')}</div></div><div class="epoch-limits"><h2>${h(l.limits)}</h2><div class="limit-fact"><b>12</b>${h(l.boss)}</div><div class="limit-fact"><b>0</b>${h(l.charge)}</div><p class="epoch-note">${h(l.interpretation)}</p></div></div>`}
 case 'sources':return `<div class="source-columns">${s.groups.map(g=>`<div class="source-column"><h2>${h(g.title)}</h2>${g.refs.map(([file,label])=>`<div class="reference"><a href="../../${encodeURI(file)}" target="_blank" rel="noopener">${h(file)}</a><p>${h(label)}</p></div>`).join('')}</div>`).join('')}</div><p class="source-boundary">${h(s.boundary)}</p>`;
 default:return '';
}}
stage.innerHTML=DATA.slides.map((s,index)=>{
 if(s.kind==='cover')return `<section class="slide cover" data-index="${index}" data-id="${s.id}" aria-label="${h(s.title)}"><div class="cover-copy"><div class="cover-tag">${h(s.tag)}</div><h1>${h(s.title)}</h1><p class="cover-subtitle">${br(s.subtitle)}</p><p class="cover-caption">${h(s.caption)}</p></div><div class="cover-figure">${coverFigure(s.diagramLabel)}</div><div class="cover-diagram-caption">${h(s.diagramLabel)}</div><div class="cover-bottom"><span>The Binding of Isaac</span><span>${h(s.date)}</span></div></section>`;
 return `<section class="slide ${s.kind} ${s.appendix?'appendix':''}" data-index="${index}" data-id="${s.id}" aria-label="${h(s.title)}"><h1>${br(s.title)}</h1><p class="subtitle">${h(s.subtitle)}</p><div class="body">${content(s)}</div><footer><span class="source">${h(s.source)}</span><span class="page">${String(index+1).padStart(2,'0')}</span></footer></section>`
}).join('');
let index=0,appendix=false,notesOpen=false,overviewOpen=false,videoURL=null,toastTimer=null;
const slides=[...stage.querySelectorAll('.slide')];
function toast(message){const el=document.getElementById('toast');el.textContent=message;el.classList.add('visible');clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.classList.remove('visible'),2600)}
function fit(){const width=innerWidth-(notesOpen?(innerWidth<=850?330:410):0),height=innerHeight-(document.body.classList.contains('clean')?0:62),scale=Math.min(width/1440,height/810);stage.style.left=`${width/2}px`;stage.style.top=`${height/2}px`;stage.style.transform=`translate(-50%,-50%) scale(${scale})`}
function renderToolbar(){const filename=DATA.language==='zh'?'midterm.en.html':'midterm.zh-CN.html';toolbar.innerHTML=`<button data-do="prev" title="${h(U.previous)}" aria-label="${h(U.previous)}">←</button><span class="count">${index+1} / ${appendix?slides.length:DATA.mainCount}</span><button data-do="next" title="${h(U.next)}" aria-label="${h(U.next)}">→</button><button data-do="overview">${h(U.overview)}</button><button data-do="notes">${h(U.notes)}</button><button data-do="fullscreen">${h(U.fullscreen)}</button><button class="optional" data-do="appendix" aria-pressed="${appendix}">${h(U.appendix)}</button><button class="optional" data-do="print" title="${h(U.print)}">PDF</button><a href="${filename}#${DATA.slides[index].id}">${h(U.language)}</a>`}
function renderNotes(){const s=DATA.slides[index],refs=s.references||[];notes.hidden=!notesOpen;notes.innerHTML=`<button class="note-close" aria-label="${h(U.close)}">×</button><h2>${br(s.title)}</h2><p class="note-timing">${h(s.appendix?U.annex:U.timing)}${s.duration?` ${s.duration} s`:''}</p><div class="note-copy">${h(s.notes)}</div>${s.presenterCue?`<p class="note-cue">${h(s.presenterCue)}</p>`:''}${refs.length?`<p class="note-source-label">${h(U.sources)}</p><ul class="note-sources">${refs.map(r=>`<li><a href="${h(r.url.startsWith('http')?r.url:'../../'+r.url)}" target="_blank" rel="noopener">${h(r.label)}</a></li>`).join('')}</ul>`:''}`;notes.querySelector('.note-close').onclick=()=>toggleNotes()}
function show(next,updateHash=true){const max=appendix?slides.length:DATA.mainCount;next=Math.max(0,Math.min(max-1,next));if(index!==next)slides[index].querySelector('video')?.pause();index=next;slides.forEach((s,i)=>s.classList.toggle('active',i===index));if(updateHash)history.replaceState(null,'',`#${DATA.slides[index].id}`);renderToolbar();renderNotes();fit();document.title=`Isaac Jev — ${DATA.slides[index].title.replace(/\n/g,' ')}`;if(DATA.slides[index].kind==='demo')ensureEmbeddedVideo()}
function toggleNotes(){notesOpen=!notesOpen;renderNotes();fit()}
function toggleOverview(){overviewOpen=!overviewOpen;overview.hidden=!overviewOpen;if(overviewOpen){overview.innerHTML=`<button class="overview-close" aria-label="${h(U.close)}">×</button><h2>Isaac Jev</h2><div class="overview-grid">${DATA.slides.map((s,i)=>`<button class="overview-item ${s.appendix?'annex':''}" data-slide="${i}"><b>${String(i+1).padStart(2,'0')}　${h(s.appendix?U.annex:U.main)}</b><span>${h(s.title)}</span></button>`).join('')}</div>`;overview.querySelector('.overview-close').onclick=toggleOverview;overview.querySelectorAll('[data-slide]').forEach(b=>b.onclick=()=>{const next=+b.dataset.slide;if(next>=DATA.mainCount)appendix=true;toggleOverview();show(next)})}}
async function fullscreen(){try{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen()}catch(error){toast(DATA.language==='zh'?'浏览器不支持此全屏请求。':'The browser could not enter fullscreen.')}}
toolbar.addEventListener('click',event=>{const action=event.target.closest('[data-do]')?.dataset.do;if(action==='prev')show(index-1);if(action==='next')show(index+1);if(action==='overview')toggleOverview();if(action==='notes')toggleNotes();if(action==='fullscreen')fullscreen();if(action==='appendix'){appendix=!appendix;show(index)}if(action==='print')window.print()});
window.addEventListener('keydown',event=>{if(event.target.closest('input,textarea,video,[contenteditable="true"]')||event.ctrlKey||event.metaKey||event.altKey)return;const k=event.key.toLowerCase();if(overviewOpen){if(k==='escape'||k==='o')toggleOverview();return}if(['arrowright','arrowdown','pagedown',' '].includes(k)){event.preventDefault();show(index+1)}else if(['arrowleft','arrowup','pageup'].includes(k)){event.preventDefault();show(index-1)}else if(k==='home')show(0);else if(k==='end')show((appendix?slides.length:DATA.mainCount)-1);else if(k==='n')toggleNotes();else if(k==='o')toggleOverview();else if(k==='f')fullscreen();else if(k==='a'){appendix=!appendix;show(index)}else if(k==='h'){document.body.classList.toggle('clean');fit()}else if(k==='escape'&&notesOpen)toggleNotes()});

const videoBox=stage.querySelector('.video-box'),video=videoBox.querySelector('video'),videoInput=videoBox.querySelector('input');
const embeddedMedia=document.getElementById('demo-media'),videoExport=videoBox.querySelector('.video-export');
let selectedVideo=null,embeddedLoading=null,videoRevision=0;
function attachVideo(blob){
 if(videoURL)URL.revokeObjectURL(videoURL);
 videoURL=URL.createObjectURL(blob);video.src=videoURL;
 video.hidden=false;videoBox.querySelector('.video-placeholder').hidden=true;
 videoBox.querySelector('.video-replace').hidden=false;videoExport.hidden=false;video.load();
}
async function ensureEmbeddedVideo(){
 if(selectedVideo||videoURL||!embeddedMedia)return;
 if(embeddedLoading)return embeddedLoading;
 const revision=videoRevision;
 videoBox.querySelector('.video-placeholder h2').textContent=U.loadingVideo;
 videoBox.querySelector('.video-load').hidden=true;
 embeddedLoading=(async()=>{
  try{
   const encoded=embeddedMedia.textContent,parts=[],chunk=1024*1024;
   for(let offset=0;offset<encoded.length;offset+=chunk){
    const binary=atob(encoded.slice(offset,offset+chunk)),bytes=new Uint8Array(binary.length);
    for(let i=0;i<binary.length;i++)bytes[i]=binary.charCodeAt(i);
    parts.push(bytes);
    if(offset%(chunk*4)===0)await new Promise(resolve=>setTimeout(resolve,0));
   }
   if(revision===videoRevision)attachVideo(new Blob(parts,{type:embeddedMedia.dataset.mime||'video/mp4'}));
  }catch(error){
   videoBox.querySelector('.video-load').hidden=false;toast(U.videoError);
  }
 })();
 return embeddedLoading;
}
function readVideoPart(blob){
 return new Promise((resolve,reject)=>{
  const reader=new FileReader();reader.onload=()=>resolve(reader.result.slice(reader.result.indexOf(',')+1));
  reader.onerror=()=>reject(reader.error);reader.readAsDataURL(blob);
 });
}
async function exportVideoHtml(){
 if(!selectedVideo&&!embeddedMedia)return;
 videoExport.disabled=true;videoExport.textContent=U.exportingVideo;
 try{
  const source=selectedVideo;
  const meta=source?{filename:source.name,mime:source.type||'video/mp4',bytes:source.size}:DATA.video;
  const serialized=JSON.stringify({...DATA,video:meta}).replaceAll('<','\\u003c');
  const endScript='<'+'/script>';
  const parts=['<!doctype html><html lang="'+h(document.documentElement.lang)+'">'+document.head.outerHTML,
   '<body><main id="stage" aria-label="Presentation"></main><nav id="toolbar" aria-label="Presentation controls"></nav><aside id="notes" hidden></aside><div id="overview" hidden></div><div id="toast" role="status"></div>',
   '<script id="deck-data" type="application/json">'+serialized+endScript,
   '<script id="demo-media" type="application/octet-stream" data-mime="'+h(meta?.mime||'video/mp4')+'">'];
  if(source){
   const chunk=3*256*1024;
   for(let offset=0;offset<source.size;offset+=chunk)parts.push(await readVideoPart(source.slice(offset,offset+chunk)));
  }else parts.push(embeddedMedia.textContent);
  parts.push(endScript,'<script id="deck-runtime">'+document.getElementById('deck-runtime').textContent+endScript,'</body></html>');
  const downloadURL=URL.createObjectURL(new Blob(parts,{type:'text/html;charset=utf-8'})),anchor=document.createElement('a');
  anchor.href=downloadURL;anchor.download=DATA.language==='zh'?'midterm.zh-CN.html':'midterm.en.html';
  document.body.append(anchor);anchor.click();anchor.remove();
  setTimeout(()=>URL.revokeObjectURL(downloadURL),10000);
 }catch(error){toast(U.exportVideoError)}
 finally{videoExport.disabled=false;videoExport.textContent=U.exportVideo}
}
videoBox.querySelector('.video-load').onclick=()=>videoInput.click();videoBox.querySelector('.video-replace').onclick=()=>videoInput.click();
videoExport.onclick=exportVideoHtml;
videoInput.addEventListener('change',()=>{const file=videoInput.files[0];if(!file)return;videoRevision++;selectedVideo=file;attachVideo(file)});
video.addEventListener('error',()=>{video.hidden=true;videoBox.querySelector('.video-placeholder').hidden=false;videoBox.querySelector('.video-load').hidden=false;toast(U.videoError)});
window.addEventListener('resize',fit);window.addEventListener('hashchange',()=>fromHash());
document.addEventListener('fullscreenchange',()=>{document.body.classList.toggle('clean',Boolean(document.fullscreenElement));fit()});
function fromHash(){const hash=location.hash.slice(1),target=DATA.slides.findIndex(s=>s.id===hash);if(target>=0){if(target>=DATA.mainCount)appendix=true;show(target,false)}else if(/^\d+$/.test(hash)){const n=+hash-1;if(n>=DATA.mainCount)appendix=true;show(n,false)}else show(0,false)}
window.DECK={data:DATA,show:(n)=>{if(n>=DATA.mainCount)appendix=true;show(n)},get index(){return index},get appendix(){return appendix},toggleNotes,toggleOverview,fit};
fromHash();document.fonts.ready.then(fit);
