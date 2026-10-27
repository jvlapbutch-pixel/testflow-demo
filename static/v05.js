// v0.5 interface: optional extension over v0.4 without overwriting legacy data.
const tf5={kits:[],commercial:[],reworks:[],dashboard:{},ufs:[]};
const v5q=s=>document.querySelector(s);
const v5esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function v5api(path,method='GET',body){const r=await fetch('/api/v05'+path,{method,headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});const data=await r.json();if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));return data}
const v5message=(s,err=false)=>{const x=v5q('#message');x.textContent=s;x.className=err?'error':'';x.style.display='block'};
function v5options(rows,key,label){return rows.map(r=>`<option value="${v5esc(r[key])}">${v5esc(label(r))}</option>`).join('')}
async function v5load(){try{const [kits,commercial,reworks,dashboard,ufs]=await Promise.all([v5api('/kits'),v5api('/commercial'),v5api('/reworks'),v5api('/dashboard'),v5api('/ufs')]);Object.assign(tf5,{kits,commercial,reworks,dashboard,ufs});v5render()}catch(e){v5message('Falha no módulo v0.5: '+e.message,true)}}
function v5render(){const d=tf5.dashboard;
 const metrics=[['Kits (produtos finais)',d.kits],['Execuções concluídas',d.testes_realizados],['Kits aprovados (estado atual)',d.aprovados],['Kits reprovados (estado atual)',d.reprovados],['Pendentes',d.pendentes],['Em testes',d.em_teste],['Retrabalhos abertos',d.retrabalhos_abertos],['Pedidos não expedidos',d.pedidos_abertos]];
 v5q('#metrics').innerHTML=metrics.map(([l,n])=>`<div class="card"><small>${v5esc(l)}</small><strong>${n??0}</strong></div>`).join('');
 const ufOptions='<option value="">Sem UF</option>'+v5options(tf5.ufs,'uf',x=>x.nome+' ('+x.uf+')');
 for(const sel of ['#v5orderUf','#v5ufValue','#v5lineUf']){const el=v5q(sel),before=el.value;el.innerHTML=ufOptions;el.value=before|| (sel==='#v5orderUf'?'SP':'')}
 const kitOpts=v5options(tf5.kits,'id',k=>k.id+' · '+k.tipo+' · '+(k.uf||'sem UF')+' · '+k.status);
 for(const sel of ['#v5kit','#v5ufKit']){const e=v5q(sel),old=e.value;e.innerHTML='<option value="">Selecione</option>'+kitOpts;e.value=old}
 const lines=tf5.commercial.flatMap(c=>c.lines.map(l=>({...l,number:c.number})));
 const el=v5q('#v5line'),old=el.value;el.innerHTML='<option value="">Selecione</option>'+v5options(lines,'id',l=>l.number+' · '+l.kit_type+' / '+l.uf+' · '+l.allocated+'/'+l.requested+' alocados');el.value=old;
 v5q('#v5pdfOrder').innerHTML='<option value="">Todos</option>'+v5options(tf5.commercial,'id',c=>c.number);v5q('#v5targetOrder').innerHTML='<option value="">Selecione</option>'+v5options(tf5.commercial,'id',c=>c.number);
 v5q('#v5orderList').innerHTML=tf5.commercial.map(c=>`<div class="kit"><h3>${v5esc(c.number)} · ${v5esc(c.customer)} <small>${v5esc(c.status)}</small></h3>${c.lines.map(l=>`<div><b>${v5esc(l.kit_type)} · ${l.uf}</b><p>Solicitados: ${l.requested} · Alocados: ${l.allocated} · Liberados após aprovação: ${l.released} · Expedidos: ${l.shipped}</p><p>Disponíveis fora de pedidos: ${l.available_unallocated} (aprovados: ${l.available_approved}) · Faltam até para alocar: ${l.pending_allocation} · Estimativa sem kit cadastrado/disponível: ${l.estimated_shortage}</p>${l.allocations.map(a=>`<p>${v5esc(a.kit_id)} · ${v5esc(a.status)} ${a.shipped_at?'· Expedido':`<button data-ship="${a.id}">Expedir (se aprovado)</button> <button data-unlink="${a.id}">Desvincular</button>`}</p>`).join('')}</div>`).join('')}</div>`).join('')||'<p>Nenhum pedido cadastrado.</p>';
 v5q('#v5reworkRows').innerHTML=tf5.reworks.map(r=>`<tr><td>${v5esc(r.kit_id)} / OT-${String(r.order_id).padStart(4,'0')}</td><td>${v5esc(r.criterion)}</td><td>${v5esc(r.tester)}</td><td>${v5esc(r.status)}</td><td>${r.status==='Concluído'?'Concluído':`<button data-repair="${r.id}">Registrar manutenção</button> <button data-retest="${r.order_id}">Criar reteste</button>`}</td></tr>`).join('');
}
v5q('#v5new').onsubmit=async e=>{e.preventDefault();const f=new FormData(e.target);try{await v5api('/commercial','POST',{number:f.get('number'),customer:f.get('customer'),notes:f.get('notes'),lines:[{kit_type:f.get('kit_type'),uf:f.get('uf'),requested:Number(f.get('requested'))}]});e.target.reset();await v5load();v5message('Pedido cadastrado. Parecer de saldo disponível.')}catch(x){v5message(x.message,true)}};
v5q('#v5addLine').onsubmit=async e=>{e.preventDefault();try{await v5api('/commercial/'+v5q('#v5targetOrder').value+'/lines','POST',{kit_type:v5q('#v5lineType').value,uf:v5q('#v5lineUf').value,requested:Number(v5q('#v5lineQty').value)});await v5load();v5message('Linha adicionada ao pedido.')}catch(x){v5message(x.message,true)}};
v5q('#v5allocate').onsubmit=async e=>{e.preventDefault();try{await v5api('/commercial/lines/'+v5q('#v5line').value+'/allocate','POST',{kit_id:v5q('#v5kit').value});await v5load();v5message('Kit alocado ao pedido. A liberação depende da aprovação.')}catch(x){v5message(x.message,true)}};
v5q('#v5uf').onsubmit=async e=>{e.preventDefault();try{await v5api('/kits/'+encodeURIComponent(v5q('#v5ufKit').value)+'/uf','PUT',{uf:v5q('#v5ufValue').value||null});await v5load();v5message('UF do Kit salva.')}catch(x){v5message(x.message,true)}};
v5q('#v5orderList').onclick=async e=>{const b=e.target.closest('button');if(!b)return;try{if(b.dataset.ship)await v5api('/commercial/allocations/'+b.dataset.ship+'/ship','POST');if(b.dataset.unlink)await v5api('/commercial/allocations/'+b.dataset.unlink+'/unlink','POST');await v5load();v5message('Vínculo comercial atualizado.')}catch(x){v5message(x.message,true)}};
v5q('#v5reworkRows').onclick=async e=>{const b=e.target.closest('button');if(!b)return;try{if(b.dataset.repair){const technician=prompt('Responsável pela manutenção:');if(!technician)return;const repair_notes=prompt('Descrição do reparo realizado:')||'';await v5api('/reworks/'+b.dataset.repair,'PUT',{status:'Aguardando reteste',technician,repair_notes})}if(b.dataset.retest){await fetch('/api/orders/OT-'+String(b.dataset.retest).padStart(4,'0')+'/retest',{method:'POST'}).then(async r=>{if(!r.ok)throw Error(JSON.stringify(await r.json()))})}await v5load();await refresh();v5message('Retrabalho atualizado.')}catch(x){v5message(x.message,true)}};
v5q('#v5pdf').onclick=()=>{const params=new URLSearchParams();if(v5q('#v5week').value)params.set('start',v5q('#v5week').value);if(v5q('#v5pdfOrder').value)params.set('commercial_id',v5q('#v5pdfOrder').value);if(v5q('#v5testerFilter').value.trim())params.set('technician',v5q('#v5testerFilter').value.trim());window.open('/api/v05/reports/weekly.pdf?'+params,'_blank')};
v5q('#v5testerFilter').addEventListener('input',()=>{const v=v5q('#v5testerFilter').value.toLowerCase().trim();for(const tr of v5q('#reportRows').children)tr.style.display=(!v||tr.children[2]?.textContent.toLowerCase().includes(v))?'':'none'});
// Ensure dashboard and new modules update after legacy refreshes without replacing existing handlers.
const v5origRefresh=refresh;refresh=async function(){await v5origRefresh();await v5load()};
window.addEventListener('load',v5load);

// Checklist shortcut to substitution: the user must explicitly select the defective component.
v5q('#checklist').addEventListener('change',e=>{
 const el=e.target;if(!el.matches('[data-criterion]'))return;
 el.parentElement.querySelector('.v5swapShortcut')?.remove();
 if(el.value!=='Reprovado')return;
 const name=(el.dataset.criterion||'').toLowerCase();
 if(!/(scanner|tablet|câmera|camera)/.test(name))return;
 const btn=document.createElement('button');btn.className='v5swapShortcut';btn.textContent='Abrir substituição de componente';btn.type='button';btn.onclick=()=>{
   const active=v5q('#activeOrder').value;const order=state.orders.find(o=>o.id===active);
   if(!order)return;
   v5q('#swapKit').value=order.equipment_id;
   v5q('#swapKit').dispatchEvent(new Event('change'));
   const nav=v5q('[data-view="swaps"]');nav.click();
   v5q('#swapOt').value=active;
   v5q('#swapReason').value='Etapa reprovada: '+el.dataset.criterion;
 };
 el.parentElement.appendChild(btn)
});
