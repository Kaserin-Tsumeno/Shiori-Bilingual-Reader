
const state = { index:null, work:null, layout:localStorage.getItem("reader.layout") || "side", currentChapter:null, tab:'chapters', fontScale:Number(localStorage.getItem('reader.fontScale')||0), dark:localStorage.getItem('reader.dark')==='1' };
const reader=document.getElementById("reader"), statusEl=document.getElementById("status"), workSelect=document.getElementById("workSelect"), chapterSelect=document.getElementById("chapterSelect"), rubyToggle=document.getElementById("rubyToggle"), searchBox=document.getElementById("searchBox"), chapterList=document.getElementById("chapterList"), bookmarkList=document.getElementById("bookmarkList"), sideSearch=document.getElementById("sideSearch"), sidebar=document.getElementById("sidebar"), overlay=document.getElementById("overlay"), darkToggle=document.getElementById('darkToggle'), fontMinus=document.getElementById('fontMinus'), fontPlus=document.getElementById('fontPlus'), bmCount=document.getElementById('bmCount');

/* 长篇作品（数万段）必须分批渲染：一次插入全部段落会让浏览器卡死。
   按「章」为单位渲染，并用滚动窗口回收远端章节，使 DOM 规模有界。 */
const CHUNK = 3;          // 每次追加的章节数
const MAX_WINDOW = 40;    // 窗口内保留的最大章节数
const PREFETCH = 900;     // 距底/顶多少像素触发追加
let winStart = 0, winEnd = 0;
let byChapter = null, chapterStart = null, chapterPos = null, paraById = null;
let ticking = false, jumping = false;   // jumping：跳转期间冻结自动加载，避免与滚动打架
let bookmarks = new Map();

let _idxCache, _worksCache;
function embeddedJson(id){ const el=document.getElementById(id); if(!el) return null; try{ return JSON.parse(el.textContent); }catch(e){ return null; } }
function embeddedIndex(){ if(_idxCache===undefined) _idxCache=embeddedJson('reader-index'); return _idxCache; }
function embeddedWorks(){ if(_worksCache===undefined) _worksCache=embeddedJson('reader-works'); return _worksCache; }
async function loadJson(path){ if(path==='works/index.json'){ const d=embeddedIndex(); if(d) return d; } const works=embeddedWorks(); if(works){ const hit=Object.values(works).find(w=>'works/'+w.work_id+'.json'===path); if(hit) return hit; } const res=await fetch(path); if(!res.ok) throw new Error(path); return res.json(); }
function escapeHtml(s){return String(s).replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch])).replace(/\n/g,"<br>")}
function showError(msg){ if(statusEl){ statusEl.textContent=msg; statusEl.classList.add('show'); } }
function clearError(){ if(statusEl){ statusEl.classList.remove('show'); statusEl.textContent=''; } }

function applyPrefs(){ document.body.classList.toggle('dark',state.dark); document.documentElement.style.setProperty('--reader-font', `${17 + state.fontScale}px`); if(darkToggle) darkToggle.textContent=state.dark?'亮':'暗'; localStorage.setItem('reader.fontScale',String(state.fontScale)); localStorage.setItem('reader.dark',state.dark?'1':'0'); }
/* 窄屏（<=720px）左右对照无法阅读，自动退化为上下排版，
   并把按钮状态改成真实生效的布局——否则会出现"选中左右、实际显示上下"的错位。 */
const FORCE_STACK_WIDTH = 720;
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

/* ---------- 阅读进度：以段落为锚点（比 scrollY 可靠，重排/改渲染策略都不失效） ---------- */
function progressKey(){ return `reader.progress.${state.work.work_id}`; }
function currentAnchor(){
  const paras=reader.querySelectorAll('.para');
  if(!paras.length) return null;
  for(const p of paras){ if(p.getBoundingClientRect().bottom>80) return p; }
  return paras[paras.length-1];
}
function saveProgress(){
  if(!state.work || jumping) return;
  const a=currentAnchor(); if(!a) return;
  try{ localStorage.setItem(progressKey(), JSON.stringify({para_id:a.id, chapter_id:a.dataset.chapter, t:Date.now()})); }catch(e){}
}
function readProgress(){
  try{ const raw=localStorage.getItem(progressKey()); if(!raw) return null; const o=JSON.parse(raw); return (o && o.para_id)?o:null; }catch(e){ return null; }
}

