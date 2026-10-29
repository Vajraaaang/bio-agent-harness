/* Trusted display of bounded saved observation geometry. No analysis execution. */
(function (root) {
  'use strict';
  const MAX_POINTS = 200000, MAX_PANEL_ROWS = 400000, PAGE_SIZE = 40;
  const COLOR = /^#[0-9a-f]{6}$/i;
  const fail = text => { throw new Error(text); };
  const text = value => typeof value === 'string' && value.length > 0 && value.length <= 2000;
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const own = (object, key) => Object.prototype.hasOwnProperty.call(object, key);
  const escapeXML = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'}[c]));
  const OKABE_ITO=['#0072B2','#E69F00','#009E73','#CC79A7','#56B4E9','#D55E00'];
  const AXIS_FONT='11px -apple-system, Inter, sans-serif';
  function niceTicks(min,max,count=5){
    if(!finite(min)||!finite(max)||max<min||!finite(count)||count<2||count>100)fail('A finite increasing axis range is required.');
    if(max===min){const values=[min];values.step=1;return values;}
    const span=max-min,raw=span/(count-1),power=10**Math.floor(Math.log10(raw)),fraction=raw/power;
    if(!finite(span)||!finite(raw)||raw<=0||!finite(power)||power<=0)fail('The saved axis range cannot be represented accurately in this view.');
    const step=(fraction<1.5?1:fraction<3?2:fraction<7?5:10)*power;
    if(!finite(step)||step<=0)fail('The saved axis tick step cannot be represented accurately.');
    const start=Math.ceil(min/step-1e-10),end=Math.floor(max/step+1e-10),values=[];
    if(!Number.isSafeInteger(start)||!Number.isSafeInteger(end))fail('The saved axis precision exceeds the interactive display range.');
    for(let i=start;i<=end&&values.length<100;i++){
      const value=Number((i*step).toPrecision(14))||0;
      if(!finite(value)||(values.length&&value<=values[values.length-1]))fail('The saved axis ticks cannot be represented distinctly.');
      values.push(value);
    }
    values.step=step;return values;
  }
  function formatTick(value,step=1){
    const digits=Math.max(0,-Math.floor(Math.log10(Math.abs(step)||1)));
    const normalized=Object.is(value,-0)?0:value;
    if(digits>15)return normalized===0?'0':Number(normalized.toPrecision(14)).toString();
    return normalized.toLocaleString('en-US',{minimumFractionDigits:0,maximumFractionDigits:digits,useGrouping:Math.abs(normalized)>=10000});
  }
  function displayCategoryColors(saved){return Object.fromEntries(Object.keys(saved).map((key,index)=>[key,OKABE_ITO[index%OKABE_ITO.length]]));}
  function interpolateViewport(start,end,progress){const t=Math.max(0,Math.min(1,progress));if(t===1)return end.slice();const ease=1-(1-t)**3;return start.map((value,index)=>value+(end[index]-value)*ease);}
  function prepare(record, payload) {
    if (!record || record.format !== 'devbench.figure/1' || !['G02','G08'].includes(record.family)
        || !payload || payload.format !== 'devbench.figure-data/1' || payload.payload_kind !== 'observation_points'
        || JSON.stringify(payload.key_columns) !== '["observation_id"]' || record.semantics?.unit !== 'cell') {
      fail('Interactive view supports retained QC and UMAP observation figures only. Use the original image for this artifact.');
    }
    if (!Array.isArray(payload.population_ids) || payload.population_ids.length > MAX_POINTS
        || !payload.population_ids.every(text) || new Set(payload.population_ids).size !== payload.population_ids.length
        || record.analysis_n !== payload.population_ids.length) fail('Figure population identities or counts are inconsistent.');
    if (!Array.isArray(payload.panels) || payload.panels.length < 1 || payload.panels.length > 8) fail('Figure panels are unavailable.');
    const population = new Set(payload.population_ids), panelIds = new Set(), renderedIds = new Set();
    let count = 0;
    const panels = payload.panels.map(panel => {
      if (!panel || !text(panel.panel_id) || panelIds.has(panel.panel_id) || !text(panel.title)
          || !Array.isArray(panel.rows) || panel.rows.length !== population.size) fail('Figure panel identities are inconsistent.');
      panelIds.add(panel.panel_id);
      count += panel.rows.length;
      if (count > MAX_PANEL_ROWS) fail('Figure exceeds the interactive display budget. Use the verified original image.');
      for (const axis of [panel.x_axis,panel.y_axis]) {
        if (!axis || !['field','label','unit','transform'].every(key => text(axis[key]))) fail('Saved axis meanings are required.');
      }
      const color = panel.color;
      if (!color || !text(color.label) || !text(color.field) || !['categorical','continuous'].includes(color.kind)) fail('Saved color semantics are required.');
      if (color.kind === 'categorical' && (!color.colors || Array.isArray(color.colors)
          || !Object.entries(color.colors).every(([key,value]) => text(key) && COLOR.test(value)))) fail('Saved category colors are invalid.');
      if (color.kind === 'continuous' && (!color.scale || color.scale.kind !== 'linear'
          || !finite(color.scale.min) || !finite(color.scale.max) || color.scale.min > color.scale.max
          || color.scale.clipped_n !== 0)) fail('Saved continuous color range is invalid.');
      const byId = new Map(), rendered = [];
      let xmin=Infinity,xmax=-Infinity,ymin=Infinity,ymax=-Infinity;
      for (const row of panel.rows) {
        if (!row || !population.has(row.observation_id) || byId.has(row.observation_id)
            || !row.metadata || typeof row.metadata !== 'object' || Array.isArray(row.metadata)) fail('Observation identities are missing or duplicated.');
        byId.set(row.observation_id,row);
        if ([row.x,row.y].some(value => value !== null && !finite(value))) fail('Figure coordinates must be finite or explicitly absent.');
        if (row.color !== null && (color.kind === 'continuous' ? !finite(row.color)||row.color<color.scale.min||row.color>color.scale.max : !own(color.colors,row.color))) fail('Figure color values do not match their saved scale.');
        if (row.status === 'rendered') {
          if (![row.x,row.y].every(finite) || row.color === null || row.exclusion_reason !== null) fail('A rendered observation lacks saved values.');
          rendered.push(row); renderedIds.add(row.observation_id);
          xmin=Math.min(xmin,row.x); xmax=Math.max(xmax,row.x); ymin=Math.min(ymin,row.y); ymax=Math.max(ymax,row.y);
        } else if (row.status !== 'excluded' || !text(row.exclusion_reason)) fail('An excluded observation needs a recorded reason.');
      }
      if (!rendered.length) { xmin=0;xmax=1;ymin=0;ymax=1; }
      const dx=(xmax-xmin)||Math.max(Math.abs(xmin)*.1,1),dy=(ymax-ymin)||Math.max(Math.abs(ymin)*.1,1);
      const bounds=[xmin-dx*.05,xmax+dx*.05,ymin-dy*.05,ymax+dy*.05];
      if(!bounds.every(finite)||!finite(bounds[1]-bounds[0])||!finite(bounds[3]-bounds[2])||bounds[1]<=bounds[0]||bounds[3]<=bounds[2])fail('The saved coordinate range cannot be represented accurately in this view.');
      niceTicks(bounds[0],bounds[1],5);niceTicks(bounds[2],bounds[3],5);
      return {...panel,byId,rendered,bounds};
    });
    if (record.rendered_n !== renderedIds.size) fail('Saved rendered population count does not match the figure.');
    return {record,ids:payload.population_ids.slice(),panels};
  }
  function inside(x,y,polygon) {
    let hit=false;
    for(let i=0,j=polygon.length-1;i<polygon.length;j=i++) {
      const a=polygon[i],b=polygon[j];
      const cross=(x-a[0])*(b[1]-a[1])-(y-a[1])*(b[0]-a[0]);
      if(Math.abs(cross)<1e-10&&x>=Math.min(a[0],b[0])&&x<=Math.max(a[0],b[0])&&y>=Math.min(a[1],b[1])&&y<=Math.max(a[1],b[1]))return true;
      if(((a[1]>y)!==(b[1]>y))&&x<(b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0])hit=!hit;
    }
    return hit;
  }
  function selectPolygon(panel,polygon,visible) {
    if (!Array.isArray(polygon)||polygon.length<3||polygon.length>10000||polygon.some(p=>!Array.isArray(p)||p.length!==2||!p.every(finite))) fail('Selection geometry is invalid.');
    return panel.rendered.filter(row=>(panel.color.kind!=='categorical'||!visible||visible.has(row.color))&&inside(row.x,row.y,polygon)).map(row=>row.observation_id);
  }
  function csvCell(value) {
    let valueText=value==null?'':typeof value==='object'?JSON.stringify(value):String(value);
    if (typeof value !== 'number' && /^[\s]*[=+@-]/.test(valueText)) valueText="'"+valueText;
    return '"'+valueText.replace(/"/g,'""')+'"';
  }
  function toCSV(rows,ids) {
    const selected=ids?new Set(ids):null,chosen=rows.filter(row=>!selected||selected.has(row.observation_id));
    const columns=['observation_id','x','y','color','status','exclusion_reason','metadata'];
    return columns.join(',')+'\r\n'+chosen.map(row=>columns.map(key=>csvCell(row[key])).join(',')).join('\r\n');
  }
  function mount(container,options) {
    if (!container || !options) fail('A figure container and retained artifact are required.');
    if(container.__scientificFigure)container.__scientificFigure.destroy();
    const model=prepare(options.record,options.payload),doc=container.ownerDocument;
    const toolbarHost=options.toolbarHost||null;
    if(toolbarHost&&(toolbarHost.ownerDocument!==doc||typeof toolbarHost.append!=='function'))fail('The figure toolbar must belong to the same application document.');
    const make=(tag,className,value)=>{const item=doc.createElement(tag);if(className)item.className=className;if(value!==undefined)item.textContent=value;return item;};
    const button=(label,action)=>{const item=make('button','secondary',label);item.type='button';item.addEventListener('click',action);return item;};
    const field=(label,control)=>{const box=make('label','sf-control');box.append(make('span','',label),control);return box;};
    const section=make('section','scientific-figure');section.setAttribute('aria-label','Interactive retained scientific figure');
    const heading=make('div','sf-heading'),title=make('h3','',model.record.family==='G02'?'Cell quality':'UMAP'),badge=make('span','tag','Display only');
    const toolbar=make('div','sf-toolbar'),panelPicker=make('select');panelPicker.setAttribute('aria-label','Saved figure panel');
    model.panels.forEach((panel,index)=>{const item=make('option','',panel.color.label);item.value=String(index);panelPicker.append(item);});
    heading.append(title,panelPicker,badge,toolbar);
    const modePicker=make('select');modePicker.setAttribute('aria-label','Plot interaction');
    [['pan','Pan'],['box','Box selection'],['lasso','Lasso selection']].forEach(([key,label])=>{const item=make('option','',label);item.value=key;modePicker.append(item);});
    modePicker.value='pan';
    const icons={
      'zoom-in':[['circle',{cx:11,cy:11,r:8}],['path',{d:'m21 21-4.3-4.3M8 11h6M11 8v6'}]],
      'zoom-out':[['circle',{cx:11,cy:11,r:8}],['path',{d:'m21 21-4.3-4.3M8 11h6'}]],
      reset:[['path',{d:'M3 11a9 9 0 1 1 2.8 6.5M3 4v7h7'}]],
      fullscreen:[['path',{d:'M8 3H3v5M16 3h5v5M21 16v5h-5M8 21H3v-5'}]],
      pan:[['path',{d:'M8 13V6a2 2 0 0 1 4 0v6M12 11V4a2 2 0 0 1 4 0v9M16 10V7a2 2 0 0 1 4 0v9c0 4-3 6-6 6h-1c-2 0-4-1-5-3l-4-6a2 2 0 0 1 3-2l1 2'}]],
      select:[['rect',{x:3,y:3,width:18,height:18,rx:2,'stroke-dasharray':'3 3'}]],
      lasso:[['path',{d:'M6 17c-3-1-4-3-4-6 0-5 5-8 10-8s10 3 10 8-5 8-10 8H9M9 15c-2 0-4 2-4 4s2 3 4 2c2-1 2-4 0-6Z'}]]
    };
    function iconButton(label,action,handler){const item=button('',handler);item.classList.add('sf-icon-button');item.setAttribute('aria-label',label);item.title=label;item.dataset.figureAction=action;
      const svg=doc.createElementNS('http://www.w3.org/2000/svg','svg');for(const [key,value] of Object.entries({viewBox:'0 0 24 24',width:'17',height:'17',fill:'none',stroke:'currentColor','stroke-width':'1.6','stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true'}))svg.setAttribute(key,value);
      for(const [tag,attributes]of icons[action]||[]){const shape=doc.createElementNS('http://www.w3.org/2000/svg',tag);for(const [key,value]of Object.entries(attributes))shape.setAttribute(key,String(value));svg.append(shape);}item.append(svg);return item;}
    const modeButtons=[];
    function changeMode(mode){modePicker.value=mode;for(const item of modeButtons)item.setAttribute('aria-pressed',String(item.dataset.mode===mode));}
    for(const [label,action,mode]of [['Pan','pan','pan'],['Select','select','box'],['Lasso selection','lasso','lasso']]){const item=iconButton(label,action,()=>changeMode(mode));item.dataset.mode=mode;item.setAttribute('aria-pressed',String(mode==='pan'));modeButtons.push(item);toolbar.append(item);}
    const plot=make('div','sf-plot'),canvas=make('canvas','sf-canvas');canvas.tabIndex=0;canvas.setAttribute('role','img');canvas.setAttribute('aria-label','Saved observation scatter plot. Use plus or minus to zoom, arrow keys to pan, or the observations table for keyboard selection.');
    const hover=make('p','sf-hover','Hover to inspect a cell.'),counts=make('p','sf-counts sf-status');counts.setAttribute('role','status');counts.setAttribute('aria-live','polite');
    plot.append(canvas,hover);
    const controls=make('details','sf-settings'),summary=make('summary','','Display settings');controls.append(summary);
    const settingGrid=make('div','sf-setting-grid'),size=make('input'),opacity=make('input');
    size.type='range';size.min='.5';size.max='6';size.step='.5';size.value='2';size.setAttribute('aria-label','Point radius');
    opacity.type='range';opacity.min='.1';opacity.max='1';opacity.step='.05';opacity.value='.8';opacity.setAttribute('aria-label','Point opacity');
    settingGrid.append(field('Point radius',size),field('Opacity',opacity));controls.append(settingGrid);
    const legend=make('div','sf-legend');legend.setAttribute('aria-label','Saved color categories');
    const actions=make('div','sf-actions');
    const selectionText=make('p','sf-selection','No observations selected.'),ask=button('Ask about selection',()=>{
      const ids=Array.from(selection);if(!ids.length)return;
      if(typeof options.onSelection==='function')options.onSelection({figure_id:model.record.figure_id,observation_ids:ids,panel_id:panel().panel_id,context:options.context||{},display_only:true});
      selectionText.textContent=ids.length.toLocaleString()+' exact observation IDs sent to the question draft. Review before submitting.';
    });ask.disabled=true;
    const clearSelection=button('Clear selection',()=>{selection.clear();updateSelection();render();});
    actions.append(ask,clearSelection);
    const exportActions=make('div','sf-exports'),exportLabel=make('span','','Export exploration:');
    if(!toolbarHost)exportActions.append(exportLabel);
    const hostedTools=toolbarHost?make('div','sf-integrated-tools'):null;
    if(hostedTools){const exportMenu=make('details','sf-export-menu'),exportSummary=make('summary','','Export');exportSummary.setAttribute('aria-label','Export figure');exportSummary.title='Export figure';exportMenu.append(exportSummary,exportActions);hostedTools.append(panelPicker,badge,toolbar,exportMenu);toolbarHost.append(hostedTools);}
    const tableDetails=make('details','sf-table-details'),tableSummary=make('summary','','Observation table · exact saved identities');tableDetails.append(tableSummary);
    const tableTools=make('div','sf-table-tools'),search=make('input');search.type='search';search.placeholder='Find observation ID';search.setAttribute('aria-label','Find observation ID');
    const selectedOnly=make('input');selectedOnly.type='checkbox';tableTools.append(search,field('Selected only',selectedOnly));
    const table=make('table','sf-table'),thead=make('thead'),headerRow=make('tr');['Select','Observation','X','Y','Saved color','Status'].forEach(value=>headerRow.append(make('th','',value)));thead.append(headerRow);table.append(thead);
    const tbody=make('tbody');table.append(tbody);const tableScroll=make('div','sf-table-scroll');tableScroll.append(table);
    const paging=make('div','sf-paging'),pageLabel=make('span');
    const previous=button('Previous',()=>{page--;renderTable();}),next=button('Next',()=>{page++;renderTable();});paging.append(previous,pageLabel,next);
    tableDetails.append(tableTools,tableScroll,paging);
    const evidence=make('details','sf-evidence');evidence.append(make('summary','','Method and provenance'));
    const evidenceGrid=make('dl','sf-kv-grid');
    for(const [key,value]of [['Representation',model.record.semantics.representation_or_layer],['Analysis population',model.ids.length.toLocaleString()+' cells'],['Palette','Okabe–Ito display colors; saved category colors remain in retained data.'],['Source run',options.context?.job_id||model.record.original_run_id||'Recorded analysis'],['Plot data',model.record.plot_data?.path||'Saved geometry']])evidenceGrid.append(make('dt','',key),make('dd','',value));
    evidence.append(evidenceGrid,make('p','muted','Display changes do not rerun the analysis.'));
    for(const limitation of model.record.semantics.limitations||[])evidence.append(make('p','muted',limitation));
    const binding=make('pre','',JSON.stringify({analysis_binding:model.record.analysis_binding||null,population_sha256:model.record.semantics.population_sha256||null,plot_data:model.record.plot_data||null,saved_category_colors:model.panels.map(panel=>panel.color.colors||null),context:options.context||{}},null,2));binding.hidden=true;
    const evidenceActions=make('div','sf-evidence-actions'),showJSON=button('Show JSON',()=>{binding.hidden=!binding.hidden;showJSON.textContent=binding.hidden?'Show JSON':'Hide JSON';});
    evidenceActions.append(showJSON,button('Copy JSON',async()=>{try{await root.navigator.clipboard.writeText(binding.textContent);hover.textContent='Provenance JSON copied.';}catch(_){binding.hidden=false;showJSON.textContent='Hide JSON';hover.textContent='Select the provenance JSON to copy it.';}}));evidence.append(evidenceActions,binding);
    if(!toolbarHost)section.append(heading);
    section.append(plot,counts,legend,controls,selectionText,actions);
    if(!toolbarHost)section.append(exportActions);
    section.append(tableDetails,evidence);container.replaceChildren(section);
    let panelIndex=0,view=model.panels[0].bounds.slice(),selection=new Set(),visible=new Set(),colors={},page=0,disposed=false,frame=0,drag=null,hoverFrame=0,animationFrame=0;
    let width=700,height=450,screenPoints=[],grid=new Map(),lastRenderMs=0;
    const margins={left:72,right:24,top:25,bottom:65};
    const panel=()=>model.panels[panelIndex];
    const dataX=x=>view[0]+(x-margins.left)/(width-margins.left-margins.right)*(view[1]-view[0]);
    const dataY=y=>view[3]-(y-margins.top)/(height-margins.top-margins.bottom)*(view[3]-view[2]);
    const screenX=x=>margins.left+(x-view[0])/(view[1]-view[0])*(width-margins.left-margins.right);
    const screenY=y=>margins.top+(view[3]-y)/(view[3]-view[2])*(height-margins.top-margins.bottom);
    const shown=row=>panel().color.kind!=='categorical'||visible.has(row.color);
    const inViewport=(x,y)=>x>=margins.left&&x<=width-margins.right&&y>=margins.top&&y<=height-margins.bottom;
    function continuousColor(value) {
      const scale=panel().color.scale,t=scale.max===scale.min?.5:Math.round(Math.max(0,Math.min(1,(value-scale.min)/(scale.max-scale.min)))*255)/255;
      // Perceptually ordered blue sequential display; exact saved numeric range is retained.
      const stops=[[247,251,255],[198,219,239],[107,174,214],[33,113,181],[8,48,107]],k=t*4,index=Math.min(3,Math.floor(k)),mix=k-index;
      return 'rgb('+stops[index].map((value,i)=>Math.round(value*(1-mix)+stops[index+1][i]*mix)).join(',')+')';
    }
    function colorFor(row){return panel().color.kind==='categorical'?colors[row.color]:continuousColor(row.color);}
    function resetCategories(){visible=new Set(Object.keys(panel().color.colors||{}));colors=displayCategoryColors(panel().color.colors||{});}
    function format(value){return value===null?'—':Math.abs(value)>=10000||Math.abs(value)>0&&Math.abs(value)<.001?value.toExponential(2):Number(value.toPrecision(4)).toString();}
    function palette(){const style=root.getComputedStyle?root.getComputedStyle(section):null;return {paper:style?.getPropertyValue('--surface').trim()||'#ffffff',ink:style?.getPropertyValue('--ink').trim()||'#0f172a',muted:style?.getPropertyValue('--muted').trim()||'#475569',line:style?.getPropertyValue('--border').trim()||style?.getPropertyValue('--line').trim()||'#e2e8f0',grid:style?.getPropertyValue('--grid-line').trim()||style?.getPropertyValue('--soft').trim()||'#f1f5f9'};}
    function drawAxes(ctx,theme){
      ctx.font=AXIS_FONT;ctx.lineWidth=1;ctx.strokeStyle=theme.grid;ctx.fillStyle=theme.muted;ctx.setLineDash([3,4]);
      const xTicks=niceTicks(view[0],view[1],5),yTicks=niceTicks(view[2],view[3],5);
      for(const value of xTicks){const x=screenX(value);ctx.beginPath();ctx.moveTo(x,margins.top);ctx.lineTo(x,height-margins.bottom);ctx.stroke();ctx.textAlign='center';ctx.fillText(formatTick(value,xTicks.step),x,height-margins.bottom+20);}
      for(const value of yTicks){const y=screenY(value);ctx.beginPath();ctx.moveTo(margins.left,y);ctx.lineTo(width-margins.right,y);ctx.stroke();ctx.textAlign='right';ctx.fillText(formatTick(value,yTicks.step),margins.left-10,y+4);}
      ctx.setLineDash([]);ctx.strokeStyle=theme.line;ctx.beginPath();ctx.moveTo(margins.left,margins.top);ctx.lineTo(margins.left,height-margins.bottom);ctx.lineTo(width-margins.right,height-margins.bottom);ctx.stroke();
      ctx.fillStyle=theme.ink;ctx.font=AXIS_FONT;ctx.textAlign='center';ctx.fillText(panel().x_axis.label,(margins.left+width-margins.right)/2,height-19);ctx.save();ctx.translate(17,(margins.top+height-margins.bottom)/2);ctx.rotate(-Math.PI/2);ctx.fillText(panel().y_axis.label,0,0);ctx.restore();
    }
    function draw(){
      frame=0;if(disposed)return;const started=performance.now(),ctx=canvas.getContext('2d');if(!ctx){hover.textContent='Canvas rendering is unavailable. Use the original image or observation table.';return;}
      width=Math.max(300,Math.min(1800,plot.clientWidth||700));height=Math.max(320,Math.min(640,Math.round(width*.65)));
      const ratio=root.devicePixelRatio||1;canvas.width=Math.round(width*ratio);canvas.height=Math.round(height*ratio);ctx.scale(ratio,ratio);
      const theme=palette();ctx.fillStyle=theme.paper;ctx.fillRect(0,0,width,height);drawAxes(ctx,theme);
      const groups=new Map();screenPoints=[];grid=new Map();let categoryVisible=0;
      for(const row of panel().rendered){if(!shown(row))continue;categoryVisible++;const x=screenX(row.x),y=screenY(row.y);if(!inViewport(x,y))continue;
        const point={x,y,row},key=colorFor(row);if(!groups.has(key))groups.set(key,[]);groups.get(key).push(point);screenPoints.push(point);
        const bin=Math.floor(x/24)+':'+Math.floor(y/24);if(!grid.has(bin))grid.set(bin,[]);grid.get(bin).push(point);}
      ctx.save();ctx.beginPath();ctx.rect(margins.left,margins.top,width-margins.left-margins.right,height-margins.top-margins.bottom);ctx.clip();ctx.globalAlpha=Number(opacity.value);
      for(const [color,points] of groups){ctx.fillStyle=color;ctx.strokeStyle='#ffffff';ctx.lineWidth=1;ctx.beginPath();const radius=Number(size.value);for(const point of points){ctx.moveTo(point.x+radius,point.y);ctx.arc(point.x,point.y,radius,0,Math.PI*2);}ctx.fill();ctx.stroke();}
      ctx.globalAlpha=1;ctx.strokeStyle=theme.ink;ctx.lineWidth=1.6;ctx.beginPath();for(const point of screenPoints)if(selection.has(point.row.observation_id)){ctx.moveTo(point.x+Number(size.value)+2,point.y);ctx.arc(point.x,point.y,Number(size.value)+2,0,Math.PI*2);}ctx.stroke();
      if(drag&&modePicker.value!=='pan'){ctx.strokeStyle=theme.ink;ctx.fillStyle=theme.line;ctx.globalAlpha=.3;ctx.beginPath();const points=modePicker.value==='box'?[[drag.start[0],drag.start[1]],[drag.last[0],drag.start[1]],drag.last,[drag.start[0],drag.last[1]]]:drag.points;points.forEach((p,i)=>i?ctx.lineTo(...p):ctx.moveTo(...p));ctx.closePath();ctx.fill();ctx.globalAlpha=1;ctx.setLineDash([4,3]);ctx.stroke();}
      ctx.restore();lastRenderMs=performance.now()-started;
      counts.replaceChildren();for(const value of [screenPoints.length.toLocaleString()+' in viewport',categoryVisible.toLocaleString()+' / '+panel().rendered.length.toLocaleString()+' visible',model.ids.length.toLocaleString()+' analysis cells','No sampling'])counts.append(make('span','sf-status-item',value));
    }
    function render(){if(!frame&&!disposed)frame=root.requestAnimationFrame(draw);}
    function renderLegend(){legend.replaceChildren();const color=panel().color;
      if(color.kind==='continuous'){const bar=make('div','sf-colorbar');bar.setAttribute('role','img');bar.setAttribute('aria-label','Sequential blue scale from '+format(color.scale.min)+' to '+format(color.scale.max));legend.append(make('p','',color.label+' · saved linear scale '+format(color.scale.min)+' to '+format(color.scale.max)),bar);return;}
      legend.append(make('p','',color.label));
      for(const category of Object.keys(color.colors)){const row=make('div','sf-category'),check=make('input'),picker=make('input');check.type='checkbox';check.checked=visible.has(category);check.setAttribute('aria-label','Show '+category);picker.type='color';picker.value=colors[category];picker.setAttribute('aria-label','Display color for '+category);
        check.addEventListener('change',()=>{check.checked?visible.add(category):visible.delete(category);page=0;render();renderTable();updateSelection();});picker.addEventListener('input',()=>{colors[category]=picker.value;render();});row.append(check,make('span','',category),picker);legend.append(row);}
    }
    function updateSelection(){const displayed=Array.from(selection).filter(id=>{const row=panel().byId.get(id);return row&&row.status==='rendered'&&shown(row);}).length;
      selectionText.textContent=selection.size?selection.size.toLocaleString()+' exact observation IDs selected · '+displayed.toLocaleString()+' visible in this panel. Selection is preserved across panels and category filters.':'No observations selected. Choose a box or lasso, or select rows in the table.';ask.disabled=!selection.size;clearSelection.disabled=!selection.size;renderTable();}
    function renderTable(){if(!tableDetails.open)return;const query=search.value.trim().toLocaleLowerCase();const rows=panel().rows.filter(row=>(row.status!=='rendered'||shown(row))&&(!selectedOnly.checked||selection.has(row.observation_id))&&(!query||row.observation_id.toLocaleLowerCase().includes(query)));
      const pages=Math.max(1,Math.ceil(rows.length/PAGE_SIZE));page=Math.max(0,Math.min(page,pages-1));tbody.replaceChildren();
      for(const row of rows.slice(page*PAGE_SIZE,(page+1)*PAGE_SIZE)){const tr=make('tr'),td=make('td'),check=make('input');check.type='checkbox';check.checked=selection.has(row.observation_id);check.disabled=row.status!=='rendered';check.setAttribute('aria-label','Select '+row.observation_id);check.addEventListener('change',()=>{check.checked?selection.add(row.observation_id):selection.delete(row.observation_id);updateSelection();render();});td.append(check);tr.append(td);
        for(const value of [row.observation_id,format(row.x),format(row.y),row.color??'—',row.status==='excluded'?'Excluded: '+row.exclusion_reason:'Plotted'])tr.append(make('td','',value));tbody.append(tr);}
      pageLabel.textContent=rows.length.toLocaleString()+' matching · page '+(page+1)+' of '+pages;previous.disabled=page===0;next.disabled=page===pages-1;
    }
    const reducedMotion=()=>Boolean(root.matchMedia?.('(prefers-reduced-motion: reduce)').matches);
    function stopAnimation(){if(animationFrame){root.cancelAnimationFrame(animationFrame);animationFrame=0;}}
    function moveViewport(target,animate){stopAnimation();if(frame){root.cancelAnimationFrame(frame);frame=0;}if(!animate||reducedMotion()){view=target.slice();render();return;}
      const start=view.slice(),started=performance.now();
      const tick=at=>{if(disposed)return;const progress=Math.min(1,Math.max(0,(at-started)/160));view=interpolateViewport(start,target,progress);draw();animationFrame=progress<1?root.requestAnimationFrame(tick):0;};
      animationFrame=root.requestAnimationFrame(tick);
    }
    function zoom(factor,x=width/2,y=height/2,animate=false){const centerX=dataX(x),centerY=dataY(y),next=[centerX+(view[0]-centerX)*factor,centerX+(view[1]-centerX)*factor,centerY+(view[2]-centerY)*factor,centerY+(view[3]-centerY)*factor];
      if(next.every(finite)&&next[1]-next[0]>1e-12&&next[3]-next[2]>1e-12)moveViewport(next,animate);}
    function reset(){size.value='2';opacity.value='.8';resetCategories();renderLegend();moveViewport(panel().bounds.slice(),true);renderTable();updateSelection();}
    toolbar.append(iconButton('Zoom in','zoom-in',()=>zoom(.8,width/2,height/2,true)),iconButton('Zoom out','zoom-out',()=>zoom(1.25,width/2,height/2,true)),iconButton('Reset view','reset',reset));
    const fullscreen=iconButton('Fullscreen','fullscreen',async()=>{try{if(doc.fullscreenElement===section)await doc.exitFullscreen();else await section.requestFullscreen();render();}catch(_){hover.textContent='Fullscreen is unavailable in this browser.';}});fullscreen.disabled=!section.requestFullscreen;toolbar.append(fullscreen);
    function download(content,type,name){const blob=content instanceof Blob?content:new Blob([content],{type}),url=URL.createObjectURL(blob),anchor=make('a');anchor.href=url;anchor.download=model.record.figure_id+'-exploration.'+name;doc.body.append(anchor);anchor.click();anchor.remove();root.setTimeout(()=>URL.revokeObjectURL(url),1000);}
    function configuration(){return {format:'devbench.figure-exploration/1',figure_id:model.record.figure_id,context:options.context||{},panel_id:panel().panel_id,viewport:view.slice(),visible_categories:panel().color.kind==='categorical'?Array.from(visible):null,category_colors:{...colors},palette:'Okabe-Ito display default; editable',saved_category_colors:{...(panel().color.colors||{})},axis_meanings:{x:panel().x_axis,y:panel().y_axis},point_radius:Number(size.value),point_stroke:'#ffffff',point_stroke_width:1,opacity:Number(opacity.value),observation_ids:Array.from(selection),scope:'Local display only; not a retained presentation revision or recomputation.'};}
    function exportLegend(){const color=panel().color;if(color.kind==='categorical')return Array.from(visible).map(category=>({label:category,color:colors[category]}));return Array.from({length:6},(_,i)=>{const value=color.scale.min+(color.scale.max-color.scale.min)*i/5;return {label:format(value),color:continuousColor(value)};});}
    function svg(){const theme=palette(),entries=exportLegend(),legendHeight=Math.ceil(entries.length/2)*18+30,totalHeight=height+65+legendHeight;const parts=['<svg xmlns="http://www.w3.org/2000/svg" width="'+width+'" height="'+totalHeight+'" viewBox="0 0 '+width+' '+totalHeight+'"><rect width="100%" height="100%" fill="'+escapeXML(theme.paper)+'"/><g font-family="-apple-system, Inter, sans-serif" fill="'+escapeXML(theme.ink)+'"><text x="'+margins.left+'" y="22" font-size="14">'+escapeXML(panel().title)+'</text><text x="'+width/2+'" y="'+(height+8)+'" text-anchor="middle" font-size="11">'+escapeXML(panel().x_axis.label)+'</text><text transform="translate(18 '+height/2+') rotate(-90)" text-anchor="middle" font-size="11">'+escapeXML(panel().y_axis.label)+'</text>'];
      const xTicks=niceTicks(view[0],view[1],5),yTicks=niceTicks(view[2],view[3],5);
      for(const value of xTicks){const x=screenX(value);parts.push('<path d="M'+x+' '+margins.top+'V'+(height-margins.bottom)+'" fill="none" stroke="'+escapeXML(theme.grid)+'" stroke-dasharray="3 4"/><text x="'+x+'" y="'+(height-margins.bottom+20)+'" text-anchor="middle" font-size="11">'+escapeXML(formatTick(value,xTicks.step))+'</text>');}
      for(const value of yTicks){const y=screenY(value);parts.push('<path d="M'+margins.left+' '+y+'H'+(width-margins.right)+'" fill="none" stroke="'+escapeXML(theme.grid)+'" stroke-dasharray="3 4"/><text x="'+(margins.left-8)+'" y="'+(y+3)+'" text-anchor="end" font-size="11">'+escapeXML(formatTick(value,yTicks.step))+'</text>');}
      for(const point of screenPoints){parts.push('<circle cx="'+point.x+'" cy="'+point.y+'" r="'+Number(size.value)+'" fill="'+escapeXML(colorFor(point.row))+'" opacity="'+Number(opacity.value)+'" stroke="#ffffff" stroke-width="1"><title>'+escapeXML(point.row.observation_id)+'</title></circle>');if(selection.has(point.row.observation_id))parts.push('<circle cx="'+point.x+'" cy="'+point.y+'" r="'+(Number(size.value)+2)+'" fill="none" stroke="'+escapeXML(theme.ink)+'" stroke-width="1.5"/>');}
      parts.push('<text x="'+margins.left+'" y="'+(height+32)+'" font-size="10">'+escapeXML(screenPoints.length+' viewport points / '+model.ids.length+' analysis cells; exploration export, no recomputation')+'</text><text x="'+margins.left+'" y="'+(height+52)+'" font-size="11">'+escapeXML(panel().color.label)+'</text>');
      entries.forEach((entry,index)=>{const x=margins.left+(index%2)*(width-margins.left-margins.right)/2,y=height+66+Math.floor(index/2)*18;parts.push('<rect x="'+x+'" y="'+(y-9)+'" width="10" height="10" fill="'+escapeXML(entry.color)+'"/><text x="'+(x+16)+'" y="'+y+'" font-size="10">'+escapeXML(entry.label)+'</text>');});
      parts.push('</g><metadata>'+escapeXML(JSON.stringify(configuration()))+'</metadata></svg>');return parts.join('');}
    function exportPNG(){
      draw();const entries=exportLegend(),image=doc.createElement('canvas'),legendHeight=Math.ceil(entries.length/2)*18+35;
      image.width=width*2;image.height=(height+100+legendHeight)*2;const ctx=image.getContext('2d'),theme=palette();
      ctx.scale(2,2);ctx.fillStyle=theme.paper;ctx.fillRect(0,0,width,image.height/2);ctx.drawImage(canvas,0,30,width,height);
      ctx.fillStyle=theme.ink;ctx.font='13px sans-serif';ctx.fillText(panel().title,72,21);ctx.font='10px sans-serif';
      ctx.fillText('Exploration view · '+screenPoints.length+' viewport points / '+model.ids.length+' analysis cells · no recomputation',72,height+58);
      ctx.fillText(panel().color.label,72,height+78);
      entries.forEach((entry,index)=>{const x=72+(index%2)*(width-96)/2,y=height+94+Math.floor(index/2)*18;
        ctx.fillStyle=entry.color;ctx.fillRect(x,y-9,10,10);ctx.fillStyle=theme.ink;ctx.fillText(entry.label,x+16,y);});
      image.toBlob(blob=>{if(blob)download(blob,'image/png','png');});
    }
    exportActions.append(button('SVG',()=>{draw();download(svg(),'image/svg+xml','svg');}),button('PNG · 2×',exportPNG),button('Saved table CSV',()=>download(toCSV(panel().rows),'text/csv;charset=utf-8','csv')),button('Selected IDs JSON',()=>download(JSON.stringify(configuration(),null,2),'application/json','json')));
    panelPicker.addEventListener('change',()=>{stopAnimation();panelIndex=Number(panelPicker.value);view=panel().bounds.slice();resetCategories();page=0;renderLegend();render();updateSelection();});
    for(const control of [size,opacity])control.addEventListener('input',render);
    search.addEventListener('input',()=>{page=0;renderTable();});selectedOnly.addEventListener('change',()=>{page=0;renderTable();});tableDetails.addEventListener('toggle',renderTable);
    const local=event=>{const rect=canvas.getBoundingClientRect();return [(event.clientX-rect.left)*width/rect.width,(event.clientY-rect.top)*height/rect.height];};
    canvas.addEventListener('wheel',event=>{if(event.ctrlKey||event.metaKey){event.preventDefault();const [x,y]=local(event);zoom(event.deltaY>0?1.12:.89,x,y);}},{passive:false});
    canvas.addEventListener('pointerdown',event=>{if(event.button!==0)return;const start=local(event);if(!inViewport(...start))return;stopAnimation();canvas.setPointerCapture(event.pointerId);drag={start,last:start,points:[start],view:view.slice()};canvas.focus();});
    canvas.addEventListener('pointermove',event=>{const point=local(event);if(drag){drag.last=point;if(modePicker.value==='pan'){const dx=(point[0]-drag.start[0])/(width-margins.left-margins.right)*(drag.view[1]-drag.view[0]),dy=(point[1]-drag.start[1])/(height-margins.top-margins.bottom)*(drag.view[3]-drag.view[2]);view=[drag.view[0]-dx,drag.view[1]-dx,drag.view[2]+dy,drag.view[3]+dy];}else if(drag.points.length<10000)drag.points.push(point);render();return;}
      if(hoverFrame)return;hoverFrame=root.requestAnimationFrame(()=>{hoverFrame=0;let best=null,distance=100;const bx=Math.floor(point[0]/24),by=Math.floor(point[1]/24);for(let ix=bx-1;ix<=bx+1;ix++)for(let iy=by-1;iy<=by+1;iy++)for(const candidate of grid.get(ix+':'+iy)||[]){const d=(candidate.x-point[0])**2+(candidate.y-point[1])**2;if(d<distance){best=candidate;distance=d;}}
        hover.textContent=best?best.row.observation_id+' · '+panel().x_axis.label+' '+format(best.row.x)+' · '+panel().y_axis.label+' '+format(best.row.y)+' · '+panel().color.label+' '+best.row.color+' · '+Object.entries(best.row.metadata).slice(0,6).map(([key,value])=>key+': '+String(value)).join(' · '):'Hover a point for its exact observation identity. Ctrl / ⌘ + scroll to zoom.';});});
    canvas.addEventListener('pointerup',event=>{if(!drag)return;const gesture=drag;drag=null;if(modePicker.value!=='pan'&&Math.hypot(gesture.last[0]-gesture.start[0],gesture.last[1]-gesture.start[1])>3){const vertices=modePicker.value==='box'?[gesture.start,[gesture.last[0],gesture.start[1]],gesture.last,[gesture.start[0],gesture.last[1]]]:gesture.points;if(vertices.length>=3)selection=new Set(selectPolygon(panel(),vertices.map(([x,y])=>[dataX(x),dataY(y)]),visible));updateSelection();}try{canvas.releasePointerCapture(event.pointerId);}catch(_){}render();});
    canvas.addEventListener('pointercancel',()=>{drag=null;render();});
    canvas.addEventListener('keydown',event=>{const dx=(view[1]-view[0])*.1,dy=(view[3]-view[2])*.1;if(['+','=','-','ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Escape','0'].includes(event.key)){event.preventDefault();stopAnimation();}if(['+','='].includes(event.key))zoom(.8);else if(event.key==='-')zoom(1.25);else if(event.key==='0')reset();else if(event.key==='Escape'){drag=null;selection.clear();updateSelection();render();}else if(event.key.startsWith('Arrow')){if(event.key==='ArrowLeft')view=[view[0]-dx,view[1]-dx,view[2],view[3]];if(event.key==='ArrowRight')view=[view[0]+dx,view[1]+dx,view[2],view[3]];if(event.key==='ArrowUp')view=[view[0],view[1],view[2]+dy,view[3]+dy];if(event.key==='ArrowDown')view=[view[0],view[1],view[2]-dy,view[3]-dy];render();}});
    const observer=root.ResizeObserver?new root.ResizeObserver(render):null;observer?.observe(plot);
    const themeObserver=root.MutationObserver?new root.MutationObserver(render):null;themeObserver?.observe(doc.documentElement,{attributes:true,attributeFilter:['data-theme','class']});
    const controller={destroy(){disposed=true;stopAnimation();observer?.disconnect();themeObserver?.disconnect();if(frame)root.cancelAnimationFrame(frame);if(hoverFrame)root.cancelAnimationFrame(hoverFrame);hostedTools?.remove();container.replaceChildren();delete container.__scientificFigure;},getSelection:()=>Array.from(selection),getConfiguration:configuration,getPerformance:()=>({last_render_ms:lastRenderMs,viewport_points:screenPoints.length,population_n:model.ids.length,display_sampling:false,device_pixel_ratio:root.devicePixelRatio||1,canvas_width:canvas.width,canvas_height:canvas.height}),exportSVG(){draw();return svg();},reset,redraw:render};
    container.__scientificFigure=controller;resetCategories();renderLegend();updateSelection();render();return controller;
  }
  const api={mount,prepare,selectPolygon,toCSV,escapeXML,niceTicks,formatTick,displayCategoryColors,interpolateViewport,limits:{population:MAX_POINTS,panel_rows:MAX_PANEL_ROWS},version:'1.1.0'};
  root.ScientificFigure=api;
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
