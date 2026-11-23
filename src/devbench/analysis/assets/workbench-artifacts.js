/* Explicit, bounded previews of backend-verified scientific artifacts. */
(function(root){
  'use strict';
  const MAX_TEXT_BYTES=256*1024,MAX_IMAGE_BYTES=8*1024*1024,MAX_DOWNLOAD_BYTES=64*1024*1024,MAX_ROWS=200,MAX_COLUMNS=100,MAX_CODE_CHARS=150000;
  const MAX_SCIENTIFIC_RESULT_BYTES=96000000;
  const HASH=/^[a-f0-9]{64}$/,RASTER=new Set(['image/png','image/jpeg','image/webp','image/gif']);
  const NOT_SUPPLIED=Object.freeze({not_supplied:true});
  const refuse=message=>{throw new Error(message);};
  const bytes=text=>new TextEncoder().encode(text).byteLength;
  const downloadLimit=artifact=>!artifact.episode_output&&artifact.content_type==='application/json'&&artifact.scientific_ref?.object_type==='result'?MAX_SCIENTIFIC_RESULT_BYTES:MAX_DOWNLOAD_BYTES;
  function hasVerifiedSavedPlots(artifact){
    const availability=artifact.plot_availability;
    return Boolean(availability&&availability.available===true&&Array.isArray(availability.capabilities)
      &&availability.capabilities.length>0&&availability.capabilities.length<=200
      &&availability.capabilities.every(id=>typeof id==='string'&&id.length>0&&id.length<=160)
      &&availability.source?.artifact_id===artifact.artifact_id&&availability.source?.sha256===artifact.sha256);
  }
  function validateDescriptor(value){
    if(!value||typeof value!=='object'||!HASH.test(value.artifact_id)||!HASH.test(value.sha256)
      ||typeof value.name!=='string'||!value.name.length||value.name.length>240||/[\x00-\x1f]/.test(value.name)
      ||typeof value.content_type!=='string'||value.content_type.length>200||typeof value.artifact_type!=='string'
      ||!Number.isSafeInteger(value.size_bytes)||value.size_bytes<0
      ||value.download_path!=='/api/scientific/artifacts/'+value.artifact_id)refuse('Artifact metadata is incomplete or the verified local download route is unavailable.');
    return {...value};
  }
  function episodeDescriptor(value,jobId){
    if(!value||value.kind!=='episode_output'||!/^[a-f0-9]{32}$/.test(jobId)||value.job_id!==jobId
      ||!['summary','synthesis','result'].includes(value.name)
      ||value.download_url!=='/api/agentic-episodes/'+jobId+'/download/'+value.name)refuse('Episode artifact does not belong to the selected analysis.');
    const bound=HASH.test(value.sha256)&&Number.isSafeInteger(value.size_bytes)&&value.size_bytes>=0;
    if((value.sha256!==undefined||value.size_bytes!==undefined)&&!bound)refuse('Episode artifact digest and byte count must both be valid.');
    return {...value,artifact_id:jobId+'/'+value.name,artifact_type:'episode_output',content_type:value.name==='summary'?'text/markdown':'application/json',download_path:value.download_url,
      name:value.name+(value.name==='summary'?'.md':'.json'),size_bytes:bound?value.size_bytes:null,sha256:bound?value.sha256:null,episode_output:true};
  }
  function parseJSON(text){
    if(typeof text!=='string'||bytes(text)>MAX_TEXT_BYTES)refuse('JSON preview exceeds the 256 KiB text budget.');
    let at=0,items=0;
    const ws=()=>{while(/[\x20\t\r\n]/.test(text[at]||'!'))at++;};
    function string(){const start=at++;let escaped=false;while(at<text.length){const char=text[at++];if(escaped){escaped=false;continue;}if(char==='\\'){escaped=true;continue;}if(char==='"')return JSON.parse(text.slice(start,at));}refuse('Unterminated JSON string.');}
    function value(depth){if(depth>32||++items>100000)refuse('JSON structure exceeds the bounded preview budget.');ws();const char=text[at];
      if(char==='{'){at++;ws();const keys=new Set();if(text[at]==='}'){at++;return;}while(at<text.length){ws();if(text[at]!=='"')refuse('Invalid JSON object key.');const key=string();if(keys.has(key))refuse('Duplicate JSON keys cannot be previewed without changing data.');keys.add(key);ws();if(text[at++]!==':')refuse('Invalid JSON object separator.');value(depth+1);ws();const end=text[at++];if(end==='}')return;if(end!==',')refuse('Invalid JSON object.');}refuse('Unterminated JSON object.');}
      if(char==='['){at++;ws();if(text[at]===']'){at++;return;}while(at<text.length){value(depth+1);ws();const end=text[at++];if(end===']')return;if(end!==',')refuse('Invalid JSON array.');}refuse('Unterminated JSON array.');}
      if(char==='"'){string();return;}
      for(const literal of ['true','false','null'])if(text.startsWith(literal,at)){at+=literal.length;return;}
      const token=/^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/.exec(text.slice(at));
      if(!token)refuse('Invalid finite JSON value.');const number=Number(token[0]);if(!Number.isFinite(number))refuse('Nonfinite JSON numbers cannot be previewed.');if(Number.isInteger(number)&&!Number.isSafeInteger(number))refuse('JSON integer exceeds exact browser precision. Download the original data.');at+=token[0].length;
    }
    value(0);ws();if(at!==text.length)refuse('Unexpected content after JSON value.');return JSON.parse(text);
  }
  function delimited(text,delimiter){
    const rows=[];let row=[],field='',quoted=false,closed=false,started=false;
    const finishField=()=>{row.push(field);if(row.length>MAX_COLUMNS)refuse('Table has more than 100 columns; download the original file.');field='';closed=false;started=false;};
    const finishRow=()=>{finishField();rows.push(row);row=[];};
    for(let i=0;i<text.length;i++){const char=text[i];
      if(quoted){if(char==='"'){if(text[i+1]==='"'){field+='"';i++;}else{quoted=false;closed=true;}}else field+=char;continue;}
      if(char===delimiter){finishField();continue;}
      if(char==='\r'||char==='\n'){finishRow();if(char==='\r'&&text[i+1]==='\n')i++;continue;}
      if(closed)refuse('Unexpected text after a quoted table field.');
      if(char==='"'){if(started||field.length)refuse('A quoted table field must start with a quote.');quoted=true;started=true;}else{field+=char;started=true;}
    }
    if(quoted)refuse('Unterminated quoted table field.');if(row.length||field.length||started||closed)finishRow();return rows;
  }
  function parseTable(text,format='csv'){
    if(typeof text!=='string'||bytes(text)>MAX_TEXT_BYTES)refuse('Table preview exceeds the 256 KiB text budget.');
    if(text.charCodeAt(0)===0xfeff)text=text.slice(1);
    let columns=[],rows=[];
    if(format==='json'){
      const data=parseJSON(text);if(!Array.isArray(data))refuse('This JSON record is not an array of table rows.');
      if(data.length){if(data.every(Array.isArray)){const width=data[0].length;if(width>MAX_COLUMNS||data.some(row=>row.length!==width))refuse('JSON table rows have inconsistent or unsupported widths.');columns=Array.from({length:width},(_,i)=>'Column '+(i+1));rows=data;}
        else if(data.every(row=>row&&typeof row==='object'&&!Array.isArray(row))){const names=new Set();for(const row of data)for(const key of Object.keys(row))names.add(key);columns=Array.from(names);if(columns.length>MAX_COLUMNS)refuse('Table has more than 100 columns.');rows=data.map(row=>columns.map(key=>Object.prototype.hasOwnProperty.call(row,key)?row[key]:NOT_SUPPLIED));}
        else refuse('JSON table must contain consistently shaped arrays or objects.');}
    }else if(format==='csv'||format==='tsv'){const data=delimited(text,format==='tsv'?'\t':',');if(data.length){columns=data[0];rows=data.slice(1);if(rows.some(row=>row.length!==columns.length))refuse('Table rows have inconsistent field counts. Download the original file to inspect them.');}}
    else refuse('This table format is not supported.');
    return {columns,rows:rows.slice(0,MAX_ROWS),totalRows:rows.length,totalColumns:columns.length,truncated:rows.length>MAX_ROWS};
  }
  function notebookCode(text){const notebook=parseJSON(text);if(!notebook||!Array.isArray(notebook.cells))refuse('Notebook cells are unavailable.');
    const cells=notebook.cells.filter(cell=>cell&&cell.cell_type==='code');let source='',truncated=false;
    for(let i=0;i<cells.length;i++){const cell=cells[i],body=Array.isArray(cell.source)?cell.source.every(part=>typeof part==='string')?cell.source.join(''):refuse('Notebook source contains unsupported values.'):typeof cell.source==='string'?cell.source:refuse('Notebook code source is missing.');
      const block='# Cell '+(i+1)+' · execution '+(cell.execution_count==null?'not recorded':String(cell.execution_count))+'\n'+body+'\n\n';
      if(source.length+block.length>MAX_CODE_CHARS){source+=block.slice(0,Math.max(0,MAX_CODE_CHARS-source.length));truncated=true;break;}source+=block;
    }
    return {source,codeCells:cells.length,truncated};
  }
  async function readBounded(response,limit,cancelled=()=>false){
    if(!Number.isSafeInteger(limit)||limit<0)refuse('A finite artifact read budget is required.');
    const declared=response.headers?.get('Content-Length');
    if(declared!==null&&declared!==undefined&&(!/^\d+$/.test(declared)||Number(declared)>limit)){await response.body?.cancel?.();refuse('Artifact exceeds the bounded read budget.');}
    if(!response.body?.getReader)refuse('This browser cannot stream a bounded preview. Use the existing export interface.');
    const reader=response.body.getReader(),chunks=[];let total=0;
    try{while(true){if(cancelled()){await reader.cancel();refuse('Artifact preview was replaced.');}const part=await reader.read();if(part.done)break;if(!(part.value instanceof Uint8Array))refuse('Artifact stream did not return bytes.');if(total+part.value.byteLength>limit){await reader.cancel();refuse('Artifact exceeds the bounded read budget.');}chunks.push(part.value);total+=part.value.byteLength;}}
    catch(error){try{await reader.cancel();}catch(_){}throw error;}finally{reader.releaseLock?.();}
    const result=new Uint8Array(total);let offset=0;for(const chunk of chunks){result.set(chunk,offset);offset+=chunk.byteLength;}return result;
  }
  async function verifyBytes(data,descriptor){
    if(data.byteLength!==descriptor.size_bytes)refuse('Artifact byte count differs from its verified descriptor.');
    if(!root.crypto?.subtle)refuse('SHA-256 verification is unavailable in this browser. Use the verified export interface.');
    const actual=Array.from(new Uint8Array(await root.crypto.subtle.digest('SHA-256',data)),byte=>byte.toString(16).padStart(2,'0')).join('');
    if(actual!==descriptor.sha256)refuse('Artifact SHA-256 does not match its verified descriptor.');return data;
  }
  function rasterKind(data){
    const starts=signature=>signature.every((byte,index)=>data[index]===byte);
    if(starts([137,80,78,71,13,10,26,10]))return 'image/png';
    if(starts([255,216,255]))return 'image/jpeg';
    if(starts([71,73,70,56,55,97])||starts([71,73,70,56,57,97]))return 'image/gif';
    if(starts([82,73,70,70])&&data[8]===87&&data[9]===69&&data[10]===66&&data[11]===80)return 'image/webp';return null;
  }
  function previewKind(artifact,mode){
    const name=artifact.name.toLowerCase(),mime=artifact.content_type.split(';')[0].trim().toLowerCase();
    if(/\.(h5ad|h5|hdf5|loom|mtx|bam|fastq|fq|npz|npy)(\.gz)?$/.test(name)||/^(anndata|matrix|expression_matrix)$/.test(artifact.artifact_type))return null;
    if(name.endsWith('.ipynb'))return 'notebook';
    if(/\.(py|r|sh|js|ts)$/.test(name))return 'code';
    if(mode==='code')return null;
    if(RASTER.has(mime))return 'image';
    if(name.endsWith('.csv')||mime==='text/csv')return 'csv';if(name.endsWith('.tsv')||mime==='text/tab-separated-values')return 'tsv';
    if(name.endsWith('.json')||mime==='application/json')return 'json';
    if(mime==='text/plain'||/\.(txt|md|log)$/.test(name))return 'text';return null;
  }
  function rasterGallery(doc,{url,alt,status,sha256,onStatus}){
    const figure=doc.createElement('figure'),mat=doc.createElement('div');
    figure.className='wa-figure-gallery';
    mat.className='wa-figure-mat';
    const image=doc.createElement('img');
    image.className='wa-artifact-image';
    image.alt=alt;
    const footer=doc.createElement('figcaption'),caption=doc.createElement('span');
    footer.className='wa-figure-footer';
    caption.textContent=status;
    caption.title=status;
    image.addEventListener('error',()=>{
      const message='The saved image could not be decoded. Download the original artifact.';
      caption.textContent=message;
      caption.title=message;
      onStatus?.(message,true);
    });
    image.src=url;
    mat.append(image);
    footer.append(caption);
    if(typeof sha256==='string'&&HASH.test(sha256)){
      const digest=doc.createElement('button');
      digest.type='button';
      digest.className='secondary wa-figure-digest';
      digest.textContent='SHA-256 '+sha256.slice(0,12)+'…';
      digest.title=sha256;
      digest.setAttribute('aria-label','Copy figure SHA-256');
      digest.addEventListener('click',async()=>{
        try{await root.navigator.clipboard.writeText(sha256);onStatus?.('SHA-256 copied.');}
        catch(_){onStatus?.('Clipboard unavailable. SHA-256: '+sha256,true);}
      });
      footer.append(digest);
    }
    figure.append(mat,footer);
    return figure;
  }
  function mount(container,options){
    if(!container||!options||typeof options.request!=='function'||!Array.isArray(options.artifacts)||options.artifacts.length>200)refuse('Use a bounded artifact catalog and authenticated local request adapter.');
    container.__workbenchArtifactCanvas?.destroy();const doc=container.ownerDocument,mode=['figures','code','evidence'].includes(options.mode)?options.mode:'figures';
    const make=(tag,className,value)=>{const item=doc.createElement(tag);if(className)item.className=className;if(value!==undefined)item.textContent=String(value);return item;};
    const button=(label,action)=>{const item=make('button','secondary',label);item.type='button';item.addEventListener('click',action);return item;};
    const shell=make('section','wa-artifact-canvas'),list=make('div','wa-artifact-list'),viewer=make('div','wa-artifact-viewer'),status=make('p','wa-artifact-status','Choose an artifact to preview or download.');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
    shell.setAttribute('aria-label','Published scientific artifacts');shell.append(list,status,viewer);container.replaceChildren(shell);
    let generation=0,disposed=false,objectURL=null,bulkController=null;const downloads=new Set();
    const announce=(message,error=false)=>{status.textContent=message;status.classList.toggle('error',error);options.onStatus?.(message,error);};
    function clear(){if(bulkController){bulkController.destroy();bulkController=null;}if(objectURL){URL.revokeObjectURL(objectURL);objectURL=null;}viewer.replaceChildren();}
    function metadata(artifact){const grid=make('dl','wa-artifact-metadata');for(const [label,value]of [['File',artifact.name],['Size',artifact.size_bytes===null?'Not supplied':artifact.size_bytes.toLocaleString()+' bytes'],['SHA-256',artifact.sha256||'Not supplied in descriptor'],['Artifact',artifact.artifact_id],['Run',artifact.job_id||artifact.scientific_run_id||options.context?.run_id||'Recorded scientific output']])grid.append(make('dt','',label),make('dd','',value));return grid;}
    async function fetchArtifact(artifact,limit,stamp){
      if(artifact.download_status==='restricted_audit_entitlement_required')refuse('Raw bytes require restricted audit entitlement.');
      if(artifact.size_bytes>limit)refuse('This artifact exceeds the browser budget. Use the existing export or CLI for the complete file.');
      const response=await options.request(artifact.download_path);if(!response||response.ok===false)refuse('The verified artifact could not be retrieved.');
      const data=await readBounded(response,artifact.size_bytes===null?limit:Math.min(limit,artifact.size_bytes),()=>disposed||stamp!==generation);if(disposed||stamp!==generation)return null;if(!artifact.episode_output||artifact.sha256)await verifyBytes(data,artifact);if(disposed||stamp!==generation)return null;return data;
    }
    function renderTable(table){const scroll=make('div','wa-table-scroll'),element=make('table','wa-artifact-table'),thead=make('thead'),tr=make('tr');for(const name of table.columns)tr.append(make('th','',name));thead.append(tr);element.append(thead);const tbody=make('tbody');for(const row of table.rows){const item=make('tr');for(const value of row)item.append(make('td','',value===NOT_SUPPLIED?'Not supplied':value!==null&&typeof value==='object'?JSON.stringify(value):String(value)));tbody.append(item);}element.append(tbody);scroll.append(element);viewer.append(make('p','wa-table-count',table.rows.length.toLocaleString()+' shown of '+table.totalRows.toLocaleString()+' rows'+(table.truncated?' · first 200 rows; download for the complete table':'')),scroll);}
    function recordView(value,depth=0,budget={rows:120}){
      if(value===null)return make('span','wa-record-null','Recorded null');if(typeof value!=='object')return make('span','',String(value));
      if(depth>=5||budget.rows<=0)return make('p','muted','Additional evidence is available in the complete download.');
      const grid=make('dl','wc-kv-grid');for(const [key,item]of Object.entries(value).slice(0,30)){if(budget.rows--<=0)break;const title=Array.isArray(value)?'Item '+(Number(key)+1):key.replaceAll('_',' ').replace(/^./,char=>char.toUpperCase()),definition=make('dd');definition.append(recordView(item,depth+1,budget));grid.append(make('dt','',title),definition);}return grid;
    }
    async function preview(artifact,kind){const stamp=++generation;clear();if(kind!=='image')viewer.append(metadata(artifact));announce('Loading '+artifact.name+'…');
      try{const data=await fetchArtifact(artifact,kind==='image'?MAX_IMAGE_BYTES:MAX_TEXT_BYTES,stamp);if(!data)return;
        if(kind==='image'){
          const mime=artifact.content_type.split(';')[0].trim().toLowerCase();
          if(rasterKind(data)!==mime)refuse('Saved bytes do not match the declared raster image type.');
          objectURL=URL.createObjectURL(new Blob([data],{type:mime}));
          const gallery=rasterGallery(doc,{
            url:objectURL,alt:artifact.name+' · verified retained artifact',
            status:'Verified image · '+artifact.name,sha256:artifact.sha256,
            onStatus:(message,error)=>{if(!disposed&&stamp===generation)announce(message,error);},
          });
          const details=make('details','wa-figure-metadata');
          details.append(make('summary','','Figure details'),metadata(artifact));
          viewer.append(gallery,details);
          announce('');
          return;
        }
        const text=new TextDecoder('utf-8',{fatal:true}).decode(data);
        if(kind==='notebook'){const result=notebookCode(text);viewer.append(make('p','wa-code-count',result.codeCells+' recorded code cells'+(result.truncated?' · preview capped at 150,000 characters':'')),make('pre','wa-artifact-code',result.source||'No code cells were recorded.'));}
        else if(kind==='csv'||kind==='tsv')renderTable(parseTable(text,kind));
        else if(kind==='json'){const record=parseJSON(text);if(record?.format==='devbench.bulk-plot-workspace/1'&&root.BulkScientificFigure){const host=make('div');viewer.append(host);bulkController=root.BulkScientificFigure.mount(host,{workspace:record});}else if(Array.isArray(record))renderTable(parseTable(text,'json'));else if(artifact.episode_output)viewer.append(recordView(record));else viewer.append(make('pre','wa-artifact-record',JSON.stringify(record,null,2)));}
        else viewer.append(make('pre','wa-artifact-code',text.length>MAX_CODE_CHARS?text.slice(0,MAX_CODE_CHARS):text));
        announce((artifact.episode_output&&!artifact.sha256?'Retained preview · ':'Verified preview · ')+artifact.name+(text.length>MAX_CODE_CHARS&&['code','text'].includes(kind)?' · first 150,000 characters':''));
      }catch(error){if(!disposed&&stamp===generation)announce(error.message||'Artifact preview is unavailable.',true);}
    }
    async function bulkPlots(artifact,mode='auto'){const stamp=++generation;clear();announce('Preparing saved plot evidence…');
      try{if(!root.BulkScientificFigure)refuse('Saved plot rendering is unavailable in this build.');
        const base=artifact.episode_output?'/api/agentic-episodes/'+artifact.job_id:artifact.download_path;
        const response=await options.request(base+(artifact.episode_output||mode==='scientific'?'/scientific-plots':'/bulk-plots'));if(!response||response.ok===false)refuse('This artifact has no supported saved plot evidence.');
        const data=await readBounded(response,MAX_DOWNLOAD_BYTES,()=>disposed||stamp!==generation);if(disposed||stamp!==generation)return;
        const workspace=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(data));
        if(artifact.episode_output?workspace?.source?.run_id!==artifact.job_id||artifact.sha256&&workspace?.source?.sha256!==artifact.sha256:workspace?.source?.sha256!==artifact.sha256||workspace?.source?.artifact_id!==artifact.artifact_id)refuse('Plot evidence differs from the selected verified artifact.');
        const host=make('div');viewer.append(host);bulkController=root.BulkScientificFigure.mount(host,{toolbarHost:options.toolbarHost,...(workspace.format==='devbench.scientific-plot-workspace/1'?{scientificWorkspace:workspace}:{workspace}),...(!artifact.episode_output?{loadCapabilities:async()=>{const response=await options.request(artifact.download_path+'/plot-capabilities');return response.json();},exportBundle:async request=>{const response=await options.request(artifact.download_path+'/scientific-bundle',request);if(!response.ok)refuse('Presentation bundle could not be created.');return {blob:await response.blob(),root_hash:response.headers.get('X-Plot-Revision-Root')};}}:{}),exportPDF:async selection=>{
          const view=selection.view,request=workspace.format==='devbench.scientific-plot-workspace/1'?selection:{kind:selection.kind,gene_id:selection.gene_id,selected_id:view.selected_id,scope:selection.scope,bounds:view.bounds,filters:view.filters,reference_lines:view.display_reference_lines};
          const result=await options.request(base+(artifact.episode_output||mode==='scientific'?'/scientific-pdf':'/bulk-pdf'),request);if(!result||result.ok===false)refuse('The PDF presentation could not be rendered.');
          const bytes=await readBounded(result,MAX_DOWNLOAD_BYTES,()=>disposed||stamp!==generation);return new Blob([bytes],{type:'application/pdf'});
        }});announce(workspace.panels?'Saved scientific plots · '+workspace.panels.length+' panels · no model refitting':'Saved bulk plots · '+workspace.genes.length.toLocaleString()+' genes · no model refitting');
      }catch(error){if(!disposed&&stamp===generation)announce(error.message||'Saved plot evidence is unavailable.',true);}
    }
    async function download(artifact){const stamp=generation;announce('Preparing '+artifact.name+'…');try{const data=await fetchArtifact(artifact,downloadLimit(artifact),stamp);if(!data)return;const url=URL.createObjectURL(new Blob([data],{type:artifact.content_type}));downloads.add(url);const anchor=make('a');anchor.href=url;anchor.download=artifact.name.split(/[\\/]/).pop();doc.body.append(anchor);anchor.click();anchor.remove();root.setTimeout(()=>{URL.revokeObjectURL(url);downloads.delete(url);},1000);announce((artifact.episode_output&&!artifact.sha256?'Retained download prepared · ':'Verified download prepared · ')+artifact.name);}catch(error){if(!disposed&&stamp===generation)announce(error.message||'Download is unavailable.',true);}}
    if(!options.artifacts.length)list.append(make('p','wa-empty','No published artifacts are recorded for this investigation.'));
    for(const candidate of options.artifacts){
      if(options.plotsOnly && (candidate.content_type==='application/json'||candidate.kind==='episode_output'||/\.json$/i.test(candidate.name||''))){
        if(hasVerifiedSavedPlots(candidate)||candidate.kind==='episode_output'&&candidate.name==='result'){
          list.append(button('Explore scientific plots',()=>{
            try{
              const descriptor=candidate.kind==='episode_output'?episodeDescriptor(candidate,options.context?.job_id):validateDescriptor(candidate);
              void bulkPlots(descriptor,'scientific');
            }catch(error){announce(error.message,true);}
          }));
        }
        continue;
      }
      const row=make('article','wa-artifact-row is-fixed');list.append(row);let artifact;
      try{artifact=candidate&&candidate.kind==='episode_output'?episodeDescriptor(candidate,options.context?.job_id):validateDescriptor(candidate);}catch(error){row.append(make('p','error',error.message));continue;}
      const heading=make('div','wa-artifact-heading wa-artifact-top');heading.append(make('strong','',artifact.name),make('span','muted',artifact.content_type+(artifact.size_bytes===null?' · retained episode output':' · '+artifact.size_bytes.toLocaleString()+' bytes')));row.append(heading);
      const actions=make('div','wa-artifact-actions'),kind=previewKind(artifact,mode),open=button('Preview',()=>preview(artifact,kind));open.dataset.artifactAction='preview';open.dataset.artifactId=artifact.artifact_id;open.disabled=!kind||artifact.size_bytes>(kind==='image'?MAX_IMAGE_BYTES:MAX_TEXT_BYTES);open.title=open.disabled?'A bounded preview is unavailable for this artifact.':'Preview verified saved bytes';
      const save=button('Download',()=>download(artifact));save.dataset.artifactAction='download';save.dataset.artifactId=artifact.artifact_id;actions.append(open);
      const restricted=artifact.download_status==='restricted_audit_entitlement_required';
      if(restricted){open.disabled=true;save.disabled=true;open.title=save.title='Raw bytes require restricted audit entitlement.';}
      if(mode==='evidence'&&artifact.sha256){
        row.append(metadata(artifact));
        if(kind==='json')open.textContent='Preview JSON';
        const copy=button('Copy SHA-256',async()=>{
          try{await root.navigator.clipboard.writeText(artifact.sha256);announce('SHA-256 copied.');}
          catch(_){announce('Clipboard unavailable. Select the displayed digest to copy it.',true);}
        });
        actions.append(copy);
      }
      actions.append(save);row.append(actions);
      if(mode==='figures'&&!artifact.episode_output&&hasVerifiedSavedPlots(artifact)){const explore=button('Explore scientific plots',()=>bulkPlots(artifact,'scientific'));explore.dataset.artifactAction='scientific-plots';explore.dataset.artifactId=artifact.artifact_id;actions.append(explore);}
      if(artifact.episode_output&&candidate.name==='result'&&mode==='figures'){const explore=button('Explore scientific plots',()=>bulkPlots(artifact,'scientific'));explore.dataset.artifactAction='scientific-plots';explore.dataset.artifactId=artifact.artifact_id;actions.append(explore);}
      if(restricted)row.append(make('p','wa-preview-unavailable','Raw bytes require restricted audit entitlement.'));
      else if(!kind)row.append(make('p','wa-preview-unavailable','Preview unavailable · download the recorded file.'));else if(artifact.size_bytes>(kind==='image'?MAX_IMAGE_BYTES:MAX_TEXT_BYTES))row.append(make('p','wa-preview-unavailable','Preview exceeds the '+(kind==='image'?'8 MiB image':'256 KiB text')+' budget. Download the recorded file.'));
      if(!restricted&&artifact.size_bytes>downloadLimit(artifact))row.append(make('p','wa-preview-unavailable','For files above '+(downloadLimit(artifact)===MAX_SCIENTIFIC_RESULT_BYTES?'96,000,000 bytes':'64 MiB')+', use the existing export or CLI.'));
    }
    if(options.plotsOnly&&!list.children.length){list.hidden=true;status.hidden=true;}
    if(options.autoMountPlots&&mode==='figures'){const primary=options.artifacts.find(a=>!a.episode_output&&hasVerifiedSavedPlots(a)&&(a.name||'').includes('contrast'))||options.artifacts.find(a=>!a.episode_output&&hasVerifiedSavedPlots(a));if(primary)bulkPlots(primary,'scientific');}
    const controller={destroy(){disposed=true;generation++;clear();for(const url of downloads)URL.revokeObjectURL(url);downloads.clear();container.replaceChildren();delete container.__workbenchArtifactCanvas;}};
    container.__workbenchArtifactCanvas=controller;return controller;
  }
  const api={mount,rasterGallery,parseTable,parseJSON,notebookCode,validateDescriptor,readBounded,verifyBytes,rasterKind,previewKind,limits:{text_bytes:MAX_TEXT_BYTES,image_bytes:MAX_IMAGE_BYTES,download_bytes:MAX_DOWNLOAD_BYTES,visible_rows:MAX_ROWS,code_characters:MAX_CODE_CHARS}};
  root.WorkbenchArtifactCanvas=api;if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
