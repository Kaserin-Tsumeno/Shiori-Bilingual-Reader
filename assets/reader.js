
const state = { index:null, work:null, layout:localStorage.getItem("reader.layout") || "side", currentChapter:null, tab:'chapters', fontScale:Number(localStorage.getItem('reader.fontScale')||0), theme:null };
const reader=document.getElementById("reader"), statusEl=document.getElementById("status"), workSelect=document.getElementById("workSelect"), chapterSelect=document.getElementById("chapterSelect"), rubyToggle=document.getElementById("rubyToggle"), searchBox=document.getElementById("searchBox"), chapterList=document.getElementById("chapterList"), bookmarkList=document.getElementById("bookmarkList"), sideSearch=document.getElementById("sideSearch"), sidebar=document.getElementById("sidebar"), overlay=document.getElementById("overlay"), darkToggle=document.getElementById('darkToggle'), fontMinus=document.getElementById('fontMinus'), fontPlus=document.getElementById('fontPlus'), bmCount=document.getElementById('bmCount');
const moreBtn=document.getElementById('moreBtn'), moreMenu=document.getElementById('moreMenu'), importFile=document.getElementById('importFile'), helpLayer=document.getElementById('helpLayer'), progressTrack=document.getElementById('progressTrack'), progressFill=document.getElementById('progressFill');

/* 长篇（数万段）必须分批渲染：一次插入全部段落会让浏览器卡死。
   按「章」渲染 + 滚动窗口回收，使 DOM 规模有界。 */
const CHUNK = 3;             // 每次追加的章节数
const MAX_WINDOW = 40;       // 窗口内保留的最大章节数
const PREFETCH = 900;        // 距底/顶多少像素触发追加
const FORCE_STACK_WIDTH=720; // 窄于此宽度左右对照不可读，自动退化为上下
let winStart=0, winEnd=0;
let byChapter=null, chapterStart=null, chapterPos=null, paraById=null;
let ticking=false, jumping=false, helpOpen=false;
let bookmarks = new Map();

let _idxCache, _worksCache;
function embeddedJson(id){ const el=document.getElementById(id); if(!el) return null; try{ return JSON.parse(el.textContent); }catch(e){ return null; } }
function embeddedIndex(){ if(_idxCache===undefined) _idxCache=embeddedJson('reader-index'); return _idxCache; }
function embeddedWorks(){ if(_worksCache===undefined) _worksCache=embeddedJson('reader-works'); return _worksCache; }
/* 本副本的主打作品：文件名指向的那部。打开哪个文件就先显示哪部作品。 */
function primaryWorkId(){ const v=embeddedJson('reader-primary'); return (typeof v==='string' && v) ? v : null; }
/* 各副本独立记住自己上次读到哪部作品，避免"A 页面打开却显示 B 作品"。 */
function sessionKey(){ const p=primaryWorkId(); return p ? 'reader.work.'+p : 'reader.work'; }
/* 数据优先取内嵌：单文件阅读器在 file:// 下 fetch 本地 JSON 会被 CORS 拦。
   若内嵌未命中且 fetch 也失败，抛可识别的 work-unavailable，由调用方给出人话提示。 */
async function loadJson(path){
  if(path==='works/index.json'){ const d=embeddedIndex(); if(d) return d; }
  const works=embeddedWorks();
  if(works){ const hit=Object.values(works).find(w=>'works/'+w.work_id+'.json'===path); if(hit) return hit; }
  try{
    const res=await fetch(path);
    if(!res.ok) throw new Error(res.status);
    return await res.json();
  }catch(e){
    const err=new Error('该作品未打包在本文件中');
    err.code='work-unavailable';
    err.path=path;
    throw err;
  }
}
function escapeHtml(s){return String(s).replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch])).replace(/\n/g,"<br>")}
function showError(msg){ if(statusEl){ statusEl.textContent=msg; statusEl.classList.add('show'); clearTimeout(window.__statusTimer); window.__statusTimer=setTimeout(()=>statusEl.classList.remove('show'),6000); } }
function byId(id){ return document.getElementById(id); }
function on(el,ev,fn){ if(el) el.addEventListener(ev,fn); }

/* ---------- 主题：auto / light / dark 三态，默认跟随系统 ---------- */
const darkMQ = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
(function initTheme(){
  const saved = localStorage.getItem('reader.theme');
  if(saved==='auto'||saved==='light'||saved==='dark'){ state.theme=saved; return; }
  const legacy = localStorage.getItem('reader.dark');     // 兼容旧的布尔存储
  state.theme = legacy==='1' ? 'dark' : (legacy==='0' ? 'light' : 'auto');
})();
function isDark(){ return state.theme==='dark' || (state.theme==='auto' && !!(darkMQ && darkMQ.matches)); }
function cycleTheme(){ state.theme = state.theme==='auto' ? 'light' : (state.theme==='light' ? 'dark' : 'auto'); applyPrefs(); }
function applyPrefs(){
  document.body.classList.toggle('dark', isDark());
  document.documentElement.style.setProperty('--reader-font', `${17 + state.fontScale}px`);
  if(darkToggle){
    darkToggle.textContent = state.theme==='auto' ? '自动' : (state.theme==='dark' ? '暗' : '亮');
    darkToggle.title = state.theme==='auto' ? '跟随系统（点击切换为常亮）' : (state.theme==='dark' ? '始终深色（点击切回自动）' : '始终浅色（点击切换为深色）');
  }
  localStorage.setItem('reader.fontScale',String(state.fontScale));
  localStorage.setItem('reader.theme',state.theme);
}
if(darkMQ && darkMQ.addEventListener) darkMQ.addEventListener('change', ()=>{ if(state.theme==='auto') applyPrefs(); });

