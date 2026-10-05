'use strict';
const assert=require('node:assert/strict');
const A=require('../assets/adapters.js');
let passed=0;
const payload=()=>({matrix:[[1,2],[3,4]],metadata:{sampleIntervalNs:.1},parameters:{gain:1},projectId:'P1',batchId:'B1',taskId:'T1',lineId:'L1',source:'import'});
const completed=(value,version='v1')=>({status:'completed',source:'测试回调，非真实算法',version,result:{value}});
async function test(name,fn){await fn();passed++;console.log('PASS '+name);}
async function main(){
  await test('两个雷达处理接口默认未接入，调用明确失败',async()=>{
    assert.deepEqual(A.list().map(a=>a.name),['RCAN','RTM']);
    assert.ok(A.list().every(a=>a.connected===false&&a.version===null));
    for(const name of A.list().map(a=>a.name))await assert.rejects(A.run(name,payload()),/未接入/);
  });
  await test('注册必须提供版本和函数，状态副本不污染注册表',async()=>{
    assert.throws(()=>A.register('x',{run(){}}),/version/);
    assert.throws(()=>A.register('x',{version:'v1'}),/run/);
    assert.throws(()=>A.register('',{version:'v1',run(){}}),/名称/);
    A.register('unit',{version:'v1',run:p=>completed(p.matrix[0][0])});
    assert.equal(A.list().find(a=>a.name==='unit').connected,true);
    const list=A.list();list.find(a=>a.name==='unit').version='bad';
    assert.equal(A.list().find(a=>a.name==='unit').version,'v1');
    assert.equal((await A.run('unit',payload())).result.value,1);
  });
  await test('缓存采用输入/标定/参数/全部关联/source/版本，键序不影响命中',async()=>{
    let calls=0;A.register('cache',{version:'v1',run:()=>completed(++calls)});
    const p=payload();await A.run('cache',p);await A.run('cache',p);assert.equal(calls,1);
    for(const field of ['projectId','batchId','taskId','lineId','source']){const x=payload();x[field]+='-different';await A.run('cache',x);}
    assert.equal(calls,6);
    const m=payload();m.matrix[0][0]=9;await A.run('cache',m);
    const metadata=payload();metadata.metadata.sampleIntervalNs=.2;await A.run('cache',metadata);
    const params=payload();params.parameters.gain=2;await A.run('cache',params);assert.equal(calls,9);
    const reordered={source:p.source,lineId:p.lineId,taskId:p.taskId,batchId:p.batchId,projectId:p.projectId,parameters:p.parameters,metadata:p.metadata,matrix:p.matrix};
    await A.run('cache',reordered);assert.equal(calls,10,'第九个键已逐出最早的条目');
    await A.run('cache',p);assert.equal(calls,10,'不同对象键序仍应命中同一输入');
    A.register('cache',{version:'v2',run:()=>completed(++calls,'v2')});await A.run('cache',p);assert.equal(calls,11);
    A.clearCache();await A.run('cache',p);assert.equal(calls,12);
  });
  await test('输入、输出与缓存深拷贝，回调和调用者不能互相污染',async()=>{
    let calls=0;A.register('copies',{version:'v1',run:p=>{calls++;p.matrix[0][0]=100;p.metadata.nested={flag:true};return completed({nested:[5]});}});
    const p=payload(),out=await A.run('copies',p);assert.equal(p.matrix[0][0],1);assert.equal(p.metadata.nested,undefined);
    out.result.value.nested[0]=-1;const again=await A.run('copies',p);assert.deepEqual(again.result.value.nested,[5]);assert.equal(calls,1);
    again.result.value.nested.push(6);assert.deepEqual((await A.run('copies',p)).result.value.nested,[5]);
  });
  await test('缓存最多 8 条并按最近使用淘汰，clearCache 全部清空',async()=>{
    let calls=0;A.register('bounded',{version:'v1',run:()=>completed(++calls)});
    const variant=i=>({...payload(),lineId:'L'+i});
    for(let i=0;i<8;i++)await A.run('bounded',variant(i));assert.equal(calls,8);
    await A.run('bounded',variant(0));assert.equal(calls,8);
    await A.run('bounded',variant(8));assert.equal(calls,9);
    await A.run('bounded',variant(0));assert.equal(calls,9,'刚访问的条目保留');
    await A.run('bounded',variant(1));assert.equal(calls,10,'最久未使用条目已淘汰');
    A.clearCache();await A.run('bounded',variant(0));assert.equal(calls,11);
  });
  await test('错误不缓存，可用同一输入重试',async()=>{
    let calls=0;A.register('retry',{version:'v1',run:()=>{if(++calls===1)throw Error('可重试失败');return completed(calls);}});
    await assert.rejects(A.run('retry',payload()),/可重试失败/);
    assert.equal((await A.run('retry',payload())).result.value,2);await A.run('retry',payload());assert.equal(calls,2);
  });
  await test('输出对象、来源、版本与合法终态必须校验',async()=>{
    for(const bad of [null,[],{}, {status:'completed',source:'',version:'v1'}, {status:'completed',source:'test',version:'v2'}, {status:'unknown',source:'test',version:'v1'}]){
      let calls=0;A.register('contract',{version:'v1',run:()=>{calls++;return bad;}});
      await assert.rejects(A.run('contract',payload()));await assert.rejects(A.run('contract',payload()));assert.equal(calls,2);
    }
    A.register('failed',{version:'v1',run:()=>({source:'test',version:'v1',status:'failed',message:'模型失败'})});
    await assert.rejects(A.run('failed',payload()),/模型失败/);
    A.register('cancelled',{version:'v1',run:()=>({source:'test',version:'v1',status:'cancelled'})});
    await assert.rejects(A.run('cancelled',payload()),e=>e.name==='AbortError');
  });
  await test('预先取消不调用回调；执行中取消立即拒绝且不回填缓存',async()=>{
    let calls=0,finish,started;
    const entered=new Promise(resolve=>{started=resolve;});
    A.register('abort',{version:'v1',run:(p,{signal})=>{calls++;assert.ok(signal);if(calls===1){started();return new Promise(resolve=>{finish=resolve;});}return completed(calls);}});
    const prior=new AbortController();prior.abort();
    await assert.rejects(A.run('abort',payload(),{signal:prior.signal}),e=>e.name==='AbortError');assert.equal(calls,0);
    const controller=new AbortController(),pending=A.run('abort',payload(),{signal:controller.signal});await entered;controller.abort();
    await assert.rejects(pending,e=>e.name==='AbortError');finish(completed(999));await Promise.resolve();
    assert.equal((await A.run('abort',payload(),{signal:new AbortController().signal})).result.value,2);assert.equal(calls,2);
  });
  await test('清空缓存或重注册时，旧的执行中任务不得回填缓存',async()=>{
    let calls=0,finish,started;const entered=new Promise(resolve=>{started=resolve;});
    A.register('generation',{version:'v1',run:()=>{calls++;if(calls===1){started();return new Promise(resolve=>{finish=resolve;});}return completed(calls);}});
    const pending=A.run('generation',payload());await entered;A.clearCache();finish(completed(1));await pending;
    assert.equal((await A.run('generation',payload())).result.value,2);
    A.register('generation',{version:'v1',run:()=>completed(3)});assert.equal((await A.run('generation',payload())).result.value,3);
  });
  await test('拒绝非矩形/无限振幅/不可序列化数据；不访问网络',async()=>{
    A.register('input',{version:'v1',run:()=>completed(1)});
    for(const matrix of [[[1],[2]],[[1,2],[3]],[[1,2],[3,Infinity]]])await assert.rejects(A.run('input',{...payload(),matrix}));
    const circular=payload();circular.metadata.self=circular.metadata;await assert.rejects(A.run('input',circular),/循环/);
    const unsupported=payload();unsupported.parameters.fn=()=>1;await assert.rejects(A.run('input',unsupported),/JSON/);
    await assert.rejects(A.run('input',{...payload(),source:''}),/source/);
  });
  console.log(`\n${passed} adapter tests passed. 注册回调均为测试桩，不代表真实算法接入。`);
}
main().catch(err=>{console.error(err);process.exitCode=1;});
