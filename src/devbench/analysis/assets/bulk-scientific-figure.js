/* Saved bulk RNA geometry and linked inspection. Display controls never refit an analysis. */
(function(root){
  'use strict';
  const MAX_GENES=100000, MAX_SAMPLES=1000, MAX_VALUES=1000000;
  const TITLES={ma:'MA · abundance and effect',volcano:'Volcano · effect and adjusted p',dispersion:'Mean–dispersion diagnostics',pca:'Sample PCA',paired_expression:'Expression by biological unit',expression:'Expression by sample',pvalue_histogram:'Raw p-value distribution'};
  const COLORS=['#0072B2','#D55E00','#009E73','#CC79A7','#E69F00','#56B4E9','#332288','#66757f'];
  const finite=v=>typeof v==='number'&&Number.isFinite(v), text=v=>typeof v==='string'&&v.length>0&&v.length<=1000;
  const fail=s=>{throw Error(s);}, esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'}[c]));
  const number=v=>v==null?'Not recorded':Math.abs(v)>0&&(Math.abs(v)<.001||Math.abs(v)>=100000)?v.toExponential(3):Number(v.toPrecision(5)).toLocaleString('en-US');
  const nullable=v=>v===null||finite(v), copy=v=>JSON.parse(JSON.stringify(v));
  function createExportMenu(document){
    const element=document.createElement('details');
    element.className='bf-export-menu';
    const summary=document.createElement('summary');
    summary.textContent='Export';
    const options=document.createElement('div');
    options.className='bf-export-options';
    element.append(summary,options);
    function close(){element.open=false;summary.focus();}
    options.addEventListener('click',event=>{
      if(event.target.closest('button:not(:disabled)'))close();
    });
    element.addEventListener('keydown',event=>{
      if(event.key!=='Escape'||!element.open)return;
      event.preventDefault();
      close();
    });
    element.addEventListener('focusout',event=>{
      if(element.open&&!element.contains(event.relatedTarget))element.open=false;
    });
    return {element,append:(...actions)=>options.append(...actions)};
  }
  function prepareWorkspace(w){
    if(!w||w.format!=='devbench.bulk-plot-workspace/1'||!w.source||!Array.isArray(w.genes)||w.genes.length>MAX_GENES||!Array.isArray(w.samples)||w.samples.length>MAX_SAMPLES||!w.availability)fail('A bounded, saved bulk plot workspace is required.');
    const ids=new Set(),sampleIds=new Set();let values=0;
    for(const s of w.samples){if(!text(s.sample_id)||sampleIds.has(s.sample_id)||!finite(s.size_factor)||s.size_factor<=0)fail('Sample identity or saved normalization factor is invalid.');sampleIds.add(s.sample_id);}
    for(const g of w.genes){
      if(!text(g.gene_id)||ids.has(g.gene_id)||!text(g.hypothesis_id)||![g.effect,g.p_value,g.q_value,g.mean_normalized_count].every(nullable))fail('Gene identities or saved numeric values are invalid.');ids.add(g.gene_id);
      if([g.p_value,g.q_value].some(v=>v!==null&&(v<0||v>1))||(g.p_value!==null&&g.q_value!==null&&g.q_value<g.p_value))fail('Saved p/q values are inconsistent.');
      if(!Array.isArray(g.warnings)||g.warnings.some(s=>typeof s!=='string')||!text(g.estimate_status))fail('Saved gene status and warning evidence are required.');
      if(g.raw_counts!==null){if(!Array.isArray(g.raw_counts)||g.raw_counts.length!==w.samples.length||g.raw_counts.some(v=>!finite(v)||v<0))fail('Gene expression is not aligned with the saved sample membership.');values+=g.raw_counts.length;}
      if(g.interval!==null&&g.interval!==undefined&&(!finite(g.interval.lower)||!finite(g.interval.upper)||g.interval.lower>g.interval.upper))fail('Saved uncertainty interval is invalid.');
      if(g.dispersion&&['raw','trend','final'].some(k=>g.dispersion[k]!=null&&!finite(g.dispersion[k])))fail('Saved dispersion is not finite.');
    }
    if(values>MAX_VALUES)fail('Saved sample expression exceeds the bounded display budget.');
    const scores=w.pca?.scores||[],seen=new Set();
    for(const s of scores){if(!sampleIds.has(s.sample_id)||seen.has(s.sample_id)||!finite(s.pc1)||!finite(s.pc2))fail('PCA scores are not uniquely aligned to saved samples.');seen.add(s.sample_id);}
    if(scores.length&&scores.length!==w.samples.length)fail('PCA score membership differs from the saved samples.');
    return w;
  }
  function bounds(rows){
    let x0=Infinity,x1=-Infinity,y0=Infinity,y1=-Infinity;
    for(const r of rows)if(finite(r.x)&&finite(r.y)){x0=Math.min(x0,r.x);x1=Math.max(x1,r.x);y0=Math.min(y0,r.y);y1=Math.max(y1,r.y);if(r.interval){y0=Math.min(y0,r.interval.lower);y1=Math.max(y1,r.interval.upper);}}
    if(x0===Infinity)return [0,1,0,1];
    const dx=(x1-x0)||Math.max(1,Math.abs(x0)*.1),dy=(y1-y0)||Math.max(1,Math.abs(y0)*.1);
    const result=[x0-dx*.06,x1+dx*.06,y0-dy*.08,y1+dy*.08];if(!result.every(finite)||!finite(result[1]-result[0])||!finite(result[3]-result[2]))fail('The saved coordinate range cannot be represented accurately.');return result;
  }
  function pairConnections(rows){const grouped=new Map();for(const r of rows)if(r.pairId&&finite(r.x)&&finite(r.y)){if(!grouped.has(r.pairId))grouped.set(r.pairId,[]);grouped.get(r.pairId).push(r);}return [...grouped.values()].filter(rs=>rs.length===2&&rs[0].x!==rs[1].x).map(rs=>rs.map(r=>r.key));}
  function finish(model){const range=bounds(model.rows);if(model.barWidth){range[0]=0;range[1]=1;range[2]=0;range[3]=Math.max(1,range[3]);}return {...model,bounds:range,connections:model.connections||[]};}
  function project(workspace,kind,geneId){
    const w=prepareWorkspace(workspace);if(!Object.hasOwn(TITLES,kind))fail('Unknown bulk plot.');
    const available=w.availability[kind]||{status:'unavailable',reason:'This diagnostic was not recorded.'};
    const model={kind,title:TITLES[kind],entityKind:'gene',source:w.source,workspace:w,rows:[],axes:{x:'',y:''},referenceLines:[],limitations:w.limitations||[],unavailable:available.status!=='available'?available.reason:null};
    const effect=w.effect_scale==='log2_relative_expression'?'Effect · log2 relative expression':'Effect · saved input scale';
    if(kind==='pca'){
      const scores=new Map((w.pca?.scores||[]).map(s=>[s.sample_id,s])),variance=w.pca?.explained_variance_ratio||[];model.entityKind='sample';model.axes={x:'PC1 ('+(finite(variance[0])?number(variance[0]*100)+'% variance':'variance unavailable')+')',y:'PC2 ('+(finite(variance[1])?number(variance[1]*100)+'% variance':'variance unavailable')+')'};
      model.rows=w.samples.map(s=>{const score=scores.get(s.sample_id);return {id:s.sample_id,key:s.sample_id,x:score?.pc1??null,y:score?.pc2??null,group:s.condition||'Condition not recorded',status:score?'estimated':'unavailable',warnings:[],reason:score?null:w.pca?.reason||'PCA score not recorded',sample:s,pairId:w.pairing?.status==='paired'?s.pair_id:null};});
      model.connections=pairConnections(model.rows);model.transform=w.pca?.transform;return finish(model);
    }
    if(kind==='paired_expression'||kind==='expression'){
      const gene=w.genes.find(g=>g.gene_id===geneId),contrast=w.effect_definition||{},reference=contrast.reference_condition,comparison=contrast.comparison_condition;
      model.entityKind='sample';model.geneId=geneId||null;model.axes={x:'Condition',y:'Normalized count · ln(1 + count)'};model.xLabels=[reference,comparison];
      if(!gene){model.unavailable='Select an exact gene to inspect its saved sample expression.';return finish(model);}
      model.title=gene.gene_id+' · expression by biological unit';
      if(!gene.raw_counts){model.unavailable='Sample expression was not retained for this gene.';return finish(model);}
      model.rows=w.samples.map((s,i)=>{const x=s.condition===reference?0:s.condition===comparison?1:null,value=gene.raw_counts[i]/s.size_factor;return {id:s.sample_id,key:s.sample_id,x,y:Math.log1p(value),group:s.condition||'Condition not recorded',status:gene.estimate_status,warnings:gene.warnings,reason:x===null?'Condition is outside the recorded contrast':null,sample:s,raw:gene.raw_counts[i],normalized:value,pairId:w.pairing?.status==='paired'?s.pair_id:null};});
      model.connections=pairConnections(model.rows);return finish(model);
    }
    if(kind==='pvalue_histogram'){
      model.entityKind='bin';model.axes={x:'Raw p-value · 20 equal-width bins',y:'Genes per bin'};const counts=Array(20).fill(0);let missing=0;
      for(const g of w.genes)if(finite(g.p_value))counts[Math.min(19,Math.floor(g.p_value*20))]++;else missing++;
      model.rows=counts.map((n,i)=>({id:'bin_'+i,key:'bin_'+i,x:(i+.5)/20,y:n,group:'Raw p',status:'estimated',warnings:[],reason:null}));model.missingProbabilityCount=missing;model.sourceGeneCount=w.genes.length;model.barWidth=.05;model.limitsNote='A p-value histogram alone does not establish calibration.';return finish(model);
    }
    model.axes=kind==='ma'?{x:'Mean normalized count · ln(1 + mean)',y:effect}:kind==='volcano'?{x:effect,y:'Adjusted p (q) · −log10(q)'}:{x:'Mean normalized count · log10(mean)',y:'Final dispersion · log10(α)'};
    if(kind==='ma')model.referenceLines=[{axis:'y',value:0,label:'Zero effect'}];
    if(kind==='volcano')model.referenceLines=[{axis:'x',value:0,label:'Zero effect'}];
    model.rows=w.genes.map(g=>{
      let x=null,y=null,reason=g.missing_reason||null,interval=null;
      if(kind==='ma'){x=g.mean_normalized_count!==null&&g.mean_normalized_count>=0?Math.log1p(g.mean_normalized_count):null;y=g.effect;interval=g.interval;}
      else if(kind==='volcano'){x=g.effect;y=g.q_value!==null&&g.q_value>0?-Math.log10(g.q_value):null;if(g.q_value===0)reason='Recorded q is zero; −log10(0) has no finite coordinate. No floor substituted.';}
      else{x=g.mean_normalized_count>0?Math.log10(g.mean_normalized_count):null;y=g.dispersion?.final>0?Math.log10(g.dispersion.final):null;}
      if((!finite(x)||!finite(y))&&!reason)reason='Required saved value is missing or incompatible with the displayed transform.';
      const warnings=g.warnings||[];return {id:g.gene_id,key:g.hypothesis_id,x,y,interval,group:warnings.length?'Recorded warning':g.estimate_status==='estimated'?'Estimated':'Unavailable',status:g.estimate_status,warnings,reason,gene:g};
    });return finish(model);
  }
  function transformed(v,transform){if(!finite(v))return null;if(transform==='identity')return v;if(transform==='natural_log1p'&&v>=0)return Math.log1p(v);if(transform==='log10'&&v>0)return Math.log10(v);if(transform==='negative_log10'&&v>0)return -Math.log10(v);fail('Invalid saved coordinate or unsupported axis transform.');}
  function prepareNumeric(record,payload){
    const data=payload?.data;
    if(record?.format!=='devbench.figure/2'||payload?.format!=='devbench.figure-data/2'||data?.format!=='devbench.numeric-plot-source/2'||record.plot_kind!==data.plot_kind||!Array.isArray(data.rows)||data.rows.length>MAX_GENES||!Array.isArray(data.membership)||data.membership.length!==data.rows.length)fail('A bounded numeric figure/2 with exact membership is required.');
    const axes={};for(const k of ['x','y']){const a=data.axes?.[k];if(!a||!text(a.label)||!text(a.unit)||!['identity','natural_log1p','negative_log10','log10'].includes(a.transform))fail('Numeric axis semantics are required.');axes[k]=a.label+' · '+a.unit+(a.transform==='identity'?'':' · '+a.transform);}
    const seen=new Set(),curve=data.entity_kind==='gene_time';
    const rows=data.rows.map((r,i)=>{const identity=curve?[r.entity_id,r.coordinate_id,r.group_id]:r.entity_id,key=JSON.stringify(identity);if(!text(r.entity_id)||seen.has(key)||JSON.stringify(data.membership[i])!==key)fail('Numeric figure membership is ambiguous.');seen.add(key);
      const coords={};for(const k of ['x','y']){const c=r[k];if(!c||!['exact','missing','censored'].includes(c.status)||(c.status==='exact'&&!finite(c.value))||(c.status!=='exact'&&c.value!==null))fail('Numeric coordinate status and value disagree.');coords[k]=c.status==='exact'?transformed(c.value,data.axes[k].transform):null;}
      let interval=null;if(r.interval){const lo=transformed(r.interval.lower,data.axes.y.transform),hi=transformed(r.interval.upper,data.axes.y.transform);if(lo===null||hi===null)fail('Invalid saved interval.');interval={...r.interval,lower:Math.min(lo,hi),upper:Math.max(lo,hi)};}
      return {id:r.entity_id,key,...coords,interval,group:r.group_id,status:coords.x===null||coords.y===null?'unavailable':'estimated',warnings:[],reason:coords.x===null||coords.y===null?'Missing or censored saved coordinate':null,coordinateId:r.coordinate_id};});
    const model={kind:data.plot_kind,title:record.figure_id,entityKind:data.entity_kind,axes,rows,source:{...payload.source_ref,analysis_binding:record.analysis_binding},referenceLines:[],limitations:data.limitations||[]};
    if(curve){const grouped=new Map();for(const r of rows){const k=JSON.stringify([r.id,r.group]);if(!grouped.has(k))grouped.set(k,[]);grouped.get(k).push(r.key);}model.connections=[...grouped.values()];}
    if(data.plot_kind==='histogram')model.barWidth=.05;
    return finish(model);
  }
  function validBounds(b){return Array.isArray(b)&&b.length===4&&b.every(finite)&&b[0]<b[1]&&b[2]<b[3];}
  function visibleRows(model,state={},scope='current'){
    if(!['complete','current'].includes(scope))fail('Choose a complete or current-view export.');
    const b=scope==='complete'?model.bounds:state.bounds||model.bounds;if(!validBounds(b))fail('Viewport bounds must be finite and increasing.');
    const counts={total:model.rows.length,displayed:0,unplottable:0,filtered:0,cropped:0},reasons={},rows=[];
    for(const r of model.rows){if(!finite(r.x)||!finite(r.y)){counts.unplottable++;const why=r.reason||'Missing coordinate';reasons[why]=(reasons[why]||0)+1;continue;}
      if(scope==='current'&&((state.status==='warnings'&&!r.warnings.length)||(state.status==='estimated'&&r.status!=='estimated')||(state.status==='selected'&&r.id!==state.selectedId)||(state.group&&state.group!=='all'&&r.group!==state.group))){counts.filtered++;continue;}
      if(r.x<b[0]||r.x>b[1]||r.y<b[2]||r.y>b[3]){counts.cropped++;continue;}rows.push(r);
    }counts.displayed=rows.length;return {rows,bounds:b.slice(),coverage:counts,omissionReasons:reasons};
  }
  function exportSnapshot(model,state={},scope='complete'){
    const view=visibleRows(model,state,scope);
    return {format:'devbench.bulk-plot-view/1',scope,plot_kind:model.kind,title:model.title,entity_kind:model.entityKind,source:copy(model.source||{}),axes:copy(model.axes),bounds:view.bounds,
      effect_definition:copy(model.workspace?.effect_definition||null),biological_unit:model.workspace?.biological_unit||null,availability:{status:model.unavailable?'unavailable':'available',reason:model.unavailable||null},limitations:copy(model.limitations||[]),pairing:copy(model.workspace?.pairing||null),
      filters:scope==='complete'?{status:'all',group:'all'}:{status:state.status||'all',group:state.group||'all'},selected_id:state.selectedId||null,coverage:view.coverage,undrawn_reasons:view.omissionReasons,
      source_gene_count:model.sourceGeneCount??model.workspace?.genes.length??null,missing_probability_count:model.missingProbabilityCount??null,
      displayed_ids:view.rows.map(r=>r.id),displayed_keys:view.rows.map(r=>r.key),display_reference_lines:copy(model.referenceLines||[]),analysis_refit:false,descriptive_projection:true,scientific_validity_established:false};
  }
  function ticks(min,max,count=5){const raw=(max-min)/Math.max(1,count-1),power=10**Math.floor(Math.log10(raw)),fraction=raw/power,step=(fraction<1.5?1:fraction<3?2:fraction<7?5:10)*power;if(!finite(step)||step<=0)return [];const result=[];for(let i=Math.ceil(min/step);i<=Math.floor(max/step)&&result.length<50;i++)result.push(Number((i*step).toPrecision(12)));return result;}
  function colors(model){return new Map([...new Set(model.rows.map(r=>r.group))].sort().map((g,i)=>[g,g==='Recorded warning'?'#b26a00':COLORS[i%COLORS.length]]));}
  function geometry(model,state,scope,width=920,height=620){const view=visibleRows(model,state,scope),b=view.bounds,m={left:86,right:28,top:72,bottom:112},pw=width-m.left-m.right,ph=height-m.top-m.bottom;
    return {...view,width,height,m,pw,ph,sx:x=>m.left+(x-b[0])/(b[1]-b[0])*pw,sy:y=>m.top+(b[3]-y)/(b[3]-b[2])*ph};}
  function toSVG(model,state={},scope='complete'){
    const g=geometry(model,state,scope),snapshot=exportSnapshot(model,state,scope),color=colors(model),parts=[],add=s=>parts.push(s),label=(x,y,s,extra='')=>add('<text x="'+x+'" y="'+y+'" '+extra+'>'+esc(s)+'</text>');
    add('<svg xmlns="http://www.w3.org/2000/svg" width="920" height="620" viewBox="0 0 920 620" role="img"><title>'+esc(model.title)+'</title><metadata>'+esc(JSON.stringify(snapshot))+'</metadata><rect width="920" height="620" fill="#fff"/><g font-family="DejaVu Sans, sans-serif" font-size="11" fill="#243744">');
    label(26,27,model.title,'font-size="18" font-weight="600"');label(26,47,(model.source?.kind==='synthetic'?'Synthetic verification · ':'')+(model.workspace?.effect_definition?model.workspace.effect_definition.comparison_condition+' minus '+model.workspace.effect_definition.reference_condition+' · ':'')+(scope==='complete'?'Complete saved population':'Current view · filters and zoom retained'));
    for(const x of (model.xLabels?[0,1].filter(x=>x>=g.bounds[0]&&x<=g.bounds[1]):ticks(g.bounds[0],g.bounds[1]))){add('<path d="M '+g.sx(x)+' '+g.m.top+' V '+(g.height-g.m.bottom)+'" stroke="#e8edef"/>');label(g.sx(x),g.height-g.m.bottom+21,model.xLabels?.[x]||number(x),'text-anchor="middle"');}
    for(const y of ticks(g.bounds[2],g.bounds[3])){add('<path d="M '+g.m.left+' '+g.sy(y)+' H '+(g.width-g.m.right)+'" stroke="#e8edef"/>');label(g.m.left-10,g.sy(y)+4,number(y),'text-anchor="end"');}
    add('<defs><clipPath id="plot-area"><rect x="'+g.m.left+'" y="'+g.m.top+'" width="'+g.pw+'" height="'+g.ph+'"/></clipPath></defs><g clip-path="url(#plot-area)">');
    for(const l of model.referenceLines||[])if(l.value>=g.bounds[l.axis==='x'?0:2]&&l.value<=g.bounds[l.axis==='x'?1:3])add('<path d="'+(l.axis==='x'?'M '+g.sx(l.value)+' '+g.m.top+' V '+(g.height-g.m.bottom):'M '+g.m.left+' '+g.sy(l.value)+' H '+(g.width-g.m.right))+'" stroke="#82919a" stroke-dasharray="4 4"/>');
    const chosen=new Map(g.rows.map(r=>[r.key,r]));
    for(const keys of model.connections||[]){const rows=keys.map(k=>chosen.get(k));if(rows.every(Boolean))add('<polyline points="'+rows.map(r=>g.sx(r.x)+','+g.sy(r.y)).join(' ')+'" fill="none" stroke="#9ba8ae" stroke-width="1.2"/>');}
    for(const r of g.rows){const x=g.sx(r.x),y=g.sy(r.y),c=color.get(r.group),selected=r.id===state.selectedId;
      if(r.interval){const lo=g.sy(r.interval.lower),hi=g.sy(r.interval.upper);add('<path data-uncertainty="'+esc(r.interval.kind||'recorded')+'" d="M '+x+' '+lo+' V '+hi+' M '+(x-3)+' '+lo+' H '+(x+3)+' M '+(x-3)+' '+hi+' H '+(x+3)+'" fill="none" stroke="#82939d" stroke-width="1"/>');}
      if(model.barWidth){const zero=g.sy(0),bar=Math.abs(g.sx(r.x+model.barWidth/2)-g.sx(r.x-model.barWidth/2));add('<rect x="'+(x-bar/2)+'" y="'+y+'" width="'+Math.max(0,bar-1)+'" height="'+Math.max(0,zero-y)+'" fill="'+c+'"/>');}
      else if(r.warnings.length)add('<path d="M '+x+' '+(y-4)+' l 4 7 h -8 Z" fill="'+c+'" stroke="'+(selected?'#172f40':c)+'"/>');
      else add('<circle cx="'+x+'" cy="'+y+'" r="'+(selected?5:2.5)+'" fill="'+c+'" fill-opacity="'+(selected?1:.62)+'"'+(selected?' stroke="#172f40" stroke-width="2"':'')+'/>');
    }
    const pinned=g.rows.find(r=>r.id===state.selectedId);if(pinned){const x=g.sx(pinned.x),y=g.sy(pinned.y),name=pinned.id.length>40?pinned.id.slice(0,37)+'…':pinned.id,w=Math.max(40,name.length*7+16),lx=Math.max(g.m.left+4,Math.min(g.width-g.m.right-w-4,x+14)),ly=Math.max(g.m.top+4,Math.min(g.height-g.m.bottom-24,y-35));add('<path d="M '+x+' '+y+' L '+lx+' '+(ly+16)+'" stroke="#3b5566"/><rect x="'+lx+'" y="'+ly+'" width="'+w+'" height="22" rx="3" fill="#fff" stroke="#c2d2db"/>');label(lx+8,ly+15,name,'font-weight="600"');}
    add('</g>');label((g.m.left+g.width-g.m.right)/2,551,model.axes.x,'text-anchor="middle"');label(0,0,model.axes.y,'text-anchor="middle" transform="translate(21 280) rotate(-90)"');
    let lx=26;for(const [name,c]of color){add('<rect x="'+lx+'" y="568" width="8" height="8" fill="'+c+'"/>');label(lx+13,576,name);lx+=Math.min(220,30+name.length*6);if(lx>760)break;}
    label(26,598,g.coverage.displayed.toLocaleString()+' shown / '+g.coverage.total.toLocaleString()+' source points · '+g.coverage.unplottable+' unplottable · '+g.coverage.filtered+' filtered · '+g.coverage.cropped+' outside view');
    label(26,614,model.unavailable?'Unavailable: '+model.unavailable:'Saved source '+(model.source?.sha256||'identity in metadata').slice(0,32)+' · display only; scientific validity not established');add('</g></svg>');return parts.join('');
  }
  function toCSV(model){const cell=v=>{let s=v==null?'':typeof v==='object'?JSON.stringify(v):String(v);if(typeof v!=='number'&&/^[\s]*[=+@-]/.test(s))s="'"+s;return '"'+s.replace(/"/g,'""')+'"';};const columns=['id','key','x','y','group','status','reason','warnings','interval','gene'];return columns.join(',')+'\r\n'+model.rows.map(r=>columns.map(k=>cell(r[k])).join(',')).join('\r\n');}
  function mount(container,options){
    if(options?.scientificWorkspace){
      if(!root.ScientificPlotWorkbench)fail('The scientific plotting workbench is unavailable in this build.');
      return root.ScientificPlotWorkbench.mount(container,options);
    }
    if(!container?.ownerDocument)fail('A plot container is required.');
    const w=options.workspace?prepareWorkspace(options.workspace):null,numeric=w?null:prepareNumeric(options.record,options.payload),doc=container.ownerDocument;
    container.__scientificFigure?.destroy();
    const make=(tag,cls,value)=>{const e=doc.createElement(tag);if(cls)e.className=cls;if(value!==undefined)e.textContent=String(value);return e;};
    const button=(label,action,cls='secondary')=>{const b=make('button',cls,label);b.type='button';b.addEventListener('click',action);return b;};
    const field=(label,input)=>{const wrap=make('label','bf-field');wrap.append(make('span','',label),input);return wrap;};
    const select=(label,choices)=>{const e=make('select');e.setAttribute('aria-label',label);for(const [value,name]of choices){const opt=make('option','',name);opt.value=value;e.append(opt);}return e;};
    const shell=make('section','bf-workspace'),heading=make('header','bf-heading'),title=make('h2','','Bulk RNA results'),subtitle=make('p','bf-subtitle'),badge=make('span','bf-badge',(w?.source||numeric?.source)?.kind==='synthetic'?'Synthetic verification':(w?.source||numeric?.source)?.execution_outcome==='scientific_failure'?'Saved result · scientific failure':'Saved analysis');
    const headText=make('div');headText.append(title,subtitle);heading.append(headText,badge);
    const toolbar=make('div','bf-toolbar'),kindPicker=select('Bulk plot',w?Object.entries(TITLES).filter(([key])=>key!==(w.pairing?.status==='paired'?'expression':'paired_expression')):[[numeric.kind,numeric.title]]),statusPicker=select('Display filter',[['all','All recorded points'],['warnings','Recorded warnings'],['estimated','Estimated points'],['selected','Pinned identity only']]),groupPicker=select('Group filter',[['all','All groups']]);
    const search=make('input');search.type='search';search.placeholder='Exact gene ID';search.setAttribute('aria-label','Find gene or sample');search.autocomplete='off';
    const searchBox=make('div','bf-search'),matches=make('div','bf-search-results');matches.setAttribute('role','list');searchBox.append(field('Find and pin',search),matches);
    toolbar.append(field('Diagnostic',kindPicker),searchBox,field('Display',statusPicker),field('Group',groupPicker));
    const body=make('div','bf-body'),main=make('div','bf-main'),plotHead=make('div','bf-plot-heading'),plotTitle=make('h3'),tools=make('div','bf-navigation'),modePicker=select('Pointer mode',[['pan','Pan / click to inspect'],['box','Drag to zoom']]);
    const plot=make('div','bf-plot'),canvas=make('canvas'),unavailable=make('p','bf-unavailable'),hover=make('p','bf-hover','Hover a point, then click to pin. Search and the saved table provide keyboard access.');
    canvas.setAttribute('role','img');canvas.tabIndex=0;canvas.setAttribute('aria-label','Bulk result plot. Use arrow keys to pan, plus/minus to zoom, zero to reset.');canvas.style.touchAction='none';
    const coverage=make('p','bf-coverage'),legend=make('div','bf-legend'),inspector=make('aside','bf-inspector');inspector.setAttribute('aria-label','Pinned scientific evidence');coverage.setAttribute('role','status');
    plot.append(canvas,unavailable);plotHead.append(plotTitle,tools);main.append(plotHead,plot,hover,legend,coverage);body.append(main,inspector);
    let focusButton=null;
    const fullscreenChanged=()=>{if(focusButton)focusButton.textContent=doc.fullscreenElement===shell?'Exit focus view':'Focus plot';render();};
    if(typeof shell.requestFullscreen==='function'){focusButton=button('Focus plot',async()=>{try{if(doc.fullscreenElement===shell)await doc.exitFullscreen();else await shell.requestFullscreen();}catch(_){hover.textContent='Fullscreen is unavailable here. Resize the Workbench canvas or use the complete export.';}});heading.append(focusButton);doc.addEventListener('fullscreenchange',fullscreenChanged);}
    const note=make('p','bf-scope','Display controls do not refit the analysis, recalculate p/q values or change the tested family.'),details=make('details','bf-table-details'),tableSummary=make('summary','','Saved point table'),tableHost=make('div','bf-table-wrap');details.append(tableSummary,tableHost);
    const exportBar=make('div','bf-export-bar'),exportScope=select('Export scope',[['complete','Complete saved population'],['current','Current view · filters + zoom']]);
    const exportMenu=createExportMenu(doc);
    exportBar.append(field('Publication export',exportScope),exportMenu.element);
    const limits=make('details','bf-limitations');limits.append(make('summary','','Data, transformations and limitations'));const limitText=make('div');limits.append(limitText);
    shell.append(heading,toolbar,body,note,exportBar,details,limits);container.replaceChildren(shell);
    const initial=w?Object.keys(TITLES).find(k=>w.availability[k]?.status==='available'&&!['paired_expression','expression'].includes(k))||'volcano':numeric.kind;
    const state={kind:initial,selectedGene:null,selectedSample:null,bounds:null,status:'all',group:'all',page:0,sort:'source'};kindPicker.value=initial;
    let model=null,disposed=false,frame=0,viewGrid=new Map(),drawnGeometry=null,drag=null,observer=null,lastRender=0;
    const urls=new Set(),source=w?.source||numeric?.source||{};
    const selectedId=()=>model?.entityKind==='sample'?state.selectedSample:state.selectedGene;
    const renderState=()=>({...state,selectedId:selectedId()});
    function setSelection(id){const geneChoice=['paired_expression','expression'].includes(state.kind)&&w?.genes.some(g=>g.gene_id===id);if(geneChoice){state.selectedGene=id;}else if(model.entityKind==='sample')state.selectedSample=id;else if(model.entityKind==='gene'||model.entityKind==='gene_time')state.selectedGene=id;else return;search.value=['paired_expression','expression'].includes(state.kind)?state.selectedGene||'':id;matches.replaceChildren();options.onSelection?.({entity_kind:geneChoice?'gene':model.entityKind,entity_id:id,source:copy(source),display_only:true});renderInspector();render();}
    function rebuild(){model=w?project(w,state.kind,state.selectedGene):numeric;state.bounds=model.bounds.slice();state.page=0;const groups=[...new Set(model.rows.map(r=>r.group))].sort();groupPicker.replaceChildren();for(const [value,name]of [['all','All groups'],...groups.map(g=>[g,g])]){const o=make('option','',name);o.value=value;groupPicker.append(o);}if(!groups.includes(state.group))state.group='all';groupPicker.value=state.group;search.placeholder=model.entityKind==='sample'&&!['paired_expression','expression'].includes(state.kind)?'Exact sample ID':'Exact gene ID';search.value=model.entityKind==='sample'&&!['paired_expression','expression'].includes(state.kind)?state.selectedSample||'':state.selectedGene||'';plotTitle.textContent=model.title;subtitle.textContent=(w?.effect_definition?w.effect_definition.comparison_condition+' minus '+w.effect_definition.reference_condition+' · ':'')+(w?w.genes.length.toLocaleString()+' genes · '+(w.samples.length?w.samples.length+' retained samples':'sample evidence unavailable')+(w.biological_unit?' · '+w.biological_unit.replaceAll('_',' '):''):'Saved numeric projection');title.textContent=w?'Bulk RNA results':model.title;renderInspector();renderLimits();renderLegend();render();}
    function renderLegend(){legend.replaceChildren();for(const [name,color]of colors(model)){const item=make('span','bf-legend-item'),swatch=make('span','bf-swatch');swatch.style.background=color;swatch.textContent=name==='Recorded warning'?'△':'';item.append(swatch,doc.createTextNode(name));legend.append(item);}if(model.connections.length)legend.append(make('span','bf-legend-item','Lines connect recorded biological pairs'));}
    function renderLimits(){limitText.replaceChildren();for(const line of [w?.effect_definition?JSON.stringify(w.effect_definition):null,model.transform,model.limitsNote,w?.pairing?.reason,...model.limitations].filter(Boolean))limitText.append(make('p','',line));limitText.append(make('p','','Source SHA-256: '+(source.sha256||'see retained figure record')));}
    function itemList(parent,items){const list=make('dl','bf-facts');for(const [label,value]of items)list.append(make('dt','',label),make('dd','',value==null?'Not recorded':String(value)));parent.append(list);}
    function renderInspector(){
      inspector.replaceChildren();inspector.append(make('span','bf-eyebrow','PINNED EVIDENCE'));
      if(model.entityKind==='sample'&&!['paired_expression','expression'].includes(state.kind)){
        const sample=w?.samples.find(s=>s.sample_id===state.selectedSample),row=model.rows.find(r=>r.id===state.selectedSample);inspector.append(make('h3','',state.selectedSample||'Choose a sample'));
        if(!row){inspector.append(make('p','bf-empty','Click a sample point or search its exact ID. Gene selection is retained separately.'));if(state.selectedGene)inspector.append(button('Return to '+state.selectedGene,()=>{state.kind='ma';kindPicker.value='ma';rebuild();}));return;}
        itemList(inspector,[['Sample',row.id],['Condition',sample?.condition||row.group],['Biological unit',sample?.unit_id],['Pair',sample?.pair_id],['Saved size factor',number(sample?.size_factor)],['X',number(row.x)],['Y',number(row.y)]]);if(row.reason)inspector.append(make('p','bf-warning',row.reason));return;
      }
      const gene=w?.genes.find(g=>g.gene_id===state.selectedGene),row=model.rows.find(r=>r.id===state.selectedGene);inspector.append(make('h3','',state.selectedGene||'Choose a gene'));
      if(!gene&&!row){inspector.append(make('p','bf-empty','Search an exact gene ID, click a point, or select a row in the saved table. Inspection stays pinned when you change plots.'));return;}
      if(gene){itemList(inspector,[['Saved status',gene.estimate_status],['Effect',number(gene.effect)],['Raw p',number(gene.p_value)],['Adjusted p (q)',number(gene.q_value)],['Mean normalized count',number(gene.mean_normalized_count)],['Interval',gene.interval?number(gene.interval.lower)+' to '+number(gene.interval.upper):null],['Dispersion · gene-wise',number(gene.dispersion?.raw)],['Dispersion · trend',number(gene.dispersion?.trend)],['Dispersion · final',number(gene.dispersion?.final)]]);
        if(gene.missing_reason)inspector.append(make('p','bf-warning',gene.missing_reason));for(const warning of gene.warnings)inspector.append(make('p','bf-warning',warning));
        if(gene.raw_counts&&w.samples.length){inspector.append(button('Inspect sample expression',()=>{state.kind=w.pairing?.status==='paired'?'paired_expression':'expression';kindPicker.value=state.kind;rebuild();}));const table=make('table','bf-counts'),header=make('tr');for(const s of ['Sample / condition','Raw','Normalized'])header.append(make('th','',s));table.append(header);w.samples.forEach((s,i)=>{const tr=make('tr');tr.append(make('td','',s.sample_id+' · '+(s.condition||'unknown')),make('td','',number(gene.raw_counts[i])),make('td','',number(gene.raw_counts[i]/s.size_factor)));table.append(tr);});const wrap=make('div','bf-counts-wrap');wrap.append(table);inspector.append(wrap,make('p','bf-small',w.pairing?.status==='paired'?'Pairing uses recorded biological unit IDs.':'Samples are shown without inferred pairing.'));}
        else inspector.append(make('p','bf-empty','Sample-level expression was not retained.'));
      }else itemList(inspector,[['Identity',row.id],['Group',row.group],['X',number(row.x)],['Y',number(row.y)],['Status',row.status],['Interval',row.interval?number(row.interval.lower)+' to '+number(row.interval.upper)+' ('+(row.interval.kind||'recorded')+')':null],['Unplotted reason',row.reason]]);
      const shown=visibleRows(model,renderState(),'current').rows,displayed=['paired_expression','expression'].includes(state.kind)?model.geneId===state.selectedGene&&shown.length>0:shown.some(r=>r.id===state.selectedGene);const visibility=make('p','bf-warning bf-pinned-visibility','Pinned gene is outside this view, filtered out, or has no finite coordinate. Its saved evidence is retained.');visibility.hidden=displayed;inspector.append(visibility);
    }
    function render(){if(disposed||frame)return;frame=(root.requestAnimationFrame||((fn)=>root.setTimeout(fn,0)))(()=>{frame=0;draw();if(details.open)renderTable();});}
    function draw(){
      if(disposed)return;const started=root.performance?.now?.()||Date.now(),width=Math.max(420,Math.round(plot.clientWidth||760)),height=Math.max(380,Math.min(560,width*.66)),g=geometry(model,renderState(),'current',width,height),ratio=Math.min(2,root.devicePixelRatio||1),ctx=canvas.getContext('2d');
      canvas.width=Math.round(width*ratio);canvas.height=Math.round(height*ratio);canvas.style.width='100%';canvas.style.height=height+'px';ctx.setTransform(ratio,0,0,ratio,0,0);ctx.fillStyle='#ffffff';ctx.fillRect(0,0,width,height);ctx.font='11px sans-serif';ctx.lineWidth=1;
      const label=(x,y,value,align='left')=>{ctx.fillStyle='#526572';ctx.textAlign=align;ctx.fillText(value,x,y);};
      for(const x of (model.xLabels?[0,1].filter(x=>x>=g.bounds[0]&&x<=g.bounds[1]):ticks(g.bounds[0],g.bounds[1]))){ctx.strokeStyle='#e7edf0';ctx.beginPath();ctx.moveTo(g.sx(x),g.m.top);ctx.lineTo(g.sx(x),height-g.m.bottom);ctx.stroke();label(g.sx(x),height-g.m.bottom+22,model.xLabels?.[x]||number(x),'center');}
      for(const y of ticks(g.bounds[2],g.bounds[3])){ctx.strokeStyle='#e7edf0';ctx.beginPath();ctx.moveTo(g.m.left,g.sy(y));ctx.lineTo(width-g.m.right,g.sy(y));ctx.stroke();label(g.m.left-10,g.sy(y)+4,number(y),'right');}
      label((g.m.left+width-g.m.right)/2,height-40,model.axes.x,'center');ctx.save();ctx.translate(18,(g.m.top+height-g.m.bottom)/2);ctx.rotate(-Math.PI/2);label(0,0,model.axes.y,'center');ctx.restore();
      ctx.save();ctx.beginPath();ctx.rect(g.m.left,g.m.top,g.pw,g.ph);ctx.clip();
      for(const l of model.referenceLines||[]){ctx.strokeStyle='#7f929d';ctx.setLineDash([4,4]);ctx.beginPath();if(l.axis==='x'){ctx.moveTo(g.sx(l.value),g.m.top);ctx.lineTo(g.sx(l.value),height-g.m.bottom);}else{ctx.moveTo(g.m.left,g.sy(l.value));ctx.lineTo(width-g.m.right,g.sy(l.value));}ctx.stroke();ctx.setLineDash([]);}
      const byKey=new Map(g.rows.map(r=>[r.key,r])),color=colors(model);viewGrid=new Map();
      for(const keys of model.connections||[]){const rows=keys.map(k=>byKey.get(k));if(rows.every(Boolean)){ctx.beginPath();rows.forEach((r,i)=>i?ctx.lineTo(g.sx(r.x),g.sy(r.y)):ctx.moveTo(g.sx(r.x),g.sy(r.y)));ctx.strokeStyle='#a3afb6';ctx.stroke();}}
      for(const r of g.rows){const x=g.sx(r.x),y=g.sy(r.y);ctx.fillStyle=color.get(r.group);ctx.globalAlpha=.68;
        if(r.interval){const lo=g.sy(r.interval.lower),hi=g.sy(r.interval.upper);ctx.strokeStyle='#82939d';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(x,lo);ctx.lineTo(x,hi);ctx.moveTo(x-3,lo);ctx.lineTo(x+3,lo);ctx.moveTo(x-3,hi);ctx.lineTo(x+3,hi);ctx.stroke();}
        if(model.barWidth){const bw=model.barWidth/(g.bounds[1]-g.bounds[0])*g.pw;ctx.fillRect(x-bw/2,y,Math.max(0,bw-1),Math.max(0,g.sy(0)-y));}
        else{ctx.beginPath();if(r.warnings.length){ctx.moveTo(x,y-4);ctx.lineTo(x+4,y+3);ctx.lineTo(x-4,y+3);ctx.closePath();}else ctx.arc(x,y,model.entityKind==='sample'?4.5:2.3,0,Math.PI*2);ctx.fill();}
        const key=Math.floor(x/24)+':'+Math.floor(y/24);if(!viewGrid.has(key))viewGrid.set(key,[]);viewGrid.get(key).push({x,y,row:r});
      }ctx.globalAlpha=1;
      for(const r of g.rows)if(r.id===selectedId()){const x=g.sx(r.x),y=g.sy(r.y);ctx.strokeStyle='#172f40';ctx.lineWidth=2;ctx.beginPath();ctx.arc(x,y,7,0,Math.PI*2);ctx.stroke();const name=r.id.length>40?r.id.slice(0,37)+'…':r.id,lw=Math.max(40,name.length*7+16),lx=Math.max(g.m.left+4,Math.min(width-g.m.right-lw-4,x+14)),ly=Math.max(g.m.top+4,Math.min(height-g.m.bottom-24,y-35));ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(x,y);ctx.lineTo(lx,ly+16);ctx.stroke();ctx.fillStyle='#fff';ctx.fillRect(lx,ly,lw,22);ctx.strokeStyle='#c2d2db';ctx.strokeRect(lx,ly,lw,22);ctx.fillStyle='#172f40';ctx.textAlign='left';ctx.font='600 12px sans-serif';ctx.fillText(name,lx+8,ly+15);}
      ctx.restore();drawnGeometry=g;const visibility=inspector.querySelector('.bf-pinned-visibility');if(visibility)visibility.hidden=['paired_expression','expression'].includes(state.kind)?model.geneId===state.selectedGene&&g.rows.length>0:g.rows.some(r=>r.id===state.selectedGene);
      const c=g.coverage;coverage.textContent=c.displayed.toLocaleString()+' shown / '+c.total.toLocaleString()+' source points · '+c.unplottable.toLocaleString()+' unplottable · '+c.filtered.toLocaleString()+' filtered · '+c.cropped.toLocaleString()+' outside view'+(model.missingProbabilityCount?' · '+model.missingProbabilityCount+' genes lack raw p':'');
      unavailable.hidden=!model.unavailable;unavailable.textContent=model.unavailable||'';tableSummary.textContent='Saved point table · '+model.rows.length.toLocaleString()+' rows, including missing values';lastRender=(root.performance?.now?.()||Date.now())-started;canvas.dataset.renderMs=lastRender.toFixed(2);canvas.dataset.sourceCount=String(model.rows.length);canvas.dataset.displaySampling='false';
    }
    function renderTable(){tableHost.replaceChildren();const nav=make('div','bf-table-nav'),sort=select('Sort saved rows',[['source','Source order'],['id','Identity'],['x','X value'],['y','Y value']]);sort.value=state.sort;sort.addEventListener('change',()=>{state.sort=sort.value;state.page=0;renderTable();});const rows=model.rows.slice();if(state.sort!=='source')rows.sort((a,b)=>state.sort==='id'?a.id.localeCompare(b.id):(a[state.sort]??Infinity)-(b[state.sort]??Infinity));const start=state.page*50;nav.append(field('Order',sort),button('Previous',()=>{state.page=Math.max(0,state.page-1);renderTable();}),make('span','',Math.min(start+1,rows.length)+'–'+Math.min(start+50,rows.length)+' of '+rows.length),button('Next',()=>{state.page=Math.min(Math.max(0,Math.ceil(rows.length/50)-1),state.page+1);renderTable();}));const table=make('table','bf-data-table'),head=make('tr');for(const name of ['Identity','X','Y','Status / reason'])head.append(make('th','',name));table.append(head);for(const r of rows.slice(start,start+50)){const tr=make('tr'),id=make('td');id.append(button(r.id,()=>setSelection(r.id),'bf-table-id'));tr.append(id,make('td','',number(r.x)),make('td','',number(r.y)),make('td','',r.reason||r.warnings.join('; ')||r.status));table.append(tr);}tableHost.append(nav,table);}
    function zoom(factor){const b=state.bounds,cx=(b[0]+b[1])/2,cy=(b[2]+b[3])/2,n=[cx+(b[0]-cx)*factor,cx+(b[1]-cx)*factor,cy+(b[2]-cy)*factor,cy+(b[3]-cy)*factor];if(validBounds(n)){state.bounds=n;render();}}
    function reset(){state.bounds=model.bounds.slice();render();}
    tools.append(modePicker,button('−',()=>zoom(1.35)),button('+',()=>zoom(.75)),button('Reset view',reset));
    const referenceDetails=make('details','bf-reference-controls'),referenceBody=make('div');referenceDetails.append(make('summary','','Display reference lines'),referenceBody);const effectLine=make('input'),qLine=make('input');for(const input of [effectLine,qLine]){input.type='number';input.min='0';input.step='any';input.placeholder='Not set';}qLine.max='1';referenceBody.append(field('|Effect| reference',effectLine),field('q reference',qLine),make('p','bf-small','Visual guides only. These are not a new hypothesis test or a calibrated discovery rule.'));main.append(referenceDetails);
    function updateReferences(){model=w?project(w,state.kind,state.selectedGene):prepareNumeric(options.record,options.payload);const value=Number(effectLine.value),q=Number(qLine.value);if(effectLine.value&&finite(value)&&value>=0&&['ma','volcano'].includes(state.kind))for(const sign of [-1,1])model.referenceLines.push({axis:state.kind==='ma'?'y':'x',value:sign*value,label:'User display reference'});if(qLine.value&&q>0&&q<=1&&state.kind==='volcano')model.referenceLines.push({axis:'y',value:-Math.log10(q),label:'User q display reference'});render();}
    effectLine.addEventListener('input',updateReferences);qLine.addEventListener('input',updateReferences);
    kindPicker.addEventListener('change',()=>{state.kind=kindPicker.value;rebuild();updateReferences();});statusPicker.addEventListener('change',()=>{state.status=statusPicker.value;renderInspector();render();});groupPicker.addEventListener('change',()=>{state.group=groupPicker.value;renderInspector();render();});details.addEventListener('toggle',()=>{if(details.open)renderTable();});
    function candidates(){return w?(model.entityKind==='sample'&&!['paired_expression','expression'].includes(state.kind)?w.samples.map(s=>({id:s.sample_id})):w.genes.map(g=>({id:g.gene_id,label:g.symbol||g.label||''}))):model.rows.map(r=>({id:r.id}));}
    function searchMatches(){const query=search.value.trim().toLowerCase();matches.replaceChildren();if(!query)return;const found=[];for(const item of candidates())if(item.id.toLowerCase().includes(query)||item.label?.toLowerCase().includes(query)){found.push(item);if(found.length===8)break;}for(const item of found){const b=button(item.id+(item.label?' · '+item.label:''),()=>{setSelection(item.id);if(['paired_expression','expression'].includes(state.kind))rebuild();},'bf-search-match');b.setAttribute('role','listitem');matches.append(b);}}
    search.addEventListener('input',searchMatches);search.addEventListener('keydown',event=>{if(event.key==='Escape'){matches.replaceChildren();return;}if(event.key!=='Enter')return;event.preventDefault();const query=search.value.trim(),exact=candidates().filter(v=>v.id===query||v.label&&v.label===query);if(exact.length===1){setSelection(exact[0].id);if(['paired_expression','expression'].includes(state.kind))rebuild();}else{hover.textContent=exact.length?'This label is ambiguous. Choose an exact identity.':'No exact match. Choose a matching identity from the list.';searchMatches();}});
    const local=event=>{const r=canvas.getBoundingClientRect();return [(event.clientX-r.left)*(drawnGeometry?.width||r.width)/r.width,(event.clientY-r.top)*(drawnGeometry?.height||r.height)/r.height];};
    function nearest(point){let result=null,distance=144;const bx=Math.floor(point[0]/24),by=Math.floor(point[1]/24);for(let x=bx-1;x<=bx+1;x++)for(let y=by-1;y<=by+1;y++)for(const p of viewGrid.get(x+':'+y)||[]){const d=(p.x-point[0])**2+(p.y-point[1])**2;if(d<distance){distance=d;result=p.row;}}return result;}
    canvas.addEventListener('pointerdown',event=>{if(event.button!==0||!drawnGeometry)return;const point=local(event),g=drawnGeometry;if(point[0]<g.m.left||point[0]>g.width-g.m.right||point[1]<g.m.top||point[1]>g.height-g.m.bottom)return;drag={start:point,last:point,bounds:state.bounds.slice()};canvas.setPointerCapture?.(event.pointerId);});
    canvas.addEventListener('pointermove',event=>{const point=local(event);if(drag){drag.last=point;if(modePicker.value==='pan'){const g=drawnGeometry,b=drag.bounds,dx=(point[0]-drag.start[0])/g.pw*(b[1]-b[0]),dy=(point[1]-drag.start[1])/g.ph*(b[3]-b[2]);state.bounds=[b[0]-dx,b[1]-dx,b[2]+dy,b[3]+dy];render();}return;}const r=nearest(point);hover.textContent=r?r.id+' · '+model.axes.x+': '+number(r.x)+' · '+model.axes.y+': '+number(r.y)+(r.warnings.length?' · recorded warning':''):'Hover a point, then click to pin. Ctrl / ⌘ + scroll to zoom.';});
    canvas.addEventListener('pointerup',event=>{if(!drag)return;const d=drag;drag=null;const moved=Math.hypot(d.last[0]-d.start[0],d.last[1]-d.start[1]);if(moved<4){const r=nearest(local(event));if(r)setSelection(r.id);}else if(modePicker.value==='box'){const g=drawnGeometry,b=d.bounds,dx=x=>b[0]+(x-g.m.left)/g.pw*(b[1]-b[0]),dy=y=>b[3]-(y-g.m.top)/g.ph*(b[3]-b[2]),x=[dx(d.start[0]),dx(d.last[0])].sort((a,b)=>a-b),y=[dy(d.start[1]),dy(d.last[1])].sort((a,b)=>a-b);if(validBounds([...x,...y]))state.bounds=[...x,...y];render();}canvas.releasePointerCapture?.(event.pointerId);});canvas.addEventListener('pointercancel',()=>{drag=null;});
    canvas.addEventListener('wheel',event=>{if(event.ctrlKey||event.metaKey){event.preventDefault();zoom(event.deltaY>0?1.15:.87);}},{passive:false});canvas.addEventListener('keydown',event=>{const b=state.bounds,dx=(b[1]-b[0])*.1,dy=(b[3]-b[2])*.1;if(['+','=','-','0','ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key))event.preventDefault();if(['+','='].includes(event.key))zoom(.75);else if(event.key==='-')zoom(1.35);else if(event.key==='0')reset();else if(event.key==='ArrowLeft'){state.bounds=[b[0]-dx,b[1]-dx,b[2],b[3]];render();}else if(event.key==='ArrowRight'){state.bounds=[b[0]+dx,b[1]+dx,b[2],b[3]];render();}else if(event.key==='ArrowUp'){state.bounds=[b[0],b[1],b[2]+dy,b[3]+dy];render();}else if(event.key==='ArrowDown'){state.bounds=[b[0],b[1],b[2]-dy,b[3]-dy];render();}});
    function download(blob,extension){const url=URL.createObjectURL(blob);urls.add(url);const link=make('a');link.href=url;link.download=(state.kind+'-'+exportScope.value+'.'+extension);doc.body.append(link);link.click();link.remove();root.setTimeout(()=>{URL.revokeObjectURL(url);urls.delete(url);},1000);}
    exportMenu.append(button('Vector SVG',()=>download(new Blob([toSVG(model,renderState(),exportScope.value)],{type:'image/svg+xml'}),'svg')),button('PNG · 2100 px',()=>{const svg=toSVG(model,renderState(),exportScope.value),url=URL.createObjectURL(new Blob([svg],{type:'image/svg+xml'})),image=new root.Image();urls.add(url);image.onload=()=>{if(disposed){URL.revokeObjectURL(url);urls.delete(url);return;}const out=make('canvas');out.width=2100;out.height=Math.round(2100*620/920);out.getContext('2d').drawImage(image,0,0,out.width,out.height);out.toBlob(blob=>{if(blob)download(blob,'png');});URL.revokeObjectURL(url);urls.delete(url);};image.onerror=()=>{URL.revokeObjectURL(url);urls.delete(url);hover.textContent='PNG export could not be encoded. Vector SVG and data exports remain available.';};image.src=url;}),button('View record JSON',()=>download(new Blob([JSON.stringify(exportSnapshot(model,renderState(),exportScope.value),null,2)],{type:'application/json'}),'json')),button('All saved rows CSV',()=>download(new Blob([toCSV(model)],{type:'text/csv;charset=utf-8'}),'csv')));
    if(options.exportPDF)exportMenu.append(button('Vector PDF',async()=>{try{const blob=await options.exportPDF({kind:state.kind,gene_id:state.selectedGene,scope:exportScope.value,view:exportSnapshot(model,renderState(),exportScope.value)});if(!disposed)download(blob,'pdf');}catch(error){hover.textContent=error.message;}}));
    const controller={destroy(){disposed=true;doc.removeEventListener('fullscreenchange',fullscreenChanged);if(doc.fullscreenElement===shell)doc.exitFullscreen().catch(()=>{});if(frame)(root.cancelAnimationFrame||root.clearTimeout)(frame);observer?.disconnect();for(const url of urls)URL.revokeObjectURL(url);urls.clear();container.replaceChildren();delete container.__scientificFigure;},getConfiguration:()=>exportSnapshot(model,renderState(),'current'),getSelection:()=>({gene_id:state.selectedGene,sample_id:state.selectedSample}),getPerformance:()=>({last_render_ms:lastRender,population_n:model.rows.length,display_sampling:false}),exportSVG:(scope='complete')=>toSVG(model,renderState(),scope),selectGene(id){if(!w?.genes.some(g=>g.gene_id===id))fail('Unknown saved gene.');state.selectedGene=id;if(['paired_expression','expression'].includes(state.kind))rebuild();else{renderInspector();render();}},selectPlot(kind){if(w&&!Object.hasOwn(TITLES,kind))fail('Unknown plot.');state.kind=kind;kindPicker.value=kind;rebuild();},reset,redraw:render};
    container.__scientificFigure=controller;observer=root.ResizeObserver?new root.ResizeObserver(render):null;observer?.observe(plot);rebuild();return controller;
  }
  const api={mount,createExportMenu,prepareWorkspace,project,prepareNumeric,exportSnapshot,toSVG,toCSV,visibleRows,ticks,geometry,colors,escapeXML:esc,formatNumber:number,limits:{genes:MAX_GENES,samples:MAX_SAMPLES,matrix_values:MAX_VALUES}};
  root.BulkScientificFigure=api;if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