/* ---------- 布局 ---------- */
function effectiveLayout(){ return (state.layout==='side' && window.innerWidth<=FORCE_STACK_WIDTH) ? 'stack' : state.layout; }
function applyLayout(){
  const eff=effectiveLayout(), narrow=window.innerWidth<=FORCE_STACK_WIDTH;
  reader.className=`reader layout-${eff}`;
  document.querySelectorAll('[data-layout]').forEach(b=>{
    b.classList.toggle('active', b.dataset.layout===eff);
    if(b.dataset.layout==='side'){ b.disabled=narrow; b.title=narrow?'屏幕过窄，已自动使用竖排':'左右对照'; }
  });
}
function setLayout(layout){ state.layout=layout; localStorage.setItem("reader.layout",layout); applyLayout(); }

/* ---------- 索引 ---------- */
function prepareWork(){
  byChapter=new Map(); chapterStart=new Set(); chapterPos=new Map(); paraById=new Map();
  state.work.chapters.forEach((c,i)=>{ byChapter.set(c.chapter_id,[]); chapterPos.set(c.chapter_id,i); chapterStart.add(c.start_paragraph_id); });
  for(const p of state.work.paragraphs){ const arr=byChapter.get(p.chapter_id); if(arr) arr.push(p); paraById.set(p.id,p); }
}

/* ---------- 阅读进度：段落锚点（比 scrollY 可靠，重排/重译都不失效） ---------- */
function progressKey(){ return `reader.progress.${state.work.work_id}`; }
function currentAnchor(){
  const paras=reader.querySelectorAll('.para');
  if(!paras.length) return null;
  for(const p of paras){ if(p.getBoundingClientRect().bottom>80) return p; }
  return paras[paras.length-1];
}
function readProgress(){ try{ const raw=localStorage.getItem(progressKey()); if(!raw) return null; const o=JSON.parse(raw); return (o && o.para_id)?o:null; }catch(e){ return null; } }
function saveProgress(){
  if(!state.work || jumping) return;
  const a=currentAnchor(); if(!a) return;
  try{ localStorage.setItem(progressKey(), JSON.stringify({para_id:a.id, chapter_id:a.dataset.chapter, t:Date.now()})); }catch(e){}
}

/* ---------- 书签 ---------- */
function bookmarksKey(){ return `reader.bookmarks.${state.work.work_id}`; }
function loadBookmarks(){
  bookmarks=new Map();
  try{ const raw=localStorage.getItem(bookmarksKey()); if(raw) for(const b of JSON.parse(raw)) if(b && b.para_id) bookmarks.set(b.para_id,b); }catch(e){}
}
function saveBookmarks(){ try{ localStorage.setItem(bookmarksKey(), JSON.stringify([...bookmarks.values()])); }catch(e){ showError('书签保存失败：浏览器存储不可用或已满'); } }
function sortedBookmarks(){ return [...bookmarks.values()].sort((a,b)=>a.para_id<b.para_id?-1:(a.para_id>b.para_id?1:0)); }
function toggleBookmark(pid){
  if(bookmarks.has(pid)) bookmarks.delete(pid);
  else {
    const p=paraById.get(pid); if(!p) return;
    const ci=chapterPos.get(p.chapter_id); const c=ci===undefined?null:state.work.chapters[ci];
    bookmarks.set(pid,{ para_id:pid, chapter_id:p.chapter_id,
      chapter_zh:(c&&c.title_zh)||'', chapter_ja:(c&&c.title_ja)||'',
      ja:(p.ja||'').slice(0,60), zh:(p.zh||'').slice(0,60), t:Date.now() });
  }
  saveBookmarks();
  const btn=reader.querySelector(`.bm-btn[data-bm="${pid}"]`);
  if(btn){ const on_=bookmarks.has(pid); btn.classList.toggle('on',on_); btn.textContent=on_?'★':'☆'; }
  renderBookmarks();
}
function renderBookmarks(){
  if(!bookmarkList) return;
  const q=(sideSearch?sideSearch.value:'').trim();
  const items=sortedBookmarks().filter(b=>!q || (b.zh||'').includes(q) || (b.ja||'').includes(q) || (b.chapter_zh||'').includes(q) || (b.chapter_ja||'').includes(q));
  if(bmCount) bmCount.textContent=String(bookmarks.size);
  if(!items.length){ bookmarkList.innerHTML=`<div class="bm-empty">${bookmarks.size?'没有匹配的书签':'还没有书签。<br>鼠标移到段落上点右上角的 ☆，或直接按 <kbd>b</kbd>。'}</div>`; return; }
  bookmarkList.innerHTML=items.map(b=>`<div class="bm-item"><button class="bm-jump" data-para="${b.para_id}"><div class="bm-line">${escapeHtml(b.chapter_zh||b.chapter_ja||b.chapter_id)}</div><div class="bm-line ja">${escapeHtml(b.ja||'')}</div><div class="bm-line">${escapeHtml(b.zh||'')}</div></button><button class="bm-remove" data-para="${b.para_id}" title="移除书签">×</button></div>`).join('');
}
function setTab(name){
  state.tab=name;
  document.querySelectorAll('.side-tabs button').forEach(b=>b.classList.toggle('active', b.dataset.tab===name));
  if(chapterList) chapterList.hidden = name!=='chapters';
  if(bookmarkList) bookmarkList.hidden = name!=='bookmarks';
  if(sideSearch) sideSearch.placeholder = name==='bookmarks' ? '筛选书签' : '筛选章节';
  if(name==='bookmarks') renderBookmarks(); else renderChapters(sideSearch?sideSearch.value:'');
}

