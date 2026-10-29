// Offline ownership/namespace tests for the actual mongosh cleanup source.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[2], 'utf8');
class Oid {
    constructor(id) { this.id = id; }
    equals(other) { return other instanceof Oid && this.id === other.id; }
    toHexString() { return this.id; }
}
const sid = new Oid('a'.repeat(24));
const tid = new Oid('b'.repeat(24));
const dcid = new Oid('c'.repeat(24));
const other = new Oid('d'.repeat(24));
const run = 'e'.repeat(32);
const node = 'f'.repeat(24);
const batch = '1'.repeat(32);
const name = 'm05_native_' + run;
const equal = (a, b) => a instanceof Oid ? a.equals(b) : a === b;
function matches(doc, query) {
    return Object.entries(query).every(([key, value]) => {
        if (key === '$or') return value.some(q => matches(doc, q));
        if (value && typeof value === 'object' && !(value instanceof Oid)) {
            if ('$in' in value) return value.$in.some(v => equal(doc[key], v));
            if ('$ne' in value) return !equal(doc[key], value.$ne);
        }
        return equal(doc[key], value);
    });
}
function fixture() {
    const s = {_id: sid, name, description: run, col_id: dcid}; // col_name deliberately absent (bson:"-")
    const t = {_id: tid, spider_id: sid, cmd: 'python -B -u worker.py',
               param: '--batch-id ' + batch + ' --m05-base-url http://127.0.0.1:18081',
               node_id: new Oid(node), status: 'finished'};
    const c = {_id: dcid, name};
    const docs = {spiders: [s], tasks: [t], spider_stats: [{_id:sid}], task_stats: [{_id:tid}],
                  task_queue: [{_id:tid}], data_collections: [c], gits: [],
                  // Actual user snapshot: live primaries, _del=false, _obj=null.
                  artifacts: [{_id:sid,_col:'spiders',_del:false,_obj:null}, {_id:tid,_col:'tasks',_del:false,_obj:null},
                              {_id:dcid,_col:'data_collections',_del:false,_obj:null}], [name]: []};
    // Existing unrelated data must survive every successful cleanup.
    for (const table of ['spiders','tasks','spider_stats','task_stats','task_queue','artifacts','data_collections']) {
        docs[table].push({_id: other, name:'existing', spider_id:other, col_id:other});
    }
    let writes = 0;
    const api = {auth:()=>({ok:1}), getCollectionNames:()=>Object.keys(docs),
                 getCollection:table=>collection(table)};
    function collection(table) {
        return {findOne:q=>(docs[table]||[]).find(d=>matches(d,q))||null,
                find:q=>({limit:()=>({toArray:()=>(docs[table]||[]).filter(d=>matches(d,q))})}),
                countDocuments:q=>(docs[table]||[]).filter(d=>matches(d,q)).length,
                deleteMany:q=>{writes++;docs[table]=(docs[table]||[]).filter(d=>!matches(d,q));},
                deleteOne:q=>{writes++;const i=(docs[table]||[]).findIndex(d=>matches(d,q));if(i>=0)docs[table].splice(i,1);},
                drop:()=>{writes++;delete docs[table];}};
    }
    for (const table of Object.keys(docs)) api[table]=collection(table);
    const P={sid:sid.id,run,name,collection:name,batch,node,tasks:[tid.id],dc:dcid.id,action:'prune'};
    const context={P,db:{getSiblingDB:()=>api},ObjectId:id=>new Oid(id),process:{env:{}}, print:()=>{}};
    return {docs,P,context,run:()=>vm.runInNewContext(source,{...context}),writes:()=>writes};
}
let scenarios=0;
{
    const f=fixture(); f.run();
    for (const table of ['spiders','tasks','spider_stats','task_stats','task_queue','artifacts','data_collections']) {
        assert.equal(f.docs[table].length,1); assert.equal(f.docs[table][0]._id.id,other.id);
    }
    assert.equal(f.docs[name],undefined); scenarios++;
    f.P.action='verify'; f.run(); scenarios++;
    f.P.action='prune'; f.run(); scenarios++;
}
for (const alter of [
    f=>{f.docs.spiders[0].description='foreign';},
    f=>{f.docs.tasks[0].status='running';},
    f=>{f.docs.tasks[0].param='another batch';},
    f=>{f.docs.spiders.push({_id:other,col_id:dcid});},
    f=>{f.docs[name].push({unexpected:'result'});},
    f=>{f.docs.artifacts[2]._obj={name:'foreign'};},
    f=>{f.docs.gits.push({_id:sid});}
]) {
    const f=fixture(); alter(f); assert.throws(()=>f.run()); assert.equal(f.writes(),0); scenarios++;
}
{
    const f=fixture(); f.P.action='verify'; assert.throws(()=>f.run()); assert.equal(f.writes(),0); scenarios++;
}
// A null object is accepted only alongside its independently checked live row.
for (const [table,index] of [['spiders',0],['tasks',1],['data_collections',2]]) {
    const missing=fixture(); missing.docs[table].shift();
    assert.throws(()=>missing.run()); assert.equal(missing.writes(),0); scenarios++;
    const deleted=fixture(); deleted.docs.artifacts[index]._del=true;
    assert.throws(()=>deleted.run()); assert.equal(deleted.writes(),0); scenarios++;
}
for (const deleted of [false,true]) {
    const f=fixture();
    for (const [table,index] of [['spiders',0],['tasks',1],['data_collections',2]]) {
        f.docs.artifacts[index]._obj=f.docs[table][0];
        f.docs.artifacts[index]._del=deleted;
        if(deleted) f.docs[table].shift();
    }
    f.run(); assert(f.writes()>0);
    assert.equal(f.docs.artifacts.length,1); assert.equal(f.docs.artifacts[0]._id.id,other.id);
    scenarios++;
}
for (const [index,obj] of [[0,{name:'foreign'}],[1,{spider_id:other}]]) {
    const f=fixture(); f.docs.artifacts[index]._obj=obj; f.docs.artifacts[index]._del=true;
    assert.throws(()=>f.run()); assert.equal(f.writes(),0); scenarios++;
}
console.log(JSON.stringify({check:'PASS',scenarios}));
