
document.querySelectorAll('[data-pallet-wrapper]').forEach((station)=>{
const canvas=station.querySelector('canvas'),ctx=canvas.getContext('2d'),button=station.querySelector('button');
const motion=matchMedia('(prefers-reduced-motion: reduce)');let paused=motion.matches,angle=0,last=0,raf,recording=false;
const logo=new Image();logo.src=canvas.dataset.logo;logo.onload=()=>draw();
const center={x:-120,y:120};
function project(p){return [500+(p[0]-p[1])*.85,380+(p[0]+p[1])*.35-p[2]]}
function polygon(pts,fill,stroke){ctx.beginPath();pts.map(project).forEach((p,i)=>i?ctx.lineTo(...p):ctx.moveTo(...p));ctx.closePath();ctx.fillStyle=fill;ctx.fill();if(stroke){ctx.strokeStyle=stroke;ctx.lineWidth=.7;ctx.stroke()}}
function disk(cx,cy,r,z,top,edge){let topPts=[],lower=[];for(let i=0;i<=80;i++){const a=i*Math.PI*2/80;topPts.push([cx+r*Math.cos(a),cy+r*Math.sin(a),z]);lower.push([cx+r*Math.cos(a),cy+r*Math.sin(a),z-13])}polygon(lower,edge);for(let i=0;i<80;i++)if(Math.cos((i+.5)*Math.PI*2/80)+Math.sin((i+.5)*Math.PI*2/80)>0)polygon([topPts[i],topPts[i+1],lower[i+1],lower[i]],edge);polygon(topPts,top,'#48667e')}
function local(x,y,z){return [center.x+x*Math.cos(angle)-y*Math.sin(angle),center.y+x*Math.sin(angle)+y*Math.cos(angle),z]}
function facesBox(x,y,z,w,d,h,palette){const v=[[x,y,z],[x+w,y,z],[x+w,y+d,z],[x,y+d,z],[x,y,z+h],[x+w,y,z+h],[x+w,y+d,z+h],[x,y+d,z+h]].map(p=>local(...p));return [[[0,1,5,4],palette[1]],[[1,2,6,5],palette[2]],[[2,3,7,6],palette[1]],[[3,0,4,7],palette[2]],[[4,5,6,7],palette[0]]].map(([ix,c])=>({points:ix.map(i=>v[i]),color:c,depth:ix.reduce((a,i)=>a+v[i][0]+v[i][1],0)/4+z*.001}))}
function box(x,y,z,w,d,h,palette){const faces=facesBox(x,y,z,w,d,h,palette).sort((a,b)=>a.depth-b.depth);for(const f of faces)polygon(f.points,f.color,'#77572f33')}
function fixedBox(x,y,z,w,d,h,palette){
const v=[[x,y,z],[x+w,y,z],[x+w,y+d,z],[x,y+d,z],[x,y,z+h],[x+w,y,z+h],[x+w,y+d,z+h],[x,y+d,z+h]];
polygon([v[1],v[2],v[6],v[5]],palette[2]);polygon([v[2],v[3],v[7],v[6]],palette[1]);polygon([v[4],v[5],v[6],v[7]],palette[0]);
}
function decal(quad){
const p=quad.map(project);ctx.save();ctx.setTransform((p[1][0]-p[0][0])/100,(p[1][1]-p[0][1])/100,(p[3][0]-p[0][0])/100,(p[3][1]-p[0][1])/100,p[0][0],p[0][1]);
ctx.fillStyle='#fbfcfd';ctx.fillRect(0,0,100,100);if(logo.complete&&logo.naturalWidth)ctx.drawImage(logo,5,5,90,90);ctx.restore();
}
function wrapperMast(){
fixedBox(-94,-91,0,76,76,12,['#a6b7c4','#738b9d','#536f84']);
fixedBox(-75,-75,12,42,42,276,['#3c6586','#204b70','#102f4c']);
fixedBox(-80,-79,288,52,50,8,['#62839d','#335b79','#173e60']);
// The original globe is mounted on the stationary column.
decal([[-73,-32,275],[-35,-32,275],[-35,-32,237],[-73,-32,237]]);
polygon([[-69,-31,37],[-61,-31,37],[-61,-31,224],[-69,-31,224]],'#a1b7c8');
polygon([[-57,-31,37],[-51,-31,37],[-51,-31,224],[-57,-31,224]],'#0a243a');
const bottom=57+98*(1-Math.cos(angle))/2;
fixedBox(-112,-48,bottom-5,62,31,48,['#aac0d0','#244f73','#123653']);
// Film roll, carriage rollers and tension arm.
let a=project([-112,-15,bottom]),b=project([-112,-15,bottom+38]);
const g=ctx.createLinearGradient(a[0]-9,0,a[0]+9,0);g.addColorStop(0,'#a9c5d5');g.addColorStop(.45,'#f9fcff');g.addColorStop(1,'#789aae');
ctx.fillStyle=g;ctx.fillRect(a[0]-8,b[1],16,38);ctx.beginPath();ctx.ellipse(b[0],b[1],8,3,0,0,Math.PI*2);ctx.fillStyle='#e9f2f6';ctx.fill();ctx.beginPath();ctx.ellipse(a[0],a[1],8,3,0,0,Math.PI*2);ctx.fillStyle='#8faebe';ctx.fill();
// A continuous clear web runs from the mast to the turning load.
const dx=-112-center.x,dy=-15-center.y;
const lx=dx*Math.cos(angle)+dy*Math.sin(angle),ly=-dx*Math.sin(angle)+dy*Math.cos(angle);
const t=Math.min(77/Math.abs(lx),62/Math.abs(ly));
const low=local(lx*t,ly*t,bottom),high=local(lx*t,ly*t,bottom+38);
polygon([[-120,-15,bottom],low,high,[-120,-15,bottom+38]],'rgba(182,218,238,.35)','rgba(118,163,190,.65)');
polygon([[-120,-15,bottom+8],local(lx*t,ly*t,bottom+8),local(lx*t,ly*t,bottom+10),[-120,-15,bottom+10]],'rgba(255,255,255,.45)');
}
function loadBrandingAndFilm(){
// Each load label is attached to its carton face and turns in perspective.
const sides=[{n:[0,1],p:(u,z)=>local(u,62,z)},{n:[1,0],p:(u,z)=>local(77,-u,z)},{n:[0,-1],p:(u,z)=>local(-u,-62,z)},{n:[-1,0],p:(u,z)=>local(-77,u,z)}];
for(const side of sides){const [nx,ny]=side.n;if((nx*Math.cos(angle)-ny*Math.sin(angle))+(nx*Math.sin(angle)+ny*Math.cos(angle))<=0)continue;
const f=side.p,edge=side.n[0]===0?76:62;decal([f(-28,140),f(28,140),f(28,84),f(-28,84)]);
polygon([f(-edge,54),f(edge,54),f(edge,201),f(-edge,201)],'rgba(224,242,252,.10)');
for(let z=58;z<200;z+=17)polygon([f(-edge,z),f(edge,z+3),f(edge,z+6),f(-edge,z+3)],'rgba(244,252,255,.23)');
polygon([f(53,55),f(59,55),f(59,199),f(53,199)],'rgba(255,255,255,.2)');
}
}
function draw(){ctx.clearRect(0,0,1000,570);if(recording){ctx.fillStyle='#f4f6f7';ctx.fillRect(0,0,1000,570);}
wrapperMast();
// Two low-profile turntables on one industrial base.
ctx.save();ctx.shadowColor='#17365326';ctx.shadowBlur=18;ctx.shadowOffsetY=12;
disk(-120,120,131,0,'#cbd6de','#8da0ae');disk(120,-120,131,0,'#cbd6de','#8da0ae');ctx.restore();
disk(-120,120,124,13,'#173d5e','#0a263f');disk(120,-120,124,13,'#173d5e','#0a263f');
disk(-120,120,110,20,'#2a5476','#183851');disk(120,-120,110,20,'#2a5476','#183851');
// Painted safety marks and deck scoring.
for(const [x,y] of [[-120,120],[120,-120]]){for(let k=0;k<12;k++){const a=k*Math.PI/6,b=a+.07;polygon([[x+113*Math.cos(a),y+113*Math.sin(a),14],[x+123*Math.cos(a),y+123*Math.sin(a),14],[x+123*Math.cos(b),y+123*Math.sin(b),14],[x+113*Math.cos(b),y+113*Math.sin(b),14]],'#d5ad57')}for(let k=-2;k<=2;k++){const half=Math.sqrt(104*104-(k*28)**2);ctx.beginPath();const p=project([x-half,y+k*28,21]),q=project([x+half,y+k*28,21]);ctx.moveTo(...p);ctx.lineTo(...q);ctx.strokeStyle='#50718a';ctx.lineWidth=.8;ctx.stroke()}}
const wood=['#b9915c','#8e6539','#a67b48'];
// Wooden pallet runners, blocks, and deck boards.
for(let y of [-63,-5,53])box(-77,y,24,154,11,7,wood);
for(let x of [-72,-8,56])for(let y of [-60,-5,50])box(x,y,31,18,16,16,wood);
for(let y=-65;y<65;y+=25)box(-80,y,47,160,19,6,['#c09c68','#9c7444','#b08753']);
// Three layers of corrugated cartons, alternating the seam direction.
for(let layer=0;layer<3;layer++){
 let boxes=[];for(let x=0;x<2;x++)for(let y=0;y<2;y++)boxes.push({x:-75+x*76,y:-60+y*61,z:54+layer*49});
 boxes.sort((a,b)=>{const p=local(a.x+38,a.y+30,0),q=local(b.x+38,b.y+30,0);return p[0]+p[1]-q[0]-q[1]});
 for(const b of boxes){box(b.x,b.y,b.z,74,59,47,['#d3b184','#b58b59','#c29a69']);
 // Packing tape on each lid remains attached to the turning cartons.
 const corners=[[b.x+33,b.y,b.z+47.4],[b.x+42,b.y,b.z+47.4],[b.x+42,b.y+59,b.z+47.4],[b.x+33,b.y+59,b.z+47.4]].map(p=>local(...p));polygon(corners,'#dfc398');}
}
loadBrandingAndFilm();
// Label the station decks without attaching text to the spinning load.
ctx.textAlign='center';ctx.font='700 13px Arial';ctx.fillStyle='#284862';ctx.fillText('S3PL',296,470);ctx.font='10px Arial';ctx.fillStyle='#718393';ctx.fillText('TURNTABLE 01',296,490);ctx.fillText('TURNTABLE 02',704,490);
}
function tick(time){if(last&&!paused)angle+=((time-last)/1000)*(Math.PI*2/14);last=time;draw();if(!paused&&!document.hidden)raf=requestAnimationFrame(tick)}
function sync(){cancelAnimationFrame(raf);last=0;button.textContent=paused?'Play animation':'Pause animation';button.setAttribute('aria-pressed',String(paused));draw();if(!paused&&!document.hidden)raf=requestAnimationFrame(tick)}
button.addEventListener('click',()=>{paused=!paused;sync()});motion.addEventListener('change',()=>{paused=motion.matches;sync()});document.addEventListener('visibilitychange',sync);sync();
});
