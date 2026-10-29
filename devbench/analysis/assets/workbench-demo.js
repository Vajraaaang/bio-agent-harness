/* Offline simulation in the current workbench shell and ScientificFigure canvas. */
(function(root){
  'use strict';
  const projects=[
    {id:'airway',title:'Airway Immune Landscape',seed:20261029,batchSizes:[94817,101206,91440],defaultView:'landscape'},
    {id:'repair',title:'Tissue Repair Atlas',seed:104729,batchSizes:[71283,83519,65874],defaultView:'quality'},
    {id:'immune',title:'Immune Activation Map',seed:130363,batchSizes:[88342,79615,92708],defaultView:'activity'},
    {id:'vascular',title:'Vascular Cell Landscape',seed:155921,batchSizes:[56843,64127,59306],defaultView:'batches'},
    {id:'stromal',title:'Stromal Remodeling Study',seed:196613,batchSizes:[106318,87429,99761],defaultView:'mitochondrial'},
    {id:'cycle',title:'Cell-Cycle Reference',seed:262147,batchSizes:[43719,52864,49173],defaultView:'landscape'}
  ];
  const groups=['T-like','B-like','Myeloid-like','Stromal-like','Endothelial-like','Cycling-like'];
  const colors=['#0072b2','#e69f00','#009e73','#cc79a7','#56b4e9','#d55e00'];
  const batches=['Batch 01','Batch 02','Batch 03'];
  const views={landscape:'Cell landscape · groups',batches:'Cell landscape · batches',quality:'Library complexity',mitochondrial:'Mitochondrial fraction',activity:'Generated immune-module score'};
  const summaryViews=root.WorkbenchSummaryPlots.titles;Object.assign(views,summaryViews);
  const batchSizes=projects[0].batchSizes.slice(),populationSize=batchSizes.reduce((a,b)=>a+b,0);
  function provenanceFor(project){return {kind:'synthetic',seed:project.seed,generator:'workbench-simulation/1',live_trials:0,biological_observations:0,batch_sizes:project.batchSizes.slice()};}
  const provenance=provenanceFor(projects[0]);
  function generateProject(id){
    const project=projects.find(item=>item.id===id);if(!project)throw Error('Choose a defined synthetic project.');
    const generated=root.WorkbenchSynthetic.generate(project.seed,project.batchSizes);
    return {...generated,project:{...project,batchSizes:project.batchSizes.slice()}};
  }
  function conversations(project){
    const prompts={landscape:'Show the cellular landscape across all three generated batches.',batches:'How do the generated batch compositions and technical depth differ?',quality:'Compare generated read depth with detected genes.',mitochondrial:'Where are the higher mitochondrial fractions in this generated population?',activity:'Show how the designed immune module varies across the generated landscape.'};
    const labels={landscape:'Map the cell landscape',batches:'Compare batch structure',quality:'Inspect library complexity',mitochondrial:'Review mitochondrial patterns',activity:'Explore the immune-module signal'};
    return [project.defaultView,...Object.keys(prompts).filter(view=>view!==project.defaultView)].map(view=>({title:labels[view],question:prompts[view],view}));
  }
  function summarize(cells){
    const counts=Array(6).fill(0),immune=Array(6).fill(0),batch=Array.from({length:3},()=>({n:0,reads:0,genes:0,mito:0}));
    let sx=0,sy=0,sxx=0,syy=0,sxy=0;
    for(const c of cells){counts[c.group]++;immune[c.group]+=c.expression[0];const b=batch[c.batch];b.n++;b.reads+=c.reads;b.genes+=c.genes;b.mito+=c.mito;sx+=c.reads;sy+=c.genes;sxx+=c.reads*c.reads;syy+=c.genes*c.genes;sxy+=c.reads*c.genes;}
    const denominator=Math.sqrt((cells.length*sxx-sx*sx)*(cells.length*syy-sy*sy));
    return {n:cells.length,groups:counts,mean_immune_by_group:immune.map((sum,i)=>counts[i]?sum/counts[i]:0),read_gene_correlation:denominator?(cells.length*sxy-sx*sy)/denominator:0,batches:batch.map(b=>({...b,mean_reads:b.n?b.reads/b.n:0,mean_genes:b.n?b.genes/b.n:0,mean_mito:b.n?b.mito/b.n:0}))};
  }
  function figure(data,view='landscape',batch='all'){
    if(Object.hasOwn(summaryViews,view))throw Error('Use the summary renderer for aggregate views.');
    if(!Object.hasOwn(views,view)||!['all','0','1','2'].includes(batch))throw Error('Choose a defined simulation view and batch.');
    const project=projects.find(p=>p.id===(data.project?.id||projects[0].id));
    if(!project||data.provenance?.seed!==project.seed||JSON.stringify(data.provenance?.batch_sizes)!==JSON.stringify(project.batchSizes)||data.cells.length!==project.batchSizes.reduce((a,b)=>a+b,0))throw Error('Generated project data must match its declared seed and batch sizes.');
    const proof=provenanceFor(project);
    const cells=batch==='all'?data.cells:data.cells.filter(c=>c.batch===Number(batch));
    const panelTitle=project.title+' · '+views[view]+(batch==='all'?'':' · '+batches[Number(batch)]),panelId=view+(batch==='all'?'':'-batch-'+batch);
    const quality=view==='quality',continuous=['mitochondrial','activity'].includes(view),byBatch=['batches','quality'].includes(view);
    const values=cells.map(c=>view==='mitochondrial'?c.mito:view==='activity'?c.expression[0]:null);
    let minimum=Infinity,maximum=-Infinity;if(continuous)for(const value of values){minimum=Math.min(minimum,value);maximum=Math.max(maximum,value);}
    const label=view==='mitochondrial'?'Generated mitochondrial fraction (%)':view==='activity'?'Generated immune-module score':byBatch?'Generated batch':'Designed cell-like group';
    const color=continuous?{kind:'continuous',field:view,label,scale:{kind:'linear',min:minimum,max:maximum,clipped_n:0}}:{kind:'categorical',field:byBatch?'batch':'group',label,colors:Object.fromEntries((byBatch?batches:groups).map((name,i)=>[name,colors[i]]))};
    const rows=cells.map((c,i)=>({observation_id:c.id,x:quality?c.reads:c.x,y:quality?c.genes:c.y,color:continuous?values[i]:byBatch?batches[c.batch]:groups[c.group],status:'rendered',exclusion_reason:null,metadata:{origin:'synthetic',batch:batches[c.batch],group:groups[c.group],generated_reads:c.reads,generated_genes:c.genes,generated_mito_percent:c.mito}}));
    const axis=(field,label,unit)=>({field,label,unit,transform:'none'});
    return {record:{format:'devbench.simulated-figure/1',family:'S01',figure_id:project.id+'-simulation-'+panelId,title:panelTitle,analysis_n:cells.length,rendered_n:cells.length,provenance:{...proof,batch_sizes:proof.batch_sizes.slice()},semantics:{unit:'cell',representation_or_layer:'generated:latent-coordinates-and-measurements; no fitted UMAP',limitations:['Synthetic data generated in this browser; no biological observations or live trial.','Group labels, separation and technical shifts are designed into the generator.']},display_options:{}},payload:{format:'devbench.simulated-figure-data/1',payload_kind:'observation_points',key_columns:['observation_id'],population_ids:rows.map(r=>r.observation_id),provenance:{...proof,batch_sizes:proof.batch_sizes.slice()},panels:[{panel_id:panelId,title:panelTitle,x_axis:axis(quality?'reads':'latent_x',quality?'Generated read count':'Generated latent coordinate 1',quality?'reads':'arbitrary units'),y_axis:axis(quality?'genes':'latent_y',quality?'Generated detected genes':'Generated latent coordinate 2',quality?'genes':'arbitrary units'),color,rows,labels:[]}]}};
  }
  function mount(doc){
    const trigger=doc.querySelector('[data-workbench-demo]');if(!trigger)return;
    const byId=id=>doc.getElementById(id),app=byId('app'),login=byId('login'),canvas=byId('interactive-figure');
    let active=false,attempted=Boolean(root.location?.hash),data=null,stats=null,controller=null,selected=0,currentProject=projects[0],currentView=projects[0].defaultView,currentBatch='all',preparing=false;
    const hidden=new Map(),nodes=[];
    const setHidden=(node,value)=>{if(!hidden.has(node))hidden.set(node,node.hidden);node.hidden=value;};
    const hideChildren=(node,except=[])=>{for(const child of node.children)if(!except.includes(child))setHidden(child,true);};
    const add=(parent,className,html)=>{const node=doc.createElement('div');node.className=className;node.dataset.demoInteractive='true';node.innerHTML=html;parent.append(node);nodes.push(node);return node;};
    const number=n=>Math.round(n).toLocaleString('en-US');
    function gate(event){
      if(!active)return;
      if(event.target.closest?.('[data-demo-interactive],#interactive-figure'))return;
      event.preventDefault();event.stopImmediatePropagation();
    }
    const events=['click','input','change','submit','keydown','pointerdown'];
    events.forEach(name=>doc.addEventListener(name,gate,true));
    byId('connect').addEventListener('click',()=>{attempted=true;},true);
    let side,chat,head;
    function renderChat(){
      const item=conversations(currentProject)[selected],largest=stats.groups.indexOf(Math.max(...stats.groups));
      const descriptions={landscape:`The generated population contains six designed cell-like groups. ${groups[largest]} is the largest group: <strong>${number(stats.groups[largest])} cells (${(stats.groups[largest]/stats.n*100).toFixed(1)}%)</strong>.`,batches:`Batch 03 has <strong>${(stats.batches[2].mean_reads/stats.batches[0].mean_reads).toFixed(2)}×</strong> the mean generated read depth of Batch 01. The batches contain ${stats.batches.map(b=>number(b.n)).join(', ')} generated cells; their sizes and mixtures were deliberately varied.`,quality:`Generated read depth and detected-gene count have a Pearson correlation of <strong>${stats.read_gene_correlation.toFixed(3)}</strong>. The generator constructs this relationship; it is not a finding about real tissue.`,mitochondrial:`Mean generated mitochondrial fraction increases from <strong>${stats.batches[0].mean_mito.toFixed(2)}%</strong> in Batch 01 to <strong>${stats.batches[2].mean_mito.toFixed(2)}%</strong> in Batch 03. This shift was specified by the generator.`,activity:`The designed T-like group has a mean generated immune-module score of <strong>${stats.mean_immune_by_group[0].toFixed(2)}</strong> on the 0–5 scale. The module pattern was built into the simulation.`};
      const depth=stats.batches.map((b,i)=>`<tr><td>${batches[i]}</td><td>${number(b.n)}</td><td>${number(b.mean_reads)}</td><td>${b.mean_mito.toFixed(2)}%</td></tr>`).join('');
      chat.innerHTML=`<header class="wd-chat-head"><h2>${item.title}</h2><span class="wd-badge">Simulation</span></header><div class="wd-thread"><div class="wd-question">${item.question}</div><div class="wd-answer"><p class="wd-caption">COMPUTED FROM GENERATED RECORDS</p><h3>${number(stats.n)} cells. Three batches. One view.</h3><p>${descriptions[item.view]}</p><div class="wd-stat-row"><div><strong>${number(stats.n)}</strong><span>generated cells</span></div><div><strong>03</strong><span>batches</span></div><div><strong>06</strong><span>groups</span></div></div><h4>Batch overview</h4><table class="wd-table"><thead><tr><th>Batch</th><th>Cells</th><th>Mean reads</th><th>Mean mito</th></tr></thead><tbody>${depth}</tbody></table><p class="wd-note">Batch mixtures, depth and mitochondrial shifts were designed into the simulation. These summaries describe generated values; they do not establish biological effects.</p><details class="wd-method"><summary>How this view was created</summary><p>Seed ${currentProject.seed}. Generated latent coordinates, not a fitted UMAP. No significance test, trial, model response or scientific approval was produced.</p></details></div></div><footer class="wd-chat-footer"><div class="wd-file">◇ ${currentProject.title} · ${number(stats.n)} generated cells</div><p>Scripted conversation prompts · local simulation only</p></footer>`;
      side.querySelectorAll('[data-chat]').forEach((button,i)=>button.setAttribute('aria-current',String(i===selected)));
    }
    function draw(){
      if(Object.hasOwn(summaryViews,currentView)){controller?.destroy();controller=root.WorkbenchSummaryPlots.mount(canvas,{data,view:currentView,batch:currentBatch});head.querySelector('[data-point-count]').textContent=number(controller.getPerformance().population_n)+' generated records summarized';head.querySelector('[data-figure]').value=currentView;return;}
      const spec=figure(data,currentView,currentBatch);
      controller?.destroy();
      controller=root.ScientificFigure.mountSimulation(canvas,{...spec,context:{label:currentProject.title+' · synthetic demonstration'}});
      head.querySelector('[data-point-count]').textContent=number(spec.record.analysis_n)+' generated cells';
      head.querySelector('[data-figure]').value=currentView;
    }
    function exit(){
      if(!active||preparing)return;controller?.destroy();controller=null;data=null;stats=null;
      nodes.splice(0).forEach(node=>node.remove());hidden.forEach((value,node)=>{node.hidden=value;});hidden.clear();
      app.classList.remove('workbench-demo-active');delete app.dataset.simulation;byId('connect').disabled=false;active=false;trigger.focus();
    }
    async function enter(){
      if(active||attempted||login.hidden||!app.hidden||byId('connect').disabled||byId('token').value.trim()||canvas.__scientificFigure){
        if(!active)byId('login-error').textContent='Open the simulation from a fresh, disconnected workbench page.';return false;
      }
      active=true;preparing=true;byId('connect').disabled=true;
      setHidden(login,true);setHidden(byId('session-heading'),true);setHidden(app,false);app.classList.add('workbench-demo-active');app.dataset.simulation='synthetic';
      const rail=byId('workspace-rail'),inspector=byId('inspector'),workspace=byId('workspace-canvas'),results=byId('results'),resultView=byId('result-view'),card=resultView.querySelector('.results-card'),stage=byId('figure-stage');
      hideChildren(app,[rail,inspector,workspace]);hideChildren(rail);hideChildren(inspector);hideChildren(workspace,[results]);hideChildren(results,[resultView]);hideChildren(resultView,[card]);hideChildren(card,[stage]);hideChildren(stage,[canvas]);
      for(const node of [rail,inspector,workspace,results,resultView,card,stage,canvas])setHidden(node,false);
      side=add(rail,'wd-sidebar',`<div class="brand"><h1>RNA Workbench</h1></div><div class="wd-project"><label for="simulation-project">Project</label><select id="simulation-project" data-project aria-label="Simulation project">${projects.map(p=>`<option value="${p.id}">${p.title}</option>`).join('')}</select><span class="wd-badge">Synthetic demonstration</span></div><div class="wd-navigation">▤ &nbsp; Project data <span data-project-count></span></div><p class="wd-caption">CONVERSATIONS</p><div class="wd-chats"></div><footer><p>Six fictional scenarios.<br>Generated locally · no live trial.</p><button type="button" class="secondary" data-exit>Exit simulation</button></footer>`);
      chat=add(inspector,'wd-chat','<div class="wd-loading">Preparing generated cells…</div>');
      head=add(workspace,'wd-workspace-head',`<header><div><p class="wd-caption">FIGURES</p><h2 data-project-title></h2><p data-point-count>Preparing generated cells…</p></div><span class="wd-badge">Synthetic</span></header><div class="wd-tabs"><strong>Figures</strong><span>Local simulation · no analysis record</span></div><div class="wd-controls"><label>Figure<select data-figure>${Object.entries(views).map(([value,label])=>`<option value="${value}">${label}</option>`).join('')}</select></label><label>Batch<select data-batch></select></label></div><p class="wd-provenance">Scatter views draw selected cells; summary views aggregate all selected records. Coordinates and measurements are generated.</p>`);
      workspace.prepend(head);
      side.querySelector('[data-exit]').onclick=exit;
      side.querySelector('[data-project]').onchange=event=>{if(preparing)return;loadProject(event.target.value);};
      head.querySelector('[data-figure]').onchange=event=>{if(preparing)return;currentView=event.target.value;draw();};
      head.querySelector('[data-batch]').onchange=event=>{if(preparing)return;currentBatch=event.target.value;draw();};
      return loadProject(currentProject.id);
    }
    async function loadProject(id){
      const project=projects.find(p=>p.id===id);if(!active||!project)return false;
      preparing=true;controller?.destroy();controller=null;data=null;stats=null;
      currentProject=project;selected=0;currentView=project.defaultView;currentBatch='all';
      const total=project.batchSizes.reduce((a,b)=>a+b,0),controls=[...side.querySelectorAll('button,select'),...head.querySelectorAll('select')];controls.forEach(control=>control.disabled=true);
      chat.innerHTML=`<div class="wd-loading">Preparing ${number(total)} generated cells…</div>`;
      head.querySelector('[data-project-title]').textContent=project.title;
      head.querySelector('[data-point-count]').textContent=number(total)+' generated cells';
      side.querySelector('[data-project-count]').textContent=number(total);
      side.querySelector('[data-project]').value=id;
      head.querySelector('[data-batch]').innerHTML=`<option value="all">All batches · ${number(total)}</option>${batches.map((name,i)=>`<option value="${i}">${name} · ${number(project.batchSizes[i])}</option>`).join('')}`;
      const chats=conversations(project);
      side.querySelector('.wd-chats').innerHTML=chats.map((c,i)=>`<button type="button" data-chat="${i}" class="wd-chat-link"><span>${c.title}</span><small>${project.title}</small></button>`).join('');
      side.querySelectorAll('[data-chat]').forEach(button=>button.onclick=()=>{if(preparing)return;selected=Number(button.dataset.chat);currentView=conversations(currentProject)[selected].view;renderChat();draw();});
      try{
        await new Promise(resolve=>root.setTimeout(resolve,40));
        data=generateProject(id);stats=summarize(data.cells);renderChat();draw();preparing=false;
        controls.forEach(control=>control.disabled=false);side.querySelector('[data-project]').focus();return true;
      }catch(error){preparing=false;controls.forEach(control=>control.disabled=false);exit();byId('login-error').textContent='Simulation unavailable: '+error.message;return false;}
    }
    trigger.addEventListener('click',enter);
    root.WorkbenchDemo.session={enter,exit,isActive:()=>active,getController:()=>controller,getProject:()=>currentProject.id};
  }
  root.WorkbenchDemo={figure,summarize,mount,views,provenance,batchSizes:batchSizes.slice(),populationSize,projects:projects.map(p=>({...p,batchSizes:p.batchSizes.slice()})),generateProject};
  if(typeof document!=='undefined')mount(document);
})(typeof window!=='undefined'?window:globalThis);
