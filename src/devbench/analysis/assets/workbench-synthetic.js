/* Deterministic, browser-only presentation. No workbench API or analysis records. */
(function (global) {
  'use strict';
  const groups = ['T-like', 'B-like', 'Myeloid-like', 'Stromal-like', 'Endothelial-like', 'Cycling-like'];
  const colors = ['#61dfba', '#86baff', '#f7bd70', '#d59af7', '#f0869e', '#d8e87a'];
  const batchColors = ['#71d9d1', '#b5a1fc', '#f6b77e'];
  const modules = ['Immune', 'Antigen', 'Myeloid', 'Matrix', 'Vascular', 'Cycle', 'Stress', 'Ribosome'];
  const seed = 20261029;
  function generate(value, batchSizes=[100000,100000,100000]) {
    if(!Array.isArray(batchSizes)||batchSizes.length!==3||batchSizes.some(n=>!Number.isSafeInteger(n)||n<1)||batchSizes.reduce((a,b)=>a+b,0)>300000)throw Error('Use three positive batch sizes totaling at most 300000 generated cells.');
    let state = value >>> 0;
    const random = () => { state = (Math.imul(1664525, state) + 1013904223) >>> 0; return (state + 1) / 4294967297; };
    const normal = () => Math.sqrt(-2 * Math.log(random())) * Math.cos(2 * Math.PI * random());
    const centers = [[-3.8,1.7],[-1.3,3.3],[2.6,2.2],[3.9,-1.9],[.3,-3],[-3.4,-2.5]];
    const cells = [];
    for (let batch = 0; batch < 3; batch++) {
      for (let i = 0; i < batchSizes[batch]; i++) {
        const mixture = [[.34,.54,.72,.84,.94,1],[.24,.43,.68,.83,.92,1],[.15,.42,.62,.74,.88,1]][batch];
        const pick=random(), group=mixture.findIndex(cutoff=>pick<cutoff);
        const a = normal(), b = normal(), center = centers[group];
        const reads = Math.round(Math.exp(8.1 + group * .1 + batch * .16 + normal() * .4));
        cells.push({id: `synthetic-${batch + 1}-${String(i + 1).padStart(4,'0')}`, batch, group,
          x: center[0] + a * .63 + b * .2 + (batch - 1) * .26,
          y: center[1] + b * .48 + a * (group % 2 ? -.28 : .25),
          reads, genes: Math.round(Math.max(150, 360 + Math.sqrt(reads) * 19 + normal() * 120)),
          mito: Math.min(35, Math.max(.2, 3.2 + batch * 1.4 + group * .3 + normal() * 1.4)),
          expression: modules.map((_,m) => Math.max(0, Math.min(5, (m === group ? 3.4 : .45) + (m > 5 ? .8 + batch * .2 : 0) + normal() * .55)))});
      }
    }
    return {provenance: {kind:'synthetic', seed:value, live_trials:0, biological_observations:0, batch_sizes:batchSizes.slice(),
      coordinate_method:'generated latent coordinates; not computed UMAP',
      interpretation:'Illustrative cell-like groups and three generated batches; no biological or clinical evidence.'}, cells};
  }
  const select = (cells, batch) => batch === 'all' ? cells.slice() : cells.filter(cell => cell.batch === Number(batch));
  function composition(cells) {
    const counts = Array.from({length:3}, () => Array(6).fill(0));
    cells.forEach(cell => counts[cell.batch][cell.group]++);
    return counts;
  }
  const escapeXML=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'}[c]));
  const drawing=()=>{if(!global.WorkbenchSummaryPlots)throw Error('The shared generated-summary renderer is required.');return global.WorkbenchSummaryPlots;};
  const text = (x,y,label,extra='') => `<text x="${x}" y="${y}" ${extra}>${escapeXML(label)}</text>`;
  const circle = (x,y,r,fill,extra='') => `<circle cx="${x.toFixed(2)}" cy="${y.toFixed(2)}" r="${r}" fill="${fill}" ${extra}/>`;
  function svg(content, title, height=320) {
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 ${height}" role="img" aria-label="${escapeXML(title)}"><title>${escapeXML(title)} — synthetic data</title><rect width="600" height="${height}" fill="#111e26"/>${content}${text(581,height-9,'SYNTHETIC', 'text-anchor="end" fill="#91a5ad" font-size="9" letter-spacing="2"')}</svg>`;
  }
  function scatterScene(cells,mode,quality=false){
    if(!cells.length||cells.length>300000||!['group','batch'].includes(mode))throw Error('A bounded generated population and color mode are required.');
    const seen=new Set();for(const c of cells){if(typeof c.id!=='string'||seen.has(c.id)||![c.x,c.y,c.reads,c.genes].every(Number.isFinite))throw Error('Generated identities and finite coordinates are required.');seen.add(c.id);}
    const api=drawing(),height=quality?350:400,left=66,right=570,top=38,bottom=height-80;
    const bounds=cells.reduce((a,c)=>[Math.min(a[0],quality?c.reads:c.x),Math.max(a[1],quality?c.reads:c.x),Math.min(a[2],quality?c.genes:c.y),Math.max(a[3],quality?c.genes:c.y)],[0,0,0,0]);
    const xt=api.niceTicks(bounds[0],bounds[1]),yt=api.niceTicks(bounds[2],bounds[3]),px=v=>left+(v-xt[0])/(xt.at(-1)-xt[0])*(right-left),py=v=>bottom-(v-yt[0])/(yt.at(-1)-yt[0])*(bottom-top);
    const scene={width:600,height,title:quality?'Generated library complexity':'Generated latent cell landscape; not computed UMAP',marks:[]},marks=scene.marks;
    const label=(x,y,text,anchor='start',size=11,fill='#b3c4cb')=>marks.push({type:'text',x,y,text,anchor,size,fill});
    marks.push({type:'rect',x:0,y:0,width:600,height,fill:'#111e26'});
    const number=v=>Number(v.toPrecision(4)).toLocaleString('en-US',{maximumFractionDigits:3});
    xt.forEach(v=>{marks.push({type:'polyline',points:[[px(v),top],[px(v),bottom]],stroke:'#24343c',width:1});label(px(v),bottom+23,number(v),'middle',10);});
    yt.forEach(v=>{marks.push({type:'polyline',points:[[left,py(v)],[right,py(v)]],stroke:'#24343c',width:1});label(left-10,py(v)+4,number(v),'end',10);});
    label(left,20,quality?'Detected genes (count)':'Generated latent coordinate 2 (unitless)');label((left+right)/2,height-34,quality?'Generated read count':'Generated latent coordinate 1 (unitless)','middle');
    const palette=quality||mode==='batch'?batchColors:colors;
    for(const c of cells)marks.push({type:'circle',x:px(quality?c.reads:c.x),y:py(quality?c.genes:c.y),radius:.55,fill:palette[quality||mode==='batch'?c.batch:c.group],opacity:quality?.12:.16,key:c.id,role:'observation'});
    if(!quality&&mode==='group')groups.forEach((name,g)=>{let x=0,y=0,n=0;for(const c of cells)if(c.group===g){x+=px(c.x);y+=py(c.y);n++;}if(n){const cx=x/n,cy=Math.max(top+16,y/n-20);marks.push({type:'rect',x:cx-49,y:cy-13,width:98,height:18,fill:'#111e26',opacity:.85});label(cx,cy,name,'middle',11,'#edf4f5');}});
    label(581,height-9,'SYNTHETIC · ALL '+cells.length.toLocaleString('en-US')+' POINTS','end',9,'#91a5ad');return scene;
  }
  function scatter(host,cells,mode,quality=false){
    const canvas=host.ownerDocument.createElement('canvas'),scene=scatterScene(cells,mode,quality);canvas.setAttribute('role','img');canvas.setAttribute('aria-label',scene.title+'; every selected generated cell drawn');canvas.dataset.pointCount=String(cells.length);drawing().drawCanvas(canvas,scene);host.replaceChildren(canvas);
  }
  function scatterSVG(cells,mode,quality=false,provenance={}){
    if(provenance.kind!=='synthetic')throw Error('The atlas exports explicitly generated data only.');
    return drawing().toSVG(scatterScene(cells,mode,quality),{...provenance,kind:'synthetic',live_trials:0,biological_observations:0,color:mode,point_count:cells.length,displayed_member_ids:cells.map(c=>c.id),rendering:'Vector geometry; every selected generated cell; no downsampling',coordinate_method:'generated latent coordinates; not computed UMAP'});
  }
  function heatmap(cells) {
    let content = '';
    groups.forEach((name,g)=>{
      const rows=cells.filter(c=>c.group===g);
      content+=text(117,58+g*34,name,'text-anchor="end" fill="#b3c4cb" font-size="11"');
      modules.forEach((_,m)=>{
        const mean=rows.length?rows.reduce((sum,c)=>sum+c.expression[m],0)/rows.length:null;
        const fraction=rows.length?rows.filter(c=>c.expression[m]>1).length/rows.length:null;
        if(mean===null)content+=text(150+m*55,58+g*34,'NA','text-anchor="middle" fill="#91a5ad" font-size="10"');
        else content+=circle(150+m*55,54+g*34,12*Math.sqrt(fraction),drawing().continuousColor(mean/5),`data-fraction="${fraction}"`);
      });
    });
    modules.forEach((name,m)=>content+=text(150+m*55,273,name,`transform="rotate(-30 ${150+m*55} 273)" text-anchor="end" fill="#b3c4cb" font-size="10"`));
    const stops=drawing().continuousStops();
    content+='<defs><linearGradient id="atlas-module-score">'+stops.map((color,i)=>`<stop offset="${i/(stops.length-1)*100}%" stop-color="${color}"/>`).join('')+'</linearGradient></defs><rect x="155" y="321" width="280" height="12" fill="url(#atlas-module-score)"/>';
    content+=text(295,312,'Mean generated module score','text-anchor="middle" fill="#b3c4cb" font-size="10"');
    [0,1,2,3,4,5].forEach(v=>content+=text(155+v*56,348,String(v),'text-anchor="middle" fill="#b3c4cb" font-size="10"'));
    content+=text(295,371,'Fraction (score > 1) · dot area','text-anchor="middle" fill="#b3c4cb" font-size="10"');
    [.25,.5,1].forEach((fraction,i)=>{content+=circle(220+i*75,394,12*Math.sqrt(fraction),'#b3c4cb',`data-fraction="${fraction}"`);content+=text(220+i*75,422,Math.round(fraction*100)+'%','text-anchor="middle" fill="#b3c4cb" font-size="10"');});
    return svg(content,'Generated module scores by cell-like group; dot area is fraction above one; color is mean score',450);
  }
  function density(cells) {
    let content='';const max=cells.reduce((m,c)=>Math.max(m,c.mito),0),width=.5,bins=Math.max(1,Math.ceil(max/width)),ticks=drawing().niceTicks(0,bins*width),limit=ticks.at(-1),px=v=>75+v/limit*470;
    for(let batch=0;batch<3;batch++){
      const rows=cells.filter(c=>c.batch===batch), counts=Array(bins).fill(0);
      rows.forEach(c=>counts[Math.min(bins-1,Math.floor(c.mito/width))]++);
      const maximum=Math.max(1,...counts), base=80+batch*78;
      const points=counts.flatMap((n,i)=>[`${px(i*width)},${base-n/maximum*54}`,`${px((i+1)*width)},${base-n/maximum*54}`]);
      content+=`<path data-bin-count="${bins}" data-bin-width="${width}" data-bin-upper="${bins*width}" d="M75 ${base} L${points.join(' L')} L${px(bins*width)} ${base}Z" fill="${batchColors[batch]}" fill-opacity=".24" stroke="${batchColors[batch]}" stroke-width="1.5"/>`;
      content+=text(19,base-22,`B0${batch+1}`,'fill="#b3c4cb" font-size="11"')+text(560,base-22,`n=${rows.length}`,'text-anchor="end" fill="#b3c4cb" font-size="10"');
    }
    ticks.forEach(t=>content+=text(px(t),271,String(t),'text-anchor="middle" fill="#91a5ad" font-size="10"'));
    content+=text(300,300,'Generated mitochondrial fraction (%)','text-anchor="middle" fill="#b3c4cb" font-size="10"');
    content+=text(300,322,'Histogram outlines · 0.5 percentage-point bins · per-batch peak','text-anchor="middle" fill="#b3c4cb" font-size="10"');
    return svg(content,'Generated mitochondrial fraction histogram outlines; each batch scaled to its own peak',350);
  }
  function proportions(cells) {
    let content='';
    composition(cells).forEach((counts,b)=>{
      const total=counts.reduce((a,c)=>a+c,0);let x=87;
      content+=text(25,67+b*72,`B0${b+1}`,'fill="#b3c4cb" font-size="12"');
      counts.forEach((n,g)=>{const w=total?n/total*430:0;content+=`<rect x="${x}" y="${43+b*72}" width="${w}" height="36" fill="${colors[g]}"/>`;if(w>40)content+=text(x+w/2,66+b*72,(n/total*100).toFixed(0)+'%',`text-anchor="middle" fill="${drawing().contrastText(colors[g])}" font-size="10"`);x+=w;});
      content+=text(532,67+b*72,`${total}`,'fill="#b3c4cb" font-size="11"');
    });
    [0,25,50,75,100].forEach(t=>content+=text(87+t*4.3,275,`${t}%`,'text-anchor="middle" fill="#91a5ad" font-size="10"'));
    groups.forEach((name,i)=>{const x=70+(i%3)*175,y=309+Math.floor(i/3)*23;content+=`<rect x="${x}" y="${y-8}" width="8" height="8" fill="${colors[i]}"/>`+text(x+13,y,name,'fill="#b3c4cb" font-size="10"');});
    return svg(content,'Cell-like group proportions within each generated batch; cells are not biological replicates',360);
  }
  function showGeneratedChart(host, source) {
    const doc = host.ownerDocument;
    const namespace = 'http://www.w3.org/2000/svg';
    const graphic = new global.DOMParser().parseFromString(source, 'image/svg+xml');
    const tags = new Set(['svg', 'title', 'text', 'circle', 'rect', 'defs', 'linearGradient', 'stop', 'path']);
    const attributes = new Set(['viewBox', 'role', 'aria-label', 'width', 'height', 'x', 'y', 'cx', 'cy', 'r',
      'id', 'fill', 'fill-opacity', 'stroke', 'stroke-width', 'stop-color', 'offset', 'text-anchor', 'font-size',
      'letter-spacing', 'transform', 'd', 'data-fraction', 'data-bin-count', 'data-bin-width', 'data-bin-upper']);
    function copy(item) {
      if (item.nodeType === 3) return doc.createTextNode(item.nodeValue);
      if (item.namespaceURI !== namespace || !tags.has(item.localName)) throw Error('Unexpected generated chart element.');
      const node = doc.createElementNS(namespace, item.localName);
      for (const {name, value} of item.attributes) {
        if (!attributes.has(name)) continue;
        if (['fill', 'stroke', 'stop-color'].includes(name) && !/^(#[0-9a-f]{3,8}|url\(#atlas-module-score\))$/i.test(value)) {
          throw Error('Unexpected generated chart paint.');
        }
        node.setAttribute(name, value);
      }
      for (const child of item.childNodes) node.append(copy(child));
      return node;
    }
    host.replaceChildren(copy(graphic.documentElement));
  }
  function mount(doc) {
    const triggers=doc.querySelectorAll('[data-synthetic-gallery]');
    const template=doc.getElementById('studio-synthetic-atlas');
    if(!triggers.length||!template)return;
    const dialog=doc.createElement('dialog');dialog.id='synthetic-gallery';dialog.setAttribute('aria-labelledby','synthetic-title');
    dialog.append(template.content.cloneNode(true));
    dialog.querySelector('[data-atlas-seed]').textContent=String(seed);
    doc.body.append(dialog);
    let data=null;
    const byId=id=>dialog.querySelector('#'+id);
    function draw(){
      const cells=select(data.cells,byId('synthetic-batch').value);
      const batchMode=byId('synthetic-color').value==='batch';
      const names=batchMode?['B01 · baseline','B02 · moderate shift','B03 · higher depth']:groups;
      byId('synthetic-legend').replaceChildren(...names.map((name,i)=>{
        const entry=doc.createElement('span'),key=doc.createElement('i');
        key.className='synthetic-key '+(batchMode?'batch-key-':'key-')+i;
        entry.append(key,name);return entry;
      }));
      byId('synthetic-count').textContent=cells.length.toLocaleString('en-US');
      byId('synthetic-landscape-count').textContent=`${cells.length.toLocaleString('en-US')} points`;
      scatter(byId('synthetic-embedding'),cells,byId('synthetic-color').value);
      showGeneratedChart(byId('synthetic-heatmap'),heatmap(cells));
      scatter(byId('synthetic-quality'),cells,'batch',true);
      showGeneratedChart(byId('synthetic-density'),density(cells));
      showGeneratedChart(byId('synthetic-proportions'),proportions(cells));
    }
    byId('synthetic-batch').onchange=draw;byId('synthetic-color').onchange=draw;
    dialog.querySelector('.synthetic-close').onclick=()=>dialog.close();
    let opener=null;dialog.addEventListener('close',()=>{if(opener)opener.focus();});
    triggers.forEach(button=>button.addEventListener('click',async()=>{
      opener=button;dialog.showModal();
      if(!data){
        byId('synthetic-count').textContent='Preparing…';dialog.setAttribute('aria-busy','true');
        byId('synthetic-download').disabled=true;byId('synthetic-batch').disabled=true;byId('synthetic-color').disabled=true;
        await new Promise(resolve=>setTimeout(resolve,40));data=generate(seed);
        dialog.removeAttribute('aria-busy');byId('synthetic-download').disabled=false;byId('synthetic-batch').disabled=false;byId('synthetic-color').disabled=false;
      }
      draw();
    }));
    byId('synthetic-download').onclick=()=>{
      if(!data)return;
      const scope=byId('synthetic-export-scope').value,batch=scope==='complete'?'all':byId('synthetic-batch').value,cells=select(data.cells,batch);
      const source=scatterSVG(cells,byId('synthetic-color').value,false,{...data.provenance,scope,batch,source_member_ids:data.cells.map(c=>c.id)});
      const blob=new Blob([source],{type:'image/svg+xml'}),url=URL.createObjectURL(blob),link=doc.createElement('a');
      link.href=url;link.download='synthetic-cell-landscape-'+scope+'.svg';dialog.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
    };
  }
  global.WorkbenchSynthetic={generate,select,composition,scatterScene,scatterSVG,heatmap,density,proportions,mount};
  if(typeof document!=='undefined')mount(document);
})(window);