/* ---------- 渲染 ---------- */
function paraHtml(p){
  const isChapter=chapterStart.has(p.id), on_=bookmarks.has(p.id);
  return `<section class="para${isChapter?' chapter-heading':''}" id="${p.id}" data-chapter="${p.chapter_id}"><button class="bm-btn${on_?' on':''}" type="button" data-bm="${p.id}" title="收藏此段">${on_?'★':'☆'}</button><div class="texts"><div class="ja" lang="ja">${p.ja_ruby_html || escapeHtml(p.ja)}</div><div class="zh" lang="zh-CN">${escapeHtml(p.zh || '')}</div></div></section>`;
}
function chapterHtml(i){ const c=state.work.chapters[i]; if(!c) return ''; return (byChapter.get(c.chapter_id)||[]).map(paraHtml).join(''); }
function renderRange(start,end){
  const total=state.work.chapters.length;
  start=Math.max(0,Math.min(start,total-1));
  end=Math.min(total,Math.max(end,start+1));
  let html=''; for(let i=start;i<end;i++) html+=chapterHtml(i);
  reader.innerHTML=html;
  winStart=start; winEnd=end;
  fillViewport();
  updateActiveChapter();
}
function appendNext(){
  if(winEnd>=state.work.chapters.length) return;
  const n=Math.min(CHUNK, state.work.chapters.length-winEnd);
  let html=''; for(let i=winEnd;i<winEnd+n;i++) html+=chapterHtml(i);
  reader.insertAdjacentHTML('beforeend', html);
  winEnd+=n; trimFront();
}
/* 增删章节时用「锚点元素」校正滚动，而不是推算高度差：
   高度差会与浏览器的 scroll anchoring 叠加，导致补偿两次、视口跑到别处。 */
function adjustScrollToKeep(anchor, topBefore){
  if(!anchor || topBefore===null || topBefore===undefined) return;
  const delta=anchor.getBoundingClientRect().top-topBefore;
  if(Math.abs(delta)>1) scrollTo(0, Math.max(0, window.scrollY+delta));
}
function prependPrev(){
  if(winStart<=0) return;
  const n=Math.min(CHUNK, winStart), from=winStart-n;
  let html=''; for(let i=from;i<winStart;i++) html+=chapterHtml(i);
  const anchor=currentAnchor();
  const topBefore=anchor?anchor.getBoundingClientRect().top:null;
  reader.insertAdjacentHTML('afterbegin', html);
  winStart=from;
  adjustScrollToKeep(anchor, topBefore);
}
function trimFront(){
  while(winEnd-winStart>MAX_WINDOW){
    const cid=state.work.chapters[winStart].chapter_id;
    const nodes=reader.querySelectorAll(`[data-chapter="${cid}"]`);
    const anchor=currentAnchor();
    const topBefore=anchor?anchor.getBoundingClientRect().top:null;
    nodes.forEach(n=>n.remove());
    winStart++;
    adjustScrollToKeep(anchor, topBefore);
  }
}
function fillViewport(){ let g=0; while(document.documentElement.scrollHeight<window.innerHeight*2 && winEnd<state.work.chapters.length && g++<40) appendNext(); }

/* ---------- 侧栏 ---------- */
function renderChapters(filter=''){
  const q=filter.trim();
  chapterList.innerHTML=state.work.chapters.map(c=>{
    const ja=c.title_ja||c.chapter_id, zh=c.title_zh||'';
    if(q && !ja.includes(q) && !zh.includes(q)) return '';
    return `<button class="chapter-item" data-chapter="${c.chapter_id}" title="${escapeHtml(ja)}"><div class="chapter-title">${escapeHtml(ja)}</div>${zh?`<div class="chapter-title-zh">${escapeHtml(zh)}</div>`:''}</button>`;
  }).join('');
  chapterList.querySelectorAll('.chapter-item').forEach(btn=>btn.addEventListener('click',()=>jumpTo(btn.dataset.chapter,null,true)));
  updateActiveChapter();
}
/* 窄屏只显示中文标题（更短，避免把 ⋯ 挤出屏幕）；宽屏中日对照。 */
function chapterLabel(c){
  const ja=c.title_ja||c.chapter_id, zh=c.title_zh||'';
  if(window.innerWidth<=900) return zh||ja;
  return zh?`${ja} ｜ ${zh}`:ja;
}
function fillChapters(){ chapterSelect.innerHTML=state.work.chapters.map(c=>`<option value="${c.chapter_id}">${escapeHtml(chapterLabel(c))}</option>`).join(''); }
function renderWork(){
  prepareWork(); loadBookmarks();
  fillChapters(); renderChapters(sideSearch?sideSearch.value:''); renderBookmarks();
  applyLayout(); reader.classList.toggle('hide-ruby', !rubyToggle.checked);
  if(!applyHash() && !restoreProgress()) renderRange(0, CHUNK);
  updateGlobalProgress();
}
async function selectWork(workId){
  const entry=state.index.works.find(w=>w.work_id===workId);
  if(!entry){ showError('找不到作品：'+workId); return false; }
  let data;
  try{ data=await loadJson(entry.path); }
  catch(err){
    if(err && err.code==='work-unavailable') showError(`「${entry.title}」没有打包在本文件中，请打开它对应的阅读器页面`);
    else showError('加载失败：'+(err&&err.message?err.message:err));
    if(workSelect && state.work) workSelect.value=state.work.work_id;
    return false;
  }
  state.work=data;
  localStorage.setItem(sessionKey(),workId);
  renderWork();
  return true;
}

