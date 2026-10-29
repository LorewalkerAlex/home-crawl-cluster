// POC-only cleanup of an unpredictable namespace, never a general retention job.
// The Python controller supplies P via a JSON literal; credentials stay in Mongo.
const d = db.getSiblingDB('crawlab');
const a = d.auth(process.env.CRAWLAB_MONGO_USERNAME, process.env.CRAWLAB_MONGO_PASSWORD);
if (!a || a.ok !== 1) throw new Error('POC database authentication failed');
const insist = (condition) => { if (!condition) throw new Error('POC ownership or state check failed'); };
insist(/^[0-9a-f]{24}$/.test(P.sid));
insist(/^[0-9a-f]{32}$/.test(P.run));
insist(P.name === 'm05_native_' + P.run);
insist(P.collection === P.name);
insist(['preflight', 'snapshot', 'prune', 'verify'].includes(P.action));
const sid = ObjectId(P.sid);
const s = d.spiders.findOne({_id: sid});
const old = d.artifacts.findOne({_id: sid});
// ColName is json-only (bson:"-"); ownership uses name/description and ColId.
if (s) insist(s.name === P.name && s.description === P.run);
if (old) insist(old._col === 'spiders' && (old._obj === null ? old._del === false && !!s : !!old._obj && old._obj.name === P.name));
const tasks = d.tasks.find({spider_id: sid}).limit(3).toArray();
insist(tasks.length <= 1);
for (const t of tasks) {
    insist(t.cmd === 'python -B -u worker.py');
    insist(t.param === '--batch-id ' + P.batch + ' --m05-base-url http://127.0.0.1:18081');
    insist(t.node_id.toHexString() === P.node);
}
const taskIds = [...new Set(tasks.map(t => t._id.toHexString()).concat(P.tasks))];
insist(taskIds.length <= 1 && taskIds.every(id => /^[0-9a-f]{24}$/.test(id)));
const tids = taskIds.map(id => ObjectId(id));
for (const tid of tids) {
    const t = d.tasks.findOne({_id: tid});
    if (t) insist(t.spider_id.equals(sid));
    const art = d.artifacts.findOne({_id: tid});
    if (art) insist(art._col === 'tasks' && (art._obj === null ? art._del === false && !!t : !!art._obj && art._obj.spider_id.equals(sid)));
}
const dc = d.data_collections.findOne({name: P.collection});
let dcids = P.dc ? [ObjectId(P.dc)] : [];
if (dc) {
    if (P.dc) insist(dc._id.equals(dcids[0]));
    dcids = [dc._id];
    if (s) insist(s.col_id.equals(dc._id));
    insist(d.spiders.countDocuments({_id: {$ne: sid}, col_id: dc._id}) === 0);
}
for (const id of dcids) {
    const registry = d.data_collections.findOne({_id: id});
    if (registry) insist(registry.name === P.collection);
    const archive = d.artifacts.findOne({_id: id});
    if (archive) insist(archive._col === 'data_collections' && (archive._obj === null ? archive._del === false && !!registry : !!archive._obj && archive._obj.name === P.collection));
}
const ids = [sid].concat(tids, dcids);
const collections = d.getCollectionNames();
const state = () => ({
    tasks: taskIds,
    statuses: tasks.map(t => t.status),
    dc: dcids.length ? dcids[0].toHexString() : null,
    counts: {
        spiders: d.spiders.countDocuments({_id: sid}),
        tasks: d.tasks.countDocuments({$or: [{spider_id: sid}, {_id: {$in: tids}}]}),
        task_queue: d.task_queue.countDocuments({_id: {$in: tids}}),
        task_stats: d.task_stats.countDocuments({_id: {$in: tids}}),
        spider_stats: d.spider_stats.countDocuments({_id: sid}),
        artifacts: d.artifacts.countDocuments({_id: {$in: ids}}),
        data_collections: d.data_collections.countDocuments({name: P.collection}),
        result_collection: d.getCollectionNames().includes(P.collection) ? 1 : 0,
        gits: d.gits.countDocuments({_id: sid})
    }
});
if (P.action === 'preflight') {
    insist(Object.values(state().counts).every(n => n === 0));
}
if (P.action === 'prune') {
    insist(tasks.every(t => ['finished', 'error', 'cancelled', 'abnormal'].includes(t.status)));
    // The controller also confirms no matching Worker process before this step.
    insist(d.gits.countDocuments({_id: sid}) === 0);
    if (collections.includes(P.collection)) {
        insist(d.getCollection(P.collection).countDocuments({}) === 0);
    }
    // Validate all ownership before any deletion; every query is scoped to POC IDs.
    d.task_queue.deleteMany({_id: {$in: tids}});
    d.task_stats.deleteMany({_id: {$in: tids}});
    d.tasks.deleteMany({_id: {$in: tids}, spider_id: sid});
    d.spider_stats.deleteOne({_id: sid});
    d.spiders.deleteOne({_id: sid, name: P.name, description: P.run});
    d.artifacts.deleteMany({_id: {$in: ids}});
    d.data_collections.deleteOne({name: P.collection, _id: {$in: dcids}});
    if (collections.includes(P.collection)) d.getCollection(P.collection).drop();
}
if (P.action === 'verify') insist(Object.values(state().counts).every(n => n === 0));
print('M05_POC_JSON:' + JSON.stringify(state()));
