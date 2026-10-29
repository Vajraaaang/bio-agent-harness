/* Deterministic, browser-only presentation. No workbench API or analysis records. */
(function (global) {
  'use strict';
  const groups = ['T-like', 'B-like', 'Myeloid-like', 'Stromal-like', 'Endothelial-like', 'Cycling-like'];
  const colors = ['#61dfba', '#86baff', '#f7bd70', '#d59af7', '#f0869e', '#d8e87a'];
  const batchColors = ['#71d9d1', '#b5a1fc', '#f6b77e'];
  const modules = ['Immune', 'Antigen', 'Myeloid', 'Matrix', 'Vascular', 'Cycle', 'Stress', 'Ribosome'];
  const seed = 20261029;
  function generate(value) {
    let state = value >>> 0;
    const random = () => { state = (Math.imul(1664525, state) + 1013904223) >>> 0; return (state + 1) / 4294967297; };
    const normal = () => Math.sqrt(-2 * Math.log(random())) * Math.cos(2 * Math.PI * random());
    const centers = [[-3.8,1.7],[-1.3,3.3],[2.6,2.2],[3.9,-1.9],[.3,-3],[-3.4,-2.5]];
    const cells = [];
    for (let batch = 0; batch < 3; batch++) {
      for (let i = 0; i < 100000; i++) {
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
    return {provenance: {kind:'synthetic', seed:value, live_trials:0, biological_observations:0,
      coordinate_method:'generated latent coordinates; not computed UMAP',
      interpretation:'Illustrative cell-like groups and atlas batches · 100k each; no biological or clinical evidence.'}, cells};
  }
  const select = (cells, batch) => batch === 'all' ? cells.slice() : cells.filter(cell => cell.batch === Number(batch));
  function composition(cells) {
    const counts = Array.from({length:3}, () => Array(6).fill(0));
    cells.forEach(cell => counts[cell.batch][cell.group]++);
    return counts;
  }
  const text = (x,y,label,extra='') => `<text x="${x}" y="${y}" ${extra}>${label}</text>`;
  const circle = (x,y,r,fill,extra='') => `<circle cx="${x.toFixed(2)}" cy="${y.toFixed(2)}" r="${r}" fill="${fill}" ${extra}/>`;
  function svg(content, title, height=320) {
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 ${height}" role="img" aria-label="${title}"><title>${title} — synthetic data</title><rect width="600" height="${height}" fill="#111e26"/>${content}${text(581,height-9,'SYNTHETIC', 'text-anchor="end" fill="#91a5ad" font-size="9" letter-spacing="2"')}</svg>`;
  }
  function scatter(host, cells, mode, quality=false) {
    const doc=host.ownerDocument, canvas=doc.createElement('canvas');
    canvas.width=1200;canvas.height=quality?640:740;
    canvas.setAttribute('role','img');
    canvas.setAttribute('aria-label',quality?'Synthetic read counts versus detected genes; every selected cell drawn':'Synthetic latent cell landscape; every selected cell drawn; not computed UMAP');
    canvas.dataset.pointCount=String(cells.length);
    const ctx=canvas.getContext('2d');ctx.scale(2,2);
    const height=quality?320:370;
    ctx.fillStyle='#111e26';ctx.fillRect(0,0,600,height);
    ctx.strokeStyle='#24343c';ctx.lineWidth=.5;
    for(let x=65;x<560;x+=80){ctx.beginPath();ctx.moveTo(x,25);ctx.lineTo(x,height-45);ctx.stroke();}
    for(let y=50;y<height-45;y+=60){ctx.beginPath();ctx.moveTo(45,y);ctx.lineTo(565,y);ctx.stroke();}
    const bounds=cells.reduce((a,c)=>[Math.min(a[0],c.x),Math.max(a[1],c.x),Math.min(a[2],c.y),Math.max(a[3],c.y),Math.max(a[4],c.reads),Math.max(a[5],c.genes)],[0,0,0,0,1,1]);
    const px=c=>quality?55+c.reads/bounds[4]*500:45+(c.x-bounds[0])/(bounds[1]-bounds[0])*510;
    const py=c=>quality?267-c.genes/bounds[5]*237:320-(c.y-bounds[2])/(bounds[3]-bounds[2])*290;
    const palette=quality||mode==='batch'?batchColors:colors;
    // Draw all cells, bucketed only to reduce fill-style changes. No decimation.
    ctx.globalAlpha=quality?.12:.16;
    for(let g=0;g<palette.length;g++){
      ctx.fillStyle=palette[g];
      for(const c of cells)if((quality||mode==='batch'?c.batch:c.group)===g)ctx.fillRect(px(c),py(c),.8,.8);
    }
    ctx.globalAlpha=1;ctx.font='11px sans-serif';ctx.fillStyle='#b3c4cb';
    if(quality){
      ctx.fillText('Detected genes',48,17);ctx.fillText('Generated read count →',210,308);
      for(let i=0;i<=4;i++){const v=Math.round(bounds[4]*i/4);ctx.fillText(v>=1000?(v/1000).toFixed(1)+'k':String(v),50+i*125,287);}
      for(let i=0;i<=3;i++)ctx.fillText(String(Math.round(bounds[5]*i/3)),5,270-i*79);
    }else{
      ctx.fillText('Generated latent coordinate 1 →',48,346);
      if(mode==='group')groups.forEach((name,g)=>{
        let x=0,y=0,n=0;for(const c of cells)if(c.group===g){x+=px(c);y+=py(c);n++;}
        if(!n)return;ctx.textAlign='center';ctx.font='12px sans-serif';ctx.lineWidth=4;ctx.strokeStyle='#111e26';ctx.strokeText(name,x/n,y/n-24);ctx.fillStyle='#edf4f5';ctx.fillText(name,x/n,y/n-24);
      });
    }
    ctx.textAlign='right';ctx.font='9px monospace';ctx.fillStyle='#91a5ad';ctx.fillText('SYNTHETIC · ALL '+cells.length.toLocaleString('en-US')+' POINTS',581,height-9);
    host.replaceChildren(canvas);
  }
  function heatmap(cells) {
    let content = '';
    groups.forEach((name,g)=>{
      const rows=cells.filter(c=>c.group===g);
      content+=text(117,58+g*34,name,'text-anchor="end" fill="#b3c4cb" font-size="11"');
      modules.forEach((_,m)=>{
        const mean=rows.length?rows.reduce((sum,c)=>sum+c.expression[m],0)/rows.length:0;
        const fraction=rows.length?rows.filter(c=>c.expression[m]>1).length/rows.length:0;
        content+=circle(150+m*55,54+g*34,2+fraction*11,colors[g],`opacity="${(.15+mean/5*.85).toFixed(3)}"`);
      });
    });
    modules.forEach((name,m)=>content+=text(150+m*55,273,name,`transform="rotate(-30 ${150+m*55} 273)" text-anchor="end" fill="#b3c4cb" font-size="10"`));
    return svg(content,'Generated module scores by cell-like group; size is fraction above one, brightness is mean');
  }
  function density(cells) {
    let content='';
    for(let batch=0;batch<3;batch++){
      const rows=cells.filter(c=>c.batch===batch), counts=Array(36).fill(0);
      rows.forEach(c=>counts[Math.min(35,Math.floor(c.mito*2))]++);
      const maximum=Math.max(1,...counts), base=80+batch*78;
      const points=counts.map((n,i)=>`${75+i*13.4},${base-n/maximum*54}`);
      content+=`<path d="M75 ${base} L${points.join(' L')} L544 ${base}Z" fill="${batchColors[batch]}" fill-opacity=".24" stroke="${batchColors[batch]}" stroke-width="1.5"/>`;
      content+=text(19,base-22,`B0${batch+1}`,'fill="#b3c4cb" font-size="11"')+text(560,base-22,`n=${rows.length}`,'text-anchor="end" fill="#b3c4cb" font-size="10"');
    }
    [0,5,10,15].forEach(t=>content+=text(75+t*26.8,271,String(t),'fill="#91a5ad" font-size="10"'));
    content+=text(300,300,'Generated mitochondrial fraction (%)','text-anchor="middle" fill="#b3c4cb" font-size="10"');
    return svg(content,'Generated mitochondrial fraction distributions; each batch scaled to its own peak');
  }
  function proportions(cells) {
    let content='';
    composition(cells).forEach((counts,b)=>{
      const total=counts.reduce((a,c)=>a+c,0);let x=87;
      content+=text(25,67+b*72,`B0${b+1}`,'fill="#b3c4cb" font-size="12"');
      counts.forEach((n,g)=>{const w=total?n/total*430:0;content+=`<rect x="${x}" y="${43+b*72}" width="${w}" height="36" fill="${colors[g]}"/>`;x+=w;});
      content+=text(532,67+b*72,`${total}`,'fill="#b3c4cb" font-size="11"');
    });
    [0,25,50,75,100].forEach(t=>content+=text(87+t*4.3,275,`${t}%`,'text-anchor="middle" fill="#91a5ad" font-size="10"'));
    return svg(content,'Cell-like group proportions within each generated batch');
  }
  function mount(doc) {
    const triggers=doc.querySelectorAll('[data-synthetic-gallery]');
    if(!triggers.length)return;
    const dialog=doc.createElement('dialog');dialog.id='synthetic-gallery';dialog.setAttribute('aria-labelledby','synthetic-title');
    dialog.innerHTML=`<div class="synthetic-shell"><header class="synthetic-head"><div><p class="synthetic-eyebrow">RNA WORKBENCH / VISUAL EXPLORER</p><h2 id="synthetic-title">Synthetic atlas<span> / 01</span></h2></div><button type="button" class="synthetic-close" aria-label="Close synthetic atlas">Close ×</button></header>
      <div class="synthetic-disclosure"><strong>Synthetic data</strong><span>Generated in your browser. No biological observations, live trial, or fitted UMAP.</span></div>
      <div class="synthetic-intro"><p>A landscape of cells.<br><em>A study in variation.</em></p><div class="synthetic-stats"><div><strong id="synthetic-count">300,000</strong><span>generated cells</span></div><div><strong>03</strong><span>atlas batches · 100k each</span></div><div><strong>06</strong><span>cell-like groups</span></div></div></div>
      <div class="synthetic-controls"><label>Batch <select id="synthetic-batch"><option value="all">All batches</option><option value="0">B01 · baseline</option><option value="1">B02 · moderate shift</option><option value="2">B03 · higher depth</option></select></label><label>Landscape color <select id="synthetic-color"><option value="group">Cell-like group</option><option value="batch">Technical batch</option></select></label><button type="button" id="synthetic-download">Download figure SVG</button></div>
      <div id="synthetic-legend" class="synthetic-legend">${groups.map((name,i)=>`<span><i class="synthetic-key key-${i}"></i>${name}</span>`).join('')}</div>
      <div class="synthetic-grid"><article class="synthetic-card synthetic-landscape"><div class="synthetic-card-title"><span>01 / CELL LANDSCAPE</span><span id="synthetic-landscape-count">300,000 points</span></div><div id="synthetic-embedding"></div><p>All selected cells are drawn, without downsampling. Generated coordinates and cell-like labels are designed into the generator.</p></article>
      <article class="synthetic-card"><div class="synthetic-card-title"><span>02 / MODULE SIGNATURES</span><span>8 illustrative modules</span></div><div id="synthetic-heatmap"></div><p>Dot size: fraction with score &gt; 1. Brightness: mean generated score (0–5). Labels are illustrative.</p></article>
      <article class="synthetic-card"><div class="synthetic-card-title"><span>03 / LIBRARY COMPLEXITY</span><span>B01 · B02 · B03</span></div><div class="synthetic-batch-key"><span class="batch-0">● B01</span><span class="batch-1">● B02</span><span class="batch-2">● B03</span></div><div id="synthetic-quality"></div><p>Generated depth and gene count are correlated by construction. Color identifies technical batch.</p></article>
      <article class="synthetic-card"><div class="synthetic-card-title"><span>04 / BATCH DISTRIBUTIONS</span><span>Shape, not density inference</span></div><div id="synthetic-density"></div><p>Histogram outlines at 0.5 percentage-point intervals, normalized separately to each batch peak.</p></article>
      <article class="synthetic-card"><div class="synthetic-card-title"><span>05 / CELL COMPOSITION</span><span>Within-batch proportions</span></div><div id="synthetic-proportions"></div><p>Counts reflect the selected generated cells. No independent biological replication is represented.</p></article></div>
      <footer class="synthetic-footer"><div><strong>Reproducible by design.</strong><p>Seed ${seed} · browser-only generator v1 · no network requests or workbench writes</p></div><p>This gallery demonstrates presentation. It does not establish biological findings, batch correction quality, or model performance.</p></footer></div>`;
    doc.body.append(dialog);
    let data=null;
    const byId=id=>dialog.querySelector('#'+id);
    function draw(){
      const cells=select(data.cells,byId('synthetic-batch').value);
      const batchMode=byId('synthetic-color').value==='batch';
      byId('synthetic-legend').innerHTML=(batchMode?['B01 · baseline','B02 · moderate shift','B03 · higher depth']:groups).map((name,i)=>`<span><i class="synthetic-key ${batchMode?'batch-key-':'key-'}${i}"></i>${name}</span>`).join('');
      byId('synthetic-count').textContent=cells.length.toLocaleString('en-US');
      byId('synthetic-landscape-count').textContent=`${cells.length.toLocaleString('en-US')} points`;
      scatter(byId('synthetic-embedding'),cells,byId('synthetic-color').value);
      byId('synthetic-heatmap').innerHTML=heatmap(cells);
      scatter(byId('synthetic-quality'),cells,'batch',true);
      byId('synthetic-density').innerHTML=density(cells);
      byId('synthetic-proportions').innerHTML=proportions(cells);
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
      const canvas=byId('synthetic-embedding').querySelector('canvas');
      if(!canvas)return;
      const source=doc.createElementNS('http://www.w3.org/2000/svg','svg');
      source.setAttribute('xmlns','http://www.w3.org/2000/svg');source.setAttribute('viewBox','0 0 1200 740');
      const image=doc.createElementNS('http://www.w3.org/2000/svg','image');image.setAttribute('href',canvas.toDataURL('image/png'));image.setAttribute('width','1200');image.setAttribute('height','740');source.append(image);
      const title=doc.createElementNS('http://www.w3.org/2000/svg','title');title.textContent='SYNTHETIC cell landscape — all selected points, raster Canvas rendering';source.prepend(title);
      const metadata=doc.createElementNS('http://www.w3.org/2000/svg','metadata');
      metadata.textContent=JSON.stringify({...data.provenance,batch:byId('synthetic-batch').value,color:byId('synthetic-color').value,point_count:Number(canvas.dataset.pointCount),rendering:'Canvas raster embedded in SVG; no downsampling'});source.prepend(metadata);
      const blob=new Blob([source.outerHTML],{type:'image/svg+xml'}),url=URL.createObjectURL(blob),link=doc.createElement('a');
      link.href=url;link.download='synthetic-cell-landscape.svg';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    };
  }
  global.WorkbenchSynthetic={generate,select,composition,mount};
  if(typeof document!=='undefined')mount(document);
})(window);