/* ---------- 跳转：瞬时定位 + 稳定后恢复自动加载 ---------- */
function scrollToPara(el){ if(el) scrollTo(0, Math.max(0, el.getBoundingClientRect().top + window.scrollY - 70)); }
/* 跳转后必须等滚动位置稳定再解除 jumping：否则 onScroll 会把「窗口起点靠近页面顶部」
   误判成用户滚到顶而触发 prependPrev，连带补偿滚动，导致落点偏移。 */
function releaseJumpWhenStable(){
  let last=-1, stable=0, frames=0;
  (function check(){
    frames++;
    const y=Math.round(window.scrollY);
    if(y===last) stable++; else { stable=0; last=y; }
    if((stable>=2 && frames>4) || frames>150){ jumping=false; return; }
    requestAnimationFrame(check);
  })();
}
function setHash(id){ try{ history.replaceState(null,'','#'+id); }catch(e){ /* file:// 下可能被拒，忽略 */ } }
function jumpTo(chapterId, paraId, close){
  const i=chapterPos.get(chapterId); if(i===undefined) return;
  jumping=true;
  renderRange(i, i+CHUNK);
  chapterSelect.value=chapterId; state.currentChapter=chapterId;
  scrollToPara((paraId && document.getElementById(paraId)) || reader.querySelector('.para'));
  if(close) closeSidebar();
  setHash(paraId||chapterId);
  updateGlobalProgress();
  releaseJumpWhenStable();
}
function restoreProgress(){
  const data=readProgress(); if(!data) return false;
  const i=chapterPos.get(data.chapter_id); if(i===undefined) return false;
  jumping=true;
  renderRange(i, i+CHUNK);
  chapterSelect.value=data.chapter_id; state.currentChapter=data.chapter_id;
  scrollToPara(document.getElementById(data.para_id));
  releaseJumpWhenStable();
  return true;
}
function applyHash(){
  let h=''; try{ h=decodeURIComponent(location.hash.slice(1)); }catch(e){ h=location.hash.slice(1); }
  if(!h) return false;
  if(paraById.has(h)){ const p=paraById.get(h); jumpTo(p.chapter_id,p.id,false); return true; }
  if(chapterPos.has(h)){ jumpTo(h,null,false); return true; }
  return false;
}
function jumpChapter(delta){ const i=chapterSelect.selectedIndex+delta; if(i>=0&&i<chapterSelect.options.length){ chapterSelect.selectedIndex=i; jumpTo(chapterSelect.value,null,false); } }
function doSearch(){
  const q=searchBox.value.trim(); if(!q) return;
  const hit=state.work.paragraphs.find(p=>(p.ja&&p.ja.includes(q))||(p.zh&&p.zh.includes(q)));
  if(!hit){ showError(`未找到「${q}」`); return; }
  jumpTo(hit.chapter_id, hit.id, false);
  requestAnimationFrame(()=>{ const el=document.getElementById(hit.id); if(el){ el.classList.add('active'); setTimeout(()=>el.classList.remove('active'),1800); } });
}
function updateActiveChapter(){
  if(!state.work||!winEnd) return;
  let current=state.work.chapters[winStart]?.chapter_id;
  for(const sec of reader.querySelectorAll('.para')){ if(sec.getBoundingClientRect().top<140) current=sec.dataset.chapter; else break; }
  state.currentChapter=current;
  if(chapterSelect.value!==current) chapterSelect.value=current;
  chapterList.querySelectorAll('.chapter-item').forEach(b=>b.classList.toggle('active', b.dataset.chapter===current));
  /* 只滚动侧栏容器本身：scrollIntoView 会连带滚动外层（页面），
     表现为"正文莫名其妙跳走"。 */
  const active=chapterList.querySelector('.chapter-item.active');
  if(active && !sidebar.classList.contains('open') && window.innerWidth>900){
    const lr=chapterList.getBoundingClientRect(), ar=active.getBoundingClientRect();
    if(ar.top<lr.top) chapterList.scrollTop += ar.top-lr.top;
    else if(ar.bottom>lr.bottom) chapterList.scrollTop += ar.bottom-lr.bottom;
  }
}
function openSidebar(){ sidebar.classList.add('open'); overlay.classList.add('show'); }
function closeSidebar(){ sidebar.classList.remove('open'); overlay.classList.remove('show'); }

