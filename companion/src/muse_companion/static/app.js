'use strict';
const $ = id => document.getElementById(id);
let state = null, lastState = '', signedIn = false, currentTab = 'inbox';
const uid = () => crypto.randomUUID();
function notice(text = '') { $('notice').textContent = text; }
function element(tag, text, className) { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (className) e.className = className; return e; }
function button(text, fn, quiet = false) { const b = element('button', text, quiet ? 'quiet' : ''); b.type = 'button'; b.onclick = () => run(fn); return b; }
async function run(fn) { notice(); try { return await fn(); } catch (e) { notice(e.message || 'Something went wrong'); } }
async function api(path, method = 'GET', body) {
  const options = {method, credentials: 'same-origin'};
  if (body instanceof FormData) options.body = body;
  else if (body !== undefined) { options.body = JSON.stringify(body); options.headers = {'Content-Type':'application/json'}; }
  const response = await fetch(path, options);
  if (!response.ok) {
    if (response.status === 401) showLogin();
    const error = await response.json().catch(() => ({}));
    throw new Error(typeof error.detail === 'string' ? error.detail : 'Please check the request and try again');
  }
  return response.headers.get('content-type')?.includes('application/json') ? response.json() : response.blob();
}
function showLogin() { signedIn = false; $('login').classList.remove('hidden'); $('main').classList.add('hidden'); $('logout').classList.add('hidden'); $('connection').textContent = 'Sign in'; }
function showMain() { signedIn = true; $('login').classList.add('hidden'); $('main').classList.remove('hidden'); $('logout').classList.remove('hidden'); $('connection').textContent = 'Connected'; }
function formatDate(seconds) { return new Date(seconds * 1000).toLocaleString([], {month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}); }
function empty(parent, title, body) { const box = element('div', undefined, 'empty'); box.append(element('strong', title), element('p', body)); parent.append(box); }
function sourceLinks(parent, citations = []) {
  parent.replaceChildren();
  for (const item of citations) {
    if (item.url) {
      try { const url = new URL(item.url); if (!['https:','http:'].includes(url.protocol)) continue;
        const a = element('a', item.title || url.hostname); a.href = url.href; a.target = '_blank'; a.rel = 'noopener noreferrer'; parent.append(a);
      } catch (_) { /* Ignore invalid source URLs. */ }
    } else if (item.filename) parent.append(element('span', item.filename, 'muted'));
  }
}
function showAnswer(result) {
  $('answer').classList.remove('hidden'); $('answer-text').textContent = result.text || 'Saved. You can follow its progress in your inbox.';
  sourceLinks($('sources'), result.citations); $('device-title').textContent = 'A little clarity'; $('device-copy').textContent = 'Your answer is ready';
}
async function refresh(force = false) {
  state = await api('/api/state'); showMain();
  const serialized = JSON.stringify(state, (k,v) => k === 'server_time' ? undefined : v);
  if (force || serialized !== lastState) { lastState = serialized; render(); }
  await renderCaptures();
}
function card(title, body, eyebrow = '', status = '') {
  const root = element('article', undefined, 'card');
  if (eyebrow) root.append(element('p', eyebrow.toUpperCase(), 'eyebrow'));
  if (status) root.append(element('span', status.replaceAll('_',' '), `state ${status}`));
  root.append(element('h3', title), element('div', body, 'body')); return root;
}
async function act(id, action) { await api(`/api/items/${id}/action`, 'POST', {action,operation_id:uid()}); await refresh(true); }
function taskDetail(task) {
  let body = task.result || task.error || task.payload, parsed;
  try { parsed = JSON.parse(body); body = parsed.text || JSON.stringify(parsed, null, 2); } catch (_) { /* Plain result. */ }
  $('detail-title').textContent = task.title; $('detail-body').textContent = body; $('detail-actions').replaceChildren();
  sourceLinks($('detail-actions'), parsed?.citations);
  if(task.state==='needs_approval')$('detail-actions').append(button('Approve these exact details',async()=>{await act(task.id,'approve');$('detail').close();}));
  if (task.kind === 'calendar' && task.state === 'completed') {
    const a = element('a', 'Download calendar event'); a.href = `/api/tasks/${task.id}/calendar.ics`; $('detail-actions').append(a);
  }
  if(task.kind==='meeting'&&task.state==='recording')$('detail-actions').append(button('Finish saved recording',async()=>{await api(`/api/meetings/${task.id}/finish`,'POST');$('detail').close();await refresh(true);}));
  if (task.kind === 'lesson' && task.state === 'completed') {
    $('detail-actions').append(button('Save learning progress', async () => {
      const text = prompt('What did you learn, or what should we practice next?');
      if (text) await api(`/api/lessons/${task.id}/progress`, 'POST', {text,source:'lesson'});
    }));
  }
  if (['queued','running','needs_approval'].includes(task.state)) $('detail-actions').append(button('Cancel task', async () => { await api(`/api/tasks/${task.id}/cancel`,'POST'); $('detail').close(); await refresh(true); }, true));
  if (['failed','cancelled'].includes(task.state) && ['research','briefing','lesson','meeting'].includes(task.kind)) $('detail-actions').append(button('Retry analysis', async () => { await api(`/api/tasks/${task.id}/retry`,'POST'); $('detail').close(); await refresh(true); }));
  $('detail').showModal();
}
function render() {
  const g = state.grep || {};
  $('grep-status').textContent = g.authorized ? `Authorized until ${formatDate(g.expires_at)}. Search and approved reads.` : (g.note || 'Grep is not connected.');
  $('grep-disconnect').disabled = !g.authorized;
  const cards = $('cards'); cards.replaceChildren();
  const tasks = state.tasks.filter(t => t.state !== 'cancelled');
  $('inbox-count').textContent = tasks.filter(t => !['completed','failed'].includes(t.state)).length;
  if (!tasks.length) empty(cards, 'Nothing slipping through the cracks.', 'Ask Muse to create a task, research an idea, or prepare your day.');
  for (const task of tasks) {
    let body = task.error || task.result || task.payload;
    try { const p = JSON.parse(body); body = p.text || Object.entries(p).map(([k,v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`).join('\n'); } catch (_) {}
    const c = card(task.title, body, task.kind, task.state), actions = element('div', undefined, 'card-actions');
    if (task.state === 'needs_approval') { actions.append(button('Review action', () => taskDetail(task)), button('Cancel', () => act(task.id,'reject'), true)); }
    if (task.kind === 'todo' && task.state === 'open') actions.append(button('Done', () => act(task.id,'done')));
    actions.append(button('Details ↗', () => taskDetail(task), true)); c.append(actions); cards.append(c);
  }
  renderMemories();
  const reminders = $('reminder-list'); reminders.replaceChildren();
  if (!state.reminders.length) empty(reminders, 'Your time is yours.', 'Add a reminder above or ask Muse in your own words.');
  for (const reminder of state.reminders) {
    const c = card(reminder.title, formatDate(reminder.due) + (reminder.ssid ? ` · At ${reminder.ssid}` : ''), 'Reminder', reminder.state);
    const actions = element('div',undefined,'card-actions'); actions.append(button('Done',()=>act(reminder.id,'done')),button('Snooze 10m',()=>act(reminder.id,'snooze'),true),button('Change time',()=>{
      $('reminder-title').value=reminder.title; const d=new Date(reminder.due*1000); d.setMinutes(d.getMinutes()-d.getTimezoneOffset()); $('reminder-time').value=d.toISOString().slice(0,16); $('reminder-form').dataset.edit=reminder.id; $('reminder-title').focus();
    },true)); c.append(actions); reminders.append(c);
  }
  const docs=$('documents'); docs.replaceChildren();
  for(const doc of state.documents){ const c=card(doc.name,doc.state,'Library'); const actions=element('div',undefined,'card-actions'); const link=element('a','Open original'); link.href=`/api/documents/${doc.id}/download`; actions.append(link,button('Check index',async()=>{const d=await api(`/api/documents/${doc.id}`);notice(`${d.name}: ${d.state}`);await refresh(true);},true),button('Remove',async()=>{if(confirm(`Remove ${doc.name} from your knowledge library?`)){await api(`/api/documents/${doc.id}`,'DELETE');await refresh(true);}},true));c.append(actions);docs.append(c);}
  const integrations=$('integrations');integrations.replaceChildren();
  if(!state.integrations.length) empty(integrations,'Ready for your connections.','No external account is connected yet. Your task inbox and calendar-file drafts work now. Configure a service on the backend to add its tools.');
  for(const i of state.integrations) integrations.append(card(i.description,`${i.read_only?'Read-only access':'Review before execution'} · ${i.kind}`,'Connected workflow'));
  $('usage').textContent=state.usage.map(u=>`${u.day}: ${u.amount} ${u.category.replaceAll('_',' ')}`).join(' · ');
  const notifications=$('notifications');notifications.replaceChildren();
  for(const n of state.notifications){const c=card(n.title,n.body,'Notification',n.priority);c.append(element('p',n.reason),button('Important',()=>api(`/api/notifications/${encodeURIComponent(n.id)}/feedback`,'POST',{important:true})),button('Keep in digest',()=>api(`/api/notifications/${encodeURIComponent(n.id)}/feedback`,'POST',{important:false}),true));notifications.append(c);}
}
function renderMemories(){const list=$('memory-list');list.replaceChildren();const query=$('memory-search').value.toLowerCase();const memories=state.memories.filter(m=>m.text.toLowerCase().includes(query));if(!memories.length)empty(list,'Keep a little more headspace.','Tell Muse what to remember. Every saved memory has a source and a date.');for(const m of memories){const c=card(m.text,`${m.source} · ${formatDate(m.updated)}`,'Memory');const actions=element('div',undefined,'card-actions');actions.append(button('Correct',async()=>{const text=prompt('Correct this memory',m.text);if(text){await api(`/api/memories/${m.id}`,'PUT',{text,source:m.source});await refresh(true);}},true),button('Forget',async()=>{if(confirm('Permanently forget this memory?')){await api(`/api/memories/${m.id}`,'DELETE');await refresh(true);}},true));c.append(actions);list.append(c);}}
$('login-form').onsubmit=e=>{e.preventDefault();run(async()=>{await api('/api/login','POST',{token:$('token').value});$('token').value='';await refresh(true);});};
$('logout').onclick=()=>run(async()=>{await stopVoice(true);if(meeting){await stopMeeting();if(meeting)throw Error('Microphone is off. Retry the retained meeting uploads before signing out.');}ws?.close();ws=null;await api('/api/logout','POST');showLogin();});
$('chat-form').onsubmit=e=>{e.preventDefault();run(async()=>{$('send').disabled=true;$('device-title').textContent='Thinking it through';try{const text=$('message').value;showAnswer(await api('/api/chat','POST',{text,operation_id:uid()}));$('message').value='';await refresh(true);}finally{$('send').disabled=false;}});};
$('message').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();$('chat-form').requestSubmit();}});
$('memory-form').onsubmit=e=>{e.preventDefault();run(async()=>{await api('/api/memories','POST',{text:$('memory-text').value,source:'typed'});$('memory-text').value='';await refresh(true);});};
$('memory-search').oninput=renderMemories;
$('reminder-form').onsubmit=e=>{e.preventDefault();run(async()=>{const id=$('reminder-form').dataset.edit;await api(id?`/api/reminders/${id}`:'/api/reminders',id?'PUT':'POST',{title:$('reminder-title').value,due_at:new Date($('reminder-time').value).toISOString()});delete $('reminder-form').dataset.edit;$('reminder-form').reset();await refresh(true);});};
$('briefing').onclick=()=>run(async()=>{await api('/api/briefing','POST');notice('Your briefing is being prepared. It will appear in your inbox.');await refresh(true);});
$('save-briefing').onclick=()=>run(async()=>{await api('/api/briefing/schedule','PUT',{hour:Number($('briefing-hour').value)});notice('Daily briefing scheduled in your companion timezone.');});
$('disable-briefing').onclick=()=>run(async()=>{await api('/api/briefing/schedule','PUT',{hour:null});notice('Scheduled briefing turned off.');});
$('lesson-form').onsubmit=e=>{e.preventDefault();run(async()=>{showAnswer(await api('/api/chat','POST',{text:`Create a five-minute practice lesson about ${$('lesson-topic').value}`,operation_id:uid()}));await refresh(true);});};
$('illustration-form').onsubmit=e=>{e.preventDefault();run(async()=>{notice('Creating your illustration…');const blob=await api('/api/illustration','POST',{text:$('illustration-prompt').value,operation_id:uid()});if($('illustration').src.startsWith('blob:'))URL.revokeObjectURL($('illustration').src);$('illustration').src=URL.createObjectURL(blob);$('illustration').classList.remove('hidden');notice();});};
for(const [id,path] of [['document-file','/api/documents'],['photo-file','/api/vision'],['meeting-file','/api/recordings?mode=meeting']]){$(id).onchange=()=>run(async()=>{const file=$(id).files[0];if(!file)return;if(file.size>24*1024*1024)throw Error('Keep uploads below 24 MB.');const form=new FormData();form.append('file',file);notice('Working with your file…');const url=path+(id==='photo-file'?`?question=${encodeURIComponent($('vision-question').value||'Explain this image and suggest the next useful step.')}`:'');const result=await api(url,'POST',form);if(result.text)showAnswer(result);notice(id==='document-file'?'Document uploaded. Check its indexing status below.':'Saved. Check your inbox for results.');$(id).value='';await refresh(true);});}
document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{currentTab=b.dataset.tab;document.querySelectorAll('.view').forEach(v=>v.classList.toggle('hidden',v.id!==currentTab));document.querySelectorAll('[data-tab]').forEach(t=>t.classList.toggle('selected',t===b));});
$('close-detail').onclick=()=>$('detail').close();
$('today').textContent=new Date().toLocaleDateString([],{weekday:'long',month:'long',day:'numeric'}).toUpperCase();

let ws=null,audioContext=null,stream=null,capture=null,source=null,epoch=0,held=false,liveActive=false,playAt=0,playing=[],readyResolve=null;
async function connectVoice(){if(ws?.readyState===WebSocket.OPEN)return;ws=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/v1/device`);ws.binaryType='arraybuffer';ws.onmessage=e=>{if(e.data instanceof ArrayBuffer){const view=new DataView(e.data);if(view.getUint32(0,true)!==epoch)return;const count=(e.data.byteLength-4)/2;if(!audioContext||!count)return;const buffer=audioContext.createBuffer(1,count,16000),data=buffer.getChannelData(0);for(let i=0;i<count;i++)data[i]=view.getInt16(4+i*2,true)/32768;const player=audioContext.createBufferSource();player.buffer=buffer;player.connect(audioContext.destination);playAt=Math.max(playAt,audioContext.currentTime+.04);player.start(playAt);playAt+=count/16000;playing.push(player);player.onended=()=>{playing=playing.filter(p=>p!==player);};return;}const event=JSON.parse(e.data);if(event.type==='voice.ready'&&event.generation===epoch)readyResolve?.();if(event.type==='caption'&&event.generation===epoch){showAnswer({text:event.text});$('voice-status').textContent='Muse is speaking';}if(event.type==='heard')$('device-copy').textContent=event.text.slice(-80);if(event.type==='error')notice(event.text);if(event.type==='voice.done'){$('voice-status').textContent='AI-generated voice · press to talk';run(()=>refresh());}if(event.type==='card')run(()=>refresh());};await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=()=>reject(Error('Could not open the voice connection'));setTimeout(()=>{if(ws.readyState!==WebSocket.OPEN)reject(Error('Voice connection timed out'));},10000);});}
async function startVoice(options={}){if(meeting)throw Error('Finish the meeting recording first.');if(held)return;held=true;$('mic').classList.add('recording');$('voice-status').textContent='Opening microphone…';try{audioContext=audioContext||new AudioContext();await audioContext.resume();for(const p of playing){try{p.stop();}catch(_){}}playing=[];playAt=0;await connectVoice();if(liveActive){ws.send(JSON.stringify({type:'voice.cancel'}));liveActive=false;}epoch++;const ready=new Promise((resolve,reject)=>{readyResolve=resolve;setTimeout(()=>reject(Error('Voice was not ready in time')),20000);});liveActive=!options.language&&$('live').checked;ws.send(JSON.stringify({type:'voice.begin',id:uid(),generation:epoch,mode:options.language?'translate':liveActive?'live':'recorded',language:options.language||'es'}));await ready;if(!held){await stopVoice(true);return;}stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,channelCount:1}});await audioContext.audioWorklet.addModule('/assets/capture.js');capture=new AudioWorkletNode(audioContext,'muse-capture');source=audioContext.createMediaStreamSource(stream);source.connect(capture);capture.connect(audioContext.destination);capture.port.onmessage=e=>{if(!held||ws.readyState!==WebSocket.OPEN)return;if(ws.bufferedAmount>128000){run(()=>stopVoice(true));notice('Network is too slow for live audio.');return;}const data=new Uint8Array(e.data),packet=new Uint8Array(data.length+4);new DataView(packet.buffer).setUint32(0,epoch,true);packet.set(data,4);ws.send(packet);};$('voice-status').textContent='Listening — release when finished';$('device-title').textContent='I’m listening';if(liveActive)$('end-live').classList.remove('hidden');setTimeout(()=>{if(held)run(()=>stopVoice(false));},29000);}catch(e){await stopVoice(true);throw e;}}
async function stopVoice(cancel=false){held=false;$('mic').classList.remove('recording');source?.disconnect();capture?.disconnect();source=capture=null;stream?.getTracks().forEach(t=>t.stop());stream=null;if(ws?.readyState===WebSocket.OPEN)ws.send(JSON.stringify({type:cancel?'voice.cancel':'voice.end',generation:epoch}));if(cancel){liveActive=false;$('end-live').classList.add('hidden');for(const p of playing){try{p.stop();}catch(_){}}playing=[];$('voice-status').textContent='AI-generated voice · press to talk';}else{$('voice-status').textContent='Working on your request…';$('device-title').textContent='One moment';}}
$('mic').onpointerdown=e=>{e.preventDefault();$('mic').setPointerCapture(e.pointerId);run(startVoice);};$('mic').onpointerup=()=>run(()=>stopVoice(false));$('mic').onpointercancel=()=>run(()=>stopVoice(true));$('end-live').onclick=()=>run(()=>stopVoice(true));
setInterval(()=>{if(ws?.readyState===WebSocket.OPEN)ws.send(JSON.stringify({type:'ping'}));},15000);
setInterval(()=>{if(signedIn&&!document.hidden)run(()=>refresh());},5000);
run(()=>refresh(true));

