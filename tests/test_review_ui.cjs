/* Exercise the real inline UI script against a minimal DOM, without a browser,
   network, actual COCO images, or writing any lab review decisions. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
  constructor(tag='div') { this.tag=tag; this.children=[]; this.events={}; this.attributes={}; this.style={}; this.value=''; this.textContent=''; this.checked=false; this.disabled=false; this.clientWidth=640; this.files=[]; }
  addEventListener(name, handler){this.events[name]=handler;}
  setAttribute(name,value){this.attributes[name]=value;}
  append(...items){this.children.push(...items);}
  appendChild(item){this.children.push(item);return item;}
  replaceChildren(...items){this.children=[...items];}
  add(option){this.children.push(option);}
  get options(){return this.children;}
  click(){if(this.onclick)this.onclick();}
  remove(){}
}
class Option extends Element {
  constructor(text,value){super('option');this.textContent=text;this.value=value;}
}
const html=fs.readFileSync(path.join(__dirname,'../tools/review_phase2.html'),'utf8');
const script=html.match(/<script>\s*([\s\S]*?)<\/script>/)[1];
const evidence={segment:{id:523,class_name:'truck',mask_pixels:400},detection:{row:1,class_name:'car'},bbox_iou:1,mask_coverage:1};
const candidate={id:'img_0001:class_swap',image:'img_0001',type:'class_swap',possible_types:['class_swap','category_swap'],reason:'Synthetic test only',image_available:true,evidence:[evidence]};
const asset={width:64,height:64,image_data:'data:image/png;base64,fixture',error:null,boxes:[{row:1,class_name:'car',bbox_xyxy:[10,10,30,30]}],segments:[{id:523,class_name:'truck',bbox_xyxy:[10,10,30,30],path:'M10 10h20v1h-20z'}]};
const data={release_id:'test-release',fingerprint:'test-fingerprint',candidates:[candidate],assets:{img_0001:asset},semantic_types:['class_swap','category_swap','missing_box','bbox_drift','instance_merge']};
const elements={};
for(const id of html.matchAll(/id="([^"]+)"/g))elements[id[1]]=new Element();
elements.reviewData.textContent=JSON.stringify(data);
elements.zoom.value='100';elements.showBoxes.checked=true;elements.showMask.checked=true;
const sandbox={document:{getElementById:id=>elements[id],createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag),body:new Element('body')},Option,Blob,URL:{createObjectURL:()=> 'blob:test',revokeObjectURL:()=>{}},setTimeout:fn=>fn(),window:{addEventListener:()=>{}},console};
vm.createContext(sandbox);vm.runInContext(script,sandbox);
const run=code=>vm.runInContext(code,sandbox);
function json(code){return JSON.parse(run(`JSON.stringify(${code})`));}

(async()=>{
 assert.equal(elements.progress.textContent,'0/1 ứng viên xong · 1 ảnh');
 assert.equal(run('Object.keys(decisions).length'),0,'No manufactured decisions');
 assert.equal(elements.drawing.children.filter(c=>c.tag==='image').length,1);
 assert.equal(elements.drawing.children.filter(c=>c.tag==='path').length,1);
 assert.equal(elements.drawing.children.filter(c=>c.tag==='rect').length,1);
 assert.throws(()=>run('csvText()'),/Chưa xác nhận/);
 elements.exportCsv.click();assert.match(elements.message.textContent,/Chưa xác nhận/);
 elements.reviewer.value='Nguyễn Hải Đăng';
 elements.reviewed.checked=true;elements.reviewed.onchange();
 assert.equal(elements.decisions.children.length,1,'Only assigned bbox proposal is required');
 elements.extraType.value='category_swap';elements.addType.click();
 const blocks=elements.decisions.children;
 for(let i=0;i<blocks.length;i++){
  const verdict=blocks[i].children[0].children[1],note=blocks[i].children[1];
  verdict.value=i===0?'rejected':'confirmed';verdict.events.change();
  note.value=`Fixture ${i} only: thấy vật giả lập, ghi "dấu ngoặc"\nxuống dòng và tiếng Việt.`;note.events.input();
 }
 assert.equal(elements.progress.textContent,'1/1 ứng viên xong · 1 ảnh');
 const csv=run('csvText()');assert.match(csv,/^image,type,verdict,note\n/);assert.match(csv,/"img_0001"/);assert.match(csv,/Nguyễn Hải Đăng/);assert.match(csv,/""dấu ngoặc""/);
 run("globalThis.savedDecision=JSON.parse(JSON.stringify(decisions[currentId]));decisions[currentId].rows[1].verdict='';decisions[currentId].rows[1].note='';decisions[currentId].rows[1].explicit=false;render()");
 assert.equal(elements.progress.textContent,'1/1 ứng viên xong · 1 ảnh','Rejected bbox is reviewed despite untouched legacy mask row');
 assert.equal(elements.decisions.children.length,1,'Blank legacy mask row stays hidden');
 assert.doesNotMatch(run('csvText()'),/category_swap/,'No invented mask verdict is exported');
 run('decisions[currentId]=savedDecision;render()');
 const progress=json('progress()');assert.equal(progress.decisions[candidate.id].rows[1].verdict,'confirmed');
 run('decisions={};dirty=false;render()');
 elements.importProgress.files=[{text:async()=>JSON.stringify(progress)}];await elements.importProgress.onchange();
 assert.match(elements.message.textContent,/Đã nạp progress/);
 assert.equal(run('csvText()'),csv,'Progress round trip retains actual CSV');
 const wrong={...progress,fingerprint:'old'};
 elements.importProgress.files=[{text:async()=>JSON.stringify(wrong)}];await elements.importProgress.onchange();
 assert.match(elements.message.textContent,/không thuộc đúng release/);
 const conflict=JSON.parse(JSON.stringify(progress));conflict.decisions[candidate.id].rows[0].note='Different synthetic reviewer observation';
 elements.importProgress.files=[{text:async()=>JSON.stringify(conflict)}];await elements.importProgress.onchange();assert.match(elements.message.textContent,/xung đột/);
 elements.imageFilter.value='no-such-image';elements.imageFilter.events.input();assert.equal(elements.heading.textContent,'Không có ứng viên theo bộ lọc');
 elements.imageFilter.value='';elements.imageFilter.events.input();
 run("DATA.assets.img_0001.error='missing image'");assert.throws(()=>run('csvText()'),/Không có ảnh/);
 run('DATA.assets.img_0001.error=null;decisions[currentId].rows[0].note="short"');assert.throws(()=>run('csvText()'),/20 ký tự/);
 run('DATA.candidates=[];decisions={};render()');assert.equal(elements.heading.textContent,'Không có ứng viên theo bộ lọc');assert.equal(run('csvText()'),'image,type,verdict,note\n');
 // Local autosave uses a mocked network; no browser or lab state is touched.
 const liveElements={};for(const id of html.matchAll(/id="([^"]+)"/g))liveElements[id[1]]=new Element();
 const liveData=JSON.parse(JSON.stringify(data));liveData.persistence={endpoint:'/api/progress/hard',token:'fixture-token'};
 liveElements.reviewData.textContent=JSON.stringify(liveData);liveElements.zoom.value='100';
 const requests=[];let rejectSave=false;
 const liveSandbox={...sandbox,document:{getElementById:id=>liveElements[id],createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag),body:new Element('body')},location:{protocol:'http:'},setTimeout:()=>1,clearTimeout:()=>{},fetch:async(url,options)=>{
  if(!options)return {ok:true,json:async()=>({progress:{...progress,revision:3,submitted:false}})};
  const body=JSON.parse(options.body);requests.push(body);
  if(rejectSave)return {ok:false,status:409,json:async()=>({error:'Newer saved work exists'})};
  return {ok:true,json:async()=>({revision:body.revision+1,status:{state:body.submitted?'complete':'needs_human_review'}})};
 }};
 vm.createContext(liveSandbox);vm.runInContext(script,liveSandbox);await new Promise(setImmediate);
 assert.equal(liveElements.saveServer.disabled,false);
 assert.equal(vm.runInContext('serverRevision',liveSandbox),3);
 await vm.runInContext('saveToServer()',liveSandbox);
 assert.equal(requests[0].revision,3);assert.equal(requests[0].submitted,false);
 liveElements.finishServer.click();await new Promise(setImmediate);
 assert.equal(requests.at(-1).submitted,true);assert.match(liveElements.message.textContent,/hoàn tất toàn bộ/);
 rejectSave=true;await vm.runInContext('saveToServer()',liveSandbox);
 assert.equal(vm.runInContext('saveBlocked',liveSandbox),true);assert.match(liveElements.message.textContent,/Newer saved work/);
 console.log('Review UI checks passed: pending gates, SVG layers, Vietnamese/CSV, progress round trip, conflicts, missing images, filters, empty CSV.');
})().catch(error=>{console.error(error);process.exitCode=1;});
