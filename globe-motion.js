(()=>{
const globes=[...document.querySelectorAll('.line-globe')];if(!globes.length)return;
const reduced=matchMedia('(prefers-reduced-motion: reduce)');let frame=0,start=0;
function draw(time){if(!start)start=time;const angle=((time-start)%8000)/8000*Math.PI*2;
for(const globe of globes){const paths=globe.querySelectorAll('.rotating-meridian');for(let n=0;n<paths.length;n++){const longitude=((angle+n*Math.PI/3+Math.PI/2)%Math.PI)-Math.PI/2;let d='';for(let i=0;i<=36;i++){const lat=-Math.PI/2+Math.PI*i/36;const x=630+438*Math.cos(lat)*Math.sin(longitude),y=610+438*Math.sin(lat);d+=(i?' L':'M')+x.toFixed(1)+','+y.toFixed(1)}paths[n].setAttribute('d',d)}}
if(!reduced.matches&&!document.hidden)frame=requestAnimationFrame(draw)}
function sync(){cancelAnimationFrame(frame);if(!reduced.matches&&!document.hidden)frame=requestAnimationFrame(draw)}
reduced.addEventListener('change',sync);document.addEventListener('visibilitychange',sync);sync();
})();