for(const [id,target] of [["translate-out","translate-theirs"],["translate-in","translate-mine"]]){const b=$(id);b.onpointerdown=e=>{e.preventDefault();b.setPointerCapture(e.pointerId);run(()=>startVoice({language:$(target).value}));};b.onpointerup=()=>run(()=>stopVoice(false));b.onpointercancel=()=>run(()=>stopVoice(true));}

async function renderCaptures(){const captures=await api('/api/captures');const box=$('captures');box.replaceChildren();for(const item of captures.filter(c=>c.state!=='completed')){const c=card('Saved voice capture',item.transcript||item.error||'Waiting for processing','Capture',item.state);const a=element('a','Download retained audio');a.href=`/api/captures/${encodeURIComponent(item.id)}/audio`;c.append(a);if(item.state==='failed')c.append(button('Retry transcription',async()=>{await api(`/api/captures/${encodeURIComponent(item.id)}/retry`,'POST');await refresh(true);}));box.append(c);}}
let meeting=null,meetingStream=null,meetingSource=null,meetingCapture=null,meetingTimer=null,meetingChunks=[],meetingSamples=0,meetingPosition=0,meetingMarked=false,meetingPending=[],meetingSending=null,meetingStopping=false;
function queueMeetingSegment(){if(!meetingSamples)return;const pcm=new Int16Array(meetingSamples);let at=0;for(const chunk of meetingChunks){pcm.set(chunk,at);at+=chunk.length;}meetingPending.push({position:meetingPosition++,pcm:pcm.buffer,marked:meetingMarked});meetingChunks=[];meetingSamples=0;meetingMarked=false;drainMeeting();}
function drainMeeting(){if(meetingSending)return meetingSending;meetingSending=(async()=>{while(meetingPending.length){const item=meetingPending[0];const response=await fetch(`/api/meetings/${meeting.id}/segments/${item.position}?marked=${item.marked}`,{method:'PUT',headers:{'Content-Type':'application/octet-stream'},body:item.pcm});if(!response.ok)throw Error('Recording upload paused. Keep this tab open and retry saved uploads.');const result=await response.json();meetingPending.shift();$('meeting-status').textContent=`${meetingStopping?'Saved':'● Recording'} · ${meetingPosition} segments · ${result.text.slice(-150)}`;}})().catch(e=>{$('meeting-retry').classList.remove('hidden');notice(e.message);}).finally(()=>{meetingSending=null;});return meetingSending;}
async function stopMeeting(){if(!meeting)return;meetingStopping=true;meetingSource?.disconnect();meetingCapture?.disconnect();meetingStream?.getTracks().forEach(t=>t.stop());clearInterval(meetingTimer);queueMeetingSegment();if(meetingSending)await meetingSending;if(meetingPending.length){$('meeting-status').textContent='Microphone off. Keep this tab open to retry retained audio.';return;}await api(`/api/meetings/${meeting.id}/finish`,'POST');meeting=null;$('meeting-start').disabled=false;$('meeting-stop').classList.add('hidden');$('meeting-mark').classList.add('hidden');$('meeting-retry').classList.add('hidden');$('meeting-status').textContent='Recording complete. Your summary is being prepared.';await refresh(true);}
$('meeting-start').onclick=()=>run(async()=>{if(meeting||held)throw Error('Finish the current microphone session first.');audioContext=audioContext||new AudioContext();await audioContext.resume();meetingStream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,channelCount:1}});try{meeting=await api('/api/meetings','POST',{title:'Meeting notebook'});await audioContext.audioWorklet.addModule('/assets/capture.js');meetingCapture=new AudioWorkletNode(audioContext,'muse-capture');meetingSource=audioContext.createMediaStreamSource(meetingStream);meetingSource.connect(meetingCapture);meetingCapture.connect(audioContext.destination);meetingPosition=0;meetingStopping=false;meetingChunks=[];meetingSamples=0;meetingPending=[];meetingCapture.port.onmessage=e=>{const chunk=new Int16Array(e.data);meetingChunks.push(chunk);meetingSamples+=chunk.length;if(meetingSamples>=320000)queueMeetingSegment();if(meetingPending.length>2){run(stopMeeting);notice('Microphone stopped because uploads cannot keep up. Retry the saved segments.');}};$('meeting-start').disabled=true;$('meeting-stop').classList.remove('hidden');$('meeting-mark').classList.remove('hidden');$('meeting-status').textContent='● Recording visibly. Make sure participants know.';meetingTimer=setInterval(()=>{if(meetingPosition>=179)run(stopMeeting);},1000);}catch(e){meetingStream.getTracks().forEach(t=>t.stop());throw e;}});
$('meeting-stop').onclick=()=>run(stopMeeting);
$('meeting-mark').onclick=()=>{meetingMarked=true;$('meeting-status').textContent='● Recording · current segment marked important';};
$('meeting-retry').onclick=()=>run(async()=>{await drainMeeting();if(meetingStopping&&!meetingPending.length)await stopMeeting();});
window.addEventListener('beforeunload',e=>{if(meeting){e.preventDefault();e.returnValue='Recording in progress';}});

$('grep-check').onclick=()=>run(async()=>{const g=await api('/api/grep/status');notice(g.connected?'Grep is connected and ready.':g.note);await refresh(true);});
$('grep-disconnect').onclick=()=>run(async()=>{if(confirm('Disconnect Grep from Muse? You can reconnect from the companion host.')){await api('/api/grep/disconnect','POST');await refresh(true);}});
