'use strict';
const $ = id => document.getElementById(id);
const modeNames = {auto:'Auto', force_standby:'Force standby', force_awake:'Force awake'};
let data = null, loading = false, saving = false;
const cards = new Map();
function element(tag, text, className) {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (className) e.className = className;
  return e;
}
function message(text, error=false) { $('message').textContent=text; $('message').className=error?'error':''; }
async function request(path, body) {
  const response=await fetch(path, {method:body?'POST':'GET',cache:'no-store',
    headers:body?{'Content-Type':'application/json'}:{}, body:body?JSON.stringify(body):undefined,
    signal:AbortSignal.timeout(8000)});
  const result=await response.json();
  if (!response.ok) throw new Error(result.detail || result.error || 'Request failed');
  return result;
}
function choices(container, selected, excluded) {
  container.replaceChildren();
  for (const r of data.renderers.filter(r=>!r.dependent && r.id!==excluded)) {
    const label=element('label',undefined,'choice'), input=element('input');
    input.type='checkbox'; input.value=r.id; input.checked=selected.includes(r.id);
    const text=element('span',r.name);text.append(element('small',r.id+' · '+(r.present===null?'Unknown':r.present?'Present':'Offline')));
    label.append(input,text);container.append(label);
  }
  if (!container.childElementCount) container.append(element('p','No normal speakers discovered yet. Power one on, then reopen this form.','muted'));
}
function selected(container) { return Array.from(container.querySelectorAll('input:checked')).map(e=>e.value); }
function statusText(status, present) {
  // A node can be a healthy Snapcast renderer without implementing the separate
  // Pi subwoofer status-reporting protocol (e.g. Rio before firmware upgrade).
  if (!status.fresh) {
    if (present === true) return 'Connected — no control report';
    if (present === false) return 'Offline — no control report';
    return 'Unknown — presence unavailable';
  }
  if (status.state==='audio_active' || status.state==='audio_muted') {
    const connection=present===true?'Connected — ':present===false?'Offline — last reported ':'';
    return connection+(status.state==='audio_muted'?'audio muted':'audio active')+' (amplifier powered)';
  }
  if (status.state==='unknown' && present===true) return 'Connected — control state unknown';
  if (status.state==='standby_pending') return status.standbyRemainingSeconds>0
    ?'Standby in '+Math.ceil(status.standbyRemainingSeconds)+' seconds…':'Waiting for standby report';
  if (status.state==='standby') return status.acknowledged?'Standby — acknowledged':'Standby requested';
  if (status.state==='awake') return status.acknowledged?'Awake — acknowledged':'Awake — reported';
  return {waking:'Waking…',unknown:'Unknown',error:'Subwoofer reported an error'}[status.state] || 'Unknown';
}
function updateStatus(card, sub) {
  card.config=sub;
  const names=new Map(data.renderers.map(r=>[r.id,r.name]));
  const renderer=data.renderers.find(r=>r.id===sub.rendererId);
  card.title.textContent=sub.name;
  card.state.textContent=statusText(sub.status, renderer ? renderer.present : null);
  card.parents.textContent='Follows: '+sub.parentRendererIds.map(id=>names.get(id)||id).join(' · ');
  card.mode.textContent=modeNames[sub.status.mode] || 'Not reported';
  card.demand.textContent=sub.demand.known?(sub.demand.needed?'Needed':'Not needed'):'Unknown';
  card.fallback.textContent=sub.fallbackVolumePercent+'%';
  card.feedback.textContent=sub.status.fresh?'Received '+Math.floor(sub.status.ageSeconds)+'s ago':
    'Control feedback unavailable ('+sub.status.detail.toLowerCase()+')';
  card.detail.textContent=sub.status.fresh?sub.status.detail:'';
}
function makeCard(sub) {
  const root=element('section',undefined,'card'), header=element('div',undefined,'card-header');
  const title=element('h2',sub.name), state=element('div',undefined,'state');header.append(title,state);
  const summary=element('dl',undefined,'summary');
  const fields={};for(const [key,label] of [['mode','Mode'],['demand','Pi demand'],['fallback','Fallback volume'],['feedback','Status feedback']]) {
    const box=element('div'),dd=element('dd');box.append(element('dt',label),dd);summary.append(box);fields[key]=dd;
  }
  const parents=element('p',undefined,'muted'), detail=element('p',undefined,'muted'), editor=element('details');editor.append(element('summary','Edit assignments and fallback volume'));
  const form=element('form'), nameLabel=element('label','Name'), name=element('input');name.value=sub.name;name.required=true;name.maxLength=120;nameLabel.append(name);
  const field=element('fieldset');field.append(element('legend','Speakers that activate this sub'));const parentBox=element('div',undefined,'choices');field.append(parentBox);
  const fallbackLabel=element('label','Fallback volume when all assigned speakers disconnect');
  const fallbackInput=element('input'), fallbackValue=element('output',sub.fallbackVolumePercent+'%');
  fallbackInput.type='range';fallbackInput.min='0';fallbackInput.max='100';
  fallbackInput.step='1';fallbackInput.value=String(sub.fallbackVolumePercent);
  fallbackInput.addEventListener('input',()=>{fallbackValue.textContent=fallbackInput.value+'%';});
  fallbackLabel.append(fallbackInput,fallbackValue);
  const save=element('button','Save settings');save.type='submit';
  form.append(nameLabel,field,fallbackLabel,save);editor.append(form,element('p','Sub ID: '+sub.id+' · Device: '+sub.rendererId,'device-id'));
  root.append(header,parents,summary,detail,editor);$('cards').append(root);
  let dirty=false;
  form.addEventListener('input',()=>{dirty=true;});
  editor.addEventListener('toggle',()=>{if(editor.open && !dirty) {sub=card.config;name.value=sub.name;choices(parentBox,sub.parentRendererIds,sub.rendererId);fallbackInput.value=String(sub.fallbackVolumePercent);fallbackValue.textContent=fallbackInput.value+'%';}});
  form.addEventListener('submit',async e=>{
    e.preventDefault(); if(saving)return;
    const parents=selected(parentBox);if(!parents.length){message('Select at least one speaker.',true);return;}
    saving=true;save.disabled=true;
    try { const result=await request('/api/subwoofers',{id:sub.id,name:name.value,rendererId:sub.rendererId,parentRendererIds:parents,fallbackVolumePercent:Number(fallbackInput.value)});
      sub=result.subwoofer;dirty=false;editor.open=false;message('Assignments saved.');
    } catch(e){message(e.message,true);}finally{saving=false;save.disabled=false;await refresh();}
  });
  const card={root,title,state,parents,detail,config:sub,...fields};cards.set(sub.id,card);return card;
}
async function refresh() {
  if(loading||saving)return;loading=true;
  try {
    data=await request('/api/subwoofers');
    $('connection').textContent=data.snapserverReachable?'Server connected':'Speaker presence unavailable';
    $('connection').className='badge'+(data.snapserverReachable?'':' offline');
    for(const sub of data.subwoofers) updateStatus(cards.get(sub.id)||makeCard(sub),sub);
    $('empty').hidden=data.subwoofers.length>0;
  } catch(e) {
    $('connection').textContent='Server not responding';$('connection').className='badge offline';
    for(const card of cards.values()){card.state.textContent='Unknown — server not responding';card.demand.textContent='Unknown';card.mode.textContent='Not available';card.feedback.textContent='Status unavailable';card.detail.textContent='';}
  } finally {loading=false;}
}
$('add').addEventListener('click',()=>{
  if(!data){message('Wait for the server connection.',true);return;}
  $('registration').hidden=false;$('new-renderer').replaceChildren();
  for(const r of data.renderers.filter(r=>!r.dependent)) {const o=element('option',r.name+' · '+r.id);o.value=r.id;$('new-renderer').append(o);}
  choices($('new-parents'),[],$('new-renderer').value);$('new-name').focus();
});
$('new-renderer').addEventListener('change',()=>choices($('new-parents'),selected($('new-parents')),$('new-renderer').value));
$('cancel').addEventListener('click',()=>{$('registration').hidden=true;});
$('new-fallback').addEventListener('input',()=>{$('new-fallback-value').textContent=$('new-fallback').value+'%';});
$('add-form').addEventListener('submit',async e=>{
  e.preventDefault();if(saving)return;
  const parents=selected($('new-parents'));if(!parents.length){message('Select at least one speaker.',true);return;}
  saving=true;const button=e.target.querySelector('button');button.disabled=true;
  try{await request('/api/subwoofers',{name:$('new-name').value,rendererId:$('new-renderer').value,parentRendererIds:parents,fallbackVolumePercent:Number($('new-fallback').value)});
    $('registration').hidden=true;$('add-form').reset();$('new-fallback-value').textContent='25%';message('Subwoofer registered.');
  }catch(e){message(e.message,true);}finally{saving=false;button.disabled=false;await refresh();}
});
refresh();setInterval(refresh,3000);
