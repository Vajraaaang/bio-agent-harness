/* One scientific plot and a persistent, typed evidence inspector. No analysis execution. */
(function(root){
  'use strict';
  const P=root.ScientificPlotPanels||(typeof require==='function'?require(__dirname+'/scientific-plot-panels.js'):null);
  const B=()=>root.BulkScientificFigure||(typeof require==='function'?require(__dirname+'/bulk-scientific-figure.js'):null);
  const finite=n=>typeof n==='number'&&Number.isFinite(n),esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'}[c]));
  const fail=s=>{throw Error(s);},copy=v=>JSON.parse(JSON.stringify(v));
  const format=v=>v==null?'Not recorded':typeof v==='number'?B().formatNumber(v):typeof v==='object'?JSON.stringify(v):String(v);
  const svgNamespace='http://www.w3.org/2000/svg';
  const legendTags=new Set(['g','text','rect','circle','line','polygon','defs','pattern','path']);
  const legendAttributes=new Set(['x','y','x1','y1','x2','y2','width','height','cx','cy','r','d','points',
    'id','patternUnits','fill','stroke','stroke-width','stroke-linejoin','stroke-linecap','stroke-dasharray',
    'opacity','text-anchor','font-family','font-size','data-scientific-legend','data-legend-height',
    'data-legend-group','data-legend-variant']);
  const legendPaint=/^(?:none|#(?:[\da-f]{3}|[\da-f]{4}|[\da-f]{6}|[\da-f]{8}))$/i;
  const legendPattern=/^url\(#scientific-[\da-f]{64}(?:-[\da-f]+)+-legend-pattern-\d+\)$/;
  function appendLegend(svg,markup){
    const parsed=new root.DOMParser().parseFromString('<svg xmlns="'+svgNamespace+'">'+markup+'</svg>','image/svg+xml');
    const wrapper=parsed.documentElement,legend=wrapper.firstElementChild;
    if(wrapper.namespaceURI!==svgNamespace||wrapper.localName!=='svg'||wrapper.children.length!==1||legend.localName!=='g'){
      fail('Scientific legend markup is invalid.');
    }
    const doc=svg.ownerDocument;
    function copyMark(mark){
      if(mark.namespaceURI!==svgNamespace||!legendTags.has(mark.localName))fail('Unexpected scientific legend element.');
      const node=doc.createElementNS(svgNamespace,mark.localName);
      for(const {name,value,namespaceURI} of mark.attributes){
        if(namespaceURI||!legendAttributes.has(name))fail('Unexpected scientific legend attribute.');
        if((name==='fill'||name==='stroke')&&!legendPaint.test(value)&&!(name==='fill'&&legendPattern.test(value))){
          fail('Unexpected scientific legend paint.');
        }
        node.setAttribute(name,value);
      }
      if(mark.localName==='text'){
        if(mark.children.length)fail('Scientific legend labels must be text.');
        node.textContent=mark.textContent;
      }else for(const child of mark.children)node.append(copyMark(child));
      return node;
    }
    svg.append(copyMark(legend));
  }
  function frame(model,state={},scope='current',width=920,height=620,fit=false){
    const xLength=model.xLabels.reduce((n,x)=>Math.max(n,Array.from(x).length),0),yLength=model.yLabels.reduce((n,x)=>Math.max(n,Array.from(x).length),0),verticalX=model.xLabels.length&&(xLength>12||model.xLabels.length>10),maxYLines=Math.max(1,Math.ceil(yLength/24));const m={left:model.yLabels.length?Math.max(90,Math.min(24,yLength)*11+16):86,right:model.xLabels.length&&!verticalX?Math.max(28,xLength*11/2+8):28,top:72,bottom:model.xLabels.length?(verticalX?xLength*11+48:64):112};height=Math.max(height,m.top+m.bottom+Math.max(280,maxYLines*13+20));
    if(scope==='complete'||fit){const p=P.pixelPadding(model);width=Math.max(width,m.left+m.right+p.left+p.right+64);height=Math.max(height,m.top+m.bottom+p.top+p.bottom+64);}
    if(width>2304||height>4608)fail('Saved glyphs exceed the bounded readable layout. Use a focused current view or inspect the full source table.');
    let pw=width-m.left-m.right,ph=height-m.top-m.bottom;
    const bounds=(scope==='complete'||fit?P.paddedBounds(model,pw,ph):state.bounds||model.bounds).slice();
    if(bounds.length!==4||!bounds.every(finite)||bounds[0]>=bounds[1]||bounds[2]>=bounds[3])fail('Saved view bounds must be finite and increasing.');
    if(model.equalAspect){const ratio=(bounds[1]-bounds[0])/(bounds[3]-bounds[2]);if(pw/ph>ratio){const next=ph*ratio;m.left+=(pw-next)/2;m.right+=(pw-next)/2;pw=next;}else{const next=pw/ratio;m.top+=(ph-next)/2;m.bottom+=(ph-next)/2;ph=next;}}
    return {width,height,m,pw,ph,bounds,verticalX,maxYLines,rows:model.rows,sx:x=>m.left+(x-bounds[0])/(bounds[1]-bounds[0])*pw,sy:y=>m.top+(bounds[3]-y)/(bounds[3]-bounds[2])*ph};
  }
  function insideSelection(x,y,polygon) {
    let hit=false;
    for(let i=0,j=polygon.length-1;i<polygon.length;j=i++) {
      const a=polygon[i],b=polygon[j];
      const cross=(x-a[0])*(b[1]-a[1])-(y-a[1])*(b[0]-a[0]);
      if(Math.abs(cross)<1e-10&&x>=Math.min(a[0],b[0])&&x<=Math.max(a[0],b[0])&&y>=Math.min(a[1],b[1])&&y<=Math.max(a[1],b[1]))return true;
      if(((a[1]>y)!==(b[1]>y))&&x<(b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0])hit=!hit;
    }
    return hit;
  }
  function normalizedState(model,state){return {...state,selectedKey:state.selectedPanelId&&state.selectedPanelId!==model.kind?null:state.selectedKey};}
  function resolveSelection(model,state,pinned=null){
    const link=state.selectedLink,scoped=normalizedState(model,state),same=r=>link&&r.link?.kind===link.kind&&r.link?.id===link.id;
    let selected=link?model.rows.find(r=>same(r)&&r.key===scoped.selectedKey)||model.rows.find(same):model.rows.find(r=>r.key===scoped.selectedKey);
    if(selected)return {row:selected,in_current_panel:true};
    if(pinned&&(link?same(pinned):pinned.key===scoped.selectedKey))return {row:pinned,in_current_panel:false};
    return {row:null,in_current_panel:false};
  }
  function snapshot(model,state={},scope='complete'){
    if(!['complete','current'].includes(scope))fail('Choose complete or current-view scope.');
    state=normalizedState(model,state);const g=frame(model,state,scope),filtered=P.filteredMarks(model,state,scope),allowed=new Set(filtered.allowedKeys),contributing=new Set(filtered.marks.flatMap(item=>item.mark.keys));
    const coverage={source_rows:model.rows.length,displayed_rows:0,missing_rows:0,filtered_rows:0,outside_view_rows:0,rows_without_submitted_mark:0,submitted_marks:filtered.marks.length,omitted_marks:filtered.omittedMarks.length,truncated:false},displayed=[],missing={};
    for(const r of model.rows){if(r.status==='excluded'){coverage.missing_rows++;const reason=r.reason||'Coordinate unavailable';missing[reason]=(missing[reason]||0)+1;}if(!allowed.has(r.key)){coverage.filtered_rows++;continue;}if(!contributing.has(r.key))coverage.rows_without_submitted_mark++;if(!finite(r.x)||!finite(r.y))continue;if(r.x<g.bounds[0]||r.x>g.bounds[1]||r.y<g.bounds[2]||r.y>g.bounds[3]){coverage.outside_view_rows++;continue;}if(contributing.has(r.key)){coverage.displayed_rows++;displayed.push(r.key);}}
    return {format:'devbench.scientific-plot-view/1',panel_id:model.kind,capability:model.capability,scope,source:copy(model.source),axes:copy(model.scientificPanel.axes),bounds:g.bounds,
      filters:scope==='complete'?{status:'all',group:'all'}:{status:state.status||'all',group:state.group||'all'},selected_link:copy(state.selectedLink||null),selected_key:state.selectedKey||null,
      source_rows:copy(model.scientificPanel.rows),context_images:model.scientificPanel.marks.filter(m=>m.type==='image').map(m=>({role:m.role,source:copy(m.image.source),sha256:m.image.sha256,width:m.image.width,height:m.image.height,pixel_to_data:m.image.pixel_to_data.slice(),coordinate_unit:m.image.coordinate_unit,registration_id:m.image.registration_id,coordinate_system_id:m.image.coordinate_system_id})),displayed_keys:displayed,coverage,omitted_data_reasons:missing,omitted_marks:filtered.omittedMarks,
      availability:copy(model.scientificPanel.availability),annotations:model.scientificPanel.annotations.slice(),limitations:model.limitations.slice(),color_scale:copy(model.colorScale),size_scale:copy(model.sizeScale),x_categories:model.xLabels.slice(),y_categories:model.yLabels.slice(),
      coverage_note:'Missing status may overlap displayed/filter/crop counts; displayed requires membership in a submitted mark.',equal_aspect:Boolean(model.equalAspect),analysis_refit:false,scientific_validity_established:false};
  }
  function selectedDisplayed(model,state,scope='current'){const visible=new Set(snapshot(model,state,scope).displayed_keys),link=state.selectedLink,local=normalizedState(model,state);return model.rows.filter(r=>visible.has(r.key)&&(link?r.link?.kind===link.kind&&r.link?.id===link.id:r.key===local.selectedKey));}
  function categoryLines(value){const chars=Array.from(value),lines=[];for(let i=0;i<chars.length;i+=24)lines.push(chars.slice(i,i+24).join(''));return lines;}
  function ticks(model,axis,g){const labels=axis==='x'?model.xLabels:model.yLabels,min=g.bounds[axis==='x'?0:2],max=g.bounds[axis==='x'?1:3];if(labels.length){const visible=labels.map((label,index)=>({value:index,label})).filter(x=>x.value>=min&&x.value<=max),capacity=Math.max(1,Math.floor(axis==='x'?g.pw/(g.verticalX?18:Math.max(30,labels.reduce((n,x)=>Math.max(n,Array.from(x).length),0)*11+8)):g.ph/(g.maxYLines*13+5))),step=Math.max(1,Math.ceil(visible.length/capacity));return visible.filter((_,i)=>i%step===0);}return B().ticks(min,max,5).map(value=>({value,label:format(value)}));}
  function svg(model,state={},scope='complete'){
    const g=frame(model,state,scope),record=snapshot(model,state,scope),legendWidth=g.width-52;
    const legendHeight=P.legendHeight(model,{width:legendWidth,state,scope});
    const selectionLabel=state.selectedLink?state.selectedLink.kind+': '+state.selectedLink.id:state.selectedKey||null,selectionState=selectionLabel?(selectedDisplayed(model,state,scope).length?'drawn':'not drawn in this panel/view'):null;const notes=[...(selectionLabel?['Manual selection · '+selectionLabel+' · '+selectionState]:[]),...(model.unavailable?['Unavailable: '+model.unavailable]:[]),...model.scientificPanel.annotations,...model.limitations];
    const noteLines=notes.flatMap(s=>String(s).match(/.{1,135}(?:\s|$)|.{1,135}/g)||[]),height=g.height+60+legendHeight+noteLines.length*16,parts=[];
    const add=s=>parts.push(s),text=(x,y,value,extra='')=>add('<text x="'+x+'" y="'+y+'" '+extra+'>'+esc(value)+'</text>');
    add('<svg xmlns="http://www.w3.org/2000/svg" width="'+g.width+'" height="'+height+'" viewBox="0 0 '+g.width+' '+height+'" role="img"><title>'+esc(model.title)+'</title><metadata>'+esc(JSON.stringify(record))+'</metadata><rect width="'+g.width+'" height="'+height+'" fill="#fff"/><g font-family="DejaVu Sans,sans-serif" font-size="11" fill="#243744">');
    text(26,27,model.title,'font-size="18" font-weight="600"');text(26,47,(model.source.kind==='synthetic'?'Synthetic verification · ':'')+(scope==='complete'?'Complete saved population':'Current view · filters and bounds retained'));
    for(const t of ticks(model,'x',g)){add('<path d="M '+g.sx(t.value)+' '+g.m.top+' V '+(g.height-g.m.bottom)+'" stroke="#e8edef"/>');text(g.sx(t.value),g.height-g.m.bottom+20,t.label,g.verticalX?'text-anchor="end" transform="rotate(-90 '+g.sx(t.value)+' '+(g.height-g.m.bottom+20)+')"':'text-anchor="middle"');}
    for(const t of ticks(model,'y',g)){add('<path d="M '+g.m.left+' '+g.sy(t.value)+' H '+(g.width-g.m.right)+'" stroke="#e8edef"/>');const lines=model.yLabels.length?categoryLines(t.label):[t.label];lines.forEach((line,i)=>text(g.m.left-9,g.sy(t.value)+4+(i-(lines.length-1)/2)*13,line,'text-anchor="end"'));}
    add('<defs><clipPath id="science-plot-area"><rect x="'+g.m.left+'" y="'+g.m.top+'" width="'+g.pw+'" height="'+g.ph+'"/></clipPath></defs><g clip-path="url(#science-plot-area)">');
    for(const l of model.referenceLines){add('<path d="'+(l.axis==='x'?'M '+g.sx(l.value)+' '+g.m.top+' V '+(g.height-g.m.bottom):'M '+g.m.left+' '+g.sy(l.value)+' H '+(g.width-g.m.right))+'" stroke="#788c97" stroke-dasharray="4 4"/>');}
    add(P.svgMarks(model,g,normalizedState(model,state),scope));for(const r of selectedDisplayed(model,state,scope)){add('<circle data-selection-key="'+esc(r.key)+'" cx="'+g.sx(r.x)+'" cy="'+g.sy(r.y)+'" r="7" fill="none" stroke="#123c54" stroke-width="2"/>');}add('</g>');text((g.m.left+g.width-g.m.right)/2,g.height-29,model.axes.x,'text-anchor="middle"');text(0,0,model.axes.y,'text-anchor="middle" transform="translate(20 '+((g.m.top+g.height-g.m.bottom)/2)+') rotate(-90)"');
    add(P.svgLegend(model,{x:26,y:g.height,width:legendWidth,state,scope}));const c=record.coverage;let y=g.height+20+legendHeight;
    text(26,y,c.displayed_rows+' shown / '+c.source_rows+' source entries · '+c.missing_rows+' missing · '+c.filtered_rows+' filtered · '+c.outside_view_rows+' outside view');y+=17;
    text(26,y,'Source '+model.source.sha256+' · no model refitting');y+=17;
    for(const line of noteLines){text(26,y,line);y+=16;}add('</g></svg>');return parts.join('');
  }
  function csv(model){const fields=['id','key','entity_kind','x','y','group','status','reason','warnings','details','link'];const cell=v=>{let s=v==null?'':typeof v==='object'?JSON.stringify(v):String(v);if(typeof v!=='number'&&/^[\s]*[=+@-]/.test(s))s="'"+s;return '"'+s.replace(/"/g,'""')+'"';};return fields.join(',')+'\r\n'+model.rows.map(r=>fields.map(k=>cell(r[k])).join(',')).join('\r\n');}
  function mount(container,options){
    if(!P)fail('The scientific mark renderer is unavailable.');const workspace=P.prepareWorkspace(options.scientificWorkspace),doc=container.ownerDocument;
    container.__scientificFigure?.destroy();const make=(tag,cls,value)=>{const e=doc.createElement(tag);if(cls)e.className=cls;if(value!==undefined)e.textContent=String(value);return e;};
    const button=(label,fn,cls='secondary')=>{const e=make('button',cls,label);e.type='button';e.addEventListener('click',fn);return e;};
    function iconButton(label,path,handler,{dash,zoomLens}={}){
      const control=button('',handler,'secondary sp-icon-button');
      control.setAttribute('aria-label',label);
      control.title=label;
      const ns='http://www.w3.org/2000/svg';
      const svg=doc.createElementNS(ns,'svg');
      const attributes={viewBox:'0 0 24 24',width:'17',height:'17',fill:'none',stroke:'currentColor','stroke-width':'1.6','stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true'};
      for(const [key,value] of Object.entries(attributes))svg.setAttribute(key,value);
      const shape=doc.createElementNS(ns,'path');
      shape.setAttribute('d',path);
      if(dash)shape.setAttribute('stroke-dasharray',dash);
      if(zoomLens){
        const lens=doc.createElementNS(ns,'circle');
        lens.setAttribute('cx','11');
        lens.setAttribute('cy','11');
        lens.setAttribute('r','8');
        svg.append(lens);
      }
      svg.append(shape);
      control.append(svg);
      return control;
    }
    const select=(label,choices)=>{const e=make('select');e.setAttribute('aria-label',label);for(const [value,name]of choices){const o=make('option','',name);o.value=value;e.append(o);}return e;};
    const field=(label,input)=>{const e=make('label','bf-field');e.append(make('span','',label),input);return e;};
    const shell=make('section','bf-workspace sp-workspace'),heading=make('header','bf-heading'),headText=make('div');headText.append(make('h2','',workspace.title),make('p','bf-subtitle',workspace.panels.length+' saved scientific panels'));heading.append(headText,make('span','bf-badge',workspace.source.kind==='synthetic'?'Synthetic verification':'Saved scientific output'));
    const toolbar=make('div','bf-toolbar'),picker=select('Scientific plot',workspace.panels.map(p=>[p.id,p.title])),group=select('Scientific group',[['all','All groups']]),status=select('Scientific display filter',[['all','All source entries'],['warnings','Recorded warnings'],['recorded','Recorded values'],['excluded','Missing / excluded'],['selected','Pinned identity only']]);
    const search=make('input');search.type='search';search.placeholder='Exact gene, sample or entity ID';search.setAttribute('aria-label','Find scientific entity');const searchBox=make('div','bf-search'),matches=make('div','bf-search-results');searchBox.append(field('Find and pin',search),matches);
    toolbar.append(field('Plot',picker),searchBox,field('Display',status),field('Group',group));
    const body=make('div','bf-body'),main=make('div','bf-main'),plotHead=make('div','bf-plot-heading'),plotTitle=make('h3'),navigation=make('div','bf-navigation sp-header-navigation'),plot=make('div','bf-plot'),canvas=make('canvas'),empty=make('p','bf-unavailable'),hover=make('p','bf-hover','Hover or click a mark; exact IDs and the table provide keyboard access.'),legendHost=make('div','sp-legends'),counts=make('p','bf-coverage'),inspector=make('aside','bf-inspector');inspector.setAttribute('aria-label','Scientific evidence inspector');counts.setAttribute('role','status');canvas.setAttribute('role','img');canvas.setAttribute('aria-label','Scientific plot. Arrow keys pan, plus/minus zoom, zero resets.');canvas.tabIndex=0;canvas.style.touchAction='none';
    navigation.setAttribute('role','group');
    navigation.setAttribute('aria-label','Figure view controls');
    const toolbarHost=options.toolbarHost;
    (toolbarHost||heading).append(navigation);
    plot.append(canvas,empty);plotHead.append(plotTitle);main.append(plotHead,plot,hover,legendHost,counts);body.append(main,inspector);
    const tableDetails=make('details','bf-table-details'),tableTitle=make('summary',''),tableHost=make('div','bf-table-wrap');tableDetails.append(tableTitle,tableHost);
    const exports=make('div','bf-export-bar'),scope=select('Scientific export scope',[['complete','Complete saved population'],['current','Current view · filters + bounds']]);
    const exportMenu=B().createExportMenu(doc);
    exports.append(field('Publication export',scope),exportMenu.element);
    const information=make('details','bf-limitations'),notes=make('div');information.append(make('summary','','Units, calculation conventions and limitations'),notes);
    shell.append(heading,toolbar,body,make('p','bf-scope','Display changes preserve source values, biological units and tested families. Aggregate marks are omitted when their required members are filtered.'),exports,tableDetails,information);container.replaceChildren(shell);
    const state={panelId:options.panelId||workspace.panels[0].id,bounds:null,status:'all',group:'all',selectedLink:null,selectedKey:null,selectedPanelId:null,fullView:true,page:0,sort:'source'},urls=new Set();let model=null,disposed=false,pending=0,observer=null,grid=new Map(),lastFrame=null,drag=null,pinned=null,assetsLoaded=false;const ready=P.loadAssets(workspace);
    const selectedKeys=new Set();
    const selectionStatus=make('p','bf-hover');
    selectionStatus.setAttribute('role','status');
    selectionStatus.hidden=true;
    main.append(selectionStatus);
    let pointerMode='pan';
    function setPointerMode(mode){
      pointerMode=mode;
      hand.setAttribute('aria-pressed',String(mode==='pan'));
      boxSelect.setAttribute('aria-pressed',String(mode==='box'));
      lasso.setAttribute('aria-pressed',String(mode==='lasso'));
    }
    // Reuse the original ScientificFigure toolbar artwork.
    const hand=iconButton('Pan','M8 13V6a2 2 0 0 1 4 0v6M12 11V4a2 2 0 0 1 4 0v9M16 10V7a2 2 0 0 1 4 0v9c0 4-3 6-6 6h-1c-2 0-4-1-5-3l-4-6a2 2 0 0 1 3-2l1 2',()=>setPointerMode('pan'));
    const boxSelect=iconButton('Select','M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z',()=>setPointerMode('box'),{dash:'3 3'});
    const lasso=iconButton('Lasso selection','M6 17c-3-1-4-3-4-6 0-5 5-8 10-8s10 3 10 8-5 8-10 8H9M9 15c-2 0-4 2-4 4s2 3 4 2c2-1 2-4 0-6Z',()=>setPointerMode('lasso'));
    setPointerMode('pan');
    navigation.append(hand,boxSelect,lasso);
    function updateSelectionStatus(){
      selectionStatus.hidden=!selectedKeys.size;
      selectionStatus.textContent=selectedKeys.size.toLocaleString()+' selected · Press Esc on the plot to clear';
    }
    function selectionOutline(gesture){
      if(gesture.mode==='lasso')return [...gesture.points,gesture.last];
      const [x,y]=gesture.start,[endX,endY]=gesture.last;
      return [[x,y],[endX,y],[endX,endY],[x,endY]];
    }
    function selectWithin(vertices){
      let minX=Infinity,minY=Infinity,maxX=-Infinity,maxY=-Infinity;
      for(const [x,y] of vertices){
        minX=Math.min(minX,x);maxX=Math.max(maxX,x);
        minY=Math.min(minY,y);maxY=Math.max(maxY,y);
      }
      selectedKeys.clear();
      for(const bucket of grid.values()){
        for(const {x,y,row} of bucket){
          if(x>=minX&&x<=maxX&&y>=minY&&y<=maxY&&insideSelection(x,y,vertices))selectedKeys.add(row.key);
        }
      }
      updateSelectionStatus();
    }
    function redraw(){if(disposed||pending)return;pending=(root.requestAnimationFrame||root.setTimeout)(()=>{pending=0;draw();});}
    function facts(parent,record){const dl=make('dl','bf-facts');for(const [key,value]of Object.entries(record))dl.append(make('dt','',key.replaceAll('_',' ')),make('dd','',format(value)));parent.append(dl);}
    function inspect(){inspector.replaceChildren();inspector.append(make('span','bf-eyebrow','PINNED SCIENTIFIC EVIDENCE'));const resolved=resolveSelection(model,state,pinned),selected=resolved.row;
      if(!selected){inspector.append(make('h3','',state.selectedLink?state.selectedLink.kind+': '+state.selectedLink.id:'Choose an entity'),make('p','bf-empty',state.selectedLink?'No saved evidence row for this typed entity in the current panel.':'Search an exact saved ID or select a mark/table row. Gene, sample, donor and other entity types remain distinct.'));return;}if(!resolved.in_current_panel)inspector.append(make('p','bf-warning','Pinned evidence belongs to a different saved panel; it is not a value from this view.'));
      inspector.append(make('h3','',selected.id));facts(inspector,{'entity kind':selected.entity_kind,group:selected.group,status:selected.status,x:selected.x,y:selected.y});if(selected.reason)inspector.append(make('p','bf-warning',selected.reason));for(const warning of selected.warnings)inspector.append(make('p','bf-warning',warning));facts(inspector,selected.details);
      if(selected.details?.column_link){const link=selected.details.column_link;inspector.append(button('Link '+link.kind+' '+link.id,()=>{state.selectedLink=copy(link);state.selectedKey=null;search.value=link.id;inspect();redraw();}));}
      const hint=make('p','bf-warning sp-pinned-visibility','The pinned entity is not drawn in this panel/view. Its retained evidence remains available.');inspector.append(hint);updateVisibility();
    }
    function updateVisibility(){const hint=inspector.querySelector('.sp-pinned-visibility');if(!hint)return;const view=snapshot(model,state,'current');const visible=new Set(view.displayed_keys);hint.hidden=model.rows.some(r=>visible.has(r.key)&&(state.selectedLink?r.link?.kind===state.selectedLink.kind&&r.link?.id===state.selectedLink.id:r.key===state.selectedKey));}
    function pin(row){pinned=row;state.selectedKey=row.key;state.selectedPanelId=model.kind;state.selectedLink=copy(row.link);search.value=row.id;matches.replaceChildren();options.onSelection?.({entity_kind:row.link?.kind||row.entity_kind,entity_id:row.link?.id||row.id,source:copy(workspace.source),display_only:true});inspect();redraw();}
    function renderLegend(){
      const width=Math.max(180,Math.min(850,legendHost.clientWidth||650)),placement={width,state,scope:'current'};
      const height=P.legendHeight(model,placement),element=doc.createElementNS(svgNamespace,'svg');
      element.setAttribute('viewBox','0 0 '+width+' '+height);
      element.setAttribute('width',String(width));
      element.setAttribute('height',String(height));
      element.setAttribute('role','img');
      element.setAttribute('aria-label','Scientific color, size and category legend');
      element.style.maxWidth='100%';
      element.style.height='auto';
      appendLegend(element,P.svgLegend(model,placement));
      legendHost.replaceChildren(element);
    }
    function rebuild(){drag=null;selectedKeys.clear();updateSelectionStatus();model=P.project(workspace,state.panelId);state.fullView=true;if(state.selectedPanelId!==model.kind)state.selectedKey=null;picker.value=state.panelId;state.bounds=model.bounds.slice();state.page=0;plotTitle.textContent=model.title;group.replaceChildren();const groups=[...new Set(model.rows.map(r=>r.group).filter(Boolean))].sort();for(const value of ['all',...groups]){const o=make('option','',value==='all'?'All groups':value);o.value=value;group.append(o);}if(!groups.includes(state.group))state.group='all';group.value=state.group;notes.replaceChildren();for(const note of [...model.scientificPanel.annotations,...model.limitations])notes.append(make('p','',note));notes.append(make('p','','Source SHA-256: '+model.source.sha256));renderLegend();inspect();redraw();}
    function draw(){if(disposed)return;if(!assetsLoaded){empty.hidden=false;empty.textContent='Loading and verifying saved context images…';return;}const start=root.performance?.now?.()||Date.now(),requestedWidth=Math.max(420,Math.round(plot.clientWidth||780)),requestedHeight=Math.max(420,Math.min(620,requestedWidth*.72));let g;try{g=frame(model,state,'current',requestedWidth,requestedHeight,state.fullView);}catch(error){canvas.width=1;canvas.height=1;canvas.dataset.error=error.message;empty.hidden=false;empty.textContent=error.message;grid=new Map();return;}state.bounds=g.bounds.slice();const width=g.width,height=g.height,ratio=Math.min(2,root.devicePixelRatio||1),ctx=canvas.getContext('2d');canvas.width=width*ratio;canvas.height=height*ratio;canvas.style.width='100%';canvas.style.height=(height*(plot.clientWidth||width)/width)+'px';delete canvas.dataset.error;ctx.setTransform(ratio,0,0,ratio,0,0);ctx.fillStyle='#fff';ctx.fillRect(0,0,width,height);ctx.font='11px sans-serif';
      const text=(x,y,value,align='left')=>{ctx.textAlign=align;ctx.fillStyle='#526572';ctx.fillText(value,x,y);};
      for(const t of ticks(model,'x',g)){ctx.strokeStyle='#e8edef';ctx.beginPath();ctx.moveTo(g.sx(t.value),g.m.top);ctx.lineTo(g.sx(t.value),height-g.m.bottom);ctx.stroke();if(g.verticalX){ctx.save();ctx.translate(g.sx(t.value),height-g.m.bottom+18);ctx.rotate(-Math.PI/2);text(0,0,t.label,'right');ctx.restore();}else text(g.sx(t.value),height-g.m.bottom+21,t.label,'center');}
      for(const t of ticks(model,'y',g)){ctx.strokeStyle='#e8edef';ctx.beginPath();ctx.moveTo(g.m.left,g.sy(t.value));ctx.lineTo(width-g.m.right,g.sy(t.value));ctx.stroke();const lines=model.yLabels.length?categoryLines(t.label):[t.label];lines.forEach((line,i)=>text(g.m.left-8,g.sy(t.value)+4+(i-(lines.length-1)/2)*13,line,'right'));}
      text((g.m.left+width-g.m.right)/2,height-25,model.axes.x,'center');ctx.save();ctx.translate(17,(g.m.top+height-g.m.bottom)/2);ctx.rotate(-Math.PI/2);text(0,0,model.axes.y,'center');ctx.restore();ctx.save();ctx.beginPath();ctx.rect(g.m.left,g.m.top,g.pw,g.ph);ctx.clip();
      for(const line of model.referenceLines){ctx.strokeStyle='#8a9ca6';ctx.setLineDash([4,4]);ctx.beginPath();if(line.axis==='x'){ctx.moveTo(g.sx(line.value),g.m.top);ctx.lineTo(g.sx(line.value),height-g.m.bottom);}else{ctx.moveTo(g.m.left,g.sy(line.value));ctx.lineTo(width-g.m.right,g.sy(line.value));}ctx.stroke();ctx.setLineDash([]);}
      const drawn=P.drawMarks(ctx,model,g,normalizedState(model,state));grid=new Map();for(const p of drawn.screenPoints){const key=Math.floor(p.x/24)+':'+Math.floor(p.y/24);if(!grid.has(key))grid.set(key,[]);grid.get(key).push(p);}
      for(const row of selectedDisplayed(model,state,'current')){ctx.strokeStyle='#123c54';ctx.lineWidth=2;ctx.beginPath();ctx.arc(g.sx(row.x),g.sy(row.y),7,0,Math.PI*2);ctx.stroke();}
      if(selectedKeys.size){
        ctx.strokeStyle='#123c54';
        ctx.lineWidth=2;
        for(const {x,y,row} of drawn.screenPoints){
          if(!selectedKeys.has(row.key))continue;
          ctx.beginPath();ctx.arc(x,y,7,0,Math.PI*2);ctx.stroke();
        }
      }
      if(drag&&drag.mode!=='pan'){
        const [first,...rest]=selectionOutline(drag);
        ctx.beginPath();ctx.moveTo(...first);
        for(const vertex of rest)ctx.lineTo(...vertex);
        ctx.closePath();
        ctx.fillStyle='rgba(28, 92, 133, .16)';ctx.fill();
        ctx.strokeStyle='#1c5c85';ctx.setLineDash([4,3]);ctx.stroke();
      }
      ctx.restore();lastFrame=g;const view=snapshot(model,state,'current'),c=view.coverage;counts.textContent=c.displayed_rows.toLocaleString()+' shown / '+c.source_rows.toLocaleString()+' source entries · '+c.missing_rows+' missing · '+c.filtered_rows+' filtered · '+c.outside_view_rows+' outside view'+(c.omitted_marks?' · '+c.omitted_marks+' aggregate/path marks omitted by filter':'');empty.hidden=!model.unavailable;empty.textContent=model.unavailable||'';tableTitle.textContent='Saved source table · '+model.rows.length.toLocaleString()+' entries';canvas.dataset.renderMs=((root.performance?.now?.()||Date.now())-start).toFixed(2);canvas.dataset.sourceCount=String(model.rows.length);canvas.dataset.submittedMarks=String(drawn.drawnMarks);canvas.dataset.displaySampling='false';canvas.dataset.panelId=model.kind;canvas.dataset.imagesVerified='true';updateVisibility();renderLegend();if(tableDetails.open)renderTable();
    }
    function renderTable(){tableHost.replaceChildren();const list=model.rows.slice();if(state.sort==='id')list.sort((a,b)=>a.id.localeCompare(b.id));const nav=make('div','bf-table-nav'),sort=select('Scientific table order',[['source','Source order'],['id','Exact identity']]);sort.value=state.sort;sort.onchange=()=>{state.sort=sort.value;state.page=0;renderTable();};nav.append(sort,button('Previous',()=>{state.page=Math.max(0,state.page-1);renderTable();}),make('span','',(state.page*50+1)+'–'+Math.min(list.length,state.page*50+50)+' / '+list.length),button('Next',()=>{state.page=Math.min(Math.ceil(list.length/50)-1,state.page+1);renderTable();}));const table=make('table','bf-data-table'),head=make('tr');for(const name of ['Identity','Unit','Group','X','Y','Status / reason'])head.append(make('th','',name));table.append(head);for(const row of list.slice(state.page*50,state.page*50+50)){const tr=make('tr'),id=make('td');id.append(button(row.id,()=>pin(row),'bf-table-id'));tr.append(id);for(const v of [row.entity_kind,row.group,row.x,row.y,row.reason||row.status])tr.append(make('td','',format(v)));table.append(tr);}tableHost.append(nav,table);}
    function zoom(factor){const b=state.bounds,cx=(b[0]+b[1])/2,cy=(b[2]+b[3])/2,next=[cx+(b[0]-cx)*factor,cx+(b[1]-cx)*factor,cy+(b[2]-cy)*factor,cy+(b[3]-cy)*factor];if(next.every(finite)&&next[0]<next[1]&&next[2]<next[3]){state.fullView=false;state.bounds=next;redraw();}}
    const reset=()=>{state.fullView=true;state.bounds=model.bounds.slice();redraw();};
    navigation.append(
      iconButton('Zoom in','m21 21-4.3-4.3M8 11h6M11 8v6',()=>zoom(.75),{zoomLens:true}),
      iconButton('Zoom out','m21 21-4.3-4.3M8 11h6',()=>zoom(1.35),{zoomLens:true}),
      iconButton('Reset view','M3 11a9 9 0 1 1 2.8 6.5M3 4v7h7',reset),
    );
    const focus=iconButton('Fullscreen','M8 3H3v5M16 3h5v5M21 16v5h-5M8 21H3v-5',async()=>{
      try{
        if(doc.fullscreenElement===shell)await doc.exitFullscreen();
        else await shell.requestFullscreen();
      }catch(_){hover.textContent='Fullscreen unavailable; resize the Workbench canvas or use a complete export.';}
    });
    const focusChanged=()=>{
      const expanded=doc.fullscreenElement===shell;
      const target=expanded?heading:toolbarHost||heading;
      if(navigation.parentNode!==target){
        const restoreFocus=doc.activeElement===focus;
        target.append(navigation);
        if(restoreFocus)focus.focus({preventScroll:true});
      }
      const label=expanded?'Exit fullscreen':'Fullscreen';
      focus.setAttribute('aria-label',label);
      focus.title=label;
      redraw();
    };
    navigation.append(focus);
    doc.addEventListener('fullscreenchange',focusChanged);
    picker.onchange=()=>{state.panelId=picker.value;rebuild();};group.onchange=()=>{state.group=group.value;redraw();};status.onchange=()=>{state.status=status.value;redraw();};tableDetails.ontoggle=()=>{if(tableDetails.open)renderTable();};
    function searchMatches(){matches.replaceChildren();const q=search.value.trim().toLowerCase();if(!q)return;const rows=model.rows.filter(r=>r.id.toLowerCase().includes(q)||r.link?.id.toLowerCase().includes(q)).slice(0,10);for(const row of rows)matches.append(button(row.id+' · '+row.entity_kind+(row.group?' · '+row.group:''),()=>pin(row),'bf-search-match'));}
    search.oninput=searchMatches;search.onkeydown=event=>{if(event.key==='Escape'){matches.replaceChildren();return;}if(event.key!=='Enter')return;event.preventDefault();const rows=model.rows.filter(r=>r.id===search.value||r.link?.id===search.value);if(rows.length===1)pin(rows[0]);else if(rows.length&&rows.every(r=>r.link&&r.link.kind===rows[0].link?.kind&&r.link.id===rows[0].link?.id))pin(rows[0]);else{hover.textContent=rows.length?'Ambiguous identity; choose its unit and group.':'No exact saved identity. Choose a search result.';searchMatches();}};
    const point=event=>{const r=canvas.getBoundingClientRect();return [(event.clientX-r.left)*(lastFrame?.width||r.width)/r.width,(event.clientY-r.top)*(lastFrame?.height||r.height)/r.height];};
    function nearest(p){let best=null,distance=225;const gx=Math.floor(p[0]/24),gy=Math.floor(p[1]/24);for(let x=gx-1;x<=gx+1;x++)for(let y=gy-1;y<=gy+1;y++)for(const item of grid.get(x+':'+y)||[]){const d=(p[0]-item.x)**2+(p[1]-item.y)**2;if(d<distance){distance=d;best=item.row;}}return best;}
    canvas.onpointerdown=event=>{
      if(event.button!==0||!lastFrame)return;
      const start=point(event),g=lastFrame;
      if(start[0]<g.m.left||start[0]>g.m.left+g.pw||start[1]<g.m.top||start[1]>g.m.top+g.ph)return;
      drag={pointerId:event.pointerId,start,last:start,points:[start],bounds:state.bounds.slice(),mode:pointerMode,moved:false};
      canvas.setPointerCapture?.(event.pointerId);
      canvas.focus({preventScroll:true});
    };
    function updateDrag(p){
      drag.last=p;
      drag.moved ||= Math.hypot(p[0]-drag.start[0],p[1]-drag.start[1])>=4;
      if(drag.mode==='pan'&&drag.moved){
        const g=lastFrame,b=drag.bounds;
        const dx=(p[0]-drag.start[0])/g.pw*(b[1]-b[0]);
        const dy=(p[1]-drag.start[1])/g.ph*(b[3]-b[2]);
        state.fullView=false;
        state.bounds=[b[0]-dx,b[1]-dx,b[2]+dy,b[3]+dy];
      }else if(drag.mode==='lasso'&&drag.points.length<9999){
        const previous=drag.points.at(-1);
        if(Math.hypot(p[0]-previous[0],p[1]-previous[1])>=2)drag.points.push(p);
      }
      redraw();
    }
    canvas.onpointermove=event=>{
      const p=point(event);
      if(drag){
        if(event.pointerId===drag.pointerId)updateDrag(p);
        return;
      }
      const row=nearest(p);
      hover.textContent=row?row.id+' · '+row.entity_kind+' · '+row.group+' · X '+format(row.x)+' · Y '+format(row.y)+(row.reason?' · '+row.reason:''):'Hover or click a mark. Ctrl / ⌘ + scroll zooms.';
    };
    canvas.onpointerup=event=>{
      if(!drag||event.pointerId!==drag.pointerId)return;
      const end=point(event);
      updateDrag(end);
      const gesture=drag;
      drag=null;
      if(!gesture.moved){
        const row=nearest(end);
        if(row)pin(row);
      }else if(gesture.mode!=='pan'){
        const vertices=selectionOutline(gesture);
        if(vertices.length>=3)selectWithin(vertices);
      }
      canvas.releasePointerCapture?.(event.pointerId);
      redraw();
    };
    canvas.onpointercancel=canvas.onlostpointercapture=event=>{
      if(drag?.pointerId!==event.pointerId)return;
      drag=null;
      redraw();
    };
    canvas.addEventListener('wheel',event=>{if(event.ctrlKey||event.metaKey){event.preventDefault();zoom(event.deltaY>0?1.15:.87);}},{passive:false});canvas.onkeydown=event=>{if(event.key==='Escape'){event.preventDefault();drag=null;selectedKeys.clear();updateSelectionStatus();redraw();return;}const b=state.bounds,dx=(b[1]-b[0])*.1,dy=(b[3]-b[2])*.1;state.fullView=false;if(['+','=','-','0','ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key))event.preventDefault();if(['+','='].includes(event.key))zoom(.75);else if(event.key==='-')zoom(1.35);else if(event.key==='0')reset();else if(event.key==='ArrowLeft'){state.bounds=[b[0]-dx,b[1]-dx,b[2],b[3]];redraw();}else if(event.key==='ArrowRight'){state.bounds=[b[0]+dx,b[1]+dx,b[2],b[3]];redraw();}else if(event.key==='ArrowUp'){state.bounds=[b[0],b[1],b[2]+dy,b[3]+dy];redraw();}else if(event.key==='ArrowDown'){state.bounds=[b[0],b[1],b[2]-dy,b[3]-dy];redraw();}};
    function exportName(){return model.kind.replace(/[^A-Za-z0-9_.-]/g,'-')+'-'+scope.value;}
    function download(blob,extension,name=exportName()){const url=URL.createObjectURL(blob);urls.add(url);const a=make('a');a.href=url;a.download=name+'.'+extension;doc.body.append(a);a.click();a.remove();root.setTimeout(()=>{URL.revokeObjectURL(url);urls.delete(url);},1000);}
    exportMenu.append(button('Vector SVG',()=>download(new Blob([svg(model,state,scope.value)],{type:'image/svg+xml'}),'svg')),button('PNG · 2100 px',()=>{const name=exportName(),source=svg(model,state,scope.value),url=URL.createObjectURL(new Blob([source],{type:'image/svg+xml'})),image=new root.Image();urls.add(url);image.onload=()=>{if(disposed)return;const output=make('canvas');output.width=2100;output.height=Math.round(2100*image.height/image.width);output.getContext('2d').drawImage(image,0,0,output.width,output.height);output.toBlob(blob=>{if(blob)download(blob,'png',name);});URL.revokeObjectURL(url);urls.delete(url);};image.onerror=()=>{URL.revokeObjectURL(url);urls.delete(url);hover.textContent='PNG encoding unavailable. Use the vector export.';};image.src=url;}),button('View record JSON',()=>download(new Blob([JSON.stringify(snapshot(model,state,scope.value),null,2)],{type:'application/json'}),'json')),button('All source rows CSV',()=>download(new Blob([csv(model)],{type:'text/csv;charset=utf-8'}),'csv')));
    if(options.exportPDF)exportMenu.append(button('Vector PDF',async()=>{try{const name=exportName(),record=snapshot(model,state,scope.value),blob=await options.exportPDF({panel_id:model.kind,scope:scope.value,bounds:record.bounds,filters:record.filters,selected_link:record.selected_link,selected_key:record.selected_key});if(!disposed)download(blob,'pdf',name);}catch(error){hover.textContent=error.message;}}));
    if(options.exportBundle)exportMenu.append(button('Presentation bundle',async()=>{const name=exportName();try{const view=snapshot(model,state,scope.value),request={panel_id:model.kind,scope:scope.value,bounds:view.bounds,filters:view.filters,selected_link:view.selected_link,selected_key:view.selected_key};await ready;const result=await options.exportBundle(request);if(!disposed){download(result.blob,'zip',name);hover.textContent='Presentation receipt root — retain separately: '+result.root_hash;}}catch(error){hover.textContent=error.message;}}));
    if(options.loadCapabilities){const capabilityDetails=make('details','bf-limitations'),capabilityBody=make('div');capabilityDetails.append(make('summary','','Plot capability status'),capabilityBody);shell.append(capabilityDetails);let loaded=false;capabilityDetails.ontoggle=async()=>{if(!capabilityDetails.open||loaded)return;loaded=true;capabilityBody.textContent='Checking source-bound capability status…';try{const catalog=await options.loadCapabilities();if(disposed)return;capabilityBody.replaceChildren(make('p','',catalog.scope));for(const item of catalog.capabilities){const label=item.implementation_status==='not_yet_implemented'?'Not implemented':item.verification_status!=='verified'?'Implemented · verification pending':item.data_availability.available?'Implemented and verified':'Implemented · required data unavailable';capabilityBody.append(make('p','',item.id.replaceAll('_',' ')+' — '+label+(item.data_availability.missing_inputs.length?' · missing '+item.data_availability.missing_inputs.join(', '):'')));}}catch(error){capabilityBody.textContent=error.message;loaded=false;}};}
    for(const control of exports.querySelectorAll('button'))control.disabled=true;ready.then(()=>{if(disposed)return;assetsLoaded=true;for(const control of exports.querySelectorAll('button'))control.disabled=false;redraw();}).catch(error=>{if(!disposed){empty.hidden=false;empty.textContent=error.message;canvas.dataset.error=error.message;}});
    const controller={ready,destroy(){disposed=true;P.disposeAssets(workspace);if(pending)(root.cancelAnimationFrame||root.clearTimeout)(pending);observer?.disconnect();doc.removeEventListener('fullscreenchange',focusChanged);navigation.remove();if(doc.fullscreenElement===shell)doc.exitFullscreen().catch(()=>{});for(const url of urls)URL.revokeObjectURL(url);container.replaceChildren();delete container.__scientificFigure;},getConfiguration:()=>snapshot(model,state,'current'),getSelection:()=>({selected_link:copy(state.selectedLink),selected_key:state.selectedKey,selected_keys:[...selectedKeys]}),exportSVG:(selectedScope='complete')=>svg(model,state,selectedScope),getPerformance:()=>({population_n:model.rows.length,mark_count:model.scientificPanel.marks.length,display_sampling:false}),selectPlot(id){state.panelId=id;rebuild();},reset,redraw};
    container.__scientificFigure=controller;observer=root.ResizeObserver?new root.ResizeObserver(redraw):null;observer?.observe(plot);rebuild();return controller;
  }
  const api={mount,frame,snapshot,resolveSelection,selectedDisplayed,exportSVG:svg,toCSV:csv};root.ScientificPlotWorkbench=api;if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
