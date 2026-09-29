/* Measured and regularized surfaces share the same calibrated world coordinates. */
(() => { 'use strict';
const D=window.TUNNEL_DATA,$=id=>document.getElementById(id),canvas=$('scene');
const fail=e=>{$('loading').style.display='grid';$('loading').className='error';$('loading').textContent='加载失败：'+e.message;document.body.dataset.error=e.message;console.error(e);};
try {
if(!D?.cameras.length)throw Error('场景数据不存在');
const gl=canvas.getContext('webgl',{antialias:true,preserveDrawingBuffer:true});
if(!gl)throw Error('浏览器未提供 WebGL');
const derivatives=gl.getExtension('OES_standard_derivatives');
const compile=(type,source)=>{const s=gl.createShader(type);gl.shaderSource(s,source);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(s));return s;};
const program=(vs,fs)=>{const p=gl.createProgram();gl.attachShader(p,compile(gl.VERTEX_SHADER,vs));gl.attachShader(p,compile(gl.FRAGMENT_SHADER,fs));gl.linkProgram(p);if(!gl.getProgramParameter(p,gl.LINK_STATUS))throw Error(gl.getProgramInfoLog(p));return p;};
const meshP=program(`attribute vec3 pos;attribute vec2 uv;attribute vec3 normal;attribute float support;
uniform mat4 vp;varying vec2 tex;varying vec3 world;varying vec3 vertexNormal;varying float observed;
void main(){world=pos;tex=uv;vertexNormal=normal;observed=support;gl_Position=vp*vec4(pos,1.);}`,
(derivatives?'#extension GL_OES_standard_derivatives : enable\n':'')+`precision highp float;
varying vec2 tex;varying vec3 world;varying vec3 vertexNormal;varying float observed;
uniform sampler2D image;uniform bool textured;uniform bool smoothNormals;uniform bool showInferred;uniform bool cap;
void main(){vec3 n=vec3(0.,1.,0.);`+(derivatives?'n=normalize(cross(dFdx(world),dFdy(world)));':'')+`
if(smoothNormals)n=normalize(vertexNormal);float light=.4+.6*abs(dot(n,normalize(vec3(.3,.8,.5))));
vec3 c=texture2D(image,tex).rgb;if(!textured||dot(tex,tex)<.0000001)c=vec3(.57,.67,.75)*light;
if(showInferred&&observed<.65)c=mix(c,vec3(.95,.57,.18),.55);
if(cap)c=vec3(.4,.5,.59)*light;gl_FragColor=vec4(c,1.);}`);
const pointP=program('attribute vec3 pos;uniform mat4 vp;uniform float size;void main(){gl_Position=vp*vec4(pos,1.);gl_PointSize=size;}',
 'precision mediump float;uniform vec4 color;uniform bool roundPoint;void main(){if(roundPoint&&distance(gl_PointCoord,vec2(.5))>.5)discard;gl_FragColor=color;}');
const unpack=s=>{const raw=atob(s),b=new Uint8Array(raw.length);for(let i=0;i<raw.length;i++)b[i]=raw.charCodeAt(i);return new Float32Array(b.buffer);};
const dot=(a,b)=>a[0]*b[0]+a[1]*b[1]+a[2]*b[2],sub=(a,b)=>a.map((x,i)=>x-b[i]),add=(a,b)=>a.map((x,i)=>x+b[i]),mul=(v,s)=>v.map(x=>x*s);
const norm=v=>{const n=Math.hypot(...v);if(n<1e-10)throw Error('无效视角');return mul(v,1/n);};
const cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
const R=D.cameras[0].world_to_camera,axes=[R[0].slice(0,3),R[1].slice(0,3).map(x=>-x),R[2].slice(0,3).map(x=>-x)],origin=(D.regularized?.bounds||D.bounds).center;
const local=v=>axes.map(a=>dot(a,sub(v,origin))),dirLocal=v=>axes.map(a=>dot(a,v));
const transform=(array,direction=false)=>{for(let i=0;i<array.length;i+=3)array.set((direction?dirLocal:local)(Array.from(array.subarray(i,i+3))),i);return array;};
const buffer=data=>{const b=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,b);gl.bufferData(gl.ARRAY_BUFFER,data,gl.STATIC_DRAW);return b;};
function prepare(mesh){const positions=transform(unpack(mesh.positions));return {pos:buffer(positions),uv:buffer(unpack(mesh.uv)),count:positions.length/3,
 normals:mesh.normals?buffer(transform(unpack(mesh.normals),true)):null,support:mesh.support?buffer(unpack(mesh.support)):null};}
const assets={raw:prepare(D.mesh)};
if(D.regularized){assets.regularized=prepare(D.regularized.mesh);const cp=unpack(D.regularized.mesh.caps);assets.caps={pos:buffer(transform(cp)),uv:buffer(new Float32Array(cp.length/3*2)),count:cp.length/3};}
const trajectory=D.cameras.map(c=>local(c.center)),pathB=buffer(new Float32Array(trajectory.flat()));
const markerB=buffer(new Float32Array()),activeB=buffer(new Float32Array()),cameraB=buffer(new Float32Array(trajectory[0]));
let surfaceMode=D.regularized?'regularized':'raw',frame=0,selected=null,mode=D.regularized?'follow':'orbit';
let yaw=.65,pitch=.35,dist=(D.regularized?.bounds||D.bounds).radius*2.7,target=[0,0,0],vp=null,markerCount=0,activeCount=0,currentCandidates=[];
const textures={},loaded=new Set();
for(const [key,url] of [['raw',D.texture_url],...(D.regularized?[['regularized',D.regularized.texture_url]]:[])]){
 const t=gl.createTexture();textures[key]=t;gl.bindTexture(gl.TEXTURE_2D,t);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,1,1,0,gl.RGBA,gl.UNSIGNED_BYTE,new Uint8Array([140,155,170,255]));
 for(const [k,v] of [[gl.TEXTURE_MIN_FILTER,gl.LINEAR],[gl.TEXTURE_MAG_FILTER,gl.LINEAR],[gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE],[gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE]])gl.texParameteri(gl.TEXTURE_2D,k,v);
 const image=new Image();image.onload=()=>{try{gl.bindTexture(gl.TEXTURE_2D,t);gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL,true);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,gl.RGBA,gl.UNSIGNED_BYTE,image);loaded.add(key);if(loaded.size===Object.keys(textures).length)document.body.dataset.texture='loaded';draw();}catch(e){fail(e);}};image.onerror=()=>fail(Error('纹理读取失败'));image.src=url;
}
function multiply(a,b){const r=new Float32Array(16);for(let col=0;col<4;col++)for(let row=0;row<4;row++)for(let k=0;k<4;k++)r[col*4+row]+=a[k*4+row]*b[col*4+k];return r;}
function perspective(fov,aspect,near,far){const f=1/Math.tan(fov/2),r=new Float32Array(16);r[0]=f/aspect;r[5]=f;r[10]=(far+near)/(near-far);r[11]=-1;r[14]=2*far*near/(near-far);return r;}
function lookAt(eye,to,up){const z=norm(sub(eye,to)),x=norm(cross(up,z)),y=cross(z,x);return new Float32Array([x[0],y[0],z[0],0,x[1],y[1],z[1],0,x[2],y[2],z[2],0,-dot(x,eye),-dot(y,eye),-dot(z,eye),1]);}
const locations=new Map(),enabled=new Set();
function location(p,name,attribute=false){const key=(p===meshP?'m':'p')+name;if(!locations.has(key))locations.set(key,attribute?gl.getAttribLocation(p,name):gl.getUniformLocation(p,name));return locations.get(key);}
function attr(p,name,b,size){const l=location(p,name,true);if(l<0)return;gl.bindBuffer(gl.ARRAY_BUFFER,b);gl.enableVertexAttribArray(l);gl.vertexAttribPointer(l,size,gl.FLOAT,false,0,0);enabled.add(l);}
function clearAttrs(){for(const l of enabled)gl.disableVertexAttribArray(l);enabled.clear();}
function updateBuffer(b,data){gl.bindBuffer(gl.ARRAY_BUFFER,b);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(data),gl.STATIC_DRAW);}
function renderMesh(asset,cap=false){clearAttrs();gl.useProgram(meshP);attr(meshP,'pos',asset.pos,3);attr(meshP,'uv',asset.uv,2);
 if(asset.normals)attr(meshP,'normal',asset.normals,3);else gl.vertexAttrib3f(location(meshP,'normal',true),0,1,0);
 if(asset.support)attr(meshP,'support',asset.support,1);else gl.vertexAttrib1f(location(meshP,'support',true),1);
 gl.uniformMatrix4fv(location(meshP,'vp'),false,vp);gl.uniform1i(location(meshP,'textured'),$('showTexture').checked&&!cap);
 gl.uniform1i(location(meshP,'smoothNormals'),!!asset.normals);gl.uniform1i(location(meshP,'showInferred'),surfaceMode==='regularized'&&$('showInferred').checked&&!cap);gl.uniform1i(location(meshP,'cap'),cap);
 gl.activeTexture(gl.TEXTURE0);gl.bindTexture(gl.TEXTURE_2D,textures[surfaceMode]);gl.uniform1i(location(meshP,'image'),0);gl.enable(gl.POLYGON_OFFSET_FILL);gl.polygonOffset(1,1);gl.drawArrays(gl.TRIANGLES,0,asset.count);gl.disable(gl.POLYGON_OFFSET_FILL);}
