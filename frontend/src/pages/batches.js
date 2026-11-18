import {batchesAPI,ledgerAPI} from '../api/entities.js';
import {session} from '../stores/session.js';
import {table} from '../components/common/table.js';
import {badge} from '../components/common/status.js';
import {esc,formObject} from '../utils/html.js';
import {message} from '../hooks/action.js';
import {downloadReceipt} from '../api/client.js';

const SAMPLE_ITEMS=[{key:'sample_A',delta:20},{key:'sample_B',delta:-10}];

export function batchesPage(){
 return `<div class="section-title"><h2>采样站批次重试</h2><span>同批次号重发整批 · 同内容幂等 · 换内容拒绝</span></div>
<section class="split">
 <div>
  <h2>录入批次并模拟收不到回执</h2>
  <p>采样站收不到回执时，用<b>同一批次号</b>重发整批写入。同批同内容不重复入账、返回原回执；换了内容一律拒绝。原批未结束时并发只认一份提交。</p>
  <p class="big-note">回执与账本同一事务生效，断电重试不丢数据。</p>
  <details><summary>查看 items 字段格式</summary><pre>${esc(JSON.stringify(SAMPLE_ITEMS,null,2))}</pre></details>
 </div>
 ${session.role!=='viewer'?`<form id="batch-form">
   <label>批次号 batch_no<input name="batch_no" value="BATCH-20261118-01" required></label>
   <label>采样站 station<input name="station" value="海砾一号站" required></label>
   <label>整批写入 items（JSON 数组）<textarea name="items" rows="5" required>${esc(JSON.stringify(SAMPLE_ITEMS,null,2))}</textarea></label>
   <label>故障演练<select name="action"><option value="">不注入故障（正常提交）</option><option value="rollback">提交前断电（回滚，需重试恢复）</option><option value="stuck">回执丢失（已入账，重试取回回执）</option></select></label>
   <button type="submit">提交 / 重试本批次 →</button>
 </form>`:'<p class="readonly">观察员仅可查看批次、回执与账本。</p>'}
</section>
<div class="section-title"><h2>批次与回执</h2><button class="secondary" id="batch-refresh">刷新</button></div>
${table(['批次号','采样站','状态','尝试次数','内容哈希','回执'],storeSnapshot.batches.map(b=>[
 esc(b.batch_no),esc(b.station),badge(b.status),b.attempts,
 `<code>${esc((b.content_hash||'').slice(0,12))}…</code>`,
 b.status==='committed'
  ?`<a href="#" class="receipt-link" data-batch="${esc(b.batch_no)}">下载/查看原回执</a>`
  :'<span class="readonly">未生成（断电残留，待重试）</span>',
]))}
<div class="section-title"><h2>断电后账本核对</h2><span>余额 = 初始 + 已提交批次流水</span></div>
${table(['账本键','当前余额'],storeSnapshot.accounts.map(a=>[esc(a.key),a.balance]))}
<h2>入账流水（最近200条）</h2>
${table(['流水号','批次号','账本键','增量','入账后余额'],storeSnapshot.entries.map(e=>[e.id,esc(e.batch_no),esc(e.key),e.delta,e.balance_after]))}`;
}

// 页面级快照，由 bindBatches 在每次渲染前刷新；与全局演练 store 解耦
export const storeSnapshot={batches:[],accounts:[],entries:[],async refresh(){
 [this.batches,this.accounts,this.entries]=await Promise.all([batchesAPI.list(),ledgerAPI.accounts(),ledgerAPI.entries()]);
}};

function parseForm(f){
 const d=formObject(f);
 let items;
 try{items=JSON.parse(d.items);}catch(e){throw new Error('items 不是合法 JSON 数组');}
 if(!Array.isArray(items)||!items.length)throw new Error('items 必须是非空数组');
 const body={batch_no:d.batch_no,station:d.station,items};
 return {body,action:d.action};
}

export function bindBatches(render){
 document.querySelector('#batch-refresh')?.addEventListener('click',render);
 document.querySelectorAll('.receipt-link').forEach(a=>a.addEventListener('click',e=>{
  e.preventDefault();
  downloadReceipt(a.dataset.batch).catch(err=>message(err.message,true));
 }));
 const form=document.querySelector('#batch-form');
 if(!form)return;
 form.addEventListener('submit',async e=>{
  e.preventDefault();
  const btn=form.querySelector('button[type=submit]');
  btn.disabled=true;
  let toast=null; // [文本, 是否错误]；先渲染再提示，避免 render 重建 DOM 后提示丢失
  try{
   const {body,action:crashAction}=parseForm(form);
   if(crashAction){
    try{
     await batchesAPI.crashDemo({...body,action:crashAction});
     toast=['故障未按预期触发，请重试',true];
    }catch(err){
     // 503 是演练的预期结果：采样站就是没拿到回执；后端消息已含故障类型与重试指引
     toast=[err.message,true];
    }
   }else{
    const out=await batchesAPI.submit(body);
    toast=[out.replay
      ?`幂等重放：返回原回执 ${out.receipt.receipt_no}，未重复入账`
      :`已提交：回执 ${out.receipt.receipt_no} 与账本同时生效`,false];
   }
  }catch(err){
   // 内容冲突 409、校验 422、权限等错误显式提示
   toast=[err.message,true];
  }finally{btn.disabled=false;}
  await render();
  if(toast)message(toast[0],toast[1]);
 });
}
