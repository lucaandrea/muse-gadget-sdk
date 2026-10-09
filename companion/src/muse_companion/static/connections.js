'use strict';
let replitPoll=null;
async function replitStatus() {
  const result=await api('/api/replit/status');
  $('replit-status').textContent=result.error || (!result.configured?'Set the companion’s public address in deployment settings to connect Replit.':result.connecting?'Waiting for Replit sign-in…':result.authorized?'Replit authorization saved. App actions still require your review.':'Replit is not connected.');
  $('replit-connect').disabled=!result.configured||result.connecting;
  $('replit-disconnect').disabled=!result.authorized&&!result.connecting;
  $('replit-auth-link').replaceChildren();
  if(result.authorization_url){
    const url=new URL(result.authorization_url);
    if(url.protocol==='https:'&&(url.hostname==='replit.com'||url.hostname.endsWith('.replit.com'))){
      const a=element('a','Continue sign-in at Replit ↗');a.href=url.href;a.target='_blank';a.rel='noopener noreferrer';$('replit-auth-link').append(a);
    }
  }
  clearTimeout(replitPoll);
  if(result.connecting)replitPoll=setTimeout(()=>{if(signedIn)run(replitStatus);},2000);
}
$('replit-refresh').onclick=()=>run(replitStatus);
$('replit-connect').onclick=()=>run(async()=>{await api('/api/replit/connect','POST');await replitStatus();});
$('replit-disconnect').onclick=()=>run(async()=>{await api('/api/replit/disconnect','POST');await replitStatus();notice('Local authorization removed. Revoke the grant in Replit settings to remove its access there.');});

async function workAccountsStatus(){
  const accounts=await api('/api/work-accounts');
  const records=(await api('/api/studio')).records;
  const box=$('work-accounts');box.replaceChildren();
  for(const account of accounts){
    const service=account.service,path='/api/work-accounts/'+service;
    const names={slack:'Slack',linear:'Linear',google_calendar:'Google Calendar'};
    const row=element('article');row.className='card';
    row.append(element('h4',names[service]),element('p',account.error|| (account.connected?'Connected as '+account.account_name:'Not connected')));
    if(!account.oauth_configured){
      const prefix={slack:'SLACK',linear:'LINEAR',google_calendar:'GOOGLE'}[service];
      row.append(element('p',`Setup: register a ${names[service]} OAuth app, then set ${prefix}_CLIENT_ID and ${prefix}_CLIENT_SECRET in deployment Secrets.`));
      row.append(element('p','Redirect address: '+(account.redirect_uri||'Set MUSE_PUBLIC_URL to this companion’s HTTPS address.')));
    }
    const connect=button(account.connected?'Reconnect':'Connect',async()=>{
      const result=await api(path+'/connect','POST');
      const url=new URL(result.authorization_url);
      const hosts={slack:'slack.com',linear:'linear.app',google_calendar:'accounts.google.com'};
      if(url.protocol!=='https:'||url.hostname!==hosts[service])throw Error('Unexpected sign-in address.');
      const link=element('a','Continue to '+names[service]+' to review access ↗');link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';
      auth.replaceChildren(link);connect.disabled=true;
    });connect.disabled=!account.oauth_configured;
    const auth=element('div');row.append(connect,auth);
    if(account.connected){
      row.append(button('Check access',async()=>{await api(path+'/check','POST');await workAccountsStatus();}),button('Disconnect',async()=>{await api(path+'/disconnect','POST');await workAccountsStatus();await refresh(true);notice('Access removed from Muse. Revoke the provider grant in its settings if needed.');}));
      row.append(element('p','Destination: '+(account.destination_name||'Choose below')));
      row.append(button('Choose destination',async()=>{
        const choices=await api(path+'/destinations');
        const select=element('select');select.setAttribute('aria-label',names[service]+' destination');
        for(const item of choices.items){const option=element('option',item.name);option.value=item.id;select.append(option);}
        if(account.destination)select.value=account.destination;
        const project=element('select');project.setAttribute('aria-label','Link imported source to a Studio project');
        const none=element('option','No fixed Studio project');none.value='';project.append(none);
        for(const record of records.filter(r=>r.kind==='project')){const option=element('option',record.title);option.value=record.id;project.append(option);}
        project.value=account.project_id||'';
        const sync=element('input');sync.type='checkbox';sync.checked=!!account.sync_enabled;
        const label=element('label','Sync this source every 15 minutes');label.prepend(sync);
        const form=element('div');form.append(select,project);if(service!=='slack')form.append(label);
        form.append(button('Save destination',async()=>{await api(path+'/destination','PUT',{destination:select.value,project_id:project.value,sync_enabled:service!=='slack'&&sync.checked});await workAccountsStatus();await refresh(true);}));
        if(choices.truncated)form.append(element('p','The source returned a partial destination list. Narrow account access or contact your administrator if the destination is absent.'));
        destination.replaceChildren(form);
      }));
      const destination=element('div');row.append(destination);
      if(service!=='slack'&&account.destination){
        row.append(element('p',account.sync.error|| (account.sync.last_success?'Last synced '+formatDate(account.sync.last_success)+(account.sync.truncated?' · partial source coverage':''):'No successful source sync yet.')));
        row.append(button('Sync now',async()=>{await api(path+'/sync','POST');await workAccountsStatus();notice('Source records refreshed. Open Studio to review them.');}));
      }
    }
    box.append(row);
  }
}
$('work-accounts-refresh').onclick=()=>run(workAccountsStatus);
$('grep-session').onchange=()=>run(async()=>{
  const file=$('grep-session').files[0];if(!file)return;
  try{if(file.size>8192)throw Error('Choose the small restricted session JSON file from grep-connect.');
    await api('/api/grep/session','POST',JSON.parse(await file.text()));notice('Restricted Grep session saved in encrypted durable storage.');await refresh(true);
  }finally{$('grep-session').value='';}
});
