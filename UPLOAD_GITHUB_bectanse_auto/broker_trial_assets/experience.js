(() => {
 const stage=document.querySelector('.experience-visual');
 if(!stage||matchMedia('(prefers-reduced-motion: reduce)').matches||!matchMedia('(pointer: fine)').matches)return;
 let frame;
 stage.addEventListener('pointermove',event=>{cancelAnimationFrame(frame);frame=requestAnimationFrame(()=>{const r=stage.getBoundingClientRect();stage.style.setProperty('--tilt-x',`${((event.clientY-r.top)/r.height-.5)*-7}deg`);stage.style.setProperty('--tilt-y',`${((event.clientX-r.left)/r.width-.5)*9}deg`)});});
 stage.addEventListener('pointerleave',()=>{cancelAnimationFrame(frame);stage.style.setProperty('--tilt-x','0deg');stage.style.setProperty('--tilt-y','0deg')});
})();
