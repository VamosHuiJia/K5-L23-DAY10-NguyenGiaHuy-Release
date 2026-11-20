/* UI logic tests with a fake DOM: no browser, sockets, or real lab verdicts. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
class Element{
 constructor(tag='div'){this.tag=tag;this.children=[];this.style={};this.attributes={};this.events={};this.value='';this.textContent='';this.checked=false;this.disabled=false;this.hidden=false;this.clientWidth=640;this.files=[];}
 append(...items){this.children.push(...items);}appendChild(item){this.children.push(item);return item;}
 replaceChildren(...items){this.children=[...items];}add(item){this.children.push(item);}get options(){return this.children;}
 setAttribute(key,value){this.attributes[key]=value;}addEventListener(key,fn){this.events[key]=fn;}
 click(){if(this.onclick)this.onclick();}remove(){}
}
class Option extends Element{constructor(text,value){super('option');this.textContent=text;this.value=value;}}
const html=fs.readFileSync(path.join(__dirname,'../tools/review_rules.html'),'utf8');
const script=html.match(/<script>\s*([\s\S]*?)<\/script>/)[1];
const citation={source:'docs/DELIVERY.md',line:45,quote:'Fixture quote: <text stays literal>',url:'https://example.test/guide#L45'};
const data={schema_version:1,mode:'rule_review',release_id:'fixture',fingerprint:'test',summary:{findings:2,related_images:2},
 cases:[{id:'missing_image:a',type:'missing_image',target:'a',title:'Thiếu ảnh',images:['a'],facts:{image_file_exists:false},citations:[citation],highlight_box_rows:[],highlight_segment_ids:[]},
 {id:'bbox_out_of_bounds:b',type:'bbox_out_of_bounds',target:'b',title:'Box vượt biên',images:['b'],facts:{},citations:[citation],highlight_box_rows:[1],highlight_segment_ids:[]}],
 assets:{a:{file_name:'a.jpg',width:64,height:64,image_missing:true,image_data:null,label_text:'',metadata:{},job:{},boxes:[],segments:[]},
 b:{file_name:'b.jpg',width:64,height:64,image_missing:false,image_data:'data:image/jpeg;base64,fixture',label_text:'fixture',metadata:{},job:{},boxes:[{row:1,class_name:'truck',bbox_xyxy:[10,10,70,30]}],segments:[]}}};
const store=new Map();
function boot(){
 const elements={};for(const match of html.matchAll(/id="([^"]+)"/g))elements[match[1]]=new Element();
 elements.ruleData.textContent=JSON.stringify(data);elements.zoom.value='100';elements.showBoxes.checked=true;
 const sandbox={document:{getElementById:id=>elements[id],createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag),body:new Element('body')},Option,Blob,
 URL:{createObjectURL:()=> 'blob:fixture',revokeObjectURL:()=>{}},setTimeout:fn=>fn(),window:{addEventListener:()=>{}},
 localStorage:{getItem:key=>store.get(key)||null,setItem:(key,value)=>store.set(key,value)}};
 vm.createContext(sandbox);vm.runInContext(script,sandbox);
 return {elements,run:code=>vm.runInContext(code,sandbox)};
}
const {elements:e,run}=boot();
assert.equal(e.pageTitle.textContent,'Review 2 lỗi luật · Bằng chứng và điều khoản');
assert.equal(e.missing.hidden,false,'Missing image evidence can still be reviewed');
assert.match(e.missing.textContent,/Không có file ảnh/);
assert.equal(e.citations.children[0].children[1].textContent,citation.quote);
assert.throws(()=>run('csvText()'),/Còn 2 mục/);
e.reviewer.value='Nguyễn Hải Đăng';e.reviewer.oninput();
e.reviewed.checked=true;e.reviewed.onchange();e.verdict.value='confirmed';e.verdict.onchange();
assert.match(e.progress.textContent,/1\/2/);assert.equal(e.completion.textContent,'Đã review mục này');
e.statusFilter.value='pending';e.statusFilter.onchange();
assert.equal(e.caseSelect.value,'bbox_out_of_bounds:b');
assert.equal(e.drawing.children.filter(el=>el.tag==='image').length,1);
assert.match(e.drawing.attributes.viewBox,/80/,'Canvas includes the box outside image border');
e.reviewed.checked=true;e.reviewed.onchange();e.verdict.value='rejected';e.verdict.onchange();
assert.match(e.progress.textContent,/2\/2/,'Rejected with optional empty note counts as reviewed');
const csv=run('csvText()');assert.match(csv,/^target,type,verdict,note,reviewer\n/);assert.match(csv,/"rejected","","Nguyễn Hải Đăng"/);
assert.doesNotMatch(csv,/category_swap/,'No extra mask task or row is required');
const snapshot=JSON.parse(run('JSON.stringify(progress())'));
assert.equal(snapshot.mode,'rule_review');assert.equal(snapshot.decisions['bbox_out_of_bounds:b'].note,'');
assert.ok(store.size,'Progress autosaves to its own browser storage key');
const restored=boot();assert.match(restored.elements.progress.textContent,/2\/2/);assert.equal(restored.run('csvText()'),csv);
const incompatible={...snapshot,mode:'semantic'};assert.throws(()=>restored.run(`restore(${JSON.stringify(incompatible)})`),/không phải review luật/);
e.note.value='Fixture ghi chú, "có dấu"\nxuống dòng';e.note.oninput();assert.match(run('csvText()'),/""có dấu""/);
assert.equal(Object.keys(snapshot.decisions).length,2);
console.log('Rule review UI passed: source citations, missing-image audit, bbox overflow, rejected completion without note/mask extras, CSV, isolated progress, local restore.');