/* ---------- 滚动 ---------- */
let scrollbarTimer=null;
function flashScrollbar(){
  document.body.classList.add('is-scrolling');
  clearTimeout(scrollbarTimer);
  scrollbarTimer=setTimeout(()=>document.body.classList.remove('is-scrolling'),1100);
}
function onScroll(){
  if(!state.work) return;
  updateGlobalProgress();
  if(jumping) return;
  const doc=document.documentElement;
  if(!sidebar.classList.contains('open')){
    if(window.innerHeight+window.scrollY>doc.scrollHeight-PREFETCH && winEnd<state.work.chapters.length) appendNext();
    if(window.scrollY<PREFETCH/2 && winStart>0) prependPrev();
  }
  updateActiveChapter();
  clearTimeout(window.__saveTimer); window.__saveTimer=setTimeout(saveProgress,400);
}
window.addEventListener('scroll',()=>{ flashScrollbar(); closeMenu(); updateToolbarAuto(); if(ticking) return; ticking=true; requestAnimationFrame(()=>{ ticking=false; onScroll(); }); },{passive:true});
window.addEventListener('resize',()=>{ closeMenu(); layoutToolbar(); applyLayout(); if(window.innerWidth>900) closeSidebar(); });
window.addEventListener('hashchange',()=>{ if(state.work) applyHash(); });

/* ---------- 全书进度条 ----------
   滚动条只能反映「当前渲染窗口」（约 40 章），代表不了全书位置；
   这个进度条按段落序号算，才是真实的全书进度。 */
function paraSeq(pid){ const n=parseInt(String(pid).slice(1),10); return Number.isFinite(n)?n:0; }
function updateGlobalProgress(){
  if(!progressFill || !state.work) return;
  const total=state.work.paragraphs.length||1;
  const a=currentAnchor();
  const seq=a?paraSeq(a.id):1;
  const pct=Math.max(0,Math.min(100, seq/total*100));
  progressFill.style.width=pct.toFixed(2)+'%';
  if(progressTrack) progressTrack.title=`全书进度 ${pct.toFixed(1)}%（点击或拖动可跳转）`;
}
function seekRatio(ratio){
  if(!state.work) return;
  const total=state.work.paragraphs.length;
  const n=Math.max(1,Math.min(total,Math.round(ratio*total)));
  const p=paraById.get('p'+String(n).padStart(5,'0'));
  if(p) jumpTo(p.chapter_id,p.id,false);
}
let dragRatio=null;
if(progressTrack){
  const ratioAt=ev=>{ const r=progressTrack.getBoundingClientRect(); return r.width?Math.max(0,Math.min(1,(ev.clientX-r.left)/r.width)):0; };
  progressTrack.addEventListener('pointerdown',ev=>{
    ev.preventDefault();
    dragRatio=ratioAt(ev);
    progressTrack.classList.add('dragging');
    try{ progressTrack.setPointerCapture(ev.pointerId); }catch(e){}
    if(progressFill) progressFill.style.width=(dragRatio*100).toFixed(2)+'%';   /* 拖动仅预览，松手才跳转，避免狂渲染 */
  });
  progressTrack.addEventListener('pointermove',ev=>{ if(dragRatio===null) return; dragRatio=ratioAt(ev); if(progressFill) progressFill.style.width=(dragRatio*100).toFixed(2)+'%'; });
  progressTrack.addEventListener('pointerup',()=>{ if(dragRatio===null) return; const r=dragRatio; dragRatio=null; progressTrack.classList.remove('dragging'); seekRatio(r); });
  progressTrack.addEventListener('pointercancel',()=>{ dragRatio=null; progressTrack.classList.remove('dragging'); updateGlobalProgress(); });
}

/* ---------- 下载 / 备份 ---------- */
function stamp(){ const d=new Date(), p=n=>String(n).padStart(2,'0'); return `${d.getFullYear()}${p(d.getMonth()+1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`; }
function fileBase(){ return state.work.reader_name || state.work.work_id; }
function download(filename, text, mime){
  try{
    const blob=new Blob([text],{type:(mime||'text/plain')+';charset=utf-8'});
    const url=URL.createObjectURL(blob);
    const a=document.createElement('a');
    a.href=url; a.download=filename;
    document.body.appendChild(a); a.click();
    setTimeout(()=>{ URL.revokeObjectURL(url); a.remove(); },0);
    showError('已导出：'+filename);
  }catch(e){ showError('导出失败：'+e.message); }
}
function exportBackup(){
  const data={ type:'bilingual-reader-backup', version:1, exported_at:new Date().toISOString(),
    works:{ [state.work.work_id]: { title:fileBase(), progress:readProgress(), bookmarks:sortedBookmarks() } } };
  download(`阅读备份-${fileBase()}-${stamp()}.json`, JSON.stringify(data,null,2), 'application/json');
}
function importBackup(file){
  const fr=new FileReader();
  fr.onload=()=>{
    try{
      const data=JSON.parse(fr.result);
      if(!data || data.type!=='bilingual-reader-backup') throw new Error('不是本阅读器导出的备份文件');
      const w=(data.works||{})[state.work.work_id];
      if(!w) throw new Error('备份里没有当前作品（'+fileBase()+'）的数据');
      const valid=[], invalid=[];
      for(const b of (w.bookmarks||[])) (b && paraById.has(b.para_id) ? valid : invalid).push(b);
      const fresh=valid.filter(b=>!bookmarks.has(b.para_id));
      const cur=readProgress();
      const progNewer = !!w.progress && (!cur || (w.progress.t||0) > (cur.t||0));
      const msg=`来自《${w.title||state.work.work_id}》的备份\n\n`
        +`书签：共 ${valid.length} 条，新增 ${fresh.length} 条，已存在 ${valid.length-fresh.length} 条`
        +(invalid.length?`，无效（段落不存在）${invalid.length} 条`:'')+`\n`
        +`进度：${w.progress ? (progNewer ? '将更新到 '+w.progress.chapter_id : '本地记录更新，保留本地') : '备份中没有进度'}\n\n`
        +`确定导入吗？`;
      if(!confirm(msg)) return;
      for(const b of valid) if(!bookmarks.has(b.para_id)) bookmarks.set(b.para_id,b);
      saveBookmarks();
      if(progNewer) try{ localStorage.setItem(progressKey(), JSON.stringify(w.progress)); }catch(e){}
      renderRange(winStart, winEnd); renderBookmarks();
      alert(`导入完成：新增 ${fresh.length} 条书签`);
    }catch(e){ showError('导入失败：'+e.message); }
  };
  fr.onerror=()=>showError('读取文件失败');
  fr.readAsText(file);
}

