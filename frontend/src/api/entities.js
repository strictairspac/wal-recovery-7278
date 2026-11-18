import {api} from './client.js';
export const logSetsAPI={list:()=>api('/log-sets'),create:data=>api('/log-sets',{method:'POST',body:JSON.stringify(data)})};
export const crashCasesAPI={list:()=>api('/crash-cases'),create:data=>api('/crash-cases',{method:'POST',body:JSON.stringify(data)})};
export const recoveriesAPI={list:()=>api('/recoveries'),get:id=>api('/recoveries/'+id),create:data=>api('/recoveries',{method:'POST',body:JSON.stringify(data)})};
export const batchesAPI={
 list:()=>api('/batches'),
 get:batchNo=>api('/batches/'+encodeURIComponent(batchNo)),
 submit:data=>api('/batches',{method:'POST',body:JSON.stringify(data)}),
 crashDemo:data=>api('/batches/crash-demo',{method:'POST',body:JSON.stringify(data)}),
};
export const ledgerAPI={
 accounts:()=>api('/ledger/accounts'),
 entries:batchNo=>api('/ledger/entries'+(batchNo?('?batch_no='+encodeURIComponent(batchNo)):'')),
};
