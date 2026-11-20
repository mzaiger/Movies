/* ts-embed.js: put this file in the ROOT of the marcitech.is-a.dev base repo, then add one line to each site:
     <script src="/ts-embed.js"></script>
   It does nothing unless the page is opened inside Trendslope. Inside Trendslope it tells the parent how tall the page is,
   so Trendslope can size the frame to the content and let the *top-level page* scroll (that is what makes Safari's bars collapse
   and lets the page run underneath them). Sites without this line keep working with the fixed full-screen frame. */
(()=>{
if(window.parent===window)return;
const ALLOWED=['https://www.trendslope.com','https://trendslope.com','https://marcitech.is-a.dev'];
let po='';try{po=(location.ancestorOrigins&&location.ancestorOrigins[0])||new URL(document.referrer).origin}catch{}
if(!ALLOWED.includes(po))return;
const de=document.documentElement,post=m=>{try{parent.postMessage(Object.assign({tsEmbed:1},m),po)}catch{}};
de.classList.add('ts-embedded');
post({type:'hello'});

// how tall is the content? (body box, so a taller frame can't keep itself tall)
let last=0,raf=0;
const measure=()=>{const b=document.body;if(!b)return 0;const r=b.getBoundingClientRect(),cs=getComputedStyle(b);
 return Math.ceil(r.bottom+(window.scrollY||0)+(parseFloat(cs.marginBottom)||0))};
const report=()=>{raf=0;const h=measure();if(h>0&&Math.abs(h-last)>=1){last=h;post({type:'height',h})}};
const sched=()=>{if(!raf)raf=requestAnimationFrame(report)};
const start=()=>{
 sched();
 try{const ro=new ResizeObserver(sched);ro.observe(de);ro.observe(document.body)}catch{}
 try{new MutationObserver(sched).observe(document.body,{childList:true,subtree:true,attributes:true})}catch{}
 addEventListener('load',sched);addEventListener('resize',sched);
 if(document.fonts&&document.fonts.ready)document.fonts.ready.then(sched);
 setTimeout(sched,300);setTimeout(sched,1500);
};
document.readyState==='loading'?addEventListener('DOMContentLoaded',start):start();

// where the screen is inside this tall frame. For anything that is position:fixed, use these:
//   --ts-vt = px from the top of the page to the top of the visible screen, --ts-vh = visible height in px
addEventListener('message',e=>{
 if(e.origin!==po||e.source!==parent)return;const m=e.data;if(!m||m.tsEmbed!==1)return;
 if(m.type==='vp'){de.style.setProperty('--ts-vt',(m.top||0)+'px');de.style.setProperty('--ts-vh',(m.height||0)+'px')}
});
})();