function renderPoints(b,count,color,size,primitive=gl.POINTS,always=false){if(!count)return;clearAttrs();gl.useProgram(pointP);attr(pointP,'pos',b,3);gl.uniformMatrix4fv(location(pointP,'vp'),false,vp);gl.uniform4fv(location(pointP,'color'),color);gl.uniform1f(location(pointP,'size'),size*Math.min(devicePixelRatio||1,2));gl.uniform1i(location(pointP,'roundPoint'),primitive===gl.POINTS);if(always)gl.disable(gl.DEPTH_TEST);gl.drawArrays(primitive,0,count);gl.enable(gl.DEPTH_TEST);}
function draw(){const rect=canvas.getBoundingClientRect(),dpr=Math.min(devicePixelRatio||1,2),w=Math.max(1,Math.round(rect.width*dpr)),h=Math.max(1,Math.round(rect.height*dpr));if(canvas.width!==w||canvas.height!==h){canvas.width=w;canvas.height=h;}gl.viewport(0,0,w,h);gl.enable(gl.DEPTH_TEST);gl.depthFunc(gl.LEQUAL);gl.disable(gl.CULL_FACE);gl.clearColor(.055,.086,.13,1);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);
 let eye,up=[0,1,0],look=target,fov=.85;
 if(mode==='follow'){const c=D.cameras[frame];eye=trajectory[frame];look=add(eye,dirLocal(c.world_to_camera[2].slice(0,3)));up=dirLocal(c.world_to_camera[1].slice(0,3).map(x=>-x));const fy=['PINHOLE','OPENCV'].includes(c.model)?c.params[1]:c.params[0];fov=2*Math.atan(c.height/(2*fy));}
 else eye=add(target,[dist*Math.cos(pitch)*Math.sin(yaw),dist*Math.sin(pitch),dist*Math.cos(pitch)*Math.cos(yaw)]);
 vp=multiply(perspective(fov,w/h,D.bounds.radius*.0005,D.bounds.radius*200),lookAt(eye,look,up));renderMesh(assets[surfaceMode]);if(surfaceMode==='regularized'&&$('showCaps').checked)renderMesh(assets.caps,true);
 if($('showPath').checked){renderPoints(pathB,trajectory.length,[.27,.78,.91,1],2,gl.LINE_STRIP);renderPoints(cameraB,1,[.44,.94,1,1],9,gl.POINTS,true);}
 if($('showMarkers').checked){renderPoints(markerB,markerCount,[1,.23,.18,1],4);renderPoints(activeB,activeCount,[1,.8,.18,1],7);}
 const err=gl.getError();if(err!==gl.NO_ERROR)throw Error('WebGL 绘制错误 '+err);document.body.dataset.rendered='true';document.body.dataset.glError=String(err);}