/* ---------- 导出译文 ---------- */
function exportContent(kind){
  const out=[];
  if(kind==='md') out.push(`# ${fileBase()}\n`);
  for(const c of state.work.chapters){
    const paras=byChapter.get(c.chapter_id)||[]; if(!paras.length) continue;
    const title=c.title_zh||c.title_ja||c.chapter_id;
    out.push(kind==='md' ? `\n## ${title}\n` : `\n【${title}】\n`);
    for(const p of paras){
      if(kind==='md' && p.ja) out.push('> '+p.ja.replace(/\n/g,'\n> ')+'\n');
      out.push((p.zh||'')+'\n');
    }
  }
  const text=out.join('\n');
  if(kind==='md') download(`${fileBase()}-双语对照-${stamp()}.md`, text, 'text/markdown');
  else download(`${fileBase()}-译文-${stamp()}.txt`, text, 'text/plain');
}

/* ---------- 工具栏/侧栏：窄屏收纳、宽屏折叠 ---------- */
/* 窄屏把工具栏控件（对照/注音/字号/主题/搜索/上下章）移入抽屉，
   宽屏再移回原位；用注释节点记住原始位置，避免顺序错乱。 */
const slotMap={layout:'slotLayout',tools:'slotTools',search:'slotSearch',nav:'slotNav'};
const movable=[...document.querySelectorAll('[data-slot]')];
const placeholders=new Map();
movable.forEach(el=>{ const ph=document.createComment('ph'); el.parentNode.insertBefore(ph,el); placeholders.set(el,ph); });
function layoutToolbar(){
  const narrow=window.innerWidth<=900;
  movable.forEach(el=>{
    const slot=byId(slotMap[el.dataset.slot]); if(!slot) return;
    if(narrow){ if(el.parentNode!==slot) slot.appendChild(el); }
    else { const ph=placeholders.get(el); if(ph && ph.parentNode && el.parentNode!==ph.parentNode) ph.parentNode.insertBefore(el, ph.nextSibling); }
  });
  if(state.work) fillChapters();   // 章节标题文案随宽窄屏变化，需要重建
}
function applySidebarPref(){ document.body.classList.toggle('sidebar-collapsed', localStorage.getItem('reader.sidebarCollapsed')==='1'); }
function toggleSidebarWide(){
  const c=document.body.classList.toggle('sidebar-collapsed');
  localStorage.setItem('reader.sidebarCollapsed', c?'1':'0');
}
/* ---------- 目录栏宽度：拖右边缘调整，双击恢复默认，[ 键整体隐藏 ----------
   宽度写成 :root 的内联 --side-w，优先级高于样式表里的响应式默认值，
   因此手动设过之后窗口再变宽变窄都不会被媒体查询覆盖。 */