/* ---------- 书签 ---------- */
function bookmarksKey(){ return `reader.bookmarks.${state.work.work_id}`; }
function loadBookmarks(){
  bookmarks=new Map();
  try{ const raw=localStorage.getItem(bookmarksKey()); if(raw) for(const b of JSON.parse(raw)) if(b && b.para_id) bookmarks.set(b.para_id,b); }catch(e){}
}
function saveBookmarks(){
  try{ localStorage.setItem(bookmarksKey(), JSON.stringify([...bookmarks.values()])); }catch(e){ showError('书签保存失败（浏览器存储不可用）'); }
}
function toggleBookmark(pid){
  if(bookmarks.has(pid)) bookmarks.delete(pid);
  else {
    const p=paraById.get(pid); if(!p) return;
    const ci=chapterPos.get(p.chapter_id);
    const c=ci===undefined?null:state.work.chapters[ci];
    bookmarks.set(pid, {
      para_id:pid, chapter_id:p.chapter_id,
      chapter_zh:(c&&c.title_zh)||'', chapter_ja:(c&&c.title_ja)||'',
      ja:(p.ja||'').slice(0,60), zh:(p.zh||'').slice(0,60), t:Date.now()
    });
  }
  saveBookmarks();
  const btn=reader.querySelector(`.bm-btn[data-bm="${pid}"]`);
  if(btn){ const on=bookmarks.has(pid); btn.classList.toggle('on',on); btn.textContent=on?'★':'☆'; }
  renderBookmarks();
}
function sortedBookmarks(){ return [...bookmarks.values()].sort((a,b)=>a.para_id<b.para_id?-1:(a.para_id>b.para_id?1:0)); }
function renderBookmarks(){
  if(!bookmarkList) return;
  const q=(sideSearch.value||'').trim();
  const items=sortedBookmarks().filter(b=>!q || (b.zh||'').includes(q) || (b.ja||'').includes(q) || (b.chapter_zh||'').includes(q) || (b.chapter_ja||'').includes(q));
  if(bmCount) bmCount.textContent=String(bookmarks.size);
  if(!items.length){ bookmarkList.innerHTML=`<div class="bm-empty">${bookmarks.size? '没有匹配的书签':'还没有书签。<br>把鼠标移到任意段落上，点右上角的 ☆ 即可收藏。'}</div>`; return; }
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
  const isChapter=chapterStart.has(p.id);
  const on=bookmarks.has(p.id);
  return `<section class="para${isChapter?' chapter-heading':''}" id="${p.id}" data-chapter="${p.chapter_id}"><button class="bm-btn${on?' on':''}" type="button" data-bm="${p.id}" title="收藏此段">${on?'★':'☆'}</button><div class="texts"><div class="ja" lang="ja">${p.ja_ruby_html || escapeHtml(p.ja)}</div><div class="zh" lang="zh-CN">${escapeHtml(p.zh || '')}</div></div></section>`;
}
function chapterHtml(i){ const c=state.work.chapters[i]; if(!c) return ''; const paras=byChapter.get(c.chapter_id)||[]; return paras.map(paraHtml).join(''); }

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
  winEnd+=n;
  trimFront();
}
function prependPrev(){
  if(winStart<=0) return;
  const n=Math.min(CHUNK, winStart), from=winStart-n;
  let html=''; for(let i=from;i<winStart;i++) html+=chapterHtml(i);
  const before=document.documentElement.scrollHeight;
  reader.insertAdjacentHTML('afterbegin', html);
  const after=document.documentElement.scrollHeight;
  winStart=from;
  scrollTo(0, Math.max(0, window.scrollY + (after-before)));
}
function trimFront(){
  while(winEnd-winStart>MAX_WINDOW){
    const cid=state.work.chapters[winStart].chapter_id;
    const nodes=reader.querySelectorAll(`[data-chapter="${cid}"]`);
    const before=document.documentElement.scrollHeight;
    nodes.forEach(n=>n.remove());
    const after=document.documentElement.scrollHeight;
    winStart++;
    scrollTo(0, Math.max(0, window.scrollY - (before-after)));
  }
}
function fillViewport(){
  let guard=0;
  while(document.documentElement.scrollHeight < window.innerHeight*2 && winEnd<state.work.chapters.length && guard++<40) appendNext();
}

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
function chapterLabel(c){ const ja=c.title_ja||c.chapter_id, zh=c.title_zh||''; return zh?`${ja} ｜ ${zh}`:ja; }
function fillChapters(){ chapterSelect.innerHTML=state.work.chapters.map(c=>`<option value="${c.chapter_id}">${escapeHtml(chapterLabel(c))}</option>`).join(''); }
function renderWork(){
  prepareWork();
  loadBookmarks();
  fillChapters();
  renderChapters(sideSearch?sideSearch.value:'');
  renderBookmarks();
  applyLayout();
  reader.classList.toggle('hide-ruby', !rubyToggle.checked);
  if(!restoreProgress()) renderRange(0, CHUNK);
}
async function selectWork(workId){
  const entry=state.index.works.find(w=>w.work_id===workId);
  state.work=await loadJson(entry.path);
  localStorage.setItem('reader.work',workId);
  renderWork();
}

