'use strict';
(() => {
  let studio = {records: [], alerts: []}, editing = null, pending = false;
  const names = {morning:'Morning brief', meeting_prep:'Meeting / 1:1 prep', prototype:'Prototype brief', feedback:'Feedback synthesis', demo:'Demo rehearsal', evaluation:'Compare experiments', research:'AI research watch', closeout:'Daily closeout'};
  const definitions = {
    project: [['status','Status',['active','paused','done']],['goal','Goal','textarea'],['app_url','App link','url'],['success_metric','Success measure','textarea'],['limitations','Known limitations','textarea'],['checks','Configured read-only checks (one name per line)','lines']],
    person: [['role','Role','text']],
    meeting: [['start','Starts','datetime-local',true],['end','Ends','datetime-local',true],['purpose','Purpose','textarea'],['participants','People','people'],['documents','Document links (one per line)','lines']],
    decision: [['state','Agreement',['suggested','agreed','superseded']],['owner','Owner','text'],['rationale','Rationale','textarea'],['alternatives','Alternatives considered','textarea']],
    commitment: [['state','Agreement / progress',['suggested','agreed','waiting','done','cancelled']],['owner','Owner / waiting on','text'],['due','Due','datetime-local'],['next_step','Next useful step','textarea']],
    blocker: [['state','Progress',['open','resolved']],['severity','Attention',['normal','important']],['owner','Owner','text'],['next_step','Next useful step','textarea',true]],
    feedback: [['source_group','Independent source (customer or interview)','text',true],['quote','Original feedback','textarea',true]],
    measurement: [['variant','Variant name','text',true],['suite','Dataset / suite version','text',true],['case','Case ID','text',true],['success','Succeeded?',['unknown','yes','no']],['latency_ms','Latency (ms)','number'],['cost_usd','Cost (USD)','number']]
  };
  function labelField(parent, name, label, type='text', value='', required=false) {
    const wrap = element('label', label, 'studio-field');
    const input = element(Array.isArray(type) || type==='people' ? 'select' : ['textarea','lines'].includes(type) ? 'textarea' : 'input');
    input.name = name; input.id = `studio-${name}`; wrap.htmlFor = input.id;
    if (Array.isArray(type)) for (const v of type) { const o=element('option',v.replaceAll('_',' '));o.value=v;input.append(o); }
    else if (type==='people') {input.multiple=true;for(const r of studio.records.filter(r=>r.kind==='person')){const o=element('option',r.title);o.value=r.id;o.selected=(value||[]).includes(r.id);input.append(o);}}
    else if (input.tagName==='INPUT') {input.type=type;if(type==='number'){input.min='0';input.step='any';}}
    if (type!=='people') input.value=type==='lines' ? (value||[]).join('\n') : type==='datetime-local' && value ? localDate(value) : Array.isArray(type) ? (type.includes(value) ? value : type[0] || '') : value ?? '';
    input.required=required;if(input.tagName==='TEXTAREA')input.rows=3;
    wrap.append(input);parent.append(wrap);return input;
  }
  function localDate(iso){const d=new Date(iso);d.setMinutes(d.getMinutes()-d.getTimezoneOffset());return d.toISOString().slice(0,16);}
  function linkedSelect(parent,name,label,kind,value=''){
    const input=labelField(parent,name,label,[]);
    const blank=element('option',kind==='project'?'All projects / no project':'None selected');blank.value='';input.append(blank);
    for(const r of studio.records.filter(r=>r.kind===kind)){const o=element('option',r.title);o.value=r.id;input.append(o);}input.value=value;return input;
  }
  function showEditor(item=null){
    editing=item;const box=$('studio-editor');box.classList.remove('hidden');box.replaceChildren(element('h3',item?'Edit context':'Add context'));
    const form=element('form',undefined,'studio-form');
    const kind=labelField(form,'kind','Type',Object.keys(definitions),item?.kind||'project');kind.disabled=!!item;
    labelField(form,'title','Title','text',item?.title||'',true);
    linkedSelect(form,'project_id','Project','project',item?.project_id||'');
    linkedSelect(form,'person_id','Person','person',item?.person_id||'');
    labelField(form,'note','Context','textarea',item?.note||'');
    labelField(form,'source_url','Source link','url',item?.source_url||'');
    labelField(form,'observed_at','Source observed at','datetime-local',item?.observed_at||new Date().toISOString(),true);
    const details=element('div',undefined,'studio-details');form.append(details);
    function fields(){details.replaceChildren();for(const [key,label,type,required] of definitions[kind.value])labelField(details,key,label,type,key==='success'?(item?.details.success==null?'unknown':item.details.success?'yes':'no'):item?.details[key],required);}
    kind.onchange=fields;fields();
    const actions=element('div',undefined,'card-actions'),save=element('button','Save context');save.type='submit';actions.append(save,button('Cancel',()=>box.classList.add('hidden'),true));form.append(actions);
    const operation=uid();
    form.onsubmit=e=>{e.preventDefault();run(async()=>{
      const values=new FormData(form),data={kind:kind.value,details:{},operation_id:operation,revision:editing?.revision||0,source_id:editing?.source_id||''};
      for(const key of ['title','note','source_url','project_id','person_id'])data[key]=values.get(key)||'';
      data.observed_at=new Date(values.get('observed_at')).toISOString();
      for(const [key,,type] of definitions[kind.value]){
        const raw=values.get(key)||'';
        data.details[key]=type==='people'?values.getAll(key):type==='lines'?raw.split('\n').map(v=>v.trim()).filter(Boolean):type==='datetime-local'?(raw?new Date(raw).toISOString():null):type==='number'?(raw===''?null:Number(raw)):key==='success'?(raw==='unknown'?null:raw==='yes'):raw;
      }
      // Preserve imported product metrics when editing the measured fields.
      if(kind.value==='measurement')data.details.product_metrics=item?.details.product_metrics||{};
      save.disabled=true;try{await api('/api/studio/records'+(editing?'/'+editing.id:''),editing?'PUT':'POST',data);box.classList.add('hidden');await renderStudio();notice('Context saved with its source and revision.');}finally{save.disabled=false;}
    });};
    box.append(form);box.scrollIntoView({behavior:'smooth',block:'start'});$('studio-title').focus();
  }
  async function showHistory(item){
    const revisions=await api(`/api/studio/records/${item.id}/history`);
    const linkedTitle=(id,kind)=>studio.records.find(r=>r.id===id)?.title||`Unavailable ${kind}`;
    const sections=revisions.map(entry=>{
      const r=entry.body,lines=[`Revision ${entry.revision} · saved ${new Date(entry.created*1000).toLocaleString()}`,r.title];
      if(r.project_id)lines.push(`Project: ${linkedTitle(r.project_id,'project')}`);
      if(r.person_id)lines.push(`Person: ${linkedTitle(r.person_id,'person')}`);
      lines.push(`Source observed: ${new Date(r.observed_at).toLocaleString()}`);
      if(r.note)lines.push(`Context: ${r.note}`);
      for(const [key,label,type] of definitions[r.kind]||[]){
        let value=r.details[key];
        if(value===null||value===undefined||value===''||(Array.isArray(value)&&!value.length))continue;
        if(type==='datetime-local')value=new Date(value).toLocaleString();
        else if(type==='people')value=value.map(id=>linkedTitle(id,'person')).join(', ');
        else if(Array.isArray(value))value=value.join('\n');
        else if(typeof value==='boolean')value=value?'Yes':'No';
        lines.push(`${label}: ${value}`);
      }
      for(const [name,value] of Object.entries(r.details.product_metrics||{}))lines.push(`${name}: ${value}`);
      return lines.join('\n');
    });
    $('detail-title').textContent=`History: ${item.title}`;
    $('detail-body').textContent=['Saved versions, newest first.'+(revisions.length===100?' Showing the latest 100 revisions.':''),...sections].join('\n\n');
    sourceLinks($('detail-actions'),revisions.filter(r=>r.body.source_url).map(r=>({url:r.body.source_url,title:`Revision ${r.revision} source`})));
    $('detail-actions').append(button('Edit current context',()=>{$('detail').close();showEditor(item);},true));
    $('detail').showModal();
  }
  $('studio-add').onclick=()=>showEditor();
  function renderWork(){
    const box=$('studio-work'),previous=box.querySelector('form');if(previous)return;
    box.append(element('h3','What would help right now?'));
    const form=element('form',undefined,'studio-form');
    const workflow=labelField(form,'workflow','Prepare',[]);for(const [key,name] of Object.entries(names)){const o=element('option',name);o.value=key;workflow.append(o);}
    linkedSelect(form,'work_project','Project','project');linkedSelect(form,'work_person','Person (for a 1:1)','person');linkedSelect(form,'work_meeting','Meeting','meeting');
    labelField(form,'instructions','What should Muse focus on?','textarea');
    const comparison=element('div',undefined,'studio-details hidden');labelField(comparison,'baseline','Baseline name');labelField(comparison,'candidate','Candidate name');form.append(comparison);
    workflow.onchange=()=>comparison.classList.toggle('hidden',workflow.value!=='evaluation');
    const submit=element('button','Prepare draft');submit.type='submit';form.append(submit);
    form.onsubmit=e=>{e.preventDefault();run(async()=>{submit.disabled=true;try{const task=await api('/api/studio/jobs','POST',{workflow:workflow.value,project_id:$('studio-work_project').value,person_id:$('studio-work_person').value,meeting_id:$('studio-work_meeting').value,instructions:$('studio-instructions').value,baseline:$('studio-baseline').value,candidate:$('studio-candidate').value,operation_id:uid()});await refresh(true);notice(`${task.title} is queued. Follow it in your inbox.`);}finally{submit.disabled=false;}});};box.append(form);
  }
  function renderSchedule(){
    const box=$('studio-schedule');if(box.querySelector('form'))return;
    box.append(element('h3','A rhythm for your day'),element('p','Scheduled drafts use the companion timezone. Important exceptions wait through quiet hours.'));
    const form=element('form',undefined,'studio-form');const workflow=labelField(form,'schedule_workflow','Draft',[]);
    for(const key of ['morning','research','closeout']){const o=element('option',names[key]);o.value=key;workflow.append(o);}
    const hour=labelField(form,'schedule_hour','Local hour (0–23)','number','8',true);hour.max='23';hour.step='1';
    const status=element('p',undefined,'muted');box.append(status);
    function current(){const s=(studio.schedules||[]).find(s=>s?.workflow===workflow.value);status.textContent=s?.hour!=null?`Scheduled at ${s.hour}:00 · ${s.weekdays_only?'weekdays':'every day'}`:'Not scheduled';if(s?.hour!=null)hour.value=s.hour;}
    workflow.onchange=current;current();
    const actions=element('div',undefined,'card-actions'),save=element('button','Schedule weekdays');save.type='submit';actions.append(save,button('Turn off',async()=>{await api('/api/studio/schedule','PUT',{workflow:workflow.value,hour:null});await renderStudio();current();},true));form.append(actions);
    form.onsubmit=e=>{e.preventDefault();run(async()=>{await api('/api/studio/schedule','PUT',{workflow:workflow.value,hour:Number(hour.value),weekdays_only:true});await renderStudio();current();notice('Schedule saved.');});};box.append(form);
  }
  function refreshOptions(id,kind){const input=$(id);if(!input)return;const value=input.value;input.replaceChildren(element('option','None selected'));input.firstChild.value='';for(const r of studio.records.filter(r=>r.kind===kind)){const o=element('option',r.title);o.value=r.id;input.append(o);}input.value=value;}
  async function renderStudio(){
    if(pending)return;pending=true;try{
      studio=await api('/api/studio');renderWork();renderSchedule();
      for(const [id,kind] of [['work_project','project'],['work_person','person'],['work_meeting','meeting']])refreshOptions('studio-'+id,kind);
      const alerts=$('studio-alerts');alerts.replaceChildren();if(!studio.alerts.length)empty(alerts,'No open exceptions.','Overdue agreed commitments and blockers appear here.');
      const groups=new Map();for(const a of studio.alerts){const key=a.project_id||'';if(!groups.has(key))groups.set(key,[]);groups.get(key).push(a);}
      for(const [project,items] of groups){const group=element('div');group.append(element('h4',studio.records.find(r=>r.id===project)?.title||'Across the Studio'));for(const a of items){const d=JSON.parse(a.body),c=card(a.title,`${d.owner?d.owner+' · ':''}${d.next_step||'Choose a next step'}`,'Exception',d.state);c.append(button('Snooze 1 hour',async()=>{await api(`/api/studio/alerts/${a.id}/snooze`,'POST');await renderStudio();}),button('Dismiss until changed',async()=>{await api(`/api/studio/alerts/${a.id}/dismiss`,'POST');await renderStudio();},true));group.append(c);}alerts.append(group);}
      const records=$('studio-records');records.replaceChildren();if(!studio.records.length)empty(records,'Start with one project.','Add its goal, then link people, meetings and commitments. You can also ask Muse to save context by voice.');
      for(const r of studio.records){const d=r.details,c=card(r.title,r.note||d.quote||d.next_step||d.goal||d.rationale||d.purpose||d.role||'',r.kind,d.state||d.status||'');c.append(element('p',`Observed ${new Date(r.observed_at).toLocaleString()} · revision ${r.revision}`,'muted'));const links=element('div',undefined,'sources');sourceLinks(links,r.source_url?[{url:r.source_url,title:'Original source'}]:[]);c.append(links,button('Review / edit',()=>showEditor(r),true),button('View history',()=>showHistory(r),true));records.append(c);}
    }finally{pending=false;}
  }
  window.renderStudio=renderStudio;
  window.addStudioDraftActions=(task,parsed)=>{
    if(!['studio','meeting'].includes(task.kind)||task.state!=='completed')return;
    $('detail-actions').append(button('Edit draft',()=>{
      if($('studio-draft-text'))return;
      const form=element('form',undefined,'correction-form'),input=labelField(form,'draft-text','Your edited draft','textarea',parsed?.text||'',true);input.maxLength=24000;
      const save=element('button','Save draft');save.type='submit';form.append(save);$('detail-actions').append(form);
      form.onsubmit=e=>{e.preventDefault();run(async()=>{await api(`/api/studio/drafts/${task.id}`,'PUT',{text:input.value,revision:task.revision});$('detail').close();await refresh(true);});};
    }),button('Copy draft',async()=>{await navigator.clipboard.writeText(parsed?.text||'');notice('Draft copied.');},true));
    const connections=(state.integrations||[]).filter(c=>!c.read_only&&c.fields.length);
    if(connections.length)$('detail-actions').append(button('Prepare work action',()=>{
      if($('studio-action-name'))return;
      const form=element('form',undefined,'correction-form'),select=labelField(form,'action-name','Connected action',[]),fields=element('div',undefined,'studio-details');
      for(const c of connections){const option=element('option',c.description+(c.destination?' · '+c.destination:''));option.value=c.name;select.append(option);}
      function inputs(){fields.replaceChildren();for(const key of connections.find(c=>c.name===select.value).fields){const value=['text','description','app_description','change_description'].includes(key)?parsed?.text||'':key==='title'?task.title:'';labelField(fields,'action-'+key,key.replaceAll('_',' '),value.length>160?'textarea':'text',value,true);}}
      select.onchange=inputs;inputs();form.append(fields);
      const submit=element('button','Create approval card');submit.type='submit';form.append(submit);$('detail-actions').append(form);
      const operation=uid();form.onsubmit=e=>{e.preventDefault();run(async()=>{const arguments_={};for(const key of connections.find(c=>c.name===select.value).fields)arguments_[key]=$('studio-action-'+key).value;submit.disabled=true;try{await api(`/api/studio/drafts/${task.id}/propose`,'POST',{name:select.value,arguments:arguments_,revision:task.revision,operation_id:operation});$('detail').close();await refresh(true);notice('Action prepared. Review its destination and full content in the inbox.');}finally{submit.disabled=false;}});};
    },true));
  };
})();