const SIDE_MIN=200, SIDE_MAX=560;
function defaultSideWidth(){ return window.innerWidth>1500 ? 340 : (window.innerWidth>1180 ? 300 : 260); }
function currentSideWidth(){
  const v=parseInt(getComputedStyle(document.documentElement).getPropertyValue('--side-w'),10);
  return (Number.isFinite(v) && v>0) ? v : defaultSideWidth();
}
function applySideWidth(w){
  const px=Math.round(Math.min(SIDE_MAX, Math.max(SIDE_MIN, w)));
  document.documentElement.style.setProperty('--side-w', px+'px');
  localStorage.setItem('reader.sideWidth', String(px));
}
function resetSideWidth(){
  localStorage.removeItem('reader.sideWidth');
  document.documentElement.style.removeProperty('--side-w');   // 交还给响应式默认
}
function restoreSideWidth(){
  const saved=Number(localStorage.getItem('reader.sideWidth')||0);
  if(saved>=SIDE_MIN && saved<=SIDE_MAX) document.documentElement.style.setProperty('--side-w', Math.round(saved)+'px');
}
function initSideResizer(){
  const grip=byId('sidebarResizer'); if(!grip) return;
  let dragging=false, startX=0, startW=0;
  grip.addEventListener('pointerdown',e=>{
    if(window.innerWidth<=900) return;          // 窄屏是抽屉，宽度不参与拖拽
    dragging=true; startX=e.clientX; startW=currentSideWidth();
    grip.classList.add('dragging'); document.body.classList.add('resizing-side');
    if(grip.setPointerCapture) grip.setPointerCapture(e.pointerId);
    e.preventDefault();
  });
  grip.addEventListener('pointermove',e=>{
    if(!dragging) return;
    applySideWidth(startW + (e.clientX-startX));
  });
  const stop=e=>{
    if(!dragging) return;
    dragging=false; grip.classList.remove('dragging'); document.body.classList.remove('resizing-side');
    if(grip.hasPointerCapture && grip.hasPointerCapture(e.pointerId)) grip.releasePointerCapture(e.pointerId);
  };
  grip.addEventListener('pointerup',stop);
  grip.addEventListener('pointercancel',stop);
  grip.addEventListener('dblclick',resetSideWidth);      // 双击回到默认宽度
  grip.addEventListener('keydown',e=>{                   // 键盘：← → 微调，Shift 加速
    if(e.key!=='ArrowLeft' && e.key!=='ArrowRight') return;
    applySideWidth(currentSideWidth() + (e.key==='ArrowRight'?1:-1)*(e.shiftKey?40:12));
    e.preventDefault();
  });
}
/* 滚动时自动隐藏工具栏（沉浸阅读）。菜单/侧栏打开时不隐藏。 */
let lastScrollY=-1;
function updateToolbarAuto(){
  const y=window.scrollY;
  if(lastScrollY<0){ lastScrollY=y; return; }   // 首次只记录，避免打开页面就隐藏
  const dy=y-lastScrollY;
  if(Math.abs(dy)<6) return;
  lastScrollY=y;
  if(moreMenu && moreMenu.classList.contains('show')) return;
  if(sidebar.classList.contains('open')) return;
  if(dy>0 && y>140) document.body.classList.add('toolbar-hidden');
  else if(dy<0) document.body.classList.remove('toolbar-hidden');
}

/* ---------- 章节跳转：记住来源位置，返回时回到原处 ---------- */
const chapterMemory=new Map();
function rememberPosition(){ const a=currentAnchor(); if(a) chapterMemory.set(a.dataset.chapter, a.id); }
function gotoChapterIndex(idx){
  const chs=state.work.chapters; if(idx<0||idx>=chs.length) return;
  rememberPosition();
  const target=chs[idx].chapter_id;
  const mem=chapterMemory.get(target);
  jumpTo(target, (mem && paraById.has(mem)) ? mem : null, false);
}
function jumpChapterMem(delta){
  const cur=chapterPos.get(state.currentChapter || chapterSelect.value);
  if(cur===undefined) return;
  gotoChapterIndex(cur+delta);
}
/* 菜单是 body 级 fixed 元素，位置按视口算；窄屏用 CSS 的底部抽屉（bottom:0）。 */
function positionMenu(){
  if(!moreMenu || !moreBtn) return;
  const r=moreBtn.getBoundingClientRect();
  if(window.innerWidth<=900){
    moreMenu.style.top='auto'; moreMenu.style.bottom='';
    moreMenu.style.left=''; moreMenu.style.right='';
  } else {
    moreMenu.style.bottom='auto'; moreMenu.style.left='auto';
    moreMenu.style.top=Math.round(r.bottom+6)+'px';
    moreMenu.style.right=Math.max(8, Math.round(window.innerWidth-r.right))+'px';
  }
}
function openMenu(){ positionMenu(); if(moreMenu) moreMenu.classList.add('show'); }
function closeMenu(){ if(moreMenu) moreMenu.classList.remove('show'); }
function toggleMenu(){ if(!moreMenu) return; if(moreMenu.classList.contains('show')) closeMenu(); else openMenu(); }
function openHelp(){ helpOpen=true; if(helpLayer) helpLayer.classList.add('show'); }
function closeHelp(){ helpOpen=false; if(helpLayer) helpLayer.classList.remove('show'); }

/* ---------- 键盘 ---------- */
function onKey(e){
  const tag=((e.target && e.target.tagName) || '').toLowerCase();
  const typing = tag==='input' || tag==='textarea' || tag==='select';
  if(typing){ if(e.key==='Escape'){ e.target.blur(); closeMenu(); } return; }
  if((e.ctrlKey||e.metaKey) && (e.key==='f'||e.key==='F')){ e.preventDefault(); if(searchBox) searchBox.focus(); return; }
  if(e.ctrlKey||e.metaKey||e.altKey) return;
  switch(e.key){
    case 'ArrowLeft': e.preventDefault(); jumpChapterMem(-1); break;
    case 'ArrowRight': e.preventDefault(); jumpChapterMem(1); break;
    case ' ': e.preventDefault(); window.scrollBy(0,(e.shiftKey?-1:1)*Math.max(200,window.innerHeight-140)); break;
    case 'Home': e.preventDefault(); if(state.currentChapter) jumpTo(state.currentChapter,null,false); break;
    case 'b': case 'B': { const a=currentAnchor(); if(a) toggleBookmark(a.id); break; }
    case '/': e.preventDefault(); if(searchBox) searchBox.focus(); break;
    case '[': e.preventDefault(); if(window.innerWidth<=900){ if(sidebar.classList.contains('open')) closeSidebar(); else openSidebar(); } else toggleSidebarWide(); break;
    case '?': e.preventDefault(); if(helpOpen) closeHelp(); else openHelp(); break;
    case 'Escape': closeMenu(); closeHelp(); closeSidebar(); break;
  }
}