function resetSelection(){selected=null;$('selection').textContent='从列表或三维红色点簇中选择候选。';}
function changeFrame(index,seekVideo=false){frame=Math.max(0,Math.min(D.cameras.length-1,index));$('time').value=frame;const c=D.cameras[frame];$('timeLabel').textContent=c.time_s.toFixed(2)+' s · '+(frame+1)+'/'+D.cameras.length;$('frameLabel').textContent=c.image_name+' · 原图 '+c.width+' × '+c.height;$('frameImage').src=c.image_url;
 $('maskImage').src=c.mask_url||'';$('maskImage').style.display=$('showMask').checked&&c.mask_url?'block':'none';updateBuffer(cameraB,trajectory[frame]);if(selected&&selected.image_name!==c.image_name)resetSelection();updateCandidates();if(seekVideo&&Math.abs($('video').currentTime-c.time_s)>.05)$('video').currentTime=c.time_s;draw();}
function updateCandidates(){const threshold=+$('confidence').value;$('confidenceLabel').textContent=threshold.toFixed(2);
 currentCandidates=D.defects.filter(d=>d.image_name===D.cameras[frame].image_name&&d.confidence>=threshold).map(d=>surfaceMode==='regularized'?{...d,...d.fitted}:d);
 $('candidateCount').textContent=currentCandidates.length+' 个观测';const list=$('candidateList');list.replaceChildren();
 for(const d of currentCandidates){const b=document.createElement('button');b.className='candidate'+(selected?.id===d.id?' selected':'');const title=document.createElement('span');title.textContent='候选 '+d.id.split('-d').pop();const score=document.createElement('span');score.className='score';score.textContent=d.confidence.toFixed(3);const info=document.createElement('span');info.className='desc';info.textContent=d.points.length?(surfaceMode==='regularized'?'通过关联检查 ':'表面命中 ')+d.hit_count+'/'+d.requested_count+' 个采样像素':(surfaceMode==='regularized'?'本拟合区段内没有通过检查的位置':'没有可用的三维位置');b.append(title,score,info);b.onclick=()=>select(d);list.append(b);}
 if(!currentCandidates.length){const t=document.createElement('div');t.className='empty';t.textContent='当前阈值下没有候选。';list.append(t);}
 let coords=currentCandidates.flatMap(d=>d.points.flatMap(local));markerCount=coords.length/3;updateBuffer(markerB,coords);coords=selected?selected.points.flatMap(local):[];activeCount=coords.length/3;updateBuffer(activeB,coords);drawEvidence();}
