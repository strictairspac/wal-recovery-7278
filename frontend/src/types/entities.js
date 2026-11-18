/** @typedef {{id:number,name:string,initial:object,records:object[],description:string}} LogSet */
/** @typedef {{id:number,name:string,log_set_id:number,crash_after:number}} CrashCase */
/** @typedef {{id:number,crash_case_id:number,label:string,status:string,snapshot:object,result:object|null}} Recovery */
/** @typedef {{id:number,batch_no:string,station:string,content_hash:string,payload:object[],status:'processing'|'committed',attempts:number,receipt:object|null,committed_at:string|null}} BatchWrite */
/** @typedef {{id:number,key:string,balance:number}} LedgerAccount */
/** @typedef {{id:number,batch_id:number,batch_no:string,key:string,delta:number,balance_after:number}} LedgerEntry */
export {};
