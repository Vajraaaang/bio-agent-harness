/* Explicit, bounded previews of backend-verified scientific artifacts. */
(function(root){
  'use strict';
  const MAX_TEXT_BYTES=256*1024,MAX_IMAGE_BYTES=8*1024*1024,MAX_DOWNLOAD_BYTES=64*1024*1024,MAX_ROWS=200,MAX_COLUMNS=100,MAX_CODE_CHARS=150000;
  const HASH=/^[a-f0-9]{64}$/,RASTER=new Set(['image/png','image/jpeg','image/webp','image/gif']);
  const NOT_SUPPLIED=Object.freeze({not_supplied:true});
  const refuse=message=>{throw new Error(message);};
  const bytes=text=>new TextEncoder().encode(text).byteLength;
  function validateDescriptor(value){
    if(!value||typeof value!=='object'||!HASH.test(value.artifact_id)||!HASH.test(value.sha256)
      ||typeof value.name!=='string'||!value.name.length||value.name.length>240||/[\x00-\x1f]/.test(value.name)
      ||typeof value.content_type!=='string'||value.content_type.length>200||typeof value.artifact_type!=='string'
      ||!Number.isSafeInteger(value.size_bytes)||value.size_bytes<0
      ||value.download_path!=='/api/scientific/artifacts/'+value.artifact_id)refuse('Artifact metadata is incomplete or the verified local download route is unavailable.');
    return {...value};
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
  function mount(container,options){
    if(!container||!options||typeof options.request!=='function'||!Array.isArray(options.artifacts)||options.artifacts.length>200)refuse('Use a bounded artifact catalog and authenticated local request adapter.');
    container.__workbenchArtifactCanvas?.destroy();const doc=container.ownerDocument,mode=['figures','code','evidence'].includes(options.mode)?options.mode:'figures';
    const make=(tag,className,value)=>{const item=doc.createElement(tag);if(className)item.className=className;if(value!==undefined)item.textContent=String(value);return item;};
    const button=(label,action)=>{const item=make('button','secondary',label);item.type='button';item.addEventListener('click',action);return item;};
    const shell=make('section','wa-artifact-canvas'),list=make('div','wa-artifact-list'),viewer=make('div','wa-artifact-viewer'),status=make('p','wa-artifact-status','Choose an artifact to preview or download.');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
    shell.setAttribute('aria-label','Published scientific artifacts');shell.append(list,status,viewer);container.replaceChildren(shell);
    let generation=0,disposed=false,objectURL=null;const downloads=new Set();
    const announce=(message,error=false)=>{status.textContent=message;status.classList.toggle('error',error);options.onStatus?.(message,error);};
    function clear(){if(objectURL){URL.revokeObjectURL(objectURL);objectURL=null;}viewer.replaceChildren();}
    function metadata(artifact){const grid=make('dl','wa-artifact-metadata');for(const [label,value]of [['File',artifact.name],['Size',artifact.size_bytes.toLocaleString()+' bytes'],['SHA-256',artifact.sha256],['Artifact',artifact.artifact_id],['Run',artifact.scientific_run_id||options.context?.run_id||'Recorded scientific output']])grid.append(make('dt','',label),make('dd','',value));return grid;}
    async function fetchArtifact(artifact,limit,stamp){
      if(artifact.size_bytes>limit)refuse('This artifact exceeds the browser budget. Use the existing export or CLI for the complete file.');
      const response=await options.request(artifact.download_path);if(!response||response.ok===false)refuse('The verified artifact could not be retrieved.');
      const data=await readBounded(response,Math.min(limit,artifact.size_bytes),()=>disposed||stamp!==generation);if(disposed||stamp!==generation)return null;await verifyBytes(data,artifact);if(disposed||stamp!==generation)return null;return data;
    }
    function renderTable(table){const scroll=make('div','wa-table-scroll'),element=make('table','wa-artifact-table'),thead=make('thead'),tr=make('tr');for(const name of table.columns)tr.append(make('th','',name));thead.append(tr);element.append(thead);const tbody=make('tbody');for(const row of table.rows){const item=make('tr');for(const value of row)item.append(make('td','',value===NOT_SUPPLIED?'Not supplied':value!==null&&typeof value==='object'?JSON.stringify(value):String(value)));tbody.append(item);}element.append(tbody);scroll.append(element);viewer.append(make('p','wa-table-count',table.rows.length.toLocaleString()+' shown of '+table.totalRows.toLocaleString()+' rows'+(table.truncated?' · first 200 rows; download for the complete table':'')),scroll);}
    async function preview(artifact,kind){const stamp=++generation;clear();viewer.append(metadata(artifact));announce('Loading '+artifact.name+'…');
      try{const data=await fetchArtifact(artifact,kind==='image'?MAX_IMAGE_BYTES:MAX_TEXT_BYTES,stamp);if(!data)return;
        if(kind==='image'){const mime=artifact.content_type.split(';')[0].trim().toLowerCase();if(rasterKind(data)!==mime)refuse('Saved bytes do not match the declared raster image type.');objectURL=URL.createObjectURL(new Blob([data],{type:mime}));const image=make('img','wa-artifact-image');image.alt=artifact.name+' · verified retained artifact';image.addEventListener('error',()=>{if(!disposed&&stamp===generation)announce('The verified image could not be decoded. Download the original artifact.',true);});image.src=objectURL;viewer.append(image);announce('Verified image · '+artifact.name);return;}
        const text=new TextDecoder('utf-8',{fatal:true}).decode(data);
        if(kind==='notebook'){const result=notebookCode(text);viewer.append(make('p','wa-code-count',result.codeCells+' recorded code cells'+(result.truncated?' · preview capped at 150,000 characters':'')),make('pre','wa-artifact-code',result.source||'No code cells were recorded.'));}
        else if(kind==='csv'||kind==='tsv')renderTable(parseTable(text,kind));
        else if(kind==='json'){const record=parseJSON(text);if(Array.isArray(record))renderTable(parseTable(text,'json'));else viewer.append(make('pre','wa-artifact-record',JSON.stringify(record,null,2)));}
        else viewer.append(make('pre','wa-artifact-code',text.length>MAX_CODE_CHARS?text.slice(0,MAX_CODE_CHARS):text));
        announce('Verified preview · '+artifact.name+(text.length>MAX_CODE_CHARS&&['code','text'].includes(kind)?' · first 150,000 characters':''));
      }catch(error){if(!disposed&&stamp===generation)announce(error.message||'Artifact preview is unavailable.',true);}
    }
    async function download(artifact){const stamp=++generation;announce('Preparing '+artifact.name+'…');try{const data=await fetchArtifact(artifact,MAX_DOWNLOAD_BYTES,stamp);if(!data)return;const url=URL.createObjectURL(new Blob([data],{type:artifact.content_type}));downloads.add(url);const anchor=make('a');anchor.href=url;anchor.download=artifact.name.split(/[\\/]/).pop();doc.body.append(anchor);anchor.click();anchor.remove();root.setTimeout(()=>{URL.revokeObjectURL(url);downloads.delete(url);},1000);announce('Verified download prepared · '+artifact.name);}catch(error){if(!disposed&&stamp===generation)announce(error.message||'Download is unavailable.',true);}}
    if(!options.artifacts.length)list.append(make('p','wa-empty','No published artifacts are recorded for this investigation.'));
    for(const candidate of options.artifacts){const row=make('article','wa-artifact-row');list.append(row);let artifact;
      try{artifact=validateDescriptor(candidate);}catch(error){row.append(make('p','error',error.message));continue;}
      const heading=make('div','wa-artifact-heading');heading.append(make('strong','',artifact.name),make('span','muted',artifact.content_type+' · '+artifact.size_bytes.toLocaleString()+' bytes'));row.append(heading);
      const actions=make('div','wa-artifact-actions'),kind=previewKind(artifact,mode),open=button('Preview',()=>preview(artifact,kind));open.dataset.artifactAction='preview';open.dataset.artifactId=artifact.artifact_id;open.disabled=!kind;open.title=kind?'Preview verified saved bytes':'A bounded preview is unavailable for this artifact.';
      const save=button('Download',()=>download(artifact));save.dataset.artifactAction='download';save.dataset.artifactId=artifact.artifact_id;actions.append(open,save);row.append(actions);
      if(!kind)row.append(make('p','wa-preview-unavailable','Preview unavailable · download the recorded file.'));else if(artifact.size_bytes>(kind==='image'?MAX_IMAGE_BYTES:MAX_TEXT_BYTES))row.append(make('p','wa-preview-unavailable','Preview exceeds the '+(kind==='image'?'8 MiB image':'256 KiB text')+' budget. Download the recorded file.'));
      if(artifact.size_bytes>MAX_DOWNLOAD_BYTES)row.append(make('p','wa-preview-unavailable','For files above 64 MiB, use the existing export or CLI.'));
    }
    const controller={destroy(){disposed=true;generation++;clear();for(const url of downloads)URL.revokeObjectURL(url);downloads.clear();container.replaceChildren();delete container.__workbenchArtifactCanvas;}};
    container.__workbenchArtifactCanvas=controller;return controller;
  }
  const api={mount,parseTable,parseJSON,notebookCode,validateDescriptor,readBounded,verifyBytes,rasterKind,previewKind,limits:{text_bytes:MAX_TEXT_BYTES,image_bytes:MAX_IMAGE_BYTES,download_bytes:MAX_DOWNLOAD_BYTES,visible_rows:MAX_ROWS,code_characters:MAX_CODE_CHARS}};
  root.WorkbenchArtifactCanvas=api;if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