function select(d){selected=d;const p=d.center,text=['观测编号：'+d.id,'模型分数：'+d.confidence.toFixed(3)+'（不代表准确率）','几何命中：'+d.hit_count+'/'+d.requested_count,
 '状态：'+(p?(surfaceMode==='regularized'?'已关联到规则化表面，病害待复核':'已落在原始表面，病害待复核'):'未通过当前表面的定位检查'),
 p?'三维中心：['+p.map(x=>x.toFixed(4)).join(', ')+'] '+D.scale.unit:'',
 surfaceMode==='regularized'&&p?'与原位置的中位位移：'+d.displacement_median.toFixed(4)+' '+D.scale.unit:'',
 surfaceMode==='regularized'&&p?'落在局部补全区的采样点：'+d.inferred_hit_count:''];
 $('selection').replaceChildren(...text.filter(Boolean).flatMap(t=>{const span=document.createElement('span');span.textContent=t;return[span,document.createElement('br')];}));updateCandidates();draw();}
function drawEvidence(){const cv=$('evidenceCanvas'),box=$('evidenceBox'),c=D.cameras[frame],w=box.clientWidth,h=box.clientHeight;cv.width=w;cv.height=h;const ctx=cv.getContext('2d');ctx.clearRect(0,0,w,h);if(!selected)return;const s=Math.min(w/c.width,h/c.height),ox=(w-c.width*s)/2,oy=(h-c.height*s)/2;ctx.fillStyle='#ffe058';for(const p of selected.pixels){ctx.beginPath();ctx.arc(ox+p[0]*s,oy+p[1]*s,2.1,0,2*Math.PI);ctx.fill();}}
function refreshLabels(){const fitted=surfaceMode==='regularized';$('regularized').classList.toggle('active',fitted);$('raw').classList.toggle('active',!fitted);$('showCaps').disabled=!fitted;$('showInferred').disabled=!fitted;
 $('overview').classList.toggle('active',mode==='orbit');$('follow').classList.toggle('active',mode==='follow');$('modeName').textContent=(fitted?'规则化连续表面':'原始多视图重建')+(mode==='follow'?' · 同帧相机视角':'');
 $('surfaceNote').textContent=fitted?($('showInferred').checked?'橙色：局部观测不足的补全区域':'断面来自点云拟合；两端为建模区段边界'):'保留原始孔洞、离群面和洞外场景';
 const count=assets[surfaceMode].count/3+(fitted&&$('showCaps').checked?assets.caps.count/3:0);
 const topology=D.regularized?.report[fitted?($('showCaps').checked?'closed_topology':'regularized_topology'):'raw_topology'];
 const entries=[['注册帧',D.cameras.length],['三角面',count.toLocaleString()],topology?['边界边',topology.boundary_edges.toLocaleString()]:['重投影误差',D.quality.point_mean_reprojection_error_px.toFixed(3)+' px']];
 $('stats').replaceChildren(...entries.map(([l,v])=>{const div=document.createElement('div'),b=document.createElement('b');b.textContent=v;div.append(b,document.createTextNode(l));return div;}));}