/* ---------- 事件 ---------- */
reader.addEventListener('click',e=>{ const b=e.target.closest('.bm-btn'); if(b){ e.preventDefault(); toggleBookmark(b.dataset.bm); } });
if(bookmarkList) bookmarkList.addEventListener('click',e=>{
  const rm=e.target.closest('.bm-remove');
  if(rm){ e.preventDefault(); bookmarks.delete(rm.dataset.para); saveBookmarks(); const btn=reader.querySelector(`.bm-btn[data-bm="${rm.dataset.para}"]`); if(btn){ btn.classList.remove('on'); btn.textContent='☆'; } renderBookmarks(); return; }
  const jp=e.target.closest('.bm-jump');
  if(jp){ e.preventDefault(); const p=paraById.get(jp.dataset.para); if(p) jumpTo(p.chapter_id,p.id,true); }
});
document.querySelectorAll('.side-tabs button').forEach(b=>b.addEventListener('click',()=>setTab(b.dataset.tab)));
document.querySelectorAll('[data-layout]').forEach(btn=>btn.addEventListener('click',()=>setLayout(btn.dataset.layout)));
on(rubyToggle,'change',()=>reader.classList.toggle('hide-ruby',!rubyToggle.checked));
on(chapterSelect,'change',()=>jumpTo(chapterSelect.value,null,false));
on(workSelect,'change',()=>selectWork(workSelect.value));
on(searchBox,'keydown',e=>{ if(e.key==='Enter') doSearch(); });
on(sideSearch,'input',()=>{ if(state.tab==='bookmarks') renderBookmarks(); else if(state.work) renderChapters(sideSearch.value); });
on(byId('prevChapter'),'click',()=>jumpChapterMem(-1));
on(byId('nextChapter'),'click',()=>jumpChapterMem(1));
on(byId('menuToggle'),'click',()=>{ if(window.innerWidth<=900) openSidebar(); else toggleSidebarWide(); });
/* 左右滑动翻章（触摸）。三重防误触：位移阈值、水平分量占优、时长限制；
   翻章前后位置由 chapterMemory 记忆，反向滑回会回到原来的段落而不是章首。 */
let tsx=0,tsy=0,tst=0,swipeTracking=false;
const SWIPE_MIN=70;
reader.addEventListener('touchstart',e=>{ if(e.touches.length!==1) return; const t=e.touches[0]; tsx=t.clientX; tsy=t.clientY; tst=Date.now(); swipeTracking=true; },{passive:true});
reader.addEventListener('touchend',e=>{
  if(!swipeTracking) return; swipeTracking=false;
  const t=e.changedTouches[0]; if(!t) return;
  const dx=t.clientX-tsx, dy=t.clientY-tsy, dt=Date.now()-tst;
  if(dt>900 || Math.abs(dx)<SWIPE_MIN) return;
  if(Math.abs(dx) < Math.abs(dy)*1.5) return;
  if(dx<0) jumpChapterMem(1); else jumpChapterMem(-1);
},{passive:true});
on(overlay,'click',closeSidebar);
on(darkToggle,'click',cycleTheme);
on(fontMinus,'click',()=>{ state.fontScale=Math.max(-3,state.fontScale-1); applyPrefs(); });
on(fontPlus,'click',()=>{ state.fontScale=Math.min(6,state.fontScale+1); applyPrefs(); });
on(moreBtn,'click',e=>{ e.stopPropagation(); toggleMenu(); });
document.addEventListener('click',e=>{ if(!e.target.closest('#moreMenu') && !e.target.closest('#moreBtn')) closeMenu(); });
on(byId('expBackup'),'click',()=>{ closeMenu(); exportBackup(); });
on(byId('impBackup'),'click',()=>{ closeMenu(); if(importFile) importFile.click(); });
on(importFile,'change',()=>{ const f=importFile.files && importFile.files[0]; if(f) importBackup(f); importFile.value=''; });
on(byId('expZh'),'click',()=>{ closeMenu(); exportContent('zh'); });
on(byId('expMd'),'click',()=>{ closeMenu(); exportContent('md'); });
on(byId('showHelp'),'click',()=>{ closeMenu(); openHelp(); });
on(byId('helpClose'),'click',closeHelp);
on(helpLayer,'click',e=>{ if(e.target===helpLayer) closeHelp(); });
document.addEventListener('keydown',onKey);

(async function init(){
  applyPrefs();
  applySidebarPref();
  restoreSideWidth();
  initSideResizer();
  layoutToolbar();
  state.index=await loadJson('works/index.json');
  workSelect.innerHTML=state.index.works.map(w=>`<option value="${w.work_id}">${escapeHtml(w.title)}</option>`).join('');
  /* 恢复本副本上次阅读的作品；没有记录（或记录的作品打不开）时用本副本的主打作品。
     若主打作品也取不到，才按 index 顺序逐个尝试，不会停在 "Failed to fetch" 上。 */
  const ids=state.index.works.map(w=>w.work_id);
  const primary=primaryWorkId();
  const saved=localStorage.getItem(sessionKey());
  const order=(saved && ids.includes(saved)) ? [saved, ...ids.filter(i=>i!==saved)]
             : (primary && ids.includes(primary)) ? [primary, ...ids.filter(i=>i!==primary)]
             : ids;
  for(const id of order){ if(await selectWork(id)) return; }
  showError('本文件中没有可阅读的作品数据');
})().catch(err=>{ showError('加载失败：'+(err&&err.message?err.message:err)); });