/* ---------- 跳转：瞬时定位 + 稳定后恢复自动加载 ---------- */
function scrollToPara(el){ if(el) scrollTo(0, Math.max(0, el.getBoundingClientRect().top + window.scrollY - 70)); }
/* 跳转后必须等滚动位置稳定再解除 jumping：
   否则 onScroll 会把「窗口起点靠近页面顶部」误判成用户滚到顶而触发 prependPrev，
   连带补偿滚动，结果是点第 10 话却跳到第 12 话。 */
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
function jumpTo(chapterId, paraId, close){
  const i=chapterPos.get(chapterId);
  if(i===undefined) return;
  jumping=true;
  renderRange(i, i+CHUNK);
  chapterSelect.value=chapterId;
  state.currentChapter=chapterId;
  scrollToPara((paraId && document.getElementById(paraId)) || reader.querySelector('.para'));
  if(close) closeSidebar();
  releaseJumpWhenStable();
}
function restoreProgress(){
  const data=readProgress(); if(!data) return false;
  const i=chapterPos.get(data.chapter_id); if(i===undefined) return false;
  jumping=true;
  renderRange(i, i+CHUNK);
  chapterSelect.value=data.chapter_id;
  state.currentChapter=data.chapter_id;
  scrollToPara(document.getElementById(data.para_id));
  releaseJumpWhenStable();
  return true;
}
function jumpChapter(delta){ const i=chapterSelect.selectedIndex+delta; if(i>=0 && i<chapterSelect.options.length){ chapterSelect.selectedIndex=i; jumpTo(chapterSelect.value,null,false); } }
function doSearch(){
  const q=searchBox.value.trim(); if(!q) return;
  const hit=state.work.paragraphs.find(p=>(p.ja&&p.ja.includes(q))||(p.zh&&p.zh.includes(q)));
  if(!hit){ showError(`未找到「${q}」`); return; }
  clearError();
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
  const active=chapterList.querySelector('.chapter-item.active');
  if(active && !sidebar.classList.contains('open') && window.innerWidth>900) active.scrollIntoView({block:'nearest'});
}
function openSidebar(){ sidebar.classList.add('open'); overlay.classList.add('show'); } function closeSidebar(){ sidebar.classList.remove('open'); overlay.classList.remove('show'); }

function onScroll(){
  if(!state.work || jumping) return;
  const doc=document.documentElement;
  if(!sidebar.classList.contains('open')){
    if(window.innerHeight+window.scrollY>doc.scrollHeight-PREFETCH && winEnd<state.work.chapters.length) appendNext();
    if(window.scrollY<PREFETCH/2 && winStart>0) prependPrev();
  }
  updateActiveChapter();
  clearTimeout(window.__saveTimer); window.__saveTimer=setTimeout(saveProgress,400);
}
window.addEventListener('scroll',()=>{ if(ticking) return; ticking=true; requestAnimationFrame(()=>{ ticking=false; onScroll(); }); },{passive:true});
window.addEventListener('resize',()=>{ applyLayout(); if(window.innerWidth>900) closeSidebar(); });

/* ---------- 事件 ---------- */
function on(el,ev,fn){ if(el) el.addEventListener(ev,fn); }
function byId(id){ return document.getElementById(id); }
reader.addEventListener('click',e=>{ const b=e.target.closest('.bm-btn'); if(b){ e.preventDefault(); toggleBookmark(b.dataset.bm); } });
if(bookmarkList) bookmarkList.addEventListener('click',e=>{
  const rm=e.target.closest('.bm-remove');
  if(rm){ e.preventDefault(); bookmarks.delete(rm.dataset.para); saveBookmarks(); const btn=reader.querySelector(`.bm-btn[data-bm="${rm.dataset.para}"]`); if(btn){ btn.classList.remove('on'); btn.textContent='☆'; } renderBookmarks(); return; }
  const jp=e.target.closest('.bm-jump');
  if(jp){ e.preventDefault(); const p=paraById.get(jp.dataset.para); if(p) jumpTo(p.chapter_id, p.id, true); }
});
document.querySelectorAll('.side-tabs button').forEach(b=>b.addEventListener('click',()=>setTab(b.dataset.tab)));
document.querySelectorAll('[data-layout]').forEach(btn=>btn.addEventListener('click',()=>setLayout(btn.dataset.layout)));
on(rubyToggle,'change',()=>reader.classList.toggle('hide-ruby',!rubyToggle.checked));
on(chapterSelect,'change',()=>jumpTo(chapterSelect.value,null,false));
on(workSelect,'change',()=>selectWork(workSelect.value));
on(searchBox,'keydown',e=>{ if(e.key==='Enter') doSearch(); });
on(sideSearch,'input',()=>{ if(state.tab==='bookmarks') renderBookmarks(); else if(state.work) renderChapters(sideSearch.value); });
on(byId('prevChapter'),'click',()=>jumpChapter(-1));
on(byId('nextChapter'),'click',()=>jumpChapter(1));
on(byId('menuToggle'),'click',openSidebar);
on(overlay,'click',closeSidebar);
on(darkToggle,'click',()=>{ state.dark=!state.dark; applyPrefs(); });
on(fontMinus,'click',()=>{ state.fontScale=Math.max(-3,state.fontScale-1); applyPrefs(); });
on(fontPlus,'click',()=>{ state.fontScale=Math.min(6,state.fontScale+1); applyPrefs(); });

(async function init(){
  applyPrefs();
  state.index=await loadJson('works/index.json');
  workSelect.innerHTML=state.index.works.map(w=>`<option value="${w.work_id}">${escapeHtml(w.title)}</option>`).join('');
  await selectWork(localStorage.getItem('reader.work') || state.index.works[0].work_id);
})().catch(err=>{ showError('加载失败：'+(err&&err.message?err.message:err)); });