function setSurface(value){if(!assets[value])return;surfaceMode=value;resetSelection();updateCandidates();refreshLabels();draw();}
function setMode(value){mode=value;refreshLabels();draw();}
$('sourceTitle').textContent=D.title;$('scaleTag').textContent=D.scale.status==='metric'?'已锚定尺度':'相对尺度 · 尚未标定';$('limits').textContent=D.limits.join(' ');
const a=document.createElement('a');a.href=D.source.url;a.target='_blank';a.rel='noreferrer';a.textContent='原始录像来源';$('attribution').append(D.source.author+' · '+D.source.license+' · ',a);$('video').src=D.video_url;$('time').max=D.cameras.length-1;
if(!D.regularized)$('surfaceControls').style.display='none';$('regularized').onclick=()=>setSurface('regularized');$('raw').onclick=()=>setSurface('raw');
$('time').oninput=e=>changeFrame(+e.target.value,true);$('prev').onclick=()=>changeFrame(frame-1,true);$('next').onclick=()=>changeFrame(frame+1,true);
$('confidence').oninput=()=>{if(selected&&selected.confidence<+$('confidence').value)resetSelection();updateCandidates();draw();};$('showMask').onchange=()=>{$('maskImage').style.display=$('showMask').checked&&D.cameras[frame].mask_url?'block':'none';};
for(const id of ['showMarkers','showPath','showTexture','showInferred','showCaps'])$(id).onchange=()=>{refreshLabels();draw();};
$('overview').onclick=()=>{const bounds=surfaceMode==='regularized'?D.regularized.bounds:D.bounds;target=local(bounds.center);dist=bounds.radius*2.7;setMode('orbit');};$('follow').onclick=()=>setMode('follow');
$('focus').onclick=()=>{if(!selected?.center)return;target=local(selected.center);dist=(D.regularized?.bounds||D.bounds).radius*.3;setMode('orbit');};
$('video').ontimeupdate=()=>{const t=$('video').currentTime;let best=0;for(let i=1;i<D.cameras.length;i++)if(Math.abs(D.cameras[i].time_s-t)<Math.abs(D.cameras[best].time_s-t))best=i;if(best!==frame)changeFrame(best);};
let drag=null,moved=0;canvas.oncontextmenu=e=>e.preventDefault();canvas.onpointerdown=e=>{drag={x:e.clientX,y:e.clientY,button:e.button};moved=0;canvas.setPointerCapture(e.pointerId);};
canvas.onpointermove=e=>{if(!drag)return;const dx=e.clientX-drag.x,dy=e.clientY-drag.y;moved+=Math.abs(dx)+Math.abs(dy);if(mode==='follow')setMode('orbit');if(drag.button===2){const s=dist*.0015;target[0]-=dx*s;target[1]+=dy*s;}else{yaw-=dx*.006;pitch=Math.max(-1.5,Math.min(1.5,pitch+dy*.006));}drag.x=e.clientX;drag.y=e.clientY;draw();};
canvas.onpointerup=e=>{drag=null;if(moved<5){const rect=canvas.getBoundingClientRect(),x=e.clientX-rect.left,y=e.clientY-rect.top;let found=null,best=18;for(const d of currentCandidates){if(!d.center)continue;const p=[...local(d.center),1],q=[0,0,0,0];for(let row=0;row<4;row++)for(let col=0;col<4;col++)q[row]+=vp[col*4+row]*p[col];if(q[3]<=0)continue;const distance=Math.hypot((q[0]/q[3]+1)*rect.width/2-x,(1-q[1]/q[3])*rect.height/2-y);if(distance<best){best=distance;found=d;}}if(found)select(found);}};
canvas.onwheel=e=>{e.preventDefault();if(mode==='follow')setMode('orbit');dist=Math.max(D.bounds.radius*.02,Math.min(D.bounds.radius*50,dist*Math.exp(e.deltaY*.001)));draw();};
window.addEventListener('resize',()=>{draw();drawEvidence();});$('frameImage').onload=drawEvidence;
window.tunnelViewer={setFrame:i=>changeFrame(i),setMode,setSurface,selectCandidate:index=>{if(currentCandidates[index])select(currentCandidates[index]);},stats:()=>({frame,surface:surfaceMode,triangles:assets[surfaceMode].count/3,currentCandidates:currentCandidates.length,selected:selected?.id||null,markerCount,glError:gl.getError()})};
changeFrame(Math.floor(D.cameras.length*(D.regularized ? .5 : .3)));refreshLabels();$('loading').style.display='none';document.body.dataset.ready='true';
}catch(e){fail(e);}
})();
